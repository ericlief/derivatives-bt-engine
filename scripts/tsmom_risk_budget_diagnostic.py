"""
Standalone, READ-ONLY diagnostic comparing the live TSMOM cluster-cap risk
budget (compute_rebalance_targets's actual output) against what Equal Risk
Contribution (ERC) and Hierarchical Risk Parity (HRP) would produce for the
same instrument universe -- to find out empirically whether the gap is
large enough to justify added complexity, before changing anything live.

Motivated by research/cta-layer-separation-risk-budgeting.md, which found
this system's cluster-cap/sqrt(n_effective) design has no direct precedent
in named portfolio-construction literature (unlike its instrument-level
vol targeting, which matches canonical TSMOM exactly). This script makes
that gap measurable instead of theoretical.

Does NOT modify derivatives_bt_engine/domain/tsmom_signal.py, derivatives_bt_engine/live/
tsmom_rebalance.py, derivatives_bt_engine/domain/tsmom_backtester.py, or any other
production code path -- it imports and calls compute_rebalance_targets
read-only, to capture what the live system actually produces, and
otherwise only reads market data via the IB connection.

Library choice for ERC -- deviation from the brief, documented here:
PyPortfolioOpt 1.6.0 has no built-in named ERC/risk-parity solver
(confirmed by direct introspection: no RiskParity class, no risk_parity
method on EfficientFrontier -- only HRPOpt and the mean-variance family).
riskfolio-lib (the suggested alternative) was tried, but its installation
force-downgraded numpy and pandas project-wide (2.5.0->2.4.6,
3.0.3->2.3.3) and broke 5 previously-passing tests -- an unacceptable side
effect for what's supposed to be an isolated, read-only diagnostic, so it
was uninstalled again. ERC here is therefore a small, standard
scipy.optimize.minimize formulation instead (Maillard, Roncalli &
Teiletche 2010's equal-risk-contribution objective -- the textbook
formulation, not a novel derivation: minimize the spread between each
asset's risk contribution, subject to weights summing to 1). HRP uses
PyPortfolioOpt's HRPOpt directly, as instructed, since that one is
available with no dependency conflicts.

Covariance estimator: exponentially weighted, pandas
`returns.ewm(halflife=...).cov()`, defaulting to a 60-day halflife. This
default is a reasonable STARTING ASSUMPTION, not a directly-confirmed
number: AQR's "Demystifying Managed Futures" (Hurst, Ooi & Pedersen 2013),
Section 3.3, states the portfolio-level covariance matrix is
exponentially weighted "analogously to" their per-instrument vol
estimator (which has a stated 60-day center of mass), but the extracted
text doesn't itself state the portfolio-level center-of-mass verbatim --
see research/cta-layer-separation-risk-budgeting.md. Treat --halflife as
tunable, not gospel.

Run (requires a live IB connection -- TWS/IB Gateway running):
    python -m scripts.tsmom_risk_budget_diagnostic --account-equity 80000
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize

log = logging.getLogger(__name__)

DEFAULT_HALFLIFE_DAYS    = 60.0
DEFAULT_MIN_SYNCED_ROWS  = 252    # ~1 trading year of common history
DEFAULT_LOOKBACK_DAYS    = 1100   # ~3 calendar years; gives ~750 trading days
DEFAULT_DB_PATH          = '/home/dev/fin/db/globex_mdp_3.0.duckdb'

# Continuous front-month from daily: nearest expiry per date,
# pre-expiry bars only.  Parameterised by asset and a lookback date cutoff
# (two positional ? placeholders) to keep it fast on the full duckdb.
_DB_CONT_FRONT_SQL = """
WITH bars AS (
    SELECT ts_event::date AS date, close, expiration
    FROM daily
    WHERE asset = ?
      AND instrument_class = 'F' AND security_type = 'FUT'
      AND expiration IS NOT NULL
      AND ts_event < CAST(expiration AS DATE)
      AND ts_event::date >= ?::DATE
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY date ORDER BY expiration ASC) AS rn
    FROM bars
)
SELECT date, close
FROM ranked
WHERE rn = 1
ORDER BY date
"""


# ------------------------------------------------------------------
# Pure logic -- no IB dependency, fully unit-testable
# ------------------------------------------------------------------

def resolve_signal_symbol(instr: dict) -> str:
    """Identical resolution order to tsmom_rebalance.py's _compute_signal
    -- reused, not reinvented, so this script's notion of "which contract's
    history backs this instrument" never silently diverges from what the
    live system actually does."""
    return instr.get('signal_symbol') or instr.get('ib_symbol') or instr['symbol']


def resolve_db_symbol(instr: dict) -> str:
    """Globex root symbol for this instrument in daily.asset.
    Resolution: explicit db_symbol > signal_symbol (thin micros already
    borrow their full-size sibling's history) > ib_symbol > symbol.
    Examples: J7→6J, BRE→6L, MZC→ZC (via signal_symbol), SIL→SI."""
    return instr.get('db_symbol') or instr.get('signal_symbol') or instr.get('ib_symbol') or instr['symbol']


def log_signal_symbol_fallbacks(instruments: list[dict]) -> dict[str, str]:
    """Returns {symbol: signal_symbol} only for instruments where the
    resolved signal_symbol differs from their own traded ticker -- i.e.
    instruments whose correlation/covariance estimate below rests on a
    DIFFERENT contract's history (e.g. MZC borrowing ZC's). Logs each one
    so the report is explicit about which correlations are substituted."""
    fallbacks = {}
    for instr in instruments:
        symbol = instr['symbol']
        ib_symbol = instr.get('ib_symbol') or symbol
        resolved = resolve_signal_symbol(instr)
        if resolved != ib_symbol:
            fallbacks[symbol] = resolved
            log.info('%s: using %s\'s continuous history (signal_symbol fallback) for this calculation',
                     symbol, resolved)
    return fallbacks


def synchronize_price_frames(price_frames: dict[str, pd.DataFrame],
                              min_rows: int = DEFAULT_MIN_SYNCED_ROWS) -> pd.DataFrame:
    """
    price_frames: {symbol: DataFrame with a 'date' and 'close' column}
    (one per instrument, already signal_symbol-substituted upstream).

    Returns a wide DataFrame (date index, one column per symbol) built
    from an inner join across every instrument's dates -- i.e. only dates
    common to ALL instruments survive, so the result is, by construction,
    a single synchronized, identical date index shared by every column.

    Fails loudly (ValueError) rather than silently dropping an instrument
    that can't be aligned: an empty/missing frame for any instrument, or
    a final common window smaller than min_rows, raises -- naming every
    instrument's own row count and date range so the actual culprit (the
    one with the shortest or most disjoint history) is identifiable
    without re-running anything.
    """
    if not price_frames:
        raise ValueError('No price frames to synchronize')

    missing = [symbol for symbol, df in price_frames.items() if df is None or df.empty]
    if missing:
        raise ValueError(f'No bars available for: {missing} -- cannot synchronize')

    wide = None
    for symbol, df in price_frames.items():
        s = df.set_index('date')['close'].rename(symbol)
        wide = s.to_frame() if wide is None else wide.join(s, how='inner')

    per_instrument_ranges = {
        symbol: (df['date'].min(), df['date'].max(), len(df))
        for symbol, df in price_frames.items()
    }

    if wide is None or wide.empty or len(wide) < min_rows:
        rows = 0 if wide is None else len(wide)
        detail = '\n'.join(
            f'  {symbol}: {n} rows, {start} -> {end}'
            for symbol, (start, end, n) in sorted(per_instrument_ranges.items())
        )
        raise ValueError(
            f'Synchronized common date window has only {rows} rows (need >= {min_rows}) -- '
            f'at least one instrument cannot be aligned with the rest even after signal_symbol '
            f'substitution. Per-instrument history:\n{detail}'
        )

    return wide.sort_index()


def compute_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily log returns from a synchronized wide price matrix (output of
    synchronize_price_frames)."""
    return np.log(prices).diff().dropna(how='any')


def compute_ewm_covariance(returns: pd.DataFrame, halflife: float = DEFAULT_HALFLIFE_DAYS) -> pd.DataFrame:
    """Exponentially-weighted covariance matrix, most recent estimate
    (the last date's slice of pandas' .ewm(halflife=...).cov() panel) --
    see module docstring for why halflife defaults to 60 and why that's a
    starting assumption, not a directly-confirmed number."""
    cov_panel = returns.ewm(halflife=halflife).cov()
    last_cov = cov_panel.loc[returns.index[-1]]
    return last_cov.loc[returns.columns, returns.columns]


def compute_erc_weights(cov: pd.DataFrame) -> pd.Series:
    """
    Equal Risk Contribution weights via scipy.optimize (see module
    docstring for why this isn't PyPortfolioOpt/riskfolio-lib) --
    Maillard, Roncalli & Teiletche (2010)'s standard formulation: minimize
    the spread of each asset's risk contribution RC_i = w_i * (Cov @ w)_i,
    subject to weights summing to 1, long-only (matching HRPOpt's default
    long-only convention below, for a fair side-by-side comparison).
    """
    symbols = list(cov.columns)
    n = len(symbols)
    cov_vals = cov.values

    def risk_contributions(w):
        port_var = w @ cov_vals @ w
        if port_var <= 0:
            return np.zeros(n)
        marginal = cov_vals @ w
        return w * marginal / np.sqrt(port_var)

    def objective(w):
        rc = risk_contributions(w)
        return np.sum((rc - rc.mean()) ** 2)

    w0 = np.full(n, 1.0 / n)
    bounds = [(1e-6, 1.0)] * n
    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]
    result = minimize(objective, w0, method='SLSQP', bounds=bounds, constraints=constraints,
                      options={'maxiter': 1000, 'ftol': 1e-12})
    if not result.success:
        raise RuntimeError(f'ERC optimization failed to converge: {result.message}')
    return pd.Series(result.x, index=symbols)


def compute_hrp_weights(returns: pd.DataFrame) -> pd.Series:
    """Hierarchical Risk Parity via PyPortfolioOpt's HRPOpt, operating
    directly on the return matrix, as instructed.

    PyPortfolioOpt 1.6.0's HRPOpt.optimize() guards its linkage_method
    argument with `if linkage_method not in sch._LINKAGE_METHODS`, a
    private scipy attribute that scipy >=1.18 removed entirely (the actual
    clustering call, sch.linkage(), doesn't use it -- it's pypfopt's own
    validation guard, nothing more). Restoring the attribute with scipy's
    well-known, stable set of valid linkage methods is a narrow, safe
    compatibility shim -- it changes no behavior, just un-breaks a
    version-skew check -- consistent with this project's existing pattern
    of small monkey-patches for library/runtime version skew (e.g.
    ib_tools' _get_loop_py313 patch for Python 3.13)."""
    import scipy.cluster.hierarchy as sch
    if not hasattr(sch, '_LINKAGE_METHODS'):
        sch._LINKAGE_METHODS = {'single', 'complete', 'average', 'weighted', 'centroid', 'median', 'ward'}

    from pypfopt.hierarchical_portfolio import HRPOpt
    hrp = HRPOpt(returns=returns)
    hrp.optimize()
    weights = hrp.clean_weights()
    return pd.Series(weights, index=returns.columns)


def compute_current_system_weights(targets: list[dict]) -> pd.Series:
    """The current cluster-cap system's risk-budget split, read directly
    off compute_rebalance_targets's own output (position_risk per
    instrument) -- NOT recomputed. Expressed as a fraction of total
    absolute risk, matching ERC/HRP's weights-sum-to-1 convention, so all
    three are comparable on an apples-to-apples basis: "where does the
    risk budget go," independent of long/short direction (ERC/HRP are
    direction-agnostic risk-allocation schemes; they don't know about the
    TSMOM signal's sign, so the comparison has to be made at the level of
    risk magnitude, not signed exposure)."""
    risks = pd.Series(
        {t['symbol']: abs(t.get('position_risk') or 0.0) for t in targets if not t.get('error')},
        dtype=float,
    )
    total = risks.sum()
    if total <= 0:
        return risks * 0.0
    return risks / total


def build_comparison_report(instruments: list[dict], current_w: pd.Series,
                             erc_w: pd.Series, hrp_w: pd.Series) -> pd.DataFrame:
    """One row per instrument: symbol, cluster, the three weights, and
    each weight's divergence from the current system (signed, so you can
    see whether ERC/HRP would size UP or DOWN relative to today)."""
    cluster_by_symbol = {instr['symbol']: instr.get('cluster', 'other') for instr in instruments}
    symbols = sorted(set(current_w.index) | set(erc_w.index) | set(hrp_w.index))

    rows = []
    for symbol in symbols:
        cur = float(current_w.get(symbol, 0.0))
        erc = float(erc_w.get(symbol, 0.0))
        hrp = float(hrp_w.get(symbol, 0.0))
        rows.append({
            'symbol': symbol,
            'cluster': cluster_by_symbol.get(symbol, 'other'),
            'current_weight': cur,
            'erc_weight': erc,
            'hrp_weight': hrp,
            'erc_minus_current': erc - cur,
            'hrp_minus_current': hrp - cur,
        })
    df = pd.DataFrame(rows).set_index('symbol')
    return df


def summarize_divergence(report: pd.DataFrame) -> dict:
    """Aggregate divergence metrics: mean absolute deviation and max
    absolute deviation, for both ERC and HRP vs. the current system --
    a quick read on "how different is this, in aggregate" without having
    to eyeball every row."""
    return {
        'erc_mad': report['erc_minus_current'].abs().mean(),
        'erc_max_abs_dev': report['erc_minus_current'].abs().max(),
        'hrp_mad': report['hrp_minus_current'].abs().mean(),
        'hrp_max_abs_dev': report['hrp_minus_current'].abs().max(),
    }


def correlation_view(cov: pd.DataFrame, cluster_by_symbol: dict[str, str]) -> pd.DataFrame:
    """Correlation matrix derived from the covariance matrix, with rows/
    columns sorted by (hand-assigned cluster, symbol) -- a quick visual
    check on whether the data's actual correlation structure agrees with
    the hand-assigned equity/energy/grain/metal/fx buckets (adjacent
    same-cluster instruments should show visibly higher correlation than
    cross-cluster pairs, if the hand-assigned buckets are picking up a
    real factor)."""
    std = np.sqrt(np.diag(cov.values))
    corr = cov.values / np.outer(std, std)
    corr_df = pd.DataFrame(corr, index=cov.index, columns=cov.columns)
    order = sorted(corr_df.index, key=lambda s: (cluster_by_symbol.get(s, 'other'), s))
    return corr_df.loc[order, order]


# ------------------------------------------------------------------
# IB-dependent IO -- not unit-tested (per the brief: manual run only)
# ------------------------------------------------------------------

def fetch_continuous_bars(ib, instr: dict, duration: str = '3 y') -> pd.DataFrame:
    """Continuous front-month daily bars for one instrument, via the same
    IBPySync.cont_future pattern already used in tsmom_rebalance.py's
    _compute_signal -- reused here, not reinvented. Uses resolve_signal_
    symbol's substitution (e.g. a thin micro borrowing its full-size
    sibling's history) exactly as the live signal computation does."""
    from ib_tools.ibpysync import IBPySync

    signal_symbol = resolve_signal_symbol(instr)
    cont = IBPySync.cont_future(signal_symbol, exchange=instr.get('exchange', 'CME'))
    ib.qualify_contracts(cont)
    bars = ib.get_historical_bars(cont, duration=duration, bar_size='1 day')
    if bars is None or bars.height == 0:
        return pd.DataFrame(columns=['date', 'close'])
    df = bars.to_pandas()
    df['date'] = pd.to_datetime(df['date'])
    return df[['date', 'close']]


def fetch_all_continuous_bars(ib, instruments: list[dict], duration: str = '3 y') -> dict[str, pd.DataFrame]:
    price_frames = {}
    for instr in instruments:
        symbol = instr['symbol']
        log.info('Fetching continuous front-month bars for %s...', symbol)
        price_frames[symbol] = fetch_continuous_bars(ib, instr, duration=duration)
    return price_frames


def fetch_db_bars(db_con, instr: dict, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> pd.DataFrame:
    """Continuous front-month daily bars from the duckdb for one instrument.
    Uses resolve_db_symbol to map IBKR tickers to Globex root symbols
    (e.g. J7→6J, BRE→6L) so FX and other divergent names fetch correctly."""
    db_symbol = resolve_db_symbol(instr)
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    result = db_con.execute(_DB_CONT_FRONT_SQL, [db_symbol, cutoff]).fetchdf()
    if result.empty:
        return pd.DataFrame(columns=['date', 'close'])
    result['date'] = pd.to_datetime(result['date'])
    return result[['date', 'close']]


def fetch_all_db_bars(instruments: list[dict], db_path: str = DEFAULT_DB_PATH,
                       lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict[str, pd.DataFrame]:
    """Fetch continuous front-month bars from duckdb for every instrument.
    No IB connection required -- uses the local Databento CME MDP3.0 feed.
    Logs the db_symbol used for each instrument so it's visible when the
    IBKR name and Globex name diverge (J7→6J, BRE→6L, MZC→ZC, etc.)."""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        price_frames = {}
        for instr in instruments:
            symbol = instr['symbol']
            db_symbol = resolve_db_symbol(instr)
            if db_symbol != symbol:
                log.info('%s: fetching duckdb bars under Globex symbol %s', symbol, db_symbol)
            else:
                log.info('Fetching duckdb bars for %s...', symbol)
            price_frames[symbol] = fetch_db_bars(con, instr, lookback_days=lookback_days)
    finally:
        con.close()
    return price_frames


def parse_args(argv=None):
    """argv: explicit CLI-style arg list (e.g. ['--account-equity', '80000']),
    for calling main()/parse_args() directly from a notebook instead of a
    real command line -- defaults to sys.argv as usual when omitted."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--instruments', default=None,
                   help='Comma-separated symbols, or path to a JSON instrument config -- defaults to '
                        "the live system's full KNOWN_INSTRUMENTS universe")
    p.add_argument('--account-equity', type=float, required=True,
                   help="Account equity USD -- required to capture the current system's actual weights "
                        '(passed through to compute_rebalance_targets unchanged)')
    p.add_argument('--vol-target', type=float, default=0.15)
    p.add_argument('--target-portfolio-vol', type=float, default=0.15)
    p.add_argument('--max-cluster-risk-pct', type=float, default=0.25)
    p.add_argument('--min-conviction', type=float, default=0.05)
    p.add_argument('--max-lot-overrun-pct', type=float, default=0.5)
    p.add_argument('--momentum-discount', type=float, default=0.5)
    p.add_argument('--max-contracts', type=int, default=15)
    p.add_argument('--max-notional', type=float, default=None)
    p.add_argument('--halflife', type=float, default=DEFAULT_HALFLIFE_DAYS,
                   help='EWM covariance halflife in days (default: %(default)s -- see module '
                        'docstring, this is a starting assumption, not a confirmed number)')
    p.add_argument('--min-synced-rows', type=int, default=DEFAULT_MIN_SYNCED_ROWS,
                   help='Minimum common-date-window rows required after synchronization '
                        '(default: %(default)s)')
    p.add_argument('--data-source', choices=['ib', 'db'], default='ib',
                   help='Price history source: "ib" (live IB connection, default) or '
                        '"db" (local duckdb, no IB required -- uses Globex root symbols '
                        'via resolve_db_symbol, e.g. J7→6J, BRE→6L). '
                        'db mode skips current-system weights (no IB = no VX gate).')
    p.add_argument('--db-path', default=DEFAULT_DB_PATH,
                   help='Path to the duckdb file (used only with --data-source db, '
                        'default: %(default)s)')
    p.add_argument('--lookback-days', type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help='Calendar days of history to fetch from duckdb '
                        '(used only with --data-source db, default: %(default)s ≈ 3 years)')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=7496)
    p.add_argument('--client-id', type=int, default=19)
    p.add_argument('--no-save', action='store_true', help='Skip saving the report CSVs to results/')
    return p.parse_args(argv)


def main(argv=None):
    """argv: explicit CLI-style arg list, forwarded to parse_args -- lets a
    notebook call main(['--account-equity', '80000', ...]) directly rather
    than going through a real command line. Returns a dict of every
    computed artifact (report/summary/corr/cov/weights/targets), in
    addition to printing/saving them, so a caller (e.g. a notebook) can
    plot or inspect them further without re-running anything."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s [%(levelname)s] %(message)s')

    from ib_tools.ibpysync import IBPySync
    from derivatives_bt_engine.domain.instruments import INSTRUMENTS as KNOWN_INSTRUMENTS
    from derivatives_bt_engine.live.run_tsmom_rebalance import _build_instruments
    from derivatives_bt_engine.live.tsmom_rebalance import compute_rebalance_targets

    args = parse_args(argv)
    instruments_spec = args.instruments or ','.join(sorted(KNOWN_INSTRUMENTS))
    instruments = _build_instruments(instruments_spec, args.max_notional, args.max_contracts)

    log_signal_symbol_fallbacks(instruments)

    config = {
        'vol_target': args.vol_target,
        'max_contracts': args.max_contracts,
        'long_only': False,
        'momentum_discount': args.momentum_discount,
        'account_equity': args.account_equity,
        'target_portfolio_vol': args.target_portfolio_vol,
        'max_cluster_risk_pct': args.max_cluster_risk_pct,
        'min_conviction': args.min_conviction,
        'max_lot_overrun_pct': args.max_lot_overrun_pct,
    }

    if args.data_source == 'db':
        # Offline path: fetch from local duckdb, no IB connection required.
        # current_weights will be empty (VX gate / contract resolution need IB).
        log.info('data_source=db — fetching from %s, no IB connection', args.db_path)
        price_frames = fetch_all_db_bars(instruments, args.db_path, args.lookback_days)
        prices = synchronize_price_frames(price_frames, min_rows=args.min_synced_rows)
        returns = compute_log_returns(prices)
        cov = compute_ewm_covariance(returns, halflife=args.halflife)
        erc_w = compute_erc_weights(cov)
        hrp_w = compute_hrp_weights(returns)
        targets = []
        current_w = pd.Series(dtype=float)
    else:
        ib = IBPySync()
        ib.connect(args.host, args.port, args.client_id)
        try:
            price_frames = fetch_all_continuous_bars(ib, instruments)
            prices = synchronize_price_frames(price_frames, min_rows=args.min_synced_rows)
            returns = compute_log_returns(prices)
            cov = compute_ewm_covariance(returns, halflife=args.halflife)
            erc_w = compute_erc_weights(cov)
            hrp_w = compute_hrp_weights(returns)
            targets = compute_rebalance_targets(ib, instruments, config)
            current_w = compute_current_system_weights(targets)
        finally:
            ib.disconnect()

    report = build_comparison_report(instruments, current_w, erc_w, hrp_w)
    summary = summarize_divergence(report)
    cluster_by_symbol = {instr['symbol']: instr.get('cluster', 'other') for instr in instruments}
    corr = correlation_view(cov, cluster_by_symbol)

    print(report.round(4).to_string())
    print()
    print('Aggregate divergence:', {k: round(v, 4) for k, v in summary.items()})
    print()
    print('Correlation matrix (sorted by hand-assigned cluster):')
    print(corr.round(2).to_string())

    if not args.no_save:
        import os
        from datetime import datetime
        results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'results'))
        os.makedirs(results_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        report.to_csv(os.path.join(results_dir, f'tsmom_risk_budget_diagnostic_{ts}.csv'))
        corr.to_csv(os.path.join(results_dir, f'tsmom_risk_budget_diagnostic_corr_{ts}.csv'))

    return {
        'report': report,
        'summary': summary,
        'corr': corr,
        'cov': cov,
        'returns': returns,
        'targets': targets,
        'current_weights': current_w,
        'erc_weights': erc_w,
        'hrp_weights': hrp_w,
    }


if __name__ == '__main__':
    main()

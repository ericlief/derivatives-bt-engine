"""
Stripped-down TSMOM test: binary sign(signal) direction, flat per-asset
vol-parity sizing (Levine & Pedersen 2016, "Which Trend Is Your Friend?",
Table 1 methodology -- every asset gets the SAME flat annualized-$-vol
target, no cluster/bucket hierarchy, no risk_scalar clamp, no cluster risk
cap, no max_notional/max_contracts ceiling). Monthly rebalance, matching
tsmom_backtester.py's cadence. Reuses only pure data-loading/signal
functions (load_portfolio_data, calculate_trend_strength, _month_end_dates)
-- none of tsmom_backtester.py's own position-sizing.

Written to answer a specific question (see
research/research_trend_strength_crossover_signal.md, Part 2 §6): does
momentum_discount (the flat Correction/Rebound de-risking multiplier in
tsmom_signal.py's compute_position_scalar) actually help on this project's
own recent data? The existing tsmom-bt CLI (tsmom_backtester.py) turned out
to be unsuitable for this -- it sizes each symbol independently against its
own vol_target/max_notional/max_contracts with NO cross-instrument risk
cap (unlike live/tsmom_rebalance.py's compute_desired_risk_budget/
apply_cluster_risk_cap), so a correlated multi-symbol run there produces
wildly overstated realized vol (82-90% against a 15% target was observed).
This script sidesteps that gap entirely with a much simpler, literature-
literal sizing scheme instead of trying to patch it.

Sharpe is invariant to FLAT_PER_ASSET_VOL_TARGET_USD's absolute level (a
uniform leverage rescale) -- the specific value only matters for realistic
contract-count rounding, not for the Sharpe comparison across
--momentum-discounts. An earlier version also reported a return figure
post-hoc rescaled to a 10% vol target (matching Figure 4's own stated
methodology in Goulding/Harvey/Mazzoleni, "returns scaled to achieve 10%
annualized monthly volatility") -- this was REMOVED after it was flagged
as misleading: the rescale multiplied only the return figure, while
total_fees (reported alongside it) stayed at its actual, un-rescaled
dollar value. That's only truly consistent under an idealized assumption
(fees scale exactly linearly with position size, same as gross P&L) that
integer contract-count rounding breaks in practice. Sharpe alone is the
valid, scale-invariant comparison and needs no such rescale/asterisk --
report raw ann_ret/ann_vol/fees together instead, all at the same actual
scale, or explicitly rescale fees by the same factor if a vol-normalized
return figure is ever needed again.

Transaction costs: get_spec(symbol)['commission'] is charged using the
SAME asymmetric convention as tsmom_backtester.py's own _rebalance_to
(mirrored from FuturesPosition.calculate_pnl) -- opening or adding to a
position is free; only the closing/shrinking leg is charged, at
2 * commission * closed_qty (both round-trip legs bundled into the
close). A same-direction resize charges only the portion that shrinks
back toward zero; a full close or a sign flip charges the entire prior
side. Plus a mandatory quarterly roll charge (2 * abs(held) * commission,
same Mon-before-3rd-Friday Mar/Jun/Sep/Dec schedule
FuturesPosition.roll_date uses, same "close old + reopen new, reopening
is free" logic as tsmom_backtester.py's _process_roll) -- a roll is a
real close-old/open-new round trip and costs commission twice even on a
quarter where the continuous price series happens not to jump.

Two corrections here, both flagged directly by the user rather than
caught in review: (1) an earlier version of this script had NO
transaction costs at all, which inflated its Sharpe/return figures versus
a realistic backtest; (2) the version right after that charged commission
symmetrically on every monthly resize (abs(new_target - held) * 1x
commission, in both directions) -- this overcharged every position
INCREASE (which should be free, same as opening) and used the wrong
quantity/multiplier on decreases and flips (e.g. a full sign flip from
+5 to -3 charged abs(-3-5)=8 units at 1x, instead of the correct
closed_qty=5 at 2x -- closing the whole long side, with the new short
open free). Fixed to match tsmom_backtester.py's _rebalance_to exactly.

Known limitation NOT fixed here, and not unique to this script: the
continuous front-month price series this project uses everywhere
(FuturesDataLoader.daily / futures_dataloader._CONTINUOUS_FRONT_MONTH_SQL)
picks, independently for EVERY date, whichever not-yet-expired contract
has the soonest expiration AMONG THOSE WITH A PRINTED BAR THAT DAY
(`row_number() OVER (PARTITION BY ts_event ORDER BY expiration ASC)`).
This has no memory of which contract was "front" yesterday -- it's
recomputed from scratch each day. Confirmed directly against ZN, March
2023: the Mar'23 contract (instrument_id 397730) trades a genuinely thin
few hundred contracts/day even at its most active, has NO printed bar at
all on 2023-03-19 (so that day's front-month price is silently read from
Jun'23 instead, already the real liquid contract by volume), reappears
with one more trade on 2023-03-20 (so the ranking flips BACK to Mar'23,
a lower price level), then goes quiet for good from 2023-03-21 onward
(ranking settles on Jun'23). The result is a pure contract-switch
artifact -- not a real price move -- read by every downstream consumer as
if it were one instrument's continuous price path. Same root phenomenon
(thin trading right before a contract's own expiration) as the BRE/6L
stuck-roll bug fixed earlier this project's history, surfacing as a
different defect: there, a position got stuck waiting for an exact-date
match that never came; here, the continuous *price series itself*
silently swaps which contract it's quoting. Both naked_futures.py's
Backtester/FuturesPosition path and tsmom_backtester.py's own multi-symbol
path read from this exact same series, so this affects all three backtest
paths equally -- worth its own separate investigation/fix, not patched
here.

Run:
    python -m scripts.tsmom_binary_vol_parity_backtest
    python -m scripts.tsmom_binary_vol_parity_backtest --momentum-discounts 0.5,1.0 --years 2023-2026
    python -m scripts.tsmom_binary_vol_parity_backtest --symbols ES,NQ,GC --years 2015-2026
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.futures_signal_generator import FuturesSignalGenerator
from derivatives_bt_engine.domain.instruments import get_spec
from derivatives_bt_engine.domain.tsmom_backtester import _month_end_dates, load_portfolio_data
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────────────
# BRE (J7's/JPY's fellow FX symbol) deliberately excluded from both universe
# lists below -- its continuous series has a known, unresolved sticky-anchor
# bug (only 71.5% of dates survive the sticky join, vs 100% for every other
# symbol; see research/research_futures_roll_logic_and_active_months.md
# §1.2/§2). 6M stands in for FX-EM exposure instead: correlated, better
# volume, no known data-quality issue.
# DEFAULT_SYMBOLS = ['ES', 'NQ', 'CL', 'GC', 'SI', 'ZN', 'ZT', 'ZL', 'ZC', 'ZS', 'ZW', 'JPY', '6M']
DEFAULT_SYMBOLS = ['MES', 'MNQ', 'MCL', 'MGC', 'SIL', 'MTN', 'MZL', 'MZC', 'MZS', 'MZW', 'J7', '6M']
DEFAULT_YEARS = '2010-2026'
# Signal history buffer before --years' own start, so ts_fast/ts_slow/
# fast_return/slow_return are non-null from the test window's first rebalance -- a
# FIXED offset from `start`, not a fixed absolute date: an earlier version
# hardcoded 2018-01-01 regardless of --years, which silently capped the
# effective test window at 2018+ even when --years asked for an earlier
# start (e.g. --years 2010-2026 tested the same window as --years
# 2015-2026 until this was fixed -- confirmed directly, both gave
# n_days=2636).
WARMUP_DAYS_BEFORE_START = 400  # > 252 (slow_return's own lookback) with headroom
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD = 10_000.0  # 1% of default capital, same for every asset -- no clustering
DEFAULT_MOMENTUM_DISCOUNTS = [0.5, 1.0]

# Goulding, Harvey & Mazzoleni (2023), "Breaking Bad Trends" -- fast/slow
# horizons for the paper's OWN eq. 4 state classification (Bull/Correction/
# Bear/Rebound) and eq. 8-10 a_Co/a_Re mixing-parameter estimator, kept
# separate from calculate_trend_strength's existing 3m/12m ts_fast/ts_slow
# (which stay canonical/untouched per that function's own docstring).
# The paper's SLOW/FAST are literally averages of trailing calendar-month
# returns (eq. 1-2); in this project's log-return convention that's exactly
# proportional to log_price.diff(N)/N for N=252/42 days (log returns
# telescope additively, so sum-of-daily = cumulative, with no error beyond
# the calendar-month-vs-fixed-trading-day-window boundary this project
# already accepts elsewhere for "252 = 1 year") -- so FAST_RETURN/SLOW_RETURN
# below reuse calculate_trend_strength's own slow_return and a newly added
# fast_return directly, not a separate rolling-mean recomputation.
PAPER_FAST_DAYS = 42   # ~2 months, matching the paper's multi-asset FAST
PAPER_SLOW_DAYS = 252  # ~12 months, matching the paper's SLOW
MIN_MONTHS_PER_PHASE = 12  # paper's own warm-up requirement per Appendix C


def _paper_state(fast_return: Optional[float], slow_ret: Optional[float]) -> Optional[str]:
    """Eq. 4's Bull/Correction/Bear/Rebound, from the sign of the paper's
    OWN raw (non-vol-normalized) fast/slow average returns -- distinct from
    but sign-equivalent to tsmom_signal.classify_regime's ts_fast/ts_slow-
    based version (dividing by a positive vol term never changes sign),
    kept separate here only because the paper's own horizons (2m/12m)
    differ from this project's canonical ones (3m/12m)."""
    if fast_return is None or slow_ret is None:
        return None
    if (isinstance(fast_return, float) and math.isnan(fast_return)) or (isinstance(slow_ret, float) and math.isnan(slow_ret)):
        return None
    slow_up, fast_up = slow_ret >= 0, fast_return >= 0
    if slow_up and fast_up:
        return 'bull'
    if slow_up and not fast_up:
        return 'correction'
    if not slow_up and not fast_up:
        return 'bear'
    return 'rebound'


def _build_monthly_state_return_history(signals: dict[str, pl.DataFrame],
                                         rebal_dates: list[date]) -> pl.DataFrame:
    """One row per (symbol, rebalance date) with the state observed at the
    START of that month (from the PRIOR rebalance date's fast_return/slow_return) paired
    with that month's own realized log return (prior rebal_date's close to
    this one's) -- i.e. exactly the (state, subsequent-month return) pairs
    Appendix C's AVG[r|s]/AVG[r^2|s] are computed over. Pooled across every
    symbol in `signals` (not per-instrument, unlike the paper's own
    single-asset design) since this project's per-symbol history (~15
    years) gives too few Correction/Rebound months on its own for a stable
    estimate -- an explicit, flagged deviation from the paper, not an
    oversight."""
    rows = []
    for sym, sig in signals.items():
        prev_close: Optional[float] = None
        prev_state: Optional[str] = None
        for d in rebal_dates:
            row = sig.filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            close, slow_ret, fast_return = row['close'][0], row['slow_return'][0], row['fast_return'][0]
            state = _paper_state(fast_return, slow_ret)
            if prev_close is not None and prev_state is not None and prev_close > 0:
                rows.append({'date': d, 'symbol': sym, 'state': prev_state,
                             'monthly_return': math.log(close / prev_close)})
            prev_close, prev_state = close, state
    if not rows:
        return pl.DataFrame(schema={'date': pl.Date, 'symbol': pl.Utf8,
                                     'state': pl.Utf8, 'monthly_return': pl.Float64})
    return pl.DataFrame(rows)


def _estimate_mixing_params(history: pl.DataFrame, as_of: date,
                             min_months: int = MIN_MONTHS_PER_PHASE) -> tuple[float, float]:
    """Appendix C, eq. 8-10 -- a_Co/a_Re from every (state, monthly_return)
    pair strictly before `as_of` (expanding window, no lookahead; pooled
    across the whole universe, see _build_monthly_state_return_history).
    Falls back to the uninformed (0.5, 0.5) -- equivalent to the flat
    momentum_discount's no-op case -- whenever there isn't yet
    `min_months` of pooled history in EITHER the Correction or Rebound
    phase, or the normalizer C / either phase's mean-squared return is
    degenerate (zero); the paper's own rule for insufficient per-asset
    history is to exclude the asset for that month entirely, which doesn't
    map cleanly onto a pooled, always-in-the-portfolio backtest, so this
    is an explicit, flagged adaptation, not a literal reproduction.

    Eq. 9's sign, as extracted from the scanned paper, appears identical in
    form to eq. 8's (both "1 - ...") -- which contradicts the paper's own
    prose ("if returns tend to be positive after rebounds... a_Re > 0.5")
    given a single shared, "typically positive" C. Uses the "+" form here
    (a_Re = 1/2*(1 + (1/C)*AVG[r|Re]/AVG[r^2|Re])) as the only version
    self-consistent with that prose -- see
    research/research_trend_strength_crossover_signal.md Part 2 §6 for the
    full errata discussion; this is flagged, not confirmed against the
    primary source's actual typeset sign."""
    prior = history.filter(pl.col('date') < as_of)
    if prior.height == 0:
        return 0.5, 0.5

    def _stats(state: str) -> tuple[int, float, float]:
        sub = prior.filter(pl.col('state') == state)
        if sub.height == 0:
            return 0, 0.0, 0.0
        r = sub['monthly_return']
        return sub.height, r.mean(), (r * r).mean()

    n_bu, avg_r_bu, avg_r2_bu = _stats('bull')
    n_be, avg_r_be, avg_r2_be = _stats('bear')
    n_co, avg_r_co, avg_r2_co = _stats('correction')
    n_re, avg_r_re, avg_r2_re = _stats('rebound')

    if n_co < min_months or n_re < min_months:
        return 0.5, 0.5
    freq_tot = n_bu + n_be
    if freq_tot == 0 or avg_r2_bu == 0 or avg_r2_be == 0:
        return 0.5, 0.5

    C = (n_bu / freq_tot) * (avg_r_bu / avg_r2_bu) - (n_be / freq_tot) * (avg_r_be / avg_r2_be)
    if C == 0 or avg_r2_co == 0 or avg_r2_re == 0:
        return 0.5, 0.5

    a_co = 0.5 * (1 - (1 / C) * (avg_r_co / avg_r2_co))
    a_re = 0.5 * (1 + (1 / C) * (avg_r_re / avg_r2_re))
    return max(0.0, min(1.0, a_co)), max(0.0, min(1.0, a_re))


def run(symbols: list[str], start: date, end: date, momentum_discount: float,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        flat_per_asset_vol_target_usd: float = DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD,
        warmup_start: Optional[date] = None,
        weighting_mode: str = 'flat_discount',
        save_prefix: Optional[str] = None) -> dict:
    """weighting_mode:
        'flat_discount' -- existing behaviour: ts_fast/ts_slow-based `ts`/`regime`
            (calculate_trend_strength's canonical columns), momentum_discount
            applied as a flat multiplier in Correction/Rebound.
        'dynamic' -- Goulding/Harvey/Mazzoleni eq. 4/7-8-10: paper's own
            2m/12m raw-return state classification, and a_Co/a_Re mixing
            weights (re-estimated at every rebalance date from all PRIOR
            pooled history, no lookahead) blending the slow/fast direction
            in Correction/Rebound instead of a flat discount. `momentum_discount`
            is ignored in this mode.

    warmup_start defaults to WARMUP_DAYS_BEFORE_START before `start` (not a
    fixed absolute date -- see that constant's own comment for why).

    save_prefix, if given, writes two CSVs for after-the-fact inspection --
    "{save_prefix}_{mode}_daily.csv" (date, capital, ret) and
    "{save_prefix}_{mode}_rebalances.csv" (one row per symbol actually
    rebalanced: date, state/regime, a_co/a_re, ts/slow_return/fast_return, weight,
    prior->target contracts, fee) -- neither existed anywhere before this,
    so there was no way to audit what a given run actually did after the
    fact, only the final summary numbers.
    """
    if warmup_start is None:
        warmup_start = start - timedelta(days=WARMUP_DAYS_BEFORE_START)
    price_data, _ = load_portfolio_data(symbols)

    signals = {}
    for sym, df in price_data.items():
        df = df.filter((pl.col('ts_event') >= warmup_start) & (pl.col('ts_event') <= end))
        sig = calculate_trend_strength(df)
        # fast_return: paper's own 2-month FAST horizon (calculate_trend_strength's
        # canonical ts_fast/regime stay untouched -- this is an addition on top,
        # from `close` directly since log_price is dropped by that function).
        sig = sig.with_columns(fast_return=pl.col('close').log().diff(PAPER_FAST_DAYS))
        signals[sym] = sig.select(['ts_event', 'close', 'ts', 'daily_std', 'regime', 'slow_return', 'fast_return']).sort('ts_event')

    rebal_dates = sorted(d for d in _month_end_dates(price_data) if start <= d <= end)
    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in signals.values())))
    all_dates = [d for d in all_dates if start <= d <= end]
    rebal_set = set(rebal_dates)

    monthly_history = (_build_monthly_state_return_history(signals, rebal_dates)
                        if weighting_mode == 'dynamic' else None)

    # Mandatory quarterly contract roll (same schedule FuturesSignalGenerator/
    # tsmom_backtester.py use for FuturesPosition.roll_date -- Monday prior to
    # the third Friday of Mar/Jun/Sep/Dec) -- a real close-old/open-new round
    # trip that costs commission twice even when this project's continuous
    # front-month price series (FuturesDataLoader.daily) doesn't itself show a
    # price change that day. Snapped onto the nearest date actually present in
    # the loaded data (a specific calendar roll_date can fall on a non-trading
    # day for a given symbol).
    roll_dates = set(FuturesSignalGenerator._get_quarterly_roll_dates(start, end))

    held = {s: 0 for s in symbols}
    prev_close: dict[str, float] = {}
    capital = initial_capital
    total_fees = 0.0
    rows = []
    # One row per (rebalance date, symbol) actually acted on -- the only
    # way to audit *why* a run's number came out the way it did (state,
    # weight, a_Co/a_Re, prior->target, fee) instead of just trusting the
    # final Sharpe. Nothing was saved anywhere before this -- confirmed
    # there was no way to tell, after the fact, what a given run actually
    # did at each rebalance.
    rebalance_events = []

    for d in all_dates:
        # Mark-to-market with yesterday's held contracts against today's move.
        pnl = 0.0
        for s in symbols:
            row = signals[s].filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            close = row['close'][0]
            if s in prev_close and held[s] != 0:
                spec = get_spec(s)
                pnl += held[s] * spec['multiplier'] * (close - prev_close[s])
            prev_close[s] = close
        capital += pnl

        # Mandatory quarterly roll: commission on a full close+reopen of
        # whatever is currently held, no price/quantity effect (see note above).
        if d in roll_dates:
            fees = 0.0
            for s in symbols:
                if held[s] != 0:
                    fees += 2 * abs(held[s]) * get_spec(s)['commission']
            capital -= fees
            total_fees += fees

        # Rebalance at month-end using today's just-observed signal.
        if d in rebal_set:
            fees = 0.0
            # Pooled a_Co/a_Re re-estimated once per rebalance date (not
            # per symbol -- see _build_monthly_state_return_history), from
            # all history strictly before `d`.
            a_co, a_re = (_estimate_mixing_params(monthly_history, d)
                          if weighting_mode == 'dynamic' else (None, None))
            for s in symbols:
                row = signals[s].filter(pl.col('ts_event') == d)
                if row.height == 0:
                    continue
                dstd = row['daily_std'][0]
                # `dstd <= 0` alone doesn't catch NaN -- comparisons with NaN
                # are always False in Python, so a NaN daily_std (e.g. from a
                # bad/missing price on some date) silently slipped through
                # this guard, propagated into dollar_vol_per_contract, and
                # crashed round() downstream with "cannot convert float NaN
                # to integer" -- confirmed on a real run.
                dstd_bad = dstd is None or (isinstance(dstd, float) and math.isnan(dstd)) or dstd <= 0

                # slow_return/fast_return are computed unconditionally above
                # (regardless of weighting_mode) so always read them here too
                # -- audit rows should show the underlying fast/slow returns
                # even on a flat_discount run, not just when 'dynamic' mode
                # is the one actually consuming them for sizing.
                slow_ret_val, fast_return_val = row['slow_return'][0], row['fast_return'][0]
                state, ts_val, regime = None, None, None
                if weighting_mode == 'dynamic':
                    state = _paper_state(fast_return_val, slow_ret_val)
                    if state is None or dstd_bad:
                        logger.warning(f"{s} on {d}: skipping rebalance, invalid signal "
                                        f"(fast_return={fast_return_val}, slow_return={slow_ret_val}, daily_std={dstd})")
                        continue
                    if state == 'bull':
                        weight = 1.0
                    elif state == 'bear':
                        weight = -1.0
                    elif state == 'correction':
                        # (1-a_co)*sign(slow=+1) + a_co*sign(fast=-1)
                        weight = 1.0 - 2 * a_co
                    else:  # rebound
                        # (1-a_re)*sign(slow=-1) + a_re*sign(fast=+1)
                        weight = 2 * a_re - 1.0
                else:
                    ts_val, regime = row['ts'][0], row['regime'][0]
                    if (ts_val is None or (isinstance(ts_val, float) and math.isnan(ts_val)) or dstd_bad):
                        logger.warning(f"{s} on {d}: skipping rebalance, invalid signal "
                                        f"(ts={ts_val}, daily_std={dstd})")
                        continue
                    direction = 1.0 if ts_val > 0 else (-1.0 if ts_val < 0 else 0.0)
                    discount = momentum_discount if regime in ('correction', 'rebound') else 1.0
                    weight = direction * discount

                spec = get_spec(s)
                close = row['close'][0]
                dollar_vol_per_contract = close * spec['multiplier'] * dstd * (252 ** 0.5)
                if dollar_vol_per_contract <= 0:
                    continue
                new_target = round(weight * flat_per_asset_vol_target_usd / dollar_vol_per_contract)
                prior = held[s]
                # Same asymmetric convention as tsmom_backtester.py's
                # _rebalance_to: opening or adding to a position is free;
                # only the closing/shrinking leg charges commission, at 2x
                # (both round-trip legs bundled into the close), matching
                # FuturesPosition.calculate_pnl's fee model. A same-
                # direction resize only charges for the portion that
                # shrinks back toward zero, not the portion added.
                if prior == 0:
                    closed_qty = 0
                elif new_target == 0 or (prior > 0) != (new_target > 0):
                    closed_qty = abs(prior)
                else:
                    closed_qty = max(0, abs(prior) - abs(new_target))
                event_fee = 2 * closed_qty * spec['commission']
                fees += event_fee
                rebalance_events.append({
                    'date': d, 'symbol': s, 'mode': weighting_mode,
                    'state': state if weighting_mode == 'dynamic' else regime,
                    'a_co': a_co, 'a_re': a_re,
                    'ts': ts_val, 'slow_return': slow_ret_val, 'fast_return': fast_return_val,
                    'weight': round(weight, 4), 'close': close, 'daily_std': dstd,
                    'prior_contracts': prior, 'target_contracts': new_target,
                    'fee': round(event_fee, 2),
                })
                held[s] = new_target
            capital -= fees
            total_fees += fees

        rows.append({'date': d, 'capital': capital})

    stats = pl.DataFrame(rows).with_columns(
        ret=pl.col('capital') / pl.col('capital').shift(1) - 1
    ).drop_nulls('ret')

    mean_ret, std_ret = stats['ret'].mean(), stats['ret'].std()
    ann_ret, ann_vol = mean_ret * 252, std_ret * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol else None
    running_max = stats['capital'].cum_max()
    dd_pct = ((stats['capital'] - running_max) / running_max * 100).min()

    if save_prefix:
        tag = f"{weighting_mode}" + (f"_{momentum_discount}" if weighting_mode == 'flat_discount' else '')
        daily_path = f"{save_prefix}_{tag}_daily.csv"
        events_path = f"{save_prefix}_{tag}_rebalances.csv"
        # Round every float column to 4dp for CSV readability -- the raw
        # values (e.g. daily_std=0.015278191541596027) are full float64
        # precision and unreadable in a spreadsheet/terminal.
        stats.with_columns(pl.col(pl.Float64).round(4)).write_csv(daily_path)
        pl.DataFrame(rebalance_events).with_columns(pl.col(pl.Float64).round(4)).write_csv(events_path)
        logger.info(f"Saved {daily_path} ({stats.height} rows) and {events_path} ({len(rebalance_events)} rows)")

    return {
        'mode': weighting_mode,
        'discount': momentum_discount if weighting_mode == 'flat_discount' else None,
        'n_days': stats.height,
        'ann_ret_pct': round(ann_ret * 100, 2),
        'ann_vol_pct': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd_pct': round(dd_pct, 2),
        'total_fees': round(total_fees, 2),
    }


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                    help='Comma-separated futures symbols, must be known instruments.py symbols (default: %(default)s)')
    p.add_argument('--years', default=DEFAULT_YEARS, help='Year range as START-END, inclusive (default: %(default)s)')
    p.add_argument('--momentum-discounts', default=','.join(str(d) for d in DEFAULT_MOMENTUM_DISCOUNTS),
                    help='Comma-separated momentum_discount values to compare, one run each (default: %(default)s)')
    p.add_argument('--initial-capital', type=float, default=DEFAULT_INITIAL_CAPITAL)
    p.add_argument('--flat-vol-target', type=float, default=DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD,
                    help='Flat annualized $ vol target, same for every asset -- no clustering (default: %(default)s)')
    p.add_argument('--include-dynamic', action='store_true',
                    help="Also run the paper's own eq. 4/7-10 dynamic a_Co/a_Re "
                         "reweighting (Goulding/Harvey/Mazzoleni), alongside the "
                         "--momentum-discounts flat-discount run(s) (default: off)")
    p.add_argument('--save-csv', default=None, metavar='PREFIX',
                    help='Write per-run "{PREFIX}_{mode}_daily.csv" (date, capital, ret) and '
                         '"{PREFIX}_{mode}_rebalances.csv" (one row per symbol actually rebalanced: '
                         'state/regime, a_co/a_re, ts/slow_return/fast_return, weight, prior->target, fee) for '
                         'after-the-fact inspection -- neither is saved anywhere without this '
                         '(default: off, only the summary dict is printed)')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    start_year, end_year = args.years.split('-')
    start, end = date(int(start_year), 1, 1), date(int(end_year), 12, 31)
    discounts = [float(d.strip()) for d in args.momentum_discounts.split(',') if d.strip()]

    for discount in discounts:
        result = run(symbols, start, end, discount,
                      initial_capital=args.initial_capital,
                      flat_per_asset_vol_target_usd=args.flat_vol_target,
                      save_prefix=args.save_csv)
        print(result)

    if args.include_dynamic:
        result = run(symbols, start, end, momentum_discount=1.0,
                      initial_capital=args.initial_capital,
                      flat_per_asset_vol_target_usd=args.flat_vol_target,
                      weighting_mode='dynamic',
                      save_prefix=args.save_csv)
        print(result)


if __name__ == '__main__':
    main()

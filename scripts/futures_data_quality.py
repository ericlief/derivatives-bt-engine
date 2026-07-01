"""
Futures data quality diagnostic: compare IB API vs DB rolling-view prices,
flag stale/illiquid periods, detect roll date contamination, assess
listwise vs pairwise deletion strategies, compare resulting covariance
matrices, and produce a structured recommendation.

Inputs (all accepted as either polars or pandas DataFrames):
    ib_prices  : wide price table — date col + one column per ticker (IB source)
    db_prices  : same shape/columns (DB rolling view; optional)
    volume     : same shape as prices (optional; enables Step 2)
    roll_dates : {ticker: [date_strings]} (optional; enables precise Step 3)

Computation is polars-based throughout (project convention). Conversion to
pandas happens exactly once, at the PyPortfolioOpt call site in Step 5
(risk_models.sample_cov requires pandas input), consistent with CLAUDE.md.

Run (live IB connection required for the fetch step):
    python -m scripts.futures_data_quality --account-equity 80000
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import polars as pl

log = logging.getLogger(__name__)

DATE_COL = 'date'
STALE_RUN_THRESHOLD   = 3
OUTLIER_SIGMA         = 5.0
PAIRWISE_N_TOLERANCE  = 0.20
VOL_DIFF_THRESHOLD    = 0.05
COV_DIFF_THRESHOLD    = 0.01
FREQUENCY             = 252
TRAILING_STD_WINDOW   = 60     # days for auto roll-detection


# ------------------------------------------------------------------
# Input normalisation — accept polars or pandas, wide or long
# ------------------------------------------------------------------

def _to_polars_wide(prices) -> pl.DataFrame:
    """Accept a polars or pandas wide price table (date col + ticker cols)
    and normalise to a polars DataFrame with a proper Date column."""
    if not isinstance(prices, pl.DataFrame):
        prices = pl.from_pandas(prices.reset_index() if prices.index.name else prices.reset_index())
    # Ensure the date column is cast to pl.Date so rolling windows work.
    if DATE_COL in prices.columns and prices[DATE_COL].dtype != pl.Date:
        prices = prices.with_columns(pl.col(DATE_COL).cast(pl.Date))
    return prices.sort(DATE_COL)


def ticker_cols(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c != DATE_COL]


# ------------------------------------------------------------------
# Step 1 — Stale Price Detection
# ------------------------------------------------------------------

def compute_log_returns(prices: pl.DataFrame) -> pl.DataFrame:
    """Daily log returns per ticker column, with the date column preserved.
    Result has the same columns as prices; first row is null for every ticker."""
    tickers = ticker_cols(prices)
    exprs = [pl.col(DATE_COL)] + [
        (pl.col(t).log() - pl.col(t).log().shift(1)).alias(t)
        for t in tickers
    ]
    return prices.select(exprs)


def detect_stale_prices(returns: pl.DataFrame, run_threshold: int = STALE_RUN_THRESHOLD) -> dict:
    """
    Step 1: flag consecutive zero-return runs per instrument.

    Returns a dict with:
      - 'stale_mask'      : polars DataFrame (bool columns, one per ticker)
      - 'runs'            : {ticker: list of (start_date, end_date, length)}
      - 'flagged_tickers' : {ticker: total_flagged_rows}
      - 'total_flagged'   : int
    """
    tickers = ticker_cols(returns)
    dates = returns[DATE_COL].to_list()
    stale_cols = {}
    runs_by_ticker = {}
    flagged_counts = {}

    for t in tickers:
        vals = returns[t].to_list()
        is_zero = [v is not None and v == 0.0 for v in vals]

        # Detect consecutive-zero runs of length >= run_threshold.
        runs = []
        i = 0
        while i < len(is_zero):
            if is_zero[i]:
                j = i
                while j < len(is_zero) and is_zero[j]:
                    j += 1
                length = j - i
                if length >= run_threshold:
                    runs.append((dates[i], dates[j - 1], length))
                i = j
            else:
                i += 1

        # Build per-row stale mask: any zero that's part of a run >= threshold.
        run_set = set()
        for start, end, _ in runs:
            for k, d in enumerate(dates):
                if start <= d <= end:
                    run_set.add(k)
        mask = [k in run_set for k in range(len(dates))]
        stale_cols[t] = mask
        runs_by_ticker[t] = runs
        flagged_counts[t] = sum(mask)

    stale_mask = pl.DataFrame({DATE_COL: dates, **stale_cols}).with_columns(
        pl.col(DATE_COL).cast(pl.Date)
    )
    total = sum(flagged_counts.values())

    return {
        'stale_mask': stale_mask,
        'runs': runs_by_ticker,
        'flagged_tickers': {t: n for t, n in flagged_counts.items() if n > 0},
        'total_flagged': total,
    }


# ------------------------------------------------------------------
# Step 2 — Volume / Liquidity Gate
# ------------------------------------------------------------------

def detect_low_volume(prices: pl.DataFrame, volume: Optional[pl.DataFrame],
                      stale_result: Optional[dict] = None,
                      volume_threshold_factor: float = 0.1) -> dict:
    """
    Step 2: flag low-volume rows and cross-reference with stale-return flags.

    volume_threshold per ticker = median(ticker volume) * volume_threshold_factor.
    Returns dict with 'low_vol_mask', 'overlap_count', 'genuine_zero_count', 'limitation'.
    """
    if volume is None:
        return {
            'limitation': 'No volume data provided — Step 2 skipped; stale detection relies on zero-return runs only.',
            'low_vol_mask': None,
            'overlap_count': None,
            'genuine_zero_count': None,
        }

    tickers = ticker_cols(prices)
    volume = _to_polars_wide(volume)
    dates = prices[DATE_COL].to_list()
    low_vol_cols = {}

    for t in tickers:
        if t not in volume.columns:
            low_vol_cols[t] = [False] * len(dates)
            continue
        vols = volume[t].to_list()
        median_vol = float(np.nanmedian([v for v in vols if v is not None]))
        threshold = median_vol * volume_threshold_factor
        low_vol_cols[t] = [v is not None and v < threshold for v in vols]

    low_vol_mask = pl.DataFrame({DATE_COL: dates, **low_vol_cols}).with_columns(
        pl.col(DATE_COL).cast(pl.Date)
    )

    overlap_count = None
    genuine_zero_count = None
    if stale_result and stale_result['stale_mask'] is not None:
        sm = stale_result['stale_mask']
        overlap = 0
        genuine = 0
        for t in tickers:
            if t not in sm.columns or t not in low_vol_cols:
                continue
            stale_flags = sm[t].to_list()
            lv_flags = low_vol_cols[t]
            overlap += sum(s and l for s, l in zip(stale_flags, lv_flags))
            genuine += sum(s and not l for s, l in zip(stale_flags, lv_flags))
        overlap_count = overlap
        genuine_zero_count = genuine

    return {
        'low_vol_mask': low_vol_mask,
        'overlap_count': overlap_count,
        'genuine_zero_count': genuine_zero_count,
        'limitation': None,
    }


# ------------------------------------------------------------------
# Step 3 — Roll Date Contamination
# ------------------------------------------------------------------

def detect_roll_contamination(returns: pl.DataFrame,
                               roll_dates: Optional[dict] = None,
                               outlier_sigma: float = OUTLIER_SIGMA,
                               trailing_window: int = TRAILING_STD_WINDOW) -> dict:
    """
    Step 3: flag returns on roll dates (provided) or auto-detected
    (|return| > outlier_sigma * trailing std).

    Returns dict with:
      - 'auto_detected'   : {ticker: [dates]}  — dates flagged automatically
      - 'provided_rolls'  : {ticker: [dates]}  — from roll_dates input
      - 'outlier_returns' : {ticker: {date: return_value}}  — extreme returns
      - 'roll_mask'       : polars DataFrame (bool, one col per ticker)
    """
    tickers = ticker_cols(returns)
    dates = returns[DATE_COL].to_list()
    auto_detected = {}
    outlier_returns = {}
    roll_mask_cols = {}

    for t in tickers:
        vals = returns[t].to_list()
        arr = np.array([v if v is not None else np.nan for v in vals], dtype=float)

        # Rolling trailing std (exclude current day to avoid look-ahead).
        flagged_indices = set()
        auto_dates = []
        for i in range(trailing_window, len(arr)):
            window = arr[max(0, i - trailing_window):i]
            std = np.nanstd(window)
            if std > 0 and abs(arr[i]) > outlier_sigma * std:
                flagged_indices.add(i)
                auto_dates.append(dates[i])

        auto_detected[t] = auto_dates
        outlier_returns[t] = {dates[i]: arr[i] for i in flagged_indices}

        # If provided roll dates are given, also flag those rows.
        provided = roll_dates.get(t, []) if roll_dates else []
        provided_set = set(str(d) for d in provided)
        for i, d in enumerate(dates):
            if str(d) in provided_set:
                flagged_indices.add(i)

        roll_mask_cols[t] = [k in flagged_indices for k in range(len(dates))]

    roll_mask = pl.DataFrame({DATE_COL: dates, **roll_mask_cols}).with_columns(
        pl.col(DATE_COL).cast(pl.Date)
    )

    return {
        'auto_detected': auto_detected,
        'provided_rolls': roll_dates or {},
        'outlier_returns': outlier_returns,
        'roll_mask': roll_mask,
    }


# ------------------------------------------------------------------
# Step 4 — Listwise vs Pairwise Deletion Assessment
# ------------------------------------------------------------------

def assess_deletion(returns: pl.DataFrame, stale_result: dict,
                     roll_result: dict,
                     pairwise_n_tolerance: float = PAIRWISE_N_TOLERANCE) -> dict:
    """
    Step 4: report how many rows survive listwise vs. pairwise deletion,
    and whether the pairwise per-pair N is consistent enough to trust.
    """
    tickers = ticker_cols(returns)
    total_rows = len(returns)

    # Build combined bad-row mask.
    stale_mask = stale_result['stale_mask']
    roll_mask = roll_result['roll_mask']
    combined_bad = pl.Series('bad', [False] * total_rows)
    for t in tickers:
        if t in stale_mask.columns:
            combined_bad = combined_bad | stale_mask[t]
        if t in roll_mask.columns:
            combined_bad = combined_bad | roll_mask[t]

    # Listwise: all instruments valid and not stale/roll.
    has_null = returns.select(
        pl.any_horizontal([pl.col(t).is_null() for t in tickers]).alias('any_null')
    )['any_null']
    listwise_mask = (~combined_bad) & (~has_null)
    listwise_rows = int(listwise_mask.sum())

    # Pairwise: per-pair count of mutually valid rows.
    pairwise_n = {}
    for i, ta in enumerate(tickers):
        for tb in tickers[i + 1:]:
            both_valid = (
                returns[ta].is_not_null() & returns[tb].is_not_null() &
                ~combined_bad &
                (stale_mask[ta].not_() if ta in stale_mask.columns else pl.Series([True] * total_rows)) &
                (stale_mask[tb].not_() if tb in stale_mask.columns else pl.Series([True] * total_rows))
            )
            pairwise_n[(ta, tb)] = int(both_valid.sum())

    ns = list(pairwise_n.values())
    cv = float(np.std(ns) / np.mean(ns)) if ns and np.mean(ns) > 0 else 0.0
    reliable = cv <= pairwise_n_tolerance

    return {
        'total_rows': total_rows,
        'listwise_rows': listwise_rows,
        'listwise_pct': round(100 * listwise_rows / total_rows, 1) if total_rows else 0,
        'pairwise_n': pairwise_n,
        'pairwise_n_cv': round(cv, 4),
        'pairwise_reliable': reliable,
        'recommended_strategy': 'listwise' if reliable else 'pairwise (flagged as unreliable)',
        'combined_bad_mask': combined_bad,
    }


# ------------------------------------------------------------------
# Step 5 — Covariance Matrix Comparison
# ------------------------------------------------------------------

def compare_covariance_matrices(ib_prices: pl.DataFrame,
                                  db_prices: Optional[pl.DataFrame] = None,
                                  halflife: float = 60.0,
                                  cov_diff_threshold: float = COV_DIFF_THRESHOLD,
                                  vol_diff_threshold: float = VOL_DIFF_THRESHOLD,
                                  frequency: int = FREQUENCY) -> dict:
    """
    Step 5: compute three covariance matrices (IB pairwise, IB listwise,
    DB listwise if provided), compare element-wise and annualised vols.
    Converts to pandas exactly here at the PyPortfolioOpt call site.
    """
    import pandas as pd
    from pypfopt import risk_models

    tickers = ticker_cols(ib_prices)

    # Convert IB prices to pandas (date-indexed).
    ib_pd = ib_prices.to_pandas().set_index(DATE_COL)
    ib_pd.index = pd.to_datetime(ib_pd.index)

    # A — IB pairwise (PyPortfolioOpt default, uses all rows with ≥1 valid pair).
    S_ib_pairwise = risk_models.sample_cov(ib_pd, frequency=frequency)

    # B — IB listwise (dropna first so every cell uses the same N).
    ib_clean = ib_pd.dropna()
    ret_ib = np.log(ib_clean).diff().dropna()
    S_ib_listwise = risk_models.sample_cov(ret_ib, returns_data=True, frequency=frequency)

    S_db_listwise = None
    if db_prices is not None:
        db_pd = db_prices.to_pandas().set_index(DATE_COL)
        db_pd.index = pd.to_datetime(db_pd.index)
        db_pd = db_pd[tickers] if all(t in db_pd.columns for t in tickers) else db_pd
        db_clean = db_pd.dropna()
        ret_db = np.log(db_clean).diff().dropna()
        S_db_listwise = risk_models.sample_cov(ret_db, returns_data=True, frequency=frequency)

    # Element-wise comparison: pairwise vs. listwise.
    diff_pw_lw = (S_ib_pairwise - S_ib_listwise).abs()
    flagged_pairs_pw_lw = [
        (r, c, float(diff_pw_lw.loc[r, c]))
        for r in tickers for c in tickers
        if r < c and diff_pw_lw.loc[r, c] > cov_diff_threshold
    ]

    diff_ib_db = None
    flagged_pairs_ib_db = []
    if S_db_listwise is not None:
        db_aligned = S_db_listwise.reindex(index=tickers, columns=tickers).fillna(0)
        diff_ib_db = (S_ib_listwise - db_aligned).abs()
        flagged_pairs_ib_db = [
            (r, c, float(diff_ib_db.loc[r, c]))
            for r in tickers for c in tickers
            if r < c and diff_ib_db.loc[r, c] > cov_diff_threshold
        ]

    # Annualised vol comparison (diagonal).
    import pandas as pd
    vols_ib_pw = pd.Series(np.sqrt(np.diag(S_ib_pairwise.values)), index=tickers, name='ib_pairwise')
    vols_ib_lw = pd.Series(np.sqrt(np.diag(S_ib_listwise.values)), index=tickers, name='ib_listwise')
    vol_df = pd.concat([vols_ib_pw, vols_ib_lw], axis=1)
    vol_df['pw_vs_lw_diff'] = (vols_ib_pw - vols_ib_lw).abs()

    if S_db_listwise is not None:
        db_reindexed = S_db_listwise.reindex(index=tickers, columns=tickers).fillna(0)
        vols_db = pd.Series(np.sqrt(np.diag(db_reindexed.values)), index=tickers, name='db_listwise')
        vol_df['db_listwise'] = vols_db
        vol_df['ib_vs_db_diff'] = (vols_ib_lw - vols_db).abs()

    flagged_vol_instruments = vol_df[vol_df['pw_vs_lw_diff'] > vol_diff_threshold].index.tolist()

    return {
        'S_ib_pairwise': S_ib_pairwise,
        'S_ib_listwise': S_ib_listwise,
        'S_db_listwise': S_db_listwise,
        'diff_pairwise_vs_listwise': diff_pw_lw,
        'diff_ib_vs_db': diff_ib_db,
        'flagged_pairs_pw_vs_lw': flagged_pairs_pw_lw,
        'flagged_pairs_ib_vs_db': flagged_pairs_ib_db,
        'vol_comparison': vol_df,
        'flagged_vol_instruments': flagged_vol_instruments,
    }


# ------------------------------------------------------------------
# Step 6 — Recommendation
# ------------------------------------------------------------------

def build_recommendation(stale: dict, volume: dict, rolls: dict,
                           deletion: dict, cov: Optional[dict],
                           prefer_source: str = 'IB') -> str:
    """Step 6: assemble a structured text recommendation."""
    lines = [
        'DATA SOURCE RECOMMENDATION',
        '-' * 26,
    ]

    # Source choice.
    db_available = cov is not None and cov.get('S_db_listwise') is not None
    if db_available and len(cov['flagged_pairs_ib_vs_db']) == 0:
        source = 'IB'
        reason = ('IB API and DB rolling view produce consistent covariance matrices '
                  '(no pairs exceed the difference threshold) -- prefer IB for fresher data.')
    elif db_available:
        source = 'hybrid'
        reason = ('IB and DB matrices diverge for some pairs -- '
                  'consider DB history for pre-IB-window periods, '
                  'IB for the recent window, with a careful splice date.')
    else:
        source = 'IB'
        reason = 'No DB source provided -- using IB data only.'

    lines += [f'Preferred source: {source}', f'Reason: {reason}', '']

    # Per-instrument issues.
    lines += ['INSTRUMENTS TO REVIEW', '-' * 21]
    has_issues = False
    for ticker, n_rows in stale['flagged_tickers'].items():
        runs = stale['runs'].get(ticker, [])
        for start, end, length in runs:
            lines.append(f'{ticker}: {length} consecutive zero returns {start} -> {end} '
                         f'— exclude period or substitute DB history for this window')
        has_issues = True

    for ticker, roll_list in rolls['auto_detected'].items():
        if roll_list:
            lines.append(f'{ticker}: {len(roll_list)} probable roll/anomaly dates auto-detected '
                         f'— review and add to roll_dates if confirmed')
            has_issues = True

    if cov and cov['flagged_vol_instruments']:
        for t in cov['flagged_vol_instruments']:
            lines.append(f'{t}: vol differs >5pp between pairwise and listwise — '
                         f'stale or missing rows are suppressing the pairwise estimate')
        has_issues = True

    if not has_issues:
        lines.append('None identified.')
    lines.append('')

    # Matrix recommendation.
    lines += ['COVARIANCE MATRIX TO USE', '-' * 24]
    method = 'listwise' if deletion['pairwise_reliable'] else 'pairwise (flagged — use listwise if possible)'
    lines.append(f'Method: {method}')
    lines.append(f'Annualisation: {FREQUENCY}')
    lines.append(f'Effective N (listwise rows): {deletion["listwise_rows"]}')
    if not deletion['pairwise_reliable']:
        lines.append(f'WARNING: pairwise N CV = {deletion["pairwise_n_cv"]:.2%} '
                     f'(>{PAIRWISE_N_TOLERANCE:.0%} threshold) — '
                     f'covariance matrix may be internally inconsistent')
    lines.append('')

    # Flagged issues.
    lines += ['FLAGGED ISSUES', '-' * 14]
    issues = []
    if stale['total_flagged']:
        issues.append(f'Stale price runs: {stale["total_flagged"]} flagged rows across '
                      f'{len(stale["flagged_tickers"])} instrument(s)')
    if volume['limitation']:
        issues.append(volume['limitation'])
    elif volume.get('genuine_zero_count'):
        issues.append(f'{volume["genuine_zero_count"]} zero-return rows occur at normal volume '
                      f'(likely holidays/circuit-breakers — safe to keep)')
    if cov and cov['flagged_pairs_pw_vs_lw']:
        issues.append(f'{len(cov["flagged_pairs_pw_vs_lw"])} covariance matrix cell(s) differ '
                      f'>1% between pairwise and listwise IB estimates')
    if not issues:
        issues = ['None — data looks clean.']
    lines += issues

    return '\n'.join(lines)


# ------------------------------------------------------------------
# Orchestration — run all steps in sequence
# ------------------------------------------------------------------

def run_quality_check(ib_prices, db_prices=None, volume=None,
                       roll_dates: Optional[dict] = None,
                       stale_run_threshold: int = STALE_RUN_THRESHOLD,
                       outlier_sigma: float = OUTLIER_SIGMA,
                       pairwise_n_tolerance: float = PAIRWISE_N_TOLERANCE,
                       vol_diff_threshold: float = VOL_DIFF_THRESHOLD,
                       cov_diff_threshold: float = COV_DIFF_THRESHOLD,
                       halflife: float = 60.0,
                       volume_threshold_factor: float = 0.1,
                       print_report: bool = True) -> dict:
    """
    Run all six quality-check steps on the provided price data.
    Accepts polars or pandas DataFrames; returns a comprehensive dict.
    """
    ib = _to_polars_wide(ib_prices)
    db = _to_polars_wide(db_prices) if db_prices is not None else None
    vol = _to_polars_wide(volume) if volume is not None else None

    # Step 1.
    returns = compute_log_returns(ib)
    stale = detect_stale_prices(returns, run_threshold=stale_run_threshold)

    # Step 2.
    volume_report = detect_low_volume(ib, vol, stale_result=stale,
                                       volume_threshold_factor=volume_threshold_factor)

    # Step 3.
    rolls = detect_roll_contamination(returns, roll_dates=roll_dates,
                                       outlier_sigma=outlier_sigma)

    # Step 4.
    deletion = assess_deletion(returns, stale, rolls,
                                pairwise_n_tolerance=pairwise_n_tolerance)

    # Step 5.
    cov = None
    try:
        cov = compare_covariance_matrices(
            ib, db,
            halflife=halflife,
            cov_diff_threshold=cov_diff_threshold,
            vol_diff_threshold=vol_diff_threshold,
        )
    except ImportError:
        log.warning('pypfopt not available — Step 5 covariance comparison skipped')

    # Step 6.
    recommendation = build_recommendation(stale, volume_report, rolls, deletion, cov)

    if print_report:
        _print_summary(stale, volume_report, rolls, deletion, cov, recommendation)

    # Produce S_final and ret_final.
    S_final = ret_final = None
    if cov is not None:
        S_final = cov['S_ib_listwise']
        import pandas as pd
        ib_pd = ib.to_pandas().set_index(DATE_COL)
        ib_pd.index = pd.to_datetime(ib_pd.index)
        ret_final = np.log(ib_pd.dropna()).diff().dropna()

    return {
        'stale': stale,
        'volume': volume_report,
        'rolls': rolls,
        'deletion': deletion,
        'covariance': cov,
        'recommendation': recommendation,
        'S_final': S_final,
        'ret_final': ret_final,
    }


def _print_summary(stale, volume, rolls, deletion, cov, recommendation):
    """Print a concise human-readable summary of all six steps."""
    print('=' * 60)
    print('STEP 1 — STALE PRICE DETECTION')
    if stale['flagged_tickers']:
        for t, n in stale['flagged_tickers'].items():
            for start, end, length in stale['runs'][t]:
                print(f'  {t}: {length} consecutive zeros {start} -> {end}')
    else:
        print('  No stale runs detected.')

    print('\nSTEP 2 — VOLUME / LIQUIDITY GATE')
    if volume['limitation']:
        print(f'  {volume["limitation"]}')
    else:
        print(f'  Zero-return + low-volume overlap: {volume["overlap_count"]}')
        print(f'  Zero-return at normal volume (holidays/CBs): {volume["genuine_zero_count"]}')

    print('\nSTEP 3 — ROLL DATE CONTAMINATION')
    total_auto = sum(len(v) for v in rolls['auto_detected'].values())
    print(f'  Auto-detected probable roll/anomaly dates: {total_auto}')
    provided = sum(len(v) for v in rolls['provided_rolls'].values())
    if provided:
        print(f'  Provided roll dates: {provided}')

    print('\nSTEP 4 — LISTWISE VS PAIRWISE DELETION')
    print(f'  Total rows: {deletion["total_rows"]}')
    print(f'  Listwise rows: {deletion["listwise_rows"]} ({deletion["listwise_pct"]}%)')
    print(f'  Pairwise N CV: {deletion["pairwise_n_cv"]:.2%} '
          f'({"OK" if deletion["pairwise_reliable"] else "UNRELIABLE — N varies too much across pairs"})')

    if cov is not None:
        print('\nSTEP 5 — COVARIANCE MATRIX COMPARISON')
        print(f'  Pairwise vs listwise — pairs with >1% diff: {len(cov["flagged_pairs_pw_vs_lw"])}')
        if cov['diff_ib_vs_db'] is not None:
            print(f'  IB vs DB — pairs with >1% diff: {len(cov["flagged_pairs_ib_vs_db"])}')
        if cov['flagged_vol_instruments']:
            print(f'  Instruments with >5pp vol diff: {cov["flagged_vol_instruments"]}')
        print('\n  Annualised vol comparison:')
        print(cov['vol_comparison'].round(4).to_string())
    else:
        print('\nSTEP 5 — skipped (pypfopt unavailable)')

    print('\n' + '=' * 60)
    print(recommendation)
    print('=' * 60)


# ------------------------------------------------------------------
# IB-dependent IO
# ------------------------------------------------------------------

def fetch_ib_prices(ib, instruments: list[dict], duration: str = '3 y') -> pl.DataFrame:
    """Fetch continuous front-month daily prices for all instruments via IB.
    Returns a wide polars DataFrame (date col + one col per symbol), with
    the same signal_symbol substitution used by the main rebalance system."""
    from ib_tools.ibpysync import IBPySync
    from scripts.tsmom_risk_budget_diagnostic import resolve_signal_symbol

    frames = {}
    for instr in instruments:
        symbol = instr['symbol']
        signal_sym = resolve_signal_symbol(instr)
        if signal_sym != (instr.get('ib_symbol') or symbol):
            log.info('%s: using %s continuous history (signal_symbol fallback)', symbol, signal_sym)
        cont = IBPySync.cont_future(signal_sym, exchange=instr.get('exchange', 'CME'))
        ib.qualify_contracts(cont)
        log.info('Fetching %s (%s) continuous bars...', symbol, signal_sym)
        bars = ib.get_historical_bars(cont, duration=duration, bar_size='1 day')
        if bars is None or bars.height == 0:
            log.warning('%s: no bars returned', symbol)
            continue
        # bars is already a pl.DataFrame (IBPySync.get_historical_bars returns
        # pl.DataFrame(bars)) -- select and rename directly, no pandas roundtrip.
        frames[symbol] = bars.select([
            pl.col(DATE_COL).cast(pl.Date),
            pl.col('close').alias(symbol),
        ])

    if not frames:
        raise RuntimeError('No bars returned for any instrument')

    # Inner join across all instruments entirely in polars.
    wide = None
    for df in frames.values():
        wide = df if wide is None else wide.join(df, on=DATE_COL, how='inner')
    return wide.sort(DATE_COL)


def load_db_prices(symbols: list[str], cache_dir: Optional[str] = None) -> Optional[pl.DataFrame]:
    """Load prices from the local duckdb/parquet cache (FuturesDataLoader).
    Returns wide polars DataFrame, or None if unavailable for any symbol."""
    import os
    from options_bt.domain.futures_dataloader import FuturesDataLoader

    cache_dir = cache_dir or os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', '.cache', 'futures'))
    os.makedirs(cache_dir, exist_ok=True)

    frames = {}
    for sym in symbols:
        try:
            df = FuturesDataLoader(asset=sym, data_dir=cache_dir,
                                    use_preprocessed=True, save_preprocessed=True).ohlcv
            # ohlcv returns a polars DataFrame with ts_event + close columns.
            frames[sym] = df.select(['ts_event', 'close']).rename(
                {'ts_event': DATE_COL, 'close': sym}
            )
        except Exception as exc:
            log.warning('DB: could not load %s (%s)', sym, exc)

    if not frames:
        return None

    wide = None
    for sym, df in frames.items():
        wide = df if wide is None else wide.join(df, on=DATE_COL, how='inner')
    return wide.with_columns(pl.col(DATE_COL).cast(pl.Date)).sort(DATE_COL)


# ------------------------------------------------------------------
# CLI / notebook entry point
# ------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--instruments', default=None,
                   help='Comma-separated symbols (defaults to full KNOWN_INSTRUMENTS universe)')
    p.add_argument('--duration', default='3 y',
                   help='IB historical data duration string (default: %(default)s)')
    p.add_argument('--include-db', action='store_true',
                   help='Also load DB rolling view prices and compare (requires local duckdb cache)')
    p.add_argument('--stale-run-threshold', type=int, default=STALE_RUN_THRESHOLD)
    p.add_argument('--outlier-sigma', type=float, default=OUTLIER_SIGMA)
    p.add_argument('--halflife', type=float, default=60.0)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=7496)
    p.add_argument('--client-id', type=int, default=22)
    return p.parse_args(argv)


def main(argv=None) -> dict:
    """Fetch data, run all quality checks, print report, return full result dict.
    argv: explicit CLI-style arg list for notebook use (e.g. ['--duration', '2 y']);
    omit to read from sys.argv as normal."""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(name)s [%(levelname)s] %(message)s')

    from ib_tools.ibpysync import IBPySync
    from options_bt.live.run_tsmom_rebalance import KNOWN_INSTRUMENTS, _build_instruments

    args = parse_args(argv)
    instruments_spec = args.instruments or ','.join(sorted(KNOWN_INSTRUMENTS))
    instruments = _build_instruments(instruments_spec, None, 15)

    ib = IBPySync()
    ib.connect(args.host, args.port, args.client_id)
    try:
        ib_prices = fetch_ib_prices(ib, instruments, duration=args.duration)
    finally:
        ib.disconnect()

    db_prices = None
    if args.include_db:
        tickers = [instr['symbol'] for instr in instruments]
        db_prices = load_db_prices(tickers)
        if db_prices is None:
            log.warning('DB prices unavailable -- proceeding with IB only')

    return run_quality_check(
        ib_prices, db_prices,
        stale_run_threshold=args.stale_run_threshold,
        outlier_sigma=args.outlier_sigma,
        halflife=args.halflife,
    )


if __name__ == '__main__':
    main()

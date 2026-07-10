"""Compare three ways of slicing 2010-2023 SPX history into backtest windows
for a single, fixed iron-condor parameter combo -- windowing scheme is the
only thing that varies. See research/research_window_slicing_iron_condor.md
for the write-up this script's output feeds.

Scheme A -- fixed rolling window, 90-day slide (already implemented in
    grid_search_backtester.py). Reused here via that module's own window
    generator and per-window job runner (not the GridSearchBacktester class
    itself -- its public .run() only returns the gspread-formatted row,
    which has no Sharpe column; going one level down to _run_one_job gets us
    the raw `res` dict too, so Sharpe can be computed the same way
    Backtester._finalize_results does internally but never persists).
Scheme B -- expanding window anchored at ANCHOR_START_DATE, end date grows
    by STEP_YEARS each step out to the data's actual last available date.
Scheme C -- expanding window like B until it hits SCHEME_C_MAX_WIDTH_YEARS,
    then a rolling window of that fixed width slides forward by STEP_YEARS.

Run: .venv/bin/python scripts/window_scheme_iron_condor.py
"""
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.strats.grid_search_backtester import _generate_windows, _init_worker, _run_one_job
from options_bt.strats.iron_condor_param_search import make_iron_condor_config
from options_bt.utils.gspread_log_util import _format_single_backtest_result_row
from options_bt.utils.logger import setup_logger

logger = setup_logger()
load_dotenv()

# ── Tunable defaults ────────────────────────────────────────────────
ANCHOR_START_DATE = "2010-01-01"     # Scheme A/B/C anchor start date
SCHEME_A_PERIOD_YEARS = 1            # fixed rolling-window width (Scheme A), matches
                                      # grid_search_backtester.py's own periods=[1] usage
SCHEME_C_MAX_WIDTH_YEARS = 5         # width Scheme C expands to before it starts rolling
STEP_YEARS = 1                       # calendar-year step for both B's expansion and C's roll
AUTOCORR_MAX_LAG = 5                 # Scheme-A lag-1..lag-N autocorrelation of Sharpe/return_pct

COMBO: Dict[str, Any] = {
    'short_delta_target': 0.25,
    'dte_target': 45,
    'max_spread_width': 50,
    'early_close_on_dte': 25,
}

# ── Infrastructure ──────────────────────────────────────────────────
# Same .env-based resolution as iron_condor_param_search.py, but with real
# fallback defaults (this machine's .env only sets FIN_DATA_ROOT/DATA_PATH,
# not the three SPX_*/VIX_* vars that OptionsDataLoader actually reads) --
# confirmed these paths resolve correctly by a one-off data load.
_DEFAULT_SPX_OPTIONS_CHAIN_PATH = os.path.expanduser("~/data/fin/market/options/SPX/eod")
_DEFAULT_SPX_UNDERLYING_PATH = os.path.expanduser("~/data/fin/market/index/SPX/eod")
_DEFAULT_VIX_PATH = os.path.expanduser("~/data/fin/market/index/VIX/eod")

_OUTPUT_CSV_PATH = "research/window_scheme_iron_condor_windows.csv"


# ── Data loading ─────────────────────────────────────────────────────
def load_data() -> dict:
    options_file = os.getenv('SPX_OPTIONS_CHAIN_PATH', _DEFAULT_SPX_OPTIONS_CHAIN_PATH)
    spx_file = os.getenv('SPX_UNDERLYING_PATH', _DEFAULT_SPX_UNDERLYING_PATH)
    vix_file = os.getenv('VIX_PATH', _DEFAULT_VIX_PATH)
    dl = OptionsDataLoader(options_file=options_file, spx_file=spx_file, vix_file=vix_file,
                            use_preprocessed=True, save_preprocessed=True)
    return dl.load_data()


def actual_data_end_date(data: dict) -> str:
    """The option chain (not the longer-running underlying/VIX series) is
    the binding constraint on how late a window can end, since trades can
    only be opened/priced where chain data exists. Found empirically rather
    than assumed (the existing grid-search scripts hardcode 2023-12-31)."""
    return data['option_chain'].index.max().strftime("%Y-%m-%d")


# ── Sharpe recompute ─────────────────────────────────────────────────
def trade_to_trade_sharpe(trade_results: pd.DataFrame) -> float:
    """Reproduces Backtester._finalize_results' own Sharpe formula
    (options_bt/domain/backtester.py ~line 356-365) exactly -- that value is
    computed there but only logged, never written into the `results` dict
    returned by Backtester.run(), so every caller that wants it (grid
    search's formatted rows included) has to recompute it from
    trade_results the same way."""
    if trade_results is None or len(trade_results) <= 1:
        return None
    avg_trade_days = trade_results['days_held'].mean()
    if not avg_trade_days or avg_trade_days <= 0:
        return None
    annualization_factor = np.sqrt(252 / avg_trade_days)
    returns = np.diff(trade_results['capital'].values) / trade_results['capital'].values[:-1]
    if len(returns) == 0 or np.std(returns) == 0:
        return None
    return float(np.mean(returns) / np.std(returns) * annualization_factor)


def window_years(start_date: str, end_date: str) -> float:
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    return round((d1 - d0).days / 365.25, 3)


# ── Window generation: Scheme B (expanding) / Scheme C (expand-then-cap) ─
def generate_expanding_windows(anchor_start: str, data_end: str, step_years: int) -> List[Tuple[str, str]]:
    """Scheme B: start fixed at anchor_start; end grows by step_years each
    step (calendar-year add, not +365d, so it lands on the same month/day
    every year) until the next step would run past data_end -- then one
    final window is appended capped at data_end itself, exactly hitting the
    'up to the actual last available date' requirement even though that
    date isn't itself a clean N-year boundary from anchor_start."""
    start_dt = date.fromisoformat(anchor_start)
    end_bound = date.fromisoformat(data_end)
    windows = []
    k = 1
    while True:
        end_dt = start_dt + relativedelta(years=step_years * k)
        if end_dt >= end_bound:
            break
        windows.append((anchor_start, end_dt.strftime("%Y-%m-%d")))
        k += 1
    # Final window: capped at the true data end (may be a few days short of
    # the next clean calendar-year boundary).
    windows.append((anchor_start, end_bound.strftime("%Y-%m-%d")))
    return windows


def generate_capped_rolling_windows(anchor_start: str, data_end: str, max_width_years: int, step_years: int) -> List[Tuple[str, str]]:
    """Scheme C: identical to Scheme B (generate_expanding_windows) while the
    window width is still below max_width_years; once width hits the cap,
    switch to sliding both start and end forward by step_years each step,
    holding width fixed at max_width_years, until the window's end reaches
    data_end (last step capped there, same as Scheme B)."""
    start_dt = date.fromisoformat(anchor_start)
    end_bound = date.fromisoformat(data_end)
    windows = []

    # Expanding phase: widths 1..max_width_years, all anchored at anchor_start.
    for w in range(1, max_width_years + 1):
        end_dt = start_dt + relativedelta(years=w)
        if end_dt >= end_bound:
            windows.append((anchor_start, end_bound.strftime("%Y-%m-%d")))
            return windows
        windows.append((anchor_start, end_dt.strftime("%Y-%m-%d")))

    # Rolling phase: width fixed at max_width_years, both edges slide forward.
    k = 1
    while True:
        roll_start = start_dt + relativedelta(years=step_years * k)
        roll_end = roll_start + relativedelta(years=max_width_years)
        if roll_end >= end_bound:
            windows.append((roll_start.strftime("%Y-%m-%d"), end_bound.strftime("%Y-%m-%d")))
            break
        windows.append((roll_start.strftime("%Y-%m-%d"), roll_end.strftime("%Y-%m-%d")))
        k += 1
    return windows


# ── Single-window backtest + row formatting (shared by all 3 schemes) ───
def run_one_window(data: dict, start_date: str, end_date: str, period_label: float,
                    save_trades: bool = False, log_to_sheets: bool = False) -> dict:
    bt_local = Backtester(data=data, save_trades=save_trades, log_to_sheets=log_to_sheets)
    config = make_iron_condor_config(COMBO, start_date, end_date)
    res = bt_local.run(config)
    param_str = bt_local._generate_param_string(config)
    row = _format_single_backtest_result_row(res, config, param_str, period_label)
    row['sharpe'] = trade_to_trade_sharpe(res['trade_results'])
    row['window_years'] = window_years(start_date, end_date)
    return row


def run_scheme_a(data: dict, start_date: str, end_date: str) -> List[dict]:
    """Reuses grid_search_backtester.py's own window generator and per-job
    runner unmodified -- same windows, same config-building, same row
    formatting the real grid search uses -- just adding the Sharpe
    recompute (see trade_to_trade_sharpe) that _format_single_backtest_result_row
    doesn't provide, and running across a process pool since this is ~50-60
    backtests."""
    windows = _generate_windows([SCHEME_A_PERIOD_YEARS], start_date, end_date)
    logger.info(f"Scheme A: {len(windows)} windows (1yr rolling, 90-day slide)")

    ctx = multiprocessing.get_context('spawn')
    shared_data = {'option_chain': data['option_chain'], 'underlying': data['underlying'], 'vix': data['vix']}
    rows = []
    max_workers = os.cpu_count()
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx,
                              initializer=_init_worker, initargs=(shared_data,)) as pool:
        futures = {
            pool.submit(_run_one_job, period, w_start, w_end, COMBO, make_iron_condor_config, False, False): (w_start, w_end)
            for period, w_start, w_end in windows
        }
        for i, future in enumerate(as_completed(futures), 1):
            w_start, w_end = futures[future]
            result = future.result()
            row = result['formatted_row']
            row['sharpe'] = trade_to_trade_sharpe(result['res']['trade_results'])
            row['window_years'] = window_years(w_start, w_end)
            rows.append(row)
            if i % 10 == 0 or i == len(windows):
                logger.info(f"Scheme A: completed {i}/{len(windows)}")
    # Sort by start date so downstream lag-N autocorrelation is meaningful.
    rows.sort(key=lambda r: r['start'])
    return rows


def run_scheme_sequential(data: dict, windows: List[Tuple[str, str]], scheme_name: str) -> List[dict]:
    rows = []
    for i, (w_start, w_end) in enumerate(windows, 1):
        logger.info(f"{scheme_name}: window {i}/{len(windows)}: {w_start} to {w_end}")
        row = run_one_window(data, w_start, w_end, period_label=window_years(w_start, w_end))
        rows.append(row)
    return rows


# ── Aggregate stats + autocorrelation ────────────────────────────────
def aggregate_stats(rows: List[dict], field: str) -> Dict[str, float]:
    vals = pd.Series([r[field] for r in rows if r.get(field) is not None], dtype=float)
    if vals.empty:
        return {'mean': None, 'std': None, 'min': None, 'max': None, 'n': 0}
    return {'mean': vals.mean(), 'std': vals.std(), 'min': vals.min(), 'max': vals.max(), 'n': len(vals)}


def lag_autocorrelation(series: pd.Series, max_lag: int) -> Dict[int, float]:
    out = {}
    for lag in range(1, max_lag + 1):
        if len(series) > lag:
            out[lag] = series.autocorr(lag=lag)
        else:
            out[lag] = None
    return out


def main():
    t0 = time.time()
    data = load_data()
    data_end = actual_data_end_date(data)
    logger.info(f"Actual data end date (option chain): {data_end}")

    # ── Scheme A: fixed 1yr rolling window, 90-day slide ──────────────
    scheme_a_rows = run_scheme_a(data, ANCHOR_START_DATE, data_end)
    for r in scheme_a_rows:
        r['scheme'] = 'A_rolling_90d_slide'

    # ── Scheme B: expanding window anchored at ANCHOR_START_DATE ──────
    scheme_b_windows = generate_expanding_windows(ANCHOR_START_DATE, data_end, STEP_YEARS)
    scheme_b_rows = run_scheme_sequential(data, scheme_b_windows, "Scheme B (expanding)")
    for r in scheme_b_rows:
        r['scheme'] = 'B_expanding'
    # Cumulative running average of Sharpe (and return_pct) across window
    # index 1..N, in addition to each window's own full-history-to-date value.
    b_sharpe_cum, b_ret_cum = [], []
    for i, r in enumerate(scheme_b_rows, 1):
        b_sharpe_cum.append(r['sharpe'])
        b_ret_cum.append(r['ret_pct'])
        r['cum_avg_sharpe'] = float(pd.Series(b_sharpe_cum, dtype=float).mean())
        r['cum_avg_ret_pct'] = float(pd.Series(b_ret_cum, dtype=float).mean())

    # ── Scheme C: expanding to a 5yr cap, then rolling ─────────────────
    scheme_c_windows = generate_capped_rolling_windows(ANCHOR_START_DATE, data_end, SCHEME_C_MAX_WIDTH_YEARS, STEP_YEARS)
    scheme_c_rows = run_scheme_sequential(data, scheme_c_windows, "Scheme C (expand-then-cap)")
    for r in scheme_c_rows:
        r['scheme'] = 'C_capped_rolling'

    all_rows = scheme_a_rows + scheme_b_rows + scheme_c_rows
    df = pd.DataFrame(all_rows)

    # Reorder so the schema-comparison columns are up front; keep everything
    # _format_single_backtest_result_row already produced after that.
    front_cols = ['scheme', 'start', 'end', 'window_years', 'sharpe', 'ret_pct', 'total_pnl',
                  'win_rate', 'max_dd_pct', 'max_dd_usd', 'total_trades',
                  'cum_avg_sharpe', 'cum_avg_ret_pct']
    other_cols = [c for c in df.columns if c not in front_cols]
    df = df[[c for c in front_cols if c in df.columns] + other_cols]

    os.makedirs(os.path.dirname(_OUTPUT_CSV_PATH), exist_ok=True)
    df.to_csv(_OUTPUT_CSV_PATH, index=False)
    logger.info(f"Wrote {len(df)} rows to {_OUTPUT_CSV_PATH}")

    # ── Scheme A autocorrelation: is the 90-day slide adding independent
    # information, or is it mostly autocorrelated noise between neighboring
    # (heavily-overlapping) windows? ──
    a_df = pd.DataFrame(scheme_a_rows).sort_values('start').reset_index(drop=True)
    sharpe_series = pd.Series(a_df['sharpe'], dtype=float)
    ret_series = pd.Series(a_df['ret_pct'], dtype=float)
    sharpe_autocorr = lag_autocorrelation(sharpe_series, AUTOCORR_MAX_LAG)
    ret_autocorr = lag_autocorrelation(ret_series, AUTOCORR_MAX_LAG)

    # ── Aggregate stats per scheme ─────────────────────────────────────
    summary = {}
    for name, rows in [('A_rolling_90d_slide', scheme_a_rows), ('B_expanding', scheme_b_rows), ('C_capped_rolling', scheme_c_rows)]:
        summary[name] = {
            'n_windows': len(rows),
            'sharpe': aggregate_stats(rows, 'sharpe'),
            'ret_pct': aggregate_stats(rows, 'ret_pct'),
        }

    print("\n=== Per-scheme window counts ===")
    for name, s in summary.items():
        print(f"{name}: {s['n_windows']} windows | Sharpe mean={s['sharpe']['mean']:.3f} std={s['sharpe']['std']:.3f} "
              f"| ret_pct mean={s['ret_pct']['mean']:.2f} std={s['ret_pct']['std']:.2f}")

    print("\n=== Scheme A lag-N autocorrelation (windows sorted by start date, 90-day slide) ===")
    print("Sharpe autocorr:", {k: round(v, 3) if v is not None else None for k, v in sharpe_autocorr.items()})
    print("ret_pct autocorr:", {k: round(v, 3) if v is not None else None for k, v in ret_autocorr.items()})

    logger.info(f"Total runtime: {time.time() - t0:.1f}s")

    return {
        'df': df,
        'summary': summary,
        'sharpe_autocorr': sharpe_autocorr,
        'ret_autocorr': ret_autocorr,
        'data_end': data_end,
        'scheme_a_windows': len(scheme_a_rows),
        'scheme_b_windows': len(scheme_b_rows),
        'scheme_c_windows': len(scheme_c_rows),
    }


if __name__ == "__main__":
    main()

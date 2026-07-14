"""Compare three window-slicing schemes for a naked (long or short) futures
position, sweeping a configurable symbol/direction combo across 2010-present.

Scheme A -- fixed rolling window, 90-day slide (same generator as
    grid_search_backtester._generate_windows).
Scheme B -- expanding window anchored at ANCHOR_START_DATE; end grows by
    STEP_YEARS each step out to the actual last available date.
Scheme C -- expanding like B until width hits SCHEME_C_MAX_WIDTH_YEARS,
    then a rolling window of that fixed width slides forward by STEP_YEARS.

Run:
    .venv/bin/python scripts/window_scheme_naked_futures.py
    .venv/bin/python scripts/window_scheme_naked_futures.py --symbol ES --dir long
    .venv/bin/python scripts/window_scheme_naked_futures.py --symbol CL --dir short
"""
import argparse
import datetime as dt
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from derivatives_bt_engine.domain.backtester import Backtester
from derivatives_bt_engine.domain.enums import FuturesStrategy
from derivatives_bt_engine.domain.futures_dataloader import FuturesDataLoader
from derivatives_bt_engine.domain.strategy_config import FuturesStrategyConfig
from derivatives_bt_engine.strats.grid_search_backtester import _generate_windows
from derivatives_bt_engine.utils.gspread_log_util import _format_single_backtest_result_row
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()
load_dotenv()

# ── Tunable defaults ──────────────────────────────────────────────────
DEFAULT_SYMBOL    = 'CL'
DEFAULT_DIRECTION = 'long'

ANCHOR_START_DATE        = "2010-01-01"
SCHEME_A_PERIOD_YEARS    = 1
SCHEME_C_MAX_WIDTH_YEARS = 5
STEP_YEARS               = 1
AUTOCORR_MAX_LAG         = 5

INITIAL_CAPITAL = 100_000.0
QUANTITY        = 1
LEVERAGE        = 1.0
FILL_PRICE      = 'mid'

# ── Infrastructure ─────────────────────────────────────────────────────
_OUTPUT_CSV_TMPL = "research/window_scheme_futures_{symbol}_{dir}.csv"
_OUTPUT_MD_TMPL  = "research/research_window_slicing_futures_{symbol}_{dir}.md"


# ── Helpers ────────────────────────────────────────────────────────────
def _window_years(start_date: str, end_date: str) -> float:
    d0 = dt.date.fromisoformat(start_date)
    d1 = dt.date.fromisoformat(end_date)
    return round((d1 - d0).days / 365.25, 3)


def generate_expanding_windows(anchor_start: str, data_end: str, step_years: int) -> List[Tuple[str, str]]:
    start_dt   = dt.date.fromisoformat(anchor_start)
    end_bound  = dt.date.fromisoformat(data_end)
    windows, k = [], 1
    while True:
        end_dt = start_dt + relativedelta(years=step_years * k)
        if end_dt >= end_bound:
            break
        windows.append((anchor_start, end_dt.strftime("%Y-%m-%d")))
        k += 1
    windows.append((anchor_start, end_bound.strftime("%Y-%m-%d")))
    return windows


def generate_capped_rolling_windows(anchor_start: str, data_end: str, max_width_years: int, step_years: int) -> List[Tuple[str, str]]:
    start_dt  = dt.date.fromisoformat(anchor_start)
    end_bound = dt.date.fromisoformat(data_end)
    windows   = []
    for w in range(1, max_width_years + 1):
        end_dt = start_dt + relativedelta(years=w)
        if end_dt >= end_bound:
            windows.append((anchor_start, end_bound.strftime("%Y-%m-%d")))
            return windows
        windows.append((anchor_start, end_dt.strftime("%Y-%m-%d")))
    k = 1
    while True:
        roll_start = start_dt + relativedelta(years=step_years * k)
        roll_end   = roll_start + relativedelta(years=max_width_years)
        if roll_end >= end_bound:
            windows.append((roll_start.strftime("%Y-%m-%d"), end_bound.strftime("%Y-%m-%d")))
            break
        windows.append((roll_start.strftime("%Y-%m-%d"), roll_end.strftime("%Y-%m-%d")))
        k += 1
    return windows


# ── Single-window backtest ─────────────────────────────────────────────
def _run_window(full_data: dict, start_date: str, end_date: str,
                futures_type: str, futures_strategy: FuturesStrategy) -> dict:
    """Run one window against the pre-loaded full OHLCV (Backtester filters
    by config start/end internally; no per-window data slicing needed since
    futures OHLCV is tiny compared with the option chain)."""
    config = FuturesStrategyConfig(
        quantity=QUANTITY,
        futures_type=futures_type,
        futures_strategy=futures_strategy,
        initial_capital=INITIAL_CAPITAL,
        leverage=LEVERAGE,
        start_date=start_date,
        end_date=end_date,
        fill_price=FILL_PRICE,
    )
    bt  = Backtester(data=full_data, save_trades=False, log_to_sheets=False)
    res = bt.run(config)
    param_str = bt._generate_param_string(config)
    period    = _window_years(start_date, end_date)
    row       = _format_single_backtest_result_row(res, config, param_str, period)
    row['sharpe']       = res.get('sharpe_trade_to_trade')
    row['mtm_sharpe']   = res.get('mtm_sharpe')
    row['window_years'] = period
    return row


def run_windows(full_data: dict, windows: List[Tuple[str, str]], scheme_name: str,
                futures_type: str, futures_strategy: FuturesStrategy) -> List[dict]:
    rows = []
    for i, (w_start, w_end) in enumerate(windows, 1):
        t0 = time.time()
        logger.info(f"{scheme_name}: window {i}/{len(windows)}: {w_start} to {w_end}")
        try:
            row = _run_window(full_data, w_start, w_end, futures_type, futures_strategy)
        except Exception as e:
            logger.error(f"Window {w_start}..{w_end} failed: {e}")
            row = {
                'start': w_start, 'end': w_end,
                'sharpe': None, 'mtm_sharpe': None, 'ret_yr': None,
                'total_pnl': None, 'win_rate': None,
                'max_dd_pct': None, 'max_dd_usd': None, 'total_trades': None,
                'window_years': _window_years(w_start, w_end),
                'error': str(e),
            }
        rows.append(row)
        logger.info(f"{scheme_name}: window {i}/{len(windows)} done in {time.time()-t0:.1f}s "
                    f"(sharpe={row.get('sharpe')}, trades={row.get('total_trades')})")
    return rows


# ── Aggregate stats + autocorrelation ─────────────────────────────────
def aggregate_stats(rows: List[dict], field: str) -> Dict[str, Any]:
    vals = np.array([r[field] for r in rows if r.get(field) is not None], dtype=float)
    if len(vals) == 0:
        return {'mean': None, 'std': None, 'min': None, 'max': None, 'n': 0}
    std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    return {'mean': float(vals.mean()), 'std': std, 'min': float(vals.min()), 'max': float(vals.max()), 'n': int(len(vals))}


def lag_autocorrelation(values: List[float], max_lag: int) -> Dict[int, Optional[float]]:
    arr = np.array(values, dtype=float)
    out = {}
    for lag in range(1, max_lag + 1):
        if len(arr) > lag:
            a, b = arr[:-lag], arr[lag:]
            mask = ~(np.isnan(a) | np.isnan(b))
            a, b = a[mask], b[mask]
            out[lag] = float(np.corrcoef(a, b)[0, 1]) if len(a) > 1 else None
        else:
            out[lag] = None
    return out


# ── Markdown report ───────────────────────────────────────────────────
def _fmt(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def write_markdown_report(path: str, symbol: str, direction: str, data_end: str,
                           summary: Dict[str, Any],
                           sharpe_autocorr: Dict[int, Optional[float]],
                           ret_autocorr: Dict[int, Optional[float]],
                           scheme_b_rows: List[dict], n_errors: int) -> None:
    lines = []
    lines.append(f"# Window-slicing scheme comparison: {symbol} {direction} futures\n")
    lines.append(
        f"Fixed strategy: naked {direction} {symbol} futures, "
        f"{QUANTITY} contract(s), ${INITIAL_CAPITAL:,.0f} capital, "
        f"fill_price='{FILL_PRICE}'. Data: {ANCHOR_START_DATE} through {data_end}."
    )

    lines.append("\n## Results by scheme\n")
    lines.append("| Scheme | Windows | Sharpe mean | Sharpe std | MTM Sharpe mean | MTM Sharpe std | ret_yr mean | ret_yr std |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, s in summary.items():
        lines.append(
            f"| {name} | {s['n_windows']} | {_fmt(s['sharpe']['mean'])} | {_fmt(s['sharpe']['std'])} | "
            f"{_fmt(s['mtm_sharpe']['mean'])} | {_fmt(s['mtm_sharpe']['std'])} | "
            f"{_fmt(s['ret_yr']['mean'], 2)} | {_fmt(s['ret_yr']['std'], 2)} |"
        )
    if n_errors:
        lines.append(f"\n*{n_errors} window(s) failed (see `error` column in CSV).*")

    lines.append("\n## Scheme A: lag-N autocorrelation (90-day slide)\n")
    lines.append("| Lag (x90 days) | Sharpe autocorr | ret_yr autocorr |")
    lines.append("|---|---|---|")
    for lag in range(1, AUTOCORR_MAX_LAG + 1):
        lines.append(f"| {lag} | {_fmt(sharpe_autocorr.get(lag))} | {_fmt(ret_autocorr.get(lag))} |")

    lines.append("\n## Scheme B: cumulative running average\n")
    lines.append("| Window (k) | End date | Window years | Sharpe | Cumulative avg Sharpe |")
    lines.append("|---|---|---|---|---|")
    for i, r in enumerate(scheme_b_rows, 1):
        lines.append(f"| {i} | {r.get('end')} | {_fmt(r.get('window_years'), 1)} | "
                     f"{_fmt(r.get('sharpe'))} | {_fmt(r.get('cum_avg_sharpe'))} |")

    with open(path, 'w') as f:
        f.write("\n".join(lines) + "\n")


# ── Main ───────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbol', default=DEFAULT_SYMBOL,
                   help=f'Futures symbol (default: {DEFAULT_SYMBOL})')
    p.add_argument('--dir', choices=['long', 'short'], default=DEFAULT_DIRECTION,
                   help=f'Position direction (default: {DEFAULT_DIRECTION})')
    return p.parse_args()


def main():
    t0   = time.time()
    args = parse_args()

    symbol = args.symbol.upper()
    futures_type = symbol  # validated by FuturesStrategyConfig.__post_init__ on first use

    futures_strategy = FuturesStrategy.LONG_FUTURES if args.dir == 'long' else FuturesStrategy.SHORT_FUTURES
    direction        = args.dir

    output_csv = _OUTPUT_CSV_TMPL.format(symbol=symbol.lower(), dir=direction)
    output_md  = _OUTPUT_MD_TMPL.format(symbol=symbol.lower(), dir=direction)

    logger.info(f"Loading {symbol} OHLCV...")
    dl        = FuturesDataLoader(asset=symbol, use_preprocessed=False, save_preprocessed=False)
    full_data = dl.load_data()

    # Derive data end from the loaded OHLCV
    data_end = full_data['underlying']['ts_event'].max().strftime("%Y-%m-%d")
    logger.info(f"Data end: {data_end}")

    # ── Scheme A ──────────────────────────────────────────────────────
    scheme_a_windows = [(s, e) for _p, s, e in _generate_windows([SCHEME_A_PERIOD_YEARS], ANCHOR_START_DATE, data_end)]
    logger.info(f"Scheme A: {len(scheme_a_windows)} windows ({SCHEME_A_PERIOD_YEARS}yr rolling, 90-day slide)")
    scheme_a_rows = run_windows(full_data, scheme_a_windows, "Scheme A", futures_type, futures_strategy)
    for r in scheme_a_rows:
        r['scheme'] = 'A_rolling_90d_slide'
    scheme_a_rows.sort(key=lambda r: r['start'])

    # ── Scheme B ──────────────────────────────────────────────────────
    scheme_b_windows = generate_expanding_windows(ANCHOR_START_DATE, data_end, STEP_YEARS)
    logger.info(f"Scheme B: {len(scheme_b_windows)} windows (expanding)")
    scheme_b_rows = run_windows(full_data, scheme_b_windows, "Scheme B", futures_type, futures_strategy)
    for r in scheme_b_rows:
        r['scheme'] = 'B_expanding'
    b_sharpe_cum, b_ret_cum = [], []
    for r in scheme_b_rows:
        b_sharpe_cum.append(r.get('sharpe'))
        b_ret_cum.append(r.get('ret_yr'))
        r['cum_avg_sharpe'] = float(np.nanmean(np.array([np.nan if v is None else v for v in b_sharpe_cum], dtype=float)))
        r['cum_avg_ret_yr'] = float(np.nanmean(np.array([np.nan if v is None else v for v in b_ret_cum], dtype=float)))

    # ── Scheme C ──────────────────────────────────────────────────────
    scheme_c_windows = generate_capped_rolling_windows(ANCHOR_START_DATE, data_end, SCHEME_C_MAX_WIDTH_YEARS, STEP_YEARS)
    logger.info(f"Scheme C: {len(scheme_c_windows)} windows (expand-then-cap)")
    scheme_c_rows = run_windows(full_data, scheme_c_windows, "Scheme C", futures_type, futures_strategy)
    for r in scheme_c_rows:
        r['scheme'] = 'C_capped_rolling'

    all_rows = scheme_a_rows + scheme_b_rows + scheme_c_rows
    n_errors = sum(1 for r in all_rows if r.get('error'))
    df = pl.DataFrame(all_rows)

    front_cols = ['scheme', 'start', 'end', 'window_years', 'sharpe', 'mtm_sharpe',
                  'ret_yr', 'roi', 'total_pnl', 'win_rate',
                  'max_dd_pct', 'max_dd_usd', 'total_trades',
                  'cum_avg_sharpe', 'cum_avg_ret_yr', 'error']
    other_cols = [c for c in df.columns if c not in front_cols]
    df = df.select([c for c in front_cols if c in df.columns] + other_cols)

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df.write_csv(output_csv)
    logger.info(f"Wrote {df.height} rows to {output_csv}")

    # ── Scheme A autocorrelation ───────────────────────────────────────
    a_df = pl.DataFrame(scheme_a_rows).sort('start')
    sharpe_autocorr = lag_autocorrelation(a_df['sharpe'].to_list(), AUTOCORR_MAX_LAG)
    ret_autocorr    = lag_autocorrelation(a_df['ret_yr'].to_list(), AUTOCORR_MAX_LAG)

    # ── Aggregate stats ────────────────────────────────────────────────
    summary = {}
    for name, rows in [('A_rolling_90d_slide', scheme_a_rows),
                        ('B_expanding', scheme_b_rows),
                        ('C_capped_rolling', scheme_c_rows)]:
        summary[name] = {
            'n_windows':  len(rows),
            'sharpe':     aggregate_stats(rows, 'sharpe'),
            'mtm_sharpe': aggregate_stats(rows, 'mtm_sharpe'),
            'ret_yr':     aggregate_stats(rows, 'ret_yr'),
        }

    write_markdown_report(output_md, symbol, direction, data_end, summary,
                          sharpe_autocorr, ret_autocorr, scheme_b_rows, n_errors)
    logger.info(f"Wrote report to {output_md}")

    print(f"\n=== {symbol} {direction} — per-scheme summary ===")
    for name, s in summary.items():
        print(f"{name}: {s['n_windows']} windows | "
              f"Sharpe mean={_fmt(s['sharpe']['mean'])} std={_fmt(s['sharpe']['std'])} | "
              f"MTM mean={_fmt(s['mtm_sharpe']['mean'])} std={_fmt(s['mtm_sharpe']['std'])} | "
              f"ret_yr mean={_fmt(s['ret_yr']['mean'], 2)} std={_fmt(s['ret_yr']['std'], 2)}")

    print("\n=== Scheme A lag-N autocorrelation ===")
    print("Sharpe:", {k: (round(v, 3) if v is not None else None) for k, v in sharpe_autocorr.items()})
    print("ret_yr:", {k: (round(v, 3) if v is not None else None) for k, v in ret_autocorr.items()})

    if n_errors:
        print(f"\n{n_errors} window(s) failed (see 'error' column in CSV).")

    logger.info(f"Total runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

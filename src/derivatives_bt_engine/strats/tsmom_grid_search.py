"""
Grid search over TSMOM hyperparameters (vol_target, regime_discount,
long_only, max_notional, max_contracts), parallelized via multiprocessing.

Unlike the options engine's GridSearchBacktester (grid_search_backtester.py,
sequential -- each combo mutates/reuses a shared Backtester instance),
run_tsmom_backtest is a pure function over a fresh config each call, with no
shared mutable state across runs, so combos can run as truly independent
worker processes via ProcessPoolExecutor.

Run:
    tsmom-grid-search --symbols ES --years 2015-2025
"""
import argparse
import itertools
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional

import polars as pl

from derivatives_bt_engine.domain.tsmom_backtester import TsmomBacktestConfig, load_portfolio_data, run_tsmom_backtest
from derivatives_bt_engine.domain.tsmom_window_reporting import (
    score_causal_windows,
    summarize_causal_windows,
)
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()


def product_dict(param_grid: dict[str, Iterable[Any]]) -> list[dict[str, Any]]:
    """Same shape as bull_put_param_search.product_dict, inlined rather than
    imported -- that module pulls in gspread/Google OAuth at import time for
    its sheet-upload helper, which this script has no reason to depend on."""
    keys = list(param_grid.keys())
    vals = [param_grid[k] if isinstance(param_grid[k], Iterable) and not isinstance(param_grid[k], (str, bytes))
            else [param_grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]


def _time_in_trade_pct(events: list[dict], all_dates: list[date], symbol: str) -> float:
    """% of the backtest window during which `symbol` held a nonzero
    position, computed from the sparse rebalance-event list (positions
    only change at rebalance dates, so the gaps between events are held
    constant)."""
    syms = sorted((e for e in events if e['symbol'] == symbol), key=lambda e: e['date'])
    if not syms or not all_dates:
        return 0.0
    total_days = (all_dates[-1] - all_dates[0]).days or 1
    in_trade_days = 0
    for i, e in enumerate(syms):
        start = e['date']
        end = syms[i + 1]['date'] if i + 1 < len(syms) else all_dates[-1]
        if e['target_contracts'] != 0:
            in_trade_days += (end - start).days
    return 100 * in_trade_days / total_days


def _direction_switches(events: list[dict], symbol: str) -> list[dict]:
    """Events where the position flips sign (long<->short) directly,
    rather than just entering/exiting flat."""
    syms = sorted((e for e in events if e['symbol'] == symbol), key=lambda e: e['date'])
    return [e for e in syms if e['prior_contracts'] != 0 and e['target_contracts'] != 0
            and (e['prior_contracts'] > 0) != (e['target_contracts'] > 0)]


@dataclass
class GridResults:
    """One full-path summary, per-window scores, and legacy symbol diagnostics."""
    summary: pl.DataFrame
    window_metrics: pl.DataFrame
    symbol_details: pl.DataFrame


def _summary_fields(window_summary: pl.DataFrame) -> dict:
    """Flatten window-scheme aggregates into one grid-ranking row."""
    fields = {}
    for row in window_summary.to_dicts():
        prefix = row.pop('scheme')
        fields.update({f'{prefix}_{key}': value for key, value in row.items()})
    return fields


def _run_one(combo_id: int, combo: dict, symbols: list[str], start_date: date,
             end_date: date, oos_start: date, max_width_years: int) -> tuple[dict, list[dict], list[dict]]:
    """Top-level, picklable worker: build a config from `combo`, run the
    backtest, then score all reporting windows from that single causal path.
    Returns a portfolio summary, per-window metrics, and per-symbol diagnostics.
    Assumes the parquet/VIX
    cache is already warm (run_grid does this once in the parent process
    before spawning workers, to avoid concurrent writes to the same cache
    file from multiple processes)."""
    config = TsmomBacktestConfig(
        symbols=symbols, start_date=start_date, end_date=end_date,
        vol_target=combo.get('vol_target', 0.15),
        regime_discount=combo.get('regime_discount', 0.5),
        long_only=combo.get('long_only', False),
        max_notional=combo.get('max_notional', 250_000),
        max_contracts=combo.get('max_contracts', 10),
    )
    result = run_tsmom_backtest(config)
    stats = result['daily_mtm']
    all_dates = stats['date'].to_list()
    window_metrics = score_causal_windows(
        result, initial_capital=config.initial_capital, oos_start=oos_start,
        max_width_years=max_width_years,
    )
    window_summary = summarize_causal_windows(window_metrics)
    summary = {
        'combo_id': combo_id,
        **combo,
        'final_capital': stats['capital'][-1],
        'cum_pnl': stats['cum_pnl'][-1],
        'max_drawdown_pct': stats['drawdown_pct'].min(),
        'n_days': result['n_days'],
        'ann_ret_pct': result['ann_ret_pct'],
        'ann_vol_pct': result['ann_vol_pct'],
        'sharpe': result['sharpe'],
        'total_fees': result['total_fees'],
        **_summary_fields(window_summary),
    }
    symbol_rows = []
    for symbol in symbols:
        switches = _direction_switches(result['trend_signals'], symbol)
        symbol_rows.append({
            'combo_id': combo_id,
            **combo,
            'symbol': symbol,
            'final_capital': stats['capital'][-1],
            'cum_pnl': stats['cum_pnl'][-1],
            'max_drawdown_pct': stats['drawdown_pct'].min(),
            'time_in_trade_pct': round(_time_in_trade_pct(result['trend_signals'], all_dates, symbol), 1),
            'n_direction_switches': len(switches),
        })
    metric_rows = [
        {'combo_id': combo_id, **combo, **row}
        for row in window_metrics.to_dicts()
    ]
    return summary, metric_rows, symbol_rows


def run_grid(symbols: list[str], start_date: date, end_date: date, param_grid: dict,
             *, oos_start: date, max_width_years: int = 5,
             max_workers: Optional[int] = None) -> GridResults:
    combos = product_dict(param_grid)
    logger.info(f"Running {len(combos)} combos across {symbols} ({start_date} to {end_date})")

    # Warm the parquet/VIX cache once, sequentially, in the parent process --
    # otherwise every worker's first cache-miss would race to write the same
    # file (FuturesDataLoader.daily's save_preprocessed path has no locking).
    load_portfolio_data(symbols)

    # mp_context='spawn', not the Linux default 'fork': polars and duckdb
    # both run internal thread pools. Forking after they've started threads
    # copies whatever locks those threads held at that instant into each
    # child -- but the threads themselves don't exist there to ever release
    # them, so every worker deadlocks at 0% CPU before doing any real work.
    # spawn starts each worker as a fresh interpreter instead, sidestepping
    # the inherited-lock problem entirely.
    ctx = multiprocessing.get_context('spawn')
    summaries, window_rows, symbol_rows = [], [], []
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as ex:
        futures = {
            ex.submit(_run_one, combo_id, combo, symbols, start_date, end_date,
                      oos_start, max_width_years): combo
            for combo_id, combo in enumerate(combos)
        }
        for i, fut in enumerate(as_completed(futures), 1):
            summary, metrics, diagnostics = fut.result()
            summaries.append(summary)
            window_rows.extend(metrics)
            symbol_rows.extend(diagnostics)
            if i % 10 == 0 or i == len(combos):
                logger.info(f"Completed {i}/{len(combos)} combos")

    return GridResults(
        summary=pl.DataFrame(summaries).sort('combo_id'),
        window_metrics=pl.DataFrame(window_rows).sort(['combo_id', 'scheme', 'window_start']),
        symbol_details=pl.DataFrame(symbol_rows).sort(['combo_id', 'symbol']),
    )


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default='ES',
                   help='Comma-separated futures symbols (default: %(default)s)')
    p.add_argument('--years', default='2015-2025',
                   help='Year range as START-END or a single YEAR (default: %(default)s)')
    p.add_argument('--oos-start', default=None,
                   help='Shared YYYY-MM-DD OOS boundary for every parameter combination; defaults to '
                        'January 1 of the year after --years starts')
    p.add_argument('--window-report-max-years', type=int, default=5,
                   help='Capped rolling-window width for causal OOS scoring (default: %(default)s)')
    p.add_argument('--max-workers', type=int, default=None,
                   help='Process pool size (default: os.cpu_count())')
    p.add_argument('--sheets-spreadsheet', default=None, metavar='NAME',
                   help='Upload grid summary, causal-window metrics, and symbol diagnostics to this '
                        'existing Google spreadsheet. Requires GSPREAD_KEY and service-account Editor access.')
    p.add_argument('--no-save', action='store_true', help='Skip saving the results CSV')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    parts = args.years.split('-')
    start_year, end_year = (parts[0], parts[0]) if len(parts) == 1 else (parts[0], parts[1])
    start_date = date(int(start_year), 1, 1)
    end_date = date(int(end_year), 12, 31)
    oos_start = date.fromisoformat(args.oos_start) if args.oos_start else date(int(start_year) + 1, 1, 1)
    if not start_date <= oos_start <= end_date:
        raise ValueError('--oos-start must fall within --years')

    param_grid = {
        'vol_target': [0.10, 0.15, 0.20],
        'regime_discount': [0.0, 0.5, 1.0],
        'long_only': [False, True],
    }

    results = run_grid(
        symbols, start_date, end_date, param_grid, oos_start=oos_start,
        max_width_years=args.window_report_max_years, max_workers=args.max_workers,
    )
    with pl.Config(tbl_rows=-1):
        print(results.summary.sort('C_capped_rolling_sharpe_mean', descending=True))

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    symbol_str = '_'.join(symbols)

    if not args.no_save:
        results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
        os.makedirs(results_dir, exist_ok=True)
        path = os.path.join(results_dir, f"tsmom_grid_{symbol_str}_{start_year}-{end_year}_{ts}.csv")
        metrics_path = os.path.join(
            results_dir, f"tsmom_grid_window_metrics_{symbol_str}_{start_year}-{end_year}_{ts}.csv"
        )
        symbols_path = os.path.join(
            results_dir, f"tsmom_grid_symbols_{symbol_str}_{start_year}-{end_year}_{ts}.csv"
        )
        results.summary.write_csv(path)
        results.window_metrics.write_csv(metrics_path)
        results.symbol_details.write_csv(symbols_path)
        print(f"\nSaved {results.summary.height} parameter rows to {path}")
        print(f"Saved {results.window_metrics.height} causal OOS window rows to {metrics_path}")

    if args.sheets_spreadsheet:
        from derivatives_bt_engine.utils.tsmom_sheets import upload_tsmom_frames
        upload_tsmom_frames(
            spreadsheet_name=args.sheets_spreadsheet,
            run_label=f'tsmom_grid_{symbol_str}',
            frames={
                'summary': results.summary,
                'window_metrics': results.window_metrics,
                'symbol_details': results.symbol_details,
            },
        )


if __name__ == "__main__":
    main()

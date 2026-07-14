"""
Grid search over TSMOM hyperparameters (vol_target, momentum_discount,
long_only, max_notional, max_contracts), parallelized via multiprocessing.

Unlike the options engine's GridSearchBacktester (grid_search_backtester.py,
sequential -- each combo mutates/reuses a shared Backtester instance),
run_tsmom_backtest is a pure function over a fresh config each call, with no
shared mutable state across runs, so combos can run as truly independent
worker processes via ProcessPoolExecutor.

Run:
    python -m derivatives_bt_engine.strats.tsmom_grid_search --symbols ES --years 2015-2025
"""
import argparse
import itertools
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Iterable, Optional

import polars as pl

from derivatives_bt_engine.domain.tsmom_backtester import TsmomBacktestConfig, load_portfolio_data, run_tsmom_backtest
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


def _run_one(combo: dict, symbols: list[str], start_date: date, end_date: date) -> list[dict]:
    """Top-level, picklable worker: build a config from `combo`, run the
    backtest, return one summary row per symbol. Assumes the parquet/VIX
    cache is already warm (run_grid does this once in the parent process
    before spawning workers, to avoid concurrent writes to the same cache
    file from multiple processes)."""
    config = TsmomBacktestConfig(
        symbols=symbols, start_date=start_date, end_date=end_date,
        vol_target=combo.get('vol_target', 0.15),
        momentum_discount=combo.get('momentum_discount', 0.5),
        long_only=combo.get('long_only', False),
        max_notional=combo.get('max_notional', 250_000),
        max_contracts=combo.get('max_contracts', 10),
    )
    result = run_tsmom_backtest(config)
    stats = result['stats']
    all_dates = stats['date'].to_list()
    rows = []
    for symbol in symbols:
        switches = _direction_switches(result['events'], symbol)
        rows.append({
            **combo,
            'symbol': symbol,
            'final_capital': stats['capital'][-1],
            'cum_pnl': stats['cum_pnl'][-1],
            'max_drawdown_pct': stats['drawdown_pct'].min(),
            'time_in_trade_pct': round(_time_in_trade_pct(result['events'], all_dates, symbol), 1),
            'n_direction_switches': len(switches),
        })
    return rows


def run_grid(symbols: list[str], start_date: date, end_date: date, param_grid: dict,
             max_workers: Optional[int] = None) -> pl.DataFrame:
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
    rows = []
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as ex:
        futures = {ex.submit(_run_one, combo, symbols, start_date, end_date): combo for combo in combos}
        for i, fut in enumerate(as_completed(futures), 1):
            rows.extend(fut.result())
            if i % 10 == 0 or i == len(combos):
                logger.info(f"Completed {i}/{len(combos)} combos")

    return pl.DataFrame(rows)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default='ES',
                   help='Comma-separated futures symbols (default: %(default)s)')
    p.add_argument('--years', default='2015-2025',
                   help='Year range as START-END or a single YEAR (default: %(default)s)')
    p.add_argument('--max-workers', type=int, default=None,
                   help='Process pool size (default: os.cpu_count())')
    p.add_argument('--no-save', action='store_true', help='Skip saving the results CSV')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    parts = args.years.split('-')
    start_year, end_year = (parts[0], parts[0]) if len(parts) == 1 else (parts[0], parts[1])
    start_date = date(int(start_year), 1, 1)
    end_date = date(int(end_year), 12, 31)

    param_grid = {
        'vol_target': [0.10, 0.15, 0.20],
        'momentum_discount': [0.0, 0.5, 1.0],
        'long_only': [False, True],
    }

    df = run_grid(symbols, start_date, end_date, param_grid, max_workers=args.max_workers)
    with pl.Config(tbl_rows=-1):
        print(df.sort('cum_pnl', descending=True))

    if not args.no_save:
        results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
        os.makedirs(results_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        symbol_str = '_'.join(symbols)
        path = os.path.join(results_dir, f"tsmom_grid_{symbol_str}_{start_year}-{end_year}_{ts}.csv")
        df.write_csv(path)
        print(f"\nSaved {len(df)} rows to {path}")


if __name__ == "__main__":
    main()

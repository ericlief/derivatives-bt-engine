"""
CLI for a naked (single-leg, long or short) futures backtest. Accepts one
or more comma-separated symbols -- each runs as its own fully independent
single-position backtest (own capital/margin, no shared risk budget or
correlation-aware sizing across symbols; that's the TSMOM backtester's
job, not this one) so you can quickly compare e.g. how a signal-gate rule
plays out on ES vs GC side by side.

Run:
    naked --symbols ES --dir long --years 2025-2026
    naked --symbols ES --dir long --years 2025
    naked --symbols MES --dir short --years 2025-2026 --quantity 2
    naked --symbols ES,GC,CL --dir long --years 2010-2026 --ts-exit-threshold 0 --ts-entry-threshold 0.5
"""
import argparse
import os

import polars as pl

from derivatives_bt_engine.domain.backtester import Backtester
from derivatives_bt_engine.domain.enums import FuturesStrategy
from derivatives_bt_engine.domain.futures_dataloader import FuturesDataLoader
from derivatives_bt_engine.domain.instruments import resolve_price_symbol
from derivatives_bt_engine.domain.strategy_config import FuturesStrategyConfig
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# Same VIX_PATH convention as the options strategies (iron_condor.py,
# bull_put.py, ...) -- a directory resolves to {dir}/processed/vix.csv
# (see BaseDataLoader._resolve_source_paths), currently
# ~/data/fin/market/index/VIX/eod, fresh through the latest trading day.
VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default='ES',
                   help='Comma-separated futures symbols, each a known instruments.py symbol '
                        '-- each runs as its own independent backtest (default: %(default)s)')
    p.add_argument('--dir', choices=['long', 'short'], default='long',
                   help='Position direction/side (default: %(default)s)')
    p.add_argument('--years', default='2025-2026',
                   help='Year range as START-END (inclusive) or a single YEAR (default: %(default)s)')
    p.add_argument('--quantity', type=int, default=1)
    p.add_argument('--initial-capital', type=float, default=100000)
    p.add_argument('--leverage', type=float, default=1.0)
    p.add_argument('--ts-exit-threshold', type=float, default=None,
                   help='Exit if the raw tsmom signal (no vol-target/discount applied) '
                        'weakens past this threshold, direction-aware (default: disabled)')
    p.add_argument('--ts-entry-threshold', type=float, default=None,
                   help='Block (re-)entry until the raw tsmom signal recovers past this '
                        'threshold, direction-aware -- typically stronger than '
                        '--ts-exit-threshold to avoid close/reopen thrashing at one shared '
                        'line (default: disabled)')
    p.add_argument('--exit-on-ts-crossover', action='store_true',
                   help='Exit when ts3m crosses to the wrong side of ts1y for this position\'s '
                        'direction, and block entry until it crosses back (default: disabled)')
    p.add_argument('--no-save', action='store_true', help='Skip saving trades/transactions/mtm to results/')
    return p.parse_args()


def _parse_years(years: str) -> tuple[str, str]:
    parts = years.split('-')
    if len(parts) == 1:
        return parts[0], parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"--years must be YYYY or YYYY-YYYY, got {years!r}")


def _run_one_symbol(symbol: str, args) -> tuple[dict, pl.DataFrame]:
    """Runs one fully independent single-position backtest for `symbol` --
    its own capital/margin, no shared risk budget or correlation-aware
    sizing with any other symbol (that coordination is the TSMOM
    backtester's job, not this one). Returns (summary row for the
    cross-symbol comparison table, this symbol's own daily mtm stats)."""
    futures_strategy = FuturesStrategy.LONG_FUTURES if args.dir == 'long' else FuturesStrategy.SHORT_FUTURES

    start_year, end_year = _parse_years(args.years)
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"

    # use_preprocessed=True is required for VIX (not just an optimization):
    # BaseDataLoader.vix_data only reads the already-current
    # {VIX_PATH}/processed/vix.parquet cache when this is True. With it
    # False, vix_data falls through to re-parsing processed/vix.csv, which
    # is a stale, separately-maintained file (ends 2024-12-31, and its
    # ambiguous M/D/YYYY-with-time date strings silently fail to parse for
    # ~60% of rows even within that stale range). Safe for the futures side
    # too: save_preprocessed=False means FuturesDataLoader.daily never
    # writes a local cache, so with no such file already present this still
    # queries duckdb fresh every run, same as before.
    # price_symbol: some micros (MES, MNQ, MTN, ...) have no db history under
    # their own symbol -- resolve_price_symbol borrows the full-size
    # sibling's (ES, NQ, ZN, ...) via instruments.py's db_symbol field.
    # `symbol` itself (used as futures_type below) stays the raw traded
    # ticker, so sizing/margin/PnL are still MES-scaled, never ES-scaled.
    # FuturesStrategyConfig.__post_init__ validates `symbol` against
    # instruments.known_futures_symbols() -- no separate check needed here.
    price_symbol = resolve_price_symbol(symbol)
    if price_symbol != symbol:
        logger.info(f"{symbol}: no db history under its own symbol -- borrowing {price_symbol}'s continuous price history")
    dl = FuturesDataLoader(asset=price_symbol, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    config = FuturesStrategyConfig(
        quantity=args.quantity,
        futures_type=symbol,
        futures_strategy=futures_strategy,
        initial_capital=args.initial_capital,
        leverage=args.leverage,
        start_date=start_date,
        end_date=end_date,
        fill_price='mid',
        ts_exit_threshold=args.ts_exit_threshold,
        ts_entry_threshold=args.ts_entry_threshold,
        exit_on_ts_crossover=args.exit_on_ts_crossover,
    )

    bt = Backtester(data=data, save_trades=not args.no_save, log_to_sheets=False)
    results = bt.run(config)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        print(f"\n=== {symbol} ===")
        print(results['trade_results'])

    trade_results = results['trade_results']
    dd = results.get('drawdown_analysis', {})
    summary = {
        'symbol': symbol,
        'trades': trade_results.height,
        'win_rate_pct': round(100 * (trade_results['pnl'] > 0).sum() / trade_results.height, 2) if trade_results.height else None,
        'total_pnl': round(trade_results['cumulative_pnl'][-1], 2) if trade_results.height else None,
        'max_loss': round(trade_results['pnl'].min(), 2) if trade_results.height else None,
        'avg_days_held': round(trade_results['days_held'].mean(), 1) if trade_results.height else None,
        'max_drawdown_usd': dd.get('max_drawdown'),
    }
    stats = results.get('stats', pl.DataFrame())
    return summary, stats


def _build_total_mtm(symbols: list[str], stats_by_symbol: dict[str, pl.DataFrame], initial_capital: float) -> pl.DataFrame:
    """Combines each symbol's independent daily mtm series (own capital,
    own initial_capital -- NOT a shared risk-budgeted portfolio, just N
    accounts' equity curves added together) into one daily total. Symbols
    with different data coverage are handled via an outer join: a missing
    day contributes 0 mtm_pnl and carries forward that symbol's last known
    capital (or its own initial_capital before its first available day)."""
    per_symbol = []
    for symbol in symbols:
        s = stats_by_symbol[symbol]
        if s.height == 0:
            continue
        per_symbol.append(
            s.select(['date', 'capital', 'mtm_pnl']).rename(
                {'capital': f'capital_{symbol}', 'mtm_pnl': f'mtm_pnl_{symbol}'}
            )
        )
    if not per_symbol:
        return pl.DataFrame()

    combined = per_symbol[0]
    for other in per_symbol[1:]:
        combined = combined.join(other, on='date', how='full', coalesce=True)
    combined = combined.sort('date')

    for symbol in symbols:
        cap_col, pnl_col = f'capital_{symbol}', f'mtm_pnl_{symbol}'
        if cap_col not in combined.columns:
            continue
        combined = combined.with_columns(
            pl.col(cap_col).fill_null(strategy='forward').fill_null(initial_capital).alias(cap_col),
            pl.col(pnl_col).fill_null(0.0).alias(pnl_col),
        )

    cap_cols = [f'capital_{s}' for s in symbols if f'capital_{s}' in combined.columns]
    pnl_cols = [f'mtm_pnl_{s}' for s in symbols if f'mtm_pnl_{s}' in combined.columns]
    combined = combined.with_columns(
        total_capital=pl.sum_horizontal(cap_cols),
        total_mtm_pnl=pl.sum_horizontal(pnl_cols),
    )
    total_initial = initial_capital * len(cap_cols)
    combined = combined.with_columns(
        total_cum_pnl=(pl.col('total_capital') - total_initial).round(2),
        total_running_max=pl.col('total_capital').cum_max(),
    )
    combined = combined.with_columns(
        total_dd_usd=(pl.col('total_capital') - pl.col('total_running_max')).round(2),
    )
    combined = combined.with_columns(
        total_dd_pct=pl.when(pl.col('total_running_max') > 0)
        .then((pl.col('total_dd_usd') / pl.col('total_running_max') * 100).round(2))
        .otherwise(0.0)
    )
    return combined


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    summary_rows = []
    stats_by_symbol = {}
    for symbol in symbols:
        summary, stats = _run_one_symbol(symbol, args)
        summary_rows.append(summary)
        stats_by_symbol[symbol] = stats

    if len(summary_rows) > 1:
        with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
            print("\n=== Cross-symbol summary (each an independent backtest, no shared risk budget) ===")
            print(pl.DataFrame(summary_rows))

        total_mtm = _build_total_mtm(symbols, stats_by_symbol, args.initial_capital)
        if total_mtm.height > 0:
            with pl.Config(tbl_rows=10, tbl_cols=-1, tbl_width_chars=200):
                print("\n=== Total mtm (sum of each symbol's own independent equity curve) ===")
                print(total_mtm.tail(10))
            print(f"Total final capital: ${total_mtm['total_capital'][-1]:,.2f}  "
                  f"Total cumulative PnL: ${total_mtm['total_cum_pnl'][-1]:,.2f}  "
                  f"Total max drawdown: ${total_mtm['total_dd_usd'].min():,.2f} "
                  f"({total_mtm['total_dd_pct'].min():.2f}%)")

            if not args.no_save:
                start_year, end_year = _parse_years(args.years)
                results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
                os.makedirs(results_dir, exist_ok=True)
                symbol_str = '_'.join(symbols)
                total_mtm.write_csv(os.path.join(results_dir, f"total_mtm_{symbol_str}_{start_year}-{end_year}.csv"))


if __name__ == "__main__":
    main()

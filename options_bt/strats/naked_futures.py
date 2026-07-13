"""
CLI for a naked (single-leg, long or short) futures backtest.

Run:
    naked --symbol ES --dir long --years 2025-2026
    naked --symbol ES --dir long --years 2025
    naked --symbol MES --dir short --years 2025-2026 --quantity 2
"""
import argparse
import os

import polars as pl

from options_bt.domain.backtester import Backtester
from options_bt.domain.enums import FuturesStrategy
from options_bt.domain.futures_dataloader import FuturesDataLoader
from options_bt.domain.instruments import resolve_price_symbol
from options_bt.domain.strategy_config import FuturesStrategyConfig
from options_bt.utils.logger import setup_logger

logger = setup_logger()

# Same VIX_PATH convention as the options strategies (iron_condor.py,
# bull_put.py, ...) -- a directory resolves to {dir}/processed/vix.csv
# (see BaseDataLoader._resolve_source_paths), currently
# ~/data/fin/market/index/VIX/eod, fresh through the latest trading day.
VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbol', default='ES',
                   help='Futures symbol, must be a known instruments.py symbol (default: %(default)s)')
    p.add_argument('--dir', choices=['long', 'short'], default='long',
                   help='Position direction/side (default: %(default)s)')
    p.add_argument('--years', default='2025-2026',
                   help='Year range as START-END (inclusive) or a single YEAR (default: %(default)s)')
    p.add_argument('--quantity', type=int, default=1)
    p.add_argument('--initial-capital', type=float, default=100000)
    p.add_argument('--leverage', type=float, default=1.0)
    p.add_argument('--no-save', action='store_true', help='Skip saving trades/transactions/mtm to results/')
    return p.parse_args()


def main():
    args = parse_args()

    symbol = args.symbol.upper()
    futures_strategy = FuturesStrategy.LONG_FUTURES if args.dir == 'long' else FuturesStrategy.SHORT_FUTURES

    parts = args.years.split('-')
    if len(parts) == 1:
        start_year = end_year = parts[0]
    elif len(parts) == 2:
        start_year, end_year = parts
    else:
        raise ValueError(f"--years must be YYYY or YYYY-YYYY, got {args.years!r}")
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
        fill_price='mid'
    )

    bt = Backtester(data=data, save_trades=not args.no_save, log_to_sheets=False)
    results = bt.run(config)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        print(results['trade_results'])


if __name__ == "__main__":
    main()

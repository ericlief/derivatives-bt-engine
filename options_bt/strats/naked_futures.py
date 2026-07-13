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
from options_bt.domain.enums import FuturesStrategy, FuturesType
from options_bt.domain.futures_dataloader import FuturesDataLoader
from options_bt.domain.strategy_config import FuturesStrategyConfig

# Same VIX_PATH convention as the options strategies (iron_condor.py,
# bull_put.py, ...) -- a directory resolves to {dir}/processed/vix.csv
# (see BaseDataLoader._resolve_source_paths), currently
# ~/data/fin/market/index/VIX/eod, fresh through the latest trading day.
VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbol', default='ES',
                   help='Futures symbol, must be a defined FuturesType member (default: %(default)s)')
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
    try:
        futures_type = FuturesType.from_symbol(symbol)
    except KeyError:
        raise ValueError(f"Unknown futures symbol {symbol!r}. Defined types: {[t.name for t in FuturesType]}")

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

    dl = FuturesDataLoader(asset=symbol, vix_file=VIX_FILE, use_preprocessed=False, save_preprocessed=False)
    data = dl.load_data()

    config = FuturesStrategyConfig(
        quantity=args.quantity,
        futures_type=futures_type,
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

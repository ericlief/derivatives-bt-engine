"""
Ad hoc profiling script for the backtest engine (not part of the installed
package — dev/ops scripts live here, strategy code lives in derivatives_bt_engine/).

Times an options backtest and a futures backtest through the current
Backtester API. Update the data paths below before running.
"""
import logging
import time

from derivatives_bt_engine.backtest.backtester import Backtester
from derivatives_bt_engine.data.dataloader import OptionsDataLoader
from derivatives_bt_engine.backtest.futures.types import FuturesStrategy
from derivatives_bt_engine.backtest.options.types import OptionsStrategy, OptionsType
from derivatives_bt_engine.backtest.position_side import PositionSide
from derivatives_bt_engine.backtest.option_leg_config import OptionLegConfig
from derivatives_bt_engine.backtest.strategy_config import FuturesStrategyConfig, SingleLegOptionStrategyConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def profile_options_backtest(data_dir: str, options_file: str, vix_file: str):
    dl = OptionsDataLoader(data_dir=data_dir, options_file=options_file, vix_file=vix_file,
                           use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    config = SingleLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionsStrategy.SHORT_PUT,
        initial_capital=100000,
        leverage=1.0,
        start_date="2020-01-01",
        end_date="2020-12-31",
        leg=OptionLegConfig(
            option_type=OptionsType.PUT,
            position_side=PositionSide.SHORT,
            delta_target=0.30,
            dte_target=30,
        ),
    )

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False)
    start = time.time()
    results = bt.run(config)
    elapsed = time.time() - start
    logger.info(f"Options backtest: {len(results['trade_results'])} trades in {elapsed:.2f}s")
    return elapsed


def profile_futures_backtest(asset: str = 'ES'):
    from derivatives_bt_engine.data.futures_dataloader import FuturesDataLoader

    dl = FuturesDataLoader(asset=asset, use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    config = FuturesStrategyConfig(
        quantity=1,
        futures_type='MES',
        futures_strategy=FuturesStrategy.LONG_FUTURES,
        initial_capital=100000,
        leverage=1.0,
        start_date="2020-01-01",
        end_date="2020-12-31",
    )

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False)
    start = time.time()
    results = bt.run(config)
    elapsed = time.time() - start
    logger.info(f"Futures backtest: {len(results['trade_results'])} trades in {elapsed:.2f}s")
    return elapsed


if __name__ == "__main__":
    DATA_DIR = "/path/to/spx/data"
    OPTIONS_FILE = "options_chain_preprocessed.csv"
    VIX_FILE = "vix.csv"

    logger.info("Starting backtest profiling...")
    options_time = profile_options_backtest(DATA_DIR, OPTIONS_FILE, VIX_FILE)
    futures_time = profile_futures_backtest()
    logger.info(f"Options: {options_time:.2f}s | Futures: {futures_time:.2f}s")

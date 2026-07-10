import os
from pathlib import Path

import pandas as pd
from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *
from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig
from dotenv import load_dotenv

# Create logger instance
logger = setup_logger()
load_dotenv()


def run_test_suite():

    """Run a suite of backtest examples with different configurations."""

    # Set up data paths.
    # NOTE: OptionsDataLoader currently requires the options chain, underlying
    # (spx.csv), and vix.csv to live in one directory (the 'underlying' filename
    # is hardcoded, not configurable), but the real source files live in three
    # separate directories under /home/dev/data/fin/market/index/. This scratch
    # fixture co-locates a Jan-Jun 2019 slice of all three for local runs; the
    # Phase 1 polars rewrite of dataloader.py should let each source be pointed
    # at its own real path instead of requiring this workaround.
    
    # Set up data paths
    print('data path', os.getenv('DATA_PATH'))

    DATA_PATH = Path(os.getenv('DATA_PATH')).expanduser()
    print('data path', os.getenv('DATA_PATH'))
    OPTIONS_FILE = "options_chain_preprocessed.csv"
    VIX_FILE = "vix.csv"
    # DATA_PATH = "/tmp/claude-1000/-home-dev-projects-o4ptions-bt/0c09cbb9-f8d7-453b-8d99-3ce255a715aa/scratchpad/spx_fixture/csv_for_pandas_baseline"
    

    dl = OptionsDataLoader(data_dir=DATA_PATH, options_file=OPTIONS_FILE, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    configs = [
        MultiLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionStrategy.IRON_CONDOR,
        spread_type=OptionSpreadType.IRON_CONDOR,
        initial_capital=100000,
        leverage=1.0,
        start_date="2017-02-23",
        end_date="2018-02-23",
        use_underlying_close=False,
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=50,
        max_trade_loss=7500.00,
        trade_selection_method=TradeSelectionMethod.PREMIUM_FIRST,

        # _pair_iron_condor_spread_legs() pairs leg_signals positionally, not by
        # option_type/position_side, so this order is load-bearing: it must be
        # [long put (lower strike), short put (higher strike),
        #  short call (lower strike), long call (higher strike)].
        legs=[
            OptionLegConfig(
            option_type=OptionType.PUT,
            position_side=PositionSide.LONG,
            delta_target=0.20,
            dte_target=30,
            ),
            OptionLegConfig(
            option_type=OptionType.PUT,
            position_side=PositionSide.SHORT,
            delta_target=0.30,
            dte_target=30,
            ),
            OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.SHORT,
            delta_target=0.30,
            dte_target=30,
            ),
            OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.LONG,
            delta_target=0.20,
            dte_target=30,
            ),
        ],
        ),
    ]

    for i, config in enumerate(configs):

        bt = Backtester(
            data=data,
            save_trades=True,
            log_to_sheets=True
        )
        results = bt.run(config)
        print(results)


if __name__ == "__main__":
    run_test_suite()

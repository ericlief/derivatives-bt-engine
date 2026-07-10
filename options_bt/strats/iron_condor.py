import os

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

    # Set up data paths. The options chain, SPX underlying, and VIX files
    # live in three different directories, each fully resolved to an
    # absolute path by its own env var -- OptionsDataLoader takes them as-is,
    # no shared base directory needed.
    #
    # .env example:
    #   SPX_OPTIONS_CHAIN_PATH=/Users/liefe/data/fin/market/index/SPX/external/options/historical/eod/processed/options_chain_preprocessed.csv
    #   SPX_UNDERLYING_PATH=/Users/liefe/data/fin/market/index/SPX/external/index/processed/spx-daily-1996-ohlc-cleaned.csv
    #   VIX_PATH=/Users/liefe/data/fin/market/index/VIX/historical/vix.parquet
    OPTIONS_FILE = os.getenv('SPX_OPTIONS_CHAIN_PATH', 'options_chain_preprocessed.csv')
    SPX_FILE = os.getenv('SPX_UNDERLYING_PATH', 'spx.csv')
    VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')

    dl = OptionsDataLoader(options_file=OPTIONS_FILE, spx_file=SPX_FILE, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
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
        max_spread_width=10,
        use_spread_width=True,
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
            # delta_target=0.20,
            dte_target=45,
            ),
            OptionLegConfig(
            option_type=OptionType.PUT,
            position_side=PositionSide.SHORT,
            delta_target=0.25,
            dte_target=45,
            ),
            OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.SHORT,
            delta_target=0.25,
            dte_target=45,
            ),
            OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.LONG,
            # delta_target=0.20,
            dte_target=45,
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

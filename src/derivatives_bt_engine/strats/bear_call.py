import os

from derivatives_bt_engine.utils.logger import setup_logger
from derivatives_bt_engine.domain.enums import *
from derivatives_bt_engine.domain.backtester import Backtester
from derivatives_bt_engine.domain.dataloader import OptionsDataLoader
from derivatives_bt_engine.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from derivatives_bt_engine.domain.option_leg_config import OptionLegConfig
from dotenv import load_dotenv

# Create logger instance
logger = setup_logger()
load_dotenv()


def main():

    """Run a suite of backtest examples with different configurations."""
    # Display width/precision for results (polars DataFrames) is configured
    # globally by backtester.py's module-level pl.Config calls.

    # Set up data paths. The options chain, SPX underlying, and VIX files
    # live in three different directories, each fully resolved to an
    # absolute path by its own env var -- OptionsDataLoader takes them as-is,
    # no shared base directory needed.
    #
    # .env example:
    #   SPX_OPTIONS_CHAIN_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_chain_eod.csv inside it)
    #   SPX_UNDERLYING_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_eod_preproc.csv inside it)
    #   VIX_PATH=/Users/liefe/data/fin/market/index/VIX/eod  (looks for processed/vix.csv inside it)
    OPTIONS_FILE = os.getenv('SPX_OPTIONS_CHAIN_PATH', 'options_chain_preprocessed.csv')
    SPX_FILE = os.getenv('SPX_UNDERLYING_PATH', 'spx.csv')
    VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')

    dl = OptionsDataLoader(options_file=OPTIONS_FILE, spx_file=SPX_FILE, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()
    # print(dl.__dict__)
    # Check data quality once
    # check_data_quality(options_chain, spx_data, vix_data)
    
    # preloaded_data = {
    #     'spx_data': spx_data,
    #     'options_data': options_chain,
    #     'options_data_multi': options_chain_multi_index,
    #     'vix_data': vix_data
    # }
    
    bt = Backtester(
        data=data,
        save_trades=True,
        log_to_sheets=True
    )

    config = MultiLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionsStrategy.BEAR_CALL_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        # leg_ratio=1.0,
        initial_capital=100000,
        leverage=1.0,
        start_date="2020-01-01",
        end_date="2020-12-31",
        use_underlying_close=False,
        early_close_days=30,
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=100,
        # Define the leg of the strategy
        legs=[
            OptionLegConfig(
            option_type=OptionsType.CALL,
            position_side=PositionSide.SHORT,
            # delta_range=(0.65, 0.75),
            delta_target=0.75,
            dte_range=(40, 45),
            ),
            OptionLegConfig(
            option_type=OptionsType.CALL,
            position_side=PositionSide.LONG,
            # delta_range=(0.65, 0.75),
            delta_target=0.65,
            dte_range=(40, 45),
            )
        ],
    
    )


    # config = SingleLegOptionStrategyConfig(
    #     quantity=1,
    #     option_strategy=OptionsStrategy.SHORT_CALL,
    #     initial_capital=100000,
    #     leverage=1.0,
    #     start_date="2020-01-01",
    #     end_date="2020-12-31",
    #     use_underlying_close=False,
    #     early_close_days=30,
    #     max_margin_utilization=0.80,
    #     max_positions=1,
    #     # Define the leg of the strategy
    #     leg=OptionLegConfig(
    #         option_type=OptionsType.CALL,
    #         position_side=PositionSide.SHORT,
    #         # delta_range=(0.65, 0.75),
    #         delta_target=0.75,
    #         dte_range=(40, 45),
    #         )
            
    # )

    results = bt.run(config)
    trade_results = results['trade_results']
    transactions = results['transactions']

    print('Finished backtest')
    print('Trade results: ')
    print(trade_results.head())
    print('Transactions:')
    print(transactions.head())
    print()

    # Define hyperparameter sets for different tests
    # hyperparameter_sets = [
        # {
        #     'option_type': OptionsType.CALL,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.75,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-3-31",
        #     'dte_range': (42, 45),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # },
        # {
        #     'option_type': OptionsType.CALL,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.75,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-3-31",
        #     'dte_range': (42, 45),
        #     'initial_capital': 100000,
        #     'early_close_days': 30
        # },
        # {
        #     'strategy': OptionsStrategy.SHORT_CALL,
        #     'option_type': OptionsType.CALL,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.75,
        #     'use_underlying_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (42, 45),
        #     'early_close_days': 30,
        #     'max_positions': 1,
        #     'initial_capital': 100000,
        #     'leverage': 1.0
        # },
        #    {
        #     'option_type': OptionsType.CALL,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.75,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (42, 45),
        #     'early_close_days': 30,
        #     'max_positions': 2,
        #     'initial_capital': 100000,
        #     'leverage': 2.0
        # },
    # ]

    # configs = [SingleLegOptionStrategyConfig(**config) for config in hyperparameter_sets]
    # # Run all tests using run_multiple_backtests
    # logger.info("\nRunning multiple backtests with different configurations...")
    # results = backtester.run_multiple_backtests(
    #     configs=configs
    # )

    # # Print results summary
    # logger.info("\nTest Results Summary:")
    # for test_id, test_data in results.items():
    #     params = test_data['params']
    #     result_df = test_data['results']
    #     execution_time = test_data['execution_time']
        
    #     logger.info(f"\n{test_id}:")
    #     logger.info(f"Parameters: {params}")
    #     logger.info(f"Execution time: {execution_time:.2f} seconds")
        
    #     if not result_df.empty:c
    #         logger.info(f"Total trades: {len(result_df)}")
    #         logger.info(f"Win rate: {(result_df['pnl'] > 0).mean():.2%}")
    #         logger.info(f"Total P&L: ${result_df['pnl'].sum():.2f}")
    #         logger.info(f"Return on capital: {(result_df['capital'].iloc[-1] / params['initial_capital'] - 1):.2%}")
    #         logger.info(f"Average days held: {result_df['days_held'].mean():.1f}")
    #         logger.info(f"Average return on margin: {result_df['return_on_margin'].mean():.2f}%")
    #     else:
    #         logger.warning("No trades executed for this configuration")

if __name__ == "__main__":
    main()
    
 
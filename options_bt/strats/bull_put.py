import os
from pathlib import Path

from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *
from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig
from dotenv import load_dotenv

# Create logger instance
logger = setup_logger()
load_dotenv()


def run_test_suite():

    """Run a suite of backtest examples with different configurations."""


    # Set up data paths.
    # The options chain, SPX underlying, and VIX files live in three
    # different directories -- DATA_PATH alone can't express that, so each
    # source has its own optional env var. Set them to full absolute paths
    # in .env; OptionsDataLoader uses an absolute options_file/spx_file/
    # vix_file as-is instead of joining it to data_dir (data_dir is only
    # used as the join-base for any of the three left as a bare filename).
    #
    # .env example:
    #   DATA_PATH=/Users/liefe/data/fin/market/index/SPX
    #   SPX_OPTIONS_CHAIN_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_chain_eod.csv inside it)
    #   SPX_UNDERLYING_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_eod_preproc.csv inside it)
    #   VIX_PATH=/Users/liefe/data/fin/market/index/VIX/eod  (looks for processed/vix.csv inside it)
    DATA_PATH = Path(os.getenv('DATA_PATH')).expanduser()
    OPTIONS_FILE = os.getenv('SPX_OPTIONS_CHAIN_PATH', 'options_chain_preprocessed.csv')
    SPX_FILE = os.getenv('SPX_UNDERLYING_PATH', 'spx.csv')
    VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')

    dl = OptionsDataLoader(data_dir=DATA_PATH, options_file=OPTIONS_FILE, spx_file=SPX_FILE, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
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
    
    configs = [ 
        MultiLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        # leg_ratios={0: 1.0, 1: 2.0, 2: 2.0, 3: 1.0},   
        initial_capital=100000,
        leverage=1.0,
        start_date="2017-02-23",
        end_date="2018-02-23",
        use_underlying_close=False,
        # early_close_on_dte=30,
        # early_close_after_dit=5
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=5,
        max_trade_loss=7500.00,
        # trade_selection_method=TradeSelectionMethod.DELTA_FIRST,
        trade_selection_method=TradeSelectionMethod.PREMIUM_FIRST,
        # vix_range=None,
        # vix_max=25, 
        # premium_ratio=0.33,

        # Define the leg of the strategy
        legs=[
            OptionLegConfig(
            option_type=OptionType.PUT,
            position_side=PositionSide.SHORT,
            # delta_range=(0.65, 0.75),
            delta_target=0.50,
            # dte_range=(40, 45),
            dte_target=30,
            ),
            OptionLegConfig(
            option_type=OptionType.PUT,
            position_side=PositionSide.LONG,
            # delta_range=(0.65, 0.75),
            delta_target=0.45,
            # dte_range=(40, 45),
            dte_target=30,
            )
        ],
        ),
    ]
    #     MultiLegOptionStrategyConfig(
    #     quantity=1,
    #     option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
    #     spread_type=OptionSpreadType.VERTICAL,
    #     # leg_ratio={0: 1.0, 1: 2.0, 2: 2.0, 3: 1.0},   
    #     initial_capital=100000,
    #     leverage=1.0,
    #     start_date="2020-01-01",
    #     end_date="2020-12-31",
    #     use_underlying_close=False,
    #     early_close_days=30,
    #     max_margin_utilization=0.80,
    #     max_positions=1,
    #     max_spread_width=100,
    #     max_trade_loss=10000.00,
    #     trade_selection_method=TradeSelectionMethod.PREMIUM_FIRST,
        
    #     # Define the leg of the strategy
    #     legs=[
    #         OptionLegConfig(
    #         option_type=OptionType.PUT,
    #         position_side=PositionSide.SHORT,
    #         # delta_range=(0.65, 0.75),
    #         delta_target=0.75,
    #         dte_range=(40, 45),
    #         ),
    #         OptionLegConfig(
    #         option_type=OptionType.PUT,
    #         position_side=PositionSide.LONG,
    #         # delta_range=(0.65, 0.75),
    #         delta_target=0.55,
    #         dte_range=(40, 45),
    #         )
    #     ],
    
    #     ),
    # ]
    for i, config in enumerate(configs):


        bt = Backtester(
            data=data,
            save_trades=True,
            log_to_sheets=True
        )
        results = bt.run(config)
        print(results)

      
        # trade_results = results['trade_results']
        # transactions = results['transactions']


        # print('Finished backtest')
        # print('Trade results: ')
        # print(trade_results.head())
        # print('Transactions:')
        # print(transactions.head())
        # print()
        # # MTM
        # print('Calculating MTM')
        # mtm_res = bt.calculate_mtm(results, config=config)
        # print(f'MTM results:')
        # print(mtm_res.head())`

    # Define hyperparameter sets for different tests
    # hyperparameter_sets = [
        # {
        #     'option_type': OptionType.CALL,
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
        #     'option_type': OptionType.CALL,
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
        #     'strategy': OptionStrategy.SHORT_CALL,
        #     'option_type': OptionType.CALL,
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
        #     'option_type': OptionType.CALL,
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
    run_test_suite()
    
 
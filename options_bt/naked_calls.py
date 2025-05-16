import pandas as pd

from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *  
from options_bt.domain.backtester import Backtester 
from options_bt.domain.dataloader import DataLoader
 

# Create logger instance
logger = setup_logger()
 

def run_test_suite():

    """Run a suite of backtest examples with different configurations."""
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    # Set up data paths
    DATA_PATH = "/Users/liefe/Data/spx"
    OPTIONS_FILE = "options_chain_preprocessed.csv"

    dl = DataLoader(data_dir=DATA_PATH, options_file=OPTIONS_FILE, use_preprocessed=True, save_preprocessed=False)
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
    
    backtester = Backtester(
        data=data,
        save_trades=True,
        log_to_sheets=True
    )

    # Define hyperparameter sets for different tests
    hyperparameter_sets = [
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
        {
            'option_type': OptionType.CALL,
            'position_side': PositionSide.SHORT,
            'delta_target': 0.75,
            'use_spx_close': True,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (42, 45),
            'early_close_days': 30,
            'max_positions': 1,
            'initial_capital': 100000,
            'leverage': 1.0
        },
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
    ]

    # Run all tests using run_multiple_backtests
    logger.info("\nRunning multiple backtests with different configurations...")
    results = backtester.run_multiple_backtests(
        hyperparameter_sets=hyperparameter_sets
    )

    # Print results summary
    logger.info("\nTest Results Summary:")
    for test_id, test_data in results.items():
        params = test_data['params']
        result_df = test_data['results']
        execution_time = test_data['execution_time']
        
        logger.info(f"\n{test_id}:")
        logger.info(f"Parameters: {params}")
        logger.info(f"Execution time: {execution_time:.2f} seconds")
        
        if not result_df.empty:
            logger.info(f"Total trades: {len(result_df)}")
            logger.info(f"Win rate: {(result_df['pnl'] > 0).mean():.2%}")
            logger.info(f"Total P&L: ${result_df['pnl'].sum():.2f}")
            logger.info(f"Return on capital: {(result_df['capital'].iloc[-1] / params['initial_capital'] - 1):.2%}")
            logger.info(f"Average days held: {result_df['days_held'].mean():.1f}")
            logger.info(f"Average return on margin: {result_df['return_on_margin'].mean():.2f}%")
        else:
            logger.warning("No trades executed for this configuration")

if __name__ == "__main__":
    run_test_suite()
    
 
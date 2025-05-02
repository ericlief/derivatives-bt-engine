import os
import pandas as pd
from options_bt.bt import run_backtest, OptionType, PositionSide, setup_logger, run_multiple_backtests

# Create logger instance
logger = setup_logger()

def run_test_suite():
    """Run a suite of backtest examples with different configurations."""
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    # Set up data paths
    DATA_PATH = "/Users/liefe/Data/spx"    
    SPX_FILE = os.path.join(DATA_PATH, "spx_2018_2023.csv")
    OPTIONS_FILE = os.path.join(DATA_PATH, "spx_options_2018_2023.csv")

    # Define hyperparameter sets for different tests
    hyperparameter_sets = [
        # {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_range': (0.28, 0.35),
        #     'use_spx_close': True,
        #     'start_date': "2011-01-01",
        #     'end_date': "2011-12-31",
        #     'dte_range': (41, 45),
        #     # 'dte_target': 45,
        #     'initial_capital': 100000,
        #     'early_close_days': None,
        #     'quantity': 1,
        #     'max_positions': 1
        # },
        {
            'option_type': OptionType.PUT,
            'position_side': PositionSide.SHORT,
            # 'delta_target': 0.30,
            'delta_range': (0.33, 0.37),
            'use_spx_close': True,
            'start_date': "2011-01-01",
            'end_date': "2011-12-31",
            'dte_range': (40, 46),
            'initial_capital': 200000,
            'early_close_days': None,
            'quantity': 2,
            'max_positions': 1
        },
        # {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.50,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (28, 31),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # },
        # {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.60,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (28, 31),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # },
        # {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.70,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (28, 31),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # },
        # {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.80,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (28, 31),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # },
        #    {
        #     'option_type': OptionType.PUT,
        #     'position_side': PositionSide.SHORT,
        #     'delta_target': 0.90,
        #     'use_spx_close': True,
        #     'start_date': "2020-01-01",
        #     'end_date': "2020-12-31",
        #     'dte_range': (28, 31),
        #     'initial_capital': 100000,
        #     'early_close_days': None
        # }

    ]

    # Run all tests using run_multiple_backtests
    logger.info("\nRunning multiple backtests with different configurations...")
    results = run_multiple_backtests(
        spx_file_path=SPX_FILE,
        options_chain_file_path=OPTIONS_FILE,
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
    
 
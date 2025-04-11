import os
import pandas as pd
from options_bt.bt import run_backtest, OptionType, PositionSide, setup_logger

# Create logger instance
logger = setup_logger()

def run_test_suite():
    """Run a suite of backtest examples with different configurations."""
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    # Set up data paths
    DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "Data", "spx")
    SPX_FILE = os.path.join(DATA_PATH, "spx_2018_2023.csv")
    OPTIONS_FILE = os.path.join(DATA_PATH, "spx_options_2018_2023.csv")

    # Test 1: PUT strategy using SPX close for intrinsic value
    logger.info("\nTest 1: PUT strategy with SPX close price")
    results1 = run_backtest(
        spx_file_path=SPX_FILE,
        options_chain_file_path=OPTIONS_FILE,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=-0.30,
        use_spx_close=True,
        **{
            'data_dir': DATA_PATH,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (28, 31),
            'initial_capital': 100000,
            'early_close_days': None,
            'use_preprocessed': True,
            'save_preprocessed': True,
            'save_trades': True
        }
    )
    print("\nResults for Test 1:")
    print(results1)
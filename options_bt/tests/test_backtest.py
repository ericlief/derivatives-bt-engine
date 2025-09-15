import os
import pandas as pd
from options_bt.bt import run_backtest, OptionType, PositionSide, setup_logger, load_backtest_data

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

    # Load data
    options_chain, options_chain_multi_index, spx_data, vix_data = load_backtest_data(
        DATA_PATH,
        use_preprocessed=True,
        save_preprocessed=True,
        options_file="spx_options_2018_2023.csv"
    )

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

    # Test 2: PUT strategy using option chain's underlying_last
    logger.info("\nTest 2: PUT strategy with option chain underlying price")
    results2 = run_backtest(
        spx_file_path=SPX_FILE,
        options_chain_file_path=OPTIONS_FILE,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=-0.30,
        use_spx_close=False,
        **{
            'data_dir': DATA_PATH,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (28, 31),
            'initial_capital': 100000,
            'early_close_days': None,
            'use_preprocessed': True,
            'save_preprocessed': False,
            'save_trades': True
        }
    )
    print("\nResults for Test 2:")
    print(results2)

    # Test 3: CALL strategy
    logger.info("\nTest 3: CALL strategy")
    results3 = run_backtest(
        spx_file_path=SPX_FILE,
        options_chain_file_path=OPTIONS_FILE,
        option_type=OptionType.CALL,
        position_side=PositionSide.LONG,
        delta_target=0.30,
        use_spx_close=True,
        **{
            'data_dir': DATA_PATH,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (28, 32),
            'initial_capital': 100000,
            'early_close_days': None,
            'use_preprocessed': True,
            'save_preprocessed': False,
            'save_trades': True
        }
    )
    print("\nResults for Test 3:")
    print(results3)

    # Test 4: Early close strategy
    logger.info("\nTest 4: Early close strategy")
    results4 = run_backtest(
        spx_file_path=SPX_FILE,
        options_chain_file_path=OPTIONS_FILE,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=0.30,
        use_spx_close=True,
        **{
            'data_dir': DATA_PATH,
            'start_date': "2020-01-01",
            'end_date': "2020-06-30",
            'dte_range': (40, 45),
            'initial_capital': 100000,
            'early_close_days': 14,
            'use_preprocessed': True,
            'save_preprocessed': False,
            'save_trades': True
        }
    )
    print("\nResults for Test 4:")
    print(results4)

    # Compare results
    logger.info("\nComparison Summary:")
    logger.info("Test 1 - PUT with SPX close:")
    logger.info(f"Total P&L: ${results1['pnl'].sum():.2f}")
    logger.info(f"Win Rate: {(results1['pnl'] > 0).mean():.2%}")
    
    logger.info("\nTest 2 - PUT with option underlying:")
    logger.info(f"Total P&L: ${results2['pnl'].sum():.2f}")
    logger.info(f"Win Rate: {(results2['pnl'] > 0).mean():.2%}")
    
    logger.info("\nTest 3 - Long CALL strategy:")
    logger.info(f"Total P&L: ${results3['pnl'].sum():.2f}")
    logger.info(f"Win Rate: {(results3['pnl'] > 0).mean():.2%}")
    
    logger.info("\nTest 4 - Early close strategy:")
    logger.info(f"Total P&L: ${results4['pnl'].sum():.2f}")
    logger.info(f"Win Rate: {(results4['pnl'] > 0).mean():.2%}")
    
    return {
        'test1': results1,
        'test2': results2,
        'test3': results3,
        'test4': results4
    }

if __name__ == "__main__":
    run_test_suite() 
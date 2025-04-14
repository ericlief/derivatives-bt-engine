import os
import pandas as pd
import time
import numpy as np
import logging
from datetime import datetime
from options_bt.bt import run_backtest, OptionType, PositionSide

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting backtest profiling...")
    
    # Use absolute path
    data_dir = "/Users/liefe/Data/spx"
    spx_file = os.path.join(data_dir, "spx_2018_2023.csv")
    options_file = os.path.join(data_dir, "spx_options_2018_2023.csv")
    
    logger.info(f"Using data directory: {data_dir}")
    
    # Run backtest with timing
    start_time = time.time()
    
    # Run MultiIndex backtest
    logger.info("\nRunning MultiIndex backtest...")
    multi_start = time.time()
    multi_results = run_backtest(
        spx_file_path=spx_file,
        options_chain_file_path=options_file,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=0.30,
        use_spx_close=True,
        **{
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
    multi_time = time.time() - multi_start
    logger.info(f"MultiIndex backtest completed in {multi_time:.2f} seconds")
    
    # Run normal backtest
    logger.info("\nRunning normal backtest...")
    normal_start = time.time()
    normal_results = run_backtest(
        spx_file_path=spx_file,
        options_chain_file_path=options_file,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=0.30,
        use_spx_close=True,
        **{
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
    normal_time = time.time() - normal_start
    logger.info(f"Normal backtest completed in {normal_time:.2f} seconds")
    
    # Calculate total time
    total_time = time.time() - start_time
    logger.info(f"\nTotal execution time: {total_time:.2f} seconds")
    
    # Compare results
    logger.info("\nPerformance Comparison:")
    logger.info(f"MultiIndex backtest:")
    logger.info(f"- Time: {multi_time:.2f} seconds")
    logger.info(f"- Trades: {len(multi_results)}")
    logger.info(f"- Total P&L: ${multi_results['pnl'].sum():.2f}")
    logger.info(f"- ROI: {(multi_results['total_capital'].iloc[-1] / 100000 - 1):.2%}")
    
    logger.info(f"\nNormal backtest:")
    logger.info(f"- Time: {normal_time:.2f} seconds")
    logger.info(f"- Trades: {len(normal_results)}")
    logger.info(f"- Total P&L: ${normal_results['pnl'].sum():.2f}")
    logger.info(f"- ROI: {(normal_results['total_capital'].iloc[-1] / 100000 - 1):.2%}")
    
    # Calculate speedup
    speedup = normal_time / multi_time
    logger.info(f"\nSpeedup: {speedup:.2f}x")

if __name__ == "__main__":
    main() 
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import logging
import time
from datetime import datetime
import os
from enum import Enum

from options_bt.domain.enums import *
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.position import SingleLegOptionPosition     
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.position import MultiLegOptionPosition
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()



class Backtester:
    """Class to manage backtest execution."""
    
    def __init__(self, 
                 data: Dict,
                 save_trades: bool = True,
                 log_to_sheets: bool = True):
        """
        Initialize backtester with configuration.
        
        Args:
            initial_capital: Starting capital
            leverage: Leverage multiplier
            max_positions: Maximum number of simultaneous positions
            max_margin_utilization: Maximum margin utilization as percentage
            save_trades: Whether to save trade results
            log_to_sheets: Whether to log results to Google Sheets
        """

        self.option_chain = data['option_chain']
        self.option_chain_multi_index = data['option_chain_multi_index']
        self.underlying = data['underlying']
        self.vix = data['vix']
        self.save_trades = save_trades
        self.log_to_sheets = log_to_sheets

        # # Track execution times
        self.execution_times = {}

        # Results (clear between runs?)
        self.results: Dict[str, pd.DataFrame] = {}

    def run(
        self,
        config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
        
    ) -> pd.DataFrame:
        """Execute a backtest with the given parameters."""
        start_time = time.time()
        logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Logic for early close days
        # leg_early_close = leg.early_close_days if leg.early_close_days is not None else strategy.early_close_days
        
        # Initialize trade manager
        trade_manager = TradeManager(config=config)
        signal_generator = OptionSignalGenerator(option_chain=self.option_chain.copy(), underlying=self.underlying.copy(), config=config)    
        # Generate or validate signals
        signal_start = time.time()
        if isinstance(config, SingleLegOptionStrategyConfig):
            signals = signal_generator.generate_single_leg_signals()
        elif isinstance(config, MultiLegOptionStrategyConfig):
            signals = signal_generator.generate_multi_leg_signals()
        else:
            raise ValueError("Invalid config type")
        
        self.execution_times['signal_generation'] = time.time() - signal_start
        
        if signals.empty:
            logger.warning("No valid signals generated")
            return pd.DataFrame()

        # Pre-calculate margin requirements for all signals
        logger.info(f"Calculating margin requirements for trade signals for {config.quantity} | {config.leg.option_type if config.leg.option_type else config.spread_type} | {config.leg.delta_target if config.leg.delta_target else config.leg.delta_range}")
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)
        max_marg_use = config.max_margin_utilization * config.initial_capital
        # Handle all spread types
        if is_spread:
            pass
            # TODO: Implement margin calculation for spreads
            # Calculate margins per spread group and ensure proper alignment
            # margins = trade_signals.groupby('spread_id').apply(SingleLegOptionStrategyConfig.calculate_margin_for_spread)
            # trade_signals['margin_required'] = trade_signals['spread_id'].map(margins)
            # logger.debug(f'Calculated margins for {len(margins)} spread groups')
            # logger.debug(f'First few margins: {margins.head()}')

        # Single leg
        else:
            signals['margin_required'] = signals.apply(
                lambda row: SingleLegOptionPosition.calculate_margin(
                    quantity=config.quantity,
                    option_type=config.leg.option_type,
                    position_side=config.leg.position_side,
                    entry_price=row['midpoint_price'],
                    strike=row['strike'],
                    underlying_price=row['underlying_last'],
                    leverage=config.leverage
                    ), 
                axis=1
            )
        
        # Filter out trades that would exceed margin limits
        valid_signals = signals[signals['margin_required'] <= max_marg_use]
        filtered_count = len(signals) - len(valid_signals)
        if filtered_count > 0:
            logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
            logger.info(f"Average margin requirement for filtered trades: ${signals['margin_required'].mean():.2f}")
            logger.info(f"Maximum margin requirement for filtered trades: ${signals['margin_required'].max():.2f}")
        logger.info(f"Total valid signals: {len(valid_signals)}")

        # Execute trades
        backtest_start = time.time()
        trade_results = trade_manager.construct_and_execute_trades_from_signals(signals)
        self.execution_times['backtest_execution'] = time.time() - backtest_start
        
        if trade_results.empty:
            logger.warning("No trades were executed successfully")
            return pd.DataFrame()
            
        # Save results if requested
        if self.save_trades:
            save_start = time.time()
            self._save_results(
                trade_results=trade_results,
                param_str=self._generate_param_string(
                    spread_type=config.spread_type,
                    option_type=config.option_type,
                    position_side=config.position_side,
                    delta_target=config.delta_target,
                    delta_range=config.delta_range,
                    dte_target=config.dte_target,
                    dte_range=config.dte_range,
                    start_date=config.start_date,
                    end_date=config.end_date
                )
            )
            self.execution_times['saving'] = time.time() - save_start
            
        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        
        return trade_results
    
    def run_multiple_backtests(
        self,
        configs: List[Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]]
  
    ) -> Dict:
        """Run multiple backtests with different parameters using the same data."""
  
 
        
        for i, config in enumerate(configs, 1):

            logger.info(f"Running backtest {i}/{len(configs)} ({i / len(config):.0%})")            
            start_time = time.time()
            # Prepare parameters for this backtest
            # params = prepare_backtest_params(
            #     params=params,
            #     spx_file_path=spx_file_path,
            #     options_chain_file_path=options_chain_file_path,
            #     options_chain=options_chain,
            #     spx_data=spx_data,
            #     preloaded_data=preloaded_data
            # )
            # Run backtest
            results = self.run_backtest(config)
            execution_time = time.time() - start_time
            
        #     results[f"backtest_{i}"] = {
        #         'params': params,
        #         'results': results,
        #         'execution_time': execution_time
        #     }
            
        #     logger.info(f"Backtest {i} completed in {execution_time:.2f} seconds")
        
        # total_time = time.time() - start_time     
        # logger.info(f"\nAll backtests completed in {total_time:.2f} seconds")
        # logger.info(f"Average time per backtest: {total_time/len(hyperparameter_sets):.2f} seconds")
        
        # return results
    

    
    
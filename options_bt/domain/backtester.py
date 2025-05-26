from typing import Dict, List, Optional, Tuple, Union
import numpy as np
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
        
        if signals.empty:
            logger.warning("No valid signals generated")
            return pd.DataFrame()

        # Pre-calculate margin requirements for all signals
        logger.info(f"Calculating margin requirements for trade signals for {config.quantity} | {config.leg.option_type if config.leg.option_type else config.spread_type} | {config.leg.delta_target if config.leg.delta_target else config.leg.delta_range}")
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)
        max_allowed_margin = config.max_margin_utilization * config.initial_capital * config.leverage
        logger.info(f"Maximum allowed margin: ${max_allowed_margin:.2f} ({config.max_margin_utilization:.0%} of capital with {config.leverage}x leverage)")
        logger.info(f"Maximum simultaneous positions: {config.max_positions}")
        
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
        valid_signals = signals[signals['margin_required'] <= max_allowed_margin]
        self.execution_times['signal_generation'] = time.time() - signal_start

        filtered_count = len(signals) - len(valid_signals)
        if filtered_count > 0:
            logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
            logger.info(f"Average margin requirement for filtered trades: ${signals['margin_required'].mean():.2f}")
            logger.info(f"Maximum margin requirement for filtered trades: ${signals['margin_required'].max():.2f}")
        logger.info(f"Total valid signals: {len(valid_signals)}")

        # Execute trades
        backtest_start = time.time()
        results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(signals, 
                                                                                option_chain=self.option_chain, 
                                                                                underlying_price_history=self.underlying)
        self.execution_times['backtest_execution'] = time.time() - backtest_start
        trade_results = results_transactions_dict['trade_results']
        transactions = results_transactions_dict['transactions']
        if trade_results.empty:
            logger.warning("No trades were executed successfully")
            return pd.DataFrame()
        
        # Calculate margin utilization
        trade_results['margin_utilization'] = round(trade_results['capital_used'] / config.initial_capital, 2)
        trade_results['avg_margin_util'] = round(trade_results['margin_utilization'].mean(), 2)
        trade_results['max_margin_util'] = round(trade_results['margin_utilization'].max(), 2       )
        logger.info(f"Average margin utilization: {trade_results['avg_margin_util'].iloc[0]:.2%}")
        logger.info(f"Maximum margin utilization: {trade_results['max_margin_util'].iloc[0]:.2%}")
        # Calculate cumulative metrics based on PnL
        trade_results['cumulative_pnl'] = round(trade_results['pnl'].cumsum(), 2)
        trade_results['capital'] = round(config.initial_capital + trade_results['cumulative_pnl'], 2)  # Track actual capital based on cumulative PnL
        trade_results['peak_capital'] = round(trade_results['capital'].cummax(), 2)
        
        # Calculate Sharpe Ratio without risk-free rate
        sharpe = None
        if len(trade_results) > 1:
            returns = np.diff(trade_results['capital'].values) / trade_results['capital'].values[:-1]
            if len(returns) > 0 and np.std(returns) > 0:
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
                logger.info(f"Sharpe Ratio: {sharpe:.2f}")

        # Save results if requested
        # Generate parameter string based on backtest type
        if is_spread:
            param_str = f"{config.spread_type.value}_spread_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"
        else:
            param_str = f"{config.leg.option_type.value}_{config.leg.position_side.value}_{f'{config.leg.delta_range[0]}:{config.leg.delta_range[1]}' if config.leg.delta_range else config.leg.delta_target}_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"
        
        if self.save_trades:
            save_start = time.time()
            self._save_results(
                config=config,
                trade_results=trade_results,
                transactions=transactions,
                param_str=param_str
            )
            self.execution_times['saving'] = time.time() - save_start
            
        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        
        # NB: cumulative_pnl is the sum of realized profits/losses across all closed trades.
        # It starts from initial_capital and accumulates only closed P&L (not unrealized)
        # Thus (option_bp) matches the analytical P&L (cumulative_pnl + initial_capital):
        assert abs(trade_results['capital'].iloc[-1] - trade_results['bp'].iloc[-1]) < 1e-6, f'Final capital: {trade_results["capital"].iloc[-1]} | BP: {trade_results["bp"].iloc[-1]}'

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
    

    
    def _save_results(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig], trade_results: pd.DataFrame, transactions: pd.DataFrame=None, mtm_df: pd.DataFrame=None, param_str: str="default"):
        """Save trade results and transactions to a CSV file."""

        # Save trades
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir = 'results'
        os.makedirs(results_dir, exist_ok=True)
        trades_csv_path = os.path.join(results_dir, f"trades_{param_str}_{timestamp}.csv")
        trade_results.to_csv(trades_csv_path, index=False)      

        # Save transactions if available
        if transactions is not None and not transactions.empty:
            transactions_csv_path = os.path.join(results_dir, f"transactions_{param_str}_{timestamp}.csv")
            transactions.to_csv(transactions_csv_path, index=False)

        # Save MTM results with same timestamp
        if mtm_df is not None and not mtm_df.empty:
            mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}_{timestamp}.csv")
            mtm_df.to_csv(mtm_csv_path, index=False)

        # Create results file for logging summary
        results_file_path = os.path.join(results_dir, f"backtest_summary_{param_str}_{timestamp}.txt")
        with open(results_file_path, 'w') as results_file:
            results_file.write("Backtest Results Summary:\n")
            results_file.write(f"Total trades executed: {len(trade_results)}\n")
            results_file.write(f"Winning trades: {(trade_results['pnl'] > 0).sum()}\n")
            results_file.write(f"Win rate: {((trade_results['pnl'] > 0).sum() / len(trade_results)):.2%}\n")
            results_file.write(f"Total P&L: ${trade_results['cumulative_pnl'].iloc[-1]:,.2f}\n")
            results_file.write(f"Final capital: ${trade_results['capital'].iloc[-1]:,.2f}\n")
            results_file.write(f"Return on initial capital: {(trade_results['capital'].iloc[-1] / config.initial_capital - 1):.2%}\n")
            results_file.write(f"Average days held: {trade_results['days_held'].mean():.1f}\n")
            results_file.write(f"Average return on margin: {trade_results['return_on_margin'].mean():.2f}%\n")
            if mtm_df is not None and not mtm_df.empty:
                results_file.write(f"Maximum drawdown: ${mtm_df['max_drawdown'].iloc[-1]:,.2f} ({mtm_df['max_drawdown_pct'].iloc[-1]:.2f}%)\n")
        
    def _log_execution_summary(self, total_time: float) -> None:
        """
        Log a summary of execution times for different parts of the backtest.
        
        Args:
            total_time: Total execution time of the backtest in seconds
        """
        logger.info("\nExecution Time Summary:")
        logger.info("-" * 30)
        
        # Log individual components
        if 'signal_generation' in self.execution_times:
            logger.info(f"Signal Generation: {self.execution_times['signal_generation']:.2f} seconds")
        
        if 'backtest_execution' in self.execution_times:
            logger.info(f"Backtest Execution: {self.execution_times['backtest_execution']:.2f} seconds")
        
        if 'saving' in self.execution_times:
            logger.info(f"Saving Results: {self.execution_times['saving']:.2f} seconds")
        
        # Calculate other time (time not accounted for in tracked components)
        tracked_time = sum(self.execution_times.values())
        other_time = total_time - tracked_time
        
        logger.info(f"Other Operations: {other_time:.2f} seconds")
        logger.info("-" * 30)
        logger.info(f"Total Time: {total_time:.2f} seconds")

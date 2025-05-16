from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import logging
import time
from datetime import datetime
import os

from options_bt.domain.enums import OptionType, PositionSide, SpreadType 
from options_bt.domain.option_position import OptionPosition
from options_bt.domain.spread import Spread
from options_bt.domain.option_trade import OptionTrade
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.trade_manager import TradeManager
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
        # self.execution_times = {}

        # Results (clear between runs?)
        # self.results: Dict[str, pd.DataFrame] = {}

    def run_backtest(
        self,
        *,
        quantity: int = 1,
        option_type: OptionType = None,
        position_side: PositionSide = None,
        delta_target: float = None,  # Optional target for delta
        delta_range: tuple = None,  # Range for delta
        dte_target: int = None,  # Optional target for days to expiration
        dte_range: tuple = None,  # Range for days to expiration
        use_spx_close: bool = False,
        start_date: str = None,
        end_date: str = None,
        initial_capital: float = 100000,
        early_close_days: int = None,
        max_margin_utilization: float = 0.80,
        leverage: float = 1.0,
        max_positions: int = 1,
        # Spread-specific parameters
        spread_type: SpreadType = None,
        legs_config: List[Dict] = None,
        spread_signals: pd.DataFrame = None,  # Pre-generated spread signals
        trade_signals: pd.DataFrame = None,   # Pre-generated trade signals for single legs
    ) -> pd.DataFrame:
        """Execute a backtest with the given parameters."""
        start_time = time.time()
        logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
     
        # Initialize trade manager
        trade_manager = TradeManager(initial_capital=config['initial_capital'], leverage=config['leverage'], max_margin_utilization=config['max_margin_utilization'])
        signal_generator = OptionSignalGenerator(**config)    
        # Generate or validate signals
        signal_start = time.time()
        if config['spread_type']:
            signals = self._prepare_spread_signals(
                spread_type=spread_type,
                legs_config=legs_config,
                spread_signals=spread_signals,
                option_chain=self.option_chain,
                start_date=start_date,
                end_date=end_date,
                dte_range=dte_range,
                dte_target=dte_target,
                spx_data=self.underlying
            )
        else:
            # signals = self._prepare_trade_signals(
            #     trade_signals=trade_signals,
            #     option_chain=self.option_chain,
            #     spx_data=self.underlying,
            #     option_type=option_type,
            #     delta_target=delta_target,
            #     delta_range=delta_range,
            #     dte_target=dte_target,
            #     dte_range=dte_range,
            #     start_date=start_date,
            #     end_date=end_date
            # )
            option
        self.execution_times['signal_generation'] = time.time() - signal_start
        
        if signals.empty:
            logger.warning("No valid signals generated")
            return pd.DataFrame()
            
        # Execute trades
        backtest_start = time.time()
        trade_results = self._execute_backtest(
            signals=signals,
            option_chain=self.option_chain,
            spx_data=self.underlying,
            option_type=option_type,
            position_side=position_side,
            early_close_days=early_close_days,
            delta_target=delta_target,
            delta_range=delta_range,
            quantity=quantity
        )
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
                    spread_type=spread_type,
                    option_type=option_type,
                    position_side=position_side,
                    delta_target=delta_target,
                    delta_range=delta_range,
                    dte_target=dte_target,
                    dte_range=dte_range,
                    start_date=start_date,
                    end_date=end_date
                )
            )
            self.execution_times['saving'] = time.time() - save_start
            
        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        
        return trade_results
    
    def run_multiple_backtests(
        self,
        hyperparameter_sets: List[Dict]
  
    ) -> Dict:
        """Run multiple backtests with different parameters using the same data."""
  
 
        
        for i, params in enumerate(hyperparameter_sets, 1):

            logger.info(f"Running backtest {i}/{len(hyperparameter_sets)} ({i / len(hyperparameter_sets):.0%})")            
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
            results = self.run_backtest(**params)
            execution_time = time.time() - start_time
            
            results[f"backtest_{i}"] = {
                'params': params,
                'results': results,
                'execution_time': execution_time
            }
            
            logger.info(f"Backtest {i} completed in {execution_time:.2f} seconds")
        
        total_time = time.time() - start_time     
        logger.info(f"\nAll backtests completed in {total_time:.2f} seconds")
        logger.info(f"Average time per backtest: {total_time/len(hyperparameter_sets):.2f} seconds")
        
        return results
    

 
    
    def _execute_backtest(self, signals: pd.DataFrame, **kwargs):
        """Execute the backtest using the trade manager."""
        # Implementation of backtest execution here
        pass
    
    def _save_results(self, trade_results: pd.DataFrame, param_str: str):
        """Save backtest results."""
        # Implementation of results saving here
        pass
    
    def _generate_param_string(self, **kwargs) -> str:
        """Generate parameter string for file naming."""
        # Implementation of parameter string generation here
        pass
    
    def _log_execution_summary(self, total_time: float):
        """Log execution time summary."""
        logger.info(f"\nTotal execution time: {total_time:.2f} seconds")
        logger.info(f"Breakdown:")
        for phase, time_taken in self.execution_times.items():
            percentage = time_taken/total_time*100
            logger.info(f"- {phase}: {time_taken:.2f} seconds ({percentage:.1f}%)") 

    def generate_param_template(self) -> Dict:
        return {
            "option_type": OptionType.PUT,
            "position_side": PositionSide.SHORT,
            "delta_target": 0.30,
            "dte_target": 30,
            "quantity": 1,
            "early_close_days": 5,
    }

    def _prepare_backtest_params(
        self,
        params: Dict,
   
        preloaded_data: Dict
    ) -> Dict:
        """
        Prepare the appropriate parameters for run_backtest based on whether 
        this is a spread or single-leg backtest.
        
        Args:
            params: Dictionary of backtest parameters
            
        Returns:
            Dictionary of parameters to pass to run_backtest
        """
        # Check if this is a spread backtest
        is_spread = 'spread_type' in params and 'legs_config' in params
        
        # Common parameters that apply to both types
        backtest_params = {
            'dte_range': params.get('dte_range'),
            'dte_target': params.get('dte_target'),
            'start_date': params.get('start_date'),
            'end_date': params.get('end_date'),
            'quantity': params.get('quantity', 1),
        }
        
        # Add specific parameters based on backtest type
        if is_spread:
            # Generate spread signals
            spread_signals = self._generate_spread_signals(
                spread_type=params['spread_type'],
                legs_config=params['legs_config'],
                start_date=params.get('start_date'),
                end_date=params.get('end_date'),
                dte_range=params.get('dte_range'),
                dte_target=params.get('dte_target'),
                spx_data=self.underlying
            )
            
            # Add spread-specific parameters
            backtest_params.update({
                'spread_signals': spread_signals,
                'spread_type': params['spread_type'],
                'legs_config': params['legs_config'],
            })
        else:
            # Generate single-leg trade signals
            trade_signals = self._generate_trade_signals(
                spx_data=self.underlying,
                option_chain=self.option_chain,
                option_type=params['option_type'],
                delta_target=params.get('delta_target'),
                delta_range=params.get('delta_range'),
                dte_target=params.get('dte_target'),
                dte_range=params.get('dte_range'),
                start_date=params.get('start_date'),
                end_date=params.get('end_date')
            )
            
            # Add single-leg specific parameters
            backtest_params.update({
                'option_type': params['option_type'],
                'position_side': params['position_side'],
                'delta_target': params.get('delta_target'),
                'delta_range': params.get('delta_range'),
                'trade_signals': trade_signals  # Add generated signals
            })
        
        # Add any remaining parameters from the original params
        for k, v in params.items():
            if k not in backtest_params:
                backtest_params[k] = v
                
        return backtest_params

from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import logging
import time
from datetime import datetime
import os

from options_bt.domain.enums import OptionType, PositionSide, SpreadType, TradeResult
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.position import Position
from options_bt.domain.spread import Spread

logger = logging.getLogger(__name__)

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
        self.data = data
        self.save_trades = save_trades
        self.log_to_sheets = log_to_sheets

        # Track execution times
        self.execution_times = {}

        # Results (clear between runs?)
        self.results: Dict[str, pd.DataFrame] = {}

    def run_backtest(
        self,
        *,

        # Hyperparameters
        quantity: int = 1,
        option_type: OptionType,
        position_side: PositionSide = None,
        delta_target: Optional[float] = None,
        delta_range: Optional[Tuple[float, float]] = None,
        dte_target: Optional[int] = None,
        dte_range: Optional[Tuple[int, int]] = None,
        use_spx_close: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        early_close_days: Optional[int] = None,
        max_positions: Optional[int] = 1,
        # Spread-specific parameters
        spread_type: Optional[SpreadType] = None,
        legs_config: Optional[List[Dict]] = None,
        spread_signals: Optional[pd.DataFrame] = None,
        trade_signals: Optional[pd.DataFrame] = None,
        # Capital parameters
        initial_capital: float = 100000.0,
        leverage: float = 1.0,
        # Data parameters (default preloaded)
        data: Optional[Dict] = None
    ) -> pd.DataFrame:
        """Execute a backtest with the given parameters."""
        start_time = time.time()
        logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
         
        options_chain = self.data['options_data']
        options_chain_multi_index = self.data['options_data_multi']
        spx_data = self.data['spx_data']
        vix_data = self.data['vix_data']
     
        # Initialize trade manager
        self.trade_manager = TradeManager(initial_capital=initial_capital, leverage=leverage)
        
        # Generate or validate signals
        signal_start = time.time()
        if spread_type:
            signals = self._prepare_spread_signals(
                spread_type=spread_type,
                legs_config=legs_config,
                spread_signals=spread_signals,
                options_chain=options_chain,
                start_date=start_date,
                end_date=end_date,
                dte_range=dte_range,
                dte_target=dte_target,
                spx_data=spx_data
            )
        else:
            signals = self._prepare_trade_signals(
                trade_signals=trade_signals,
                options_chain=options_chain,
                spx_data=spx_data,
                option_type=option_type,
                delta_target=delta_target,
                delta_range=delta_range,
                dte_target=dte_target,
                dte_range=dte_range,
                start_date=start_date,
                end_date=end_date
            )
        
        self.execution_times['signal_generation'] = time.time() - signal_start
        
        if signals.empty:
            logger.warning("No valid signals generated")
            return pd.DataFrame()
            
        # Execute trades
        backtest_start = time.time()
        trade_results = self._execute_backtest(
            signals=signals,
            options_chain=options_chain,
            spx_data=spx_data,
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
            
            # Run backtest
            results = self.run_backtest(**params)
            execution_time = time.time() - start_time
            
            results[f"backtest_{i}"] = {
                'params': params,
                'results': result,
                'execution_time': execution_time
            }
            
            logger.info(f"Backtest {i} completed in {execution_time:.2f} seconds")
        
        total_time = time.time() - start_time     
        logger.info(f"\nAll backtests completed in {total_time:.2f} seconds")
        logger.info(f"Average time per backtest: {total_time/len(hyperparameter_sets):.2f} seconds")
        
        return results
    

    def _prepare_spread_signals(self, spread_type: SpreadType, legs_config: List[Dict],
                              spread_signals: Optional[pd.DataFrame], **kwargs):
        """Prepare spread signals for backtest."""
        # Implementation of spread signal preparation here
        pass
    
    def _prepare_trade_signals(self, trade_signals: Optional[pd.DataFrame], **kwargs):
        """Prepare trade signals for backtest."""
        # Implementation of trade signal preparation here
        pass
    
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
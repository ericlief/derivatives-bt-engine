from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import logging
import time
from datetime import datetime
import os
from enum import Enum

from options_bt.domain.enums import *
from options_bt.domain.single_leg_option_strategy_config import SingleLegOptionStrategyConfig
from options_bt.domain.multi_leg_option_strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.option_position import OptionPosition     
from options_bt.domain.option_trade import OptionTrade
from options_bt.domain.spread import Spread
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

class PositionSide(str, Enum):
    """Position side enumeration."""
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

    @staticmethod
    def is_long(value: Union['PositionSide', str]) -> bool:
        """
        Check if the value represents a LONG position.
        
        Args:
            value: Can be PositionSide enum or string
            
        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, str):
            return value.lower() == "long"
        return value == PositionSide.LONG

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
        trade_manager = TradeManager(
            initial_capital=config.initial_capital, 
            leverage=config.leverage, 
            max_margin_utilization=config.max_margin_utilization,
            max_positions=config.max_positions,
            early_close_days=config.early_close_days,
            use_underlying_close=config.use_underlying_close
        )
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
            
        # Execute trades
        backtest_start = time.time()
        trade_results = self._execute_backtest(
            signals=signals,
            option_chain=self.option_chain,
            spx_data=self.underlying,
            option_type=config.leg.option_type,
            position_side=config.leg.position_side,
            early_close_days=config.early_close_days,
            delta_target=config.leg.delta_target,
            delta_range=config.leg.delta_range,
            quantity=config.quantity
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
    
@staticmethod
def calculate_margin(underlying_price: float, entry_price: float, 
                           position_side: Union[PositionSide, str],
                           strike: float,
                           option_type: Union[OptionType, str],
                           margin_req_percent: float = 0.15) -> float:
    """
    Calculate required margin for option position using IB's formula for Index Options.
    
    Args:
        underlying_price (float): Current price of the underlying asset.
        entry_price (float): Option premium, which is the mid of the bid and ask prices.
        position_side (Union[PositionSide, str]): Indicates whether the position is LONG or SHORT.
        strike (float): The strike price of the option.
        option_type (Union[OptionType, str]): The type of the option, which can be PUT or CALL.
        margin_req_percent (float, optional): The margin requirement percentage. Defaults to 0.15, which is the value for Interactive Brokers.
    
    Returns:
        float: The required margin in dollars.
    """
    # Convert string to enum if needed
    # if isinstance(position_side, str):
    #     position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
    
    # For long positions, margin is just the cost of the option
    # There is no margin req for Long positions
    if PositionSide.is_long(position_side):
        # return round(entry_price * 100, 2)  # Convert to dollars
        return 0
    
    # For short positions, use IB's formula for Index Options
    else:  # PositionSide.SHORT
        # Calculate out-of-the-money amount
        if OptionType.is_put(option_type): 
            # For puts: OTM when strike > underlying, ITM when strike <= underlying
            otm_amount = max(0, underlying_price - strike)
        else:  # CALL
            # For calls: OTM when strike >= underlying, ITM when strike < underlying
            otm_amount = max(0, strike - underlying_price)
        
        # IB's margin formula for Index Options
        margin_required = (
            entry_price +  # Option price
            max(
                # First term: 15% of underlying price minus OTM amount
                (margin_req_percent * underlying_price - otm_amount),
                # Second term: 10% of underlying price
                (0.10 * underlying_price)
            )
        ) * 100  # Convert to dollars

        return round(margin_required, 2)
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
import pandas as pd
import logging
from options_bt.domain.enums import *
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig

logger = setup_logger()

class TradeManager:
    """Class to manage trade creation and execution."""
    
    def __init__(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]):
        self.config = config
        self.initial_capital = config.initial_capital
        self.leverage = config.leverage
        self.option_bp = config.initial_capital
        self.max_margin_utilization = config.max_margin_utilization
        self.max_positions = config.max_positions
        self.trade_counter = 0
        self.open_positions: List[Union[SingleLegOptionPosition, MultiLegOptionPosition]] = []
    
    def execute_trade(self, trade: Union[SingleLegOptionPosition, MultiLegOptionPosition]) -> Optional[SingleLegOptionPosition]:
        """
        Execute a trade with the current buying power and leverage, updating the option buying power state (option_bp)
        
        Args:
            trade: Position to execute
            
        Returns:
            Executed trade if successful, None otherwise
        """
        # if trade is None:
        #     return None, self.option_bp

        # Use spread price for spreads, individual leg price for single legs
        if isinstance(trade, MultiLegOptionPosition) and trade.spread_type != OptionSpreadType.NONE.value:
            if pd.isna(trade.spread_price):
                logger.error(f"Missing spread_price for spread {trade.spread_id} leg {trade.leg_number}")
                return None
            premium = abs(trade.spread_price) * 100 * trade.quantity
        else:
            premium = abs(trade.entry_price) * 100 * trade.quantity

        # Calculate effective margin requirement with leverage
        effective_margin = trade.margin_required  
        if effective_margin is None or effective_margin <= 0:
            logger.error(f"Invalid margin requirement for trade on {trade.entry_date}")
            return None 
        
        # Open LONG position
        if trade.is_long:
            # Check if enough buying power to buy the option
            if self.option_bp >= premium:
                self.option_bp -= premium  # Deduct premium
                return trade 
            else:
                logger.warning(f"Insufficient buying power (${self.option_bp}) to buy option. Required: ${premium:.2f}")
                return None 

        # Open SHORT position
        elif trade.is_short:
            # Check if enough buying power for margin
            if self.option_bp >= effective_margin:
                self.option_bp += premium  # Credit premium
                self.option_bp -= effective_margin  # Reserve margin
                return trade
            else:
                logger.warning(f"Insufficient buying power (${self.option_bp}) to sell option. Required margin: ${effective_margin:.2f}")
                return None

        return None
    
    def construct_and_execute_trades_from_signals(self,
                         trade_signals: pd.DataFrame,
                        ) -> pd.DataFrame:
        """
        Construct and execute trades based on signals.
        
        Args:
            trade_signals: DataFrame containing trade signals
            config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
        Returns:
            DataFrame containing trade results
        """
        # Initialize variables
        trade_counter = 0
        option_bp = self.option_bp
        open_positions = []
        trade_results = []
        skipped_trades = 0
        
        # Check if we're dealing with spreads
        # is_spread = 'spread_type' in trade_signals.columns and trade_signals['spread_type'].iloc[0] != SpreadType.NONE
        
        for trade_signal in trade_signals.itertuples():
            current_date = trade_signal.Index
            
            # First, check if any open positions need to be closed
            positions_to_remove = []
            for pos in open_positions:

                # Close position if we're on/past the close_date or expire_date
                if ((pos.close_date is not None and current_date >= pos.close_date) or
                    (pos.expire_date is not None and current_date >= pos.expire_date)):
                    
                    logger.debug(f'Closing position: {pos}')
                    result = pos.close_position(option_chain=self.option_chain, underlying_price_history=self.underlying, option_bp=option_bp)
                    if result:
                        option_bp = result.bp
                        positions_to_remove.append(pos)
                        logger.debug(f"Closed position - BP: ${option_bp:.2f}")
                        trade_results.append(result)
        
            # Remove closed positions
            for pos in positions_to_remove:
                open_positions.remove(pos)
        
            # Skip if we've reached max positions
            if len(open_positions) >= self.max_positions:
                skipped_trades += 1
                continue

            # Create new trade from signal
            if isinstance(self.config, SingleLegOptionStrategyConfig):
                candidate_position = SingleLegOptionPosition.construct_from_signal(
                    trade_signal=trade_signal, 
                    entry_date=current_date, 
                    position_side=self.config.leg.position_side, 
                    option_type=self.config.leg.option_type, 
                    quantity=self.config.quantity, 
                    early_close_days=self.config.early_close_days
                )
            else:
                candidate_position = MultiLegOptionPosition.construct_from_signal(
                    trade_signal=trade_signal, 
                    entry_date=current_date, 
                    position_side=self.config.leg.position_side, 
                    option_type=self.config.leg.option_type, 
                    quantity=self.config.quantity, 
                    early_close_days=self.config.early_close_days   
                )   

            # Try to execute the new trade if it was created successfully
            if candidate_position is not None:
                executed_trade = self.execute_trade(candidate_position)
                if executed_trade:
                    executed_trade.trade_id = trade_counter
                    open_positions.append(executed_trade)
                    trade_counter += 1  # Increment counter only for successful trades
                    logger.debug(f'Opened position: {executed_trade}')
                    logger.debug(f'BP: ${option_bp:.2f}')
                else:
                    skipped_trades += 1
            else:
                logger.debug("Skipping trade - invalid signal")
                skipped_trades += 1

        # Close any remaining open positions at their expiration
        for pos in open_positions:
            result = pos.close(pos, full_chain_df, underlying_price_history, option_bp)
            if result:
                trade_results.append(result)
                option_bp = result['option_bp']

        if not trade_results:
            logger.warning("No trades were executed successfully")
            return pd.DataFrame()

        results_df = pd.DataFrame(trade_results)
        













        # if is_spread:
        #     # For spreads, we already have positions with dates
        #     # Group positions by spread_id and date for processing in chronological order
        #     spread_groups = trades.groupby(['spread_id', 'entry_date'])
            
        #     # Sort groups by date
        #     sorted_spreads = sorted(spread_groups, key=lambda x: x[0][1])
        #     logger.debug(f'Sorted spreads {sorted_spreads}')
            
        #     # Process each spread's positions
        #     for (spread_id, current_date), group in sorted_spreads:
        #         # First, check if any open positions need to be closed
        #         positions_to_remove = []
        #         for pos in open_positions:
        #             # Close position if we're on/past the close_date or expire_date
        #             if (('close_date' in pos and pos['close_date'] is not None and current_date >= pos['close_date']) or
        #                 ('expire_date' in pos and pos['expire_date'] is not None and current_date >= pos['expire_date'])):
                        
        #                 logger.debug(f'Closing position: {pos}')
        #                 result = close_position(pos, full_chain_df, underlying_price_history, option_bp)
        #                 if result:
        #                     option_bp = result['option_bp']
        #                     positions_to_remove.append(pos)
        #                     logger.debug(f"Closed position - BP: ${option_bp:.2f}")
        #                     trade_results.append(result)
                
        #         # Remove closed positions
        #         for pos in positions_to_remove:
        #             open_positions.remove(pos)
                
        #         # Skip if we've reached max positions
        #         if len(open_positions) >= max_positions:
        #             skipped_trades += 1
        #             continue
                
        #         # Execute all legs of the spread together
        #         spread_executed = True
        #         spread_positions = []
                
        #         # First leg will check BP for the entire spread
        #         first_leg = True
        #         for position in group.itertuples():
        #             position_dict = position._asdict()
        #             position_dict['trade_id'] = trade_counter
        #             if first_leg:
        #                 # First leg checks BP for entire spread
        #                 executed_position, option_bp = execute_trade(position_dict, option_bp, leverage)
        #                 first_leg = False
        #             else:
        #                 # Other legs don't affect BP
        #                 executed_position = position_dict.copy()
                    
        #             if executed_position:
        #                 spread_positions.append(executed_position)
        #                 logger.debug(f'Prepared spread leg: {executed_position}')
        #             else:
        #                 spread_executed = False
        #                 break
                
        #         if spread_executed:
        #             open_positions.extend(spread_positions)
        #             trade_counter += 1
        #             logger.debug(f'BP: ${option_bp:.2f}')
        #         else:
        #             skipped_trades += 1
        # else:
        #     # Traditional single-leg processing
        #     trades = trades.sort_index()
            
        #     for i, trade_signal in trades.iterrows():
        #         current_date = trade_signal.name
                
        #         logger.debug(f'Trade signal {i}, {trade_signal.name}, Delta {trade_signal.p_delta}')

        #         # First, check if any open positions need to be closed
        #         positions_to_remove = []
        #         for pos in open_positions:
        #             # Close position if we're on/past the close_date or expire_date
        #             if (('close_date' in pos and pos['close_date'] is not None and current_date >= pos['close_date']) or
        #                 ('expire_date' in pos and pos['expire_date'] is not None and current_date >= pos['expire_date'])):
                        
        #                 logger.debug(f'Closing position: {pos}')
        #                 result = close_position(pos, full_chain_df, underlying_price_history, option_bp)
        #                 if result:
        #                     option_bp = result['option_bp']
        #                     positions_to_remove.append(pos)
        #                     logger.debug(f"Closed position - BP: ${option_bp:.2f}")
        #                     trade_results.append(result)
                
        #         # Remove closed positions
        #         for pos in positions_to_remove:
        #             open_positions.remove(pos)
                
        #         # Skip if we've reached max positions
        #         if len(open_positions) >= max_positions:
        #             skipped_trades += 1
        #             continue
                
        #         # Create new trade from signal
        #         new_trade = create_trade_from_signal(trade_signal, quantity, option_type, position_side, delta_target, current_date, early_close_days, delta_range)
                
        #         # Try to execute the new trade
        #         executed_trade, option_bp = execute_trade(new_trade, option_bp, leverage)
        #         if executed_trade:
        #             executed_trade['trade_id'] = trade_counter
        #             open_positions.append(executed_trade)
        #             trade_counter += 1  # Increment counter only for successful trades
        #             logger.debug(f'Opened position: {executed_trade}')
        #             logger.debug(f'BP: ${option_bp:.2f}')
        #         else:
        #             skipped_trades += 1
        
        # # Close any remaining open positions at their expiration
        # for pos in open_positions:
        #     result = close_position(pos, full_chain_df, underlying_price_history, option_bp)
        #     if result:
        #         trade_results.append(result)
        #         option_bp = result['option_bp']
        
        # if not trade_results:
        #     logger.warning("No trades were executed successfully")
        #     return pd.DataFrame()
        
        # results_df = pd.DataFrame(trade_results)
        
        # Calculate cumulative metrics based on PnL
        # results_df['cumulative_pnl'] = results_df['pnl'].cumsum()
        # results_df['capital'] = initial_capital  + results_df['cumulative_pnl']  # Track actual capital based on cumulative PnL
        # results_df['peak_capital'] = results_df['capital'].cummax()
        
        return results_df   
    
    # def _execute_backtest(self, signals: pd.DataFrame, **kwargs):
    #     """Execute the backtest using the trade manager."""
    #     # Implementation of backtest execution here
    #     pass
    
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
        
 

    
    # @staticmethod   
    # def calculate_intrinsic_value(underlying_price: float, strike: float, option_type: Union[OptionType, str]) -> float:
    #     """
    #     Calculates the intrinsic value of an option.
        
    #     Args:
    #         underlying_price (float): The current price of the underlying asset.
    #         strike (float): The strike price of the option.
    #         option_type (Union[OptionType, str]): The type of option, either PUT or CALL.
        
    #     Returns:
    #         float: The intrinsic value of the option.
    #     """

    #     # is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    #     # if isinstance(option_type, str):
    #     #     is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    #     # else:
    #     #     is_put = option_type == OptionType.PUT
        
    #     logger.debug(f'Expiration. Calculating intrinsic value for {option_type}, strike={strike}, underlying={underlying_price}')
    #     logger.debug(f'IV: {max(0, strike - underlying_price) if OptionType.is_put(option_type) else max(0, underlying_price - strike)}')

        
    #     if OptionType.is_put(option_type):
    #         return max(0, strike - underlying_price)
    #     else:  # CALL
    #         return max(0, underlying_price - strike)


    # def get_closing_data(
    #         self,
    #         position: SingleLegOptionPosition,
    #     ) -> Optional[SingleLegOptionPosition]:
    #     """
    #     Get closing price data for an option position.  
        
    #     Args:
    #         position OptionPosition: Position containing trade details.
        
    #     Returns:
    #         Optional[Position]: The updated position object with closing price and other relevant data, or None if no valid closing data is found.
    #     """
    #     expire_date = position.expire_date

    #     # If no close_date, this is an expiration.
    #     if not position.close_date:
    #         if expire_date not in self.underlying.index:
    #             logger.warning(f"No valid closing data found for position with expire date {expire_date}. Returning None.")
    #             return None  # Return None if no valid closing data is found
            
    #         # Get underlying (e.g. SPX) at close
    #         try:
    #             if 'close' in self.underlying:
    #                 underlying_close = self.underlying.loc[expire_date, 'close']
    #             elif 'Close' in self.underlying:
    #                 underlying_close = self.underlying.loc[expire_date, 'Close']
    #             else:
    #                 raise ValueError(f"No valid closing data found for position with expire date {expire_date}. Returning None.")
    #         except (KeyError, ValueError):
    #             logger.warning(f"No valid closing data found for position with expire date {expire_date}. Returning None.")
    #             return None  # Return None if no valid closing data is found
            
    #         position.underlying_exit = underlying_close
    #         position.exit_price = calculate_intrinsic_value(underlying_close, position.strike, position.option_type)
    #         position.exit_price = get_signed_exit_price(position)

    #         # Get delta value at expiration
    #         delta_col = "p_delta" if is_put(position) else 'c_delta'
    #         filtered_df = self.options_chain[
    #                 (self.options_chain.index == expire_date) &
    #                 (self.options_chain.expire_date == expire_date) &
    #                 (self.options_chain.strike == position.strike)
    #             ]
            
    #         if not filtered_df.empty:
    #             exit_delta = round(filtered_df[delta_col].iloc[0], 2)      
    #             position.exit_delta = exit_delta

    #         return position
        
    #     # Early close - get data from close_date forward (up to 5 days)
    #     close_date = position.close_date
    #     date_range = pd.date_range(close_date, close_date + pd.Timedelta(days=5))
        
    #     filtered_df = self.options_chain[
    #         (self.options_chain.index.isin(date_range)) & 
    #         (self.options_chain.expire_date == position.expire_date) &
    #         (self.options_chain.strike == position.strike)
    #     ].sort_index()  # Sort by date to try closest dates first
        
    #     if filtered_df.empty:
    #         logger.warning(f"No valid prices found within 5 days of close date {close_date}. Returning None.")
    #         return position  # Return unchanged position if no valid prices were found within 5 days
            
    #     bid_col = "p_bid" if is_put(position) else "c_bid"
    #     ask_col = "p_ask" if is_put(position) else "c_ask"
    #     delta_col = "p_delta" if is_put(position) else 'c_delta'

    #     # Try each date in the filtered data until we find valid prices
    #     for _, row in filtered_df.iterrows():
    #         bid = row[bid_col]
    #         ask = row[ask_col]
    #         underlying_close = row['underlying_last']
    #         position.underlying_exit = underlying_close
    #         position.exit_delta = round(row[delta_col], 2)
    #         mid_price = calculate_midpoint_price(bid, ask)
    #         if mid_price is not None:
    #             position.exit_price = mid_price
    #             position.exit_price = get_signed_exit_price(position)
    #             return position
        
    #     # If we get here, no valid prices were found within 5 days
    #     logger.error(f"No valid closing prices found for position with strike {position.strike} and expire date {position.expire_date}. Returning None.")
    #     return position  # Return None if no valid closing prices were
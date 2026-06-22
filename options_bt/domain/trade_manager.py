from typing import Optional, Dict, Union, List, NamedTuple, Tuple
import pandas as pd
import polars as pl
from options_bt.domain.enums import *
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition, FuturesPosition
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.utils.logger import setup_logger
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig

logger = setup_logger()

class TradeManager:
    """Class to manage trade creation and execution."""
    
    def __init__(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig], vix: Optional[pd.DataFrame] = None):
        self.config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig] = config
        self.initial_capital: float = config.initial_capital
        self.leverage: float = config.leverage
        self.bp: float = config.initial_capital
        self.max_margin_utilization: float = config.max_margin_utilization
        self.max_positions: int = config.max_positions
        self.trade_counter: int = 1
        self.transaction_counter: int = 1
        self.open_positions: List[Union[SingleLegOptionPosition, MultiLegOptionPosition]] = []
        self.vix: Optional[pd.DataFrame] = vix

        logger.info(f'TradeManager instantiated')
        logger.info(f'Init Cap: {self.initial_capital} | BP: {self.bp} | Trades: {self.trade_counter}')
        logger.info(f'Using vix range: {self.config.vix_range}')
        logger.info(f'VIX sample: {self.vix.head() if self.vix is not None else "N/A"}')

    def _execute_trade(self, position: Union[SingleLegOptionPosition, MultiLegOptionPosition]) -> Optional[Tuple[SingleLegOptionPosition, float]]:
        """
        Execute a trade with the current buying power and leverage, updating the option buying power state (option_bp)
        
        Args:
            position: Position to execute
            
        Returns:
            Executed position if successful, None otherwise, bp_effect
        """

        bp_effect = 0
        logger.debug(f'In `execute_trade` | BP {self.bp} | BP Effect {bp_effect}')
        logger.info(f'Init Cap: {self.initial_capital} | BP: {self.bp} | Trades: {self.trade_counter}')

        if isinstance(position, MultiLegOptionPosition) and position.spread_type != OptionSpreadType.NONE:
            if pd.isna(position.spread_price):
                logger.error(f"Missing spread_price for spread {position.spread_id} leg {position.leg_number}")
                return None, bp_effect


        # Validate margin requirement
        # if isinstance(self.config, SingleLegOptionStrategyConfig):
        #     effective_margin = position.margin_required / self.leverage    
        # else:
        #     effective_margin = None
        effective_margin = position.margin_required
        if effective_margin is None or effective_margin <= 0 and position.position_side == PositionSide.SHORT:
            logger.error(f"Null or invalid margin requirement for short position on {position.entry_date}")
            return None, bp_effect

        # Futures have no premium (no upfront cash leg) — margin is reserved
        # symmetrically for long and short, unlike options where only the
        # short side posts margin and the long side just pays a premium.
        if isinstance(position, FuturesPosition):
            if self.bp >= effective_margin:
                bp_effect -= effective_margin
                logger.debug(f'Reserved futures margin. BP: {self.bp} | BP Effect: {bp_effect}')
                return position, bp_effect
            else:
                logger.warning(f"Insufficient buying power (${self.bp}) for futures margin. Required: ${effective_margin:.2f}")
                return None, bp_effect

        # Retrieve absolute premium regardless of position type
        premium = abs(position.signed_premium)

        # Open LONG position
        if position.is_long:            # Check if enough buying power to buy the option
            if self.bp >= premium:
                logger.debug(f'BP: {self.bp}')
                # self.bp -= premium  # Deduct premium
                bp_effect -= premium
                logger.debug(f'Executing long trade. BP: {self.bp}| BP Effect: {bp_effect}')
                return position, bp_effect
            else:
                logger.warning(f"Insufficient buying power (${self.bp}) to buy option. Required: ${premium:.2f}")
                return None, bp_effect

        # Open SHORT position
        elif position.is_short:
            # Check if enough buying power for margin
            if self.bp >= effective_margin:
                logger.debug(f'BP: {self.bp}')
                bp_effect += premium  # Credit premium
                bp_effect -= effective_margin  # Reserve margin
                # self.bp += premium  # Credit premium
                # self.bp -= effective_margin  # Reserve margin
                logger.debug(f'After premium and margin update. BP: {self.bp} | BP Effect: {bp_effect}')
                logger.info(f'Init Cap: {self.initial_capital} | Trades: {self.trade_counter}')

                return position, bp_effect
            else:
                logger.warning(f"Insufficient buying power (${self.bp}) to sell option. Required margin: ${effective_margin:.2f}")
                return None, bp_effect

        return None, bp_effect
    

    def construct_and_execute_trades_from_signals(self,
                         trade_signals: pd.DataFrame,
                         option_chain: pd.DataFrame,
                         underlying_price_history: pd.DataFrame
                        ) -> Dict:  
        """
        Construct and execute trades based on signals.
        
        Args:
            trade_signals: DataFrame containing trade signals
            config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
        Returns:
            Dictionary containing trade results and transactions
        """
        # Initialize variables
        all_trade_results = []
        all_transactions = []
        skipped_trades = 0

        # Futures signals are polars-native (no spreads/legs, simpler date
        # handling); options signals stay on the existing pandas DatetimeIndex
        # path. Branch only at the few index/iteration touchpoints below —
        # the position construction/execution/closing logic further down is
        # already polymorphic across position types and needs no branching.
        is_futures = isinstance(self.config, FuturesStrategyConfig)

        if is_futures:
            if trade_signals is None or trade_signals.height == 0:
                return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}
            start = trade_signals['ts_event'].min()
            # Bound on underlying_price_history's max, not signals' max: the
            # signal generator drops the tail end of a backtest window when
            # no roll date falls strictly after those days (e.g. the last
            # ~2 weeks of a single-year run, since the *next* cycle's roll
            # date is out of range) — but an already-open position still
            # needs its daily close-check evaluated through the actual end
            # of the period, or it never rolls/closes naturally and instead
            # force-closes at the very end via close_all.
            end = underlying_price_history['ts_event'].max()
            dates = pl.date_range(start, end, interval='1d', eager=True).to_list()
        else:
            if trade_signals is None or trade_signals.empty:
                return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}
            start = trade_signals.index.min()
            end = trade_signals.index.max()
            dates = pd.date_range(start, end)

        # Iterate through all dates in backtest range first in order to manage trades, e.g. exit when certain
        # certain conditions are met (vix, etc.) 
        # Additionally we will open or close any positions if conditions are fulfilled
        for date in dates:
            current_date = date
            logger.debug(f'Processing date: {current_date}')

            if is_futures:
                match = trade_signals.filter(pl.col('ts_event') == current_date)
                trade_signal = match if match.height > 0 else None
            else:
                if current_date in trade_signals.index:
                    trade_signal = trade_signals.loc[[current_date]] # force to df
                else:
                    trade_signal = None

            # VIX gating (skip trade if outside range or missing)
            vix_close_value = None
            vix_early_closure = False

            if self.config.vix_range is not None or self.config.vix_max is not None:
                if is_futures:
                    if isinstance(self.vix, pl.DataFrame) and self.vix.height > 0 and 'ts_event' in self.vix.columns:
                        vix_match = self.vix.filter(pl.col('ts_event') == current_date)
                        if vix_match.height > 0:
                            try:
                                vix_close_value = float(vix_match['close'][0])
                            except Exception:
                                vix_close_value = None
                            logger.debug(f'VIX daily value {vix_close_value}')
                else:
                    if isinstance(self.vix, pd.DataFrame) and not self.vix.empty and current_date in self.vix.index:
                        row = self.vix.loc[current_date]
                        try:
                            vix_close_value = float(row['close'] if 'close' in row else float(row))
                        except Exception:
                            vix_close_value = None
                        logger.debug(f'VIX daily value {vix_close_value}')

                if vix_close_value is not None:
                    # Check vix_max for early exit
                    if self.config.vix_max is not None and vix_close_value > self.config.vix_max:
                        vix_early_closure = True
                        logger.debug(f'VIX {vix_close_value} exceeds max {self.config.vix_max}')

            # Close any expired positions
            n_open_positions = len(self.open_positions)
            if n_open_positions > 0:    
                if vix_early_closure:
                    logger.debug(f'VIX early closure for {n_open_positions} open positions')        
                    
                trade_results, transactions = self._close_expired_positions(
                    option_chain=option_chain, 
                    underlying_price_history=underlying_price_history,
                    current_date=current_date,
                    vix_early_closure=vix_early_closure  # Pass the boolean flag
                )
                # only aggregate results of close was successfull
                if trade_results is not None:
                    # Aggregate trade results and transactions
                    all_trade_results.extend(trade_results)
                    all_transactions.extend(transactions)

                else:
                    logger.error("Failed to close some trades")
            
            # Check vix_range for trade entry
            if vix_close_value is not None and self.config.vix_range is not None:
                lo, hi = self.config.vix_range
                if not (lo <= vix_close_value <= hi):
                    skipped_trades += 1
                    logger.debug(f'Skipping trade date {current_date} due to VIX {vix_close_value} outside range {lo}-{hi}')
                    continue

            # Construct a new position from the trade signal if possible on the current date
            if trade_signal is not None:
                row_iter = trade_signal.iter_rows(named=True) if is_futures else trade_signal.itertuples()
                for trade in row_iter:
                    # Skip if we've reached max open positions
                    if len(self.open_positions) >= self.max_positions:
                        skipped_trades += 1
                        logger.debug(f'Skipping trade date {current_date} due to max {self.max_positions} positions. Current positions: {len(self.open_positions)}')
                        break
                    
                    # Attempt trade 
                    candidate_position = self.construct_position_from_signal(trade, current_date=current_date)
                      # Try to execute the new trade if it was created successfully
                    if candidate_position is not None:
                        logger.debug(f'BP: ${self.bp:.2f}')
                        logger.debug(f'Executing trade: {candidate_position}')
                        
                        # Execute the trade and create transactions and trades
                        executed_trade, bp_effect = self._execute_trade(candidate_position)
                        if executed_trade is not None:
                            self.bp += bp_effect  # apply once for the spread
                            executed_trade.trade_id = self.trade_counter
                            logger.debug(f'Successfully executed trade {executed_trade.trade_id} | BP: ${self.bp:.2f}')
                            # Handle spread
                            if isinstance(executed_trade, MultiLegOptionPosition):
                                for i, leg in enumerate(executed_trade.legs):
                                    leg_bp = bp_effect if i == 0 else None
                                    leg.trade_id = self.trade_counter  # update before creating transaction
                                    leg.transaction_id = self.transaction_counter # ''
                                    transaction = executed_trade.create_transaction(leg, current_date, 'open', leg_bp)
                                    all_transactions.append(transaction)
                                 
                                    self.transaction_counter += 1
                            # Single leg
                            else:
                                executed_trade.transaction_id = self.transaction_counter
                                transaction = executed_trade.create_transaction(executed_trade, current_date, 'open', bp_effect)
                                all_transactions.append(transaction)
                                self.transaction_counter += 1

                            self.open_positions.append(executed_trade)

                            # Prepare trade result
                            # transaction = executed_trade.create_transaction(executed_trade, current_date, 'open')
                            self.trade_counter += 1  # Increment counter only for successful trades
                            
                            logger.debug(f'Successfully executed trade: {executed_trade.trade_id}')
                            logger.debug(f'BP: ${self.bp:.2f}')
                            
                        else:
                            skipped_trades += 1
                    else:
                        logger.debug("Skipping trade - invalid signal")
                        skipped_trades += 1
            

        # Close any remaining open positions at their expiration
        logger.info(f'Dates in period exhausted, attempting to close any remaining open positions: {self.open_positions}')
        trade_results, transactions = self._close_expired_positions(option_chain=option_chain, 
                                                                    underlying_price_history=underlying_price_history,
                                                                    current_date=current_date,
                                                                    close_all=True)
    

        if trade_results is not None:
            all_trade_results.extend(trade_results)
            all_transactions.extend(transactions)


        else:
            logger.error("Failed to close some trades")

        return {'trade_results': pd.DataFrame(all_trade_results), 
                'transactions': pd.DataFrame(all_transactions)}

    def _close_expired_positions(self,
                                 option_chain: pd.DataFrame,
                                 underlying_price_history: pd.DataFrame,
                                 current_date: pd.Timestamp,
                                 vix_early_closure=False,  # Close all open pos
                                 close_all=False) -> List[Optional[OptionTradeResult]]:
        """
        Close all open positions that have reached their expiration or close date, and update the option buying power accordingly.
        
        Args:
            current_date: The current date to check against the positions' expiration and close dates.
        """
        # First, check if any open positions need to be closed
        positions_to_remove = []
        trade_results = []
        transactions = []
        
        for pos in self.open_positions:

            # Handle positions with expiration or close date beyond backtest end date, closing then (NB: if
            # it is desired to close at the end bound of the backtest can modify here)
            if close_all:
                current_date = pos.close_date if pos.close_date is not None else pos.expire_date

            early_closure = False
            if (
                (pos.close_date is not None and current_date >= pos.close_date) or
                vix_early_closure    
            ):
                early_closure = True

            # Close position if we're on/past the close_date or expire_date
            if (
                (pos.expire_date is not None and current_date >= pos.expire_date) or
                early_closure
                ):

                logger.debug(f'Closing position: {pos.trade_id}')
                
                if isinstance(pos, MultiLegOptionPosition):

                    # Set early close date if not set for all legs (e.g. for VIX early closure)
                    if early_closure:
                        for idx, leg in enumerate(pos.legs):
                            prev = leg.close_date
                            leg.close_date = current_date
                            logger.debug(f'Leg {idx+1} close_date set: {prev} -> {leg.close_date}')

                    # Assign new transaction IDs for each leg close before closing
                    for leg in pos.legs:
                        leg.transaction_id = self.transaction_counter
                        self.transaction_counter += 1

                    # For multi-leg positions, use the spread's close method which handles all legs
                    result, leg_transactions, total_bp_effect = pos.close(option_chain=option_chain, 
                                                                         underlying_price_history=underlying_price_history,
                                                                         force=close_all)                    
                                                                         
                    if result:  
                        # Update buying power with aggregated bp_effect
                        self.bp += total_bp_effect 
                        # Restore margin since we bypassed bp updates for individual legs
                        if pos.margin_required is not None:
                            self.bp += pos.margin_required
                        result.bp = round(self.bp, 2)
                        positions_to_remove.append(pos)
                        logger.debug(f"Closed multi-leg position {pos.trade_id} - Total BP Effect: ${total_bp_effect:.2f} - New BP: ${self.bp:.2f}")
                        trade_results.append(result)
                        transactions.extend(leg_transactions)
                    
                    else:
                        logger.error('Unable to close one or more positions due to incomplete closing data')
                        self.transaction_counter -= 2
                        # return None, None
                
                # Single leg position
                else:
                    if early_closure or vix_early_closure:
                        pos.close_date = current_date
                    
                    # Assign new transaction ID for the close before closing
                    pos.transaction_id = self.transaction_counter
                    self.transaction_counter += 1

                    result, transaction, bp_effect = pos.close(option_chain=option_chain,
                                                    underlying_price_history=underlying_price_history,
                                                    force=close_all)

                    if result:  
                        # Update buying power with the calculated bp_effect
                        self.bp += bp_effect
                        result.bp = round(self.bp, 2)
                        positions_to_remove.append(pos)
                        logger.debug(f"Closed position {pos.transaction_id} - BP Effect: ${bp_effect:.2f} - New BP: ${self.bp:.2f}")
                        trade_results.append(result)
                        transactions.append(transaction)    
                    
                    else:
                        logger.error('Unable to close one or more positions due to incomplete closing data')
                        self.transaction_counter -= 1
                        # return None, None

        # Remove closed positions
        for pos in positions_to_remove:
            self.open_positions.remove(pos)

        return trade_results, transactions

    def construct_position_from_signal(self, trade_signal: pd.Series, 
                                       current_date: pd.Timestamp) -> Optional[Union[SingleLegOptionPosition, MultiLegOptionPosition]]:
        """
        Creates a new position based on the provided trade signal.
        
        This method takes a trade signal as input and constructs a new position object. 
        The type of position created depends on the configuration of the trade manager.
        Args:
            trade_signal (pd.Series): The trade signal to use for constructing the position. 
                                        This signal should contain all necessary information for creating a position, 
                                        such as entry date, position side, option type, quantity, and early close days.
            current_date (pd.Timestamp): The current date to use for constructing the position.
        Returns:
            Optional[Union[SingleLegOptionPosition, MultiLegOptionPosition]]: A new position object if the signal is valid, otherwise None.
        """
        
        # Construct new position from signal
        if isinstance(self.config, SingleLegOptionStrategyConfig):
            return SingleLegOptionPosition.construct_from_signal(
                trade_signal=trade_signal,
                option_strategy=self.config.option_strategy,
                entry_date=current_date,
                position_side=self.config.leg.position_side,
                option_type=self.config.leg.option_type,
                quantity=self.config.quantity,
                early_close_after_dit=self.config.early_close_after_dit,
                early_close_on_dte=self.config.early_close_on_dte,
            )
        elif isinstance(self.config, FuturesStrategyConfig):
            return FuturesPosition.construct_from_signal(
                trade_signal=trade_signal,
                futures_strategy=self.config.futures_strategy,
                entry_date=current_date,
                position_side=self.config.position_side,
                futures_type=self.config.futures_type,
                quantity=self.config.quantity,
                roll_date=trade_signal['roll_date'],
            )
        else:
            return MultiLegOptionPosition.construct_from_signal(
                trade_signal=trade_signal,
                config=self.config,
                entry_date=current_date,
            )

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
    #         position.exit_price = signed_entry_price(position)

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
    #             position.exit_price = signed_entry_price(position)
    #             return position
        
    #     # If we get here, no valid prices were found within 5 days
    #     logger.error(f"No valid closing prices found for position with strike {position.strike} and expire date {position.expire_date}. Returning None.")
    #     return position  # Return None if no valid closing prices were
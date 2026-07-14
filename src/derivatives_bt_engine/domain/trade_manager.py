import math
from datetime import date
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
import polars as pl
from derivatives_bt_engine.domain.enums import *
from derivatives_bt_engine.domain.position import SingleLegOptionPosition, MultiLegOptionPosition, FuturesPosition
from derivatives_bt_engine.domain.trade_result import OptionTradeResult
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength
from derivatives_bt_engine.utils.logger import setup_logger
from derivatives_bt_engine.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig

logger = setup_logger()

class TradeManager:
    """Class to manage trade creation and execution."""

    def __init__(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig], vix: Optional[pl.DataFrame] = None):
        self.config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig] = config
        self.initial_capital: float = config.initial_capital
        self.leverage: float = config.leverage
        self.bp: float = config.initial_capital
        self.max_margin_utilization: float = config.max_margin_utilization
        self.max_positions: int = config.max_positions
        self.trade_counter: int = 1
        self.transaction_counter: int = 1
        self.open_positions: List[Union[SingleLegOptionPosition, MultiLegOptionPosition, FuturesPosition]] = []
        self.vix: Optional[pl.DataFrame] = vix

        logger.info(f'TradeManager instantiated')
        logger.info(f'Init Cap: {self.initial_capital} | BP: {self.bp} | Trades: {self.trade_counter}')
        logger.info(f'Using vix range: {self.config.vix_range}')
        if isinstance(config, FuturesStrategyConfig):
            logger.info(f'ts_exit_threshold: {config.ts_exit_threshold} | ts_entry_threshold: {config.ts_entry_threshold} '
                        f'| exit_on_ts_crossover: {config.exit_on_ts_crossover}')
        logger.info(f'VIX sample: {self.vix.head() if self.vix is not None else "N/A"}')

    def _execute_trade(self, position: Union[SingleLegOptionPosition, MultiLegOptionPosition, FuturesPosition]) -> Optional[Tuple[SingleLegOptionPosition, float]]:
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
            spread_price = position.spread_price
            if spread_price is None or (isinstance(spread_price, float) and math.isnan(spread_price)):
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
                         trade_signals: pl.DataFrame,
                         option_chain: pl.DataFrame,
                         underlying_price_history: pl.DataFrame
                        ) -> Dict:
        """
        Construct and execute trades based on signals.

        Args:
            trade_signals: polars DataFrame of trade signals (futures and
                options paths are both polars-native end to end now).
            config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
        Returns:
            Dictionary containing trade results and transactions
        """
        # Initialize variables
        all_trade_results = []
        all_transactions = []
        skipped_trades = 0

        is_futures = isinstance(self.config, FuturesStrategyConfig)
        date_col = 'ts_event' if is_futures else 'date'

        if trade_signals is None or trade_signals.height == 0:
            return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

        # Precompute the full trend-strength series once (not re-derived
        # per day in the loop below) -- calculate_trend_strength's rolling
        # windows make a per-iteration recompute O(n^2) over a multi-year
        # daily backtest; a single pass up front plus a per-day lookup is
        # the same trick TSMOM's own _compute_target avoids needing only
        # because it runs monthly, not daily.
        signal_df = None
        if is_futures and (self.config.ts_exit_threshold is not None
                           or self.config.ts_entry_threshold is not None
                           or self.config.exit_on_ts_crossover):
            signal_df = (
                calculate_trend_strength(underlying_price_history.sort(date_col))
                .select([date_col, 'signal', 'ts3m', 'ts1y'])
            )

        def _signal_gate_reason(sig_val, ts3m_val, ts1y_val, is_long: bool, threshold: Optional[float]) -> Optional[str]:
            """Which direction-aware weak-signal condition holds for
            `threshold` (an exit or entry threshold -- same shape, different
            value so entry can require a stronger bar than exit, avoiding
            close/reopen thrashing right at one shared line), if any --
            returned as a specific reason string (not just a bool) so
            callers can record *why* a position closed, not just that it
            did."""
            if threshold is not None and sig_val is not None:
                if is_long and sig_val < threshold:
                    return 'signal_ts_threshold'
                if not is_long and sig_val > -threshold:
                    return 'signal_ts_threshold'
            if self.config.exit_on_ts_crossover and ts3m_val is not None and ts1y_val is not None:
                if is_long and ts3m_val < ts1y_val:
                    return 'signal_crossover'
                if not is_long and ts3m_val > ts1y_val:
                    return 'signal_crossover'
            return None

        start = trade_signals[date_col].min()
        if is_futures:
            # Bound on underlying_price_history's max, not signals' max: the
            # signal generator drops the tail end of a backtest window when
            # no roll date falls strictly after those days (e.g. the last
            # ~2 weeks of a single-year run, since the *next* cycle's roll
            # date is out of range) — but an already-open position still
            # needs its daily close-check evaluated through the actual end
            # of the period, or it never rolls/closes naturally and instead
            # force-closes at the very end via close_all.
            end = underlying_price_history[date_col].max()
        else:
            end = trade_signals[date_col].max()
        dates = pl.date_range(start, end, interval='1d', eager=True).to_list()

        # Iterate through all dates in backtest range first in order to manage trades, e.g. exit when certain
        # certain conditions are met (vix, etc.)
        # Additionally we will open or close any positions if conditions are fulfilled
        for current_date in dates:
            logger.debug(f'Processing date: {current_date}')

            match = trade_signals.filter(pl.col(date_col) == current_date)
            trade_signal = match if match.height > 0 else None

            # VIX gating (skip trade if outside range or missing)
            vix_close_value = None
            vix_early_closure = False

            if self.config.vix_range is not None or self.config.vix_max is not None:
                if self.vix is not None and self.vix.height > 0 and date_col in self.vix.columns:
                    vix_match = self.vix.filter(pl.col(date_col) == current_date)
                    if vix_match.height > 0:
                        try:
                            vix_close_value = float(vix_match['close'][0])
                        except Exception:
                            vix_close_value = None
                        logger.debug(f'VIX daily value {vix_close_value}')

                if vix_close_value is not None:
                    # Check vix_max for early exit
                    if self.config.vix_max is not None and vix_close_value > self.config.vix_max:
                        vix_early_closure = True
                        logger.debug(f'VIX {vix_close_value} exceeds max {self.config.vix_max}')

            # Signal-based gating (ts_exit_threshold/ts_entry_threshold/
            # exit_on_ts_crossover), futures only. Direction-aware: mirrors
            # the LONG condition for SHORT positions. signal_entry_blocked
            # uses ts_entry_threshold (typically a stronger bar than
            # ts_exit_threshold) so a just-closed position doesn't reopen
            # the instant the signal ticks back over the exit line.
            signal_exit_reason: Optional[str] = None
            signal_entry_blocked = False
            if signal_df is not None:
                sig_match = signal_df.filter(pl.col(date_col) == current_date)
                if sig_match.height > 0:
                    sig_val = sig_match['signal'][0]
                    ts3m_val = sig_match['ts3m'][0]
                    ts1y_val = sig_match['ts1y'][0]
                    is_long = self.config.position_side == PositionSide.LONG

                    signal_exit_reason = _signal_gate_reason(
                        sig_val, ts3m_val, ts1y_val, is_long, self.config.ts_exit_threshold)
                    signal_entry_blocked = _signal_gate_reason(
                        sig_val, ts3m_val, ts1y_val, is_long, self.config.ts_entry_threshold) is not None

                    if signal_exit_reason is not None:
                        logger.debug(f'Signal exit gate triggered ({signal_exit_reason}): signal={sig_val}, ts3m={ts3m_val}, ts1y={ts1y_val}')
                    if signal_entry_blocked:
                        logger.debug(f'Signal entry gate blocked: signal={sig_val}, ts3m={ts3m_val}, ts1y={ts1y_val}')

            # Close any expired positions
            n_open_positions = len(self.open_positions)
            if n_open_positions > 0:
                if vix_early_closure:
                    logger.debug(f'VIX early closure for {n_open_positions} open positions')
                if signal_exit_reason is not None:
                    logger.debug(f'Signal early closure for {n_open_positions} open positions')

                trade_results, transactions = self._close_expired_positions(
                    option_chain=option_chain,
                    underlying_price_history=underlying_price_history,
                    current_date=current_date,
                    vix_early_closure=vix_early_closure,  # Pass the boolean flag
                    signal_exit_reason=signal_exit_reason,
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

            # Check signal-based gate for trade entry (mirrors vix_range above,
            # using ts_entry_threshold/exit_on_ts_crossover instead of ts_exit_threshold
            # -- keeps a just-closed position flat until the signal genuinely
            # recovers, instead of reopening the next day it ticks back over
            # the exit line)
            if signal_entry_blocked:
                skipped_trades += 1
                logger.debug(f'Skipping trade date {current_date} due to signal entry gate')
                continue

            # Construct a new position from the trade signal if possible on the current date
            if trade_signal is not None:
                for trade in trade_signal.iter_rows(named=True):
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

        return {'trade_results': pl.DataFrame(all_trade_results),
                'transactions': pl.DataFrame(all_transactions)}

    def _close_expired_positions(self,
                                 option_chain: pl.DataFrame,
                                 underlying_price_history: pl.DataFrame,
                                 current_date: date,
                                 vix_early_closure=False,  # Close all open pos
                                 signal_exit_reason: Optional[str] = None,  # Close all open pos (ts_exit_threshold/exit_on_ts_crossover); also names *why*
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

        # Reason recorded on the trade result for an early closure -- 'vix'/
        # signal_exit_reason take priority (they're what force-closed a
        # position that hadn't otherwise reached its own close_date/
        # expire_date yet); if neither fired, pass None through so
        # SingleLegOptionPosition/MultiLegOptionPosition/FuturesPosition each
        # fall back to their own existing default ('early closure'/
        # 'expiration'/'roll') -- unchanged behavior when no gate applies.
        if signal_exit_reason is not None:
            reason = signal_exit_reason
        elif vix_early_closure:
            reason = 'vix'
        else:
            reason = None

        for pos in self.open_positions:

            # Handle positions with expiration or close date beyond backtest end date, closing then (NB: if
            # it is desired to close at the end bound of the backtest can modify here)
            if close_all:
                current_date = pos.close_date if pos.close_date is not None else pos.expire_date

            early_closure = False
            if (
                (pos.close_date is not None and current_date >= pos.close_date) or
                vix_early_closure or signal_exit_reason is not None
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
                                                                         force=close_all,
                                                                         close_reason=reason)

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

                    # FuturesPosition's own close_reason default is None (not
                    # 'roll') -- since close() always forwards the keyword
                    # explicitly, an unset gate must be filled in here or a
                    # natural roll would record close_reason=None instead of
                    # 'roll'. Options positions derive their own default
                    # ('early closure'/'expiration') internally when None.
                    close_reason_arg = (reason or 'roll') if isinstance(pos, FuturesPosition) else reason
                    result, transaction, bp_effect = pos.close(option_chain=option_chain,
                                                    underlying_price_history=underlying_price_history,
                                                    force=close_all,
                                                    close_reason=close_reason_arg)

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

    def construct_position_from_signal(self, trade_signal: dict,
                                       current_date: date) -> Optional[Union[SingleLegOptionPosition, MultiLegOptionPosition, FuturesPosition]]:
        """
        Creates a new position based on the provided trade signal.

        This method takes a trade signal as input and constructs a new position object.
        The type of position created depends on the configuration of the trade manager.
        Args:
            trade_signal (dict): A polars row (from iter_rows(named=True)) containing
                                  all necessary information for creating a position,
                                  such as entry date, position side, option type, quantity, and early close days.
            current_date (date): The current date to use for constructing the position.
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
                fill_price=self.config.fill_price,
            )
        else:
            return MultiLegOptionPosition.construct_from_signal(
                trade_signal=trade_signal,
                config=self.config,
                entry_date=current_date,
            )

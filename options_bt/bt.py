import sys
import os
from typing import Dict, List, NamedTuple, Optional, Tuple, TypedDict, Union
from enum import Enum 

import logging
from datetime import datetime
import time
# import dask.dataframe as dd  # Commented out Dask import
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
 
from options_bt.domain.enums import *  
from options_bt.domain.spread import Spread
from options_bt.domain.option_trade import OptionTrade   
from options_bt.domain.option_position import OptionPosition
from options_bt.domain.schemas import (
    OPTIONS_CHAIN_SCHEMA,
    TRADE_SIGNALS_SCHEMA,
    POSITION_SCHEMA,
    TRADE_RESULTS_SCHEMA,
    validate_dataframe_schema,
    standardize_dataframe,
    add_spread_fields   
)
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()




def calculate_margin(underlying_price: float, entry_price: float, 
                           position_side: Union[PositionSide, str],
                           strike: float,
                           option_type: Union[OptionType, str],
                           margin_req_percent: float = 0.15) -> float:
    """
    Calculate required margin for option position using IB's formula for Index Options.
    
    Args:
        underlying_price: Current price of underlying asset
        entry_price: Option premium (mid of bid/ask)
        position_side: Whether position is LONG or SHORT
        strike: Option strike price
        option_type: Type of option (PUT or CALL)
        margin_req_percent: Margin requirement percentage (default 0.15 for IB)
    
    Returns:
        Required margin in dollars
    """
    # Convert string to enum if needed
    # if isinstance(position_side, str):
    #     position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
    
    # For long positions, margin is just the cost of the option
    # There is no margin req for Long positions
    if is_long(position_side):
        # return round(entry_price * 100, 2)  # Convert to dollars
        return 0
    
    # For short positions, use IB's formula for Index Options
    else:  # PositionSide.SHORT
        # Calculate out-of-the-money amount
        if is_put(option_type):
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

def calculate_margin_for_spread(leg_group: pd.DataFrame) -> float:
    """
    Calculate margin requirement for a spread position.
    
    Args:
        leg_group: DataFrame containing the legs of the spread
        
    Returns:
        float: Total margin required for the spread
    """
    # logger.debug(f'Calculating margin for spread type: {leg_group.iloc[0]["spread_type"]}')
    
    # For diagonal spreads, margin depends on whether it's a long or short diagonal spread
    if is_spread_type(leg_group, SpreadType.DIAGONAL):
        total_margin = 0
        for leg in leg_group.itertuples():
            leg_margin = calculate_margin(
                leg.underlying_entry,
                leg.entry_price,
                leg.position_side,
                leg.strike,
                leg.option_type,
                leg.expiration
            )
            total_margin += leg_margin
        return total_margin
    
    # For vertical spreads, margin is the width of the spread
    elif is_spread_type(leg_group, SpreadType.VERTICAL):
        legs = list(leg_group.itertuples())
        if len(legs) != 2:
            raise ValueError(f"Vertical spread must have exactly 2 legs, got {len(legs)}")
        strikes = sorted([leg.strike for leg in legs])
        return abs(strikes[1] - strikes[0]) * 100
    
    # For calendar spreads, margin is the width of the spread
    elif is_spread_type(leg_group, SpreadType.CALENDAR):
        legs = list(leg_group.itertuples())
        if len(legs) != 2:
            raise ValueError(f"Calendar spread must have exactly 2 legs, got {len(legs)}")
        strikes = sorted([leg.strike for leg in legs])
        return abs(strikes[1] - strikes[0]) * 100
    
    # For iron condors, margin is the width of the put spread plus the width of the call spread
    elif is_spread_type(leg_group, SpreadType.IRON_CONDOR):
        legs = list(leg_group.itertuples())
        if len(legs) != 4:
            raise ValueError(f"Iron condor must have exactly 4 legs, got {len(legs)}")
        
        # Sort legs by strike price
        legs.sort(key=lambda x: x.strike)
        strikes = sorted([leg.strike for leg in legs])
        
        # Calculate width of put spread (first two legs) and call spread (last two legs)
        put_spread_width = abs(strikes[1] - strikes[0])
        call_spread_width = abs(strikes[3] - strikes[2])
        
        return (put_spread_width + call_spread_width) * 100
    
    # For butterflies, margin is the width of the spread
    elif is_spread_type(leg_group, SpreadType.BUTTERFLY):
        legs = list(leg_group.itertuples())
        if len(legs) != 3:
            raise ValueError(f"Butterfly spread must have exactly 3 legs, got {len(legs)}")
        strikes = sorted([leg.strike for leg in legs])
        return abs(strikes[2] - strikes[0]) * 100
    
    else:
        raise ValueError(f"Unsupported spread type: {leg_group.iloc[0]['spread_type']}")

def calculate_intrinsic_value(underlying_price: float, strike: float, option_type: Union[OptionType, str]) -> float:
    """
    Calculate intrinsic value for an option.
    
    Args:
        underlying_price: Current price of underlying asset
        strike: Option strike price
        option_type: Type of option (PUT or CALL)
    
    Returns:
        Intrinsic value of the option
    """

    # is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    # if isinstance(option_type, str):
    #     is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    # else:
    #     is_put = option_type == OptionType.PUT
    
    logger.debug(f'Expiration. Calculating intrinsic value for {option_type}, strike={strike}, underlying={underlying_price}')
    logger.debug(f'IV: {max(0, strike - underlying_price) if is_put(option_type) else max(0, underlying_price - strike)}')

    
    if is_put(option_type):
        return max(0, strike - underlying_price)
    else:  # CALL
        return max(0, underlying_price - strike)

def calculate_midpoint_price(bid: float, ask: float) -> Optional[float]:
    """
    Calculate the midpoint price between bid and ask, with validation.
    
    Args:
        bid: Bid price
        ask: Ask price
        
    Returns:
        Optional[float]: Midpoint price if valid, None if invalid
    """
    if bid <= 0 or ask <= 0:
        return None
        
    spread_pct = ((ask - bid) / bid) * 100
    if spread_pct > 50.0:  # Spread too wide (50% threshold)
        logger.warning(f"Bid-ask spread too wide: bid={bid}, ask={ask}, spread={spread_pct:.2f}%")
        return None
        
    return (bid + ask) / 2

def get_closing_data(
    position: 'Position',
    full_chain_df: pd.DataFrame, 
    spx_data: pd.DataFrame
) -> Optional['Position']:
    """
    Get closing price data for an option position.
    
    Args:
        position (Position): Position containing trade details.
        full_chain_df (pd.DataFrame): DataFrame containing full option chain data.
        spx_data (pd.DataFrame): DataFrame containing underlying price data.
    
    Returns:
        Optional[Position]: The updated position object with closing price and other relevant data, or None if no valid closing data is found.
    """
  
    expire_date = position['expire_date']
    # If no close_date, this is an expiration.
    if 'close_date' not in position or not position['close_date']:
        if expire_date not in spx_data.index:
            logger.warning(f"No valid closing data found for position with expire date {expire_date}. Returning None.")
            return position  # Return None if no valid closing data is found
        
        # Get underlying (e.g. SPX) at close
        underlying_close = spx_data.loc[expire_date, 'close']
        position['underlying_exit'] = underlying_close
        position['exit_price'] = calculate_intrinsic_value(underlying_close, position['strike'], position['option_type'])
        position['exit_price'] = get_signed_exit_price(position)

        # Get delta value at expiration
        delta_col = "p_delta" if is_put(position) else 'c_delta'
        filtered_df = full_chain_df[
                (full_chain_df.index == expire_date) &
                (full_chain_df['expire_date'] == expire_date) &
                (full_chain_df['strike'] == position['strike'])
            ]
        
        if not filtered_df.empty:
            exit_delta = round(filtered_df[delta_col].iloc[0], 2)      
            position['exit_delta'] = exit_delta

        return position
    
    # Early close - get data from close_date forward (up to 5 days)
    close_date = position['close_date']
    date_range = pd.date_range(close_date, close_date + pd.Timedelta(days=5))
    
    filtered_df = full_chain_df[
        (full_chain_df.index.isin(date_range)) & 
        (full_chain_df['expire_date'] == position['expire_date']) &
        (full_chain_df['strike'] == position['strike'])
    ].sort_index()  # Sort by date to try closest dates first
    
    if filtered_df.empty:
        logger.warning(f"No valid prices found within 5 days of close date {close_date}. Returning None.")
        return position  # Return unchanged position if no valid prices were found within 5 days
        
    bid_col = "p_bid" if is_put(position) else "c_bid"
    ask_col = "p_ask" if is_put(position) else "c_ask"
    delta_col = "p_delta" if is_put(position) else 'c_delta'

    # Try each date in the filtered data until we find valid prices
    for _, row in filtered_df.iterrows():
        bid = row[bid_col]
        ask = row[ask_col]
        underlying_close = row['underlying_last']
        position['underlying_exit'] = underlying_close
        position['exit_delta'] = round(row[delta_col], 2)
        mid_price = calculate_midpoint_price(bid, ask)
        if mid_price is not None:
            position['exit_price'] = mid_price
            position['exit_price'] = get_signed_exit_price(position)
            return position
    
    # If we get here, no valid prices were found within 5 days
    logger.error(f"No valid closing prices found for position with strike {position['strike']} and expire date {position['expire_date']}. Returning None.")
    return position  # Return None if no valid closing prices were found

def calculate_option_pnl(position: 'Position') -> float:
    """
    Calculate P&L for option position.
    
    Args:
        position: Position
    
    Returns:
        P&L in dollars
    """
    # Calculate P&L using entry and closing prices (signed)
    pnl = get_signed_entry_price(position) + get_signed_exit_price(position)
    return pnl * 100 * position.quantity if position.is_short() else max(0, pnl * 100 * position.quantity) # clamp loss to zero if LONG

def close_position(position: Position, 
                  full_chain_df: pd.DataFrame, 
                  underlying_price_history: pd.DataFrame,
                  option_bp: float) -> Optional[TradeResult]:
    """
    Close an open option position and calculate results.
    
    Args:
        position: Position containing trade details.
        full_chain_df: DataFrame containing full option chain data.
        underlying_price_history: DataFrame containing underlying price data.
        option_bp: Current buying power before closing position.
    
    Returns:
        TradeResult if successful, None if closing data is unavailable.

    Note:
        This function does not track cash flow explicitly due to the complexities 
        involved in managing multiple simultaneous trades. Instead, it updates 
        option buying power based on margin requirements.
    """
    close_reason = None
    min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970
    
    # Validate entry_date
    entry_date = position.entry_date
    if not isinstance(entry_date, pd.Timestamp) or entry_date <= min_valid_date:
        logger.error(f"Invalid entry date: {entry_date} - skipping trade")
        return None
    
    # Early closure, get close date with validation
    if position.close_date is not None:
        close_reason = 'early closure'
        close_date = position.close_date
    elif position.expire_date is not None:
        close_reason = 'expired'
        close_date = position.expire_date
    else:
        logger.error("Both close_date and expire_date are None in position - skipping trade")
        return None
    
    logger.debug(f'Close Reason: {close_reason}')

    # Validate close_date
    if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
        logger.error(f"Invalid close date: {close_date} - skipping trade")
        return None
    
    # Ensure close_date is not before entry_date
    if close_date < position.entry_date:
        logger.error(f"Close date {close_date} is before entry date {position.entry_date} - skipping trade")
        return None
    
    # Get closing prices
    position = get_closing_data(position, full_chain_df, underlying_price_history)
    
    # If get_closing_data returned None values, we should skip this trade
    if position.exit_price is None:
        logger.warning("Skipping trade due to missing close data")
        return None
    
    # Calculate P&L using the premium from entry
    pnl = calculate_option_pnl(position)
    logger.debug(f'Calculated pnl: {pnl}')
    
    # Update buying power to reflect the P&L from the closed trade
    exit_price = get_signed_exit_price(position)
    premium = exit_price * 100 * position.quantity  # Premium in dollars
    option_bp += premium  # Add/subtract exit premium (already signed)

    # Restore margin for short positions
    if position.is_short():
        option_bp += position.margin_required  # Restore full margin requirement

    # Calculate days held (time between close date and entry)
    days_held = pd.Timedelta(close_date - position.entry_date).days
    
    # Safety check for negative days
    if days_held < 0:
        logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
        return None
    
    # Prepare the trade result with all relevant information
    trade_result: TradeResult = {
        'trade_id': position.trade_id,
        'quantity': position.quantity,
        'option_type': position.option_type.value if isinstance(position.option_type, Enum) else str(position.option_type),
        'position_side': position.position_side.value if isinstance(position.position_side, Enum) else str(position.position_side),
        'entry_date': position.entry_date,
        'exit_date': close_date,
        'expire_date': position.expire_date,
        'entry_delta': round(position.entry_delta, 2),
        'exit_delta': round(position.exit_delta, 2),
        'entry_dte': position.entry_dte,
        'days_held': days_held,
        'underlying_entry': position.underlying_entry,
        'underlying_exit': position.underlying_exit,
        'strike': position.strike, 
        'entry_price': round(position.entry_price, 2),
        'exit_price': round(position.exit_price, 2),
        'capital_used': position.margin_required,  # Keep this as is
        'option_bp': round(option_bp, 2),  # Updated buying power
        'return_on_margin': round(pnl / position.margin_required * 100, 2) if position.margin_required > 0 else 0,
        'close_reason': close_reason,
        'pnl': round(pnl, 2),
        'spread_type': position.get('spread_type', SpreadType.NONE.value),
        'spread_id': position.get('spread_id', None),
        'leg_number': position.get('leg_number', None)
    }
    return trade_result

def is_bid_ask_spread_reasonable(bid: float, ask: float, max_spread_percent: float = 50.0) -> bool:
    """
    Check if the bid-ask spread is reasonable.
    
    Args:
        bid: Bid price
        ask: Ask price
        max_spread_percent: Maximum allowed spread as a percentage (default 50%)
        
    Returns:
        bool: True if spread is reasonable, False otherwise
    """
    if bid <= 0 or ask <= 0:
        return False
        
    spread_percent = ((ask - bid) / bid) * 100
    return spread_percent <= max_spread_percent

def create_trade_from_signal(
    trade_signal: NamedTuple,  # Assuming trade_signal is a from Pandas itertuples
    quantity: int,
    option_type: OptionType,
    position_side: PositionSide,
    delta_target: float,
    entry_date: pd.Timestamp,  # Assuming entry_date is a pandas Timestamp
    early_close_days: Optional[int] = None,  # Optional parameter for early closure
    delta_range: Optional[Tuple[float, float]] = None,  # Optional parameter for delta range
    # INSERT_YOUR_REWRITE_HERE
) -> Optional['Position']:
    """
    Creates a Position object from a given trade signal.

    This function takes a trade signal, along with other parameters, and attempts to create a Position object. It checks the trade signal's delta against a target delta and ensures the entry date is valid.

    Args:
        trade_signal (NamedTuple): A named tuple representing a trade signal, containing information about the trade.
        trade_counter (int): A counter for the number of trades.
        option_type (OptionType): The type of option (PUT or CALL).
        position_side (PositionSide): The side of the position (BUY or SELL).
        delta_target (float): The target delta for the trade.
        entry_date (pd.Timestamp): The date of entry into the trade.
        early_close_days (Optional[int]): The number of days before expiration to close the trade early. Defaults to None.
        delta_range (Optional[Tuple[float, float]]): The range of delta values to consider. Defaults to None.
        trade_counter (Optional[int]): A counter for the number of trades. Defaults to None.

    Returns:
        Optional[Position]: A Position object if the trade is valid, otherwise None.
    """

    # logger.debug(f"Creating trade from signal for date: {entry_date}")
    
    # Initialize skipped_trades counter
    skipped_trades = 0
    
    # Check if this trade meets our delta criteria before attempting execution
    delta_col = "p_delta" if is_put(option_type) else "c_delta"
    trade_delta = getattr(trade_signal, delta_col)  # Use getattr() as a function
    
    # # For puts, we want negative deltas, so convert positive input to negative
    # if is_put(option_type):
    #     target_delta = -abs(delta_target) if delta_target is not None else None
    #     # For puts, we want to compare the actual values since more negative means more ITM
    #     delta_diff = abs(trade_delta - target_delta) if target_delta is not None else None
    # else:
    #     target_delta = abs(delta_target) if delta_target is not None else None
    #     delta_diff = abs(trade_delta - target_delta) if target_delta is not None else None


    # # Check if delta_range is provided and filter accordingly
    # if delta_range:
    #     if is_put(option_type):
    #         # For puts, more negative delta means more ITM
    #         min_delta = -abs(delta_range[1])  #  More negative (more ITM)
    #         max_delta = -abs(delta_range[0])  # Less negative (more OTM)
    #         if not (min_delta <= trade_delta <= max_delta):
    #             logger.debug(f"Skipping trade with delta {trade_delta:.2f} (not in range: {min_delta:.2f} to {max_delta:.2f})")
    #             skipped_trades += 1
    #             return None
    #     else:
    #         # For calls, higher positive delta means more ITM
    #         min_delta = abs(delta_range[0])  # Less positive (more OTM)
    #         max_delta = abs(delta_range[1])  # More positive (more ITM)
    #         if not (min_delta <= trade_delta <= max_delta):
    #             logger.debug(f"Skipping trade with delta {trade_delta:.2f} (not in range: {min_delta:.2f} to {max_delta:.2f})")
    #             skipped_trades += 1
    #             return None

    # # Skip trades that are too far from our target delta
    # if delta_diff is not None and delta_diff > abs(target_delta) * 0.20:  # Allow 20% deviation from target delta
    #     logger.debug(f"Skipping trade with delta {trade_delta:.2f} (target: {target_delta:.2f}, diff: {delta_diff:.2f})")
    #     skipped_trades += 1
    #     return None

    # Validate trade_signal.Index is a valid date
    min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970
    
    if not isinstance(entry_date, pd.Timestamp):
        logger.error(f"Entry date {entry_date} is not a Timestamp")
        return None
        
    if entry_date <= min_valid_date:
        logger.error(f"Entry date {entry_date} is before {min_valid_date}")
        return None
    
    # Validate expire_date exists and is not None
    if not trade_signal.expire_date:
        logger.error(f"expire_date is missing for trade signal on {trade_signal.Index}")
        return None
    
    # Validate expire_date is a valid date
    expire_date = trade_signal.expire_date
    
    if not isinstance(expire_date, pd.Timestamp):
        logger.error(f"Expire date {expire_date} is not a Timestamp")
        return None
        
    if expire_date <= min_valid_date:
        logger.error(f"Expire date {expire_date} is before {min_valid_date}")
        return None
        
    if expire_date <= entry_date:
        logger.error(f"Expire date {expire_date} is not after entry date {entry_date}")
        return None
    
    # Validate strike value is present
    if not hasattr(trade_signal, 'strike') or pd.isna(trade_signal.strike):
        logger.error(f"Missing strike value in trade signal on {trade_signal.Index}")
        return None
    
    # Get bid and ask values
    # Get bid/ask fields based on option type
    # bid_field = "p_bid" if is_put(option_type) else "c_bid"
    # ask_field = "p_ask" if is_put(option_type) else "c_ask"
    # bid = getattr(trade_signal, bid_field, 0)
    # ask = getattr(trade_signal, ask_field, 0)
    
    # Check if bid-ask spread is reasonable
    # if not is_bid_ask_spread_reasonable(bid, ask):
    #     logger.debug(f"Skipping trade_signal with unreasonable bid-ask spread: bid={bid}, ask={ask}")
    #     skipped_trade_signals += 1
    #     return None
        
    # entry_price = calculate_midpoint_price(bid, ask)
    entry_price = trade_signal.midpoint_price
    if entry_price is None:
        logger.error(f"Missing midpoint price for trade signal on {trade_signal.Index}")
        return None
    # Adjust entry price sign based on position side
    # For long positions, entry price should be negative (cash outflow)
    # For short positions, entry price should be positive (cash inflow)
    signed_entry_price = -entry_price if is_long(position_side) else entry_price

    # Calculate DTE
    entry_dte = pd.Timedelta(trade_signal.expire_date - entry_date).days
    
    # Create Position from trade signal
    # Calculate initial margin
    underlying_price = trade_signal.underlying_last
    # init_margin = calculate_margin(underlying_price, abs(entry_price), position_side, trade_signal.strike, option_type)  # Use absolute entry price for margin
    init_margin = trade_signal.margin_required

    # Create the position with date    
    trade = Position(
        trade_id=None,
        quantity=quantity,  # Added quantity field
        option_type=option_type,
        position_side=position_side,
        strike=trade_signal.strike,
        expire_date=trade_signal.expire_date,
        entry_date=entry_date,
        entry_price=signed_entry_price,
        entry_delta=trade_delta,
        entry_dte=trade_signal.dte if hasattr(trade_signal, 'dte') else entry_dte,
        underlying_entry=underlying_price,
        margin_required=trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else 0,
        close_date=entry_date + pd.Timedelta(days=early_close_days) if early_close_days is not None else None,
    )
    return trade

def execute_trade(trade: Position, 
                  option_bp: float, 
                  leverage: float = 4.0, 
                  ) -> Tuple[Optional[Position], float, float]:
    """
    Execute a trade with the given position, cash, option buying power, and leverage.
    
    Args:
        trade: Position containing trade details
        option_bp: Current buying power for options
        leverage: Leverage for the trade (default: 4.0)
    
    Returns:
        Tuple of (trade, cash, option_bp) if successful, None if trade cannot be executed
    """

    # Use spread price for spreads, individual leg price for single legs
    if isinstance(trade, Spread) and hasattr(trade, 'spread_type') and trade.spread_type != SpreadType.NONE.value:
        if pd.isna(trade.spread_price):
            logger.error(f"Missing spread_price for spread {trade.spread_id} leg {trade.leg_number} on {trade.entry_date}")
            return None, option_bp
        premium = abs(trade.spread_price) * 100 * trade.quantity  # Use spread price for spreads
    else:
        premium = abs(trade.entry_price) * 100 * trade.quantity  # Use individual leg price for single legs

    # Calculate effective margin requirement with leverage
    effective_margin = trade.margin_required / leverage
    if effective_margin is None or effective_margin <= 0:
        logger.error(f"Invalid margin requirement for trade on {trade.entry_date}")
        return None, option_bp
    
    # Open LONG position
    if trade.is_long:  # Remove parentheses - it's a property
        # For long positions, check if there is enough buying power to buy the option
        if option_bp >= premium:  # Check against buying power
            option_bp -= premium  # Deduct premium from buying power
            return trade, option_bp
        else:
            logger.warning(f"Insufficient buying power (${option_bp}) to buy option on {trade.entry_date}. Required: ${premium:.2f}")
            return None, option_bp

    # Open SHORT position
    elif trade.is_short:  # Remove parentheses - it's a property
        # For short positions, check if buying power is sufficient
        if option_bp >= effective_margin:
            option_bp += premium  # Credit premium
            option_bp -= effective_margin  # Reserve margin
            return trade, option_bp
        else:
            logger.warning(f"Insufficient buying power (${option_bp}) to sell option on {trade.entry_date}. Required margin: ${effective_margin:.2f}")
            return None, option_bp

    return None, option_bp  # Return None if trade type is not recognized

def execute_backtest_trades(trade_signals: pd.DataFrame,
                            full_chain_df: pd.DataFrame, 
                            underlying_price_history: pd.DataFrame,
                            option_type: OptionType = None,
                            position_side: PositionSide = None,
                            initial_capital: float = 100000.00,
                            max_positions: int = 1,
                            leverage: float = 1.0,
                            early_close_days: int = None,
                            delta_target: float = None,
                            delta_range: Tuple[float, float] = None,
                            quantity: int = 1
                           ) -> pd.DataFrame:
    """
    Execute trades based on signals.
    
    Args:
        trade_signals: DataFrame containing trade signals
        full_chain_df: DataFrame containing the full options chain
        underlying_price_history: DataFrame containing the underlying price history
        option_type: Type of option (call/put)
        position_side: Side of the position (long/short)
        initial_capital: Initial capital to start with
        max_positions: Maximum number of positions to hold at once
        leverage: Leverage to use for margin calculations
        early_close_days: Number of days before expiration to close positions
        delta_target: Target delta for the position
        delta_range: Range of acceptable deltas
        quantity: Number of contracts to trade
        
    Returns:
        DataFrame containing trade results
    """
    # Initialize variables
    trade_counter = 0
    option_bp = initial_capital
    open_positions = []
    trade_results = []
    skipped_trades = 0
    
    # Check if we're dealing with spreads
    is_spread = 'spread_type' in trade_signals.columns and trade_signals['spread_type'].iloc[0] != SpreadType.NONE
    
    for trade_signal in trade_signals.itertuples():
        current_date = trade_signal.Index
        
        # First, check if any open positions need to be closed
        positions_to_remove = []
        for pos in open_positions:

            # Close position if we're on/past the close_date or expire_date
            if ((pos.close_date is not None and current_date >= pos.close_date) or
                (pos.expire_date is not None and current_date >= pos.expire_date)):
                
                logger.debug(f'Closing position: {pos}')
                result = close_position(pos, full_chain_df, underlying_price_history, option_bp)
                if result:
                    option_bp = result['option_bp']
                    positions_to_remove.append(pos)
                    logger.debug(f"Closed position - BP: ${option_bp:.2f}")
                    trade_results.append(result)
    
        # Remove closed positions
        for pos in positions_to_remove:
            open_positions.remove(pos)
    
        # Skip if we've reached max positions
        if len(open_positions) >= max_positions:
            skipped_trades += 1
            continue

        # Create new trade from signal
        trade = create_trade_from_signal(trade_signal, quantity, option_type, position_side, delta_target, current_date, early_close_days, delta_range)
        
        # Try to execute the new trade if it was created successfully
        if trade is not None:
            executed_trade, option_bp = execute_trade(trade, option_bp, leverage)
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
        result = close_position(pos, full_chain_df, underlying_price_history, option_bp)
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
    results_df['cumulative_pnl'] = results_df['pnl'].cumsum()
    results_df['capital'] = initial_capital + results_df['cumulative_pnl']  # Track actual capital based on cumulative PnL
    results_df['peak_capital'] = results_df['capital'].cummax()
    
    return results_df

def preprocess_options_data(options_chain: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and preprocess options data to catch and fix common issues.
    
    Args:
        options_chain: Raw options chain DataFrame
    
    Returns:
        Cleaned options chain DataFrame
    """
    logger.info("Preprocessing options chain data...")
    
    # Make a copy to avoid modifying the original
    df = options_chain.copy()
    
    # Normalize the DataFrame index if needed - more efficient approach
    try:
        # Check just the first few values instead of the entire index
        sample_size = min(10, len(df.index))
        if sample_size > 0:
            # Take a sample of index values to check if normalization is needed
            sample_indices = df.index[:sample_size]
            has_time_component = False
            
            # Check if any of the sampled indices have time components
            for idx in sample_indices:
                if hasattr(idx, 'time') and idx.time() != pd.Timestamp('00:00:00').time():
                    has_time_component = True
                    break
            
            # Only normalize if we detected time components
            if has_time_component:
                logger.info("Normalizing index dates...")
                df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
                logger.info("Index normalization complete")
    except Exception as e:
        logger.info(f"Index normalization skipped: {e}")
        # Just skip normalization if there's an issue - it's likely already normalized
        pass
    
    # Check and fix expire_date issues
    if 'expire_date' in df.columns:
        # Count invalid timestamps
        invalid_dates = 0
        fixed_dates = 0
        
        # Sample a few rows to check if fixes are needed
        sample_size = min(100, len(df))
        sample_rows = df.sample(n=sample_size) if sample_size > 0 else df
        
        needs_fixing = False
        for _, row in sample_rows.iterrows():
            if row['expire_date'] is not None and not isinstance(row['expire_date'], pd.Timestamp):
                needs_fixing = True
                break
        
        # Only process if we detected issues in the sample
        if needs_fixing:
            logger.info("Fixing invalid expire_date values...")
            # Check for any non-Timestamp objects in expire_date
            for i, row in df.iterrows():
                if row['expire_date'] is not None and not isinstance(row['expire_date'], pd.Timestamp):
                    invalid_dates += 1
                    try:
                        # Try to convert to Timestamp
                        df.at[i, 'expire_date'] = pd.Timestamp(row['expire_date'])
                        fixed_dates += 1
                    except:
                        # If conversion fails, set to NaT (pandas missing timestamp)
                        df.at[i, 'expire_date'] = pd.NaT
            
            logger.info(f"Fixed {fixed_dates} of {invalid_dates} invalid expire_date values")
        
        # Normalize all expire_dates to remove time component
        logger.info("Normalizing expire_date values...")
        df['expire_date'] = pd.to_datetime(df['expire_date'])
        # Use .dt accessor for Series objects instead of directly calling floor
        df['expire_date'] = df['expire_date'].dt.normalize()
        
        # Drop rows with missing expire_dates
        rows_before = len(df)
        df = df.dropna(subset=['expire_date'])
        rows_dropped = rows_before - len(df)
        logger.info(f"Dropped {rows_dropped} rows with missing expire_dates")
    
    # Normalize any other date columns that might exist
    date_columns = ['quote_readtime', 'trade_date']
    for col in date_columns:
        if col in df.columns:
            logger.info(f"Normalizing {col} values...")
            df[col] = pd.to_datetime(df[col])
            # Use .dt accessor for Series objects
            df[col] = df[col].dt.normalize()
    
    # Ensure all numeric columns are properly typed
    numeric_cols = ['strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'p_delta', 'c_delta', 'underlying_last']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Filter out any negative prices
    for col in ['p_bid', 'p_ask', 'c_bid', 'c_ask']:
        if col in df.columns:
            invalid_prices = (df[col] < 0).sum()
            if invalid_prices > 0:
                logger.info(f"Found {invalid_prices} negative values in {col}, replacing with NaN")
                df.loc[df[col] < 0, col] = np.nan
    
    # Calculate days to expiration if it doesn't exist
    if 'dte' not in df.columns:
        logger.info("Calculating days to expiration...")
        df['dte'] = df.apply(lambda row: pd.Timedelta(row['expire_date'] - row.name).days, axis=1)
    
    if 'c_size' in df.columns:
        pass  # need to preprocess


    logger.info("Sample of preprocessed data:")
    logger.debug(str(df.head(5)))

    return df

def preprocess_spx_data(spx_data: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and preprocess SPX price data.
    
    Args:
        spx_data: Raw SPX price DataFrame
    
    Returns:
        Cleaned SPX price DataFrame
    """
    logger.info("Preprocessing SPX data...")
    
    # Make a copy to avoid modifying the original
    df = spx_data.copy()
    
    # Normalize the DataFrame index if needed
    try:
        df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
    except Exception as e:
        logger.info(f"Index normalization skipped: {e}")
    
    # Ensure all numeric columns are properly typed
    numeric_cols = ['open', 'high', 'low', 'close']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Sort by date
    df.sort_index(inplace=True)
    
    return df

def preprocess_vix_data(vix_data: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and preprocess VIX data.
    
    Args:
        vix_data: Raw VIX DataFrame
    
    Returns:
        Cleaned VIX DataFrame
    """
    logger.info("Preprocessing VIX data...")
    
    # Make a copy to avoid modifying the original
    df = vix_data.copy()
    
    # Normalize the DataFrame index if needed
    try:
        df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
    except Exception as e:
        logger.info(f"Index normalization skipped: {e}")
    
    # Ensure all numeric columns are properly typed
    numeric_cols = ['open', 'high', 'low', 'close']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Sort by date
    df.sort_index(inplace=True)
    
    return df

def load_backtest_data(data_dir, use_preprocessed=True, save_preprocessed=True, options_file="options.csv"):
    """
    Load and preprocess data for backtesting from a standard data directory.
    
    Args:
        data_dir: Path to directory containing the data files
        use_preprocessed: Whether to use preprocessed data files
        save_preprocessed: Whether to save preprocessed data for future use
        options_file: Name of the options data file (default: options.csv)
    
    Returns:
        tuple: (options_chain, options_chain_multi_index, spx_data, vix_data)
    """
    raw_files = {
        'options': os.path.join(data_dir, options_file),
        'spx': os.path.join(data_dir, 'spx.csv'),
        'vix': os.path.join(data_dir, 'vix.csv')
    }
    
    processed_files = {
        'options': os.path.join(data_dir, "options.pkl"),
        'spx': os.path.join(data_dir, "spx.pkl"),
        'vix': os.path.join(data_dir, "vix.pkl"),
        'chain_multi_index': os.path.join(data_dir, "chain_multi_index.pkl")
    }
    
    try:
        options_chain = None
        options_chain_multi_index = None
        
        # Try to load preprocessed data if requested
        if use_preprocessed:
            # Load MultiIndex options chain if it exists
            if os.path.exists(processed_files['chain_multi_index']):
                logger.info("Loading MultiIndex options chain")
                options_chain_multi_index = pd.read_pickle(processed_files['chain_multi_index'])
            
            # Load normal options chain if it exists
            if os.path.exists(processed_files['options']):
                logger.info("Loading normal options chain")
                options_chain = pd.read_pickle(processed_files['options'])
            
            # Load SPX and VIX data
            if os.path.exists(processed_files['spx']) and os.path.exists(processed_files['vix']):
                spx_data = pd.read_pickle(processed_files['spx'])
                vix_data = pd.read_pickle(processed_files['vix'])
        
        # If either chain is missing, load and process raw data
        if options_chain is None or options_chain_multi_index is None:
            logger.info("Loading and preprocessing original data files")
            raw_options = pd.read_csv(raw_files['options'], index_col=0, parse_dates=True)
            spx_data = pd.read_csv(raw_files['spx'], index_col=0, parse_dates=True)
            vix_data = pd.read_csv(raw_files['vix'], index_col=0, parse_dates=True)
            
            # Preprocess the data
            options_chain = preprocess_options_data(raw_options)
            spx_data = preprocess_spx_data(spx_data)
            vix_data = preprocess_vix_data(vix_data)
            
            # Create MultiIndex version if needed
            if options_chain_multi_index is None:
                logger.info("Creating MultiIndex structure")
                options_chain_multi_index = options_chain.reset_index().rename(columns={'index': 'date'})
                options_chain_multi_index = options_chain_multi_index.set_index(['date', 'strike']).sort_index()
            
            # Save preprocessed data if requested
            if save_preprocessed:
                if options_chain is not None:
                    options_chain.to_pickle(processed_files['options'])
                options_chain_multi_index.to_pickle(processed_files['chain_multi_index'])
                spx_data.to_pickle(processed_files['spx'])
                vix_data.to_pickle(processed_files['vix'])
                logger.info("Saved preprocessed data files")
        
        logger.info(f"Loaded and preprocessed data:")
        logger.info(f"- Normal options chain: {len(options_chain)} rows")
        logger.info(f"- MultiIndex options chain: {len(options_chain_multi_index)} rows")
        logger.info(f"- SPX data: {len(spx_data)} rows")
        logger.info(f"- VIX data: {len(vix_data)} rows")
        
        return options_chain, options_chain_multi_index, spx_data, vix_data
        
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}")
        raise

def generate_trade_signals(
    spx_data: pd.DataFrame,
    options_chain: pd.DataFrame,
    option_type: OptionType,
    delta_target: float,
    delta_range: Tuple[float, float],
    dte_target: int,
    dte_range: Tuple[int, int],
    start_date: str = None,
    end_date: str = None,
) -> pd.DataFrame:
    """
    Generate trade signals based on the provided parameters. These are not the actual trades,
    but rather potential trades filtered for the desired criteria. The DataFrame should have a 
    pd.DateTime index
    
    Args:
        spx_data: DataFrame containing underlying price data
        options_chain: DataFrame containing options chain data
        option_type: Type of option strategy to trade (PUT or CALL)
        delta_target: Target delta value for the trade
        delta_range: Range of delta values to consider
        dte_target: Target days to expiration for the trade
        dte_range: Range of days to expiration to consider
        start_date: Start date for the trade signals
        end_date: End date for the trade signals
    
    Returns:
        DataFrame containing the generated trade signals
    """

    logger.debug(f'Generating trade signals for {option_type}|{delta_target if delta_target else delta_range}|{dte_target if dte_target else dte_range}|{start_date if start_date else "all"}|{end_date if end_date else "all"}')
    
    # Create a copy of the options chain to avoid modifying the original
    chain_df = options_chain.copy()
    
    # Filter by DATE range if provided
    if start_date:
        start_date = pd.to_datetime(start_date)
        chain_df = chain_df[chain_df.index >= start_date]
    
    if end_date:
        end_date = pd.to_datetime(end_date)
        chain_df = chain_df[chain_df.index <= end_date]
        logger.debug(f'Sorting for date range: {start_date}-{end_date}')
        logger.debug(f'Sample chain of length: {len(chain_df)}')
        logger.debug(chain_df.head())

    # Remove columns that are not needed
    prefix = 'p_' if is_put(option_type) else 'c_'
    cols = chain_df.columns
    needed_cols = [col for col in cols if col.startswith(prefix)]
    needed_cols.extend(['strike', 'dte', 'underlying_last', 'expire_date', 'strike_distance', 'strike_distance_pct'])
    chain_df = chain_df[needed_cols]
    

    # Filter out options with zero or negative bids/asks
    bid_col = f'{prefix}bid'
    ask_col = f'{prefix}ask'
    chain_df = chain_df[
        (chain_df[bid_col] > 0) & 
        (chain_df[ask_col] > 0)
    ]
    
    # Filter out options with unreasonable spreads (50% max)
    chain_df['spread_percent'] = ((chain_df[ask_col] - chain_df[bid_col]) / chain_df[bid_col]) * 100
    chain_df = chain_df[chain_df['spread_percent'] <= 50.0]  # Max 50% spread
    
    logger.debug(f'After spread filtering: {len(chain_df)} options remaining')
    logger.debug(chain_df['spread_percent'].describe())

    # Precompute midpoint price for each row
    chain_df['midpoint_price'] = chain_df.apply(
        lambda row: calculate_midpoint_price(row[bid_col], row[ask_col]),   
        axis=1  
    )
    
    # Filter by DTE based on whether we have a single value or range
    if dte_range:
        dte_mask = (chain_df['dte'] >= dte_range[0]) & (chain_df['dte'] <= dte_range[1])
        chain_df = chain_df[dte_mask]
        logger.debug(chain_df['dte'].describe())
        logger.debug(f'Filtering for dte range: {dte_range}')
        logger.debug(f'Sample chain of length: {len(chain_df)}')
        logger.debug(chain_df.head())
        logger.debug(chain_df['dte'].describe())

    elif dte_target:
        logger.debug(chain_df['dte'].describe())
        dte_mask = abs(chain_df['dte'] - dte_target) < 1
        chain_df = chain_df[dte_mask]
        logger.debug(f'Filtering for dte target: {dte_target}')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())
        logger.debug(chain_df['dte'].describe())

    else:
        logger.error('Need to provide either <dte_target> or <dte_range>')
        raise ValueError
    
    # Filter by delta parameters        
    delta_col = 'p_delta' if is_put(option_type) else 'c_delta'
    logger.debug(f'Initial delta distribution')
    logger.debug(chain_df[delta_col].describe())
    
    if delta_range:
        # Handle range case
        if is_put(option_type):
            min_delta = -abs(delta_range[1])  # More negative (more ITM)
            max_delta = -abs(delta_range[0])  # Less negative (more OTM)
        else:
            min_delta = abs(delta_range[0])  # Less positive (more OTM)
            max_delta = abs(delta_range[1])  # More positive (more ITM)

        logger.debug(chain_df[delta_col].describe())
        logger.debug(f'Filtering for delta range: {min_delta} to {max_delta} for {option_type.value}')
        delta_mask = chain_df[delta_col].between(min_delta, max_delta)
        chain_df = chain_df[delta_mask]
        logger.debug(chain_df[delta_col].describe())

        # Sort by delta value while maintaining the date index
        ascending = is_call(option_type)  # Ascending for calls, descending for puts
        chain_df = chain_df.sort_values(by=[delta_col], ascending=ascending)
        trade_signals = chain_df
        logger.debug(f'Sample chain of length: {len(chain_df)}')
        logger.debug(chain_df.head())

    elif delta_target:
        # Handle target case
        if is_put(option_type):
            # For puts, we want negative deltas
            target = -abs(delta_target)
            # For puts, we want to find options with deltas closest to the target (more negative)
            ascending = False
        else:
            # For calls, we want positive deltas
            target = abs(delta_target)
            # For calls, we want to find options with deltas closest to the target (more positive)
            ascending = True

        logger.debug(f'Filtering for delta target: {target} for {option_type.value}')
        delta_diff = abs(chain_df[delta_col] - target)
        chain_df = chain_df.assign(delta_diff=delta_diff)
        
        # Filter out options that are too far from target delta (20% tolerance)
        max_delta_diff = abs(target) * 0.20  # 20% tolerance
        chain_df = chain_df[chain_df['delta_diff'] <= max_delta_diff]
        
        # Sort by delta difference and delta value while maintaining the date index
        chain_df = chain_df.sort_values(by=['delta_diff', delta_col], ascending=[True, ascending])
        trade_signals = chain_df
        logger.debug(f'Sample chain of length: {len(chain_df)}')
        logger.debug(chain_df.head())
    else:
        logger.error('Need to provide either delta_target or delta_range')
        raise ValueError
    
    logger.info(f"Generated {len(trade_signals)} trade signals")
    logger.info("\nSample of trade signals:")
    logger.info(trade_signals.head())
    
    return trade_signals

def check_data_quality(options_chain, spx_data, vix_data):
    """
    Check data quality for all datasets (options chain, SPX data, and VIX data).
    Verifies required columns exist and checks for missing or invalid values.
    
    Args:
        options_chain: DataFrame containing options chain data
        spx_data: DataFrame containing SPX data
        vix_data: DataFrame containing VIX data
    """
    datasets = {
        'Options Chain': {
            'df': options_chain,
            'required_cols': ['expire_date', 'strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'underlying_last']
        },
        'SPX': {
            'df': spx_data,
            'required_cols': ['close', 'open', 'high', 'low']
        },
        'VIX': {
            'df': vix_data,
            'required_cols': ['close']
        }
    }

    for dataset_name, dataset_info in datasets.items():
        df = dataset_info['df']
        
        logger.debug(f"Type of dataframe: {type(df), df.head()}")
        
        # Skip checking if the DataFrame is a Dask DataFrame
        # if isinstance(df, dd.DataFrame):
        #     logger.info(f"Skipping data quality check for {dataset_name} (Dask DataFrame).")
        #     continue
        if len(df.columns) > 50:
            logger.info("Skipping QA for Dask DataFrame")
            continue

        required_cols = dataset_info['required_cols']
        
        logger.info(f"\nChecking {dataset_name} data quality...")
        
        # Check for missing values in key columns
        logger.info(f"\nMissing values in {dataset_name}:")
        for col in required_cols:
            if col in df.columns:
                missing = df[col].isna().sum()
                percent = (missing / len(df)) * 100 if len(df) > 0 else 0
                logger.info(f"{col}: {missing} missing values ({percent:.2f}%)")
        
        # Check date ranges
        if not df.empty:
            logger.info(f"\n{dataset_name} date range: {df.index.min()} to {df.index.max()}")
        
        # Check for negative or zero values in bid/ask (separately from missing values)
        logger.info("\nZero or negative values (not including NaN):")
        bid_ask_cols = ['p_bid', 'p_ask', 'c_bid', 'c_ask']
        for col in bid_ask_cols:
            if col in df.columns:
                # Count zero values (where the column is not NaN and the value is 0)
                zero_values = ((df[col] == 0) & ~df[col].isna()).sum()
                zero_percent = (zero_values / len(df)) * 100 if len(df) > 0 else 0
                
                # Count negative values (where the column is not NaN and the value is negative)
                negative_values = ((df[col] < 0) & ~df[col].isna()).sum()
                negative_percent = (negative_values / len(df)) * 100 if len(df) > 0 else 0
                
                # Count NaN values separately
                nan_values = df[col].isna().sum()
                nan_percent = (nan_values / len(df)) * 100 if len(df) > 0 else 0
                
                logger.info(f"{col}: {zero_values} zeros ({zero_percent:.2f}%), {negative_values} negative ({negative_percent:.2f}%), {nan_values} NaN ({nan_percent:.2f}%)")
        
        # Sample data
        if not df.empty:
            logger.info("\nSample data:")
            logger.info(df.head(2))
    
    logger.info("\n=== End Data Quality Check ===\n")

def calculate_daily_value(trade: pd.Series, date: pd.Timestamp, options_chain_multi_index: pd.MultiIndex, spx_data: pd.DataFrame, use_spx_close: bool = True):
    """
    Calculate the daily market value of open positions and margin requirements.
    
    Args:
        trade (pd.Series): Trade result containing trade results.
        date (pd.Timestamp): Date to calculate value for.
        options_chain_multi_index (pd.MultiIndex): MultiIndex DataFrame with option chain data.
        spx_data (pd.DataFrame): DataFrame containing SPX closing prices.
        use_spx_close (bool, optional): Whether to use SPX close price (True) or underlying_last from options data (False).
    
    Returns:
        Market value of the position
    """
    try:
        # Check if the date exists in the MultiIndex
        if date not in options_chain_multi_index.index.get_level_values(0):
            # Find the nearest date
            available_dates = options_chain_multi_index.index.get_level_values(0)
            nearest_date = available_dates[available_dates <= date][-1]
            # logger.debug(f"Found nearest date {nearest_date} before target date {date}.")
            date = nearest_date
        
        # Get the price data using MultiIndex
        price_data = options_chain_multi_index.loc[(date, trade.strike)]
        price_data = price_data.loc[price_data['expire_date']==trade.expire_date]

        # Expiration, so use intrinsic value
        if date == trade.expire_date:
            # Get underlying price based on source preference
            if use_spx_close:
                if date not in spx_data.index:
                    logger.error(f"No SPX close price available for {date}")
                    return None
                underlying_price = spx_data.loc[date, 'close']
                # logger.debug(f"Using SPX close price: {underlying_price}")
            else:
                underlying_price = price_data['underlying_last'].iloc[0]
                # logger.debug(f"Using options chain underlying_last: {underlying_price}")

            close = calculate_intrinsic_value(underlying_price, trade.strike, trade.option_type)
            market_value = round(close * 100 * trade.quantity, 2)
            # logger.debug(f'Calculated intrinsic value on date={date} for strike={trade.strike} and value={market_value}')

        # Either MTM daily or early closure, so calculate mid point of bid/ask quote
        else:
            bid_col = 'p_bid' if is_put(trade) else "c_bid"
            ask_col = 'p_ask' if is_put(trade) else "c_ask"
            bid = price_data[bid_col].iloc[0] 
            ask = price_data[ask_col].iloc[0] 
            mid = calculate_midpoint_price(bid, ask)
            if mid is None:
                logger.warning(f"Invalid bid/ask prices on {date} for strike {trade.strike}: bid={bid}, ask={ask}")
                return None
            market_value = round(mid * 100 * trade.quantity, 2)
            # logger.debug(f'Calculated mid value on date={date} for strike={trade.strike}, bid={bid}, ask={ask}, mid={mid}, value={market_value}')
        
        # Validate sign of value according to PositionSide
        try:
            if is_long(trade):
                assert market_value >= 0 
            else:
                assert is_short(trade)
                assert market_value <= 0      
        except AssertionError as e:
            if is_long(trade):
                market_value = abs(market_value)
            else:
                market_value = -abs(market_value)

        return market_value 
    
    except KeyError:
        logger.warning(f"No data for strike {trade.strike} on {date}")
    except Exception as e:
        logger.error(f"Error calculating daily value: {str(e)}")

    return None

def calculate_mtm(start_date, end_date, initial_capital, trade_results, options_chain_multi_index, spx_data, param_str, use_spx_close: bool = True, results_dir="results", leverage: float = 1.0):
    """
    Calculate and save mark-to-market (MTM) data for a backtest.

    Args:
        start_date (str or pd.Timestamp): The start date of the backtest period.
        end_date (str or pd.Timestamp): The end date of the backtest period.
        initial_capital (float): The initial capital for the backtest.
        trade_results (pd.DataFrame): DataFrame containing trade results.
        options_chain_multi_index (pd.MultiIndex): MultiIndex for the options chain data.
        spx_data (pd.DataFrame): DataFrame containing S&P 500 data.
        param_str (str): A string of parameters for the backtest.
        use_spx_close (bool, optional): Flag to use S&P 500 close price. Defaults to True.
        results_dir (str, optional): Directory to save the results. Defaults to "results".
        leverage (float, optional): Leverage factor for the backtest. Defaults to 1.0.
    """
    # Ensure the results directory exists
    os.makedirs(results_dir, exist_ok=True)
    
    # Convert string dates to timestamps if they're strings
    start_date = pd.Timestamp(start_date).normalize() if start_date else options_chain_multi_index.index.get_level_values(0).min()
    initial_end_date = pd.Timestamp(end_date).normalize() if end_date else options_chain_multi_index.index.get_level_values(0).max()
    
    # Find the latest exit date for trades that opened within our range
    latest_exit = initial_end_date
    for trade in trade_results.itertuples():
        trade_start = pd.Timestamp(trade.entry_date).normalize()
        trade_end = pd.Timestamp(trade.exit_date).normalize()
        if start_date <= trade_start <= initial_end_date:  # Trade opened in our range
            latest_exit = max(latest_exit, trade_end)
    
    # Use the later of initial_end_date or latest_exit
    end_date = latest_exit
    
    if initial_end_date != end_date:
        logger.debug(f"Adjusting MTM end date from {initial_end_date} to {end_date} to include all trade exits")
    
    # Initialize tracking variables
    cash = initial_capital
    peak_liquidity = initial_capital  # Change from peak_capital to peak_liquidity
    net_liq = initial_capital  # Change from capital to net_liq
    option_bp = initial_capital     # This tracks available buying power for new trades
    cumulative_pnl = 0              # Track cumulative P&L
    daily_data = []
    
    # Dictionary to track active trades
    active_trades = {}  # (entry_date, strike, option_type) -> {'position_value': value, 'margin_requirement': margin}
    
    # Process each date in the backtest period
    for date in pd.date_range(start=start_date, end=end_date):
        date = pd.Timestamp(date).normalize()
        daily_pnl = 0
        daily_margin_requirement = 0
        daily_position_value = 0
        daily_cash_flow = 0  # Track daily cash flows from premiums
        
        logger.debug(f'Processing date: {date}')
        # First, check for any trades that start on this date
        for _, trade in trade_results.iterrows():
            trade_start = pd.Timestamp(trade.entry_date).normalize()
            trade_end = pd.Timestamp(trade.exit_date).normalize()
            trade_id = (trade.expire_date, trade.strike, trade.option_type)

            # Handle existing trades
            if trade_id in active_trades:
                logger.debug(f'Processing active trade: {trade_id}')
                current_value = calculate_daily_value(trade, date, options_chain_multi_index, spx_data, use_spx_close)
                prev_value = active_trades[trade_id]['position_value']
                
                # Calculate daily P&L for this trade
                daily_pnl += round(current_value - prev_value, 2) if current_value is not None else 0
                logger.debug(f'Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

                # Close trade
                if trade_end == date:
                    logger.debug(f'Closing trade: {trade_id}')

                    # Release margin back to BP for short positions
                    if is_short(trade):
                        logger.debug(f'BP before: {option_bp}')
                        option_bp += active_trades[trade_id]['margin_requirement']
                        logger.debug(f"BP after margin release of {active_trades[trade_id]['margin_requirement']}: {option_bp}")

                    # Validate closing/exit price sign 
                    exit_price = get_signed_exit_price(trade)
                    premium = round(exit_price * 100 * trade.quantity, 2)  # Premium in dollars
                    logger.debug(f'Premium exit: {premium}')
                    logger.debug(f'daily cash effect, before: {daily_cash_flow} | BP {option_bp}')
                    # Accumulate this to cash reserves
                    # daily_cash_flow += premium  # Already signed in the trade
                    # option_bp += premium  # Already signed in the trade
                    daily_cash_flow = round(daily_cash_flow + premium, 2)
                    option_bp = round(option_bp + premium, 2)
                    logger.debug(f'daily cash effect, after: {daily_cash_flow} | BP {option_bp}')

                    del active_trades[trade_id]

                # Update trade
                else:
                    logger.debug(f'Updating existing trade {trade_id}')
                    if current_value is not None:
                        active_trades[trade_id]['position_value'] = current_value
                        daily_position_value = round(daily_position_value + current_value, 2)  # Only add once
                        daily_margin_requirement += active_trades[trade_id]['margin_requirement']
            
            # Handle new trades
            elif trade_start == date:
                logger.debug(f'Opening new trade: {trade_id}')
                # position_value = calculate_daily_value(trade, date, options_chain_multi_index, spx_data, use_spx_close)
                entry_price = get_signed_entry_price(trade)
                position_value = -round(entry_price * 100 * trade.quantity, 2)
                if position_value is not None:
                    active_trades[trade_id] = {
                        'position_value': position_value,
                        'margin_requirement': trade.capital_used
                    }
                    
                    # entry_price = get_signed_entry_price(trade)
                    premium = round(entry_price * 100 * trade.quantity, 2)  # total premium dollars

                    # Accumulate entry price to cash flow (signed based on position side)
                    logger.debug(f'Premium at entry: {premium}')
                    logger.debug(f'Daily cash before: {daily_cash_flow}, BP before: {option_bp}')
                    # daily_cash_flow += premium  # Already signed in the trade
                    # option_bp += premium  # Already signed in the trade
                    daily_cash_flow = round(daily_cash_flow + premium, 2)
                    option_bp = round(option_bp + premium, 2)

                    req_margin = trade.capital_used
                    daily_margin_requirement += req_margin
                    
                    # For short positions, reduce BP
                    if is_short(trade):
                        option_bp = round(option_bp - req_margin / leverage, 2)  # Account for leverage in BP reductio
                        logger.debug(f'Margin reduced for short, BP now: {option_bp}')

                    # Update position value and margin
                    daily_position_value = round(daily_position_value + position_value, 2)
                    logger.debug(f'Daily Position Value: {position_value}')
 
        # Update cumulative P&L
        cumulative_pnl = round(cumulative_pnl + daily_pnl, 2)
        
        # Update cash with any daily premium flows
        # NB: there is no change in daily cash or BP due to unrealized pnl for equity and index options, only for certain futures
        cash  = round(cash + daily_cash_flow, 2)
        
        # Calculate net liquidation value
        net_liq = round(cash + daily_position_value, 2)

        # daily drift persists but final seems ok
        drift = abs(net_liq - (initial_capital + cumulative_pnl))
        if 1 < drift < 5:
            logger.warning(f'FLOATING ERR DRIFT under $5: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')
        else:
            logger.error(f'FLOATING ERR DRIFT above $10: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')

        # Update peak liquidity if net liquidation value is higher
        if net_liq > peak_liquidity:
            peak_liquidity = net_liq
            
        # Calculate drawdown
        drawdown_amount = - max(0, round(peak_liquidity - net_liq, 2))
        drawdown_pct = round(drawdown_amount / peak_liquidity * 100, 2) if peak_liquidity > 0 else 0

        # Calculate ROI metrics
        daily_roi = round(daily_pnl / daily_margin_requirement * 100, 2) if daily_margin_requirement > 0 else 0
        total_roi = round((net_liq - initial_capital) / initial_capital * 100, 2)
        
        # Store daily data with expanded metrics
        daily_data.append({
            'Date': date,
            'Net Liquidity': round(net_liq, 2),
            'BP': round(option_bp, 2),
            'Cash': cash,
            'Position Value': round(daily_position_value, 2),
            'Margin Requirement': round(daily_margin_requirement, 2),
            'Daily P&L': round(daily_pnl, 2),
            'Cumulative P&L': round(cumulative_pnl, 2),
            'Drawdown ($)': round(drawdown_amount, 2),
            'Drawdown (%)': round(drawdown_pct, 2),
            'Daily ROI (%)': daily_roi,
            'Total ROI (%)': total_roi,
            'Active Positions': len(active_trades),
            'Peak Liquidity': round(peak_liquidity, 2),
            'Margin Utilization (%)': round(daily_margin_requirement / initial_capital * 100, 2)
        })
        
        # Log daily summary
        logger.debug(f'Date: {date}')
        logger.debug(f'  Daily P&L: ${daily_pnl:.2f}')
        logger.debug(f'  Cumulative P&L: ${cumulative_pnl:.2f}')
        logger.debug(f'  Daily ROI: {daily_roi:.2f}%')
        logger.debug(f'  Cash: {cash:.2f}')
        logger.debug(f'  Net Liquidity: ${net_liq:.2f}')
        logger.debug(f'  BP: ${option_bp:.2f}')
        logger.debug(f'  Active Positions: {len(active_trades)}')
    
    # Create DataFrame and calculate metrics
    daily_df = pd.DataFrame(daily_data)
    max_drawdown_amount = daily_df['Drawdown ($)'].min()
    max_drawdown_percentage = daily_df['Drawdown (%)'].min()
    
    # Save results
    # mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}.csv")
    # daily_df.to_csv(mtm_csv_path, index=False)
    # logger.info(f"MTM results saved to {mtm_csv_path}")
    
    return daily_df, max_drawdown_amount, max_drawdown_percentage

def pivot_options_chain(options_chain, needed_col):
    """
    Pivot the options chain using Dask for better memory handling.
    """
    # Reduce precision of numeric columns
    float_cols = [
        'strike',
        'p_delta', 'c_delta',
        'p_bid', 'p_ask', 'c_bid', 'c_ask',
        'p_last', 'c_last',
        'p_iv', 'c_iv',
        'underlying_last'
    ]
    
    logger.debug(f"Starting pivot operation with DataFrame of shape {options_chain.shape}")
    logger.debug(f"Memory usage before pivot: {options_chain.memory_usage().sum() / 1024**2:.2f} MB")
    
    try:
        # Get the index name before resetting
        date_col = options_chain.index.name if options_chain.index.name else 'date'
        logger.debug(f"Using date column name: {date_col}")
        
        # Reset index to make the date a column with the specified name
        options_chain = options_chain.reset_index(names=[date_col])
        
        # Ensure required columns exist
        required_cols = ['strike', 'expire_date', date_col]
        missing_cols = [col for col in required_cols if col not in options_chain.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        # Convert date column to categorical type for Dask pivot
        options_chain[date_col] = options_chain[date_col].astype('category')
        
        # Convert to Dask DataFrame
        # logger.debug("Converting to Dask DataFrame")
        # dask_df = dd.from_pandas(options_chain, npartitions=4)
        
        # Log the columns before pivot
        logger.debug(f"Columns before pivot: {options_chain.columns.tolist()}")
        
        # Pivot operation
        logger.debug("Starting pivot table operation")
        pivoted_chain = options_chain.pivot_table(
            index='strike', 
            columns=date_col,
            values=needed_col,
            aggfunc='first'
        )
        
        # logger.debug("Computing final result")
        # result = pivoted_chain.compute()
        
        # Log the final columns
        logger.debug(f"Final columns after pivot: {pivoted_chain.columns.tolist()[:10]}")
        
        logger.debug(f"Pivot completed successfully. Result shape: {pivoted_chain.shape}")
        logger.debug(f"Memory usage after pivot: {pivoted_chain.memory_usage().sum() / 1024**2:.2f} MB")
        
        return pivoted_chain
        
    except Exception as e:
        logger.error(f"Error during pivot operation: {str(e)}")
        raise

def prepare_options_chain(options_chain, path, param_str):
    """
    Prepare the options chain data with memory optimizations.
    """
    
    # Create the pickle file path
    # pickle_path = os.path.join(data_dir, f"pivoted_options_{param_str}.pkl")
    
    if os.path.exists(path):
        logger.debug("Loading pivoted options chain from pickle")
        return pd.read_pickle(path)
    
    # Keep only necessary columns
    needed_cols = [
        'strike', 'expire_date', 'quote_readtime',
        'p_delta', 'c_delta',
        'p_bid', 'p_ask', 'c_bid', 'c_ask',
        'p_last', 'c_last',
        'p_iv', 'c_iv',  # Added IV fields
        'p_size', 'c_size',  # Added size fields
        'underlying_last', 'dte'
    ]
    options_chain = options_chain[needed_cols]
    
    # Use the new pivot_options_chain function
    logger.debug("Pivoting options chain using Dask")
    pivoted_chain = pivot_options_chain(options_chain, needed_cols)
    
    logger.debug(pivoted_chain.head(2))
    # Save to pickle
    logger.debug("Saving pivoted options chain to pickle")
    pivoted_chain.to_pickle(path)
    logger.debug(f"Saved pivoted chain to {path}")
    
    return pivoted_chain



def run_backtest(
    *,
    spx_file_path: str,
    options_chain_file_path: str,
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
    use_preprocessed: bool = True,
    save_preprocessed: bool = True,
    save_trades: bool = True,
    preloaded_data: dict = None,
    log_to_sheets: bool = True,
    max_margin_utilization: float = 0.80,
    leverage: float = 1.0,
    max_positions: int = 1,
    quantity: int = 1,
    # Spread-specific parameters
    spread_type: SpreadType = None,
    legs_config: List[Dict] = None,
    spread_signals: pd.DataFrame = None,  # Pre-generated spread signals
    trade_signals: pd.DataFrame = None,   # Pre-generated trade signals for single legs
) -> pd.DataFrame:
    """
    Execute a backtest of an options trading strategy.
    
    This function can handle both single-leg positions and multi-leg spreads.
    For single-leg positions, provide option_type and position_side.
    For spreads, provide spread_type and legs_config.
    """
    # Determine if this is a spread backtest
    is_spread = spread_type is not None and legs_config is not None
    
    # Validate parameters based on backtest type
    if is_spread:
        if option_type is not None or position_side is not None:
            logger.warning("option_type and position_side are ignored for spread backtests")
        if not legs_config:
            raise ValueError("legs_config must be provided for spread backtests")
    else:
        # Single-leg validation
        if option_type is None or position_side is None:
            raise ValueError("option_type and position_side must be provided for single-leg backtests")
        # Validation for delta and dte parameters
        if delta_target is None and delta_range is None:
            raise ValueError("You must provide either 'delta_target' or 'delta_range'.")
        
        if dte_target is None and dte_range is None:
            raise ValueError("You must provide either 'dte_target' or 'dte_range'.")

    start_time = time.time()
    logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Load data if not preloaded
    if preloaded_data is None:
        data_loading_start = time.time()
        options_chain, options_chain_multi_index, spx_data, vix_data = load_backtest_data(
            data_dir=os.path.dirname(spx_file_path),
            use_preprocessed=use_preprocessed,
            save_preprocessed=save_preprocessed,
            options_file=os.path.basename(options_chain_file_path)
        )
        data_loading_time = time.time() - data_loading_start
        logger.info(f"Data loading and preprocessing completed in {data_loading_time:.2f} seconds")
    else:
        spx_data = preloaded_data['spx_data']
        options_chain = preloaded_data['options_data']
        options_chain_multi_index = preloaded_data['options_data_multi']
        vix_data = preloaded_data['vix_data']
        data_loading_time = 0
        logger.info("Using pre-loaded data")
    
    # Calculate maximum allowed margin based on initial capital and leverage
    max_allowed_margin = initial_capital * max_margin_utilization * leverage
    logger.info(f"Maximum allowed margin: ${max_allowed_margin:.2f} ({max_margin_utilization:.0%} of capital with {leverage}x leverage)")
    logger.info(f"Maximum simultaneous positions: {max_positions}")
    
    # Generate trade signals based on backtest type
    signal_start = time.time()
    
    # All spread types
    if is_spread:
        # Use spread signals if provided, otherwise generate them
        if spread_signals is None:
            trade_signals = generate_spread_signals(
                options_chain=options_chain,
                spread_type=spread_type,
                legs_config=legs_config,
                start_date=start_date,
                end_date=end_date,
                dte_range=dte_range,
                dte_target=dte_target,
                spx_data=spx_data
            )
        else:
            trade_signals = spread_signals
            
        if trade_signals.empty:
            logger.warning("No spread signals generated with the current parameters.")
            return pd.DataFrame()
            
        # Create spread positions using the helper function
        leg_positions = create_spread_positions(
            spread_signals=trade_signals,
            spread_type=spread_type,
            legs_config=legs_config,
            early_close_days=early_close_days,
            quantity=quantity
        )
        
        # Use the generated leg positions as trade signals
        # by converting list of positions to a DataFrame
        if leg_positions:
            trade_signals = pd.DataFrame(leg_positions)
            logger.debug(f'Leg positions created: {trade_signals}')
        else:
            logger.warning("No valid spread positions generated")
            return pd.DataFrame()

    # Single legs
    else:
        # Use trade signals if provided, otherwise generate them
        if trade_signals is not None and not trade_signals.empty:
            logger.debug("Using provided trade signals")
        else:
            # Generate normal single-leg signals
            trade_signals = generate_trade_signals(
                spx_data, 
                options_chain,
                option_type=option_type,
                delta_target=delta_target,
                delta_range=delta_range,
                dte_target=dte_target,
                dte_range=dte_range,
                start_date=start_date,
                end_date=end_date
            )
        
        if trade_signals.empty:
            logger.warning("No trade signals generated with the current parameters.")
            return pd.DataFrame()
        

    # Pre-calculate margin requirements for all signals
    logger.info(f"Calculating margin requirements for trade signals for {quantity} | {option_type if option_type else spread_type} | {delta_target if delta_target else delta_range}")
    
    # Handle all spread types
    if is_spread:
        # Calculate margins per spread group and ensure proper alignment
        margins = trade_signals.groupby('spread_id').apply(calculate_margin_for_spread)
        trade_signals['margin_required'] = trade_signals['spread_id'].map(margins)
        logger.debug(f'Calculated margins for {len(margins)} spread groups')
        logger.debug(f'First few margins: {margins.head()}')

    # Single leg
    else:
        trade_signals['margin_required'] = trade_signals.apply(
            lambda row: calculate_margin(
                row['underlying_last'],
                (row['p_bid'] + row['p_ask']) / 2 if is_put(option_type) else (row['c_bid'] + row['c_ask']) / 2,
                position_side,
                row['strike'],
                option_type
            ) * quantity,  # Multiply by quantity
            axis=1
        )
    
    
    # Filter out trades that would exceed margin limits
    valid_signals = trade_signals[trade_signals['margin_required'] <= max_allowed_margin]
    filtered_count = len(trade_signals) - len(valid_signals)
    if filtered_count > 0:
        logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
        logger.info(f"Average margin requirement for filtered trades: ${trade_signals['margin_required'].mean():.2f}")
        logger.info(f"Maximum margin requirement for filtered trades: ${trade_signals['margin_required'].max():.2f}")

    signal_time = time.time() - signal_start
    logger.info(f"Signal generation completed in {signal_time:.2f} seconds")
    
    # Run backtest with valid signals
    backtest_start = time.time()
    logger.info(f"Running backtest with {len(valid_signals)} valid trades")
    
    # For spread positions, valid_signals contains all legs per row
    trade_results = execute_backtest_trades(
        valid_signals,
        options_chain,
        spx_data,
        option_type=None if is_spread else option_type,  # Option type is in the position for spreads
        position_side=None if is_spread else position_side,  # Position side is in the position for spreads
        initial_capital=initial_capital,
        max_positions=max_positions,
        leverage=leverage,
        early_close_days=early_close_days,
        delta_target=delta_target,
        delta_range=delta_range,
        quantity=quantity
    )
    
    backtest_time = time.time() - backtest_start
    logger.info(f"Backtest execution completed in {backtest_time:.2f} seconds")
    
    if trade_results.empty:
        logger.warning("No trades were executed successfully")
        return pd.DataFrame()
    
    # NB: cumulative_pnl is the sum of realized profits/losses across all closed trades.
    # It starts from initial_capital and accumulates only closed P&L (not unrealized)
    # Thus (option_bp) matches the analytical P&L (cumulative_pnl + initial_capital):
    assert abs(trade_results['capital'].iloc[-1] - trade_results['option_bp'].iloc[-1]) < 1e-6, f'Final capital: {trade_results["capital"].iloc[-1]} | BP: {trade_results["option_bp"].iloc[-1]}'

    # Calculate MTM
    logger.info(f"Running MTM calculation")
    mtm_start = time.time()
    # Generate parameter string based on backtest type
    if is_spread:
        param_str = f"{spread_type.value}_spread_{f'{dte_range[0]}:{dte_range[1]}' if dte_range else dte_target}_{start_date}:{end_date}"
    else:
        param_str = f"{option_type.value}_{position_side.value}_{f'{delta_range[0]}:{delta_range[1]}' if delta_range else delta_target}_{f'{dte_range[0]}:{dte_range[1]}' if dte_range else dte_target}_{start_date}:{end_date}"
        
    daily_df, max_drawdown, max_drawdown_pct = calculate_mtm(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        trade_results=trade_results,
        options_chain_multi_index=options_chain_multi_index,
        spx_data=spx_data,
        param_str=param_str,
        use_spx_close=use_spx_close,
        leverage=leverage
    )
    mtm_time = time.time() - mtm_start
    logger.info(f"MTM calculation completed in {mtm_time:.2f} seconds")
    
    # Add margin utilization metrics to trade_results
    if not trade_results.empty:
        trade_results['margin_utilization'] = round(trade_results['capital_used'] / initial_capital, 2)
        avg_margin_util = trade_results['margin_utilization'].mean()
        max_margin_util = trade_results['margin_utilization'].max()
        logger.info(f"Average margin utilization: {avg_margin_util:.2%}")
        logger.info(f"Maximum margin utilization: {max_margin_util:.2%}")
    
    # Calculate Sharpe Ratio without risk-free rate
    sharpe = None
    if len(trade_results) > 1:
        returns = np.diff(trade_results['capital'].values) / trade_results['capital'].values[:-1]
        if len(returns) > 0 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
            logger.info(f"Sharpe Ratio: {sharpe:.2f}")
    
    # Save summary trade_results to CSV
    if save_trades:
        save_start = time.time()
        results_dir = 'results'
        os.makedirs(results_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save trades
        trades_csv_path = os.path.join(results_dir, f"trades_{param_str}_{timestamp}.csv")
        trade_results.to_csv(trades_csv_path, index=False)
        
        # Save MTM results with same timestamp
        mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}_{timestamp}.csv")
        daily_df.to_csv(mtm_csv_path, index=False)
        
        # Create results file for logging summary
        results_file_path = os.path.join(results_dir, f"backtest_results_{param_str}_{timestamp}.txt")
        with open(results_file_path, 'w') as results_file:
            results_file.write("Backtest Results Summary:\n")
            results_file.write(f"Total trades executed: {len(trade_results)}\n")
            results_file.write(f"Winning trades: {(trade_results['pnl'] > 0).sum()}\n")
            results_file.write(f"Win rate: {((trade_results['pnl'] > 0).sum() / len(trade_results)):.2%}\n")
            results_file.write(f"Total P&L: ${trade_results['cumulative_pnl'].iloc[-1]:,.2f}\n")
            results_file.write(f"Final capital: ${trade_results['capital'].iloc[-1]:,.2f}\n")
            results_file.write(f"Return on initial capital: {(trade_results['capital'].iloc[-1] / initial_capital - 1):.2%}\n")
            results_file.write(f"Average days held: {trade_results['days_held'].mean():.1f}\n")
            results_file.write(f"Average return on margin: {trade_results['return_on_margin'].mean():.2f}%\n")
            results_file.write(f"Maximum drawdown: ${max_drawdown:.2f} ({max_drawdown_pct:.2f}%)\n")

        logger.info(f"Results saved to {results_file_path}")
        
        save_time = time.time() - save_start
        logger.info(f"Trades saved to {trades_csv_path} in {save_time:.2f} seconds")
        logger.info(f"MTM results saved to {mtm_csv_path}")
    
    # Calculate total time
    total_time = time.time() - start_time
    logger.info(f"\nTotal execution time: {total_time:.2f} seconds")
    logger.info(f"Breakdown:")
    logger.info(f"- Data loading: {data_loading_time:.2f} seconds ({data_loading_time/total_time*100:.1f}%)")
    logger.info(f"- Signal generation: {signal_time:.2f} seconds ({signal_time/total_time*100:.1f}%)")
    logger.info(f"- Backtest execution: {backtest_time:.2f} seconds ({backtest_time/total_time*100:.1f}%)")
    logger.info(f"- MTM calculation: {mtm_time:.2f} seconds ({mtm_time/total_time*100:.1f}%)")
    if save_trades:
        logger.info(f"- Results saving: {save_time:.2f} seconds ({save_time/total_time*100:.1f}%)")
    
    # Log combined results
    logger.info(f"\nBacktest Results Summary:")
    logger.info(f"Total trades executed: {len(trade_results)}")
    logger.info(f"Winning trades: {(trade_results['pnl'] > 0).sum()}")
    logger.info(f"Win rate: {((trade_results['pnl'] > 0).sum() / len(trade_results)):.2%}")
    logger.info(f"Total P&L: ${trade_results['cumulative_pnl'].iloc[-1]:,.2f}")
    logger.info(f"Final capital: ${trade_results['capital'].iloc[-1]:,.2f}")
    logger.info(f"Return on initial capital: {(trade_results['capital'].iloc[-1] / initial_capital - 1):.2%}")
    logger.info(f"Average days held: {trade_results['days_held'].mean():.1f}")
    logger.info(f"Average return on margin: {trade_results['return_on_margin'].mean():.2f}%")
    logger.info(f"Maximum drawdown: ${max_drawdown:.2f} ({max_drawdown_pct:.2f}%)")
    
    # Log to Google Sheets if enabled
    if log_to_sheets and not trade_results.empty:
        try:
            log_to_google_sheets(trade_results, param_str, daily_df)
        except Exception as e:
            logger.error(f"Failed to log to Google Sheets: {str(e)}")

    # Final assertion
    assert abs(trade_results['option_bp'].iloc[-1] - daily_df['BP'].iloc[-1]) < 1e-6, f'Final trade BP: {trade_results["option_bp"].iloc[-1]} | MTM BP: {daily_df["BP"].iloc[-1]}'
    assert abs(daily_df['BP'].iloc[-1] - daily_df['Cash'].iloc[-1]) < 1e-6, f'MTM BP: {daily_df["BP"].iloc[-1]} | Cash: {daily_df["Cash"].iloc[-1]}'
    assert abs(daily_df['Net Liquidity'].iloc[-1] - daily_df['Cash'].iloc[-1]) < 1e-6, f'MTM Net Liquidity: {daily_df["Net Liquidity"].iloc[-1]} | Cash: {daily_df["Cash"].iloc[-1]}'
    
    return trade_results

def check_date_presence(pivoted_chain, date_to_check):
    """
    Check if a specific date is present in the pivoted DataFrame columns.
    
    Args:
        pivoted_chain: The pivoted DataFrame with MultiIndex columns.
        date_to_check: The date to check for presence in the columns.
    
    Returns:
        bool: True if the date is present, False otherwise.
    """
    # Ensure the date is in the correct format
    date_to_check = pd.to_datetime(date_to_check).normalize()
    
    # Access the second level of the MultiIndex (assuming it's the date)
    date_level = pivoted_chain.columns.levels[1]
    
    # Check if the date is present
    is_present = date_to_check in date_level
    logger.info(f"Date {date_to_check} presence: {is_present}")
    return is_present

def calculate_net_liq(cash: float, open_positions: List['Position']) -> float:
    """
    Calculate the net liquidity based on cash and open positions.
    
    Args:
        cash: Current cash available in the account
        open_positions: List of open positions
    
    Returns:
        Net liquidity value
    """
    total_value = cash
    for position in open_positions:
        # Calculate market value of each position
        market_value = calculate_intrinsic_value(position['underlying_entry'], position['strike'], position['option_type'])
        total_value += market_value * 100  # Convert to dollars
    
    return total_value

def generate_spread_signals(
    options_chain: pd.DataFrame,
    spread_type: SpreadType,
    legs_config: List[Dict],
    start_date: str = None,
    end_date: str = None,
    dte_range: Tuple[int, int] = None,
    dte_target: int = None,
    spx_data: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Generate trade signals for option spreads by pairing legs according to the specified spread type.
    
    Args:
        options_chain: DataFrame containing options chain data
        spread_type: Type of spread to generate
        legs_config: List of configurations for each leg of the spread
            Each leg config should have:
            - option_type: OptionType for this leg
            - position_side: PositionSide for this leg
            - delta_target or delta_range: Delta criteria for this leg
            - ratio: Quantity ratio for this leg (default 1)
        start_date: Start date for the trade signals
        end_date: End date for the trade signals
        dte_range: Range of days to expiration to consider
        dte_target: Target days to expiration for the trade
        spx_data: DataFrame containing SPX price data (optional)
    
    Returns:
        DataFrame containing the generated spread signals with legs paired by date
    """
    logger.info(f"Generating {spread_type.value} spread signals...")
    
    if spread_type == SpreadType.NONE:
        raise ValueError("Use generate_trade_signals for single-leg positions")
    
    # Generate signals for each leg separately
    leg_signals = []
    for i, leg_config in enumerate(legs_config):
        option_type = leg_config['option_type']
        position_side = leg_config['position_side']
        delta_target = leg_config.get('delta_target')
        delta_range = leg_config.get('delta_range')
        
        # Ensure either delta_target or delta_range is provided
        if delta_target is None and delta_range is None:
            logger.error(f"Leg {i+1} must have either delta_target or delta_range specified")
            return pd.DataFrame()
        
        # Filter options chain for the 
        leg_df = generate_trade_signals(
            spx_data=spx_data,  # Pass SPX data if available
            options_chain=options_chain,
            option_type=option_type,
            delta_target=delta_target,
            delta_range=delta_range,
            dte_target=dte_target,
            dte_range=dte_range,
            start_date=start_date,
            end_date=end_date,
        )
        
        if leg_df.empty:
            logger.warning(f"No signals generated for leg {i+1} with config: {leg_config}")
            return pd.DataFrame()
        
        # Store the index name before adding columns
        index_name = leg_df.index.name
        
        # Add leg-specific columns
        leg_df['leg_number'] = i + 1
        leg_df['position_side'] = position_side.value if isinstance(position_side, Enum) else position_side
        leg_df['option_type'] = option_type.value if isinstance(option_type, Enum) else option_type
        leg_df['leg_ratio'] = leg_config.get('ratio', 1)
        leg_df['delta_target'] = delta_target
        if delta_range:
            leg_df['delta_range_min'] = delta_range[0]
            leg_df['delta_range_max'] = delta_range[1]
        
        # Restore the index name
        leg_df.index.name = index_name
        
        leg_signals.append(leg_df)
    
    # No valid signals for one or more legs
    if any(df.empty for df in leg_signals):
        logger.warning("One or more legs returned no signals")
        return pd.DataFrame()
    
    # Create spread signals based on the spread type
    if spread_type == SpreadType.VERTICAL:
        return _pair_vertical_spread_legs(leg_signals, spread_type)
    elif spread_type == SpreadType.CALENDAR:
        return _pair_calendar_spread_legs(leg_signals, spread_type)
    elif spread_type == SpreadType.DIAGONAL:
        return _pair_diagonal_spread_legs(leg_signals, spread_type)
    elif spread_type == SpreadType.BUTTERFLY:
        return _pair_butterfly_spread_legs(leg_signals, spread_type)
    elif spread_type == SpreadType.IRON_CONDOR:
        return _pair_iron_condor_spread_legs(leg_signals, spread_type)
    else:
        raise ValueError(f"Unsupported spread type: {spread_type}")

def _pair_vertical_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
    """
    Pair legs for vertical spreads (same expiration, different strikes).
    
    Args:
        leg_signals: List of DataFrames containing signals for each leg
        spread_type: Type of spread being created
    
    Returns:
        DataFrame with paired spread signals
    """
    if len(leg_signals) != 2:
        raise ValueError(f"Vertical spreads require exactly 2 legs, got {len(leg_signals)}")
    
    logger.debug("Pairing vertical spread legs...")
    
    # Extract the two legs
    leg1 = leg_signals[0].copy()
    leg2 = leg_signals[1].copy()
    
    # Convert index to datetime if it's not already
    leg1.index = pd.to_datetime(leg1.index)
    leg2.index = pd.to_datetime(leg2.index)
    
    # Store index name and ensure it's not None
    index_name = leg1.index.name or 'date'
    leg1.index.name = index_name
    leg2.index.name = index_name
    
    # Reset index to make date a column
    leg1 = leg1.reset_index()
    leg2 = leg2.reset_index()
    
    # Rename columns to distinguish between legs
    leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name and col != "expire_date"}
    leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name and col != "expire_date"}
    
    leg1 = leg1.rename(columns=leg1_cols)
    leg2 = leg2.rename(columns=leg2_cols)
    
    # Merge on date and expiration date to ensure the legs are for the same expiration 
    # and same trading day
    paired = pd.merge(
        leg1,
        leg2,
        on=[index_name, "expire_date"],
        how="inner"
    )
    logger.debug(f"Paired vertical spread legs: {paired.head()}")
    
    # Filter for valid vertical spread criteria
    # For example, ensure the strikes are different
    if len(paired) > 0:
        if spread_type == SpreadType.VERTICAL:
            paired = paired[paired["leg1_strike"] != paired["leg2_strike"]]
            
            # For put vertical spreads, leg1 strike should be higher than leg2 strike for a credit spread
            if is_put(leg_signals[0].iloc[0]["option_type"]) and is_short(leg_signals[0].iloc[0]["position_side"]):
                paired = paired[paired["leg1_strike"] > paired["leg2_strike"]]
            # For call vertical spreads, leg1 strike should be lower than leg2 strike for a credit spread
            elif is_call(leg_signals[0].iloc[0]["option_type"]) and is_short(leg_signals[0].iloc[0]["position_side"]):
                paired = paired[paired["leg1_strike"] < paired["leg2_strike"]]
    
    # Add spread information
    paired["spread_type"] = spread_type.value
    
    # Calculate spread metrics
    paired["spread_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
    
    # Calculate spread price (add code to adjust based on position side)
    # For a credit spread, we want to sell the first leg and buy the second leg
    paired["leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
            row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
        ),
        axis=1
    )
    
    paired["leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
            row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
        ),
        axis=1
    )
    
    # Calculate net spread price (credit if positive, debit if negative)
    # For credit spreads (short first leg, long second leg)
    if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
        paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
    # For debit spreads (long first leg, short second leg)
    else:
        paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
    
    # Set the index back to the date column
    paired = paired.set_index(index_name)
    
    logger.debug(f"Paired {len(paired)} valid vertical spreads")
    logger.debug(paired.head())
    
    return paired

def _pair_calendar_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
    """
    Pair legs for calendar spreads (same strike, different expirations).
    
    Args:
        leg_signals: List of DataFrames containing signals for each leg
        spread_type: Type of spread being created
    
    Returns:
        DataFrame with paired spread signals
    """
    if len(leg_signals) != 2:
        raise ValueError(f"Calendar spreads require exactly 2 legs, got {len(leg_signals)}")
    
    logger.debug("Pairing calendar spread legs...")
    
    # Extract the two legs
    leg1 = leg_signals[0].copy()  # Front month (near-term expiration)
    leg2 = leg_signals[1].copy()  # Back month (far-term expiration)
    
    # Convert index to datetime if it's not already
    leg1.index = pd.to_datetime(leg1.index)
    leg2.index = pd.to_datetime(leg2.index)
    
    # Store index name and ensure it's not None
    index_name = leg1.index.name or 'date'
    leg1.index.name = index_name
    leg2.index.name = index_name
    
    # Reset index to make date a column
    leg1 = leg1.reset_index()
    leg2 = leg2.reset_index()
    
    # Rename columns to distinguish between legs
    leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name}
    leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name}
    
    leg1 = leg1.rename(columns=leg1_cols)
    leg2 = leg2.rename(columns=leg2_cols)
    
    # Merge on date and strike to ensure the legs are for the same strike
    # and same trading day but different expirations
    paired = pd.merge(
        leg1,
        leg2,
        on=[index_name],
        how="inner"
    )
    logger.debug(f"Paired calendar spread legs: {paired.head()}")
    
    # Filter for valid calendar spread criteria
    # Ensure strikes are the same
    paired = paired[paired["leg1_strike"] == paired["leg2_strike"]]
    
    # Ensure expirations are different and in the correct order
    paired = paired[paired["leg1_expire_date"] < paired["leg2_expire_date"]]
    
    # Add spread information
    paired["spread_type"] = spread_type.value
    paired["time_width"] = paired.apply(lambda row: pd.Timedelta(row["leg2_expire_date"] - row["leg1_expire_date"]).days, axis=1)
    
    # Calculate leg prices
    paired["leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
            row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
        ),
        axis=1
    )
    
    paired["leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
            row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
        ),
        axis=1
    )
    
    # Calculate net spread price (usually a debit for a standard calendar)
    # For standard calendar spreads (short front month, long back month)
    if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
        paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
    # For reverse calendar spreads (long front month, short back month)
    else:
        paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
    
    # Set the index back to the date column
    paired = paired.set_index(index_name)
    
    logger.debug(f"Paired {len(paired)} valid calendar spreads")
    
    return paired

def _pair_diagonal_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
    """
    Pair legs for diagonal spreads (different strikes, different expirations).
    
    Args:
        leg_signals: List of DataFrames containing signals for each leg
        spread_type: Type of spread being created
    
    Returns:
        DataFrame with paired spread signals
    """
    if len(leg_signals) != 2:
        raise ValueError(f"Diagonal spreads require exactly 2 legs, got {len(leg_signals)}")
    
    logger.debug("Pairing diagonal spread legs...")
    
    # Extract the two legs
    leg1 = leg_signals[0].copy()  # Front month, first strike
    leg2 = leg_signals[1].copy()  # Back month, second strike
    
    # Convert index to datetime if it's not already
    leg1.index = pd.to_datetime(leg1.index)
    leg2.index = pd.to_datetime(leg2.index)
    
    # Store index name and ensure it's not None
    index_name = leg1.index.name or 'date'
    leg1.index.name = index_name
    leg2.index.name = index_name
    
    # Reset index to make date a column
    leg1 = leg1.reset_index()
    leg2 = leg2.reset_index()
    
    # Rename columns to distinguish between legs
    leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name}
    leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name}
    
    leg1 = leg1.rename(columns=leg1_cols)
    leg2 = leg2.rename(columns=leg2_cols)
    
    # Merge on date to ensure the legs are for the same trading day
    paired = pd.merge(
        leg1,
        leg2,
        on=[index_name],
        how="inner"
    )
    logger.debug(f"Paired diagonal spread legs: {paired.head()}")
    
    # Filter for valid diagonal spread criteria
    # Ensure expirations are different and in the correct order
    paired = paired[paired["leg1_expire_date"] < paired["leg2_expire_date"]]
    
    # Add spread information
    paired["spread_type"] = spread_type.value
    paired["time_width"] = paired.apply(lambda row: pd.Timedelta(row["leg2_expire_date"] - row["leg1_expire_date"]).days, axis=1)
    paired["strike_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
    
    # Calculate leg prices
    paired["leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
            row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
        ),
        axis=1
    )
    
    paired["leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
            row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
        ),
        axis=1
    )
    
    # Calculate net spread price
    # For standard diagonal spreads (short front month, long back month)
    if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
        paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
    # For reverse diagonal spreads (long front month, short back month)
    else:
        paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
    
    # Set the index back to the date column
    paired = paired.set_index(index_name)
    
    logger.debug(f"Paired {len(paired)} valid diagonal spreads")
    
    return paired

def _pair_butterfly_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
    """
    Pair legs for butterfly spreads (3 strikes, same expiration).
    
    Args:
        leg_signals: List of DataFrames containing signals for each leg
        spread_type: Type of spread being created
    
    Returns:
        DataFrame with paired spread signals
    """
    if len(leg_signals) != 3:
        raise ValueError(f"Butterfly spreads require exactly 3 legs, got {len(leg_signals)}")
    
    logger.debug("Pairing butterfly spread legs...")
    
    # Extract the three legs
    leg1 = leg_signals[0].copy().reset_index()  # Lower strike
    leg2 = leg_signals[1].copy().reset_index()  # Middle strike (2x quantity)
    leg3 = leg_signals[2].copy().reset_index()  # Higher strike
    
    # Convert date columns to pandas Timestamps
    leg1['date'] = pd.to_datetime(leg1['date'])
    leg2['date'] = pd.to_datetime(leg2['date'])
    leg3['date'] = pd.to_datetime(leg3['date'])
    
    # Set index name to make it clear
    leg1 = leg1.rename(columns={"index": "date"})
    leg2 = leg2.rename(columns={"index": "date"})
    leg3 = leg3.rename(columns={"index": "date"})
    
    # Rename columns to distinguish between legs
    leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != "date" and col != "expire_date"}
    leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != "date" and col != "expire_date"}
    leg3_cols = {col: f"leg3_{col}" for col in leg3.columns if col != "date" and col != "expire_date"}
    
    leg1 = leg1.rename(columns=leg1_cols)
    leg2 = leg2.rename(columns=leg2_cols)
    leg3 = leg3.rename(columns=leg3_cols)
    
    # Merge on date and expiration to ensure all legs are for the same expiration
    # and same trading day
    paired = pd.merge(leg1, leg2, on=["date", "expire_date"], how="inner")
    paired = pd.merge(paired, leg3, on=["date", "expire_date"], how="inner")
    logger.debug(f"Paired butterfly spread legs: {paired.head()}")
    
    # Filter for valid butterfly spread criteria
    if len(paired) > 0:
        # Calculate differences between strikes
        paired["diff1"] = paired["leg2_strike"] - paired["leg1_strike"]
        paired["diff2"] = paired["leg3_strike"] - paired["leg2_strike"]
        
        # Keep only rows where the differences are equal (or very close)
        paired = paired[abs(paired["diff1"] - paired["diff2"]) < 0.01]
        
        # Ensure strikes are in ascending order
        paired = paired[
            (paired["leg1_strike"] < paired["leg2_strike"]) & 
            (paired["leg2_strike"] < paired["leg3_strike"])
        ]
    
    # Add spread information
    paired["spread_type"] = spread_type.value
    paired["wing_width"] = paired["diff1"]  # Width between strikes
    
    # Calculate leg prices
    paired["leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
            row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
        ),
        axis=1
    )
    
    paired["leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
            row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
        ),
        axis=1
    )
    
    paired["leg3_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["leg3_p_bid"] if is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_bid"],
            row["leg3_p_ask"] if is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_ask"]
        ),
        axis=1
    )
    
    # Calculate net spread price
    # Long butterfly: buy wing options, sell 2x middle option
    if is_long(leg_signals[0].iloc[0]["position_side"]):
        paired["spread_price"] = paired["leg1_price"] - 2 * paired["leg2_price"] + paired["leg3_price"]
    # Short butterfly: sell wing options, buy 2x middle option
    else:
        paired["spread_price"] = 2 * paired["leg2_price"] - paired["leg1_price"] - paired["leg3_price"]
    
    logger.debug(f"Paired {len(paired)} valid butterfly spreads")
    
    return paired

def _pair_iron_condor_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
    """
    Pair legs for iron condor spreads (4 strikes, same expiration).
    
    Args:
        leg_signals: List of DataFrames containing signals for each leg
        spread_type: Type of spread being created
    
    Returns:
        DataFrame with paired spread signals
    """
    if len(leg_signals) != 4:
        raise ValueError(f"Iron condor spreads require exactly 4 legs, got {len(leg_signals)}")
    
    logger.debug("Pairing iron condor spread legs...")
    
    # Extract the four legs
    put_leg1 = leg_signals[0].copy().reset_index()  # Lower put strike (long)
    put_leg2 = leg_signals[1].copy().reset_index()  # Higher put strike (short)
    call_leg1 = leg_signals[2].copy().reset_index()  # Lower call strike (short)
    call_leg2 = leg_signals[3].copy().reset_index()  # Higher call strike (long)
    
    # Convert date columns to pandas Timestamps
    put_leg1['date'] = pd.to_datetime(put_leg1['date'])
    put_leg2['date'] = pd.to_datetime(put_leg2['date'])
    call_leg1['date'] = pd.to_datetime(call_leg1['date'])
    call_leg2['date'] = pd.to_datetime(call_leg2['date'])
    
    # Set index name to make it clear
    put_leg1 = put_leg1.rename(columns={"index": "date"})
    put_leg2 = put_leg2.rename(columns={"index": "date"})
    call_leg1 = call_leg1.rename(columns={"index": "date"})
    call_leg2 = call_leg2.rename(columns={"index": "date"})
    
    # Rename columns to distinguish between legs
    put_leg1_cols = {col: f"put_leg1_{col}" for col in put_leg1.columns if col != "date" and col != "expire_date"}
    put_leg2_cols = {col: f"put_leg2_{col}" for col in put_leg2.columns if col != "date" and col != "expire_date"}
    call_leg1_cols = {col: f"call_leg1_{col}" for col in call_leg1.columns if col != "date" and col != "expire_date"}
    call_leg2_cols = {col: f"call_leg2_{col}" for col in call_leg2.columns if col != "date" and col != "expire_date"}
    
    put_leg1 = put_leg1.rename(columns=put_leg1_cols)
    put_leg2 = put_leg2.rename(columns=put_leg2_cols)
    call_leg1 = call_leg1.rename(columns=call_leg1_cols)
    call_leg2 = call_leg2.rename(columns=call_leg2_cols)
    
    # Merge on date and expiration to ensure all legs are for the same expiration
    # and same trading day
    paired = pd.merge(put_leg1, put_leg2, on=["date", "expire_date"], how="inner")
    paired = pd.merge(paired, call_leg1, on=["date", "expire_date"], how="inner")
    paired = pd.merge(paired, call_leg2, on=["date", "expire_date"], how="inner")
    logger.debug(f"Paired iron condor spread legs: {paired.head()}")
    
    # Filter for valid iron condor spread criteria
    if len(paired) > 0:
        # Ensure strikes are in the correct order
        paired = paired[
            (paired["put_leg1_strike"] < paired["put_leg2_strike"]) &
            (paired["put_leg2_strike"] < paired["call_leg1_strike"]) &
            (paired["call_leg1_strike"] < paired["call_leg2_strike"])
        ]
    
    # Add spread information
    paired["spread_type"] = spread_type.value
    paired["put_width"] = paired["put_leg2_strike"] - paired["put_leg1_strike"]
    paired["call_width"] = paired["call_leg2_strike"] - paired["call_leg1_strike"]
    paired["middle_width"] = paired["call_leg1_strike"] - paired["put_leg2_strike"]
    
    # Calculate leg prices
    paired["put_leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["put_leg1_p_bid"],
            row["put_leg1_p_ask"]
        ),
        axis=1
    )
    
    paired["put_leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["put_leg2_p_bid"],
            row["put_leg2_p_ask"]
        ),
        axis=1
    )
    
    paired["call_leg1_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["call_leg1_c_bid"],
            row["call_leg1_c_ask"]
        ),
        axis=1
    )
    
    paired["call_leg2_price"] = paired.apply(
        lambda row: calculate_midpoint_price(
            row["call_leg2_c_bid"],
            row["call_leg2_c_ask"]
        ),
        axis=1
    )
    
    # Calculate spread price - assuming standard iron condor (sell the middle strikes, buy the wings)
    # Credit from put vertical spread
    put_spread_price = paired["put_leg2_price"] - paired["put_leg1_price"]
    # Credit from call vertical spread
    call_spread_price = paired["call_leg1_price"] - paired["call_leg2_price"]
    # Total credit from iron condor
    paired["spread_price"] = put_spread_price + call_spread_price
    
    logger.debug(f"Paired {len(paired)} valid iron condor spreads")
    
    return paired

# def run_spread_backtest(
#     *,
#     spx_file_path: str,
#     options_chain_file_path: str,
#     spread_type: SpreadType,
#     legs_config: List[Dict],
#     spread_signals: Optional[pd.DataFrame] = None,
#     use_spx_close: bool = False,
#     start_date: str = None,
#     end_date: str = None,
#     initial_capital: float = 100000,
#     early_close_days: int = None,
#     use_preprocessed: bool = True,
#     save_preprocessed: bool = True,
#     save_trades: bool = True,
#     preloaded_data: dict = None,
#     log_to_sheets: bool = True,
#     max_margin_utilization: float = 0.80,
#     leverage: float = 1.0,
#     max_positions: int = 1,
#     dte_range: Tuple[int, int] = None,
#     dte_target: int = None,
# ) -> pd.DataFrame:
#     """
#     Execute a backtest for spread trading strategies.
    
#     Args:
#         spx_file_path: Path to the SPX data file
#         options_chain_file_path: Path to the options chain data file
#         spread_type: Type of spread to trade
#         legs_config: Configuration for each leg of the spread
#         spread_signals: Pre-generated spread signals (optional)
#         use_spx_close: Whether to use SPX close price for valuations
#         start_date: Start date for the backtest
#         end_date: End date for the backtest
#         initial_capital: Initial capital for the backtest
#         early_close_days: Days before expiration to close positions
#         use_preprocessed: Whether to use preprocessed data
#         save_preprocessed: Whether to save preprocessed data
#         save_trades: Whether to save trade results
#         preloaded_data: Pre-loaded data for the backtest
#         log_to_sheets: Whether to log results to Google Sheets
#         max_margin_utilization: Maximum margin utilization
#         leverage: Leverage multiplier
#         max_positions: Maximum number of positions allowed
#         dte_range: Range of days to expiration to consider
#         dte_target: Target days to expiration
        
#     Returns:
#         DataFrame of trade results
#     """
    
#     start_time = time.time()
#     logger.info(f"Starting spread backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
#     # Load data if not preloaded
#     if preloaded_data is None:
#         data_loading_start = time.time()
#         options_chain, options_chain_multi_index, spx_data, vix_data = load_backtest_data(
#             data_dir=os.path.dirname(spx_file_path),
#             use_preprocessed=use_preprocessed,
#             save_preprocessed=save_preprocessed,
#             options_file=os.path.basename(options_chain_file_path)
#         )
#         data_loading_time = time.time() - data_loading_start
#         logger.info(f"Data loading and preprocessing completed in {data_loading_time:.2f} seconds")
#     else:
#         spx_data = preloaded_data['spx_data']
#         options_chain = preloaded_data['options_data']
#         options_chain_multi_index = preloaded_data['options_data_multi']
#         vix_data = preloaded_data['vix_data']
#         data_loading_time = 0
#         logger.info("Using pre-loaded data")
    
#     # Generate spread signals if not provided
#     if spread_signals is None:
#         signal_start = time.time()
#         spread_signals = generate_spread_signals(
#             options_chain=options_chain,
#             spread_type=spread_type,
#             legs_config=legs_config,
#             start_date=start_date,
#             end_date=end_date,
#             dte_range=dte_range,
#             dte_target=dte_target,
#             spx_data=spx_data
#         )
#         signal_time = time.time() - signal_start
#         logger.info(f"Spread signal generation completed in {signal_time:.2f} seconds")
    
#     if spread_signals.empty:
#         logger.warning("No spread signals generated with the current parameters.")
#         return pd.DataFrame()
    
#     # Execute the backtest
#     # For now, we'll use a simplified approach that treats each leg separately
#     # Future iterations could have more sophisticated spread-specific logic
    
#     trade_results_list = []
#     spread_counter = 1
    
#     # Process spread signals and create individual leg positions
#     for _, spread_signal in spread_signals.iterrows():
#         spread_date = spread_signal.name if hasattr(spread_signal, 'name') else spread_signal['date']
        
#         # Create positions for each leg of the spread
#         leg_positions = []
#         for i, leg_config in enumerate(legs_config):
#             leg_number = i + 1
#             leg_prefix = f"leg{leg_number}_"
            
#             # Extract leg-specific data from the spread signal
#             leg_strike = spread_signal[f"{leg_prefix}strike"]
#             leg_option_type = leg_config['option_type']
#             leg_position_side = leg_config['position_side']
#             leg_quantity = leg_config.get('ratio', 1)
            
#             # Calculate days to expiration properly
#             if f"{leg_prefix}dte" in spread_signal:
#                 dte_value = spread_signal[f"{leg_prefix}dte"]
#             else:
#                 # Calculate dte using proper timedelta operations
#                 delta = pd.Timedelta(spread_signal['expire_date'] - spread_date)
#                 dte_value = delta.days
            
#             # Create a simplified signal for this leg
#             leg_signal = pd.Series({
#                 'strike': leg_strike,
#                 'expire_date': spread_signal['expire_date'],
#                 'underlying_last': spread_signal.get(f"{leg_prefix}underlying_last", spread_signal.get('underlying_last')),
#                 'p_bid': spread_signal.get(f"{leg_prefix}p_bid", 0),
#                 'p_ask': spread_signal.get(f"{leg_prefix}p_ask", 0),
#                 'c_bid': spread_signal.get(f"{leg_prefix}c_bid", 0),
#                 'c_ask': spread_signal.get(f"{leg_prefix}c_ask", 0),
#                 'p_delta': spread_signal.get(f"{leg_prefix}p_delta", 0),
#                 'c_delta': spread_signal.get(f"{leg_prefix}c_delta", 0),
#                 'dte': dte_value,
#             }, name=spread_date)
            
#             # Create the leg position
#             position = create_trade_from_signal(
#                 leg_signal,
#                 leg_quantity,
#                 leg_option_type,
#                 leg_position_side,
#                 leg_config.get('delta_target'),  # Pass the leg's delta_target
#                 spread_date,
#                 early_close_days,
#                 leg_config.get('delta_range')    # Pass the leg's delta_range
#             )
            
#             if position:
#                 # Add spread-specific information
#                 position['spread_type'] = spread_type.value
#                 position['spread_id'] = spread_counter
#                 position['leg_number'] = leg_number
#                 position['leg_ratio'] = leg_quantity
                
#                 leg_positions.append(position)
#             else:
#                 # If any leg can't be created, skip this spread
#                 logger.warning(f"Could not create leg {leg_number} for spread on {spread_date}")
#                 break
        
#         # If all legs were created, add to the list of positions to trade
#         if len(leg_positions) == len(legs_config):
#             trade_results_list.extend(leg_positions)
#             spread_counter += 1
    
#     # Execute the backtest with the leg positions
#     # This will need additional logic in the execute_backtest_trades function
#     # to handle the spread logic correctly
    
#     # For now, this is a placeholder
#     # Replace with spread-specific backtest execution when implemented
#     # trade_results = execute_backtest_trades_for_spreads(...)
    
#     logger.info(f"Created {len(trade_results_list)} leg positions for {spread_counter-1} spreads")
    
#     # Calculate total time
#     total_time = time.time() - start_time
#     logger.info(f"\nTotal execution time: {total_time:.2f} seconds")
    
#     # Return placeholder empty DataFrame
#     # Replace with actual results when spread execution is implemented
#     return pd.DataFrame(trade_results_list)

def create_spread_positions(
    spread_signals: pd.DataFrame,
    spread_type: SpreadType,
    legs_config: List[Dict],
    early_close_days: Optional[int] = None,
    quantity: int = 1
) -> List['Position']:
    """
    Create individual leg positions from spread signals.
    
    Args:
        spread_signals: DataFrame containing the spread signals
        spread_type: Type of spread being created
        legs_config: Configuration for each leg of the spread
        early_close_days: Optional days before expiration to close positions
        quantity: Base quantity multiplier for all legs
        
    Returns:
        List of Position objects representing individual legs of spreads
    """
    leg_positions = []
    spread_counter = 1
    
    # Check if we have any valid spread signals
    if spread_signals.empty:
        logger.warning(f"No valid spread signals to process")
        return leg_positions
    
    logger.debug(f'Creating spread positions for {spread_type}')
    
    # Process spread signals to create individual leg positions
    for spread_signal in spread_signals.itertuples():

        # logger.debug(f'Processing spread signal: {spread_signal}')

        # Convert spread_date to pandas Timestamp
        # spread_date = pd.to_datetime(spread_signal.name if hasattr(spread_signal, 'name') else spread_signal['date'])
        spread_date = spread_signal.Index
        # Create positions for each leg of the spread
        spread_legs = []
        for i, leg_config in enumerate(legs_config):
            leg_number = i + 1
            leg_prefix = f"leg{leg_number}_"
            strike_attr = f"{leg_prefix}strike" 

            # Ensure all required leg fields exist
            if not hasattr(spread_signal, strike_attr):
                logger.warning(f"Missing strike for leg {leg_number} in spread signal")
                break
                
            # Extract leg-specific data from the spread signal
            try:
                leg_strike = getattr(spread_signal, strike_attr)
                leg_option_type = leg_config['option_type']
                leg_position_side = leg_config['position_side']
                leg_quantity = leg_config.get('ratio', 1) * quantity  # Apply the base quantity
                
                # Calculate days to expiration properly
                dte_atrr = f"{leg_prefix}dte"
                if hasattr(spread_signal, dte_atrr):
                    dte_value = getattr(spread_signal, dte_atrr)
                else:
                    # Calculate dte using proper timedelta operations
                    expire_date = pd.to_datetime(spread_signal.expire_date)
                    delta = pd.Timedelta(expire_date - spread_date)
                    dte_value = delta.days
                
                # Create a simplified signal for this leg
                # leg_signal = pd.Series({
                #     'strike': leg_strike,
                #     'expire_date': pd.to_datetime(spread_signal['expire_date']),
                #     'underlying_last': spread_signal.get(f"{leg_prefix}underlying_last", spread_signal.get('underlying_last')),
                #     'p_bid': spread_signal.get(f"{leg_prefix}p_bid", 0),
                #     'p_ask': spread_signal.get(f"{leg_prefix}p_ask", 0),
                #     'c_bid': spread_signal.get(f"{leg_prefix}c_bid", 0),
                #     'c_ask': spread_signal.get(f"{leg_prefix}c_ask", 0),
                #     'p_delta': spread_signal.get(f"{leg_prefix}p_delta", 0),
                #     'c_delta': spread_signal.get(f"{leg_prefix}c_delta", 0),
                #     'dte': dte_value,
                # }, name=spread_date)
                leg_signal = pd.Series({
                    'strike': leg_strike,
                    'expire_date': pd.to_datetime(getattr(spread_signal, 'expire_date')),
                    'underlying_last': getattr(spread_signal, f"{leg_prefix}underlying_last", 
                                               getattr(spread_signal, 'underlying_last', np.nan)),
                    'p_bid': getattr(spread_signal, f"{leg_prefix}p_bid", 0),
                    'p_ask': getattr(spread_signal, f"{leg_prefix}p_ask", 0),
                    'c_bid': getattr(spread_signal, f"{leg_prefix}c_bid", 0),
                    'c_ask': getattr(spread_signal, f"{leg_prefix}c_ask", 0),
                    'p_delta': getattr(spread_signal, f"{leg_prefix}p_delta", 0),
                    'c_delta': getattr(spread_signal, f"{leg_prefix}c_delta", 0),
                    'dte': dte_value,
                }, name=spread_date)
                
                if pd.isna(leg_signal['underlying_last']):
                    logger.warning(f"Missing underlying_last for {leg_prefix} on {spread_date}. Defaulting to NaN.")
                
                # Create the leg position
                position = create_trade_from_signal(
                    leg_signal,
                    leg_quantity,
                    leg_option_type,
                    leg_position_side,
                    leg_config.get('delta_target'),
                    spread_date,
                    early_close_days,
                    leg_config.get('delta_range')
                )
                
                if position:
                    # Add spread-specific information
                    position['spread_type'] = spread_type.value
                    position['spread_id'] = spread_counter
                    position['leg_number'] = leg_number
                    position['leg_ratio'] = leg_config.get('ratio', 1)
                    # Add the total spread price to each leg
                    position['spread_price'] = getattr(spread_signal, 'spread_price', 0)
                    
                    spread_legs.append(position)
                else:
                    # If any leg can't be created, skip this spread
                    logger.warning(f"Could not create leg {leg_number} for spread on {spread_date}")
                    break
            except KeyError as e:
                logger.warning(f"Missing key in spread signal: {e}")
                break
            except Exception as e:
                logger.warning(f"Error creating leg {leg_number} for spread: {e}")
                break
        
        # If all legs were created, add to the list of positions to trade
        if len(spread_legs) == len(legs_config):
            leg_positions.extend(spread_legs)
            spread_counter += 1
    
    logger.info(f"Created {len(leg_positions)} leg positions for {spread_counter-1} spreads")
    return leg_positions

def prepare_backtest_params(
    params: Dict,
    spx_file_path: str,
    options_chain_file_path: str,
    options_chain: pd.DataFrame,
    spx_data: pd.DataFrame,
    preloaded_data: Dict
) -> Dict:
    """
    Prepare the appropriate parameters for run_backtest based on whether 
    this is a spread or single-leg backtest.
    
    Args:
        params: Dictionary of backtest parameters
        spx_file_path: Path to SPX data file
        options_chain_file_path: Path to options chain file
        options_chain: Options chain DataFrame
        spx_data: SPX data DataFrame
        preloaded_data: Dictionary of preloaded data
        
    Returns:
        Dictionary of parameters to pass to run_backtest
    """
    # Check if this is a spread backtest
    is_spread = 'spread_type' in params and 'legs_config' in params
    
    # Common parameters that apply to both types
    backtest_params = {
        'spx_file_path': spx_file_path,
        'options_chain_file_path': options_chain_file_path,
        'preloaded_data': preloaded_data,
        'dte_range': params.get('dte_range'),
        'dte_target': params.get('dte_target'),
        'start_date': params.get('start_date'),
        'end_date': params.get('end_date'),
        'quantity': params.get('quantity', 1),
    }
    
    # Add specific parameters based on backtest type
    if is_spread:
        # Generate spread signals
        spread_signals = generate_spread_signals(
            options_chain=options_chain,
            spread_type=params['spread_type'],
            legs_config=params['legs_config'],
            start_date=params.get('start_date'),
            end_date=params.get('end_date'),
            dte_range=params.get('dte_range'),
            dte_target=params.get('dte_target'),
            spx_data=spx_data
        )
        
        # Add spread-specific parameters
        backtest_params.update({
            'spread_signals': spread_signals,
            'spread_type': params['spread_type'],
            'legs_config': params['legs_config'],
        })
    else:
        # Generate single-leg trade signals
        trade_signals = generate_trade_signals(
            spx_data=spx_data,
            options_chain=options_chain,
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

def run_multiple_backtests(
    spx_file_path: str,
    options_chain_file_path: str,
    hyperparameter_sets: list,
    use_preprocessed: bool = True,
    save_preprocessed: bool = True,
    max_positions: int = 1
) -> dict:
    """
    Run multiple backtests with different hyperparameters using the same loaded data.
    
    Args:
        spx_file_path: Path to SPX data file
        options_chain_file_path: Path to options chain data file
        hyperparameter_sets: List of dictionaries containing hyperparameter sets
        use_preprocessed: Whether to use preprocessed data
        save_preprocessed: Whether to save preprocessed data
        max_positions: Maximum number of simultaneous positions allowed (default: 1)
        
    Returns:
        Dictionary containing results for each hyperparameter set
    """
    # Load data once
    logger.info("Loading data for multiple backtests...")
    data_loading_start = time.time()
    options_chain, options_chain_multi_index, spx_data, vix_data = load_backtest_data(
        data_dir=os.path.dirname(spx_file_path),
        use_preprocessed=use_preprocessed,
        save_preprocessed=save_preprocessed,
        options_file=os.path.basename(options_chain_file_path)
    )
    data_loading_time = time.time() - data_loading_start
    logger.info(f"Data loading completed in {data_loading_time:.2f} seconds")
    
    # Check data quality once
    check_data_quality(options_chain, spx_data, vix_data)
    
    preloaded_data = {
        'spx_data': spx_data,
        'options_data': options_chain,
        'options_data_multi': options_chain_multi_index,
        'vix_data': vix_data
    }
    
    results = {}
    total_start_time = time.time()
    
    for i, params in enumerate(hyperparameter_sets, 1):
        logger.info(f"\nRunning backtest {i}/{len(hyperparameter_sets)} with parameters:")
        for key, value in params.items():
            logger.info(f"  {key}: {value}")
        
        start_time = time.time()
        
        # Add max_positions to the parameters if not already specified
        if 'max_positions' not in params:
            params['max_positions'] = max_positions
            
        logger.debug(f'Running with params {params}')
        
        # Prepare parameters for this backtest
        backtest_params = prepare_backtest_params(
            params=params,
            spx_file_path=spx_file_path,
            options_chain_file_path=options_chain_file_path,
            options_chain=options_chain,
            spx_data=spx_data,
            preloaded_data=preloaded_data
        )
        
        # Run the backtest
        result = run_backtest(**backtest_params)
        execution_time = time.time() - start_time
        
        results[f"backtest_{i}"] = {
            'params': params,
            'results': result,
            'execution_time': execution_time
        }
        
        logger.info(f"Backtest {i} completed in {execution_time:.2f} seconds")
    
    total_time = time.time() - total_start_time
    logger.info(f"\nAll backtests completed in {total_time:.2f} seconds")
    logger.info(f"Average time per backtest: {total_time/len(hyperparameter_sets):.2f} seconds")
    
    return results

def validate_dataframe_schema(df: pd.DataFrame, schema: dict, name: str = "") -> bool:
    """
    Validate that a DataFrame conforms to the specified schema.
    """
    return validate_dataframe_schema(df, schema, name)
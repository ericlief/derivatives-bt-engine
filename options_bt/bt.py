import sys
import pandas as pd
import numpy as np
import os
from typing import Dict, List, Optional, Tuple, TypedDict, Union
from enum import Enum, auto
import logging
from datetime import datetime
import dask.dataframe as dd

# Configure logging
def setup_logger(log_file: str = None):
    """
    Set up logging configuration.
    
    Args:
        log_file: Optional path to log file. If None, uses default name with timestamp.
    """
    if log_file is None:
        # Create logs directory if it doesn't exist
        os.makedirs('logs', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = f'logs/backtest_{timestamp}.log'
    
    # Create a logger
    logger = logging.getLogger(__name__)
    
    # Return existing logger if it already has handlers
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.DEBUG)  # Set the logger to the lowest level

    # Create file handler for all messages, including DEBUG
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)  # Log all messages to the file

    # Create console handler for INFO and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Log only INFO and above to the console

    # Create a formatter and set it for both handlers
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

# Create logger instance
logger = setup_logger()

class OptionType(Enum):
    CALL = "call"
    PUT = "put"

class PositionSide(Enum):
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

class Position(TypedDict):
    entry_date: pd.Timestamp
    expire_date: pd.Timestamp
    underlying_last: float
    strike: float
    option_type: str
    position_side: str
    bid: float
    ask: float
    entry_price: float
    margin_required: float
    close_date: Optional[pd.Timestamp]  # Optional field

class TradeResult(TypedDict):
    option_type: str
    position_side: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expire_date: pd.Timestamp
    entry_delta: float
    entry_dte: Optional[int]
    days_held: int
    underlying_entry: float
    underlying_exit: float
    strike: float
    entry_price: float
    exit_price: float
    pnl: float
    capital_used: float
    total_capital: float
    return_on_margin: float

def calculate_option_margin(underlying_price: float, entry_price: float, 
                           position_side: Union[PositionSide, str],
                           margin_req_percent: float = 0.20) -> float:
    """
    Calculate required margin for option position.
    
    Args:
        underlying_price: Current price of underlying asset
        entry_price: Option premium (mid of bid/ask)
        position_side: Whether position is LONG or SHORT
        margin_req_percent: Margin requirement percentage (default 0.20 for index options)
    
    Returns:
        Required margin in dollars
    """
    # Convert string to enum if needed
    if isinstance(position_side, str):
        position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
    
    # For long positions, margin is just the cost of the option
    if position_side == PositionSide.LONG:
        return round(entry_price * 100, 2)  # Convert to dollars
    
    # For short positions, use the more complex calculation
    else:  # PositionSide.SHORT
        margin_required = max(
            underlying_price * margin_req_percent,  # Percentage of underlying
            entry_price + (underlying_price * 0.10)  # Premium + additional percentage
        ) * 100  # Convert to dollars

        return round(margin_required, 2)

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
    if isinstance(option_type, str):
        is_put = option_type.lower() == "put"
    else:
        is_put = option_type == OptionType.PUT
        
    if is_put:
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
        Midpoint price if valid, None if invalid prices
    """
    if pd.isna(bid) or pd.isna(ask):
        logger.error(f"Invalid bid/ask prices: bid={bid}, ask={ask} (NaN values)")
        return None
        
    if bid < 0 or ask < 0:  # Only reject negative values, allow zeros
        logger.error(f"Invalid bid/ask prices: bid={bid}, ask={ask} (negative values)")
        return None
        
    # If both prices are zero, the midpoint is zero
    if bid == 0 and ask == 0:
        logger.debug(f"Both bid and ask are zero, returning midpoint of 0")
        return 0.0
        
    # Calculate spread percentage only if at least one price is non-zero
    if bid > 0 or ask > 0:
        spread_pct = (ask - bid) / ((bid + ask) / 2) if (bid + ask) > 0 else float('inf')
        if spread_pct > 0.20:  # Spread too wide
            logger.warning(f"Bid-ask spread too wide: bid={bid}, ask={ask}, spread={spread_pct:.2%}")
            # TODO Not sure if we can use some alternative valuation
        
    return round((bid + ask) / 2, 2)

def get_closing_data(position: Position,
                    full_chain_df: pd.DataFrame, 
                    spx_data: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    """
    Get closing price data for option position.
    
    Args:
        position: Position containing trade details
        full_chain_df: DataFrame containing full option chain data
        spx_data: DataFrame containing underlying price data
    
    Returns:
        Tuple of (closing_price, underlying_close)
    """
    
    # If no close_date, this is an expiration - use underlying price directly
    if 'close_date' not in position or not position['close_date']:
        if position['expire_date'] not in spx_data.index:
            return None, None
            
        underlying_close = spx_data.loc[position['expire_date'], 'close']
        close_price = calculate_intrinsic_value(underlying_close, position['strike'], position['option_type'])
        return close_price, underlying_close
    
    # Early close - get data from close_date forward (up to 5 days)
    close_date = position['close_date']
    date_range = pd.date_range(close_date, close_date + pd.Timedelta(days=5))
    
    filtered_df = full_chain_df[
        (full_chain_df.index.isin(date_range)) & 
        (full_chain_df['expire_date'] == position['expire_date']) &
        (full_chain_df['strike'] == position['strike'])
    ].sort_index()  # Sort by date to try closest dates first
    
    if filtered_df.empty:
        return None, None
        
    bid_col = "p_bid" if position['option_type'] in [OptionType.PUT, "put"] else "c_bid"
    ask_col = "p_ask" if position['option_type'] in [OptionType.PUT, "put"] else "c_ask"
    
    # Try each date in the filtered data until we find valid prices
    for idx, row in filtered_df.iterrows():
        bid = row[bid_col]
        ask = row[ask_col]
        underlying_close = row['underlying_last']
        
        mid_price = calculate_midpoint_price(bid, ask)
        if mid_price is not None:
            logger.debug(f"Using prices from {idx} for close date {close_date}")
            return mid_price, underlying_close
    
    # If we get here, no valid prices were found within 5 days
    logger.error(f"No valid closing prices found within 5 days of {close_date}. Strike: {position['strike']}, "
                f"Type: {position['option_type']}, Expire: {position['expire_date']}. "
                f"Last bid/ask seen: {bid}/{ask}")
    return None, None

def calculate_option_pnl(position: Position, underlying_close: float) -> float:
    """
    Calculate P&L for option position.
    
    Args:
        position: Position dictionary containing trade details
        underlying_close: Closing price of underlying at expiration
    
    Returns:
        P&L in dollars
    """
    # entry_price is already signed based on position side in create_trade_from_signal
    # Calculate intrinsic value at expiration
    intrinsic_value = calculate_intrinsic_value(underlying_close, position['strike'], position['option_type'])
    
    # P&L is the difference between intrinsic value and entry price
    # entry_price is already signed (negative for long, positive for short)
    # For long positions: P&L = intrinsic_value + entry_price (entry_price is negative)
    # For short positions: P&L = -intrinsic_value + entry_price (entry_price is positive)
    pnl = (intrinsic_value if position['position_side'] == PositionSide.LONG.value 
           else -intrinsic_value) + position['entry_price']
    
    return pnl * 100  # Convert to dollars

def close_position(position: Position, 
                  full_chain_df: pd.DataFrame, 
                  underlying_price_history: pd.DataFrame,
                  current_capital: float) -> Tuple[float, Optional[TradeResult]]:
    """
    Close an open option position and calculate results.
    
    Args:
        position: Position containing trade details
        full_chain_df: DataFrame containing full option chain data
        underlying_price_history: DataFrame containing underlying price data
        current_capital: Current total capital before closing position
    
    Returns:
        Tuple of (new_total_capital, trade_result)
        If closing data is unavailable, returns (current_capital, None) to indicate the trade should be skipped
    """
    # Define minimum valid date for validation
    min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970
    
    # Validate entry_date
    entry_date = position['entry_date']
    if not isinstance(entry_date, pd.Timestamp) or entry_date <= min_valid_date:
        logger.error(f"Invalid entry date: {entry_date} - skipping trade")
        return current_capital, None
    
    # Get close date with validation
    if 'close_date' in position and position['close_date'] is not None:
        close_date = position['close_date']
    elif 'expire_date' in position and position['expire_date'] is not None:
        close_date = position['expire_date']
    else:
        logger.error("Both close_date and expire_date are None in position - skipping trade")
        return current_capital, None
    
    # Validate close_date
    if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
        logger.error(f"Invalid close date: {close_date} - skipping trade")
        return current_capital, None
    
    # Ensure close_date is not before entry_date
    if close_date < entry_date:
        logger.error(f"Close date {close_date} is before entry date {entry_date} - skipping trade")
        return current_capital, None
    
    close_price, underlying_close = get_closing_data(position, full_chain_df, underlying_price_history)
    
    # If get_closing_data returned None values, we should skip this trade
    if close_price is None or underlying_close is None:
        logger.warning("Skipping trade due to missing close data")
        return current_capital, None
    
    pnl = calculate_option_pnl(position, underlying_close)
    margin_released = position['margin_required']
    capital_change = margin_released + pnl
    total_capital_after = current_capital + capital_change
    
    # Calculate days held - dates should already be normalized
    days_held = (close_date - entry_date).days
   
    # Safety check for negative days
    if days_held < 0:
        logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
        return current_capital, None
    
    trade_result: TradeResult = {
        'option_type': position['option_type'],
        'position_side': position['position_side'],
        'entry_date': entry_date,
        'exit_date': close_date,
        'expire_date': position['expire_date'],
        'entry_delta': round(position.get('entry_delta', 0.0), 2),
        'entry_dte': position.get('entry_dte', None),
        'days_held': days_held,
        'underlying_entry': position['underlying_last'],
        'underlying_exit': underlying_close,
        'strike': position['strike'], 
        'entry_price': round(position['entry_price'], 2),
        'exit_price': round(close_price, 2),
        'pnl': round(pnl, 2),
        'capital_used': margin_released,
        'total_capital': round(total_capital_after, 2),
        'return_on_margin': round(pnl / margin_released * 100, 2),
     }
    
    return total_capital_after, trade_result

def create_trade_from_signal(trade_signal, underlying_price: float, entry_price: float, 
                            option_type: OptionType, position_side: PositionSide) -> Optional[Position]:
    """
    Create a Position from a signal row
    
    Args:
        trade_signal: Row from trades DataFrame (as namedtuple)
        underlying_price: Price of underlying asset
        entry_price: Option entry price (mid of bid/ask)
        option_type: Type of option (PUT or CALL)
        position_side: Whether buying or selling the option
    """
    logger.debug(f"Creating trade from signal for date: {trade_signal.Index}")
    
    # Get bid/ask fields based on option type
    bid_field = "p_bid" if option_type == OptionType.PUT else "c_bid"
    ask_field = "p_ask" if option_type == OptionType.PUT else "c_ask"
    
    # Validate trade_signal.Index is a valid date
    if not hasattr(trade_signal, 'Index'):
        logger.error("No Index field in trade signal")
        return None
        
    entry_date = trade_signal.Index
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
    bid = getattr(trade_signal, bid_field, 0)
    ask = getattr(trade_signal, ask_field, 0)
    
    # Validate bid-ask spread isn't too wide (indicating low liquidity)
    if bid > 0 and ask > 0:
        spread_pct = (ask - bid) / ((bid + ask) / 2)
        if spread_pct > 0.15:  # 15% spread is quite wide
            logger.error(f"Bid-ask spread too wide ({spread_pct:.1%}) on {entry_date}, indicating low liquidity")
            return None
    
    # Get delta value based on option type
    delta_field = "p_delta" if option_type == OptionType.PUT else "c_delta"
    entry_delta = getattr(trade_signal, delta_field, None)
    
    # Calculate DTE
    entry_dte = (trade_signal.expire_date - trade_signal.Index).days
    
    # Adjust entry price sign based on position side
    # For long positions, entry price should be negative (cash outflow)
    # For short positions, entry price should be positive (cash inflow)
    signed_entry_price = -entry_price if position_side == PositionSide.LONG else entry_price
    
    # Create the position with data
    position: Position = {
        'entry_date': entry_date,
        'expire_date': expire_date,
        'underlying_last': underlying_price,
        'strike': trade_signal.strike,
        'option_type': option_type.value,
        'position_side': position_side.value,
        'bid': bid,
        'ask': ask,
        'entry_price': round(signed_entry_price, 2),  # Use the signed entry price
        'margin_required': calculate_option_margin(underlying_price, abs(entry_price), position_side),  # Use absolute entry price for margin
        'close_date': None,
        'entry_delta': round(entry_delta, 2) if entry_delta is not None else None,
        'entry_dte': entry_dte
    }
    return position

def execute_trade(trade: Position, available_capital: float) -> Tuple[Optional[Position], float]:
    """
    Attempt to execute a trade given available capital.
    
    Args:
        trade: Position to be executed
        available_capital: Available capital for the trade
    
    Returns:
        Tuple of (position if executed or None, remaining capital)
    """
    if available_capital >= trade['margin_required']:
        return trade, available_capital - trade['margin_required']
    else:
        logger.warning(f"Insufficient capital (${available_capital}) for trade on {trade['entry_date']}, requires ${trade['margin_required']}")
        return None, available_capital

def execute_backtest_trades(trade_signals_df: pd.DataFrame, 
                full_chain_df: pd.DataFrame, 
                spx_data: pd.DataFrame,
                option_type: OptionType = OptionType.PUT,
                position_side: PositionSide = PositionSide.SHORT,
                initial_capital: float = 100000,
                early_close_days: Optional[int] = None) -> pd.DataFrame:
    """
    Run backtest with sequential trades with access to full option chain data.
    
    Args:
        trade_signals_df: DataFrame containing trade signals
        full_chain_df: DataFrame containing full option chain data
        spx_data: DataFrame containing underlying price data
        option_type: Type of option strategy to trade (PUT or CALL)
        position_side: Whether buying or selling options (LONG or SHORT)
        initial_capital: Starting capital amount
        early_close_days: If set, close positions this many days after entry instead of at expiration
    
    Returns:
        DataFrame containing backtest results
    """
    results: List[TradeResult] = []
    capital = initial_capital
    open_position: Optional[Position] = None
    total_trades_considered = 0
    skipped_trades = 0
    
    # Track highest capital for drawdown calculation
    peak_capital = round(initial_capital, 2)
    current_drawdown = 0.0
    max_drawdown = 0.0
    
    # Determine bid/ask column names based on option type
    bid_field = "p_bid" if option_type == OptionType.PUT else "c_bid"
    ask_field = "p_ask" if option_type == OptionType.PUT else "c_ask"
    
    # Keep track of the last position's actual close date, not expiration date
    last_position_close_date = None
    
    # Sort trade signals by date to ensure chronological processing
    # sorted_trade_signals_df = trade_signals_df.sort_index()
    
    # For itertuples with DatetimeIndex, we need to access the date via Index attribute
    for trade_signal in trade_signals_df.itertuples():
        total_trades_considered += 1
        
        # Skip if we can't access the trade date
        try:
            trade_date = trade_signal.Index
            logger.debug(f"Processing potential trade for date {trade_date} and capital {capital:.2f}")
        except Exception as e:
            logger.error(f"Error accessing trade date: {e} - skipping")
            skipped_trades += 1
            continue
        
        # Skip this trade if we still have an open position
        if open_position is not None:
            continue
        
        # Skip this trade if it's before the last position's close date
        # (we can open a new trade the day after the previous one was closed)
        if last_position_close_date is not None and trade_date < last_position_close_date:
            continue
            
        # Skip if the trade_signal doesn't have the expected structure
        if not hasattr(trade_signal, 'Index') or not hasattr(trade_signal, 'expire_date'):
            logger.warning(f"Malformed trade signal, missing index or expire_date - skipping")
            skipped_trades += 1
            continue
        
        # Get underlying price
        try:
            if hasattr(trade_signal, 'underlying_last') and pd.notna(trade_signal.underlying_last):
                underlying_price = trade_signal.underlying_last
            elif trade_date in spx_data.index and 'close' in spx_data.columns:
                underlying_price = spx_data.loc[trade_date, 'close']
            else:
                logger.warning(f"No underlying price data for {trade_date}, skipping trade")
                skipped_trades += 1
                continue
        except Exception as e:
            logger.error(f"Error retrieving underlying price for {trade_date}: {e} - skipping")
            skipped_trades += 1
            continue
        
        # Calculate entry price for new trade - use correct bid/ask fields based on option type
        try:
            bid = getattr(trade_signal, bid_field, 0)
            ask = getattr(trade_signal, ask_field, 0)
            
            # Calculate midpoint of bid-ask spread for entry price
            entry_price = calculate_midpoint_price(bid, ask)
            
            # Skip if we have invalid entry price
            if entry_price is None:
                logger.warning(f"Invalid entry price calculated from bid={bid}, ask={ask} for {trade_date} - skipping")
                skipped_trades += 1
                continue
        except Exception as e:
            logger.error(f"Error calculating entry price for {trade_date}: {e} - skipping")
            skipped_trades += 1
            continue
        
        # Create and execute trade (validation of bid/ask happens inside create_trade_from_signal)
        new_trade = create_trade_from_signal(trade_signal, underlying_price, entry_price, option_type, position_side)
        
        # Skip if trade creation failed
        if new_trade is None:
            logger.warning(f"Failed to create trade for {trade_date} - skipping")
            skipped_trades += 1
            continue
        
        # Execute the trade if we have sufficient capital
        executed_position, capital = execute_trade(new_trade, capital)
        
        # Check if trade was successfully executed
        if executed_position is None:
            logger.warning(f"Insufficient capital (${capital}) for trade on {trade_date} - skipping")
            skipped_trades += 1
            continue
            
        # Store the open position
        open_position = executed_position
        
        # Set the closing date based on early_close_days or expiration
        if early_close_days is not None and early_close_days > 0:
            # Calculate the early close date
            early_close_date = trade_date + pd.Timedelta(days=early_close_days)
            
            # Ensure early close date exists in price data
            close_date_found = False
            test_date = early_close_date
            
            # Look for the next available trading day if the calculated date is not in the data
            for _ in range(5):  # Try up to 5 days forward
                if test_date in spx_data.index:
                    open_position['close_date'] = test_date
                    close_date_found = True
                    logger.debug(f"Setting early close date to {test_date} ({early_close_days} days after entry)")
                    break
                test_date += pd.Timedelta(days=1)
            
            if not close_date_found:
                logger.warning(f"Couldn't find valid early close date, using expiration date")
        
        # Close the position at expiration or early close date
        capital, trade_result = close_position(open_position, full_chain_df, spx_data, capital)
        
        # Process the trade result
        if trade_result is not None:
            # Add the result to our list
            results.append(trade_result)
            
            # Update peak capital and calculate drawdown
            current_capital = trade_result['total_capital']
            if current_capital > peak_capital:
                peak_capital = current_capital
            
            # Calculate current drawdown as percentage
            if peak_capital > 0:  # Avoid division by zero
                current_drawdown = round((current_capital - peak_capital) / peak_capital * 100, 2)
                max_drawdown = min(max_drawdown, current_drawdown)
            
            # Update the last_position_close_date to the actual exit date, not expiration date
            last_position_close_date = trade_result['exit_date']
            logger.debug(f"Closed position opened on {open_position['entry_date']}, closed on {last_position_close_date}, result: ${trade_result['pnl']:.2f}")
            logger.debug(f"Current capital: ${current_capital:.2f}, current drawdown: {current_drawdown:.2f}%, max drawdown: {max_drawdown:.2f}%")
        else:
            # If close_position returned None, the trade was skipped due to missing data
            logger.warning(f"Trade on {open_position['entry_date']} was skipped due to missing closing data")
            
            # IMPORTANT FIX: Restore the capital that was reserved for this trade since it was skipped
            capital += open_position['margin_required']
            logger.debug(f"Restored capital: ${open_position['margin_required']:.2f}, new balance: ${capital:.2f}")
            
            # In this case, we use the original expiration date to sequence future trades
            # because we don't have valid close data
            last_position_close_date = open_position['expire_date']
            logger.debug(f"Using expiration date {last_position_close_date} for sequencing next trade")
            
            skipped_trades += 1
        
        # Clear the position after closing
        open_position = None
    
    # Convert results to DataFrame and calculate cumulative metrics
    results_df = pd.DataFrame(results)
    if not results_df.empty:
        # Add cumulative metrics
        results_df['cumulative_pnl'] = results_df['pnl'].cumsum().round(2)
        
        # Calculate drawdown for each trade
        results_df['peak_capital'] = results_df['total_capital'].cummax().round(2)
        results_df['drawdown'] = (results_df['total_capital']-results_df['peak_capital']).round(2)
        results_df['drawdown_pct'] = (results_df['drawdown'] / results_df['peak_capital'] * 100).round(2)
        
        # Calculate trade statistics once
        total_trades = len(results_df)
        winning_trades = (results_df['pnl'] > 0).sum()
        win_rate = winning_trades / total_trades
        max_drawdown = results_df['drawdown_pct'].min()
        
        # Log all statistics
        logger.info(f"Processed {total_trades_considered} potential trades:")
        logger.info(f"  - {total_trades} successful trades")
        logger.info(f"  - {skipped_trades} skipped trades due to missing/invalid data")
        logger.info(f"  - Winning trades: {winning_trades}")
        logger.info(f"  - Win rate: {win_rate:.2%}")
        logger.info(f"  - Maximum drawdown: {max_drawdown:.2f}%")
        
        # Add statistics to the results_df as attributes
        results_df.attrs['total_trades'] = total_trades
        results_df.attrs['winning_trades'] = winning_trades
        results_df.attrs['win_rate'] = win_rate
        results_df.attrs['max_drawdown'] = max_drawdown
        results_df.attrs['skipped_trades'] = skipped_trades
    else:
        logger.warning(f"No valid trades executed during backtest. All {skipped_trades} trades skipped due to data issues.")

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
        df['dte'] = (df['expire_date'] - df.index).dt.days
    
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

def load_backtest_data(data_dir, use_preprocessed=True, save_preprocessed=True):
    """
    Load and preprocess data for backtesting from a standard data directory.
    
    Args:
        data_dir: Path to directory containing the data files
        use_preprocessed: Whether to use preprocessed data files
        save_preprocessed: Whether to save preprocessed data for future use
    
    Returns:
        tuple: (options_chain, options_chain_multi_index, spx_data, vix_data)
    """
    raw_files = {
        'options': os.path.join(data_dir, 'options.csv'),
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
    
    # Create a copy of the options chain to avoid modifying the original
    chain_df = options_chain.copy()
    
    # Filter by DATE range if provided
    if start_date:
        start_date = pd.to_datetime(start_date)
        # chain_df = chain_df[chain_df.index.get_level_values('date') >= start_date]
        chain_df = chain_df[chain_df.index >= start_date]

    
    if end_date:
        end_date = pd.to_datetime(end_date)
        # chain_df = chain_df[chain_df.index.get_level_values('date') <= end_date]
        chain_df = chain_df[chain_df.index <= end_date]
        logger.debug(f'Sorting for date range: {start_date}-{end_date}')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())

    # Filter by DTE based on whether we have a single value or ran
    if dte_range:
        dte_mask = (chain_df['dte'] >= dte_range[0]) & (chain_df['dte'] <= dte_range[1])
        chain_df = chain_df[dte_mask]
        logger.debug(f'Filtering for dte range: {dte_range}')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())
    elif dte_target:
        dte_mask = abs(chain_df['dte'] - dte_target) < 1
        chain_df = chain_df[dte_mask]
        logger.debug(f'Filtering for dte target: {dte_target}')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())
    else:
        logger.error('Need to provide either <dte_target> or <dte_range>')
        raise ValueError
    
    # Determine delta column based on option type
    delta_col = 'p_delta' if option_type == OptionType.PUT else 'c_delta'
    
    # Filter by delta parameters
    if delta_range:
        # Handle range case
        if option_type == OptionType.PUT and delta_range[0] > 0:
            min_delta, max_delta = -delta_range[1], -delta_range[0]
        else:
            min_delta, max_delta = delta_range
        
        delta_mask = chain_df[delta_col].between(min_delta, max_delta)
        chain_df = chain_df[delta_mask]
        # Sort by delta value, think should increase for calls  0.30, 0.32, ... and decrease for puts  -.30, -.32, ...
        ascending = (option_type == OptionType.CALL)
        logger.debug(f"Filtering and sorting in {'ascending' if ascending else 'descending'} order")
        logger.debug(f'for delta range {delta_range} for OptionType={option_type.value} -> delta col={delta_col}')
        chain_df = chain_df.reset_index().sort_values(by=['index', delta_col],
                                                     ascending=[True, ascending])
        trade_signals = chain_df.set_index('index')
        logger.debug(f"Filtering and sorting in {'ascending' if ascending else 'descending'} order")
        logger.debug(f'for delta range {delta_range} for OptionType={option_type.value} -> delta col={delta_col}')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())

    elif delta_target:
        # Handle target case
        # target = delta_target if option_type == OptionType.PUT else abs(delta_target)
        target = -abs(delta_target) if option_type == OptionType.PUT else abs(delta_target)
        logger.debug(f'Filtering for delta_target={delta_target}')

        delta_diff = abs(chain_df[delta_col] - target)
        chain_df = chain_df.assign(delta_diff=delta_diff)
        # logger.debug(f'Delta diff size: {delta_diff.size}')
        chain_df = chain_df[chain_df.delta_diff < 0.05]

        # logger.debug(f'Delta diff size: {delta_diff.size}')
        
        # Add and sort by delta_diff, ascending: .001, .002
        # chain_df = chain_df.assign(delta_diff=delta_diff).sort_values('delta_diff')
        logger.debug(f'Filtering and sorting for delta difference in delta target={target} for OptionType={option_type}/delta col={delta_col}')
        
        chain_df = chain_df.reset_index().sort_values(by=['index', 'delta_diff'], 
                                                      ascending=[True, True])
        trade_signals = chain_df.set_index('index')
        logger.debug('Sample chain')
        logger.debug(chain_df.head())
    else:
        logger.error('Need to provide either delta_target or delta_range')
        raise ValueError
    
    # Sort by date and delta difference (if it exists)
    # if 'delta_diff' in chain_df.columns:
    #     chain_df = chain_df.sort_values('delta_diff')
    
    # Group by date and get the best option for each date
    # trade_signals = chain_df.groupby(level='date').first()
    # logger.debug('Grouping by level=date.first()')
    # trade_signals = chain_df.sort_index()

    logger.info(f"Generated {len(trade_signals)} trade signals")
    # Print the head of the trade signals DataFrame to show a sample of the generated signals
    logger.info("\nSample of trade signals:")
    logger.info(trade_signals.head())

    # sys.exit()
    
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

def calculate_daily_value(trade, date, options_chain_multi_index, spx_data, use_spx_close: bool = True):
    """
    Calculate the daily market value of open positions and margin requirements.
    
    Args:
        trade: Trade result containing position details
        date: Date to calculate value for
        options_chain_multi_index: MultiIndex DataFrame with option chain data
        spx_data: DataFrame containing SPX closing prices
        use_spx_close: Whether to use SPX close price (True) or underlying_last from options data (False)
    
    Returns:
        Market value of the position
    """
    try:
        # Check if the date exists in the MultiIndex
        if date not in options_chain_multi_index.index.get_level_values(0):
            # Find the nearest date
            available_dates = options_chain_multi_index.index.get_level_values(0)
            nearest_date = available_dates[available_dates <= date][-1]
            logger.debug(f"Found nearest date {nearest_date} before target date {date}.")
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
                logger.debug(f"Using SPX close price: {underlying_price}")
            else:
                underlying_price = price_data['underlying_last'].iloc[0]
                logger.debug(f"Using options chain underlying_last: {underlying_price}")

            close = calculate_intrinsic_value(underlying_price, trade.strike, trade.option_type)
            market_value = round(close * 100, 2)
            logger.debug(f'Calculated intrinsic value on date={date} for strike={trade.strike} and value={market_value}')

        # Either MTM daily or early closure, so calculate mid point of bid/ask quote
        else:
            bid_col = 'p_bid' if 'put' in trade.option_type.lower() else "c_bid"
            ask_col = 'p_ask' if 'put' in trade.option_type.lower() else "c_ask"
            bid = price_data[bid_col].iloc[0] 
            ask = price_data[ask_col].iloc[0] 
            mid = calculate_midpoint_price(bid, ask)
            if mid is None:
                logger.warning(f"Invalid bid/ask prices on {date} for strike {trade.strike}: bid={bid}, ask={ask}")
                return None
            market_value = round(100 * mid, 2)
            logger.debug(f'Calculated mid value on date={date} for strike={trade.strike}, bid={bid}, ask={ask}, mid={mid}, value={market_value}')

        return market_value if not "short" in trade.position_side.lower() else -market_value
    
    except KeyError:
        logger.warning(f"No data for strike {trade.strike} on {date}")
    except Exception as e:
        logger.error(f"Error calculating daily value: {str(e)}")

    return None

def calculate_mtm(start_date, end_date, initial_capital, trade_results, options_chain_multi_index, spx_data, param_str, use_spx_close: bool = True, results_dir="results"):
    """
    Calculate and save mark-to-market (MTM) data for a backtest.
    
    Args:
        ... (existing args) ...
        use_spx_close: Whether to use SPX close price (True) or underlying_last from options data (False)
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
    
    logger.debug(f"Adjusting MTM end date from {initial_end_date} to {end_date} to include all trade exits")
    
    # Initialize tracking variables
    peak_capital = initial_capital
    net_liquidity = initial_capital  # This tracks cash + position values
    options_bp = initial_capital     # This tracks available buying power for new trades
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
        logger.debug(f'Processing date: {date}')
        # First, check for any trades that start on this date
        for trade in trade_results.itertuples():
            trade_start = pd.Timestamp(trade.entry_date).normalize()
            trade_end = pd.Timestamp(trade.exit_date).normalize()
            trade_id = (trade.expire_date, trade.strike, trade.option_type)
            

            # Handle existing trades
            if trade_id in active_trades:
                logger.debug(f'Processing active trade: {trade_id}')
                current_value = calculate_daily_value(trade, date, options_chain_multi_index, spx_data, use_spx_close)
                prev_value = active_trades[trade_id]['position_value']
                # Calculate daily P&L for this trade
                daily_pnl += current_value - prev_value if current_value is not None else 0
                logger.debug(f'Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

                # If trade closes today
                if trade_end == date:
                    logger.debug('Closing trade: {trade_id}')
                    options_bp += active_trades[trade_id]['margin_requirement']  # Release margin back to BP
                    del active_trades[trade_id]
                else:
                    logger.debug(f'Updating existing trade {trade_id}')
                    if current_value is not None:
                        active_trades[trade_id]['position_value'] = current_value
                        daily_position_value += current_value  # Only add once
                        daily_margin_requirement += active_trades[trade_id]['margin_requirement']
            
            # Handle new trades
            elif trade_start == date:
                logger.debug(f'Opening new trade: {trade_id}')
                position_value = calculate_daily_value(trade, date, options_chain_multi_index, spx_data, use_spx_close)
                if position_value is not None:
                    active_trades[trade_id] = {
                        'position_value': position_value,
                        'margin_requirement': trade.capital_used
                    }
                    entry_price = round(trade.entry_price * 100, 2)
                    daily_position_value += position_value
                    daily_pnl += entry_price + position_value
                    logger.debug(f'{position_value} + {entry_price} -> Daily PnL: {entry_price + position_value}')
                    daily_margin_requirement += trade.capital_used
                    options_bp -= trade.capital_used
                    logger.debug(f'BP: {options_bp}')

        # Update cumulative P&L
        cumulative_pnl += daily_pnl
        
        # Update net liquidity with daily P&L
        net_liquidity += daily_pnl
            # Update peak capital if net liquidity is higher
        if net_liquidity > peak_capital:
            peak_capital = net_liquidity
        # Calculate drawdown
        drawdown_amount = - max(0, round(peak_capital - net_liquidity, 2))
        drawdown_pct = (drawdown_amount / peak_capital * 100) if peak_capital > 0 else 0

        # Calculate ROI metrics
        daily_roi = round(daily_pnl / daily_margin_requirement * 100, 2) if daily_margin_requirement > 0 else 0
        total_roi = round((net_liquidity - initial_capital) / initial_capital * 100, 2)
        
        # Store daily data with expanded metrics
        daily_data.append({
            'Date': date,
            'Net Liquidity': round(net_liquidity, 2),
            'Options BP': round(options_bp, 2),
            'Position Value': round(daily_position_value, 2),
            'Margin Requirement': round(daily_margin_requirement, 2),
            'Daily P&L': round(daily_pnl, 2),
            'Cumulative P&L': round(cumulative_pnl, 2),
            'Drawdown ($)': round(drawdown_amount, 2),
            'Drawdown (%)': round(drawdown_pct, 2),
            'Daily ROI (%)': daily_roi,
            'Total ROI (%)': total_roi,
            'Active Positions': len(active_trades),
            'Peak Capital': round(peak_capital, 2),
            'Margin Utilization (%)': round(daily_margin_requirement / initial_capital * 100, 2)
        })
        
        # Log daily summary
        logger.debug(f'Date: {date}')
        logger.debug(f'  Daily P&L: ${daily_pnl:.2f}')
        logger.debug(f'  Cumulative P&L: ${cumulative_pnl:.2f}')
        logger.debug(f'  Daily ROI: {daily_roi:.2f}%')
        logger.debug(f'  Net Liquidity: ${net_liquidity:.2f}')
        logger.debug(f'  Options BP: ${options_bp:.2f}')
        logger.debug(f'  Active Positions: {len(active_trades)}')
    
    # Create DataFrame and calculate metrics
    daily_df = pd.DataFrame(daily_data)
    max_drawdown_amount = daily_df['Drawdown ($)'].max()
    max_drawdown_percentage = daily_df['Drawdown (%)'].max()
    
    # Save results
    mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}.csv")
    daily_df.to_csv(mtm_csv_path, index=False)
    logger.info(f"MTM results saved to {mtm_csv_path}")
    
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
        logger.debug("Converting to Dask DataFrame")
        dask_df = dd.from_pandas(options_chain, npartitions=4)
        
        # Log the columns before pivot
        logger.debug(f"Columns before pivot: {dask_df.columns.tolist()}")
        
        # Pivot operation
        logger.debug("Starting pivot table operation with Dask")
        pivoted_chain = dask_df.pivot_table(
            index='strike', 
            columns=date_col,
            values=needed_col,
        aggfunc='first'
    )
        
        logger.debug("Computing final result")
        result = pivoted_chain.compute()
        
        # Log the final columns
        logger.debug(f"Final columns after pivot: {result.columns.tolist()[:10]}")
        
        logger.debug(f"Pivot completed successfully. Result shape: {result.shape}")
        logger.debug(f"Memory usage after pivot: {result.memory_usage().sum() / 1024**2:.2f} MB")
        
        return result
        
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
    spx_file_path: str,
    options_chain_file_path: str,
    option_type: OptionType = OptionType.PUT,
    position_side: PositionSide = PositionSide.SHORT,
    delta_target: float = None,
    use_spx_close: bool = True,
    **kwargs
) -> pd.DataFrame:
    """
    Load data, generate signals, run backtest and return results.
    
    Args:
        spx_file_path: Path to SPX data file
        options_chain_file_path: Path to options chain data file
        option_type: Type of option to trade (PUT or CALL)
        position_side: Position side (LONG or SHORT)
        delta_target: Target delta value
        use_spx_close: Whether to use SPX close price (True) or underlying_last (False)
        **kwargs: Additional arguments including:
            - start_date: Start date for backtest
            - end_date: End date for backtest
            - dte_target: Target DTE value
            - dte_range: Tuple of (min_dte, max_dte)
            - delta_range: Tuple of (min_delta, max_delta)
            - initial_capital: Starting capital amount
            - early_close_days: Days to hold before early close
            - use_preprocessed: Whether to use preprocessed data
            - save_preprocessed: Whether to save preprocessed data
            - save_trades: Whether to save trade results
            - data_dir: Directory containing data files
    """
    # Set default values for all kwargs
    defaults = {
        'start_date': None,
        'end_date': None,
        'dte_target': None,
        'dte_range': None,
        'delta_range': None,
        'initial_capital': 100000,
        'early_close_days': None,
        'use_preprocessed': True,
        'save_preprocessed': True,
        'save_trades': True,
        'data_dir': os.path.dirname(spx_file_path)
    }
    
    # Update defaults with provided kwargs
    for key, value in kwargs.items():
        defaults[key] = value
    
    # Store original string dates for filename
    start_date_str = defaults['start_date']
    end_date_str = defaults['end_date']
    
    # Load data
    start_time = pd.Timestamp.now()
    options_chain, options_chain_multi_index, spx_data, vix_data = load_backtest_data(
        defaults['data_dir'],
        use_preprocessed=defaults['use_preprocessed'],
        save_preprocessed=defaults['save_preprocessed']
    )
    load_time = (pd.Timestamp.now() - start_time).total_seconds()
    logger.info(f"Data loading and preprocessing completed in {load_time:.2f} seconds")
    
    # Check data quality
    check_data_quality(options_chain, spx_data, vix_data)

    # Safely handle optional parameters for filename construction
    delta_str = (f"delta_{delta_target}" if delta_target else 
                f"delta_{defaults['delta_range'][0]}-{defaults['delta_range'][1]}" 
                if defaults['delta_range'] else "delta_any")
    
    dte_str = (f"dte_{defaults['dte_target']}" if defaults['dte_target'] else 
               f"dte_{defaults['dte_range'][0]}-{defaults['dte_range'][1]}" 
               if defaults['dte_range'] else "dte_any")
               
    early_close_str = (f"early_{defaults['early_close_days']}" 
                      if defaults['early_close_days'] else "full_term")
    
    param_str = f"{option_type.name}_{position_side.name}_{delta_str}_{dte_str}_{early_close_str}_{start_date_str}-{end_date_str}"
    
    # Generate trade signals
    signal_time = pd.Timestamp.now()
    trade_signals = generate_trade_signals(
        spx_data, 
        options_chain,
        option_type=option_type,
        delta_target=delta_target,
        delta_range=defaults['delta_range'],
        dte_target=defaults['dte_target'],
        dte_range=defaults['dte_range'],
        start_date=defaults['start_date'],
        end_date=defaults['end_date']
    )
    signal_generation_time = (pd.Timestamp.now() - signal_time).total_seconds()
    logger.info(f"Signal generation completed in {signal_generation_time:.2f} seconds")
    
    if trade_signals.empty:
        logger.warning("No trade signals generated with the current parameters.")
        return pd.DataFrame()  # Return empty DataFrame if no signals
    
    # Run backtest
    backtest_time = pd.Timestamp.now()
    logger.info(f"Running backtest for params:\t{param_str}")
    results = execute_backtest_trades(
        trade_signals,
        options_chain,
        spx_data,
        option_type=option_type,
        position_side=position_side,
        initial_capital=defaults['initial_capital'],
        early_close_days=defaults['early_close_days']
    )
    backtest_execution_time = (pd.Timestamp.now() - backtest_time).total_seconds()
    total_time = (pd.Timestamp.now() - start_time).total_seconds()
    logger.info(f"Backtest execution completed in {backtest_execution_time:.2f} seconds")
    logger.info(f"Total time: {total_time:.2f} seconds")
    
    # Call the MTM function with the MultiIndex version and use_spx_close parameter
    daily_df, max_drawdown, max_drawdown_pct = calculate_mtm(
        defaults['start_date'], defaults['end_date'], defaults['initial_capital'], results, 
        options_chain_multi_index, spx_data, param_str,
        use_spx_close=use_spx_close
    )
            
    # Print summary statistics
    logger.info("\nBacktest Results Summary:")
    logger.info(f"Total trades: {len(results)}")
    logger.info(f"Win rate: {(results['pnl'] > 0).mean():.2%}")
    logger.info(f"Average P&L: ${results['pnl'].mean():.2f}")
    logger.info(f"Total P&L: ${results['pnl'].sum():.2f}")
    logger.info(f"Initial capital: ${defaults['initial_capital']:.2f}")
    logger.info(f"Final capital: ${results['total_capital'].iloc[-1]:.2f}")
    logger.info(f"Return on initial capital: {(results['total_capital'].iloc[-1] / defaults['initial_capital'] - 1):.2%}")
    logger.info(f"Average days held: {results['days_held'].mean():.1f}")
    logger.info(f"Average return on margin: {results['return_on_margin'].mean():.2f}%")
    logger.info(f"Maximum drawdown: ${max_drawdown:.2f} ({max_drawdown_pct:.2f}%)")
    
    # Calculate Sharpe Ratio without risk-free rate
    sharpe = None
    if len(results) > 1:
        returns = np.diff(results['total_capital'].values) / results['total_capital'].values[:-1]
        if len(returns) > 0 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)  # Annualize by multiplying by sqrt(252)
            logger.info(f"Sharpe Ratio: {sharpe:.2f}")
    
    # Save summary results to CSV
    if defaults['save_trades']:
        results_dir = 'results'  # Updated to be consistent with logs directory
        os.makedirs(results_dir, exist_ok=True)
        results_csv_path = os.path.join(results_dir, f"results_{param_str}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv")
        results.to_csv(results_csv_path, index=False)
        logger.info(f"Summary results saved to {results_csv_path}")
    
    return results

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

# Example usage:
if __name__ == "__main__":
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    # Example file paths
    DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data")
    results = run_backtest(
        spx_file_path=os.path.join(DATA_PATH, "spx_2018_2023.csv"),
        options_chain_file_path=os.path.join(DATA_PATH, "spx_options_2018_2023.csv"),
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
    print("\nBasic example results:")
    print(results)
    print("\nFor more examples, see test_backtest.py")
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
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()  # Also print to console
        ]
    )
    return logging.getLogger(__name__)

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
    if 'close_date' not in position or position['close_date'] is None:
        if position['expire_date'] not in spx_data.index:
            return None, None
            
        underlying_close = spx_data.loc[position['expire_date'], 'close']
        
        # Calculate intrinsic value
        if position['option_type'] in [OptionType.PUT, "put"]:
            close_price = max(0, position['strike'] - underlying_close)
        else:  # CALL
            close_price = max(0, underlying_close - position['strike'])
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
        
        if not (pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0):
            spread_pct = (ask - bid) / ((bid + ask) / 2)
            if spread_pct <= 0.20:  # Found valid prices with acceptable spread
                logger.info(f"Using prices from {idx} for close date {close_date}")
                return round((bid + ask) / 2, 2), underlying_close
    
    # If we get here, no valid prices were found
    return None, None

def calculate_option_pnl(underlying_close: float, strike: float, entry_price: float, 
                      option_type: Union[OptionType, str], position_side: Union[PositionSide, str]) -> float:
    """
    Calculate P&L for option position.
    
    Args:
        underlying_close: Closing price of underlying at expiration
        strike: Option strike price
        entry_price: Option entry price
        option_type: Type of option (PUT or CALL) - can be enum or string
        position_side: Whether position is LONG or SHORT - can be enum or string
    
    Returns:
        P&L in dollars
    """
    # Convert strings to enums if needed
    if isinstance(option_type, str):
        option_type = OptionType.PUT if option_type.lower() == "put" else OptionType.CALL
        
    if isinstance(position_side, str):
        position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
    
    # For short positions (selling options)
    if position_side == PositionSide.SHORT:
        if option_type == OptionType.PUT:
            if underlying_close > strike:
                # Put expires worthless, keep full premium
                return entry_price * 100
            else:
                # Put assigned, loss on price difference
                return (underlying_close - strike + entry_price) * 100
        else:  # CALL
            if underlying_close < strike:
                # Call expires worthless, keep full premium
                return entry_price * 100
            else:
                # Call assigned, loss on price difference
                return (strike - underlying_close + entry_price) * 100
    
    # For long positions (buying options)
    else:  # PositionSide.LONG
        if option_type == OptionType.PUT:
            if underlying_close > strike:
                # Put expires worthless, lose premium
                return -entry_price * 100
            else:
                # Put has value, profit on price difference
                return (strike - underlying_close - entry_price) * 100
        else:  # CALL
            if underlying_close < strike:
                # Call expires worthless, lose premium
                return -entry_price * 100
            else:
                # Call has value, profit on price difference
                return (underlying_close - strike - entry_price) * 100

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
    # Debug: log position details to help diagnose issues
    # logger.debug("\nClosing position details:")
    # for k, v in position.items():
    #     logger.debug(f"  {k}: {v}, type: {type(v)}")
    
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
    
    strike = position['strike']
    entry_price = position['entry_price']
    option_type = position['option_type']
    
    close_price, underlying_close = get_closing_data(position, full_chain_df, underlying_price_history)
    
    # If get_closing_data returned None values, we should skip this trade
    if close_price is None or underlying_close is None:
        logger.warning("Skipping trade due to missing close data")
        return current_capital, None
    
    pnl = calculate_option_pnl(underlying_close, strike, entry_price, option_type, position['position_side'])
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
        'option_type': option_type.value if isinstance(option_type, OptionType) else option_type,
        'position_side': position['position_side'].value if isinstance(position['position_side'], PositionSide) else position['position_side'],
        'entry_date': entry_date,
        'exit_date': close_date,
        'expire_date': position['expire_date'],
        'entry_delta': round(position.get('entry_delta', 0.0), 2),
        'entry_dte': position.get('entry_dte', None),
        'days_held': days_held,
        'underlying_entry': position['underlying_last'],
        'underlying_exit': underlying_close,
        'strike': strike, 
        'entry_price': round(entry_price, 2),
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
    
    Returns:
        Position dictionary or None if validation fails
    """
    logger.info(f"Creating trade from signal for date: {trade_signal.Index}")
    
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
    if not hasattr(trade_signal, 'expire_date'):
        logger.error(f"No expire_date field in trade signal on {trade_signal.Index}")
        return None
    
    if trade_signal.expire_date is None:
        logger.error(f"expire_date is None for trade signal on {trade_signal.Index}")
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
        'entry_price': round(entry_price, 2),
        'margin_required': calculate_option_margin(underlying_price, entry_price, position_side),
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

def run_backtest(trade_signals_df: pd.DataFrame, 
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
    sorted_trade_signals_df = trade_signals_df.sort_index()
    
    # For itertuples with DatetimeIndex, we need to access the date via Index attribute
    for trade_signal in sorted_trade_signals_df.itertuples():
        total_trades_considered += 1
        
        # Skip if we can't access the trade date
        try:
            trade_date = trade_signal.Index
            logger.info(f"Processing potential trade for date {trade_date} and capital {capital}")
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
            entry_price = round((bid + ask) / 2, 2)
            
            # Skip if we have invalid entry price
            if pd.isna(entry_price) or entry_price <= 0:
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
                    logger.info(f"Setting early close date to {test_date} ({early_close_days} days after entry)")
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
                current_drawdown = - round((peak_capital - current_capital) / peak_capital * 100, 2)
                max_drawdown = max(max_drawdown, current_drawdown)
            
            # Update the last_position_close_date to the actual exit date, not expiration date
            last_position_close_date = trade_result['exit_date']
            logger.info(f"Closed position opened on {open_position['entry_date']}, closed on {last_position_close_date}, result: ${trade_result['pnl']:.2f}")
            logger.info(f"Current capital: ${current_capital:.2f}, current drawdown: {current_drawdown:.2f}%, max drawdown: {max_drawdown:.2f}%")
        else:
            # If close_position returned None, the trade was skipped due to missing data
            logger.warning(f"Trade on {open_position['entry_date']} was skipped due to missing closing data")
            
            # IMPORTANT FIX: Restore the capital that was reserved for this trade since it was skipped
            capital += open_position['margin_required']
            logger.info(f"Restored capital: ${open_position['margin_required']:.2f}, new balance: ${capital:.2f}")
            
            # In this case, we use the original expiration date to sequence future trades
            # because we don't have valid close data
            last_position_close_date = open_position['expire_date']
            logger.info(f"Using expiration date {last_position_close_date} for sequencing next trade")
            
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
        results_df['drawdown_pct'] = ((results_df['peak_capital'] - results_df['total_capital']) / results_df['peak_capital'] * 100).round(2)
        
        # Calculate trade statistics once
        total_trades = len(results_df)
        winning_trades = (results_df['pnl'] > 0).sum()
        win_rate = winning_trades / total_trades
        max_drawdown = results_df['drawdown_pct'].max()
        
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
        tuple: (options_chain, spx_data, vix_data)
    """
    # Define standard file names
    raw_files = {
        'options': os.path.join(data_dir, 'options.csv'),
        'spx': os.path.join(data_dir, 'spx.csv'),
        'vix': os.path.join(data_dir, 'vix.csv')
    }
    
    processed_files = {
        'options': os.path.join(data_dir, 'options.pkl'),
        'options_pivoted': os.path.join(data_dir, 'options_pivoted.pkl'),
        'spx': os.path.join(data_dir, 'spx.pkl'),
        'vix': os.path.join(data_dir, 'vix.pkl')
    }
    
    try:
        # Try to load preprocessed data if requested
        if use_preprocessed:
            if os.path.exists(processed_files['options_pivoted']):
                logger.info("Loading pivoted options chain")
                options_chain = pd.read_pickle(processed_files['options_pivoted'])
                
                if os.path.exists(processed_files['spx']) and os.path.exists(processed_files['vix']):
                    logger.info("Loading preprocessed SPX and VIX data")
                    spx_data = pd.read_pickle(processed_files['spx'])
                    vix_data = pd.read_pickle(processed_files['vix'])
                    return options_chain, spx_data, vix_data
            
            elif all(os.path.exists(f) for f in [processed_files['options'], processed_files['spx'], processed_files['vix']]):
                logger.info("Loading non-pivoted preprocessed data")
                options_chain = pd.read_pickle(processed_files['options'])
                spx_data = pd.read_pickle(processed_files['spx'])
                vix_data = pd.read_pickle(processed_files['vix'])
                
                # Pivot the options chain
                logger.info("Pivoting options chain")
                options_chain = prepare_options_chain(options_chain, processed_files['options_pivoted'], "default")
                return options_chain, spx_data, vix_data
        
        # Load and preprocess original data
        logger.info("Loading and preprocessing original data files")
        options_chain = pd.read_csv(raw_files['options'], index_col=0, parse_dates=True)
        spx_data = pd.read_csv(raw_files['spx'], index_col=0, parse_dates=True)
        vix_data = pd.read_csv(raw_files['vix'], index_col=0, parse_dates=True)
        
        # Preprocess the data
        options_chain = preprocess_options_data(options_chain)
        spx_data = preprocess_spx_data(spx_data)
        vix_data = preprocess_vix_data(vix_data)
        
        # Save preprocessed non-pivoted data if requested
        if save_preprocessed:
            options_chain.to_pickle(processed_files['options'])
            spx_data.to_pickle(processed_files['spx'])
            vix_data.to_pickle(processed_files['vix'])
            logger.info("Saved preprocessed data files")
        
        # Pivot the options chain
        logger.info("Pivoting options chain")
        options_chain = prepare_options_chain(options_chain, processed_files['options_pivoted'], "default")
        
        logger.info(f"Loaded and preprocessed data:")
        logger.info(f"- Options chain: {len(options_chain)} rows")
        logger.info(f"- SPX data: {len(spx_data)} rows")
        logger.info(f"- VIX data: {len(vix_data)} rows")
        
        return options_chain, spx_data, vix_data
        
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}")
        raise

def generate_trade_signals(spx_data: pd.DataFrame, 
                          options_chain: pd.DataFrame,
                          start_date: str = None,
                          end_date: str = None,
                          option_type: OptionType = OptionType.PUT,
                          delta_target: float = -0.30,
                          delta_range: Tuple[float, float] = None,
                          dte_target: int = None,
                          dte_range: Tuple[int, int] = None) -> pd.DataFrame:
    """
    Generate trade signals based on delta target/range and days to expiration.
    
    Args:
        spx_data: DataFrame containing SPX price history
        options_chain: DataFrame containing full options chain data
        start_date: Optional start date for filtering (e.g., "2010-01-01")
        end_date: Optional end date for filtering (e.g., "2020-12-31")
        option_type: Type of option (PUT or CALL)
        delta_target: Target delta for option selection (single value)
        delta_range: Range of delta values (min, max) as tuple
        dte_target: Target days to expiration (single value)
        dte_range: Range of DTE values (min, max) as tuple
    
    Returns:
        DataFrame containing trade signals
    """
    # Create a copy of the options chain to avoid modifying the original
    chain_df = options_chain.copy()
    
    # Filter by date range if provided
    # if start_date:
    #     start_date = pd.to_datetime(start_date)
    #     chain_df = chain_df[chain_df.columns.get_level_values('date') >= start_date]
    # if end_date:
    #     end_date = pd.to_datetime(end_date)
    #     chain_df = chain_df[chain_df.index.get_level_values('date') <= end_date]
    
    
    
    # Filter using the date level of the MultiIndex columns
    dates = chain_df.columns.get_level_values('date')

    #  Convert to datetime if necessary
    if pd.api.types.is_categorical_dtype(dates):
        dates = pd.to_datetime(dates)

    # Filter the date to the interval of interest
    logger.info(f'Filtering date range: {start_date}-{end_date}')
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)
    interval_filter = dates[(dates >= start_date) & (dates <= end_date)]

    # print(len(interval))
    # print(len(set(interval)))

    # Select the filtered columns
    chain_df = chain_df.loc[:, (slice(None), interval_filter)]
    print(chain_df)

    print("Level Names: ", chain_df.columns.names)
    # print(f"dte in df: {'dte' in filtered_df.columns.get_level_values(0)}")
    # filtered_columns = date_level[(date_level >= start_date) & (date_level <= end_date)]
    # non_date_cols = set(filtered_df.columns.get_level_values(0))

    # Select the filtered columns
    # filtered_df = chain_df.loc[:, (slice(None), filtered_columns)]

    # Compute to see the result
    # result = filtered_df.compute()
    # print(result)

    # # Calculate days to expiration if needed
    # if 'dte' not in filtered_df:
    #     chain_df['dte'] = (chain_df['expire_date'] - chain_df.index).dt.days
    
    # Filter by DTE based on whether we have a single value or range
    if dte_range is not None:
        logger.info(f'Getting dte initial range {dte_range}')
        dte_values = chain_df.loc[:, ('dte', slice(None))]

        print('dte vals', dte_values.describe())
        print('original dte val shape', dte_values.shape)
        print('dte levels', dte_values.columns.levels)
        print(f"NaN values in dte_values (before dropping nan): {dte_values.isna().sum().sum()}")
        # Drop rows with NaN values in the 'dte' column before filtering
        cleaned_dte = dte_values.dropna(how='all')
        print(f"NaN values in dte_values (after dropping nan cols): {cleaned_dte.isna().sum().sum()}")

        # Now filter the dte values within the desired range
        # filtered_dte = cleaned_dte[(cleaned_dte >= 70) & (cleaned_dte <= 75)]
        # print('cleaned nan prior', filtered_dte)

        dte_mask = (dte_values >= dte_range[0]) & (dte_values <= dte_range[1])
        print("Got mask of shape:", dte_mask.shape)
        print(dte_mask)
        l1_cols = chain_df.columns.levels[0]
        print("Cols", l1_cols)
        print('levels in mask', dte_mask.columns.levels)
        # dte_mask.columns = dte_mask.columns.droplevel(1)
        # print(dte_mask)
        # print('levels in mask', dte_mask.columns.levels)
        dte_mask.columns = dte_mask.columns.droplevel(0)

        print("drop levels mask", dte_mask)
        # print('levels in mask', dte_mask.columns.levels)
        # stats = dte_mask.apply(lambda c: sum(dte_mask[c]) )
        stats = sum([r for c in dte_mask.columns for r in dte_mask[c]])
        print("Number of True vals in original mask: ", stats)
        # print(pd.api.types.is_bool(dte_mask))
        l1_cols = chain_df.columns.levels[0]
        # print('red mask', chain_df
        full_mask = pd.concat({k: dte_mask for k in l1_cols}, axis=1)
        print("Broadcast mask", full_mask)
        print("Broadcast mask shape", full_mask.shape)
        print(full_mask.columns.levels)
        stats = sum([r for c in chain_df.columns for r in full_mask[c]])
        print("Number of True vals in full broadcast mask: ", stats)
        stats = sum([r for c in chain_df.columns for r in full_mask[c] 
                     if 'dte' in c ])
        print("Number of True vals in DTE partition: ", stats)
        print("Number of True vals in DTE partition: ", full_mask.loc[:, ('dte', slice(None))].sum().sum())


        tile_mask = np.tile(dte_mask.values, [len(chain_df.columns.levels[0])])
        print("Tiled mask", tile_mask.shape)
         # dte_mask.dropna(how='all')
        # dte_mask.dropna(axis=1, how='all')
        # valid_strikes = dte_mask.any(axis=1)  # This will give you a boolean Series for strikes
        # print("Valid strikes mask:", valid_strikes.shape)
        # print('mask', valid_strikes)

        filtered_dte = cleaned_dte[dte_mask]
        # print('dte vals', dte_values.describe())
        # print(f"NaN values in dte_values (before filtering): {dte_values.isna().sum().sum()}")
        # filtered_cols = dte_mask.any(axis=0)
        # print(f'filtered_col mask: {filtered_cols}')
        # filtered_dates = dte_values[dte_mask]
        # filtered_dte = dte_values.loc[:, filtered_cols]
        # print("filtered col dte:", filtered_dte)
        print(f"NaN values in dte_values (after filtering): {filtered_dte.isna().sum().sum()}")
        filtered_dte = filtered_dte.dropna(how='all')
        # valid_dates = valid_dates.dropna(axis=1)
        print(f"NaN values in dte_values (after dropping nan): {filtered_dte.isna().sum().sum()}")
        print("Filtered dte", filtered_dte)       
        print(filtered_dte.describe())

        filtered_dte = filtered_dte.dropna(axis=1, how='all')
        # valid_dates = valid_dates.dropna(axis=1)
        print(f"NaN values in dte_values (after dropping nan cols): {filtered_dte.isna().sum().sum()}")
        print("Filtered dte", filtered_dte)      
        print(filtered_dte.describe())
        
        print("Example values", filtered_dte.loc[:, ('dte', '2020-01-21')])

        # valid_dates = dte_values[dte_mask]
        # print("dte mask", dte_mask)
        # print("dte mask axis=0", dte_mask.any(axis=0))

        # valid_dates = dte_values.columns.get_level_values(1)[dte_mask.any(axis=0)]
        # print(valid_dates)
        # chain_df = chain_df.loc[:, ('dte', valid_dates)]
        # valid_dates = chain_df.columns.get_level_values(1)[dte_mask.any(axis=0)]
        # print(f"Valid dates: {valid_dates}")    
        # Filter the chain_df based on valid dates
        print(f"Shape of chain_df before filtering: {chain_df.shape}")
        print(f"NaN values in filtered chain_df: {chain_df.isna().sum().sum()}")
        print(chain_df.describe())
        chain_df.dropna(how='all')
        chain_df.dropna(axis=1, how='all')

        print(f"Shape of chain_df after dropping nan: {chain_df.shape}")
        print(f"NaN values in filtered chain_df: {chain_df.isna().sum().sum()}")
        print(chain_df.describe())
        # chain_df = chain_df.loc[:, (slice(None), valid_dates)]
        # chain_df = chain_df.loc[:, dte_mask.any(axis=0)]
        # Expand the mask to match the shape of chain_df
        # This assumes that the mask should apply to all columns in chain_df
        # expanded_mask = np.zeros_like(chain_df, dtype=bool)
        # expanded_mask[:, :dte_mask.shape[1]] = dte_mask

        # Use np.where to apply the expanded mask to all fields
        # broadcasted_mask = np.where(expanded_mask, chain_df, np.nan)

        # Convert the result back to a DataFrame
        # filtered_chain = pd.DataFrame(broadcasted_mask, index=chain_df.index, columns=chain_df.columns)
        filtered_chain = chain_df.where(full_mask)
        # print(f"Shape of chain_df before broadcasting: {filtered_chain.shape}")

        # # Drop columns and rows that are all NaN
        # filtered_chain = filtered_chain.dropna(how='all')
        # filtered_chain = filtered_chain.dropna(axis=1, how='all')

        # # Debugging output
        # print(f"Filtered chain after dropping NaNs: {filtered_chain.head()}")
        # print(f"Shape of filtered_chain after dropping NaNs: {filtered_chain.shape}")

        print(f"Shape of chain_df after filtering: {filtered_chain.shape}")
        print(f"Full dte-filtered chain dates: {filtered_chain.head()}")
        # print(chain_df.describe())

        # # print(f"NaN values in df (before dropping nan): {chain_df.isna().sum().sum()}")
        # chain_df = chain_df.dropna(how='all')
        # chain_df = chain_df.dropna(axis=1, how='all')
        # print(f"NaN values in df (after dropping nan): {chain_df.isna().sum().sum()}")
        # print(f"Shape of chain_df after cleaning: {chain_df.shape}")
        # print(chain_df.describe())

        #  # print(f"Full dte-filtered chain dates: {chain_df.head()}")
        # # print(chain_df.describe())
        filtered_chain.to_pickle("results/signals.pkl")

        sys.exit()

    elif dte_target is not None:
        logger.info(f'Getting dte target {dte_target}')

        dte_values = chain_df.loc[:, ('dte', slice(None))]
        print('dte vals', dte_values.describe())
        print(f"NaN values in dte_values (before filtering): {dte_values.isna().sum().sum()}")

        dte_mask = abs(dte_values - dte_target) < 1
        print("dte mask", dte_mask)
        # valid_dates = dte_values.columns.get_level_values(1)[dte_mask.any(axis=0)]
        filtered_dates = dte_values[dte_mask]
        # chain_df.loc[]
        
        print('filtered vals', dte_values[dte_mask])
        # print("dte mask axis=0", dte_mask.any(axis=0))
        
        print(f"NaN values in dte_values (after filtering): {filtered_dates.isna().sum().sum()}")

        valid_dates = filtered_dates.dropna(axis=0, how='all')
        # valid_dates = valid_dates.dropna(axis=1)

        print("valid dates", valid_dates)

        chain_df = chain_df.loc[:, (slice(None), valid_dates)]
        print(f"Filtered dates df: {chain_df.head()}")

    else:
        logger.error('Need to provide either <dte_target> or <dte_range>')
        raise ValueError
    
    # Determine delta column based on option type
    delta_col = 'p_delta' if option_type == OptionType.PUT else 'c_delta'

    # Filter by delta based on whether we have a target or range
    if delta_range is not None:
        # For puts, convert to negative range if needed
        if option_type == OptionType.PUT and delta_range[0] > 0:
            min_delta, max_delta = -delta_range[1], -delta_range[0]
        else:
            min_delta, max_delta = delta_range
        
        # Filter by delta range
        delta_mask = chain_df.columns.get_level_values(delta_col)
        delta_mask = delta_mask.between(min_delta, max_delta)
        chain_df = chain_df.loc[:, (delta_mask, slice(None))]
        
    elif delta_target is not None:

        # For target delta, find options with delta close to target
        target = delta_target if option_type == OptionType.PUT else abs(delta_target)
        delta_mask = chain_df.columns.get_level_values(delta_col)
        delta_diff = abs(delta_mask - target)
        # chain_df['delta_diff'] = abs(chain_df[delta_col] - target)
        chain_df = chain_df[chain_df['delta_diff'] < 0.05]  # Within 0.05 of target
    
    # Sort by date and delta difference (if it exists)
    if 'delta_diff' in chain_df.columns:
        chain_df = chain_df.sort_values(['delta_diff'])
    
    # Group by date and get the best option for each date
    trade_signals = chain_df.groupby(level=0).first()
    
    logger.info(f"Generated {len(trade_signals)} trade signals")
    # Print the head of the trade signals DataFrame to show a sample of the generated signals
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
        
        logger.info(f"Type of dataframe: {type(df), df.head()}")
        
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

def calculate_daily_value(date, trade_results, pivoted_chain):
    """
    Calculate the daily portfolio value and ROI for a given date based on open positions.
    """
    daily_value = 0
    total_capital_used = 0
    daily_pnl = 0
        
    # logger.info(f"Pivotingoptions chain")

    # # First pivot the options chain for faster lookups
    # pivoted_chain = options_chain.pivot_table(
    #     index=['strike', 'expire_date'],
    #     columns=options_chain.index.normalize(),
    #     values=['p_last', 'c_last'],
    #     aggfunc='first'
    # )
    # logger.info(f"Pivoted options chain {pivoted_chain.head(2)}")
    
    date = date.normalize()
    logger.info("Getting MTM daily value for {date}")
    for trade in trade_results.itertuples():
        if trade.entry_date <= date <= trade.exit_date:
            last_field = 'p_last' if 'put' in trade.option_type else 'c_last'
            
            if date not in pivoted_chain.columns.levels[1]:
                # Find nearest date (faster with columns)
                available_dates = pivoted_chain.columns.levels[1]
                nearest_date = available_dates[available_dates <= date][-1]
                logger.info(f"Found date {nearest_date} before target date {date}.")
                date = nearest_date
            
            try:
                market_value = round(pivoted_chain.loc[(trade.strike, trade.expire_date), (last_field, date)] * 100, 2)
                logger.info(f"Got daily price data for {last_field} on {date}")
                daily_value += market_value
                total_capital_used += trade.capital_used
                
                # Calculate P&L based on position side
                if "long" in trade.position_side.lower():
                    daily_pnl += market_value - trade.entry_price * 100
                elif "short" in trade.position_side.lower():
                    daily_pnl += trade.entry_price * 100 - market_value
                else:
                    logger.error("Position side not recognized")
                    
            except KeyError:
                logger.warning(f"No data for strike {trade.strike}, expiration {trade.expire_date}")
                continue

    roi = (daily_pnl / total_capital_used) if total_capital_used else 0

    return daily_value, daily_pnl, roi

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

    logger.info(f"Starting pivot operation with DataFrame of shape {options_chain.shape}")
    logger.info(f"Memory usage before pivot: {options_chain.memory_usage().sum() / 1024**2:.2f} MB")

    try:
        # Get the index name before resetting
        date_col = options_chain.index.name if options_chain.index.name else 'date'
        logger.info(f"Using date column name: {date_col}")
        
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
        logger.info("Converting to Dask DataFrame")
        dask_df = dd.from_pandas(options_chain, npartitions=4)
        
        # Log the columns before pivot
        logger.info(f"Columns before pivot: {dask_df.columns.tolist()}")
        
        # Pivot operation
        logger.info("Starting pivot table operation with Dask")
        pivoted_chain = dask_df.pivot_table(
            index='strike', 
            columns=date_col,
            values=needed_col,
            aggfunc='first'
        )
        
        logger.info("Computing final result")
        result = pivoted_chain.compute()
        
        # Log the final columns
        logger.info(f"Final columns after pivot: {result.columns.tolist()[:10]}")
        
        logger.info(f"Pivot completed successfully. Result shape: {result.shape}")
        logger.info(f"Memory usage after pivot: {result.memory_usage().sum() / 1024**2:.2f} MB")
        
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
        logger.info("Loading pivoted options chain from pickle")
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
    logger.info("Pivoting options chain using Dask")
    pivoted_chain = pivot_options_chain(options_chain, needed_cols)
    
    logger.info(pivoted_chain.head(2))
    # Save to pickle
    logger.info("Saving pivoted options chain to pickle")
    pivoted_chain.to_pickle(path)
    logger.info(f"Saved pivoted chain to {path}")
    
    return pivoted_chain

def calculate_mtm(start_date, end_date, initial_capital, trade_results, options_chain, param_str, results_dir="../results"):
    """
    Calculate and save mark-to-market (MTM) data for a backtest.
    
    Args:
        start_date: The starting date for the MTM calculation period.
        end_date: The ending date for the MTM calculation period.
        initial_capital: The initial capital amount at the start of the backtest.
        trade_results: The backtest results containing trade data.
        options_chain: DataFrame containing options chain data.
        param_str: A string identifier for the parameter set used in the backtest.
        results_dir: Directory where results will be saved. Defaults to "../results".
    
    Returns:
        None. Results are saved to a CSV file in the specified directory.
    """
    # Ensure the results directory exists
    os.makedirs(results_dir, exist_ok=True)
    
    logger.info(f"Preparing options chain")
    # First pivot the options chain for faster lookups
    pivoted_chain = prepare_options_chain(options_chain, results_dir, param_str)
    
    # Initialize variables
    peak_capital = initial_capital
    current_capital = initial_capital
    daily_data = []

    # Simulate daily portfolio value updates
    for date in pd.date_range(start=start_date, end=end_date):
        # Calculate the portfolio value, daily P&L, and ROI for the current date
        _, daily_pnl, roi = calculate_daily_value(date, trade_results, pivoted_chain)
        
        current_capital += daily_pnl  # Update current capital with daily P&L
        
        # Calculate drawdown in dollars
        drawdown_amount = peak_capital - current_capital
        
        # Update peak capital if current capital is higher
        if current_capital > peak_capital:
            peak_capital = current_capital
        
        # Store daily data
        daily_data.append({
            'Date': date,
            'Portfolio Value': current_capital,
            'Drawdown ($)': drawdown_amount,
            'Drawdown (%)': - drawdown_amount / peak_capital,
            'PnL': daily_pnl,
            'ROI': roi
        })

    # Calculate maximum drawdown percentage
    max_drawdown_amount = daily_df['Drawdown ($)'].max()
    max_drawdown_percentage = - (max_drawdown_amount / peak_capital) * 100

    # Save daily MTM data to CSV
    mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}.csv")
    daily_df = pd.DataFrame(daily_data)
    daily_df.to_csv(mtm_csv_path, index=False)
    logger.info(f"MTM results saved to {mtm_csv_path}")
    
    return daily_df, max_drawdown_amount, max_drawdown_percentage

def run_and_analyze_backtest(data_dir: str, 
                            option_type: OptionType = OptionType.PUT,
                            position_side: PositionSide = PositionSide.SHORT,
                            start_date: str = None,
                            end_date: str = None,
                            delta_target: float = None,
                            delta_range: Tuple[float, float] = None,
                            dte_target: int = None,
                            dte_range: Tuple[int, int] = None,
                            initial_capital: float = 100000,
                            early_close_days: Optional[int] = None,
                            use_preprocessed: bool = False,
                            save_preprocessed: bool = False,
                            save_trades: bool = True) -> pd.DataFrame:
    """
    Load data, generate signals, run backtest and return results.
    """
    # Store original string dates for filename
    start_date_str = start_date
    end_date_str = end_date
    
    # Load data
    start_time = pd.Timestamp.now()
    options_chain, spx_data, vix_data = load_backtest_data(
        data_dir,
        use_preprocessed=use_preprocessed,
        save_preprocessed=save_preprocessed
    )
    load_time = (pd.Timestamp.now() - start_time).total_seconds()
    logger.info(f"Data loading and preprocessing completed in {load_time:.2f} seconds")
    
    # Check data quality
    check_data_quality(options_chain, spx_data, vix_data)

    delta_str = f"delta_{delta_target}" if delta_target else f"delta_{delta_range[0]}-{delta_range[1]}"
    dte_str = f"dte_{dte_target}" if dte_target else f"dte_{dte_range[0]}-{dte_range[1]}"
    early_close_str = f"early_{early_close_days}" if early_close_days else "full_term"
    
    # Format date range for filename using original string dates
    if start_date_str and end_date_str:
        date_range = f"{start_date_str.replace('-', '')}-{end_date_str.replace('-', '')}"
    else:
        date_range = "full_period"
        
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    param_str = f"{option_type.name}_{position_side.name}_{delta_str}_{dte_str}_{early_close_str}_{date_range}"
    
    # Generate trade signals
    signal_time = pd.Timestamp.now()
    trade_signals = generate_trade_signals(
        spx_data, 
        options_chain,
        start_date=start_date,
        end_date=end_date,
        option_type=option_type,
        delta_target=delta_target,
        delta_range=delta_range,
        dte_target=dte_target,
        dte_range=dte_range
    )
    signal_generation_time = (pd.Timestamp.now() - signal_time).total_seconds()
    logger.info(f"Signal generation completed in {signal_generation_time:.2f} seconds")
    
    if trade_signals.empty:
        logger.warning("No trade signals generated with the current parameters.")
        return pd.DataFrame()  # Return empty DataFrame if no signals
    
    # Run backtest
    backtest_time = pd.Timestamp.now()
    logger.info(f"Running backtest for params:\t{param_str}")
    results = run_backtest(
        trade_signals,
        options_chain,
        spx_data,
        option_type=option_type,
        position_side=position_side,
        initial_capital=initial_capital,
        early_close_days=early_close_days
    )
    backtest_execution_time = (pd.Timestamp.now() - backtest_time).total_seconds()
    total_time = (pd.Timestamp.now() - start_time).total_seconds()
    logger.info(f"Backtest execution completed in {backtest_execution_time:.2f} seconds")
    logger.info(f"Total time: {total_time:.2f} seconds")
    
    # Call the MTM function
    daily_df, max_drawdown, max_drawdown_pct = calculate_mtm(start_date, end_date, initial_capital, results, options_chain, param_str)
            
    # Print summary statistics
    logger.info("\nBacktest Results Summary:")
    logger.info(f"Total trades: {len(results)}")
    logger.info(f"Win rate: {(results['pnl'] > 0).mean():.2%}")
    logger.info(f"Average P&L: ${results['pnl'].mean():.2f}")
    logger.info(f"Total P&L: ${results['pnl'].sum():.2f}")
    logger.info(f"Initial capital: ${initial_capital:.2f}")
    logger.info(f"Final capital: ${results['total_capital'].iloc[-1]:.2f}")
    logger.info(f"Return on initial capital: {(results['total_capital'].iloc[-1] / initial_capital - 1):.2%}")
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
    results_dir = '../results'  # Updated to be consistent with logs directory
    os.makedirs(results_dir, exist_ok=True)
    results_csv_path = os.path.join(results_dir, f"results_{param_str}_{timestamp}.csv")
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
    # Example file paths - update these to your actual file paths
    DATA_PATH = "/Users/liefe/Projects/ericlief/Fin/data/spx"
    # SPX_DATA_PATH = os.path.join(DATA_PATH, "spx-daily-1996-ohlc-cleaned.csv")
    # OPTIONS_CHAIN_PATH = os.path.join(DATA_PATH, "options_chain_preprocessed.csv") 
    # VIX_DATA_PATH = os.path.join(DATA_PATH, "vix.csv")
    # The first time, process and save the data
    logger = setup_logger()
    logger.info("First run: processing and saving data for future use...")
    short_put_results = run_and_analyze_backtest(
        DATA_PATH,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        start_date="2020-01-01",
        end_date="2020-12-31",
        delta_range=(0.30, 0.35),  # Will be converted to negative for puts
        dte_range=(28, 31),
        # delta_target=.30,
        # dte_target=10,
        initial_capital=100000,
        early_close_days=None,     # Hold until expiration
        use_preprocessed=True,    # Don't use preprocessed data the first time
        save_preprocessed=True,    # Save the preprocessed data for future use
        save_trades=True           # Save trade results to CSV
    )
    print(short_put_results)
    
    # Subsequent runs can use the saved preprocessed data
    logger.info("\nSecond run: using preprocessed data...")
    long_call_results = run_and_analyze_backtest(
        DATA_PATH,
        option_type=OptionType.CALL,
        position_side=PositionSide.LONG,
        start_date="2020-01-01",
        end_date="2020-12-31",
        delta_target=0.30,
        dte_range=(28, 32),
        initial_capital=100000,
        early_close_days=None,    # Hold until expiration
        use_preprocessed=True,    # Use the saved preprocessed data
        save_preprocessed=False,  # No need to save again
        save_trades=True          # Save trade results to CSV
    )
    print(long_call_results)
    
    # Third run: early close example
    logger.info("\nThird run: early close strategy...")
    early_close_results = run_and_analyze_backtest(
        data_dir=DATA_PATH,
        option_type=OptionType.PUT,
        position_side=PositionSide.SHORT,
        start_date="2020-01-01",
        end_date="2020-06-30",
        delta_range=(0.20, 0.25),
        dte_range=(40, 45),
        initial_capital=100000,
        early_close_days=14,      # Close positions after 14 days (approximately half DTE)
        use_preprocessed=True,    # Use the saved preprocessed data
        save_preprocessed=False,  # No need to save again
        save_trades=True          # Save trade results to CSV
    )
    print(early_close_results)
import sys
import pandas as pd
import numpy as np
import os
from typing import Dict, List, Optional, Tuple, TypedDict, Union
from enum import Enum, auto
import logging
from datetime import datetime
import time
# import dask.dataframe as dd  # Commented out Dask import
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

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
    trade_id: int
    entry_date: pd.Timestamp
    expire_date: pd.Timestamp
    underlying_last: float
    strike: float
    option_type: str
    position_side: str
    bid: float
    ask: float
    premium: float  # Snapshot of the premium at entry
    margin_required: float
    entry_delta: float
    entry_dte: int
    close_date: Optional[pd.Timestamp]  # Optional field

class TradeResult(TypedDict):
    trade_id: int
    option_type: str
    position_side: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expire_date: pd.Timestamp
    entry_delta: float
    exit_delta: float
    entry_dte: Optional[int]
    days_held: int
    underlying_entry: float
    underlying_exit: float
    strike: float
    entry_price: float
    exit_price: float
    pnl: float
    cash: float
    option_bp: float
    return_on_margin: float
    close_reason: str


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
    if isinstance(position_side, str):
        position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
    
    # For long positions, margin is just the cost of the option
    # There is no margin req for Long positions
    if position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
        # return round(entry_price * 100, 2)  # Convert to dollars
        return 0
    
    # For short positions, use IB's formula for Index Options
    else:  # PositionSide.SHORT
        # Calculate out-of-the-money amount
        if option_type == OptionType.PUT:
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

    is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    # if isinstance(option_type, str):
    #     is_put = option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
    # else:
    #     is_put = option_type == OptionType.PUT
    
    logger.debug(f'Expiration. Calculating intrinsic value for {option_type}, strike={strike}, underlying={underlying_price}, is_put:{is_put}')
    logger.debug(f'{max(0, strike - underlying_price) if is_put else max(0, underlying_price - strike)}')

    
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
        Tuple of (closing_price, underlying_close, exit_delta)
    """
    
    delta_col = "p_delta" if position['option_type'] in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_delta"

    # If no close_date, this is an expiration .
    # Calculate intrinsic value, i.e. use underlying price directly
    if 'close_date' not in position or not position['close_date']:
        if position['expire_date'] not in spx_data.index:
            return None, None
        expire_date = position['expire_date']
        underlying_close = spx_data.loc[expire_date, 'close']
        close_price = calculate_intrinsic_value(underlying_close, position['strike'], position['option_type'])
        filtered_df = full_chain_df[
            (full_chain_df.index == expire_date) &
            (full_chain_df['expire_date'] == expire_date) &
            (full_chain_df['strike'] == position['strike'])
        ]
        if not filtered_df.empty:
            exit_delta = round(filtered_df[delta_col].iloc[0], 2)
    
        return close_price, underlying_close, exit_delta
    
    # Early close - get data from close_date forward (up to 5 days)
    close_date = position['close_date']
    date_range = pd.date_range(close_date, close_date + pd.Timedelta(days=5))
    
    filtered_df = full_chain_df[
        (full_chain_df.index.isin(date_range)) & 
        (full_chain_df['expire_date'] == position['expire_date']) &
        (full_chain_df['strike'] == position['strike'])
    ].sort_index()  # Sort by date to try closest dates first
    
    if filtered_df.empty:
        return None, None, None
        
    bid_col = "p_bid" if position['option_type'] in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_bid"
    ask_col = "p_ask" if position['option_type'] in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_ask"
    
    logger.debug(f'{position['option_type']}: {bid_col}-{ask_col}')

    # Try each date in the filtered data until we find valid prices
    for idx, row in filtered_df.iterrows():
        bid = row[bid_col]
        ask = row[ask_col]
        underlying_close = row['underlying_last']
        exit_delta = round(row[delta_col], 2)
        mid_price = calculate_midpoint_price(bid, ask)
        if mid_price is not None:
            logger.debug(f"Using prices from {idx} for close date {close_date}, mid_price={mid_price}, underlying_close={underlying_close}")
            return mid_price, underlying_close, exit_delta
    
    # If we get here, no valid prices were found within 5 days
    logger.error(f"No valid closing prices found within 5 days of {close_date}. Strike: {position['strike']}, "
                f"Type: {position['option_type']}, Expire: {position['expire_date']}. "
                f"Last bid/ask seen: {bid}/{ask}")
    return None, None, None

def calculate_option_pnl(entry_price, closing_price: float) -> float:
    """
    Calculate P&L for option position.
    
    Args:
        entry price: Entry price of the options (signed)
        closing_price: Closing price of the option (signed)
    
    Returns:
        P&L in dollars
    """
    # Calculate P&L using entry and closing prices (signed)
    pnl = entry_price + closing_price
    return pnl * 100 if entry_price > 0 else max(0, pnl * 100) # clamp loss to zero if LONG

def close_position(position: Position, 
                  full_chain_df: pd.DataFrame, 
                  underlying_price_history: pd.DataFrame,
                  option_bp: float) -> Optional[TradeResult]:
    """
    Close an open option position and calculate results.
    
    Args:
        position: Position containing trade details
        full_chain_df: DataFrame containing full option chain data
        underlying_price_history: DataFrame containing underlying price data
        option_bp: Current buying power before closing position
    
    Returns:
        TradeResult if successful, None if closing data is unavailable
    """
    close_reason = None

    # Define minimum valid date for validation
    min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970
    
    # Validate entry_date
    entry_date = position['entry_date']
    if not isinstance(entry_date, pd.Timestamp) or entry_date <= min_valid_date:
        logger.error(f"Invalid entry date: {entry_date} - skipping trade")
        return None
    
    # Early closure, get close date with validation
    if 'close_date' in position and position['close_date'] is not None:
        close_reason = 'early closure'
        close_date = position['close_date']
    elif 'expire_date' in position and position['expire_date'] is not None:
        close_reason = 'expired'
        close_date = position['expire_date']
    else:
        logger.error("Both close_date and expire_date are None in position - skipping trade")
        return None
    
    logger.debug(f'Close Reason: {close_reason}')

    # Validate close_date
    if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
        logger.error(f"Invalid close date: {close_date} - skipping trade")
        return None
    
    # Ensure close_date is not before entry_date
    if close_date < entry_date:
        logger.error(f"Close date {close_date} is before entry date {entry_date} - skipping trade")
        return None
    
    # Get closing prices
    close_price, underlying_close, exit_delta = get_closing_data(position, full_chain_df, underlying_price_history)
    
    # If get_closing_data returned None values, we should skip this trade
    if close_price is None or underlying_close is None:
        logger.warning("Skipping trade due to missing close data")
        return None
    
    # Handle sign for credit/debit prices (i.e. short vs. long)
    signed_close_price = abs(close_price) if position['position_side'] in [PositionSide.LONG, PositionSide.LONG.value, 'long'] else -close_price
    
    logger.debug(f'Premium: {position["premium"]}, Close_price: {signed_close_price}')

    # Calculate P&L using the premium from entry
    pnl = calculate_option_pnl(position['premium'], signed_close_price)
    logger.debug(f'Calculated pnl: {pnl}')
    
    # Calculate final cash using entry cash and exit price only (avoid double counting premium)
    cash = position['entry_cash'] + (signed_close_price * 100)  # Convert to dollars
    logger.debug(f'Final cash: entry_cash + exit_price = {position["entry_cash"]} + {signed_close_price * 100} = {cash}')
    
    # Restore buying power for short positions
    req_margin = position['margin_required']
    if position['position_side'] in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']:
        option_bp += req_margin
        logger.debug(f"Restored buying power ${req_margin:.2f} for closed short position")
    
    # Calculate days held - dates should already be normalized
    days_held = (close_date - entry_date).days
   
    # Safety check for negative days
    if days_held < 0:
        logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
        return None
    
    trade_result: TradeResult = {
        'trade_id': position['trade_id'],
        'option_type': position['option_type'].value if isinstance(position['option_type'], Enum) else str(position['option_type']),
        'position_side': position['position_side'].value if isinstance(position['position_side'], Enum) else str(position['position_side']),
        'entry_date': entry_date,
        'exit_date': close_date,
        'expire_date': position['expire_date'],
        'entry_delta': round(position['entry_delta'], 2),
        'exit_delta': round(exit_delta, 2),
        'entry_dte': position['entry_dte'],
        'days_held': days_held,
        'underlying_entry': position['underlying_last'],
        'underlying_exit': underlying_close,
        'strike': position['strike'], 
        'entry_price': round(position['premium'], 2),
        'exit_price': round(signed_close_price, 2),
        'pnl': round(pnl, 2),
        'capital_used': req_margin,
        'cash': round(cash, 2),
        'option_bp': round(option_bp, 2),
        'return_on_margin': round(pnl / position['margin_required'] * 100, 2) if position['margin_required'] > 0 else 0,
        'close_reason': close_reason
    }
    
    return trade_result

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
    bid_field = "p_bid" if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_bid"
    ask_field = "p_ask" if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_ask"
    
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
    delta_field = "p_delta" if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_delta"
    entry_delta = getattr(trade_signal, delta_field, None)
    
    # Calculate DTE
    entry_dte = (trade_signal.expire_date - trade_signal.Index).days
    
    # Adjust entry price sign based on position side
    # For long positions, entry price should be negative (cash outflow)
    # For short positions, entry price should be positive (cash inflow)
    signed_entry_price = -entry_price if position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long'] else entry_price
    
    # Calculate initial margin
    init_margin = calculate_margin(underlying_price, abs(entry_price), position_side, trade_signal.strike, option_type)  # Use absolute entry price for margin
    
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
        'premium': signed_entry_price,
        'margin_required': init_margin,
        'close_date': None,
        'entry_delta': round(entry_delta, 2) if entry_delta is not None else None,
        'entry_dte': entry_dte,
        'entry_cash': 0
    }
    return position

def execute_trade(trade: Position, cash: float, option_bp: float, leverage: float = 4.0) -> Tuple[Optional[Position], float, float]:
    
    # Unsign here for comparison
    entry_price = abs(trade['premium'])
    # Calculate effective margin requirement with leverage
    effective_margin = trade['margin_required'] / leverage
    
    # Open LONG position
    if trade['position_side'] in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
        # For long positions, check if there is enough cash to buy the option
        if cash >= entry_price * 100:  # Cash needed to buy the option
            cash -= entry_price * 100  # Deduct premium (convert to dollars)
            trade['entry_cash'] = cash  # Store cash snapshot at entry
            return trade, cash, option_bp
        else:
            logger.warning(f"Insufficient cash (${cash}) to buy option on {trade['entry_date']}. Required: ${abs(trade['premium']) * 100:.2f}")
            return None, cash, option_bp

    # Open SHORT position
    elif trade['position_side'] in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']:
        # For short positions, check if buying power is sufficient
        if option_bp >= effective_margin:
            option_bp -= effective_margin
            cash += entry_price * 100  # Credit premium
            trade['entry_cash'] = cash  # Store cash snapshot at entry
            return trade, cash, option_bp
        else:
            logger.warning(f"Insufficient buying power (${option_bp}) for trade on {trade['entry_date']}. Requires: ${effective_margin:.2f} with {leverage}x leverage")
            return None, cash, option_bp

    else:
        logger.error('Position side not recognized')
        return None, cash, option_bp
    
def execute_backtest_trades(trades: pd.DataFrame,
                            full_chain_df: pd.DataFrame, 
                            underlying_price_history: pd.DataFrame,
                            option_type: OptionType,
                            position_side: PositionSide,
                            initial_capital: float = 100000.00,
                            max_positions: int = 1,
                            leverage: float = 1.0,
                            early_close_days: int = None,
                            delta_target: float = None
                           ) -> pd.DataFrame:
    """
    Execute a series of trades and track results.
    """
    cash = initial_capital
    options_bp = initial_capital
    open_positions: List[Position] = []
    trade_results: List[TradeResult] = []
    skipped_trades = 0
    total_trades = len(trades)
    trade_counter = 1
    
    # Sort trades by entry date
    trades = trades.sort_index()
    
    for _, trade_signal in trades.iterrows():
        current_date = trade_signal.name
        
        # First, check if any open positions need to be closed
        positions_to_remove = []
        for pos in open_positions:
            # Close position if we're on/past the close_date or expire_date
            if (('close_date' in pos and pos['close_date'] is not None and current_date >= pos['close_date']) or
                ('expire_date' in pos and pos['expire_date'] is not None and current_date >= pos['expire_date'])):
                
                logger.debug(f'Closing position: {pos}')
                result = close_position(pos, full_chain_df, underlying_price_history, options_bp)
                if result:
                    # Update cash and BP from the trade result
                    cash = result['cash']
                    options_bp = result['option_bp']
                    positions_to_remove.append(pos)
                    logger.debug(f"Closed position - Cash: ${cash:.2f}, BP: ${options_bp:.2f}")
                    trade_results.append(result)
        
        # Remove closed positions
        for pos in positions_to_remove:
            open_positions.remove(pos)
        
        # Skip if we've reached max positions
        if len(open_positions) >= max_positions:
            skipped_trades += 1
            continue
        
        # Check if this trade meets our delta criteria before attempting execution
        delta_col = "p_delta" if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_delta"
        trade_delta = trade_signal[delta_col]
        
        # For puts, we want negative deltas, so convert positive input to negative
        if option_type in [OptionType.PUT, OptionType.PUT.value, "put"]:
            target_delta = -abs(delta_target)
            delta_diff = abs(trade_delta - target_delta)
        else:
            target_delta = abs(delta_target)
            delta_diff = abs(trade_delta - target_delta)
        
        # Skip trades that are too far from our target delta
        if delta_diff > 0.05:  # Allow 5% deviation from target delta
            logger.debug(f"Skipping trade with delta {trade_delta:.2f} (target: {target_delta:.2f}, diff: {delta_diff:.2f})")
            skipped_trades += 1
            continue
        
        # Create Position from trade signal
        entry_price = calculate_midpoint_price(
                trade_signal['p_bid'] if option_type in [OptionType.PUT, OptionType.PUT.value, 'put'] else trade_signal['c_bid'],
                trade_signal['p_ask'] if option_type in [OptionType.PUT, OptionType.PUT.value, 'put'] else trade_signal['c_ask']
            )
        
        position = Position(
            trade_id=trade_counter,
            entry_date=current_date,
            expire_date=trade_signal['expire_date'],
            underlying_last=trade_signal['underlying_last'],
            strike=trade_signal['strike'],
            option_type=option_type,
            position_side=position_side,
            bid=trade_signal['p_bid'] if option_type in [OptionType.PUT, OptionType.PUT.value, 'put'] else trade_signal['c_bid'],
            ask=trade_signal['p_ask'] if option_type in [OptionType.PUT, OptionType.PUT.value, 'put'] else trade_signal['c_ask'],
            premium=entry_price if position_side in [PositionSide.SHORT.value, PositionSide.SHORT, 'short'] else -entry_price,
            margin_required=trade_signal['margin_required'] if 'margin_required' in trade_signal else 0,
            close_date=current_date + pd.Timedelta(days=early_close_days) if early_close_days is not None else None,
            entry_delta=trade_signal['p_delta'] if option_type in [OptionType.PUT, OptionType.PUT.value, 'put'] else trade_signal['c_delta'],
            entry_dte=trade_signal['dte'] if 'dte' in trade_signal else None
        )
            
        # Try to execute the new trade
        executed_trade, cash, options_bp = execute_trade(position, cash, options_bp, leverage)
        if executed_trade:
            executed_trade['trade_id'] = trade_counter
            open_positions.append(executed_trade)
            trade_counter += 1  # Increment counter only for successful trades
            logger.debug(f'Opened position: {executed_trade}'
                         f'Cash: ${cash:.2f}, BP: ${options_bp:.2f}'
                         f'signal: {trade_signal}')
        else:
            skipped_trades += 1
    
    # Close any remaining open positions at their expiration
    for pos in open_positions:
        result = close_position(pos, full_chain_df, underlying_price_history, options_bp)
        if result:
            trade_results.append(result)
            cash = result['cash']
            options_bp = result['option_bp']
    
    if not trade_results:
        logger.warning("No trades were executed successfully")
        return pd.DataFrame()
    
    results_df = pd.DataFrame(trade_results)
    
    # Calculate cumulative metrics based on PnL
    results_df['cumulative_pnl'] = results_df['pnl'].cumsum()
    results_df['capital'] = initial_capital + results_df['cumulative_pnl']  # Track actual capital based on cumulative PnL
    results_df['peak_capital'] = results_df['capital'].cummax()
    results_df['drawdown'] = results_df['capital'] - results_df['peak_capital']
    results_df['drawdown_pct'] = round(results_df['drawdown'] / results_df['peak_capital'] * 100, 2)
    
    # Log statistics
    total_trades = len(results_df)
    winning_trades = (results_df['pnl'] > 0).sum()
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    
    logger.info(f"\nBacktest Results:")
    logger.info(f"Total trades executed: {total_trades}")
    logger.info(f"Winning trades: {winning_trades}")
    logger.info(f"Win rate: {win_rate:.2%}")
    logger.info(f"Initial capital: ${initial_capital:,.2f}")
    logger.info(f"Total P&L: ${results_df['cumulative_pnl'].iloc[-1]:,.2f}")
    logger.info(f"Final capital: ${results_df['capital'].iloc[-1]:,.2f}")
    logger.info(f"Final buying power: ${options_bp:,.2f}")
    
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

    logger.debug(f'Generating trade signals for {option_type}...')
    
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
        logger.debug('Sample chain')
        logger.debug(chain_df.head())

    # Filter by DTE based on whether we have a single value or range
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
    delta_col = 'p_delta' if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else 'c_delta'
    
    # Filter by delta parameters
    if delta_range:
        # Handle range case
        if option_type in [OptionType.PUT, OptionType.PUT.value, "put"]:
            # For puts, we want negative deltas, so convert positive input to negative
            min_delta = -abs(delta_range[1])  # More negative (further OTM)
            max_delta = -abs(delta_range[0])  # Less negative (closer to ATM)
        else:
            # For calls, we want positive deltas
            min_delta = abs(delta_range[0])  # Less positive (closer to ATM)
            max_delta = abs(delta_range[1])  # More positive (further OTM)
        
        logger.debug(f'Filtering for delta range: {min_delta} to {max_delta} for {option_type.value}')
        delta_mask = chain_df[delta_col].between(min_delta, max_delta)
        chain_df = chain_df[delta_mask]
        
        # Sort by delta value
        ascending = (option_type == OptionType.CALL)  # Ascending for calls, descending for puts
        chain_df = chain_df.reset_index().sort_values(by=['index', delta_col],
                                                     ascending=[True, ascending])
        trade_signals = chain_df.set_index('index')
        logger.debug('Sample chain after delta filtering')
        logger.debug(chain_df.head())

    elif delta_target:
        # Handle target case
        if option_type in [OptionType.PUT, OptionType.PUT.value, "put"]:
            # logger.debug(f'Got put {option_type}')
            # For puts, we want negative deltas
            target = -abs(delta_target)
            # For puts, we want to find options with deltas closest to the target (more negative)
            ascending = False
            # logger.debug(f'Got put {option_type}, delta={target}, ascending={ascending}')

        else:
# For calls, we want positive deltas
            target = abs(delta_target)
            # For calls, we want to find options with deltas closest to the target (more positive)
            ascending = True
            # logger.debug(f'Got call {option_type}, delta={target}, ascending={ascending}')


        logger.debug(f'Filtering for delta target: {target} for {option_type.value}')
        delta_diff = abs(chain_df[delta_col] - target)
        chain_df = chain_df.assign(delta_diff=delta_diff)
        
        # Sort by delta difference and delta value
        chain_df = chain_df.reset_index().sort_values(by=['index', 'delta_diff', delta_col],
                                                     ascending=[True, True, ascending])
        trade_signals = chain_df.set_index('index')
        logger.debug('Sample chain after delta target filtering')
        logger.debug(chain_df.head())
    else:
        logger.error('Need to provide either delta_target or delta_range')
        raise ValueError
    
    # Add option_type to the trade signals
    # trade_signals['option_type'] = option_type.value
    
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
            market_value = round(close * 100, 2)
            # logger.debug(f'Calculated intrinsic value on date={date} for strike={trade.strike} and value={market_value}')

        # Either MTM daily or early closure, so calculate mid point of bid/ask quote
        else:
            bid_col = 'p_bid' if trade.option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_bid"
            ask_col = 'p_ask' if trade.option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else "c_ask"
            bid = price_data[bid_col].iloc[0] 
            ask = price_data[ask_col].iloc[0] 
            mid = calculate_midpoint_price(bid, ask)
            if mid is None:
                logger.warning(f"Invalid bid/ask prices on {date} for strike {trade.strike}: bid={bid}, ask={ask}")
                return None
            market_value = round(100 * mid, 2)
            # logger.debug(f'Calculated mid value on date={date} for strike={trade.strike}, bid={bid}, ask={ask}, mid={mid}, value={market_value}')
        
        # Validate sign of value according to PositionSide
        try:
            if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
                assert market_value >= 0 
            else:
                assert trade.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']
                assert market_value <= 0      
        except AssertionError as e:
            if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
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

                # Close trade
                if trade_end == date:
                    logger.debug(f'Closing trade: {trade_id}')
                    # Release margin back to BP for short positions
                    if trade.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']:
                        option_bp += active_trades[trade_id]['margin_requirement']
                    
                    # Validate closing/exit price sign 
                    exit_price = trade.exit_price
                    try:
                        if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
                            assert exit_price >= 0 
                        else:
                            assert trade.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']
                            assert exit_price <= 0      
                    except AssertionError as e:
                        if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
                            exit_price = abs(exit_price)
                        else:
                            exit_price = -abs(exit_price)
                    logger.debug(f'exit price: {exit_price}')
                    logger.debug(f'daily cash before: {daily_cash_flow}')
                    # Accumulate this to cash reserves
                    daily_cash_flow += exit_price * 100  # Premium in dollars
                    logger.debug(f'daily cash after: {daily_cash_flow}')

                    del active_trades[trade_id]
                # Update trade
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
                    
                    entry_price = round(trade.entry_price * 100, 2)  # in dollars

                    # Validate sign of entry price acc. to PositionSide
                    try:
                        if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
                            assert entry_price > 0 
                        else:
                            assert trade.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']
                            assert entry_price < 0      
                    except AssertionError as e:
                        if trade.position_side in [PositionSide.LONG, PositionSide.LONG.value, 'long']:
                            entry_price = -abs(entry_price)
                        else:
                            entry_price = abs(entry_price)

                    # Accumulate entry price to cash flow (signed based on position side)
                    logger.debug(f'entry price: {entry_price}')
                    logger.debug(f'daily cash before: {daily_cash_flow}')
                    daily_cash_flow += entry_price  # Already signed in the trade
                    logger.debug(f'daily cash after: {daily_cash_flow}')

                    # Update position value and margin
                    daily_position_value += position_value
                    req_margin = trade.capital_used
                    daily_margin_requirement += req_margin
                    
                    # For short positions, reduce BP
                    if trade.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, 'short']:
                        option_bp -= req_margin / leverage  # Account for leverage in BP reduction
                    
                    logger.debug(f'Position Value: {position_value}, Entry Premium: {entry_price}')
                    logger.debug(f'Option BP: {option_bp}, Cash: {cash}')

        # Update cumulative P&L
        cumulative_pnl += daily_pnl
        
        # Update cash with daily premium flows
        cash += daily_cash_flow
        
        # Calculate net liquidation value
        net_liq = cash + daily_position_value
        
        # Update peak liquidity if net liquidation value is higher
        if net_liq > peak_liquidity:
            peak_liquidity = net_liq
            
        # Calculate drawdown
        drawdown_amount = - max(0, round(peak_liquidity - net_liq, 2))
        drawdown_pct = (drawdown_amount / peak_liquidity * 100) if peak_liquidity > 0 else 0

        # Calculate ROI metrics
        daily_roi = round(daily_pnl / daily_margin_requirement * 100, 2) if daily_margin_requirement > 0 else 0
        total_roi = round((net_liq - initial_capital) / initial_capital * 100, 2)
        
        # Store daily data with expanded metrics
        daily_data.append({
            'Date': date,
            'Net Liquidity': round(net_liq, 2),
            'Options BP': round(option_bp, 2),
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
        logger.debug(f'  Options BP: ${option_bp:.2f}')
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

def log_to_google_sheets(results_df: pd.DataFrame, param_str: str, daily_df: pd.DataFrame = None):
    """
    Log backtest results to Google Sheets.
    
    Args:
        results_df: DataFrame containing trade results
        param_str: String describing the backtest parameters
        daily_df: Optional DataFrame containing daily MTM data
    """
    try:
        # Set up credentials
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive']
        
        # Load credentials from environment variable or file
        creds_json = os.getenv('GOOGLE_CREDS_JSON')
        if creds_json:
            creds_dict = json.loads(creds_json)
        else:
            creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
            with open(creds_path, 'r') as f:
                creds_dict = json.load(f)
        
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(credentials)
        
        # Open the spreadsheet
        spreadsheet = gc.open('Options Backtest Results')
        
        # Create a new worksheet for this backtest
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        worksheet_name = f"Backtest_{timestamp}"
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
        
        # Write summary statistics
        summary_data = [
            ['Backtest Summary', ''],
            ['Timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['Parameters', param_str],
            ['Total Trades', len(results_df)],
            ['Win Rate', f"{(results_df['pnl'] > 0).mean():.2%}"],
            ['Average P&L', f"${results_df['pnl'].mean():.2f}"],
            ['Total P&L', f"${results_df['pnl'].sum():.2f}"],
            ['Initial Capital', f"${results_df['cash'].iloc[0]:.2f}"],
            ['Final Capital', f"${results_df['cash'].iloc[-1]:.2f}"],
            ['Return on Capital', f"{(results_df['cash'].iloc[-1] / results_df['cash'].iloc[0] - 1):.2%}"],
            ['Average Days Held', f"{results_df['days_held'].mean():.1f}"],
            ['Average Return on Margin', f"{results_df['return_on_margin'].mean():.2f}%"],
            ['Maximum Drawdown', f"${results_df['drawdown'].min():.2f} ({results_df['drawdown_pct'].min():.2f}%)"],
            ['', ''],
            ['Trade Results', '']
        ]
        
        # Write summary data
        worksheet.update('A1', summary_data)
        
        # Write trade results
        if not results_df.empty:
            # Prepare trade results data
            trade_results = results_df[[
                'entry_date', 'exit_date', 'strike', 'option_type', 
                'position_side', 'entry_price', 'exit_price', 'pnl',
                'days_held', 'return_on_margin'
            ]].copy()
            
            # Format dates
            trade_results['entry_date'] = trade_results['entry_date'].dt.strftime('%Y-%m-%d')
            trade_results['exit_date'] = trade_results['exit_date'].dt.strftime('%Y-%m-%d')
            
            # Write headers
            worksheet.update('A15', [trade_results.columns.tolist()])
            # Write data
            worksheet.update('A16', trade_results.values.tolist())
        
        # Write daily MTM data if available
        if daily_df is not None:
            # Add a separator
            worksheet.update(f'A{len(results_df) + 20}', [['', ''], ['Daily MTM Data', '']])
            
            # Prepare daily data
            daily_data = daily_df[[
                'Date', 'Net Liquidity', 'Position Value', 
                'Daily P&L', 'Cumulative P&L', 'Drawdown (%)'
            ]].copy()
            
            # Format dates
            daily_data['Date'] = daily_data['Date'].dt.strftime('%Y-%m-%d')
            
            # Write headers
            worksheet.update(f'A{len(results_df) + 22}', [daily_data.columns.tolist()])
            # Write data
            worksheet.update(f'A{len(results_df) + 23}', daily_data.values.tolist())
        
        logger.info(f"Results logged to Google Sheets in worksheet: {worksheet_name}")
        
    except Exception as e:
        logger.error(f"Error logging to Google Sheets: {str(e)}")
        raise

def run_backtest(
    *,
    spx_file_path: str,
    options_chain_file_path: str,
    option_type: OptionType,
    position_side: PositionSide,
    delta_target: float,
    use_spx_close: bool = False,
    start_date: str = None,
    end_date: str = None,
    dte_range: tuple = (28, 31),
    initial_capital: float = 100000,
    early_close_days: int = None,
    use_preprocessed: bool = True,
    save_preprocessed: bool = True,
    save_trades: bool = True,
    preloaded_data: dict = None,
    log_to_sheets: bool = True,
    max_margin_utilization: float = 0.80,  # Maximum percentage of capital that can be used for margin
    leverage: float = 1.0,  # New parameter: leverage multiplier (e.g. 4.0 for 4x leverage)
    max_positions: int = 1  # Maximum number of simultaneous positions allowed
) -> pd.DataFrame:
    """
    Run a backtest with the given parameters.
    
    Args:
        ... (existing args) ...
        max_margin_utilization: Maximum percentage of capital that can be used for margin (0.0 to 1.0)
        leverage: Leverage multiplier for margin requirements (e.g. 4.0 for 4x leverage)
        max_positions: Maximum number of simultaneous positions allowed (default: 1)
    """
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
    
    # Generate trade signals
    signal_start = time.time()
    trade_signals = generate_trade_signals(
        spx_data, 
        options_chain,
        option_type=option_type,
        delta_target=delta_target,
        delta_range=None,
        dte_target=dte_range[0],
        dte_range=dte_range,
        start_date=start_date,
        end_date=end_date
    )
    signal_time = time.time() - signal_start
    logger.info(f"Signal generation completed in {signal_time:.2f} seconds")
    
    if trade_signals.empty:
        logger.warning("No trade signals generated with the current parameters.")
        return pd.DataFrame()
    
    # Pre-calculate margin requirements for all signals
    logger.info(f"Calculating margin requirements for trade signals for {option_type} | {position_side}...")
    trade_signals['margin_required'] = trade_signals.apply(
        lambda row: calculate_margin(
            row['underlying_last'],
            (row['p_bid'] + row['p_ask']) / 2 if option_type in [OptionType.PUT, OptionType.PUT.value, "put"] else (row['c_bid'] + row['c_ask']) / 2,
            position_side,
            row['strike'],
            option_type
        ),
        axis=1
    )
    
    # Filter out trades that would exceed margin limits
    valid_signals = trade_signals[trade_signals['margin_required'] <= max_allowed_margin]
    filtered_count = len(trade_signals) - len(valid_signals)
    if filtered_count > 0:
        logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
        logger.info(f"Average margin requirement for filtered trades: ${trade_signals['margin_required'].mean():.2f}")
        logger.info(f"Maximum margin requirement for filtered trades: ${trade_signals['margin_required'].max():.2f}")
    
    # Run backtest with valid signals
    backtest_start = time.time()
    logger.info(f"Running backtest with {len(valid_signals)} valid trades")
    trade_results = execute_backtest_trades(
        valid_signals,
        options_chain,
        spx_data,
        option_type=option_type,
        position_side=position_side,
        initial_capital=initial_capital,
        max_positions=max_positions,
        leverage=leverage,
        early_close_days=early_close_days,
        delta_target=delta_target  # Pass delta_target through
    )
    backtest_time = time.time() - backtest_start
    logger.info(f"Backtest execution completed in {backtest_time:.2f} seconds")
    
    # Calculate MTM
    mtm_start = time.time()
    param_str = f"{option_type.value}_{position_side.value}_delta{delta_target}_dte{dte_range[0]}-{dte_range[1]}"
    daily_df, max_drawdown, max_drawdown_pct = calculate_mtm(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        trade_results= trade_results,
        options_chain_multi_index=options_chain_multi_index,
        spx_data=spx_data,
        param_str=param_str,
        use_spx_close=use_spx_close,
        leverage=leverage
    )
    mtm_time = time.time() - mtm_start
    logger.info(f"MTM calculation completed in {mtm_time:.2f} seconds")
    
    # Add margin utilization metrics totrade_results
    if not trade_results.empty:
        trade_results['margin_utilization'] = round(trade_results['capital_used'] / initial_capital, 2)
        avg_margin_util = trade_results['margin_utilization'].mean()
        max_margin_util = trade_results['margin_utilization'].max()
        logger.info(f"Average margin utilization: {avg_margin_util:.2%}")
        logger.info(f"Maximum margin utilization: {max_margin_util:.2%}")
    
    # Print summary statistics
    logger.info("\nBacktest Results Summary:")
    logger.info(f"Total trades: {len(trade_results)}")
    logger.info(f"Win rate: {(trade_results['pnl'] > 0).mean():.2%}")
    logger.info(f"Average P&L: ${trade_results['pnl'].mean():.2f}")
    logger.info(f"Total P&L: ${trade_results['pnl'].sum():.2f}")
    logger.info(f"Initial capital: ${initial_capital:.2f}")
    logger.info(f"Final capital: ${trade_results['cash'].iloc[-1]:.2f}")
    logger.info(f"Return on initial capital: {(trade_results['cash'].iloc[-1] / initial_capital - 1):.2%}")
    logger.info(f"Average days held: {trade_results['days_held'].mean():.1f}")
    logger.info(f"Average return on margin: {trade_results['return_on_margin'].mean():.2f}%")
    logger.info(f"Maximum drawdown: ${max_drawdown:.2f} ({max_drawdown_pct:.2f}%)")
    
    # Calculate Sharpe Ratio without risk-free rate
    sharpe = None
    if len(trade_results) > 1:
        returns = np.diff(trade_results['cash'].values) / trade_results['cash'].values[:-1]
        if len(returns) > 0 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
            logger.info(f"Sharpe Ratio: {sharpe:.2f}")
    
    # Save summary trade_results to CSV
    if save_trades:
        save_start = time.time()
        results_dir = 'results'
        os.makedirs(results_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trades_csv_path = os.path.join(results_dir, f"trades_{param_str}_{timestamp}.csv")
        trade_results.to_csv(trades_csv_path, index=False)
        
        # Save MTM results with same timestamp
        mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}_{timestamp}.csv")
        daily_df.to_csv(mtm_csv_path, index=False)
        
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
    logger.info(f"- Results saving: {save_time:.2f} seconds ({save_time/total_time*100:.1f}%)")
    
    # Log to Google Sheets if enabled
    if log_to_sheets and not trade_results.empty:
        try:
            log_to_google_sheets(trade_results, param_str, daily_df)
        except Exception as e:
            logger.error(f"Failed to log to Google Sheets: {str(e)}")
    
    return trade_results

def run_multiple_backtests(
    spx_file_path: str,
    options_chain_file_path: str,
    hyperparameter_sets: list,
    use_preprocessed: bool = True,
    save_preprocessed: bool = True,
    max_positions: int = 1  # Add max_positions parameter with default value
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
            
        logger.debug(f'running with params {params}')

        # Extract required parameters from params
        required_params = {
            'spx_file_path': spx_file_path,
            'options_chain_file_path': options_chain_file_path,
            'option_type': params['option_type'],
            'position_side': params['position_side'],
            'delta_target': params['delta_target'],
            'preloaded_data': preloaded_data
        }
        
        # Add optional parameters from params
        optional_params = {k: v for k, v in params.items() if k not in required_params}
        
        result = run_backtest(
            **required_params,
            **optional_params
        )
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

def calculate_net_liq(cash: float, open_positions: List[Position]) -> float:
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
        market_value = calculate_intrinsic_value(position['underlying_last'], position['strike'], position['option_type'])
        total_value += market_value * 100  # Convert to dollars
    
    return total_value

# Example usage:
if __name__ == "__main__":

    DATA_PATH = "/Users/liefe/Data/spx"    
    SPX_FILE = os.path.join(DATA_PATH, "spx_2018_2023.csv")
    OPTIONS_FILE = os.path.join(DATA_PATH, "spx_options_2018_2023.csv")


    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    # Example file paths
    # DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data")
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

    # Example hyperparameter sets
    hyperparameter_sets = [
        {
            'option_type': OptionType.PUT,
            'position_side': PositionSide.SHORT,
            'delta_target': 0.30,
            'use_spx_close': True,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (28, 31),
            'initial_capital': 100000,
            'early_close_days': None
        },
        {
            'option_type': OptionType.PUT,
            'position_side': PositionSide.SHORT,
            'delta_target': 0.25,
            'use_spx_close': True,
            'start_date': "2020-01-01",
            'end_date': "2020-12-31",
            'dte_range': (28, 31),
            'initial_capital': 100000,
            'early_close_days': 5
        }
    ]

    # Run multiple backtests
    results = run_multiple_backtests(
        spx_file_path="/Users/liefe/Data/spx/spx_2018_2023.csv",
        options_chain_file_path="/Users/liefe/Data/spx/spx_options_2018_2023.csv",
        hyperparameter_sets=hyperparameter_sets
    )
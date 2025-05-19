from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple
from functools import cached_property

import pandas as pd
import numpy as np

from options_bt.domain.enums import *  
from options_bt.domain.base_position import BasePosition
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

@dataclass
class OptionPosition(BasePosition):
    """Core option position. Represents a single 'open' option contract position."""
    # Required parameters (no defaults)

    option_type: Union[OptionType, str]
    strike: float
    expire_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_delta: float
    entry_dte: int
    underlying_entry: float

    # Optional parameters (with defaults)
    margin_required: Optional[float] = None  # Store margin requirement

    # Should go into Trade class
    # exit_date: Optional[pd.Timestamp] = None
    # exit_price: Optional[float] = None
    # exit_delta: Optional[float] = None
    # underlying_exit: Optional[float] = None
    close_date: Optional[pd.Timestamp] = None  # For early closure

    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.option_type, str):
            self.option_type = OptionType(self.option_type.lower())
      
        
        # Calculate margin required based on entry price and underlying entry
        # if self.entry_price is not None and self.underlying_entry is not None:
        #     self.margin_required = self.calculate_margin()

    @cached_property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]

    @cached_property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]

    @cached_property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]

    @cached_property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]

    def is_closed(self) -> bool:
        """Check if position is closed based on exit information."""
        pass
        # return (self.exit_date is not None or  # Normal exit
        #         (self.expire_date is not None and pd.Timestamp.now() >= self.expire_date))  # Expired

    @property
    def is_open(self) -> bool:
        """Check if position is currently open."""
        return not self.is_closed()

    @cached_property
    def get_signed_entry_price(self) -> float:
        """
        Get the entry price with correct sign based on position side.
        - Long positions should have negative entry price (debit/BTO)
        - Short positions should have positive entry price (credit/STO)
        """
        if not self.entry_price:
            return 0
            
        try:
            if self.is_long:
                assert self.entry_price <= 0  # debit premium, buy to open (BTO)
            elif self.is_short:
                assert self.entry_price >= 0  # credit premium, sell to open (STO)
        except AssertionError:
            logger.debug(f'Fixing sign of entry price {self.entry_price}')
            return -abs(self.entry_price) if self.is_long else abs(self.entry_price)
            
        return self.entry_price

    @cached_property
    def get_signed_exit_price(self) -> float:
        """
        Get the exit price with correct sign based on position side.
        - Long positions should have positive exit price (credit/STC)
        - Short positions should have negative exit price (debit/BTC)
        """
        if not self.exit_price:
            return 0
            
        try:
            if self.is_long:
                assert self.exit_price >= 0  # sell to close (STC)
            elif self.is_short:
                assert self.exit_price <= 0  # buy to close (BTC)
        except AssertionError:
            logger.debug(f'Fixing sign of exit price {self.exit_price}')
            return abs(self.exit_price) if self.is_long else -abs(self.exit_price)
            
        return self.exit_price

    def calculate_margin(self, leverage: float = 1.0) -> float:
        """Calculate margin requirement for the position."""
        if not self.entry_price or not self.underlying_entry:
            return 0

        if self.is_long:
            return 0  # No margin required for long positions
        
        # For short positions, use IB's formula
        margin_req_percent = 0.15  # 15% for index options
        
        # Calculate out-of-the-money amount
        if self.is_put:
            otm_amount = max(0, self.underlying_entry - self.strike)
        else:  # Call
            otm_amount = max(0, self.strike - self.underlying_entry)
        
        # IB's margin formula
        margin = (
            abs(self.entry_price) +  # Option price
            max(
                (margin_req_percent * self.underlying_entry - otm_amount),
                (0.10 * self.underlying_entry)
            )
        ) * 100 * self.quantity  # Convert to dollars
        
        return round(margin / leverage, 2)

    def calculate_pnl(self, exit_price: Optional[float] = None) -> float:
        """
        Calculate P&L for the position.
        
        Args:
            exit_price: Optional exit price. If not provided, returns 0 (unrealized P&L).
        """
        if exit_price is None:
            return 0
            
        # Get correctly signed prices
        entry = self.get_signed_entry_price
        # For long positions, exit price should be positive (credit/STC)
        # For short positions, exit price should be negative (debit/BTC)
        signed_exit = abs(exit_price) if self.is_long else -abs(exit_price)
        
        pnl = entry + signed_exit  # Signs are already correct
        
        # For long positions, clamp loss to zero
        if self.is_long:
            return max(0, pnl * 100 * self.quantity)
        return pnl * 100 * self.quantity

    def close_position(self, 
                      full_chain_df: pd.DataFrame, 
                      underlying_price_history: pd.DataFrame,
                      option_bp: float) -> Optional[Dict]:
        """
        Close this position and calculate results.
        
        Args:
            full_chain_df: DataFrame containing full option chain data.
            underlying_price_history: DataFrame containing underlying price data.
            option_bp: Current buying power before closing position.
        
        Returns:
            Optional[Dict]: Trade result dictionary if successful, None if closing data is unavailable.
        """
        close_reason = None
        min_valid_date = pd.Timestamp('1990-01-01')
        
        # Validate entry_date
        if not isinstance(self.entry_date, pd.Timestamp) or self.entry_date <= min_valid_date:
            logger.error(f"Invalid entry date: {self.entry_date} - skipping trade")
            return None
        
        # Early closure, get close date with validation
        if self.close_date is not None:
            close_reason = 'early closure'
            close_date = self.close_date
        elif self.expire_date is not None:
            close_reason = 'expired'
            close_date = self.expire_date
        else:
            logger.error("Both close_date and expire_date are None - skipping trade")
            return None
        
        logger.debug(f'Close Reason: {close_reason}')

        # Validate close_date
        if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
            logger.error(f"Invalid close date: {close_date} - skipping trade")
            return None
        
        # Ensure close_date is not before entry_date
        if close_date < self.entry_date:
            logger.error(f"Close date {close_date} is before entry date {self.entry_date} - skipping trade")
            return None
        
        # Get closing data
        closing_data = self._get_closing_data(full_chain_df, underlying_price_history)
        if closing_data is None:
            logger.warning("Skipping trade due to missing close data")
            return None
            
        # Update position with closing data
        self.exit_price = closing_data['exit_price']
        self.exit_delta = closing_data['exit_delta']
        self.underlying_exit = closing_data['underlying_exit']
        
        # Calculate P&L
        pnl = self.calculate_pnl()
        logger.debug(f'Calculated pnl: {pnl}')
        
        # Update buying power
        premium = self.get_signed_exit_price * 100 * self.quantity
        option_bp += premium  # Add/subtract exit premium (already signed)

        # Restore margin for short positions
        if self.is_short:
            option_bp += self.margin_required

        # Calculate days held
        days_held = pd.Timedelta(close_date - self.entry_date).days
        if days_held < 0:
            logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
            return None
        
        # Prepare trade result
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'option_type': self.option_type.value if isinstance(self.option_type, Enum) else str(self.option_type),
            'position_side': self.position_side.value if isinstance(self.position_side, Enum) else str(self.position_side),
            'entry_date': self.entry_date,
            'exit_date': close_date,
            'expire_date': self.expire_date,
            'entry_delta': round(self.entry_delta, 2),
            'exit_delta': round(self.exit_delta, 2),
            'entry_dte': self.entry_dte,
            'days_held': days_held,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'entry_price': round(self.entry_price, 2),
            'exit_price': round(self.exit_price, 2),
            'capital_used': self.margin_required,
            'option_bp': round(option_bp, 2),
            'return_on_margin': round(pnl / self.margin_required * 100, 2) if self.margin_required > 0 else 0,
            'close_reason': close_reason,
            'pnl': round(pnl, 2),
            'spread_type': getattr(self, 'spread_type', SpreadType.NONE.value),
            'spread_id': getattr(self, 'spread_id', None),
            'leg_number': getattr(self, 'leg_number', None)
        }

    def _get_closing_data(self, full_chain_df: pd.DataFrame, spx_data: pd.DataFrame) -> Optional[Dict]:
        """
        Get closing price data for this position.
        
        Args:
            full_chain_df: DataFrame containing full option chain data.
            spx_data: DataFrame containing underlying price data.
        
        Returns:
            Optional[Dict]: Dictionary with closing data if successful, None if no valid data found.
        """
        # If no close_date, this is an expiration
        if not self.close_date:
            if self.expire_date not in spx_data.index:
                logger.warning(f"No valid closing data found for position with expire date {self.expire_date}")
                return None
            
            # Get underlying price at close
            underlying_close = spx_data.loc[self.expire_date, 'close']
            
            # Calculate intrinsic value at expiration
            exit_price = self._calculate_intrinsic_value(underlying_close)
            exit_price = -abs(exit_price) if self.is_long else abs(exit_price)

            # Get delta value at expiration
            delta_col = "p_delta" if self.is_put else 'c_delta'
            filtered_df = full_chain_df[
                (full_chain_df.index == self.expire_date) &
                (full_chain_df['expire_date'] == self.expire_date) &
                (full_chain_df['strike'] == self.strike)
            ]
            
            exit_delta = round(filtered_df[delta_col].iloc[0], 2) if not filtered_df.empty else None

            return {
                'underlying_exit': underlying_close,
                'exit_price': exit_price,
                'exit_delta': exit_delta
            }
        
        # Early close - get data from close_date forward (up to 5 days)
        date_range = pd.date_range(self.close_date, self.close_date + pd.Timedelta(days=5))
        filtered_df = full_chain_df[
            (full_chain_df.index.isin(date_range)) & 
            (full_chain_df['expire_date'] == self.expire_date) &
            (full_chain_df['strike'] == self.strike)
        ].sort_index()
        
        if filtered_df.empty:
            logger.warning(f"No valid prices found within 5 days of close date {self.close_date}")
            return None
            
        bid_col = "p_bid" if self.is_put else "c_bid"
        ask_col = "p_ask" if self.is_put else "c_ask"
        delta_col = "p_delta" if self.is_put else 'c_delta'

        # Try each date until we find valid prices
        for _, row in filtered_df.iterrows():
            bid = row[bid_col]
            ask = row[ask_col]
            underlying_close = row['underlying_last']
            exit_delta = round(row[delta_col], 2)
            
            mid_price = self._calculate_midpoint_price(bid, ask)
            if mid_price is not None:
                return {
                    'underlying_exit': underlying_close,
                    'exit_price': mid_price,
                    'exit_delta': exit_delta
                }
        
        logger.error(f"No valid closing prices found for strike {self.strike} and expire date {self.expire_date}")
        return None

    def _calculate_intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        if self.is_put:
            return max(0, self.strike - underlying_price)
        else:  # Call
            return max(0, underlying_price - self.strike)

    def _calculate_midpoint_price(self, bid: float, ask: float) -> Optional[float]:
        """Calculate midpoint price with validation."""
        if bid <= 0 or ask <= 0:
            return None
            
        spread_pct = ((ask - bid) / bid) * 100
        if spread_pct > 50.0:  # Spread too wide
            logger.warning(f"Bid-ask spread too wide: bid={bid}, ask={ask}, spread={spread_pct:.2f}%")
            return None
            
        return (bid + ask) / 2

    @classmethod
    def create_vertical_spread(
        cls,
        strikes: List[float],
        option_type: OptionType,
        expire_date: pd.Timestamp,
        is_credit: bool = True
    ) -> List[ OptionPosition]:
        """
        Factory method to create a vertical spread.
        
        Args:
            strikes: [short_strike, long_strike]
            option_type: PUT or CALL
            expire_date: Expiration date
            is_credit: If True, creates credit spread (default)
        """
        if len(strikes) != 2:
            raise ValueError("Vertical spread requires exactly 2 strikes")

        # For credit spreads:
        # PUT: Sell higher strike, buy lower strike
        # CALL: Sell lower strike, buy higher strike
        if is_credit:
            if option_type == OptionType.PUT:
                short_strike, long_strike = max(strikes), min(strikes)
            else:  # CALL
                short_strike, long_strike = min(strikes), max(strikes)
        else:  # Debit spreads are opposite
            if option_type == OptionType.PUT:
                short_strike, long_strike = min(strikes), max(strikes)
            else:  # CALL
                short_strike, long_strike = max(strikes), min(strikes)

        return [
            cls(strike=short_strike, option_type=option_type, position_side=PositionSide.SHORT, expire_date=expire_date),
            cls(strike=long_strike, option_type=option_type, position_side=PositionSide.LONG, expire_date=expire_date)
        ]

    @classmethod
    def create_iron_condor(
        cls,
        put_strikes: List[float],
        call_strikes: List[float],
        expire_date: pd.Timestamp
    ) -> List[' OptionPosition']:
        """
        Factory method to create an iron condor.
        
        Args:
            put_strikes: [long_put_strike, short_put_strike]
            call_strikes: [short_call_strike, long_call_strike]
            expire_date: Expiration date
        """
        if len(put_strikes) != 2 or len(call_strikes) != 2:
            raise ValueError("Iron condor requires exactly 2 strikes for puts and 2 for calls")

        put_spread = cls.create_vertical_spread(put_strikes, OptionType.PUT, expire_date, is_credit=True)
        call_spread = cls.create_vertical_spread(call_strikes, OptionType.CALL, expire_date, is_credit=True)
        return put_spread + call_spread

    def to_dict(self) -> Dict:
        """Convert position to dictionary format."""
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'entry_date': self.entry_date,
            'expire_date': self.expire_date,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'option_type': self.option_type.value if isinstance(self.option_type, OptionType) else self.option_type,
            'position_side': self.position_side.value if isinstance(self.position_side, PositionSide) else self.position_side,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'entry_delta': self.entry_delta,
            'exit_delta': self.exit_delta,
            'entry_dte': self.entry_dte,
            'close_date': self.close_date,
            'margin_required': self.margin_required
        }

    # def from_row(self, row: NamedTuple, quantity: int, option_type: OptionType, position_side: PositionSide, delta_target: float, entry_date: pd.Timestamp, early_close_days: int, delta_range: Tuple[float, float] = None) -> Position:
    #     trade_id = row.trade_id
    #     quantity: int = 1
    #     option_type: Union[OptionType, str]
    #     position_side: Union[PositionSide, str]
    #     strike: float
    #     expire_date: pd.Timestamp

    #     # Entry state
    #     entry_date: Optional[pd.Timestamp] = None
    #     entry_price: Optional[float] = None
    #     underlying_entry: Optional[float] = None
    #     margin_required: Optional[float] = None  # Store margin requirement

    #     delta_col = "p_delta" if is_put(option_type) else "c_delta"
    #     trade_delta = row[delta_col]
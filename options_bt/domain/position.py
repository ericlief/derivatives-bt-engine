from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple
from __future__ import annotations
import pandas as pd
import numpy as np
import logging
from options_bt.domain.enums import OptionType, PositionSide

logger = logging.getLogger(__name__)

@dataclass
class Position:
    """Core option position. Represents a single option contract position."""
    trade_id: Optional[int] = None
    quantity: int = 1
    option_type: Union[OptionType, str]
    position_side: Union[PositionSide, str]
    strike: float
    expire_date: pd.Timestamp

    # Entry state
    entry_date: Optional[pd.Timestamp] = None
    entry_price: Optional[float] = None
    entry_delta: Optional[float] = None
    underlying_entry: Optional[float] = None
    margin_required: Optional[float] = None  # Store margin requirement

    # Exit state (filled when closed)
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_delta: Optional[float] = None
    underlying_exit: Optional[float] = None

    # Days to expiration
    entry_dte: Optional[int] = None
    close_date: Optional[pd.Timestamp] = None  # For early closure

    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.option_type, str):
            self.option_type = OptionType(self.option_type.lower())
        if isinstance(self.position_side, str):
            self.position_side = PositionSide(self.position_side.lower())
        
        # Calculate margin required based on entry price and underlying entry
        if self.entry_price is not None and self.underlying_entry is not None:
            self.margin_required = self.calculate_margin()

    @property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]

    @property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]

    @property
    def is_open(self) -> bool:
        """Check if position is currently open."""
        return self.entry_date is not None and self.exit_date is None

    @property
    def is_closed(self) -> bool:
        """Check if position is closed."""
        return self.exit_date is not None

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

    def calculate_pnl(self) -> float:
        """Calculate P&L for the position."""
        if not self.entry_price or not self.exit_price:
            return 0
            
        pnl = self.entry_price + self.exit_price  # Signs are already correct from execution
        return pnl * 100 * self.quantity

    @classmethod
    def create_vertical_spread(
        cls,
        strikes: List[float],
        option_type: OptionType,
        expire_date: pd.Timestamp,
        is_credit: bool = True
    ) -> List[Position]:
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
    ) -> List['Position']:
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

    def from_row(self, row: NamedTuple) -> Position:
        trade_id = row.trade_id
        quantity: int = 1
        option_type: Union[OptionType, str]
        position_side: Union[PositionSide, str]
        strike: float
        expire_date: pd.Timestamp

        # Entry state
        entry_date: Optional[pd.Timestamp] = None
        entry_price: Optional[float] = None
        entry_delta: Optional[float] = None
        underlying_entry: Optional[float] = None
        margin_required: Optional[float] = None  # Store margin requirement

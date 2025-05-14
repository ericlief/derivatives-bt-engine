from enum import Enum
from typing import TypedDict, Optional
import pandas as pd

class OptionType(Enum):
    CALL = "call"
    PUT = "put"

class PositionSide(Enum):
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

class SpreadType(Enum):
    NONE = "none"      # Single leg position
    VERTICAL = "vertical"  # Vertical spread (same expiration, different strikes)
    CALENDAR = "calendar"  # Calendar spread (same strike, different expirations)
    DIAGONAL = "diagonal"  # Diagonal spread (different strikes, different expirations)
    IRON_CONDOR = "iron_condor"  # Iron condor (4 legs)
    BUTTERFLY = "butterfly"  # Butterfly spread (3 legs)

class Position(TypedDict):
    trade_id: int
    quantity: int
    entry_date: pd.Timestamp
    expire_date: pd.Timestamp
    underlying_entry: float
    underlying_exit: float
    strike: float
    option_type: str
    position_side: str
    bid: float
    ask: float
    entry_price: float  # Snapshot of the premium at entry
    exit_price: float
    margin_required: float
    entry_delta: float
    entry_dte: int
    close_date: Optional[pd.Timestamp]  # Optional field
    spread_type: Optional[str]  # Type of spread (NONE for single legs)
    spread_id: Optional[int]  # ID to group legs of the same spread
    leg_number: Optional[int]  # Position of this leg in the spread (1, 2, 3, 4)
    leg_ratio: Optional[float]  # Ratio for this leg (e.g., 1 for most legs, 2 for ratio spreads)
    spread_price: Optional[float]  # Total price of the spread (for spread positions)

class TradeResult(TypedDict):
    trade_id: int
    quantity: int
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
    capital_used: float  
    option_bp: float
    return_on_margin: float
    close_reason: str
    pnl: float
    spread_type: Optional[str]  # Type of spread (NONE for single legs)
    spread_id: Optional[int]  # ID to group legs of the same spread
    leg_number: Optional[int]  # Position of this leg in the spread 
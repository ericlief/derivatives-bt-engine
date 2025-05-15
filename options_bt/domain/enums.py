from enum import Enum
from typing import TypedDict, Optional, Union
import pandas as pd

class OptionType(Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @classmethod
    def is_put(cls, value: Union['OptionType', str, pd.Series]) -> bool:
        """
        Check if the value represents a PUT option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, cls):
            return value == cls.PUT
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [cls.PUT, cls.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @classmethod
    def is_call(cls, value: Union['OptionType', str, pd.Series]) -> bool:
        """
        Check if the value represents a CALL option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, cls):
            return value == cls.CALL
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [cls.CALL, cls.CALL.value, "call"]
        elif isinstance(value, str):
            return value.lower() == "call"
        return False

class PositionSide(Enum):
    """Position side enumeration."""
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

    @classmethod
    def is_long(cls, value: Union['PositionSide', str, pd.Series]) -> bool:
        """
        Check if the value represents a LONG position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, cls):
            return value == cls.LONG
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [cls.LONG, cls.LONG.value, "long"]
        elif isinstance(value, str):
            return value.lower() == "long"
        return False

    @classmethod
    def is_short(cls, value: Union['PositionSide', str, pd.Series]) -> bool:
        """
        Check if the value represents a SHORT position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if SHORT, False otherwise
        """
        if isinstance(value, cls):
            return value == cls.SHORT
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [cls.SHORT, cls.SHORT.value, "short"]
        elif isinstance(value, str):
            return value.lower() == "short"
        return False

class SpreadType(Enum):
    """Spread type enumeration."""
    NONE = "none"      # Single leg position
    VERTICAL = "vertical"  # Vertical spread (same expiration, different strikes)
    CALENDAR = "calendar"  # Calendar spread (same strike, different expirations)
    DIAGONAL = "diagonal"  # Diagonal spread (different strikes, different expirations)
    IRON_CONDOR = "iron_condor"  # Iron condor (4 legs)
    BUTTERFLY = "butterfly"  # Butterfly spread (3 legs)

    @classmethod
    def is_spread_type(cls, value: Union['SpreadType', str, pd.Series, pd.DataFrame], spread_type: 'SpreadType') -> bool:
        """
        Check if a value matches the given spread type.
        
        Args:
            value: Can be SpreadType enum, string, pandas Series, or DataFrame with 'spread_type' column
            spread_type: SpreadType to check against
            
        Returns:
            bool: True if types match, False otherwise
        """
        if isinstance(value, cls):
            return value == spread_type
        elif isinstance(value, str):
            return value.lower() == spread_type.value
        elif isinstance(value, pd.DataFrame):
            return value.iloc[0]['spread_type'].lower() == spread_type.value
        elif isinstance(value, pd.Series):
            return value['spread_type'].lower() == spread_type.value
        return False
 
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
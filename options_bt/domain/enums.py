from __future__ import annotations
from enum import Enum
from typing import TypedDict, Optional, Union, NamedTuple
import pandas as pd

class OptionType(str, Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Union[OptionType, str, pd.Series]) -> bool:
        """
        Check if the value represents a PUT option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.PUT
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @staticmethod
    def is_call(value: Union[OptionType, str, pd.Series]) -> bool:
        """
        Check if the value represents a CALL option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.CALL
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]
        elif isinstance(value, str):
            return value.lower() == "call"
        return False

class PositionSide(str, Enum):
    """Position side enumeration."""
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

    @staticmethod
    def is_long(value: Union[PositionSide, str, pd.Series]) -> bool:
        """
        Check if the value represents a LONG position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.LONG
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif isinstance(value, str):
            return value.lower() == "long"
        return False

    @staticmethod
    def is_short(value: Union[PositionSide, str, pd.Series]) -> bool:
        """
        Check if the value represents a SHORT position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if SHORT, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.SHORT
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]
        elif isinstance(value, str):
            return value.lower() == "short"
        return False

class OptionSpreadType(str, Enum):
    """Spread type enumeration."""
    NONE = "none"      # Single leg position
    VERTICAL = "vertical"  # Vertical spread (same expiration, different strikes)
    CALENDAR = "calendar"  # Calendar spread (same strike, different expirations)
    DIAGONAL = "diagonal"  # Diagonal spread (different strikes, different expirations)
    IRON_CONDOR = "iron_condor"  # Iron condor (4 legs)
    BUTTERFLY = "butterfly"  # Butterfly spread (3 legs)

    @staticmethod
    def is_spread_type(value: Union[OptionSpreadType, str, pd.Series, pd.DataFrame], spread_type: OptionSpreadType) -> bool:
        """
        Check if a value matches the given spread type.
        
        Args:
            value: Can be SpreadType enum, string, pandas Series, or DataFrame with 'spread_type' column
            spread_type: SpreadType to check against
            
        Returns:
            bool: True if types match, False otherwise
        """
        if isinstance(value, OptionSpreadType):
            return value == spread_type
        elif isinstance(value, str):
            return value.lower() == spread_type.value
        elif isinstance(value, pd.DataFrame) and 'spread_type' in value.columns:
            return value.iloc[0]['spread_type'].lower() == spread_type.value
        elif isinstance(value, pd.Series) and 'spread_type' in value:
            return value['spread_type'].lower() == spread_type.value
        return False

class BaseStrategy(str, Enum):
    """Base class for strategy types."""
    pass

class OptionStrategy(BaseStrategy):
    """Option strategy type enumeration."""
    SHORT_PUT = "short put"
    LONG_PUT = "long put"
    SHORT_CALL = "short call"
    LONG_CALL = "long call"
    BULL_PUT_CREDIT_SPREAD = "bull put credit spread"
    BEAR_PUT_DEBIT_SPREAD = "bear put debit spread"
    BULL_CALL_DEBIT_SPREAD = "bull call debit spread"
    BEAR_CALL_CREDIT_SPREAD = "bear call credit spread"
    CUSTOM_STRATEGY = "custom strategy"
    IRON_CONDOR = "iron condor"
    BUTTERFLY = "butterfly"
    STRADDLE = "straddle"
    STRANGLE = "strangle"

class FuturesType(str, Enum):
    """Futures contract type enumeration."""
    MES = "MES" # Micro E-mini S&P 500
    CONTRACT_MULTIPLIER = 5 # As per your instruction

class FuturesStrategy(BaseStrategy):
    """Futures strategy type enumeration."""
    LONG_FUTURES = "long futures"  
    SHORT_FUTURES = "short futures"


class TradeSelectionMethod(str, Enum):
    """Trade selection method enumeration."""
    PREMIUM_FIRST = "premium first"
    DELTA_FIRST = "delta first"
    WEIGHTED = "weighted"

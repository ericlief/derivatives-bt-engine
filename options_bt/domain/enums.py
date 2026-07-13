from __future__ import annotations
from enum import Enum
from typing import TypedDict, Optional, Union, NamedTuple

class OptionType(str, Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Union[OptionType, str]) -> bool:
        """
        Check if the value represents a PUT option.

        Args:
            value: Can be OptionType enum or string

        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.PUT
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @staticmethod
    def is_call(value: Union[OptionType, str]) -> bool:
        """
        Check if the value represents a CALL option.

        Args:
            value: Can be OptionType enum or string

        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.CALL
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
    def is_long(value: Union[PositionSide, str]) -> bool:
        """
        Check if the value represents a LONG position.

        Args:
            value: Can be PositionSide enum or string

        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.LONG
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif isinstance(value, str):
            return value.lower() == "long"
        return False

    @staticmethod
    def is_short(value: Union[PositionSide, str]) -> bool:
        """
        Check if the value represents a SHORT position.

        Args:
            value: Can be PositionSide enum or string

        Returns:
            bool: True if SHORT, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.SHORT
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

# Futures contracts are not modeled as an enum-per-instrument (there used
# to be a FuturesType here) -- an underlying instrument isn't a distinct
# *type* any more than an option's underlying is (OptionType above is just
# CALL/PUT, not one member per underlying). Contract specs (multiplier/
# margin/commission) are looked up by plain symbol string via
# options_bt.domain.instruments.get_spec(symbol); known_futures_symbols()
# is the membership check for validation.
class FuturesStrategy(BaseStrategy):
    """Futures strategy type enumeration."""
    LONG_FUTURES = "long_futures"  
    SHORT_FUTURES = "short_futures"


class TradeSelectionMethod(str, Enum):
    """Trade selection method enumeration."""
    PREMIUM_FIRST = "premium first"
    DELTA_FIRST = "delta first"
    WEIGHTED = "weighted"


class TrendRegime(str, Enum):
    """TSMOM trend regime from classify_regime() -- sign agreement between
    the fast (~3mo) and slow (~12mo) trend-strength scores."""
    BULL = "bull"
    CORRECTION = "correction"
    BEAR = "bear"
    REBOUND = "rebound"
    UNKNOWN = "unknown"


class VolRegime(str, Enum):
    """TSMOM vol-spike regime from check_vol_regime() -- current vol
    (VX front-month live, or spot VIX in the backtest) vs its trailing
    63-day MA. Portfolio-wide and VIX/VX-driven -- feeds market_stress_scale,
    not signal_confidence (see SignalConfidenceRegime, which is per-
    instrument and asset-specific instead)."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    SPIKE = "spike"
    EXTREME = "extreme"


class SignalConfidenceRegime(str, Enum):
    """Per-instrument, asset-specific vol-regime classification from
    classify_signal_confidence() -- THIS instrument's own short-window/
    long-window realized-vol ratio vs its own history. NOT VIX/VX-driven
    (contrast VolRegime, which is portfolio-wide) -- catches e.g. a corn-
    harvest or JPY-intervention vol spike that broad-market VX/VIX has no
    visibility into. Feeds signal_confidence, an opt-in discount on trust
    in that instrument's own trend signal."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

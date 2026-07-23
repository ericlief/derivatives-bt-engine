from __future__ import annotations
from enum import Enum
from typing import TypedDict, Optional, Union, NamedTuple

class OptionsType(str, Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Union[OptionsType, str]) -> bool:
        """
        Check if the value represents a PUT option.

        Args:
            value: Can be OptionsType enum or string

        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, OptionsType):
            return value == OptionsType.PUT
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionsType.PUT, OptionsType.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @staticmethod
    def is_call(value: Union[OptionsType, str]) -> bool:
        """
        Check if the value represents a CALL option.

        Args:
            value: Can be OptionsType enum or string

        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, OptionsType):
            return value == OptionsType.CALL
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionsType.CALL, OptionsType.CALL.value, "call"]
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

class OptionsStrategy(BaseStrategy):
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
# *type* any more than an option's underlying is (OptionsType above is just
# CALL/PUT, not one member per underlying). Contract specs (multiplier/
# margin/commission) are looked up by plain symbol string via
# derivatives_bt_engine.domain.instruments.get_spec(symbol); known_futures_symbols()
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


class SignalModel(str, Enum):
    """Which economic construction domain.signal_spec.compute_signal() uses
    to turn a price series into a trend-strength score -- the "signal
    formula" axis, independent of both WindowBasis (below) and
    instruments.annualization_days (see tsmom_signal.py's own module
    docstring for why those two are independent of each other and of this).

    CLASSIC_TS   -- this project's original, canonical fast/slow (3m/12m)
                    tanh-blend construction (tsmom_signal.calculate_trend_
                    strength). Still the default everywhere; nothing about
                    adding this enum changes its behavior.
    GOULDING_DYNAMIC -- Goulding, Harvey & Mazzoleni (2023)'s bimonthly
                    (fast) vs. annual (slow) construction with Bull/Bear/
                    Correction/Rebound-conditioned dynamic reweighting
                    (eq. 4/7-10) -- see research_trend_strength_crossover_
                    signal.md Part 2 §6/§6b for the literature and the
                    already-validated standalone-script implementation this
                    formalizes into a reusable, swappable model."""
    CLASSIC_TS = "classic_ts"
    GOULDING_DYNAMIC = "goulding_dynamic"


class WindowBasis(str, Enum):
    """How domain.signal_spec.compute_signal() turns a signal's fast/slow
    horizon into an actual lookback -- the "window representation" axis,
    independent of SignalModel (above) and of instruments.annualization_days.

    OBSERVATIONS -- a fixed trading-day ROW COUNT (e.g. 63/252), the same
                    number of rows looked back regardless of how much real
                    calendar time that spans for a given instrument or era.
                    This project's long-standing convention
                    (tsmom_signal.calculate_trend_strength); default here.
    CALENDAR     -- a fixed CALENDAR interval (e.g. "3 months ago" by date,
                    via a join_asof lookup), matching what a paper like
                    Goulding et al. literally means by "N-month return" --
                    the row count this actually spans varies by instrument/
                    era (holidays, this project's own Sunday-session-merge
                    fix, etc.), so the vol-scaling denominator becomes a
                    per-row computed quantity instead of a fixed sqrt(N)
                    constant. Only really adds precision over OBSERVATIONS
                    at discrete (e.g. monthly) evaluation points -- a
                    continuously daily-recomputed CALENDAR window converges
                    to nearly the same values as OBSERVATIONS, since both
                    smooth out to the same underlying trend at that
                    frequency; see domain.signal_spec's own module
                    docstring for the full tradeoff discussion."""
    OBSERVATIONS = "observations"
    CALENDAR = "calendar"

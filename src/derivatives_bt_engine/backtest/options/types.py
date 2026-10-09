"""Option-contract and strategy vocabulary for the event-driven backtest."""

from __future__ import annotations

from enum import Enum
from typing import Any


class OptionsType(str, Enum):
    """Distinguish call and put option contracts."""

    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Any) -> bool:
        """Return whether an enum, string, or option-like object is a put."""
        if isinstance(value, OptionsType):
            return value == OptionsType.PUT
        if hasattr(value, "option_type"):
            return value.option_type in (OptionsType.PUT, OptionsType.PUT.value)
        return isinstance(value, str) and value.lower() == OptionsType.PUT.value

    @staticmethod
    def is_call(value: Any) -> bool:
        """Return whether an enum, string, or option-like object is a call."""
        if isinstance(value, OptionsType):
            return value == OptionsType.CALL
        if hasattr(value, "option_type"):
            return value.option_type in (OptionsType.CALL, OptionsType.CALL.value)
        return isinstance(value, str) and value.lower() == OptionsType.CALL.value


class OptionSpreadType(str, Enum):
    """Shape of a simulated multi-leg option position."""

    NONE = "none"
    VERTICAL = "vertical"
    CALENDAR = "calendar"
    DIAGONAL = "diagonal"
    IRON_CONDOR = "iron_condor"
    BUTTERFLY = "butterfly"


class OptionsStrategy(str, Enum):
    """Supported option-position shapes in the event-driven backtest."""

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


class TradeSelectionMethod(str, Enum):
    """Ranking policy for eligible option-contract candidates."""

    PREMIUM_FIRST = "premium first"
    DELTA_FIRST = "delta first"
    WEIGHTED = "weighted"

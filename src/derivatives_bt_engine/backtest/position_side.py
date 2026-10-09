"""Direction vocabulary shared by simulated option and futures positions."""

from __future__ import annotations

from enum import Enum
from typing import Any


class PositionSide(str, Enum):
    """Direction of a simulated position."""

    LONG = "long"
    SHORT = "short"

    @staticmethod
    def is_long(value: Any) -> bool:
        """Return whether an enum, string, or position-like object is long."""
        if isinstance(value, PositionSide):
            return value == PositionSide.LONG
        if hasattr(value, "position_side"):
            return value.position_side in (PositionSide.LONG, PositionSide.LONG.value)
        return isinstance(value, str) and value.lower() == PositionSide.LONG.value

    @staticmethod
    def is_short(value: Any) -> bool:
        """Return whether an enum, string, or position-like object is short."""
        if isinstance(value, PositionSide):
            return value == PositionSide.SHORT
        if hasattr(value, "position_side"):
            return value.position_side in (
                PositionSide.SHORT,
                PositionSide.SHORT.value,
            )
        return isinstance(value, str) and value.lower() == PositionSide.SHORT.value

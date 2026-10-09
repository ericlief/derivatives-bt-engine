"""Futures strategy vocabulary for the event-driven backtest."""

from enum import Enum


class FuturesStrategy(str, Enum):
    """Direction of a legacy single-instrument futures strategy."""

    LONG_FUTURES = "long_futures"
    SHORT_FUTURES = "short_futures"

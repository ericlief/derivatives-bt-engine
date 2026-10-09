"""Route futures history to the three supported trend signal classes."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

import polars as pl

from derivatives_bt_engine.calculations.ewmac import ewmac
from derivatives_bt_engine.data.futures_history import FuturesHistory
from derivatives_bt_engine.calculations.continuous_momentum import (
    build_features,
    continuous_momentum,
)
from derivatives_bt_engine.calculations.goulding import goulding_monthly


class FuturesSignalClass(str, Enum):
    """The input representation is part of each signal's contract."""

    RETURN_TSMOM = "return_tsmom"
    GOULDING_MONTHLY = "goulding_monthly"
    EWMAC = "carver_ewmac"
    # Preserve the existing serialized/configured value without carrying the
    # old author-specific name through new internal code.
    CARVER_EWMAC = "carver_ewmac"


def run_futures_signal(
    history: FuturesHistory,
    signal_class: FuturesSignalClass | str,
    *,
    parameters: Mapping[str, Any] | None = None,
) -> pl.DataFrame:
    """Run one signal class with the correct continuous representation.

    Return-defined models exclude observations whose contract return is not
    valid (notably zero/negative references).  The excluded dates remain in
    ``history.signal`` with quality flags for audit.  EWMAC deliberately keeps
    those dates because additive point changes remain meaningful.
    """
    kind = FuturesSignalClass(signal_class)
    kwargs = dict(parameters or {})
    if kind == FuturesSignalClass.EWMAC:
        return ewmac(history.panama_bars(), **kwargs)

    eligible = history.signal.filter(
        pl.col("return_valid") | (pl.col("quality_flag") == "initial_observation")
    )
    bars = eligible.select(
        pl.col("trade_date").alias("ts_event"),
        pl.col("signal_index").alias("close"),
        "ret_1d",
        "quality_flag",
    ).sort("ts_event")
    features = build_features(bars)
    if kind == FuturesSignalClass.RETURN_TSMOM:
        return continuous_momentum(features, **kwargs)
    return goulding_monthly(features, **kwargs)


def run_futures_signal_set(
    history: FuturesHistory,
    *,
    parameters: Mapping[FuturesSignalClass | str, Mapping[str, Any]] | None = None,
) -> dict[FuturesSignalClass, pl.DataFrame]:
    """Run all three classes for a single-source or hybrid history."""
    configured = parameters or {}
    return {
        kind: run_futures_signal(
            history,
            kind,
            parameters=configured.get(kind, configured.get(kind.value, {})),
        )
        for kind in FuturesSignalClass
    }

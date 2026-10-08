"""Tests for provider-neutral single- and multi-speed EWMAC construction."""

import math

import polars as pl

from derivatives_bt_engine.domain.ewmac import EwmacRule
from derivatives_bt_engine.domain.ewmac_components import (
    build_ewmac_components,
    build_ewmac_rule_component,
)


def _price_frame(rows: int = 80) -> pl.DataFrame:
    """Return a deterministic price history with an IB-like extra column."""
    closes = [100.0 + index * 0.1 + math.sin(index / 3.0) for index in range(rows)]
    return pl.DataFrame({
        "date": pl.date_range(
            pl.date(2024, 1, 1),
            pl.date(2024, 1, 1) + pl.duration(days=rows - 1),
            eager=True,
        ),
        "close": closes,
        "volume": [1_000 + index for index in range(rows)],
    })


def test_rule_component_derives_changes_and_causal_scalar():
    """A close-only frame receives point changes and prior-only scaling."""
    result = build_ewmac_rule_component(
        _price_frame(),
        EwmacRule(4, 16),
        instrument_code="STOCK",
        scalar_min_periods=3,
        vol_span=4,
        vol_slow_years=1,
        vol_min_samples=2,
    )

    assert result.get_column("point_change")[0] is None
    assert result.get_column("scalar_4_16").head(3).null_count() == 3
    assert result.get_column("scalar_4_16").drop_nulls().len() > 0
    assert result.get_column("fcst_4_16").drop_nulls().abs().max() <= 1.0


def test_component_builder_retains_history_and_joins_requested_speeds():
    """The generic builder keeps provider fields and adds each rule once."""
    rules = (EwmacRule(4, 16), EwmacRule(8, 32))
    result = build_ewmac_components(
        _price_frame(),
        rules=rules,
        forecast_scalars={"4/16": 0.5, "8/32": 0.75},
        vol_span=4,
        vol_slow_years=1,
        vol_min_samples=2,
    )

    assert result.height == 80
    assert "date" in result.columns
    assert "ts_event" not in result.columns
    assert "volume" in result.columns
    assert result.get_column("scalar_4_16").unique().to_list() == [0.5]
    assert result.get_column("scalar_8_32").unique().to_list() == [0.75]
    assert {
        "raw_fcst_4_16",
        "fcst_4_16",
        "raw_fcst_8_32",
        "fcst_8_32",
    } <= set(result.columns)

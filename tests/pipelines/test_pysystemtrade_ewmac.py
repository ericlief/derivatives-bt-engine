"""Tests for reusable EWMAC construction and notebook-facing views."""

from types import SimpleNamespace

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.pysystemtrade_ewmac import (
    EwmacResearchView,
    PooledEwmacRuleData,
    build_ewmac_component_frame,
)
from derivatives_bt_engine.calculations.ewmac import EwmacRule


def _history() -> SimpleNamespace:
    """Return the minimal Panama-history interface needed by the builder."""
    bars = pl.DataFrame({
        "ts_event": pl.date_range(
            pl.date(2024, 1, 1), pl.date(2024, 1, 8), eager=True
        ),
        "close": [100.0, 101.0, 102.0, 101.0, 103.0, 104.0, 103.0, 105.0],
        "point_change": [None, 1.0, 1.0, -1.0, 2.0, 1.0, -1.0, 2.0],
        "quality_flag": [""] * 8,
    })
    return SimpleNamespace(panama_bars=lambda: bars)


def _pooled(rule: EwmacRule) -> PooledEwmacRuleData:
    """Return a small always-valid pooled scalar for deterministic tests."""
    scalar = pl.DataFrame({
        "ts_event": pl.date_range(
            pl.date(2024, 1, 1), pl.date(2024, 1, 8), eager=True
        ),
        "pool_key": ["global"] * 8,
        "forecast_scalar": [0.5] * 8,
    })
    return PooledEwmacRuleData(
        rule=rule,
        scalar_history=scalar,
        coverage=pl.DataFrame(),
        cache_metadata={"scalar_cache_hit": True},
        vol_span=2,
        vol_slow_years=1,
        vol_slow_weight=0.3,
        vol_min_samples=2,
        target_abs_forecast=0.5,
        forecast_cap=1.0,
    )


def test_component_builder_exposes_compact_and_detailed_shapes():
    """Report consumers stay compact while notebooks receive raw diagnostics."""
    rules = [EwmacRule(2, 4), EwmacRule(4, 8)]
    pooled = {rule.key: _pooled(rule) for rule in rules}

    compact = build_ewmac_component_frame(_history(), pooled)
    detailed = build_ewmac_component_frame(_history(), pooled, detailed=True)

    assert compact.columns == [
        "date", "point_vol", "point_change", "fcst_2_4", "fcst_4_8"
    ]
    assert {
        "close",
        "raw_ewmac_2_4",
        "raw_fcst_2_4",
        "scalar_2_4",
        "fcst_2_4",
        "raw_ewmac_4_8",
        "raw_fcst_4_8",
        "scalar_4_8",
        "fcst_4_8",
    } <= set(detailed.columns)
    assert detailed.get_column("scalar_2_4").drop_nulls().unique().to_list() == [0.5]


def test_research_view_filters_display_tables_without_mutating_full_history():
    """Notebook date slicing and default forecast columns remain predictable."""
    canonical_rule = EwmacRule(4, 16)
    forecasts = pl.DataFrame({
        "date": pl.date_range(
            pl.date(2024, 1, 1), pl.date(2024, 1, 3), eager=True
        ),
        "close": [100.0, 101.0, 102.0],
        "point_vol": [1.0, 1.0, 1.0],
        canonical_rule.column: [None, 0.1, 0.2],
    })
    scalars = pl.DataFrame({
        "date": forecasts.get_column("date"),
        "rule": [canonical_rule.key] * 3,
        "forecast_scalar": [None, 0.4, 0.5],
    })
    view = EwmacResearchView(
        instrument_code="TEST",
        rules=(canonical_rule,),
        history=SimpleNamespace(metadata={"cache_hit": True}),
        forecasts=forecasts,
        pooled_scalars=scalars,
        normalization_coverage={},
        cache_metadata={
            canonical_rule.key: {
                "forecast_panel_cache_hit": True,
                "scalar_cache_hit": True,
            }
        },
    )

    table = view.forecast_table(start="2024-01-02")
    scalar_table = view.scalar_table(canonical_rule.key, end="2024-01-02")

    assert table.shape == (2, 4)
    assert table.columns == ["date", "close", "point_vol", "fcst_4_16"]
    assert scalar_table.height == 2
    assert view.forecasts.height == 3
    assert view.cache_status().select(
        "history_cache_hit", "forecast_panel_cache_hit", "scalar_cache_hit"
    ).row(0) == (True, True, True)


def test_component_builder_rejects_mismatched_rule_dictionary_key():
    """Rule identity cannot silently diverge from the persisted column name."""
    with pytest.raises(ValueError, match="does not match"):
        build_ewmac_component_frame(
            _history(), {"wrong": _pooled(EwmacRule(2, 4))}
        )

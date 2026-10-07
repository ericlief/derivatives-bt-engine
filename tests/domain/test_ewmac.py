"""Tests for EWMAC construction and causal forecast normalization."""

from datetime import date, timedelta

import polars as pl

from derivatives_bt_engine.domain.ewmac import ewmac


def _dates(count: int) -> list[date]:
    """Return consecutive dates for compact EWMAC unit-test frames."""
    start = date(2024, 1, 1)
    return [start + timedelta(days=offset) for offset in range(count)]


def test_ewmac_preserves_history_point_change() -> None:
    """A provider-owned point-change series is authoritative, not rebuilt."""
    supplied_changes = [None, 0.25, -0.5, 0.75, -0.25, 0.5]
    frame = pl.DataFrame(
        {
            "ts_event": _dates(6),
            "close": [100.0, 101.0, 103.0, 106.0, 110.0, 115.0],
            # These intentionally differ from close.diff() to expose any
            # accidental replacement of the history provider's data.
            "point_change": supplied_changes,
        }
    )

    result = ewmac(
        frame,
        fast_span=2,
        slow_span=4,
        vol_span=2,
        vol_min_samples=2,
    )

    assert result.get_column("point_change").to_list() == supplied_changes


def test_ewmac_derives_point_change_for_close_only_input_after_sorting() -> None:
    """Generic close-only callers retain a deterministic sorted fallback."""
    dates = _dates(3)
    frame = pl.DataFrame(
        {
            "ts_event": [dates[2], dates[0], dates[1]],
            "close": [103.0, 100.0, 101.0],
        }
    )

    result = ewmac(
        frame,
        fast_span=2,
        slow_span=4,
        vol_span=2,
        vol_min_samples=2,
    )

    assert result.get_column("ts_event").to_list() == dates
    assert result.get_column("point_change").to_list() == [None, 1.0, 2.0]

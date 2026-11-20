"""Tests for carry-rule cost estimation in the futures market screen."""

from datetime import date, timedelta

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.market_screen import _carry_rule_turnover


def test_carry_turnover_excludes_source_changes_and_gaps() -> None:
    """Turnover uses adjacent valid pairs even with a continuous EWMA."""
    start = date(2024, 1, 2)
    frame = pl.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in (0, 1, 2, 3, 4, 5, 15)],
            "source_segment": ["pysystemtrade"] * 7,
            "source_segment_right": ["pysystemtrade"] * 2 + ["ib"] * 5,
            "forecast_valid": [True, True, True, True, False, True, True],
            "fcst_carry_5": [0.0, 1.0, 10.0, 12.0, None, 20.0, 40.0],
        }
    )

    turnover = _carry_rule_turnover(
        frame,
        smooth_span=5,
        annualization_days=256,
    )

    # The only valid changes are 1 in the PST data and 2 in the IB data.
    assert turnover == pytest.approx(1.5 * 256)


def test_carry_turnover_accepts_single_source_without_tags() -> None:
    """PST reference histories can omit row-level provider tags."""
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "forecast_valid": [True, True],
            "fcst_carry_5": [0.0, 1.0],
        }
    )

    assert _carry_rule_turnover(frame, smooth_span=5) == pytest.approx(256)

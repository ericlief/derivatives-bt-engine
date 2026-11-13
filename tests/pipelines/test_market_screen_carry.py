"""Tests for carry-rule cost estimation in the futures market screen."""

from datetime import date, timedelta

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.market_screen import _carry_rule_turnover


def test_carry_turnover_does_not_bridge_smoothing_segments() -> None:
    """Source handoffs and invalid-history breaks are not forecast trades."""
    start = date(2024, 1, 2)
    frame = pl.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in range(4)],
            "carry_segment": [1, 1, 2, 2],
            "forecast_valid": [True, True, True, True],
            "fcst_carry_5": [0.0, 1.0, 10.0, 12.0],
        }
    )

    turnover = _carry_rule_turnover(
        frame,
        smooth_span=5,
        annualization_days=256,
    )

    # The valid changes are 1 within segment one and 2 within segment two.
    assert turnover == pytest.approx(1.5 * 256)


def test_carry_turnover_requires_segment_ownership() -> None:
    """Turnover inputs must retain the smoothing segment audit field."""
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "forecast_valid": [True, True],
            "fcst_carry_5": [0.0, 1.0],
        }
    )

    with pytest.raises(ValueError, match="carry_segment"):
        _carry_rule_turnover(frame, smooth_span=5)

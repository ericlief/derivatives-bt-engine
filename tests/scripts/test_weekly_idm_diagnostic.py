from datetime import date

import pytest
import polars as pl

from scripts.weekly_idm_diagnostic import (
    compound_completed_calendar_weeks,
    effective_sample_size,
    span_to_halflife,
)


def test_span_25_has_25_observation_effective_sample_size():
    halflife = span_to_halflife(25.0)

    assert halflife == pytest.approx(8.6597, rel=1e-4)
    assert effective_sample_size(10_000, halflife) == pytest.approx(25.0, rel=1e-10)


def test_weekly_returns_are_compounded_and_labelled_at_week_end():
    daily = pl.DataFrame({
        "ts_event": [
            date(2026, 9, 14),
            date(2026, 9, 15),
            date(2026, 9, 18),
            date(2026, 9, 21),
        ],
        "A": [0.10, -0.10, 0.05, 0.02],
        "B": [0.01, 0.02, -0.01, 0.03],
    })

    weekly = compound_completed_calendar_weeks(daily)

    assert weekly.get_column("ts_event").to_list() == [date(2026, 9, 20), date(2026, 9, 27)]
    assert weekly.get_column("A").to_list() == pytest.approx([
        (1.10 * 0.90 * 1.05) - 1.0,
        0.02,
    ])
    assert weekly.get_column("B").to_list() == pytest.approx([
        (1.01 * 1.02 * 0.99) - 1.0,
        0.03,
    ])

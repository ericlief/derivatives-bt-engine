import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.forecast_combination import (
    CombinedForecastEngine,
    CorrelationForecastDiversification,
    ForecastCombinationConfig,
)


def _frame(values_4, values_64):
    n = len(values_4)
    return pl.DataFrame({
        "ts_event": pl.date_range(
            pl.date(2024, 1, 1),
            pl.date(2024, 1, 1) + pl.duration(days=n - 1),
            eager=True,
        ),
        "point_vol": [1.0] * n,
        "point_change": [0.1] * n,
        "fcst_4_16": values_4,
        "fcst_64_256": values_64,
    })


def test_combined_turnover_is_measured_after_combining_components():
    frame = _frame(
        [0.5, -0.5, 0.5, -0.5, 0.5],
        [-0.5, 0.5, -0.5, 0.5, -0.5],
    )
    engine = CombinedForecastEngine(config=ForecastCombinationConfig(
        min_valid_observations=3,
    ))
    result = engine.combine(
        frame,
        ["4/16", "64/256"],
        np.eye(2),
        ["4/16", "64/256"],
    )

    # Renormalized weights are 1/7 and 6/7. Turnover is calculated from the
    # resulting position series, rather than summing the two rule turnovers.
    expected_fdm = 1.0 / np.sqrt((1 / 7) ** 2 + (6 / 7) ** 2)
    combined = np.array([-2.5 / 7, 2.5 / 7, -2.5 / 7, 2.5 / 7, -2.5 / 7])
    expected = np.abs(np.diff(combined * expected_fdm / 0.5)).mean() * 256
    assert result.fdm == pytest.approx(expected_fdm)
    assert result.turnover == pytest.approx(expected)
    assert result.audit["forecast_elig"] is True


def test_forecast_gap_after_warmup_is_audited_and_excluded():
    frame = _frame(
        [None, 1.0, 2.0, None, 3.0],
        [None, 1.0, 2.0, 2.5, 3.0],
    )
    engine = CombinedForecastEngine(config=ForecastCombinationConfig(
        min_valid_observations=3,
    ))
    result = engine.combine(
        frame,
        ["4/16", "64/256"],
        np.eye(2),
        ["4/16", "64/256"],
    )

    assert result.audit["fcst_4_16_nulls"] == 2
    assert result.audit["forecast_post_warmup_invalid_obs"] == 1
    assert result.audit["forecast_elig"] is False
    assert result.audit["forecast_excl"] == "forecast_gaps_after_warmup"


def test_forecast_diversification_is_capped():
    policy = CorrelationForecastDiversification(cap=2.5)

    multiplier = policy.multiplier(
        ["a", "b", "c", "d", "e"],
        {key: 0.2 for key in "abcde"},
        np.eye(5),
        list("abcde"),
    )

    assert multiplier == pytest.approx(np.sqrt(5))


def test_turnover_ewm_applies_to_average_position_not_forecast():
    frame = pl.DataFrame({
        "ts_event": pl.date_range(
            pl.date(2024, 1, 1), pl.date(2024, 1, 5), eager=True
        ),
        "point_vol": [1.0, 1.25, 2.0, 2.5, 4.0],
        "point_change": [0.1] * 5,
        "fcst_4_16": [0.2] * 5,
    })
    engine = CombinedForecastEngine(config=ForecastCombinationConfig(
        average_position_ewm_com=2,
        min_valid_observations=3,
    ))

    result = engine.combine(frame, ["4/16"], np.eye(1), ["4/16"])

    assert result.frame.get_column("combined_forecast").to_list() == pytest.approx(
        [0.2] * 5
    )
    last = result.frame.tail(1).row(0, named=True)
    assert last["avg_position"] == pytest.approx(0.25)
    assert last["smooth_avg_position"] != pytest.approx(last["avg_position"])

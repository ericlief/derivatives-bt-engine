import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.forecast_combination import (
    CombinedForecastEngine,
    CorrelationForecastDiversification,
    ForecastCombinationConfig,
    combine_ewmac_forecasts,
)
from derivatives_bt_engine.domain.ewmac import EwmacRule


def _frame(values_4, values_64):
    n = len(values_4)
    return pl.DataFrame({
        "date": pl.date_range(
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
        "date": pl.date_range(
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


def test_dataframe_helper_builds_components_and_disables_fdm():
    """One price frame can use the complete engine with a fixed unit FDM."""
    rows = 80
    history = pl.DataFrame({
        "date": pl.date_range(
            pl.date(2024, 1, 1),
            pl.date(2024, 1, 1) + pl.duration(days=rows - 1),
            eager=True,
        ),
        "close": [100.0 + index * 0.1 + np.sin(index / 3) for index in range(rows)],
    })
    result = combine_ewmac_forecasts(
        history,
        rules=(EwmacRule(4, 16), EwmacRule(8, 32)),
        forecast_scalars={"4/16": 0.5, "8/32": 0.75},
        fdm=1.0,
        config=ForecastCombinationConfig(min_valid_observations=10),
        vol_span=4,
        vol_slow_years=1,
        vol_min_samples=2,
    )

    valid = result.frame.filter(pl.col("forecast_valid"))
    expected = (
        valid.get_column("fcst_4_16") * 0.25
        + valid.get_column("fcst_8_32") * 0.75
    ).clip(-1.0, 1.0)
    assert result.fdm == 1.0
    assert "date" in result.frame.columns
    assert "ts_event" not in result.frame.columns
    assert "mixed_point_vol" in result.frame.columns
    assert "point_vol" not in result.frame.columns
    assert result.frame.columns.index("annualization_sqrt") < result.frame.columns.index(
        "fast_ewma_4_16"
    )
    assert result.weights["4/16"] == pytest.approx(0.25)
    assert result.weights["8/32"] == pytest.approx(0.75)
    assert valid.get_column("combined_forecast").to_list() == pytest.approx(
        expected.to_list()
    )
    assert result.audit["forecast_elig"] is True

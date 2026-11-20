"""Tests for raw carry, smoothed forecasts, FDM, and trend/carry blending."""

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.calculations.carry import (
    CARRY_FORECAST_SCALAR,
    aft_final_rule_fdm,
    build_carry_components,
    build_carry_forecast,
    build_raw_carry_frame,
    combine_trend_and_carry_forecasts,
    contract_year_fraction,
)
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)


def _dates(rows: int) -> list[date]:
    """Return consecutive dates for compact deterministic fixtures."""
    start = date(2024, 1, 1)
    return [start + timedelta(days=index) for index in range(rows)]


def test_contract_year_fraction_matches_pysystemtrade_encoding() -> None:
    assert contract_year_fraction("20240300") == pytest.approx(2024.25)
    assert contract_year_fraction("202412") == pytest.approx(2025.0)
    with pytest.raises(ValueError, match="invalid futures contract"):
        contract_year_fraction("20241300")


def test_raw_carry_annualises_curve_slope_and_point_volatility() -> None:
    carry = pl.DataFrame(
        {
            "date": _dates(2),
            "current_price": [101.0, 100.0],
            "current_contract": ["20240300", "20240300"],
            "carry_price": [100.0, 100.0],
            "carry_contract": ["20240600", "20240600"],
        }
    )
    point_vol = pl.DataFrame(
        {"date": _dates(2), "mixed_point_vol": [0.25, 0.25]}
    )

    result = build_raw_carry_frame(carry, point_vol)

    # One point over a quarter year is four points annualised; 0.25 daily
    # point vol becomes four annualised at sqrt(256).
    assert result["ann_roll"][0] == pytest.approx(4.0)
    assert result["ann_point_vol"][0] == pytest.approx(4.0)
    assert result["raw_carry"][0] == pytest.approx(1.0)
    assert result["carry_valid"].to_list() == [True, False]
    assert result["carry_excl"].to_list() == ["", "zero_raw_roll"]


def test_carry_span_smoothing_matches_polars_and_uses_normalized_scalar() -> None:
    """A Carry5 label means a five-day EWMA span, not COM five."""
    values = [0.05 + 0.01 * np.sin(index / 3.0) for index in range(40)]
    raw = pl.DataFrame(
        {
            "date": _dates(len(values)),
            "raw_carry": values,
            "carry_valid": [True] * len(values),
        }
    )

    result = build_carry_components(raw)
    expected_smooth = pl.Series(values).ewm_mean(
        span=5,
        adjust=True,
        min_samples=1,
    )
    com_smooth = pl.Series(values).ewm_mean(
        com=5,
        adjust=True,
        min_samples=1,
    )
    expected_forecast = expected_smooth * CARRY_FORECAST_SCALAR

    assert result.frame["smooth_carry_5"].to_list() == pytest.approx(
        expected_smooth.to_list()
    )
    assert result.frame["fcst_carry_5"].to_list() == pytest.approx(
        expected_forecast.to_list()
    )
    assert result.frame["smooth_carry_5"][-1] != pytest.approx(com_smooth[-1])
    assert result.weights == {
        "carry5": 0.25,
        "carry20": 0.25,
        "carry60": 0.25,
        "carry120": 0.25,
    }
    assert result.fdm == pytest.approx(1.23)


def test_carry_smoothing_keeps_state_across_nulls_handoff_and_gap() -> None:
    """All four speeds retain history and emit null targets on missing rows."""
    raw = pl.DataFrame(
        {
            "date": [*_dates(4), date(2024, 1, 20)],
            "raw_carry": [0.1, None, None, -0.1, 0.2],
            "carry_valid": [True, False, False, True, True],
            "source_segment": ["pysystemtrade", "pysystemtrade", "ib", "ib", "ib"],
        }
    )

    result = build_carry_components(raw)

    assert result.frame["forecast_valid"].to_list() == [True, False, False, True, True]
    assert result.frame["combined_forecast"][1:3].null_count() == 2
    for span in (5, 20, 60, 120):
        decay = 1 - 2 / (span + 1)
        smooth = result.frame[f"smooth_carry_{span}"]
        assert smooth[1:3].null_count() == 2
        assert result.frame[f"fcst_carry_{span}"][1:3].null_count() == 2
        assert smooth[3] == pytest.approx((0.1 * decay - 0.1) / (1 + decay))
        assert smooth[4] == pytest.approx(
            (0.1 * decay**2 - 0.1 * decay + 0.2) / (1 + decay + decay**2)
        )


@pytest.mark.parametrize(
    ("rule_count", "fdm"),
    [(1, 1.0), (4, 1.23), (5, 1.25), (9, 1.34), (40, 2.0), (50, 2.0)],
)
def test_aft_final_rule_fdm_uses_published_count_table(
    rule_count: int, fdm: float
) -> None:
    assert aft_final_rule_fdm(rule_count) == pytest.approx(fdm)


def test_carry_forward_fills_forecasts_across_invalid_rows_and_roll(monkeypatch) -> None:
    """Keep raw observations separate from forecast values carried forward."""
    dates = [*_dates(3), date(2024, 1, 12), date(2024, 1, 15)]
    execution = pl.DataFrame({
        "date": dates,
        "point_vol": [0.25] * 5,
        "mixed_point_vol": [0.25] * 5,
        "point_change": [0.1] * 5,
    })
    monkeypatch.setattr(
        "derivatives_bt_engine.calculations.carry._history_execution_frame",
        lambda history: execution,
    )
    history = SimpleNamespace(
        marks=pl.DataFrame({
            "date": dates,
            "contract_id": ["20240300"] * 3 + ["20240600"] * 2,
        }),
        carry=pl.DataFrame({
            "date": [dates[1], dates[2], dates[4]],
            "current_contract": ["20240300", "20240300", "20240600"],
            "carry_contract": ["20240600", "20240600", "20240900"],
            "current_price": [100.1, 100.0, 99.9],
            "carry_price": [100.0] * 3,
        }),
    )
    forecast = build_carry_forecast(history, min_valid_observations=2).frame

    assert forecast["carry_obs_date"].to_list() == [
        None, dates[1], dates[2], None, dates[4],
    ]
    assert forecast["carry_forecast_obs_date"].to_list() == [
        None, dates[1], dates[1], dates[1], dates[4],
    ]
    assert forecast["carry_stale_days"].to_list() == [None, 0, 1, 10, 0]
    assert forecast["raw_carry"][2:4].null_count() == 2
    assert forecast["fcst_carry_5"][0] is None
    assert forecast["fcst_carry_5"][1:4].to_list() == pytest.approx([0.3] * 3)
    assert forecast["fcst_carry_5"][4] < forecast["fcst_carry_5"][3]
    assert forecast["forecast_valid"].to_list() == [False, True, True, True, True]

    calibration = build_carry_forecast(
        history, min_valid_observations=2, turnover_max_stale_days=7,
    ).frame
    assert calibration["forecast_valid"].to_list() == [False, True, False, False, True]


def test_trend_carry_blend_uses_60_40_and_nine_rule_fdm() -> None:
    dates = _dates(3)
    trend_columns = {
        "fcst_4_16": [1.0, None, 1.0],
        "fcst_8_32": [1.0] * 3,
        "fcst_16_64": [1.0] * 3,
        "fcst_32_128": [1.0] * 3,
        "fcst_64_256": [1.0] * 3,
    }
    trend = CombinedForecastResult(
        pl.DataFrame(
            {
                "date": dates,
                "point_change": [0.1] * 3,
                "point_vol": [1.0] * 3,
                "source_segment": ["trend-source"] * 3,
                "raw_fcst_4_16": [0.5] * 3,
                "scalar_4_16": [2.0] * 3,
                **trend_columns,
                "forecast_valid": [True] * 3,
                "combined_forecast": [1.0] * 3,
            }
        ),
        {
            "4/16": 0.05,
            "8/32": 0.15,
            "16/64": 0.20,
            "32/128": 0.30,
            "64/256": 0.30,
        },
        1.25,
        {},
    )
    carry = CombinedForecastResult(
        pl.DataFrame(
            {
                "date": dates,
                **{f"fcst_carry_{span}": [-1.0] * 3 for span in (5, 20, 60, 120)},
                "carry_obs_date": dates,
                "source_segment": ["carry-source"] * 3,
                "raw_roll": [-0.2] * 3,
                "raw_carry": [-1 / 30] * 3,
                "smooth_carry_5": [-1 / 30] * 3,
                "carry_forecast_scalar": [30.0] * 3,
                "forecast_valid": [True] * 3,
                "combined_forecast": [-1.23] * 3,
            }
        ),
        {f"carry{span}": 0.25 for span in (5, 20, 60, 120)},
        1.23,
        {},
    )

    result = combine_trend_and_carry_forecasts(
        trend,
        carry,
        min_valid_observations=2,
    )

    # Pre-FDM family forecast is 0.6 * 1 + 0.4 * -1 = 0.2.
    assert result.fdm == pytest.approx(1.34)
    first = result.frame.row(0, named=True)
    assert first["trend_fdm"] == pytest.approx(1.25)
    assert first["carry_fdm"] == pytest.approx(1.23)
    assert first["forecast_fdm"] == pytest.approx(1.34)
    assert first["source_segment"] == "trend-source"
    assert first["carry_source_segment"] == "carry-source"
    assert first["carry_obs_date"] == dates[0]
    assert first["raw_roll"] == pytest.approx(-0.2)
    assert first["raw_carry"] == pytest.approx(-1 / 30)
    assert first["smooth_carry_5"] * first["carry_forecast_scalar"] == pytest.approx(
        first["fcst_carry_5"]
    )
    assert first["raw_fcst_4_16"] * first["scalar_4_16"] == pytest.approx(
        first["fcst_4_16"]
    )
    assert first["wt_fcst_4_16"] == pytest.approx(0.03)
    assert first["wt_fcst_carry_5"] == pytest.approx(0.10)
    assert result.frame["trend_forecast_missing"].to_list() == [False, True, False]
    assert result.frame["fcst_4_16"].to_list() == [1.0, 1.0, 1.0]
    assert result.frame["combined_forecast"].to_list() == pytest.approx(
        [0.268] * 3
    )
    assert sum(result.weights.values()) == pytest.approx(1.0)
    assert sum(
        weight for rule, weight in result.weights.items() if rule.startswith("trend:")
    ) == pytest.approx(0.60)
    assert sum(
        weight for rule, weight in result.weights.items() if rule.startswith("carry:")
    ) == pytest.approx(0.40)

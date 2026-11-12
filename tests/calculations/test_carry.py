"""Tests for raw carry, smoothed forecasts, FDM, and trend/carry blending."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import polars as pl
import pytest

from derivatives_bt_engine.calculations.carry import (
    CARRY_FORECAST_SCALAR,
    aft_final_rule_fdm,
    build_carry_components,
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


def test_carry_com_smoothing_matches_pandas_and_uses_normalized_scalar() -> None:
    values = [0.05 + 0.01 * np.sin(index / 3.0) for index in range(40)]
    raw = pl.DataFrame(
        {
            "date": _dates(len(values)),
            "raw_carry": values,
            "carry_valid": [True] * len(values),
        }
    )

    result = build_carry_components(raw)
    expected = pd.Series(values).ewm(10).mean() * CARRY_FORECAST_SCALAR

    assert result.frame["smooth_carry_10"].to_list() == pytest.approx(
        (expected / CARRY_FORECAST_SCALAR).to_list()
    )
    assert result.frame["fcst_carry_10"].to_list() == pytest.approx(
        expected.to_list()
    )
    assert result.weights == {
        "carry10": 0.25,
        "carry30": 0.25,
        "carry60": 0.25,
        "carry125": 0.25,
    }
    assert result.fdm == pytest.approx(1.23)


def test_carry_smoothing_restarts_at_source_handoff() -> None:
    raw = pl.DataFrame(
        {
            "date": _dates(4),
            "raw_carry": [1.0, 1.0, -1.0, -1.0],
            "carry_valid": [True] * 4,
            "source_segment": ["ref", "ref", "ib", "ib"],
        }
    )

    result = build_carry_components(raw, smooth_coms=(10,))

    assert result.frame["carry_segment"].to_list() == [1, 1, 2, 2]
    assert result.frame["smooth_carry_10"][2] == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("rule_count", "fdm"),
    [(1, 1.0), (4, 1.23), (5, 1.25), (9, 1.34), (40, 2.0), (50, 2.0)],
)
def test_aft_final_rule_fdm_uses_published_count_table(
    rule_count: int, fdm: float
) -> None:
    assert aft_final_rule_fdm(rule_count) == pytest.approx(fdm)


def test_trend_carry_blend_uses_60_40_and_nine_rule_fdm() -> None:
    dates = _dates(3)
    trend_columns = {
        "fcst_4_16": [1.0] * 3,
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
                **{f"fcst_carry_{span}": [-1.0] * 3 for span in (10, 30, 60, 125)},
                "forecast_valid": [True] * 3,
                "combined_forecast": [-1.23] * 3,
            }
        ),
        {f"carry{span}": 0.25 for span in (10, 30, 60, 125)},
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

"""Tests for pooled Goulding forecasts consumed by portfolio selection."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.goulding_selection_forecast import (
    _forecast_rows,
)


def _monthly_frame(raw_scale: float) -> pl.DataFrame:
    """Return a warm synthetic Bull history with controlled raw magnitude."""
    months = [date(year, month, 1) for year in (2020, 2021) for month in range(1, 13)]
    return pl.DataFrame({
        "forecast_month": months,
        "decision_date": months,
        "ret": [0.01] * len(months),
        "g_fast": [raw_scale] * len(months),
        "g_slow": [raw_scale] * len(months),
        "g_fast_rms_vol": [raw_scale] * len(months),
        "g_slow_rms_vol": [raw_scale] * len(months),
        "g_fast_sharpe": [1.0] * len(months),
        "g_slow_sharpe": [1.0] * len(months),
        "g_regime": ["bull"] * len(months),
    })


def test_sharpe_selection_uses_normalized_legs_and_preserves_raw_audit() -> None:
    """Cross-market return scale must not determine Sharpe-mode conviction."""
    monthly = {"LOW": _monthly_frame(0.01), "HIGH": _monthly_frame(0.10)}
    clusters = {"LOW": "equity", "HIGH": "equity"}

    sharpe = _forecast_rows(
        monthly,
        clusters,
        signal_mode="sharpe",
        target_abs_forecast=1.0,
        forecast_cap=2.0,
    )
    continuous = _forecast_rows(
        monthly,
        clusters,
        signal_mode="continuous",
        target_abs_forecast=1.0,
        forecast_cap=2.0,
    )

    sharpe_low = sharpe["LOW"].drop_nulls("combined_forecast").tail(1).row(
        0, named=True
    )
    sharpe_high = sharpe["HIGH"].drop_nulls("combined_forecast").tail(1).row(
        0, named=True
    )
    continuous_low = continuous["LOW"].drop_nulls("combined_forecast").tail(1)
    continuous_high = continuous["HIGH"].drop_nulls("combined_forecast").tail(1)

    assert sharpe_low["g_fast"] == pytest.approx(0.01)
    assert sharpe_high["g_fast"] == pytest.approx(0.10)
    assert sharpe_low["g_raw_forecast"] == pytest.approx(1.0)
    assert sharpe_high["g_raw_forecast"] == pytest.approx(1.0)
    assert sharpe_low["combined_forecast"] == pytest.approx(
        sharpe_high["combined_forecast"]
    )
    assert continuous_high["combined_forecast"][0] > continuous_low[
        "combined_forecast"
    ][0]

"""Tests for the combined EWMAC notebook diagnostics facade."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.pipelines.ewmac_notebook import (
    ExecutableCashSettings,
    build_ewmac_notebook_view,
)


def _combined_result() -> CombinedForecastResult:
    """Return a small complete combined forecast suitable for both P&L paths."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "close": [100.0, 101.0, 102.0, 101.0, 103.0],
        "point_change": [None, 1.0, 1.0, -1.0, 2.0],
        "point_vol": [1.0] * 5,
        "combined_forecast": [1.0, 2.0, 2.0, 1.0, -1.0],
        "avg_position": [1.0] * 5,
        "smooth_avg_position": [1.0] * 5,
        "subsystem_position": [1.0, 2.0, 2.0, 1.0, -1.0],
        "normalized_position": [1.0, 2.0, 2.0, 1.0, -1.0],
        "is_roll": [False, False, True, False, False],
    })
    return CombinedForecastResult(
        frame=frame,
        weights={"16/64": 1.0},
        fdm=1.0,
        turnover=1.0,
        audit={"forecast_elig": True},
    )


def test_notebook_view_builds_subsystem_and_executable_cash_paths():
    """The facade keeps relative-risk and account-scaled P&L distinct."""
    result = _combined_result()
    view = build_ewmac_notebook_view(
        result,
        instrument_code="TEST",
        multiplier=10.0,
        ann_cost_sr=0.01,
        cash_settings=ExecutableCashSettings(
            capital=1_000.0,
            target_vol=0.20,
            weight=0.50,
            trade_cost_usd_per_con=2.0,
            roll_cost_usd_per_con=3.0,
        ),
    )

    assert view.forecasts is result.frame
    assert "net_pnl_usd" in view.pnl.columns
    assert view.cash is not None
    assert {"target_cons", "exec_cons", "cash_realized_net_pnl_usd"} <= set(
        view.cash.columns
    )
    assert view.forecast_positions(start="2024-01-03").height == 3

    subsystem = view.subsystem_pnl_curves()
    expected_subsystem_net = view.pnl.get_column("net_pnl_usd").fill_null(0.0).sum()
    assert subsystem.get_column("cum_net_pnl_usd")[-1] == pytest.approx(
        expected_subsystem_net
    )
    cash = view.cash_pnl_curves()
    expected_cash_net = (
        view.cash.get_column("cash_realized_net_pnl_usd").fill_null(0.0).sum()
    )
    assert cash.get_column("cum_cash_realized_net_pnl_usd")[-1] == pytest.approx(
        expected_cash_net
    )


def test_notebook_view_hvplot_contains_cash_panels_when_requested():
    """The notebook dashboard renders all forecast, position, and P&L panels."""
    view = build_ewmac_notebook_view(
        _combined_result(),
        instrument_code="TEST",
        multiplier=10.0,
        cash_settings=ExecutableCashSettings(
            capital=1_000.0,
            target_vol=0.20,
        ),
    )

    dashboard = view.plot(width=500, height=150)

    assert len(dashboard) == 7


def test_cash_curve_is_explicitly_optional():
    """A relative-risk view must not silently claim executable cash results."""
    view = build_ewmac_notebook_view(
        _combined_result(),
        instrument_code="TEST",
        multiplier=10.0,
    )

    assert view.cash is None
    with pytest.raises(ValueError, match="cash P&L was not requested"):
        view.cash_pnl_curves()
    assert len(view.plot(width=500, height=150)) == 5

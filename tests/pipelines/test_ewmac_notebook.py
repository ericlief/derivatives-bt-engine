"""Tests for the combined EWMAC notebook diagnostics facade."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.pipelines.ewmac_notebook import (
    ExecutableCashSettings,
    SubsystemReferenceSettings,
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
        "is_roll": [False, False, True, False, False],
    })
    return CombinedForecastResult(
        frame=frame,
        weights={"16/64": 1.0},
        fdm=1.0,
        audit={"forecast_elig": True},
    )


def test_notebook_view_builds_reference_return_and_cash_paths():
    """The facade keeps equal-weight reference returns and cash P&L distinct."""
    result = _combined_result()
    view = build_ewmac_notebook_view(
        result,
        instrument_code="TEST",
        multiplier=10.0,
        ann_cost_sr=0.01,
        reference_settings=SubsystemReferenceSettings(
            capital=1_000.0,
            target_vol=0.20,
            reference_book_count=2,
            reference_idm=1.0,
            max_weight_idm=1.0,
        ),
        cash_settings=ExecutableCashSettings(
            capital=1_000.0,
            target_vol=0.20,
            weight=0.50,
            trade_cost_usd_per_con=2.0,
            roll_cost_usd_per_con=3.0,
        ),
    )

    assert view.forecasts is result.frame
    assert "ref_net_pct" in view.reference.columns
    assert view.reference_turnover is not None
    assert {
        "ref_avg_pos",
        "ref_smooth_avg_pos",
        "ref_tgt_pos",
        "ref_norm_pos",
        "ref_abs_pos_change",
        "ref_roll_mean_abs_pos_change",
        "daily_dvol",
    } <= set(view.reference.columns)
    assert "daily_dollar_vol_per_contract" not in view.reference.columns
    assert view.cash is not None
    assert {
        "tgt_pos",
        "exec_con",
        "held_con",
        "ann_dvol",
        "trade_cost_per_con",
        "roll_cost_per_con",
        "net_pnl_usd",
    } <= set(view.cash.columns)
    assert not any("avg_roll" in name for name in view.cash.columns)
    assert view.forecast_positions(start="2024-01-03").height == 3

    reference = view.subsystem_reference_pct_curves()
    expected_reference_net = (
        view.reference.get_column("ref_net_pct")
        .fill_null(0.0)
        .sum()
    )
    assert reference.get_column(
        "cum_ref_net_pct"
    )[-1] == pytest.approx(
        expected_reference_net
    )
    cash = view.cash_pnl_curves()
    expected_cash_net = (
        view.cash.get_column("net_pnl_usd").fill_null(0.0).sum()
    )
    assert cash.get_column("cum_net_pnl_usd")[-1] == pytest.approx(
        expected_cash_net
    )


def test_notebook_view_hvplot_contains_cash_panels_when_requested():
    """The notebook dashboard renders all forecast, position, and P&L panels."""
    view = build_ewmac_notebook_view(
        _combined_result(),
        instrument_code="TEST",
        multiplier=10.0,
        reference_settings=SubsystemReferenceSettings(
            capital=1_000.0,
            target_vol=0.20,
        ),
        cash_settings=ExecutableCashSettings(
            capital=1_000.0,
            target_vol=0.20,
        ),
    )

    dashboard = view.plot(width=500, height=150)

    assert len(dashboard) == 7


def test_cash_curve_is_explicitly_optional():
    """A reference-return view must not silently claim executable cash results."""
    view = build_ewmac_notebook_view(
        _combined_result(),
        instrument_code="TEST",
        multiplier=10.0,
        reference_settings=SubsystemReferenceSettings(
            capital=1_000.0,
            target_vol=0.20,
        ),
    )

    assert view.cash is None
    with pytest.raises(ValueError, match="cash P&L was not requested"):
        view.cash_pnl_curves()
    assert len(view.plot(width=500, height=150)) == 5

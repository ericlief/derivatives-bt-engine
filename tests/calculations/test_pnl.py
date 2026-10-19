"""Tests for subsystem-reference returns and executable cash P&L."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.pnl import (
    apply_forecast_position_buffer,
    append_reference_position_turnover,
    build_executable_cash_pnl_curve,
    build_subsystem_reference_return_curve,
)


def test_subsystem_reference_return_uses_equal_weight_risk_budget():
    frame = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 5)],
        "point_change": [None, 1.0, 1.0],
        "point_vol": [2.0, 2.0, 2.0],
        "combined_forecast": [1.0, 2.0, 3.0],
        "fx_to_usd": [2.0, 2.0, 2.0],
    })

    result = build_subsystem_reference_return_curve(
        frame,
        capital=1_000.0,
        target_vol=0.16,
        reference_book_count=2,
        reference_idm=1.0,
        max_weight_idm=1.0,
        multiplier=10.0,
        ann_cost_sr=0.10,
        annualization_days=256,
    )
    result, turnover = append_reference_position_turnover(
        result,
        average_position_ewm_com=2,
        turnover_rolling_days=2,
        annualization_days=256,
    )

    assert result.get_column("ref_weight").to_list() == pytest.approx(
        [0.5] * 3
    )
    assert result.get_column(
        "ref_annual_risk_budget_usd"
    ).to_list() == pytest.approx([80.0] * 3)
    assert result.get_column(
        "ref_daily_risk_budget_usd"
    ).to_list() == pytest.approx([5.0] * 3)
    assert result.get_column(
        "ref_return_capital_usd"
    ).to_list() == pytest.approx([500.0] * 3)
    assert result.get_column(
        "daily_dollar_vol_per_contract"
    ).to_list() == pytest.approx([40.0] * 3)
    assert result.get_column(
        "ref_average_position"
    ).to_list() == pytest.approx([0.125] * 3)
    assert result.get_column(
        "ref_target_position"
    ).to_list() == pytest.approx([0.125, 0.25, 0.375])
    held = result.get_column("held_ref_position").to_list()
    assert held[0] is None
    assert held[1:] == pytest.approx([0.125, 0.25])
    assert result.get_column(
        "ref_gross_return"
    ).to_list()[1:] == pytest.approx(
        [0.005, 0.01]
    )
    costs = result.get_column("ref_cost_return").to_list()
    assert costs[0] is None
    assert costs[1] == pytest.approx(-0.016 * (1 / 365.25))
    assert costs[2] == pytest.approx(-0.016 * (3 / 365.25))
    assert result.get_column("ref_net_return")[1] == pytest.approx(
        0.005 + costs[1]
    )
    assert turnover is not None
    assert "smoothed_ref_average_position" in result.columns
    assert "ref_normalized_position" in result.columns


def test_subsystem_reference_preserves_missing_historical_fx_as_invalid():
    frame = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2)],
        "point_change": [None, 1.0],
        "point_vol": [1.0, 1.0],
        "combined_forecast": [1.0, 1.0],
        "fx_to_usd": [None, None],
    })

    result = build_subsystem_reference_return_curve(
        frame,
        capital=1_000.0,
        target_vol=0.20,
        reference_book_count=10,
        reference_idm=2.5,
        max_weight_idm=0.20,
        multiplier=5.0,
        ann_cost_sr=0.0,
    )

    assert result.get_column("ref_net_return").null_count() == 2
    assert result.get_column("ref_return_valid").to_list() == [False, False]


def test_executable_cash_pnl_separates_trade_and_policy_roll_costs():
    """Buffered position changes and held-contract rolls remain independent."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "close": [100.0, 101.0, 102.0, 103.0, 104.0],
        "point_change": [None, 1.0, 1.0, 1.0, 1.0],
        "point_vol": [1.0] * 5,
        "combined_forecast": [1.0, 2.0, 2.0, 1.0, 1.0],
        "fx_to_usd": [1.0] * 5,
        "is_roll": [False, False, True, False, True],
    })

    result = build_executable_cash_pnl_curve(
        frame,
        capital=1_000.0,
        target_vol=0.20,
        weight=0.50,
        idm=1.0,
        multiplier=10.0,
        trade_cost_usd_per_con=2.0,
        roll_cost_usd_per_con=3.0,
        annualization_days=4,
    )

    assert result.get_column("close").to_list() == frame.get_column(
        "close"
    ).to_list()
    assert result.get_column("average_position").to_list() == pytest.approx(
        [5.0] * 5
    )
    assert result.get_column(
        "buffer_width_position"
    ).to_list() == pytest.approx([0.5] * 5)
    assert result.get_column("exec_cons").to_list() == [5, 10, 10, 6, 6]
    assert result.get_column("trade_cons").to_list() == pytest.approx(
        [5.0, 5.0, 0.0, 4.0, 0.0]
    )
    assert result.get_column("realized_roll_cons").to_list() == pytest.approx(
        [0.0, 0.0, 10.0, 0.0, 6.0]
    )
    assert result.get_column("interval_avg_abs_cons").to_list() == [
        7.5,
        7.5,
        7.5,
        8.0,
        8.0,
    ]
    assert result.get_column("expected_roll_cons").to_list() == [0, 0, 8, 0, 8]
    assert result.get_column("cash_trade_cost_usd").to_list() == pytest.approx(
        [-10.0, -10.0, 0.0, -8.0, 0.0]
    )
    assert result.get_column("cash_realized_roll_cost_usd").to_list() == (
        pytest.approx([0.0, 0.0, -30.0, 0.0, -18.0])
    )
    assert result.get_column("cash_expected_roll_cost_usd").to_list() == (
        pytest.approx([0.0, 0.0, -24.0, 0.0, -24.0])
    )


def test_forecast_buffer_matches_pysystemtrade_reference_fixture():
    """Match pysystemtrade apply_buffer with its default trade-to-edge policy."""
    frame = pl.DataFrame({
        "target_position": [5.0, 5.4, 6.1, 5.7, 4.2, -0.6, -1.6],
        "average_position": [5.0] * 7,
    })

    result = apply_forecast_position_buffer(frame)

    # Generated from pysystemtrade's apply_buffer(..., trade_to_edge=True,
    # roundpositions=True). Exact .5 boundaries also verify pandas-compatible
    # Polars' ties-to-even rounding matches pandas at exact half contracts.
    assert result.get_column("buffer_top_cons").to_list() == [
        6, 6, 7, 6, 5, 0, -1,
    ]
    assert result.get_column("buffer_bot_cons").to_list() == [
        4, 5, 6, 5, 4, -1, -2,
    ]
    assert result.get_column("exec_cons").to_list() == [5, 5, 6, 6, 5, 0, -1]


def test_average_position_is_pysystemtrade_vol_scalar_with_weight_and_idm():
    """Annual and daily formulations produce the same average contracts."""
    capital = 100_000.0
    target_vol = 0.20
    weight = 0.10
    idm = 2.5
    annualization_days = 256
    point_vol = 2.0
    multiplier = 10.0
    fx_to_usd = 1.25

    ann_dvol = (
        point_vol
        * annualization_days**0.5
        * multiplier
        * fx_to_usd
    )
    our_avg_position = capital * target_vol * weight * idm / ann_dvol

    pst_daily_cash_vol_target = (
        capital * target_vol / annualization_days**0.5
    )
    pst_daily_dvol = point_vol * multiplier * fx_to_usd
    pst_vol_scalar = pst_daily_cash_vol_target / pst_daily_dvol
    pst_portfolio_avg_position = pst_vol_scalar * weight * idm

    assert our_avg_position == pytest.approx(pst_portfolio_avg_position)

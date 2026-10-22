"""Tests for subsystem-reference percentages and executable cash P&L."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.pnl import (
    apply_forecast_position_buffer,
    append_reference_position_turnover,
    build_executable_cash_pnl_curve,
    build_fixed_capital_portfolio_curve,
    build_subsystem_reference_return_curve,
)


def test_subsystem_reference_pct_uses_equal_weight_risk_budget():
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
        "ref_ann_risk_usd"
    ).to_list() == pytest.approx([80.0] * 3)
    assert result.get_column(
        "ref_daily_risk_usd"
    ).to_list() == pytest.approx([5.0] * 3)
    assert result.get_column(
        "ref_budget_usd"
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
        "ref_gross_pct"
    ).to_list()[1:] == pytest.approx(
        [0.5, 1.0]
    )
    assert result.get_column("ref_gross_usd").to_list()[1:] == pytest.approx(
        [2.5, 5.0]
    )
    costs = result.get_column("ref_cost_pct").to_list()
    assert costs[0] is None
    assert costs[1] == pytest.approx(-1.6 * (1 / 365.25))
    assert costs[2] == pytest.approx(-1.6 * (3 / 365.25))
    assert result.get_column("ref_net_pct")[1] == pytest.approx(
        0.5 + costs[1]
    )
    assert result.get_column("ref_cost_usd")[1] == pytest.approx(
        costs[1] / 100.0 * 500.0
    )
    assert result.get_column("ref_net_usd")[1] == pytest.approx(
        2.5 + costs[1] / 100.0 * 500.0
    )
    assert result.get_column("cum_ref_gross_usd").to_list() == (
        pytest.approx([0.0, 2.5, 7.5])
    )
    assert result.get_column("cum_ref_net_usd").to_list() == (
        pytest.approx([
            0.0,
            result.get_column("ref_net_usd")[1],
            result.get_column("ref_net_usd")[1]
            + result.get_column("ref_net_usd")[2],
        ])
    )
    assert result.get_column("cum_ref_net_usd").to_list() == pytest.approx(
        result.select(
            pl.col("ref_net_usd").fill_null(0.0).cum_sum()
        ).to_series().to_list()
    )
    assert result.get_column("cum_ref_gross_pct").to_list() == (
        pytest.approx([0.0, 0.5, 1.5])
    )
    assert result.get_column("cum_ref_net_pct").to_list() == (
        pytest.approx([
            0.0,
            result.get_column("ref_net_pct")[1],
            result.get_column("ref_net_pct")[1]
            + result.get_column("ref_net_pct")[2],
        ])
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

    assert result.get_column("ref_net_pct").null_count() == 2
    assert result.get_column("ref_return_valid").to_list() == [False, False]


def test_executable_cash_pnl_separates_trade_and_policy_roll_costs():
    """Buffered position changes and held-contract rolls remain independent."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "close": [100.0, 101.0, 102.0, 103.0, 104.0],
        # Distinct from additive Panama close: notional must use raw contract px.
        "contract_close": [200.0, 202.0, 204.0, 206.0, 208.0],
        "point_change": [None, 1.0, 1.0, 1.0, 1.0],
        "fast_point_vol": [0.8] * 5,
        "slow_point_vol": [1.2] * 5,
        "point_vol": [1.0] * 5,
        "combined_forecast": [1.0, 2.0, 2.0, 1.0, 1.0],
        "ref_normalized_position": [0.1, 0.2, 0.3, 0.4, 0.5],
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
    assert result.get_column("con_px").to_list() == frame.get_column(
        "contract_close"
    ).to_list()
    assert result.columns[
        result.columns.index("fast_point_vol"):
        result.columns.index("ann_pct_vol") + 1
    ] == [
        "fast_point_vol",
        "slow_point_vol",
        "point_vol",
        "daily_dvol",
        "ann_dvol",
        "ann_pct_vol",
    ]
    assert result.get_column("daily_dvol").to_list() == pytest.approx(
        [10.0] * 5
    )
    assert result.get_column("ann_dvol").to_list() == pytest.approx(
        [20.0] * 5
    )
    assert result.get_column("ann_pct_vol").to_list() == pytest.approx(
        [
            20.0 / (close * 10.0) * 100.0
            for close in frame.get_column("contract_close").to_list()
        ]
    )
    assert result.get_column("avg_pos").to_list() == pytest.approx(
        [5.0] * 5
    )
    assert result.get_column("track_err_ann_usd").to_list() == (
        pytest.approx([None, 0.0, 0.0, 0.0, 20.0])
    )
    assert result.get_column("track_err_ann_pct_cap").to_list() == (
        pytest.approx([None, 0.0, 0.0, 0.0, 2.0])
    )
    assert result.get_column("ref_norm_pos").to_list() == pytest.approx(
        [0.1, 0.2, 0.3, 0.4, 0.5]
    )
    assert "average_position" not in result.columns
    assert "ref_normalized_position" not in result.columns
    assert result.get_column(
        "buf_width_pos"
    ).to_list() == pytest.approx([0.5] * 5)
    assert result.get_column("exec_con").to_list() == [5, 10, 10, 6, 5]
    exec_idx = result.columns.index("exec_con")
    assert result.columns[exec_idx:exec_idx + 4] == [
        "exec_con", "held_con", "trade_con", "roll_con",
    ]
    assert result.get_column("trade_con").to_list() == pytest.approx(
        [5.0, 5.0, 0.0, -4.0, -1.0]
    )
    assert "trade_abs_con" not in result.columns
    assert result.get_column("roll_con").to_list() == pytest.approx(
        [0.0, 0.0, 10.0, 0.0, 5.0]
    )
    assert result.get_column("rebalance_on_roll").to_list() == [True] * 5
    assert result.get_column("roll_rebalanced").to_list() == [
        False, False, True, False, True,
    ]
    assert result.get_column("trade_cost_per_con").to_list() == [
        2.0, 2.0, 2.0, 2.0, 2.0,
    ]
    assert result.get_column("roll_cost_per_con").to_list() == [
        3.0, 3.0, 3.0, 3.0, 3.0,
    ]
    assert result.get_column("trade_cost_usd").to_list() == pytest.approx(
        [-10.0, -10.0, 0.0, -8.0, -2.0]
    )
    assert result.get_column("roll_cost_usd").to_list() == (
        pytest.approx([0.0, 0.0, -30.0, 0.0, -15.0])
    )
    assert result.get_column("trade_cost_pct").to_list() == (
        pytest.approx([-1.0, -1.0, 0.0, -0.8, -0.2])
    )
    assert result.get_column("roll_cost_pct").to_list() == pytest.approx(
        [0.0, 0.0, -3.0, 0.0, -1.5]
    )
    assert result.get_column("total_cost_pct").to_list() == (
        pytest.approx([-1.0, -1.0, -3.0, -0.8, -1.7])
    )
    assert result.get_column("cum_total_cost_pct").to_list() == (
        pytest.approx([-1.0, -2.0, -5.0, -5.8, -7.5])
    )
    assert result.get_column("gross_pct").to_list() == (
        pytest.approx([0.0, 5.0, 10.0, 10.0, 6.0])
    )
    assert result.get_column("net_pct").to_list() == (
        pytest.approx([-1.0, 4.0, 7.0, 9.2, 4.3])
    )
    assert result.get_column("cum_gross_pnl_usd").to_list() == pytest.approx(
        [0.0, 50.0, 150.0, 250.0, 310.0]
    )
    assert result.get_column(
        "cum_net_pnl_usd"
    ).to_list() == pytest.approx([-10.0, 30.0, 100.0, 192.0, 235.0])
    assert not any("avg_roll" in name or "_contrib" in name for name in result.columns)
    assert not any(column.startswith("cash_") for column in result.columns)

    buffered_roll = build_executable_cash_pnl_curve(
        frame,
        capital=1_000.0,
        target_vol=0.20,
        weight=0.50,
        idm=1.0,
        multiplier=10.0,
        trade_cost_usd_per_con=2.0,
        roll_cost_usd_per_con=3.0,
        annualization_days=4,
        rebalance_on_roll=False,
    )
    assert buffered_roll.get_column("exec_con").to_list() == [
        5, 10, 10, 6, 6,
    ]
    assert buffered_roll.get_column("roll_con").to_list() == (
        pytest.approx([0.0, 0.0, 10.0, 0.0, 6.0])
    )


def test_ann_pct_vol_is_null_without_raw_contract_close():
    """Adjusted Panama levels are never substituted for missing notional px."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, 1)],
        "close": [100.0],
        "point_change": [1.0],
        "point_vol": [2.0],
        "combined_forecast": [1.0],
        "fx_to_usd": [1.0],
    })
    result = build_executable_cash_pnl_curve(
        frame,
        capital=1_000.0,
        target_vol=0.20,
        weight=0.50,
        idm=1.0,
        multiplier=10.0,
        trade_cost_usd_per_con=0.0,
        roll_cost_usd_per_con=0.0,
    )

    assert result.get_column("ann_pct_vol").null_count() == 1
    assert result.get_column("track_err_ann_usd").null_count() == 1
    assert result.get_column("track_err_ann_pct_cap").null_count() == 1


def test_fixed_capital_portfolio_curve_reports_percentage_points():
    """Portfolio USD flows and instrument contributions share account capital."""
    cash = pl.DataFrame({
        "date": [date(2024, 1, 1)],
        "symbol": ["MES"],
        "pnl_valid": [True],
        "gross_pnl_usd": [50.0],
        "trade_cost_usd": [-10.0],
        "roll_cost_usd": [-20.0],
        "net_pnl_usd": [20.0],
    })

    result = build_fixed_capital_portfolio_curve(
        cash,
        initial_capital_usd=1_000.0,
    )

    assert result["portfolio_gross_pct"][0] == pytest.approx(5.0)
    assert result["portfolio_trade_cost_pct"][0] == pytest.approx(-1.0)
    assert result["portfolio_roll_cost_pct"][0] == pytest.approx(-2.0)
    assert result["portfolio_total_cost_pct"][0] == pytest.approx(-3.0)
    assert result["portfolio_net_pct"][0] == pytest.approx(2.0)
    assert result["cumulative_portfolio_total_cost_pct"][0] == pytest.approx(
        -3.0
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

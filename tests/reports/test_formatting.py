"""Tests for the shared public-report numeric precision contract."""

import polars as pl
import pytest

from derivatives_bt_engine.reports.formatting import (
    rename_public_report_columns,
    round_public_report,
)


def test_round_public_report_formats_phase2_money_and_diagnostics():
    """Dollar fields use cents while general Phase 2 floats use four places."""
    report = pl.DataFrame({
        "symbol": ["MZC"],
        "ann_dvol": [610.216],
        "risk_traded_day": [56.3289],
        "net_pnl_usd": [123.456],
        "weight": [0.0250958569],
        "net_sr": [0.4409032124],
        "rank": [9],
    })

    rounded = round_public_report(report)

    assert rounded["ann_dvol"][0] == pytest.approx(610.22)
    assert rounded["risk_traded_day"][0] == pytest.approx(56.33)
    assert rounded["net_pnl_usd"][0] == pytest.approx(123.46)
    assert rounded["weight"][0] == pytest.approx(0.0251)
    assert rounded["net_sr"][0] == pytest.approx(0.4409)
    assert rounded["rank"][0] == 9


def test_round_public_report_keeps_reproduction_rates_at_six_places():
    """FX and percentage-point returns retain audit precision."""
    report = pl.DataFrame({
        "cur_fx_to_usd": [0.00660449],
        "ann_return_vol": [0.12345649],
        "ann_pct_vol": [1.23456789],
        "con_px": [123.456789123],
        "trade_cost_pct": [-0.00012649],
    })

    rounded = round_public_report(report)

    assert rounded["cur_fx_to_usd"][0] == pytest.approx(0.006604)
    assert rounded["ann_return_vol"][0] == pytest.approx(0.123456)
    assert rounded["ann_pct_vol"][0] == pytest.approx(1.234568)
    assert rounded["con_px"][0] == pytest.approx(123.456789)
    assert rounded["trade_cost_pct"][0] == pytest.approx(-0.000126)


def test_public_report_uses_percentage_points_for_portfolio_cash_curve():
    """Portfolio curve percent fields receive compact names at its boundary."""
    report = pl.DataFrame({
        "portfolio_gross_pct": [1.23456789],
        "portfolio_total_cost_pct": [-0.00012649],
        "cumulative_portfolio_total_cost_pct": [-0.12345678],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "gross_pct",
        "total_cost_pct",
        "cum_total_cost_pct",
    ]
    assert public.row(0) == pytest.approx((1.234568, -0.000126, -0.123457))


def test_public_report_renames_cash_cost_columns_at_boundary():
    """Internal unit-explicit cash names become compact public USD names."""
    report = pl.DataFrame({
        "trade_cost_usd_per_con": [1.234],
        "ann_roll_cost_usd": [12.346],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == ["trade_cost_per_con", "ann_roll_cost"]
    assert public.row(0) == pytest.approx((1.23, 12.35))


def test_joined_cash_audit_columns_drop_cash_prefix():
    """Per-instrument cash fields stay concise beside forecasts/reference data."""
    report = pl.DataFrame({
        "cash_pnl_valid": [True],
        "cash_gross_pnl_usd": [12.346],
        "cash_net_pct": [0.1234567],
        "cum_cash_net_pnl_usd": [20.0],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "pnl_valid",
        "gross_pnl_usd",
        "net_pct",
        "cum_net_pnl_usd",
    ]
    assert public.row(0) == (True, 12.35, 0.123457, 20.0)


def test_public_report_renames_buffer_parameters_at_boundary():
    """Continuous positions and buffer parameters use compact report tokens."""
    report = pl.DataFrame({
        "average_position": [2.5],
        "target_position": [4.0],
        "buffer_width_position": [0.25],
        "buffer_size": [0.10],
        "buffer_trade_to_edge": [True],
        "rebalance_on_roll": [True],
        "roll_rebalanced": [False],
    })

    public = rename_public_report_columns(report)

    assert public.columns == [
        "avg_pos",
        "targ_pos",
        "buf_width_pos",
        "buf_size",
        "buf_trade_to_edge",
        "rebalance_on_roll",
        "roll_rebalanced",
    ]


def test_public_report_renames_subsystem_reference_columns():
    """Reference sizing and percentages remain distinct from portfolio cash."""
    report = pl.DataFrame({
        "ref_book_count": [15],
        "ref_idm": [2.5],
        "ref_ann_risk_usd": [2_500.126],
        "ref_budget_usd": [12_500.126],
        "ref_average_position": [3.45678],
        "ref_target_position": [4.56789],
        "held_ref_position": [4.0],
        "ref_net_pct": [0.123449],
        "ref_return_valid": [True],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "ref_book_n",
        "ref_idm",
        "ref_ann_risk_usd",
        "ref_budget_usd",
        "ref_avg_pos",
        "ref_targ_pos",
        "held_ref_pos",
        "ref_net_pct",
        "ref_ret_valid",
    ]
    assert public.row(0)[:8] == pytest.approx(
        (
            15,
            2.5,
            2_500.13,
            12_500.13,
            3.4568,
            4.5679,
            4.0,
            0.123449,
        )
    )
    assert public["ref_ret_valid"][0]


def test_public_report_uses_position_dvol_and_contract_tokens():
    """Research cash reports compact reference, dollar-vol, and contract names."""
    report = pl.DataFrame({
        "ref_abs_position_changes": [0.012345],
        "ref_rolling_mean_abs_position_change": [0.123456],
        "daily_dollar_vol_per_contract": [123.456],
        "smoothed_ref_average_position": [2.34567],
        "ref_normalized_position": [1.23456],
        "roll_cons": [3.0],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "ref_abs_pos_change",
        "ref_roll_mean_abs_pos_change",
        "daily_dvol",
        "ref_smooth_avg_pos",
        "ref_norm_pos",
        "roll_con",
    ]
    assert public.row(0) == pytest.approx(
        (0.0123, 0.1235, 123.46, 2.3457, 1.2346, 3.0)
    )


def test_public_report_renames_grid_parameters_and_rounds_margin_money():
    """Grid summaries share compact names and the central money precision."""
    report = pl.DataFrame({
        "target_volatility": [0.20],
        "maximum_instrument_margin_percent": [20.0],
        "maximum_instrument_margin_usd": [12_345.678],
        "book_margin_at_maximum_usd": [54_321.987],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "target_vol",
        "max_instr_margin_pct",
        "max_instr_margin_usd",
        "book_margin_at_max_usd",
    ]
    assert public.row(0) == pytest.approx((0.20, 20.0, 12_345.68, 54_321.99))

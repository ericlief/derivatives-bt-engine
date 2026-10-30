"""Tests for the shared public-report numeric precision contract."""

import polars as pl
import pytest

from derivatives_bt_engine.reports.formatting import (
    order_cash_pnl_report,
    rename_public_report_columns,
    round_public_report,
)


def test_round_public_report_formats_phase2_money_and_diagnostics():
    """Dollar fields use cents while general Phase 2 floats use four places."""
    report = pl.DataFrame({
        "symbol": ["MZC"],
        "ann_dvol": [610.216],
        "risk_traded_day": [56.3289],
        "net_pnl": [123.456],
        "weight": [0.0250958569],
        "net_sr": [0.4409032124],
        "rank": [9],
    })

    rounded = round_public_report(report)

    assert rounded["ann_dvol"][0] == pytest.approx(610.22)
    assert rounded["risk_traded_day"][0] == pytest.approx(56.33)
    assert rounded["net_pnl"][0] == pytest.approx(123.456)
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


def test_cash_report_preserves_small_point_changes_and_pnl():
    """Small cash audit contributions remain visibly nonzero in Parquet."""
    report = pl.DataFrame({
        "point_change": [0.00001234567],
        "cash_gross_pnl_usd": [0.00123456],
        "cash_net_pnl_usd": [-0.00123456],
        "cash_gross_pct": [0.00001234567],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.row(0) == pytest.approx(
        (0.000012, 0.0012, -0.0012, 0.000012)
    )


def test_public_report_uses_percentage_points_for_portfolio_cash_curve():
    """Portfolio curve percent fields receive compact names at its boundary."""
    report = pl.DataFrame({
        "portfolio_gross_pct": [1.23456789],
        "portfolio_total_cost_pct": [-0.00012649],
        "cumulative_portfolio_total_cost_pct": [-0.12345678],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "port_gross_pct",
        "port_total_cost_pct",
        "cum_port_total_cost_pct",
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


def test_reference_cumulative_amounts_use_consistent_report_rounding():
    """Reference amounts use cents without an implicit USD suffix."""
    public = round_public_report(pl.DataFrame({
        "cum_ref_gross": [12.346],
        "cum_ref_net": [10.005],
    }))

    assert public.row(0) == pytest.approx((12.35, 10.01))


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
        "gross_pnl",
        "net_pct",
        "cum_net_pnl",
    ]
    assert public.row(0) == (True, 12.346, 0.123457, 20.0)


def test_cash_pnl_report_orders_primary_fields_before_params_and_audit():
    """Daily reading fields lead; sizing parameters and lineage trail."""
    report = pl.DataFrame({
        "date": ["2026-01-02"],
        "symbol": ["MGC"],
        "historical_instrument_code": ["GOLD"],
        "weight": [0.1],
        "close": [3000.0],
        "point_change": [1.25],
        "point_vol": [25.0],
        "combined_forecast": [1.0],
        "target_position": [2.0],
        "held_cons": [1],
        "exec_cons": [2],
        "signed_trade_cons": [1],
        "vol_scalar": [3.5],
        "buffer_width_position": [0.2],
        "buffer_top_cons": [2],
        "buffer_bot_cons": [1],
        "cash_gross_pnl_usd": [10.0],
        "cash_gross_pct": [0.01],
        "cash_net_pnl_usd": [9.0],
        "cash_net_pct": [0.009],
        "cum_cash_gross_pnl_usd": [100.0],
        "cum_cash_net_pnl_usd": [90.0],
        "cum_cash_gross_pct": [0.1],
        "cum_cash_net_pct": [0.09],
        "cash_trade_cost_usd": [-1.0],
        "cash_roll_cost_usd": [0.0],
        "cash_pnl_valid": [True],
    })

    public = order_cash_pnl_report(rename_public_report_columns(report))

    assert public.columns[:26] == [
        "symbol", "date", "close", "pt_change", "pt_vol", "comb_fcst",
        "vol_scalar", "tgt_pos", "buf_width_pos", "buffer_top_con",
        "buffer_bot_con", "held_con", "exec_con", "trade_con", "gross_pnl",
        "gross_pct", "net_pnl", "net_pct", "cum_gross_pnl", "cum_net_pnl",
        "cum_gross_pct", "cum_net_pct", "trade_cost", "roll_cost", "weight",
        "hist_instr_code",
    ]


def test_public_report_renames_buffer_parameters_at_boundary():
    """Continuous positions and buffer parameters use compact report tokens."""
    report = pl.DataFrame({
        "average_position": [2.5],
        "vol_scalar": [5.0],
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
        "vol_scalar",
        "tgt_pos",
        "buf_width_pos",
        "buf_size",
        "buf_trade_to_edge",
        "rebalance_on_roll",
        "roll_rebalanced",
    ]


def test_public_trade_delta_is_signed():
    """The public trade quantity uses positive/negative position changes."""
    report = pl.DataFrame({
        "signed_trade_cons": [-2.0],
    })

    public = rename_public_report_columns(report)

    assert public.columns == ["trade_con"]
    assert public.row(0) == (-2.0,)


def test_public_report_renames_and_rounds_tracking_error_columns():
    """Tracking error uses USD cents and account percentage-point precision."""
    report = pl.DataFrame({
        "annual_tracking_error_usd": [123.456],
        "annual_tracking_error_pct_capital": [0.12345678],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "track_err_ann",
        "track_err_ann_pct_cap",
    ]
    assert public.row(0) == pytest.approx((123.46, 0.123457))


def test_public_report_abbreviates_combined_and_portfolio():
    """Public schemas use the agreed comb/port tokens consistently."""
    report = pl.DataFrame({
        "combined_forecast": [1.25],
        "period_year_fraction": [0.002738],
        "forecast_valid": [True],
        "portfolio_cash_net_pnl_usd": [12.345],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == ["comb_fcst", "yr_frac", "fcst_valid", "port_net"]
    assert public.row(0) == (1.25, 0.002738, True, 12.34)


def test_public_report_abbreviates_candidate_position_and_tolerance():
    """Candidate, position, and tolerance terms use consistent short tokens."""
    report = pl.DataFrame({
        "candidate_ref_max_position": [4.25],
        "candidate_ref_contract_band_distance": [0.5],
        "ref_max_position": [5.0],
        "score_tie_tolerance_percent": [2.5],
    })

    public = rename_public_report_columns(report)

    assert public.columns == [
        "cand_ref_max_pos",
        "cand_ref_con_band_distance",
        "ref_max_pos",
        "score_tie_tol_pct",
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
        "ref_ann_risk",
        "ref_budget",
        "vol_scalar",
        "ref_tgt_pos",
        "ref_held_pos",
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
        "ref_avg_pos",
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
        "max_instr_marg_pct",
        "max_instr_marg",
        "book_marg_at_max",
    ]
    assert public.row(0) == pytest.approx((0.20, 20.0, 12_345.68, 54_321.99))


def test_public_report_compacts_margin_and_forecast_audit_names():
    """Margin terminology and long forecast-audit counts use shared tokens."""
    report = pl.DataFrame({
        "ib_whatif_initial_margin": [101.234],
        "ib_whatif_initial_margin_usd": [202.345],
        "ib_whatif_maintenance_margin": [81.234],
        "ib_whatif_maintenance_margin_usd": [162.345],
        "portfolio_margin_at_max_usd": [500.555],
        "instr_margin_limit_usd": [250.555],
        "forecast_post_warmup_invalid_obs": [3],
        "ref_roll_ann_turnover": [12.345],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == [
        "init_marg_native",
        "init_marg",
        "maint_marg_native",
        "maint_marg",
        "port_marg_at_max",
        "instr_marg_lim",
        "fcst_post_warmup_inval_n",
        "ref_roll_ann_turn",
    ]
    assert public.row(0) == pytest.approx(
        (101.23, 202.34, 81.23, 162.34, 500.56, 250.56, 3, 12.345)
    )

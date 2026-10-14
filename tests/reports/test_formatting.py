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
    """FX and return-volatility inputs retain the precision needed for audit."""
    report = pl.DataFrame({
        "cur_fx_to_usd": [0.00660449],
        "ann_return_vol": [0.12345649],
    })

    rounded = round_public_report(report)

    assert rounded["cur_fx_to_usd"][0] == pytest.approx(0.006604)
    assert rounded["ann_return_vol"][0] == pytest.approx(0.123456)


def test_public_report_renames_cash_cost_columns_at_boundary():
    """Internal unit-explicit cash names become compact public USD names."""
    report = pl.DataFrame({
        "trade_cost_usd_per_con": [1.234],
        "ann_roll_cost_usd": [12.346],
    })

    public = round_public_report(rename_public_report_columns(report))

    assert public.columns == ["trade_cost_per_con", "ann_roll_cost"]
    assert public.row(0) == pytest.approx((1.23, 12.35))


def test_public_report_renames_buffer_parameters_at_boundary():
    """Executable buffer parameters use centralized compact report tokens."""
    report = pl.DataFrame({
        "buffer_size": [0.10],
        "buffer_trade_to_edge": [True],
    })

    public = rename_public_report_columns(report)

    assert public.columns == ["buf_size", "buf_trade_to_edge"]

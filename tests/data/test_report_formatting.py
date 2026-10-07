"""Tests for the shared public-report numeric precision contract."""

import polars as pl
import pytest

from derivatives_bt_engine.data.report_formatting import round_public_report


def test_round_public_report_formats_phase2_money_and_diagnostics():
    """Dollar fields use cents while general Phase 2 floats use four places."""
    report = pl.DataFrame({
        "symbol": ["MZC"],
        "ann_dvol": [610.216],
        "risk_traded_day": [56.3289],
        "weight": [0.0250958569],
        "net_sr": [0.4409032124],
        "rank": [9],
    })

    rounded = round_public_report(report)

    assert rounded["ann_dvol"][0] == pytest.approx(610.22)
    assert rounded["risk_traded_day"][0] == pytest.approx(56.33)
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

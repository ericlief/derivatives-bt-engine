"""Tests for multiplier-, FX-, and cost-aware subsystem P&L."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.calculations.pnl import build_subsystem_pnl_curve


def test_subsystem_pnl_lags_position_and_accrues_cost_by_elapsed_year():
    frame = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 5)],
        "point_change": [None, 1.0, 1.0],
        "point_vol": [2.0, 2.0, 2.0],
        "avg_position": [0.5, 0.5, 0.5],
        "subsystem_position": [1.0, 2.0, 3.0],
        "fx_to_usd": [2.0, 2.0, 2.0],
    })

    result = build_subsystem_pnl_curve(
        frame,
        multiplier=10.0,
        ann_cost_sr=0.10,
        annualization_days=256,
    )

    assert result.get_column("pnl_position").to_list() == pytest.approx(
        [0.05, 0.10, 0.15]
    )
    held = result.get_column("held_pnl_position").to_list()
    assert held[0] is None
    assert held[1:] == pytest.approx([0.05, 0.10])
    assert result.get_column("gross_pnl_usd").to_list()[1:] == pytest.approx(
        [1.0, 2.0]
    )
    # The proxy is divided by multiplier and FX, leaving $16 annual risk.
    assert result.get_column("ann_risk_pts").to_list() == pytest.approx([0.8] * 3)
    costs = result.get_column("cost_pnl_usd").to_list()
    assert costs[0] is None
    assert costs[1] == pytest.approx(-1.6 * (1 / 365.25))
    assert costs[2] == pytest.approx(-1.6 * (3 / 365.25))
    assert result.get_column("net_pnl_usd")[1] == pytest.approx(
        1.0 + costs[1]
    )


def test_subsystem_pnl_preserves_missing_historical_fx_as_invalid():
    frame = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2)],
        "point_change": [None, 1.0],
        "point_vol": [1.0, 1.0],
        "avg_position": [1.0, 1.0],
        "subsystem_position": [1.0, 1.0],
        "fx_to_usd": [None, None],
    })

    result = build_subsystem_pnl_curve(frame, multiplier=5.0, ann_cost_sr=0.0)

    assert result.get_column("net_pnl_usd").null_count() == 2
    assert result.get_column("pnl_valid").to_list() == [False, False]

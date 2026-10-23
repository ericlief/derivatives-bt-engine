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
    _resolve_globex_asset,
    _resolve_stitched_symbol_mappings,
    _screen_row,
    build_ewmac_notebook_view,
    build_ewmac_notebook_view_from_screen,
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
        symbol="TEST",
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

    assert view.forecasts is not result.frame
    assert "comb_fcst" in view.forecasts.columns
    assert "combined_forecast" not in view.forecasts.columns
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
        "net_pnl",
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
        view.cash.get_column("net_pnl").fill_null(0.0).sum()
    )
    assert cash.get_column("cum_net_pnl")[-1] == pytest.approx(
        expected_cash_net
    )


def test_notebook_view_loads_contract_costs_from_screen_row(tmp_path):
    """A screen row supplies MES terms while retaining the input signal FX."""
    result = _combined_result()
    result = CombinedForecastResult(
        frame=result.frame.with_columns(pl.lit(1.25).alias("fx_to_usd")),
        weights=result.weights,
        fdm=result.fdm,
        audit=result.audit,
    )
    screen = pl.DataFrame({
        "symbol": ["MES"],
        "pst_instr_code": ["MES"],
        "signal_symbol": ["MES"],
        "mult": [5.0],
        "trade_sr": [0.001],
        "roll_sr": [0.002],
        "ref_ann_rolls": [4.0],
        "cost_usd": [1.25],
        "roll_cost_usd": [3.5],
    })
    screen_path = tmp_path / "screen.parquet"
    screen.write_parquet(screen_path)

    view = build_ewmac_notebook_view_from_screen(
        result,
        screen=screen_path,
        symbol="MES",
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
            idm=1.0,
        ),
    )

    assert view.symbol == "MES"
    assert view.reference_turnover is not None
    expected_ann_cost_sr = 0.001 * view.reference_turnover + 0.002 * 4.0
    assert view.reference["ref_ann_cost_pct"][0] == pytest.approx(
        -expected_ann_cost_sr * 0.20 * 100.0
    )
    assert view.reference["daily_dvol"][2] == pytest.approx(1.0 * 5.0 * 1.25)
    assert view.cash is not None
    assert view.cash["trade_cost_per_con"][0] == pytest.approx(1.25)
    assert view.cash["roll_cost_per_con"][0] == pytest.approx(3.5)
    assert view.cash["daily_dvol"][2] == pytest.approx(1.0 * 5.0 * 1.25)


def test_notebook_view_uses_labeled_snapshot_costs_when_whatif_is_null():
    """Margin-rejected WhatIf rows can use the screen's explicit estimate."""
    view = build_ewmac_notebook_view_from_screen(
        _combined_result(),
        screen=pl.DataFrame({
            "symbol": ["MGC"],
            "pst_instr_code": ["GOLD_micro"],
            "signal_symbol": ["GC"],
            "mult": [100.0],
            "ann_dvol": [100_000.0],
            "trade_sr": [None],
            "roll_sr": [None],
            "ref_ann_rolls": [6.0],
            "cost_usd": [None],
            "roll_cost_usd": [None],
            "snap_ref_config_cost_usd": [22.47],
            "snap_spread_cash_usd": [20.0],
            "ref_config_comm_usd": [2.47],
        }),
        symbol="MGC",
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
        ),
    )

    assert view.cash is not None
    assert view.cash["trade_cost_per_con"][0] == pytest.approx(22.47)
    assert view.cash["roll_cost_per_con"][0] == pytest.approx(24.94)
    assert view.cash["trade_cost_src"][0] == "snap_ref_config"
    assert view.cash["roll_cost_src"][0] == "snap_ref_config_calendar_spread"
    assert view.reference["trade_cost_src"][0] == "snap_ref_config"
    assert view.reference["roll_cost_src"][0] == "snap_ref_config_calendar_spread"


def test_notebook_screen_lookup_uses_executable_symbol_not_signal_symbol():
    screen = pl.DataFrame({
        "symbol": ["MGC"],
        "pst_instr_code": ["GOLD_micro"],
        "signal_symbol": ["GC"],
    })

    assert _screen_row(screen, "MGC")["pst_instr_code"] == "GOLD_micro"
    with pytest.raises(ValueError, match="no executable symbol row"):
        _screen_row(screen, "GC")


def test_stitched_view_resolves_executable_and_signal_mappings_separately():
    mappings = [
        {
            "symbol": "MGC",
            "pst_instr_code": "GOLD_micro",
            "signal_symbol": "GC",
            "ib_symbol": "MGC",
            "multiplier": 10.0,
        },
        {
            "symbol": "GC",
            "pst_instr_code": "GOLD",
            "signal_symbol": "GC",
            "ib_symbol": "GC",
            "multiplier": 100.0,
        },
    ]

    executable, signal = _resolve_stitched_symbol_mappings(
        mappings,
        symbol="MGC",
        signal_symbol=None,
    )

    assert executable["symbol"] == "MGC"
    assert executable["pst_instr_code"] == "GOLD_micro"
    assert signal["symbol"] == "GC"
    assert signal["pst_instr_code"] == "GOLD"
    assert _resolve_globex_asset(signal["pst_instr_code"]) == "GC"


def test_notebook_view_hvplot_contains_cash_panels_when_requested():
    """The notebook dashboard renders all forecast, position, and P&L panels."""
    view = build_ewmac_notebook_view(
        _combined_result(),
        symbol="TEST",
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
        symbol="TEST",
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

from datetime import date, timedelta

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_risk import (
    build_cost_risk_row,
    volatility_from_bars,
)
from derivatives_bt_engine.domain.instruments import resolve_active_months


def test_vxm_dated_contract_resolution_allows_every_month():
    assert resolve_active_months("VXM") == [
        "F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z",
    ]


def test_volatility_matches_project_fast_window_convention():
    closes = [100.0]
    for i in range(90):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.995))
    bars = pl.DataFrame({
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))],
        "close": closes,
    })

    result = volatility_from_bars(
        bars,
        annualization_days=252,
        vol_window=20,
    )

    expected_daily = (
        bars.with_columns(ret=pl.col("close").pct_change())
        .tail(20)["ret"]
        .std()
    )
    assert result["daily_return_vol"] == pytest.approx(expected_daily)
    assert result["annual_return_vol"] == pytest.approx(expected_daily * 252**0.5)
    assert result["history_rows"] == len(closes)


def test_cost_row_uses_half_spread_each_way_and_scales_by_dollar_vol():
    row = build_cost_risk_row(
        symbol="MES",
        signal_symbol="ES",
        contract_id="MESZ6",
        expiration="20261218",
        current_price=6000.0,
        multiplier=5.0,
        commission_per_side=0.61,
        annual_return_vol=0.20,
        annualization_days=252,
        history_rows=260,
        history_start=date(2025, 1, 1),
        history_end=date(2025, 12, 31),
        bid=5999.75,
        ask=6000.00,
    )

    assert row["notional_per_contract"] == pytest.approx(30_000.0)
    assert row["annual_dollar_vol_per_contract"] == pytest.approx(6_000.0)
    assert row["full_spread_points"] == pytest.approx(0.25)
    assert row["one_way_spread_cash"] == pytest.approx(0.625)
    assert row["round_trip_spread_cash"] == pytest.approx(1.25)
    assert row["one_way_total_cost"] == pytest.approx(1.235)
    assert row["round_trip_total_cost"] == pytest.approx(2.47)
    assert row["round_trip_cost_per_annual_dollar_vol"] == pytest.approx(2.47 / 6000.0)
    assert row["spread_quality"] == "live_snapshot"


def test_missing_bid_ask_is_unknown_not_zero_cost():
    row = build_cost_risk_row(
        symbol="MCL",
        signal_symbol="CL",
        contract_id="MCLX6",
        expiration="20261020",
        current_price=80.0,
        multiplier=100.0,
        commission_per_side=0.76,
        annual_return_vol=0.30,
        annualization_days=259,
        history_rows=250,
        history_start=date(2025, 1, 1),
        history_end=date(2025, 12, 31),
    )

    assert row["commission_round_trip"] == pytest.approx(1.52)
    assert row["full_spread_points"] is None
    assert row["round_trip_total_cost"] is None
    assert row["round_trip_cost_per_annual_dollar_vol"] is None
    assert row["spread_quality"] == "unknown_no_live_bid_ask"

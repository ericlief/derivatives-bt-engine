from datetime import date, timedelta
from types import ModuleType, SimpleNamespace
import sys

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_risk import (
    build_cost_risk_row,
    volatility_from_bars,
)
from derivatives_bt_engine.domain.instruments import resolve_active_months
from derivatives_bt_engine.live.tsmom_rebalance import _resolve_contract


def test_vxm_dated_contract_resolution_allows_every_month():
    assert resolve_active_months("VXM") == [
        "F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z",
    ]


def test_full_carver_mapping_passes_ib_multiplier_and_currency(monkeypatch):
    calls = []

    class FakeIBPySync:
        @staticmethod
        def future(symbol, **kwargs):
            calls.append((symbol, kwargs))
            return SimpleNamespace(
                symbol=symbol,
                lastTradeDateOrContractMonth=kwargs.get("expiration", ""),
            )

    fake_module = ModuleType("ib_tools.ibpysync")
    fake_module.IBPySync = FakeIBPySync
    monkeypatch.setitem(sys.modules, "ib_tools.ibpysync", fake_module)

    class FakeIB:
        def req_contract_details(self, contract):
            return [SimpleNamespace(contract=SimpleNamespace(
                lastTradeDateOrContractMonth="20261215",
                localSymbol="BREZ6",
                tradingClass="BRE",
                multiplier="100000",
            ))]

        def qualify_contracts(self, contract):
            return [contract]

    _resolve_contract(FakeIB(), {
        "symbol": "BRE",
        "ib_symbol": "BRE",
        "exchange": "CME",
        "ib_currency": "USD",
        "ib_multiplier": 100000.0,
        "multiplier": 100000.0,
        "expiry": "auto",
    }, min_days=7)

    assert len(calls) == 2
    assert all(call[1]["multiplier"] == "100000" for call in calls)
    assert all(call[1]["currency"] == "USD" for call in calls)


def test_volatility_uses_carver_fast_slow_point_vol_blend():
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
        fast_span=20,
        slow_years=10,
        slow_weight=0.3,
    )

    changes = bars["close"].diff()
    expected_fast = changes.ewm_std(span=20, adjust=True, min_samples=10)
    expected_slow = expected_fast.ewm_mean(span=2520, adjust=True, min_samples=1)
    expected_mixed = expected_fast * 0.7 + expected_slow * 0.3
    assert result["fast_point_vol"] == pytest.approx(expected_fast[-1])
    assert result["slow_point_vol"] == pytest.approx(expected_slow[-1])
    assert result["mixed_point_vol"] == pytest.approx(expected_mixed[-1])
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
        mixed_point_vol=6000.0 * 0.20 / 252**0.5,
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
        mixed_point_vol=80.0 * 0.30 / 259**0.5,
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

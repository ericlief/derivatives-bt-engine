from argparse import Namespace
from datetime import date, timedelta
import inspect
from types import ModuleType, SimpleNamespace
import sys

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_risk import (
    _emit_report,
    _history_volatility,
    _round_report_decimals,
    _ticker_values,
    build_cost_risk_row,
    diagnose_instrument,
    volatility_from_bars,
    parse_args,
)
from derivatives_bt_engine.domain.instruments import resolve_active_months
from derivatives_bt_engine.live.tsmom_rebalance import (
    _format_ib_multiplier,
    _resolve_contract,
)


def test_vxm_dated_contract_resolution_allows_every_month():
    assert resolve_active_months("VXM") == [
        "F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z",
    ]


def test_market_data_type_belongs_to_diagnostic_not_history_loader():
    diagnostic_parameters = inspect.signature(diagnose_instrument).parameters
    history_parameters = inspect.signature(_history_volatility).parameters

    assert "market_data_type" in diagnostic_parameters
    assert "market_data_type" not in history_parameters


def test_broad_audit_defaults_to_automatic_quote_fallback():
    assert parse_args([]).market_data_type == "auto"


def test_automatic_quote_fallback_tries_live_then_delayed_then_frozen():
    class FakeIB:
        def __init__(self):
            self.mode = None
            self.modes = []

        def set_market_data_type(self, mode):
            self.mode = mode
            self.modes.append(mode)

        def req_mkt_data(self, contract, generic_ticks=""):
            assert generic_ticks == ""
            if self.mode < 4:
                return SimpleNamespace(bid=None, ask=None, last=None, close=None)
            return SimpleNamespace(bid=5999.75, ask=6000.0, last=5999.75, close=5990.0)

        def sleep(self, seconds):
            pass

        def cancel_mkt_data(self, contract):
            pass

    ib = FakeIB()
    quote = _ticker_values(ib, SimpleNamespace(symbol="ES"), 0.0, "auto")

    assert ib.modes == [1, 3, 4]
    assert quote["mid"] == pytest.approx(5999.875)
    assert quote["market_data_type"] == "delayed-frozen"
    assert quote["market_data_attempts"] == "live,delayed,delayed-frozen"


def test_report_rounds_money_to_two_decimals_and_other_floats_to_four():
    report = pl.DataFrame({
        "notional_per_contract": [12345.6789],
        "commission_round_trip": [2.3456],
        "price": [1.234567],
        "fx_to_usd": [0.00660449],
        "annual_return_vol": [0.123456],
        "history_rows": [100],
    })

    rounded = _round_report_decimals(report)

    assert rounded["notional_per_contract"][0] == pytest.approx(12345.68)
    assert rounded["commission_round_trip"][0] == pytest.approx(2.35)
    assert rounded["price"][0] == pytest.approx(1.2346)
    assert rounded["fx_to_usd"][0] == pytest.approx(0.0066)
    assert rounded["annual_return_vol"][0] == pytest.approx(0.1235)
    assert rounded["history_rows"][0] == 100


def test_emitted_report_includes_generation_timestamp(capsys):
    emitted = _emit_report(
        pl.DataFrame({"symbol": ["JPY"], "price": [0.0067]}),
        Namespace(no_save=True),
    )

    assert "report_generated_at_utc" in emitted.columns
    timestamp = emitted["report_generated_at_utc"][0]
    assert timestamp.endswith("+00:00")
    assert "T" in timestamp
    capsys.readouterr()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12_500_000.0, "12500000"),
        (6_250_000.0, "6250000"),
        (50_000_000.0, "50000000"),
        (12.5, "12.5"),
        (0.01, "0.01"),
    ],
)
def test_ib_multiplier_never_uses_scientific_notation(value, expected):
    assert _format_ib_multiplier(value) == expected


def test_full_carver_mapping_passes_large_ib_multiplier_and_currency(monkeypatch):
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
                localSymbol="6JZ6",
                tradingClass="6J",
                multiplier="12500000",
            ))]

        def qualify_contracts(self, contract):
            return [contract]

    _resolve_contract(FakeIB(), {
        "symbol": "JPY",
        "ib_symbol": "JPY",
        "exchange": "CME",
        "ib_currency": "",
        "ib_multiplier": 12_500_000.0,
        "multiplier": 12_500_000.0,
        "expiry": "auto",
    }, min_days=7)

    assert len(calls) == 2
    assert all(call[1]["multiplier"] == "12500000" for call in calls)
    assert all(call[1]["currency"] == "" for call in calls)


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
        quote_quality="delayed_snapshot",
    )

    assert row["notional_per_contract"] == pytest.approx(30_000.0)
    assert row["annual_dollar_vol_per_contract"] == pytest.approx(6_000.0)
    assert row["full_spread_points"] == pytest.approx(0.25)
    assert row["one_way_spread_cash"] == pytest.approx(0.625)
    assert row["round_trip_spread_cash"] == pytest.approx(1.25)
    assert row["one_way_total_cost"] == pytest.approx(1.235)
    assert row["round_trip_total_cost"] == pytest.approx(2.47)
    assert row["round_trip_cost_per_annual_dollar_vol"] == pytest.approx(2.47 / 6000.0)
    assert row["spread_quality"] == "delayed_snapshot"


def test_return_vol_uses_history_price_not_current_execution_price():
    row = build_cost_risk_row(
        symbol="SP500",
        signal_symbol="ES",
        contract_id="ESZ6",
        expiration="20261218",
        current_price=7700.0,
        multiplier=50.0,
        commission_per_side=2.25,
        mixed_point_vol=30.0436603917,
        vol_reference_price=5304.25,
        annualization_days=256,
        history_rows=10559,
        history_start=date(1982, 9, 14),
        history_end=date(2024, 3, 28),
    )

    expected_daily = 30.0436603917 / 5304.25
    assert row["price"] == 7700.0
    assert row["vol_reference_price"] == 5304.25
    assert row["daily_return_vol"] == pytest.approx(expected_daily)
    assert row["annual_return_vol"] == pytest.approx(expected_daily * 16.0)


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
    assert row["spread_quality"] == "unknown_no_bid_ask"

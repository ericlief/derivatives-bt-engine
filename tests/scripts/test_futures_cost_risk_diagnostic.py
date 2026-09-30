from argparse import Namespace
from datetime import date, timedelta
import inspect
import statistics
from types import ModuleType, SimpleNamespace
import sys

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_risk import (
    _attach_affordability_ranks,
    _configured_cost_estimate,
    _blend_recent_and_historical_return_volatility,
    _emit_report,
    _ewmac_rule_performance,
    _historical_bid_ask_spread,
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
    assert "spread_duration" in diagnostic_parameters
    assert "spread_use_rth" in diagnostic_parameters
    assert "market_data_type" not in history_parameters
    assert "contract_details_timeout" in diagnostic_parameters


def test_broad_audit_defaults_to_automatic_quote_fallback():
    assert parse_args([]).market_data_type == "auto"


def test_broad_audit_defaults_to_reviewed_pool_ewmac_cost_baseline():
    args = parse_args([])

    assert args.cost_ewmac_fast_spans == (4, 8, 16, 32, 64)
    assert args.cost_ewmac_vol_span == 32
    assert args.cost_ewmac_vol_slow_years == 10
    assert args.cost_ewmac_vol_slow_weight == pytest.approx(0.3)
    assert args.cost_ewmac_vol_min_samples == 10
    assert args.rule_cost_limit_sr == pytest.approx(0.15)
    assert args.spread_duration == "30 D"
    assert not args.spread_all_hours
    assert not args.skip_ewmac_cost_baseline


def test_ewmac_performance_delays_forecast_and_annualizes_turnover():
    frame = pl.DataFrame({
        "ts_event": [date(2024, 1, day) for day in range(1, 6)],
        "ewmac_forecast": [None, 0.5, 0.5, -0.5, -0.5],
        "point_vol": [1.0] * 5,
        "pt_change_1d": [None, 1.0, 2.0, -1.0, 2.0],
    })

    metrics, pnl = _ewmac_rule_performance(frame)

    expected_pnl = [2.0, -1.0, -2.0]
    expected_sharpe = (
        statistics.mean(expected_pnl)
        / statistics.stdev(expected_pnl)
        * 256**0.5
    )
    assert pnl["risk_adjusted_pnl"].to_list() == expected_pnl
    assert metrics["ewmac_pre_cost_sharpe"] == pytest.approx(expected_sharpe)
    assert metrics["ewmac_forecast_turnover"] == pytest.approx(
        256 * (1.0 / 3) / 0.5
    )


def test_configured_cost_uses_pooled_turnover_and_reference_rolls():
    estimate = _configured_cost_estimate(
        {
            "symbol": "MICRO",
            "instrument_code": "MICRO",
            "representative_instrument": "FULL",
            "carver_spread_points": 0.25,
            "commission": 2.0,
            "per_trade_cost": 1.0,
            "percentage_cost": 0.0001,
        },
        {
            "multiplier": 50.0,
            "price": 100.0,
            "vol_reference_price": 100.0,
            "mixed_point_vol": 1.0,
            "fx_to_usd": 1.0,
            "annualization_days": 256,
            "annual_dollar_vol_per_contract": 800.0,
        },
        pooled_summaries={
            16: {
                "ewmac_pooled_forecast_turnover": 2.0,
                "ewmac_median_instrument_pre_cost_sharpe": 0.3,
            },
            64: {
                "ewmac_pooled_forecast_turnover": 0.2,
                "ewmac_median_instrument_pre_cost_sharpe": 0.3,
            },
        },
        strategy_metrics_by_rule={
            16: {
                "instrument_code": "FULL",
                "ewmac_pre_cost_sharpe": 0.5,
                "strategy_rolls_per_year": 4.0,
            },
            64: {
                "instrument_code": "FULL",
                "ewmac_pre_cost_sharpe": 0.4,
                "strategy_rolls_per_year": 4.0,
            },
        },
        rule_cost_limit_sr=0.15,
    )

    per_trade_sr = 14.5 / 800.0
    assert estimate["strategy_reference_instrument"] == "FULL"
    assert estimate["configured_one_way_cost_native"] == pytest.approx(14.5)
    assert estimate["configured_sr_cost_per_trade"] == pytest.approx(per_trade_sr)
    assert estimate["ewmac_16_64_forecast_annual_sr_cost"] == pytest.approx(
        per_trade_sr * 2.0
    )
    assert estimate["strategy_reference_roll_transactions_per_year"] == pytest.approx(8.0)
    assert estimate["ewmac_16_64_total_annual_sr_cost"] == pytest.approx(
        per_trade_sr * 10.0
    )
    assert not estimate["ewmac_16_64_cost_eligible"]
    assert estimate["ewmac_64_256_cost_eligible"]
    assert estimate["eligible_ewmac_rules"] == "64/256"


def test_configured_cost_prefers_ib_historical_half_spread():
    estimate = _configured_cost_estimate(
        {
            "symbol": "MES",
            "carver_spread_points": 0.25,
            "commission": 0.61,
        },
        {
            "price": 6_000.0,
            "multiplier": 5.0,
            "fx_to_usd": 1.0,
            "annual_dollar_vol_per_contract": 6_000.0,
            "selected_one_way_spread_points": 0.125,
            "selected_spread_source": "ib_dated_contract",
        },
        pooled_summaries=None,
        strategy_metrics_by_rule=None,
        rule_cost_limit_sr=0.15,
    )

    assert estimate["selected_one_way_spread_points"] == pytest.approx(0.125)
    assert estimate["configured_one_way_cost"] == pytest.approx(1.235)
    assert estimate["configured_cost_quality"] == "ib_historical_bid_ask"
    assert estimate["instrument_has_eligible_ewmac_rule"] is None


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


def test_report_uses_auditable_precision_for_money_rates_and_other_floats():
    report = pl.DataFrame({
        "notional_per_contract": [12345.6789],
        "one_way_total_cost": [2.3456],
        "price": [1.234567],
        "fx_to_usd": [0.00660449],
        "annual_return_vol": [0.123456],
        "history_rows": [100],
    })

    rounded = _round_report_decimals(report)

    assert rounded["notional_per_contract"][0] == pytest.approx(12345.68)
    assert rounded["one_way_total_cost"][0] == pytest.approx(2.35)
    assert rounded["price"][0] == pytest.approx(1.2346)
    assert rounded["fx_to_usd"][0] == pytest.approx(0.006604)
    assert rounded["annual_return_vol"][0] == pytest.approx(0.123456)
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
    assert row["one_way_total_cost"] == pytest.approx(1.235)
    assert row["one_way_cost_per_annual_dollar_vol"] == pytest.approx(1.235 / 6000.0)
    assert not any("round_trip" in column for column in row)
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
    assert row["price_scale_to_vol_reference"] == pytest.approx(7700.0 / 5304.25)
    assert row["annual_dollar_vol_per_contract"] == pytest.approx(
        expected_daily * 7700.0 * 50.0 * 16.0
    )


def test_current_return_vol_override_sets_current_dollar_risk():
    row = build_cost_risk_row(
        symbol="SP500",
        signal_symbol="ES",
        contract_id="ESZ6",
        expiration="20261218",
        current_price=7700.0,
        multiplier=50.0,
        commission_per_side=2.25,
        mixed_point_vol=30.0,
        vol_reference_price=5300.0,
        daily_return_vol_override=0.01,
        annualization_days=256,
        history_rows=10_000,
        history_start=date(1982, 1, 1),
        history_end=date(2024, 1, 1),
    )

    assert row["daily_return_vol"] == pytest.approx(0.01)
    assert row["current_mixed_point_vol"] == pytest.approx(77.0)
    assert row["annual_dollar_vol_per_contract"] == pytest.approx(61_600.0)


def test_recent_fast_return_vol_blends_with_carver_slow_anchor():
    blended = _blend_recent_and_historical_return_volatility(
        {"ib_recent_fast_return_vol": 0.02},
        {"slow_point_vol": 50.0, "reference_price": 5_000.0},
        slow_weight=0.3,
    )

    assert blended["carver_slow_return_vol"] == pytest.approx(0.01)
    assert blended["risk_daily_return_vol"] == pytest.approx(0.017)
    assert blended["risk_return_vol_source"] == "ib_dated_fast_carver_slow"


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

    assert row["commission_per_side"] == pytest.approx(0.76)
    assert row["full_spread_points"] is None
    assert row["one_way_total_cost"] is None
    assert row["one_way_cost_per_annual_dollar_vol"] is None
    assert row["spread_quality"] == "unknown_no_bid_ask"


def test_historical_spread_retries_with_exposed_duration():
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            if kwargs["bar_size"] == "1 min":
                raise TimeoutError("one-minute request timed out")
            return pl.DataFrame({
                "date": [
                    "2026-09-01 10:00:00",
                    "2026-09-01 10:02:00",
                ],
                "open": [6000.0, 6000.25],
                "close": [6000.25, 6000.75],
            })

    ib = FakeIB()
    result = _historical_bid_ask_spread(
        ib,
        SimpleNamespace(symbol="MES"),
        duration="30 D",
        use_rth=True,
        source="ib_dated_contract",
    )

    assert [call["bar_size"] for call in ib.calls] == ["1 min", "2 mins"]
    assert all(call["duration"] == "30 D" for call in ib.calls)
    assert result["ib_historical_spread_mean_points"] == pytest.approx(0.375)
    assert result["ib_historical_spread_observations"] == 2


def test_affordability_ranks_are_within_asset_class():
    report = pl.DataFrame({
        "symbol": ["MICRO", "FULL", "BOND"],
        "asset_class": ["Equity", "Equity", "Rates"],
        "notional_per_contract": [30_000.0, 300_000.0, 120_000.0],
        "annual_dollar_vol_per_contract": [6_000.0, 60_000.0, 8_000.0],
        "configured_sr_cost_per_trade": [0.002, 0.001, 0.003],
    })

    ranked = _attach_affordability_ranks(
        report,
        target_vol=0.20,
        min_contracts=4,
    )
    micro = ranked.filter(pl.col("symbol") == "MICRO").row(0, named=True)
    full = ranked.filter(pl.col("symbol") == "FULL").row(0, named=True)

    assert micro["affordability_rank_in_asset_class"] == 1
    assert full["cost_rank_in_asset_class"] == 1
    assert micro["min_capital_full_weight_idm1"] == pytest.approx(120_000.0)

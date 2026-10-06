from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
import inspect
from pathlib import Path
import statistics
from types import ModuleType, SimpleNamespace
import sys
from zoneinfo import ZoneInfo

import polars as pl
import pytest

import derivatives_bt_engine.data.futures_cost_risk as futures_cost_risk
from derivatives_bt_engine.data.futures_cost_risk import (
    _attach_affordability_ranks,
    _attach_phase2_prefilters,
    _configured_cost_estimate,
    _blend_recent_and_historical_return_volatility,
    _emit_report,
    _ewmac_rule_performance,
    _historical_bid_ask_spread,
    _history_volatility,
    _historical_request_timed_out,
    _ib_contract_currency,
    _latest_dated_mark,
    _roll_rate_audit,
    _round_report_decimals,
    _volume_stats_from_trade_bars,
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


def test_current_ib_fx_replaces_stale_reference_and_keeps_audit_fields():
    class FakeIB:
        def get_fx_to_usd(self, currency, **kwargs):
            assert currency == "EUR"
            return {
                "rate": 1.175,
                "queried_at_utc": "2026-10-01T15:00:00+00:00",
                "source": "ib_delayed_mid",
                "pair": "EURUSD",
                "market_data_type": "delayed",
            }

    selected = futures_cost_risk.load_current_fx_to_usd(
        FakeIB(),
        {"EUR"},
        {
            "EUR": {
                "rate": 1.077585,
                "asof": "2024-03-29",
                "source": "pysystemtrade_reference",
                "reference_rate": 1.077585,
                "reference_asof": "2024-03-29",
                "reference_source": "pysystemtrade_reference",
            }
        },
        market_data_type="auto",
        quote_wait_seconds=0.0,
    )

    assert selected["EUR"]["rate"] == pytest.approx(1.175)
    assert selected["EUR"]["source"] == "ib_delayed_mid"
    assert selected["EUR"]["pair"] == "EURUSD"
    assert selected["EUR"]["reference_rate"] == pytest.approx(1.077585)
    assert selected["EUR"]["reference_asof"] == "2024-03-29"


def test_unavailable_ib_fx_makes_reference_rate_the_selected_rate():
    class FakeIB:
        def get_fx_to_usd(self, currency, **kwargs):
            raise ValueError("USDCAD: IB cash-FX contract did not qualify")

    selected = futures_cost_risk.load_current_fx_to_usd(
        FakeIB(),
        {"CAD"},
        {
            "CAD": {
                "rate": 0.72,
                "asof": "2024-03-29",
                "source": "pysystemtrade_reference",
                "reference_rate": 0.72,
                "reference_asof": "2024-03-29",
                "reference_source": "pysystemtrade_reference",
            }
        },
        market_data_type="auto",
        quote_wait_seconds=0.0,
    )

    assert selected["CAD"]["rate"] == pytest.approx(0.72)
    assert selected["CAD"]["source"] == "pysystemtrade_reference_fallback"
    assert "USDCAD" in selected["CAD"]["error"]


def test_delayed_mark_uses_small_bid_ask_history_when_snapshot_is_unavailable():
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            return pl.DataFrame({
                "date": ["2026-09-30 17:00:00"],
                "open": [1_115.0],
                "close": [1_121.0],
            })

    ib = FakeIB()
    price, source = _latest_dated_mark(
        ib,
        SimpleNamespace(symbol="EOE"),
        market_data_type="delayed",
    )

    assert price == pytest.approx(1_118.0)
    assert source == "dated_contract_historical_bid_ask_mid"
    assert ib.calls == [{
        "duration": "1 D",
        "bar_size": "5 mins",
        "what_to_show": "BID_ASK",
        "use_rth": True,
    }]


@pytest.mark.parametrize(
    "message",
    [
        "1 min:request timeout",
        "1 min:reqHistoricalDataAsync timed out after 60s",
        "API historical data query cancelled: 55",
    ],
)
def test_historical_timeout_detection_covers_ib_wording(message):
    assert _historical_request_timed_out(message)


def test_ib_contract_currency_falls_back_to_qualified_or_native_currency():
    assert _ib_contract_currency(
        {"ib_currency": "", "currency": "EUR"},
        SimpleNamespace(currency="EUR"),
    ) == "EUR"
    assert _ib_contract_currency({"ib_currency": "", "currency": "EUR"}) == "EUR"


def test_retired_bsby_contract_is_rejected_before_ib_requests():
    with pytest.raises(RuntimeError, match="permanently delisted"):
        diagnose_instrument(
            object(),
            {
                "symbol": "BB3M",
                "instrument_code": "BB3M",
                "ib_symbol": "BSBY",
                "exchange": "CME",
                "currency": "USD",
                "multiplier": 2_500.0,
                "commission": 2.0,
            },
            duration="1 Y",
            spread_duration="30 D",
            spread_use_rth=True,
            min_days=7,
            contract_details_timeout=8.0,
            quote_wait_seconds=0.0,
            use_rth=False,
            vol_source="pysystemtrade",
            fast_span=32,
            slow_years=10,
            slow_weight=0.3,
            market_data_type="auto",
            pysystemtrade_provider=None,
            fx_by_currency={},
        )


def test_delayed_quote_runs_dated_vol_and_spread_after_live_354(monkeypatch):
    contract = SimpleNamespace(
        symbol="MES",
        conId=123,
        localSymbol="MESZ6",
        lastTradeDateOrContractMonth="20261218",
    )
    historical_vol = {
        "mixed_point_vol": 50.0,
        "fast_point_vol": 55.0,
        "slow_point_vol": 40.0,
        "reference_price": 5_000.0,
        "history_rows": 1_000,
        "history_start": date(2020, 1, 1),
        "history_end": date(2024, 1, 1),
        "vol_observations": 999,
        "slow_history_years": 10,
        "fast_vol_span": 32,
        "slow_vol_span": 2_520,
        "slow_vol_weight": 0.3,
        "zero_return_fraction": 0.0,
    }
    monkeypatch.setattr(
        futures_cost_risk,
        "_history_volatility",
        lambda *args, **kwargs: (252, historical_vol),
    )
    monkeypatch.setattr(
        futures_cost_risk,
        "_resolve_contract",
        lambda *args, **kwargs: contract,
    )
    history_calls = []

    def recent_history(*args, **kwargs):
        history_calls.append("recent_vol")
        return {
            "ib_recent_fast_return_vol": 0.02,
            "ib_recent_vol_observations": 100,
            "ib_recent_vol_start": date(2026, 1, 1),
            "ib_recent_vol_end": date(2026, 9, 30),
            "ib_recent_vol_duration": "1 Y",
        }

    def spread_history(*args, **kwargs):
        history_calls.append("spread")
        result = futures_cost_risk._skipped_historical_spread(
            duration="30 D",
            reason="",
        )
        result.update({
            "ib_historical_spread_source": "ib_dated_contract",
            "ib_historical_spread_bar_size": "1 min",
            "ib_historical_spread_observations": 100,
            "ib_historical_spread_mean_points": 12.0,
            "ib_historical_spread_median_points": 0.25,
        })
        return result

    monkeypatch.setattr(
        futures_cost_risk,
        "_recent_dated_return_volatility",
        recent_history,
    )
    monkeypatch.setattr(
        futures_cost_risk,
        "_historical_bid_ask_spread",
        spread_history,
    )

    class FakeIB:
        def __init__(self):
            self.market_data_modes = []

        def get_quote(self, *args, **kwargs):
            return {
                "bid": 5_999.75,
                "ask": 6_000.0,
                "last": 5_999.75,
                "close": 5_990.0,
                "mid": 5_999.875,
                "market_data_type": "delayed",
                "market_data_attempts": "live,delayed",
                "error_codes": [354],
                "selected_error_codes": [],
            }

        @staticmethod
        def quote_allows_historical(quote):
            return True

        def select_historical_market_data_type(self, quote):
            self.market_data_modes.append(3)
            return "delayed"

    ib = FakeIB()

    row = diagnose_instrument(
        ib,
        {
            "symbol": "MES",
            "signal_symbol": "ES",
            "exchange": "CME",
            "currency": "USD",
            "multiplier": 5.0,
            "commission": 0.61,
            "carver_spread_points": 0.25,
        },
        duration="1 Y",
        spread_duration="30 D",
        spread_use_rth=True,
        min_days=7,
        contract_details_timeout=8.0,
        quote_wait_seconds=0.0,
        use_rth=False,
        vol_source="pysystemtrade",
        fast_span=32,
        slow_years=10,
        slow_weight=0.3,
        market_data_type="auto",
        pysystemtrade_provider=None,
        fx_by_currency={"USD": {"rate": 1.0, "asof": date(2026, 9, 30)}},
    )

    assert row["ib_historical_requests_allowed"]
    assert row["ib_historical_market_data_type"] == "delayed"
    assert row["quote_error_codes"] == "354"
    assert row["quote_selected_error_codes"] == ""
    assert row["risk_return_vol_source"] == "ib_dated_fast_carver_slow"
    assert row["selected_spread_source"] == "ib_dated_contract"
    assert row["selected_one_way_spread_points"] == pytest.approx(0.125)
    assert history_calls == ["recent_vol", "spread"]
    assert ib.market_data_modes == [3]


def test_broad_audit_defaults_to_reviewed_pool_ewmac_cost_baseline():
    args = parse_args([])

    assert args.cost_ewmac_fast_spans == (4, 8, 16, 32, 64)
    assert args.cost_ewmac_vol_span == 32
    assert args.cost_ewmac_vol_slow_years == 10
    assert args.cost_ewmac_vol_slow_weight == pytest.approx(0.3)
    assert args.cost_ewmac_vol_min_samples == 10
    assert args.rule_cost_limit_sr == pytest.approx(0.15)
    assert args.affordability_capital_usd == pytest.approx(100_000.0)
    assert args.affordability_idm == pytest.approx(1.0)
    assert args.affordability_min_contracts == 4
    assert args.affordability_min_main_instruments == 15
    assert args.affordability_main_asset_classes == (
        "Equity", "Ags", "Vol", "OilGas", "FX", "Metals", "Bond",
    )
    assert args.volume_lookback_days == 20
    assert args.volume_duration == "2 M"
    assert args.instrument_cost_limit_sr == pytest.approx(0.01)
    assert args.liquidity_ann_trades == pytest.approx(25.0)
    assert args.liquidity_business_days == 250
    assert args.max_market_volume_pct == pytest.approx(1.0)
    assert args.min_daily_volume == pytest.approx(100.0)
    assert args.spread_duration == "5 D"
    assert not args.spread_all_hours
    assert not args.skip_ewmac_cost_baseline


def test_short_selection_aliases_are_adjustable():
    args = parse_args(["--initial-capital-usd", "500000", "--target-vol", "0.25"])

    assert args.affordability_capital_usd == pytest.approx(500_000.0)
    assert args.affordability_target_vol == pytest.approx(0.25)


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


def test_roll_rate_audit_detects_historical_policy_change():
    rows = [
        {"trade_date": date(2010, 1, 4), "is_roll": False},
        {"trade_date": date(2024, 3, 28), "is_roll": False},
    ]
    for year in range(2011, 2016):
        for month in (9, 12):
            rows.append({"trade_date": date(year, month, 1), "is_roll": True})
    for year in range(2016, 2024):
        for month in (3, 5, 7, 9, 12):
            rows.append({"trade_date": date(year, month, 1), "is_roll": True})

    audit = _roll_rate_audit(
        pl.DataFrame(rows).sort("trade_date"),
        hold_roll_cycle="HKNUZ",
    )

    assert audit["strategy_rolls"] == 50
    assert audit["strategy_configured_rolls_per_year"] == pytest.approx(5.0)
    assert audit["strategy_observed_rolls_per_year_recent"] == pytest.approx(5.0)
    assert audit["strategy_recent_roll_start_year"] == 2019
    assert audit["strategy_recent_roll_end_year"] == 2023
    assert audit["strategy_recent_roll_years"] == 5
    assert audit["strategy_roll_rate_audit"] == "historical_policy_change"


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
        rule_cost_limit_sr=0.10,
    )

    per_trade_sr = 14.5 / 800.0
    per_roll_sr = 16.5 / 800.0
    assert estimate["strategy_reference_instrument"] == "FULL"
    assert estimate["configured_one_way_cost_native"] == pytest.approx(14.5)
    assert estimate["configured_trade_sr"] == pytest.approx(per_trade_sr)
    assert estimate["configured_roll_sr"] == pytest.approx(per_roll_sr)
    assert estimate["ewmac_16_64_trade_ann_cost_sr"] == pytest.approx(
        per_trade_sr * 2.0
    )
    assert estimate["ewmac_16_64_ann_trades"] == pytest.approx(2.0)
    assert estimate["ewmac_16_64_ann_rolls"] == pytest.approx(4.0)
    assert estimate["ewmac_16_64_roll_ann_cost_sr"] == pytest.approx(
        per_roll_sr * 4.0
    )
    assert estimate["ewmac_16_64_tot_ann_cost_sr"] == pytest.approx(
        per_trade_sr * 2.0 + per_roll_sr * 4.0
    )
    assert not any("transactions" in key or "_tx" in key for key in estimate)
    assert not estimate["ewmac_16_64_cost_eligible"]
    assert estimate["ewmac_64_256_cost_eligible"]
    assert estimate["eligible_ewmac_rules"] == "64/256"


def test_configured_cost_uses_current_cycle_not_full_history_roll_rate():
    estimate = _configured_cost_estimate(
        {
            "symbol": "MZC",
            "instrument_code": "MZC",
            "representative_instrument": "CORN_mini",
            "carver_spread_points": 0.5,
            "commission": 0.76,
            "per_trade_cost": 0.0,
            "percentage_cost": 0.0,
        },
        {
            "multiplier": 5.0,
            "price": 497.0,
            "fx_to_usd": 1.0,
            "annual_dollar_vol_per_contract": 602.67,
        },
        pooled_summaries={
            64: {
                "ewmac_pooled_forecast_turnover": 5.5685,
                "ewmac_median_instrument_pre_cost_sharpe": 0.3,
            },
        },
        strategy_metrics_by_rule={
            64: {
                "instrument_code": "CORN_mini",
                "strategy_rolls": 132,
                "strategy_rolls_per_year": 2.4622,
                "strategy_observed_rolls_per_year_full": 2.4622,
                "strategy_observed_rolls_per_year_recent": 5.0,
                "strategy_hold_roll_cycle": "HKNUZ",
                "strategy_configured_rolls_per_year": 5.0,
                "strategy_roll_rate_audit": "historical_policy_change",
            },
        },
        rule_cost_limit_sr=0.15,
    )

    assert estimate[
        "strategy_reference_observed_rolls_per_year_full"
    ] == pytest.approx(2.4622)
    assert estimate[
        "strategy_reference_configured_rolls_per_year"
    ] == pytest.approx(5.0)
    assert estimate["strategy_reference_ann_rolls"] == pytest.approx(
        5.0
    )
    assert estimate["strategy_reference_roll_rate_source"] == (
        "configured_hold_cycle"
    )
    assert estimate["ewmac_64_256_ann_trades"] == pytest.approx(5.5685)
    assert estimate["ewmac_64_256_ann_rolls"] == pytest.approx(5.0)


def test_roll_audit_survives_missing_execution_cost_inputs():
    estimate = _configured_cost_estimate(
        {
            "symbol": "MZC",
            "instrument_code": "MZC",
            "representative_instrument": "CORN_mini",
            "carver_spread_points": None,
        },
        {
            "multiplier": 5.0,
            "price": 497.0,
            "fx_to_usd": 1.0,
            "annual_dollar_vol_per_contract": 602.67,
        },
        pooled_summaries={64: {"ewmac_pooled_forecast_turnover": 5.5685}},
        strategy_metrics_by_rule={
            64: {
                "strategy_observed_rolls_per_year_full": 2.4622,
                "strategy_observed_rolls_per_year_recent": 5.0,
                "strategy_configured_rolls_per_year": 5.0,
                "strategy_roll_rate_audit": "historical_policy_change",
            },
        },
        rule_cost_limit_sr=0.15,
    )

    assert estimate["configured_cost_quality"] == "incomplete_static_inputs"
    assert estimate["strategy_reference_ann_rolls"] == pytest.approx(
        5.0
    )
    assert estimate["strategy_reference_roll_rate_audit"] == (
        "historical_policy_change"
    )


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


def test_configured_cost_labels_snapshot_spread_separately_from_history():
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
            "selected_spread_source": "ib_snapshot",
        },
        pooled_summaries=None,
        strategy_metrics_by_rule=None,
        rule_cost_limit_sr=0.15,
    )

    assert estimate["configured_cost_quality"] == "ib_snapshot_bid_ask"


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


def test_public_report_leads_with_primary_values_and_normalizes_spreads():
    report = futures_cost_risk._public_report_schema(pl.DataFrame({
        "symbol": ["AEX_mini"],
        "price": [1103.975],
        "fx_to_usd": [1.175],
        "cur_fx_source": ["ib_delayed_mid"],
        "current_mixed_point_vol": [6.4668],
        "daily_return_vol": [0.005858],
        "annual_dollar_vol_per_contract": [2432.0],
        "selected_one_way_spread_points": [3.177],
        "configured_one_way_cost": [75.0],
        "configured_trade_sr": [0.03],
        "configured_roll_sr": [0.06],
        "full_spread_points": [3.25],
        "ib_historical_spread_mean_points": [6.354],
        "ib_historical_spread_median_points": [6.6],
        "ib_historical_spread_p90_points": [9.75],
        "mixed_point_vol": [5.847],
        "vol_reference_price": [883.25],
        "carver_configured_one_way_spread_points": [2.4],
        "strategy_reference_configured_rolls_per_year": [12.0],
        "strategy_reference_ann_rolls": [12.0],
        "strategy_reference_roll_rate_source": ["configured_hold_cycle"],
        "strategy_reference_roll_rate_audit": [
            "consistent_with_configured_cycle"
        ],
        "ewmac_4_16_ann_trades": [56.0],
        "ewmac_4_16_ann_rolls": [12.0],
        "ewmac_4_16_trade_ann_cost_sr": [1.68],
        "ewmac_4_16_roll_ann_cost_sr": [0.72],
        "ewmac_4_16_tot_ann_cost_sr": [2.4],
        "ewmac_4_16_reference_pre_cost_sharpe": [0.1],
    }))

    assert report["spread_pts"][0] == pytest.approx(3.177)
    assert report["snap_spread_pts"][0] == pytest.approx(1.625)
    assert report["ib_hspread_mean_pts"][0] == pytest.approx(3.177)
    assert report["ib_hspread_median_pts"][0] == pytest.approx(3.3)
    assert report["ib_hspread_p90_pts"][0] == pytest.approx(4.875)
    assert report["ref_spread_pts"][0] == pytest.approx(2.4)
    assert "cost_usd" in report.columns
    assert "ann_dvol" in report.columns
    assert "ewmac_4_16_ann_trades" in report.columns
    assert "ewmac_4_16_ann_rolls" in report.columns
    assert "ewmac_4_16_tot_ann_cost_sr" in report.columns
    assert "ewmac_4_16_ref_pre_cost_sr" in report.columns
    assert "trade_sr" in report.columns
    assert "roll_sr" in report.columns
    assert not any("tx" in column for column in report.columns)
    assert not any("total_transactions" in column for column in report.columns)
    assert report["ref_strat_cfg_rolls_per_year"][0] == pytest.approx(
        12.0
    )
    assert report["ref_ann_rolls"][0] == pytest.approx(
        12.0
    )
    assert report["ref_strat_roll_rate_src"][0] == "configured_hold_cycle"
    assert report.columns.index("spread_pts") < report.columns.index(
        "snap_spread_pts"
    )
    assert report.columns.index("daily_pt_vol") < report.columns.index(
        "ref_mixed_pt_vol"
    )


def test_emitted_report_includes_generation_timestamp(capsys):
    emitted = _emit_report(
        pl.DataFrame({"symbol": ["JPY"], "price": [0.0067]}),
        Namespace(no_save=True),
    )

    assert "report_ts_ct" in emitted.columns
    timestamp = emitted["report_ts_ct"][0]
    assert timestamp.endswith(("-05:00", "-06:00"))
    assert "T" in timestamp
    capsys.readouterr()


def test_timestamped_output_path_uses_chicago_and_preserves_csv_extension():
    generated_at = datetime(2026, 10, 1, 14, 54, 18, tzinfo=timezone.utc)

    output = futures_cost_risk._timestamped_output_path(
        Path("pysystemtrade_cost_phase1_ib.csv"), generated_at
    )

    assert output == Path("pysystemtrade_cost_phase1_ib_20261001_095418.csv")


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


def test_configured_cost_uses_annual_dollar_volatility():
    estimate = _configured_cost_estimate(
        {
            "symbol": "CORN",
            "carver_spread_points": 0.125,
            "commission": 2.97,
        },
        {
            "price": 497.25,
            "multiplier": 50.0,
            "fx_to_usd": 1.0,
            "annual_dollar_vol_per_contract": 6_000.0,
        },
        pooled_summaries=None,
        strategy_metrics_by_rule=None,
        rule_cost_limit_sr=0.15,
    )

    assert estimate["configured_one_way_cost"] == pytest.approx(9.22)
    assert estimate["configured_trade_sr"] == pytest.approx(
        9.22 / 6_000.0
    )
    assert estimate["configured_roll_sr"] == pytest.approx(
        12.19 / 6_000.0
    )


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
                raise ValueError("one-minute history was empty")
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
        bar_sizes=("1 min", "2 mins"),
    )

    assert [call["bar_size"] for call in ib.calls] == ["1 min", "2 mins"]
    assert all(call["duration"] == "30 D" for call in ib.calls)
    assert result["ib_historical_spread_mean_points"] == pytest.approx(0.375)
    assert result["ib_historical_spread_observations"] == 2


def test_historical_spread_timeout_does_not_cascade_to_more_requests():
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("reqHistoricalDataAsync timed out after 60s")

    ib = FakeIB()
    result = _historical_bid_ask_spread(
        ib,
        SimpleNamespace(symbol="MES"),
        duration="30 D",
        use_rth=True,
        source="ib_dated_contract",
    )

    assert [call["bar_size"] for call in ib.calls] == ["15 mins"]
    assert result["ib_historical_spread_mean_points"] is None
    assert "timed out" in result["ib_historical_spread_failures"]


def test_historical_spread_empty_after_ib_insync_timeout_does_not_cascade(
    monkeypatch,
):
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            return pl.DataFrame()

    clock = iter((100.0, 160.0))
    monkeypatch.setattr(
        futures_cost_risk.time,
        "monotonic",
        lambda: next(clock),
    )
    ib = FakeIB()
    result = _historical_bid_ask_spread(
        ib,
        SimpleNamespace(symbol="GBM"),
        duration="30 D",
        use_rth=True,
        source="ib_dated_contract",
    )

    assert [call["bar_size"] for call in ib.calls] == ["15 mins"]
    assert result["ib_historical_spread_mean_points"] is None
    assert "returned empty after 60.0s" in result[
        "ib_historical_spread_failures"
    ]
    assert "timeout" in result["ib_historical_spread_failures"]


def test_delayed_spread_uses_full_window_fifteen_minute_first():
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            return pl.DataFrame({
                "date": ["2026-09-01 10:00:00"],
                "open": [100.0],
                "close": [100.25],
            })

    ib = FakeIB()
    result = _historical_bid_ask_spread(
        ib,
        SimpleNamespace(symbol="EOE"),
        duration="30 D",
        use_rth=True,
        source="ib_dated_contract",
        market_data_type="delayed",
    )

    assert [
        (call["bar_size"], call["duration"])
        for call in ib.calls
    ] == [("15 mins", "30 D")]
    assert result["ib_historical_spread_bar_size"] == "15 mins"
    assert result["ib_historical_spread_duration"] == "30 D"


def test_live_spread_uses_one_full_window_fifteen_minute_request():
    class FakeIB:
        def __init__(self):
            self.calls = []

        def get_historical_bars(self, contract, **kwargs):
            self.calls.append(kwargs)
            return pl.DataFrame({
                "date": ["2026-09-01 10:00:00"],
                "open": [7800.0],
                "close": [7800.25],
            })

    ib = FakeIB()
    result = _historical_bid_ask_spread(
        ib,
        SimpleNamespace(symbol="MES"),
        duration="30 D",
        use_rth=True,
        source="ib_dated_contract",
        market_data_type="live",
    )

    assert [
        (call["bar_size"], call["duration"])
        for call in ib.calls
    ] == [("15 mins", "30 D")]
    assert result["ib_historical_spread_mean_points"] == pytest.approx(0.25)


def test_spread_report_timestamps_normalize_mixed_exchange_zones_to_chicago():
    def stats(zone: str):
        return futures_cost_risk._spread_stats_from_bid_ask_bars(
            pl.DataFrame({
                "date": [datetime(2026, 9, 30, 15, 45, tzinfo=ZoneInfo(zone))],
                "open": [7800.0],
                "close": [7800.25],
            }),
            duration="30 D",
            bar_size="15 mins",
            source="ib_dated_contract",
        )

    report = pl.DataFrame([
        stats("Europe/Amsterdam"),
        stats("America/New_York"),
    ], infer_schema_length=None)

    assert report.schema["ib_historical_spread_start"] == pl.String
    assert report.get_column("ib_historical_spread_start").to_list() == [
        "2026-09-30T08:45:00-05:00",
        "2026-09-30T14:45:00-05:00",
    ]


def test_affordability_ranks_are_within_asset_class():
    report = pl.DataFrame({
        "symbol": ["MICRO", "FULL", "BOND"],
        "asset_class": ["Equity", "Equity", "Rates"],
        "notional_per_contract": [30_000.0, 300_000.0, 120_000.0],
        "annual_dollar_vol_per_contract": [6_000.0, 60_000.0, 8_000.0],
        "configured_trade_sr": [0.002, 0.001, 0.003],
    })

    ranked = _attach_affordability_ranks(
        report,
        target_vol=0.20,
        min_contracts=4,
    )
    micro = ranked.filter(pl.col("symbol") == "MICRO").row(0, named=True)
    full = ranked.filter(pl.col("symbol") == "FULL").row(0, named=True)
    bond = ranked.filter(pl.col("symbol") == "BOND").row(0, named=True)

    assert micro["affordability_rank_in_asset_class"] == 1
    assert full["cost_rank_in_asset_class"] == 1
    assert micro["cost_rank_in_asset_class"] == 2
    assert micro["min_capital_full_weight_idm1"] == pytest.approx(120_000.0)
    assert micro["min_capital_equal_weight"] == pytest.approx(1_800_000.0)
    assert micro["affordability_cluster_role"] == "main"
    assert micro["counts_toward_main_instrument_minimum"]
    assert micro["equal_weight_dvol_budget"] == pytest.approx(20_000.0 / 15)
    assert micro["equal_weight_average_contracts"] == pytest.approx(2.0 / 9.0)
    assert not micro["equal_weight_meets_min_contracts"]
    assert not micro["main_instrument_affordable_for_scenario"]
    assert bond["affordability_cluster_role"] == "special"
    assert not bond["counts_toward_main_instrument_minimum"]
    assert not bond["main_instrument_affordable_for_scenario"]


def test_affordability_scenario_splits_risk_across_main_instruments():
    report = pl.DataFrame({
        "symbol": ["MICRO"],
        "asset_class": ["Equity"],
        "notional_per_contract": [10_000.0],
        "annual_dollar_vol_per_contract": [2_500.0],
        "configured_trade_sr": [0.002],
    })

    ranked = _attach_affordability_ranks(
        report,
        target_vol=0.20,
        min_contracts=4,
        capital_usd=100_000.0,
        idm=1.0,
        min_main_instruments=2,
    )
    row = ranked.row(0, named=True)

    assert row["equal_weight_dvol_budget"] == pytest.approx(10_000.0)
    assert row["equal_weight_average_contracts"] == pytest.approx(4.0)
    assert row["equal_weight_meets_min_contracts"]
    assert row["main_instrument_affordable_for_scenario"]
    assert row["min_capital_equal_weight"] == pytest.approx(100_000.0)


def test_volume_stats_average_latest_twenty_daily_contract_counts():
    bars = pl.DataFrame({
        "date": [date(2026, 1, 1) + timedelta(days=day) for day in range(25)],
        "volume": [float(day) for day in range(1, 26)],
    })

    stats = _volume_stats_from_trade_bars(
        bars,
        lookback_days=20,
        source="ib_dated_contract",
    )

    assert stats["avg_daily_volume_contracts"] == pytest.approx(15.5)
    assert stats["volume_observations"] == 20
    assert stats["volume_start"] == date(2026, 1, 6)
    assert stats["volume_end"] == date(2026, 1, 25)


def test_phase2_prefilter_uses_relative_market_risk_volume():
    report = pl.DataFrame({
        "symbol": ["GE"],
        "price": [96.0],
        "fx_to_usd": [1.0],
        "multiplier": [2_500.0],
        "annual_dollar_vol_per_contract": [1_100.0],
        "configured_trade_sr": [0.005],
        "avg_daily_volume_contracts": [100_000.0],
        "instrument_has_eligible_ewmac_rule": [True],
        "execution_eligible": [True],
        "ib_availability": ["contract_qualified"],
    })

    filtered = _attach_phase2_prefilters(
        report,
        initial_capital_usd=500_000.0,
        target_vol=0.25,
        instrument_cost_limit_sr=0.01,
        liquidity_ann_trades=25.0,
        liquidity_business_days=250,
        max_market_volume_pct=1.0,
        min_daily_volume_contracts=100.0,
    )
    selected = filtered.row(0, named=True)

    assert selected["risk_traded_usd_day"] == pytest.approx(12_500.0)
    assert selected["mkt_risk_vol_usd_day"] == pytest.approx(110_000_000.0)
    assert selected["pct_mkt_volume"] == pytest.approx(0.011363636)
    assert selected["phase2_eligible"]
    assert selected["selection_bucket"] == "add_first"
    assert selected["phase2_exclusion"] == ""
    public = futures_cost_risk._public_report_schema(filtered)
    assert public["avg_daily_volume"][0] == pytest.approx(100_000.0)
    assert public["ann_dvol"][0] == pytest.approx(1_100.0)
    assert public["init_cap_usd"][0] == pytest.approx(500_000.0)
    assert "liq_ann_dvol" not in public.columns
    assert not any(name.startswith("selected_") for name in public.columns)


def test_phase2_prefilter_requires_more_than_hundred_contracts():
    report = pl.DataFrame({
        "symbol": ["THIN"],
        "price": [100.0],
        "fx_to_usd": [1.0],
        "multiplier": [1_000.0],
        "annual_dollar_vol_per_contract": [20_000.0],
        "configured_trade_sr": [0.005],
        "avg_daily_volume_contracts": [100.0],
        "instrument_has_eligible_ewmac_rule": [True],
        "execution_eligible": [True],
        "ib_availability": ["contract_qualified"],
    })

    selected = _attach_phase2_prefilters(
        report,
        initial_capital_usd=100_000.0,
        target_vol=0.20,
        instrument_cost_limit_sr=0.01,
        liquidity_ann_trades=25.0,
        liquidity_business_days=250,
        max_market_volume_pct=1.0,
        min_daily_volume_contracts=100.0,
    ).row(0, named=True)

    assert selected["pct_mkt_volume"] < 1.0
    assert not selected["contract_volume_eligible"]
    assert selected["risk_volume_eligible"]
    assert not selected["phase2_eligible"]
    assert selected["selection_bucket"] == "add_later"
    assert selected["phase2_exclusion"] == "contract_volume"


def test_phase2_prefilter_buckets_missing_subscription_data_first():
    report = pl.DataFrame({
        "symbol": ["NO_DATA"],
        "price": [None],
        "fx_to_usd": [1.0],
        "multiplier": [10.0],
        "configured_trade_sr": [None],
        "avg_daily_volume_contracts": [None],
        "annual_dollar_vol_per_contract": [None],
        "instrument_has_eligible_ewmac_rule": [True],
        "execution_eligible": [True],
        "ib_availability": ["unavailable_or_unverified"],
    })

    selected = _attach_phase2_prefilters(
        report,
        initial_capital_usd=100_000.0,
        target_vol=0.20,
        instrument_cost_limit_sr=0.01,
        liquidity_ann_trades=25.0,
        liquidity_business_days=250,
        max_market_volume_pct=1.0,
        min_daily_volume_contracts=100.0,
    ).row(0, named=True)

    assert not selected["data_eligible"]
    assert selected["selection_bucket"] == "dont_add_no_data"
    assert "data_unavailable" in selected["phase2_exclusion"]

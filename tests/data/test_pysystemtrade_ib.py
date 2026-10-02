import pytest

from derivatives_bt_engine.data.pysystemtrade_ib import (
    add_local_execution_overlays,
    load_pysystemtrade_ib_mapping,
)
from derivatives_bt_engine.domain.instruments import resolve_active_months


def test_bundled_carver_ib_mapping_has_expected_coverage_and_point_values():
    mapping = load_pysystemtrade_ib_mapping()

    assert mapping.height == 584
    assert mapping["instrument_code"].n_unique() == 584
    corn = mapping.filter(mapping["instrument_code"] == "CORN").row(0, named=True)
    assert corn["ib_symbol"] == "ZC"
    assert corn["ib_exchange"] == "CBOT"
    assert corn["ib_multiplier"] == pytest.approx(5000)
    assert corn["price_magnifier"] == pytest.approx(100)
    assert corn["ib_effective_point_value"] == pytest.approx(50)


def test_na_ib_currency_becomes_unspecified_not_literal_na():
    mapping = load_pysystemtrade_ib_mapping()
    corn = mapping.filter(mapping["instrument_code"] == "CORN").row(0, named=True)
    assert corn["ib_currency"] == ""


def test_local_grain_micros_borrow_matching_carver_histories():
    parent_codes = {
        "CORN_mini", "SOYBEAN_mini", "SOYMEAL", "SOYOIL", "WHEAT_mini",
    }
    parents = [
        {
            "instrument_code": code,
            "symbol": code,
            "description": code,
            "currency": "USD",
            "carver_spread_points": 0.14,
        }
        for code in parent_codes
    ]

    rows = add_local_execution_overlays(parents)
    overlays = {
        row["symbol"]: row
        for row in rows
        if row.get("mapping_status") == "local_execution_overlay"
    }

    assert set(overlays) == {"MZC", "MZL", "MZM", "MZS", "MZW"}
    micro = overlays["MZC"]
    assert micro["instrument_code"] == "CORN_mini"
    assert micro["history_instrument_code"] == "CORN_mini"
    assert micro["signal_symbol"] == "ZC"
    assert micro["ib_symbol"] == "MZC"
    assert micro["ib_multiplier"] == 500
    assert micro["price_magnifier"] == 100
    assert micro["multiplier"] == 5
    assert micro["commission"] == pytest.approx(0.76)
    assert micro["carver_spread_points"] is None
    assert micro["representative_instrument"] == "CORN_mini"
    assert micro["include_default_pool"] is False
    assert resolve_active_months("MZC") == ["H", "K", "N", "U", "Z"]

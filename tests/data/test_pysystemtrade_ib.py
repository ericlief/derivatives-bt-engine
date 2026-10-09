import pytest

from derivatives_bt_engine.data.pysystemtrade_ib import (
    add_local_execution_overlays,
    load_pysystemtrade_ib_mapping,
)
from derivatives_bt_engine.calculations.instruments import (
    resolve_active_months,
    resolve_execution_eligibility,
)


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


def test_ibkr_us_execution_restriction_uses_executable_ib_symbol():
    restricted = resolve_execution_eligibility("SGX", ib_symbol="STI")
    ordinary = resolve_execution_eligibility("SP500", ib_symbol="ES")

    assert restricted == {
        "execution_profile": "ibkr_us",
        "execution_eligible": False,
        "execution_restriction_reason": "ibkr_us_product_restriction",
    }
    assert ordinary == {
        "execution_profile": "ibkr_us",
        "execution_eligible": True,
        "execution_restriction_reason": None,
    }


def test_local_micros_borrow_matching_carver_histories():
    parent_codes = {
        "CORN_mini",
        "SOYBEAN_mini",
        "SOYMEAL",
        "SOYOIL",
        "US10U",
        "US30",
        "WHEAT_mini",
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

    assert set(overlays) == {
        "MTN",
        "MWN",
        "MZC",
        "MZL",
        "MZM",
        "MZS",
        "MZW",
    }
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

    mtn = overlays["MTN"]
    assert mtn["instrument_code"] == "US10U"
    assert mtn["history_instrument_code"] == "US10U"
    assert mtn["signal_symbol"] == "TN"
    assert mtn["ib_symbol"] == "MTN"
    assert mtn["multiplier"] == 100
    assert mtn["active_months"] == ["H", "M", "U", "Z"]
    assert resolve_active_months("MTN") == ["H", "M", "U", "Z"]

    mwn = overlays["MWN"]
    assert mwn["instrument_code"] == "US30"
    assert mwn["history_instrument_code"] == "US30"
    assert mwn["signal_symbol"] == "UB"
    assert mwn["ib_symbol"] == "MWN"
    assert mwn["multiplier"] == 100
    assert mwn["active_months"] == ["H", "M", "U", "Z"]
    assert resolve_active_months("MWN") == ["H", "M", "U", "Z"]

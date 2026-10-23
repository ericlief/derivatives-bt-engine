import pytest
import polars as pl

from derivatives_bt_engine.data.pysystemtrade_ib import (
    _assign_executable_symbols,
    add_local_execution_overlays,
    load_pysystemtrade_ib_mapping,
)
from derivatives_bt_engine.calculations.instruments import (
    resolve_active_months,
)
from derivatives_bt_engine.data.execution_profiles import (
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


def test_executable_symbols_use_unique_ib_roots_and_explicit_collision_aliases():
    mapping = load_pysystemtrade_ib_mapping()
    instruments = mapping.select("instrument_code", "ib_symbol")
    assigned = _assign_executable_symbols(instruments)
    assert assigned["symbol"].n_unique() == assigned.height

    by_code = {
        row["instrument_code"]: row["symbol"]
        for row in assigned.iter_rows(named=True)
    }
    assert by_code["GOLD"] == "GC"
    assert by_code["GOLD_micro"] == "MGC"
    assert by_code["AUD"] == "AUD"
    assert by_code["AUD-ICE"] == "AUD-ICEUS"
    assert by_code["MIB_micro"] == "MIB-micro"


def test_executable_symbol_identity_keeps_pst_code_as_second_report_key():
    from derivatives_bt_engine.pipelines.market_screen import (
        _public_report_schema,
    )
    from derivatives_bt_engine.reports.formatting import (
        rename_public_report_columns,
    )

    report = rename_public_report_columns(
        pl.DataFrame({
            "symbol": ["MGC"],
            "pysystemtrade_instrument_code": ["GOLD_micro"],
        })
    )
    assert report.columns == ["symbol", "pst_instr_code"]
    assert report.row(0) == ("MGC", "GOLD_micro")

    screen = _public_report_schema(pl.DataFrame({
        "symbol": ["MGC"],
        "pysystemtrade_instrument_code": ["GOLD_micro"],
        "signal_symbol": ["GC"],
    }))
    assert screen.columns[:3] == [
        "symbol", "pst_instr_code", "signal_symbol"
    ]


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


def test_ibkr_us_execution_restriction_excludes_unavailable_exchanges():
    """Known unavailable venues remain research-only for the US profile."""
    for exchange in ("CDE", "FTA", "IPE", "MONEP"):
        restricted = resolve_execution_eligibility(
            "TEST",
            ib_symbol="TEST",
            exchange=exchange,
        )
        assert not restricted["execution_eligible"]
        assert (
            restricted["execution_restriction_reason"]
            == "ibkr_us_exchange_no_trading_permission"
        )

    ordinary = resolve_execution_eligibility(
        "EURIBOR-ICE",
        ib_symbol="ER3",
        exchange="ICEEU",
    )
    assert ordinary["execution_eligible"]
    assert ordinary["execution_restriction_reason"] is None

    # EUREX is not categorically unavailable. Only products for which this
    # account returned explicit regulatory permission failures are screened.
    for ib_symbol in (
        "DJESL",
        "DJESS",
        "ESE",
        "ESU",
        "OMXH25",
        "SX3P",
        "SX4P",
        "SX86P",
        "SX8E",
        "SX8P",
        "SXAP",
        "SXDP",
        "SXEP",
        "SXKE",
        "SXOP",
        "SXPP",
        "SXRP",
    ):
        eurex_product = resolve_execution_eligibility(
            "TEST",
            ib_symbol=ib_symbol,
            exchange="EUREX",
        )
        assert not eurex_product["execution_eligible"]
        assert (
            eurex_product["execution_restriction_reason"]
            == "ibkr_us_product_no_trading_permission"
        )
    eurex_bund = resolve_execution_eligibility(
        "BUND",
        ib_symbol="GBL",
        exchange="EUREX",
    )
    assert eurex_bund["execution_eligible"]
    assert eurex_bund["execution_restriction_reason"] is None

    # Other venues remain live-checkable except for the exact products that
    # returned account-level permission or sanctions failures.
    restricted_products = (
        ("3KTB", "KSE"),
        ("EBM", "MATIF"),
        ("ECF", "ENDEX"),
        ("FIVNM30", "SGX"),
        ("FLKTB", "KSE"),
        ("FTMIB", "IDEM"),
        ("JPNK400", "OSE.JPN"),
        ("K200", "KSE"),
        ("K200M", "KSE"),
        ("KOSDQ150", "KSE"),
        ("MTW", "HKFE"),
    )
    for ib_symbol, exchange in restricted_products:
        product = resolve_execution_eligibility(
            "TEST",
            ib_symbol=ib_symbol,
            exchange=exchange,
        )
        assert not product["execution_eligible"]
        assert (
            product["execution_restriction_reason"]
            == "ibkr_us_product_no_trading_permission"
        )

    sanctions_product = resolve_execution_eligibility(
        "HANGTECH",
        ib_symbol="HSTECH",
        exchange="HKFE",
    )
    assert not sanctions_product["execution_eligible"]
    assert (
        sanctions_product["execution_restriction_reason"]
        == "ibkr_us_product_sanctions_restriction"
    )

    for ib_symbol, exchange in (
        ("K300", "KSE"),
        ("TFM", "ENDEX"),
        ("ECO", "MATIF"),
        ("HSI", "HKFE"),
        ("N225M", "OSE.JPN"),
    ):
        unverified = resolve_execution_eligibility(
            "TEST",
            ib_symbol=ib_symbol,
            exchange=exchange,
        )
        assert unverified["execution_eligible"]
        assert unverified["execution_restriction_reason"] is None

    # STI is explicitly restricted, but the rest of SGX remains subject to
    # live contract/account verification rather than a venue-wide assumption.
    sgx_product = resolve_execution_eligibility(
        "IRON",
        ib_symbol="SCI",
        exchange="SGX",
    )
    assert sgx_product["execution_eligible"]
    assert sgx_product["execution_restriction_reason"] is None


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

from datetime import datetime

import polars as pl

from derivatives_bt_engine.data.pysystemtrade_pooling import (
    apply_pysystemtrade_pooling_mapping,
    discover_pysystemtrade_duplicate_candidates,
    load_pysystemtrade_pooling_mapping,
    pooling_mapping_fingerprint,
)


def _write_mapping(path) -> None:
    pl.DataFrame(
        {
            "instrument_code": ["FULL", "MICRO"],
            "economic_family_id": ["TEST", "TEST"],
            "roll_policy_id": ["TEST_QUARTERLY", "TEST_QUARTERLY"],
            "duplicate_group_id": ["TEST_SIZE", "TEST_SIZE"],
            "pooling_role": ["primary", "execution_duplicate"],
            "representative_instrument": ["FULL", "FULL"],
            "include_default": [True, False],
            "decision_basis": ["longer_history", "same_history"],
            "notes": ["", ""],
        }
    ).write_csv(path)


def test_bundled_mapping_is_reviewed_and_excludes_only_explicit_rows():
    mapping = load_pysystemtrade_pooling_mapping()

    assert mapping["instrument_code"].n_unique() == mapping.height
    assert mapping.filter(~pl.col("include_default")).height == 23
    crude = mapping.filter(pl.col("instrument_code").str.starts_with("CRUDE"))
    assert set(crude.filter(pl.col("include_default"))["instrument_code"]) == {
        "CRUDE_ICE",
        "CRUDE_W",
    }
    assert set(crude.filter(~pl.col("include_default"))["instrument_code"]) == {
        "CRUDE_W_micro",
        "CRUDE_W_mini",
    }
    assert len(pooling_mapping_fingerprint()) == 16


def test_apply_mapping_defaults_unmapped_history_to_included_singleton(tmp_path):
    mapping_path = tmp_path / "pool.csv"
    _write_mapping(mapping_path)
    catalog = pl.DataFrame(
        {"instrument_code": ["FULL", "MICRO", "OTHER"], "value": [1, 2, 3]}
    )

    classified = apply_pysystemtrade_pooling_mapping(
        catalog, mapping_path=mapping_path
    )

    full = classified.filter(pl.col("instrument_code") == "FULL").row(
        0, named=True
    )
    micro = classified.filter(pl.col("instrument_code") == "MICRO").row(
        0, named=True
    )
    other = classified.filter(pl.col("instrument_code") == "OTHER").row(
        0, named=True
    )
    assert full["include_default"]
    assert not micro["include_default"]
    assert micro["representative_instrument"] == "FULL"
    assert other["include_default"]
    assert other["pooling_role"] == "singleton"
    assert other["economic_family_id"] == "OTHER"


def test_candidate_discovery_uses_symbol_description_broker_and_review_map():
    common = {
        "asset_class": "Equity",
        "currency": "USD",
        "ib_exchange": "CME",
        "adjusted_start": datetime(2000, 1, 1),
        "price_days": 100,
        "hold_roll_cycle": "HMUZ",
        "roll_offset_days": -5,
        "carry_offset": 1,
        "priced_roll_cycle": "HMUZ",
        "expiry_offset": 15,
        "roll_policy_id": "SP500_QUARTERLY",
        "duplicate_group_id": "SP500_SIZE",
        "representative_instrument": "SP500",
        "decision_basis": "same_history",
        "notes": "",
    }
    catalog = pl.DataFrame(
        [
            {
                **common,
                "instrument_code": "SP500",
                "description": "US equity index S&P500",
                "point_size": 50.0,
                "ib_symbol": "ES",
                "economic_family_id": "SP500",
                "pooling_role": "primary",
                "include_default": True,
            },
            {
                **common,
                "instrument_code": "SP500_micro",
                "description": "S&P 500 micro",
                "point_size": 5.0,
                "ib_symbol": "MES",
                "economic_family_id": "SP500",
                "pooling_role": "execution_duplicate",
                "include_default": False,
            },
            {
                **common,
                "instrument_code": "UNRELATED",
                "description": "Unrelated equity index",
                "point_size": 10.0,
                "ib_symbol": "XYZ",
                "economic_family_id": "UNRELATED",
                "duplicate_group_id": None,
                "pooling_role": "singleton",
                "include_default": True,
                "representative_instrument": "UNRELATED",
            },
        ],
        infer_schema_length=None,
    )

    candidates = discover_pysystemtrade_duplicate_candidates(catalog)

    assert candidates.height == 1
    pair = candidates.row(0, named=True)
    assert (pair["instrument_a"], pair["instrument_b"]) == (
        "SP500",
        "SP500_micro",
    )
    assert "normalized_symbol" in pair["candidate_reasons"]
    assert "reviewed_economic_family" in pair["candidate_reasons"]

from datetime import date, datetime
from types import SimpleNamespace

import polars as pl

from derivatives_bt_engine.data.pysystemtrade_pooling import (
    _history_pair_metrics,
    apply_pysystemtrade_pooling_mapping,
    discover_pysystemtrade_duplicate_candidates,
    load_pysystemtrade_pooling_mapping,
    pooling_mapping_fingerprint,
    write_pysystemtrade_pooling_audit,
)


def _pair_history(dates, levels):
    signal = pl.DataFrame(
        {
            "trade_date": dates,
            "signal_index": levels,
        }
    ).with_columns(pl.col("signal_index").pct_change().alias("ret_1d"))
    marks = pl.DataFrame(
        schema={
            "trade_date": pl.Date,
            "contract_id": pl.String,
            "is_roll": pl.Boolean,
        }
    )
    return SimpleNamespace(signal=signal, marks=marks)


def test_pair_correlation_recomputes_returns_over_common_date_intervals():
    all_dates = [date(2024, 1, day) for day in range(1, 6)]
    levels = [100.0, 110.0, 132.0, 118.8, 124.74]
    left = _pair_history(all_dates, levels)
    right = _pair_history(
        [all_dates[0], *all_dates[2:]],
        [levels[0], *levels[2:]],
    )

    metrics = _history_pair_metrics(left, right)

    assert metrics["overlap_return_days"] == 3
    assert metrics["return_correlation"] == 1.0


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
    assert mapping.filter(~pl.col("include_default")).height == 24
    assert not mapping["decision_basis"].str.contains("source_rows").any()
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

    representatives = {
        row["duplicate_group_id"]: row["instrument_code"]
        for row in mapping.filter(pl.col("pooling_role") == "primary").to_dicts()
    }
    assert representatives["CADUSD_SIZE_VARIANTS"] == "CAD"
    assert representatives["IBEX_SIZE_VARIANTS"] == "IBEX"
    assert representatives["JGB_OSE_SIZE_VARIANTS"] == "JGB"
    assert representatives["NASDAQ_SIZE_VARIANTS"] == "NASDAQ"
    assert representatives["USDSGD_SIZE_VARIANTS"] == "SGD"
    assert representatives["ETHER_SIZE_VARIANTS"] == "ETHEREUM"


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


def test_report_shows_affirmative_representative_with_broker_identity(tmp_path):
    classifications = pl.DataFrame(
        [
            {
                "instrument_code": "FULL",
                "description": "Full contract",
                "ib_symbol": "NQ",
                "ib_effective_point_value": 20.0,
                "adjusted_start": datetime(2000, 1, 1),
                "adjusted_end": datetime(2024, 1, 1),
                "price_days": 101,
                "duplicate_group_id": "TEST_SIZE_VARIANTS",
                "economic_family_id": "TEST",
                "roll_policy_id": "TEST_QUARTERLY",
                "pooling_role": "primary",
                "representative_instrument": "FULL",
                "include_default": True,
                "decision_basis": "longer_daily_history",
                "pooling_mapping_hash": "testhash",
            },
            {
                "instrument_code": "MICRO",
                "description": "Micro contract",
                "ib_symbol": "MNQ",
                "ib_effective_point_value": 2.0,
                "adjusted_start": datetime(2000, 1, 1),
                "adjusted_end": datetime(2024, 1, 1),
                "price_days": 100,
                "duplicate_group_id": "TEST_SIZE_VARIANTS",
                "economic_family_id": "TEST",
                "roll_policy_id": "TEST_QUARTERLY",
                "pooling_role": "execution_duplicate",
                "representative_instrument": "FULL",
                "include_default": False,
                "decision_basis": "same_contract_policy_history",
                "pooling_mapping_hash": "testhash",
            },
        ]
    )

    write_pysystemtrade_pooling_audit(tmp_path, classifications, pl.DataFrame())

    report = (tmp_path / "report.md").read_text()
    assert "## Selected representatives for size variants" in report
    assert "| TEST_SIZE_VARIANTS | FULL | Full contract | NQ | 20.0 |" in report
    assert "| MICRO | TEST | TEST_QUARTERLY | execution_duplicate | FULL |" in report
    assert "Common-interval returns are recomputed" in report

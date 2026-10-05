import os

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_rankings import (
    find_latest_phase1_cost_report,
    load_latest_phase1_cost_report,
    phase2_search_universe,
    phase2_step1_candidates,
    top_n_by_asset_class,
)


def _report() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["ES", "MES", "NQ", "ZN", "ZT"],
        "description": ["S&P", "Micro S&P", "Nasdaq", "10Y", "2Y"],
        "asset_class": ["Equity", "Equity", "Equity", "Rates", "Rates"],
        "cost_rank_in_asset_class": [2, 3, 1, 2, 1],
        "affordability_rank_in_asset_class": [3, 1, 2, 2, 1],
        "notional_rank_in_asset_class": [3, 1, 2, 2, 1],
        "trade_sr": [0.002, 0.004, 0.001, 0.003, 0.002],
        "execution_eligible": [True, True, True, True, True],
    })


def test_load_latest_phase1_cost_report_prefers_newest_ib_file(tmp_path):
    older = tmp_path / "pysystemtrade_cost_phase1_ib_20261001_120000.csv"
    newer = tmp_path / "pysystemtrade_cost_phase1_ib_20261002_120000.csv"
    _report().head(1).write_csv(older)
    _report().head(2).write_csv(newer)
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

    path, report = load_latest_phase1_cost_report(tmp_path)

    assert path == newer.resolve()
    assert report.height == 2


def test_find_latest_phase1_cost_report_raises_for_empty_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="No Phase 1 cost report"):
        find_latest_phase1_cost_report(tmp_path)


def test_top_n_by_asset_class_supports_named_rankings():
    rankings = top_n_by_asset_class(
        _report(), n=2, rank_by="affordability"
    )

    assert list(rankings) == ["Equity", "Rates"]
    assert rankings["Equity"].get_column("symbol").to_list() == ["MES", "NQ"]
    assert rankings["Rates"].get_column("symbol").to_list() == ["ZT", "ZN"]


def test_top_n_by_asset_class_cost_ranking_is_cheapest_first():
    rankings = top_n_by_asset_class(_report(), n=2, rank_by="cost")

    assert rankings["Equity"].get_column("symbol").to_list() == ["NQ", "ES"]
    assert rankings["Rates"].get_column("symbol").to_list() == ["ZT", "ZN"]


def test_top_n_by_asset_class_can_keep_all_columns_and_filter_classes():
    rankings = top_n_by_asset_class(
        _report(),
        n=1,
        rank_by="trade_sr",
        asset_classes=["Rates"],
        columns=None,
    )

    assert list(rankings) == ["Rates"]
    assert rankings["Rates"].columns == _report().columns
    assert rankings["Rates"].get_column("symbol").to_list() == ["ZT"]


def test_top_n_by_asset_class_excludes_restricted_execution_by_default():
    report = pl.concat([
        _report(),
        pl.DataFrame({
            "symbol": ["SGX"],
            "description": ["Straits Times Index"],
            "asset_class": ["Equity"],
            "cost_rank_in_asset_class": [0],
            "affordability_rank_in_asset_class": [0],
            "notional_rank_in_asset_class": [0],
            "trade_sr": [0.0],
            "execution_eligible": [False],
        }),
    ])

    eligible = top_n_by_asset_class(report, n=1, columns=None)
    all_rows = top_n_by_asset_class(
        report,
        n=1,
        columns=None,
        eligible_only=False,
    )

    assert eligible["Equity"]["symbol"].to_list() == ["NQ"]
    assert all_rows["Equity"]["symbol"].to_list() == ["SGX"]


def test_phase2_search_universe_filters_without_mutating_audit_report():
    report = _report().with_columns(
        pl.Series("phase2_eligible", [True, False, None, True, False])
    )

    selected = phase2_search_universe(report)

    assert selected.get_column("symbol").to_list() == ["ES", "ZN"]
    assert report.height == 5


def test_phase2_search_universe_requires_new_phase1_schema():
    with pytest.raises(ValueError, match="rerun the Phase 1 audit"):
        phase2_search_universe(_report())


def _phase2_step1_report() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["PASS", "ILLIQ", "BIG", "NOIB", "NODATA"],
        "asset_cls": ["Ags", "Ags", "Equity", "Vol", "FX"],
        "ann_dvol": [500.0, 500.0, 25_000.0, 500.0, 500.0],
        "avg_daily_volume": [1_000.0, 50.0, 1_000.0, 1_000.0, None],
        "pct_mkt_volume": [0.1, 2.0, 0.1, 0.1, None],
        "cost_elig": [True] * 5,
        "size_elig": [True, True, False, True, True],
        "liq_elig": [True, False, True, True, False],
        "data_elig": [True, True, True, True, False],
        "instr_has_elig_ewmac_rule": [True] * 5,
        "exec_elig": [True] * 5,
        "phase2_elig": [True, False, False, True, False],
        "ib_avail": [
            "contract_qualified",
            "contract_qualified",
            "contract_qualified",
            "unavailable_or_unverified",
            "contract_qualified",
        ],
    })


def test_phase2_step1_uses_saved_gates_and_keeps_only_selected_rows():
    report = _phase2_step1_report()

    selected = phase2_step1_candidates(report)

    assert selected.get_column("symbol").to_list() == ["PASS"]
    assert selected.get_column("ann_dvol").to_list() == [500.0]
    assert report.height == 5


def test_phase2_step1_does_not_recalculate_phase1_metrics():
    report = _phase2_step1_report().with_columns(
        pl.when(pl.col("symbol") == "PASS")
        .then(99.0)
        .otherwise(pl.col("pct_mkt_volume"))
        .alias("pct_mkt_volume")
    )

    selected = phase2_step1_candidates(report)

    assert selected.get_column("symbol").to_list() == ["PASS"]
    assert selected.get_column("pct_mkt_volume").to_list() == [99.0]


def test_phase2_step1_requires_current_phase1_gate_schema():
    with pytest.raises(ValueError, match="missing Step 1 selection fields"):
        phase2_step1_candidates(_report())

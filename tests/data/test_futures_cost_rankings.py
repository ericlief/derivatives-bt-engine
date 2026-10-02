import os

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_rankings import (
    find_latest_phase1_cost_report,
    load_latest_phase1_cost_report,
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
        "selected_sr_cost_per_trade": [0.002, 0.004, 0.001, 0.003, 0.002],
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


def test_top_n_by_asset_class_can_keep_all_columns_and_filter_classes():
    rankings = top_n_by_asset_class(
        _report(),
        n=1,
        rank_by="selected_sr_cost_per_trade",
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
            "selected_sr_cost_per_trade": [0.0],
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

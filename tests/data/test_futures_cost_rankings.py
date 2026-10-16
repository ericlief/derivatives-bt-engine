import os

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_cost_rankings import (
    apply_phase2_selection_filters,
    find_latest_phase1_cost_report,
    load_latest_phase1_cost_report,
    override_phase2_selection_scenario,
    phase2_search_universe,
    phase2_step1_candidates,
    phase2_step1_config,
    resolve_phase2_selection_config,
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
    older = tmp_path / "pysystemtrade_cost_phase1_ib_20261001_120000.parquet"
    newer = tmp_path / "pysystemtrade_cost_phase1_ib_20261002_120000.parquet"
    _report().head(1).write_parquet(older)
    _report().head(2).write_parquet(newer)
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

    path, report = load_latest_phase1_cost_report(tmp_path)

    assert path == newer.resolve()
    assert report.height == 2


def test_load_latest_phase1_cost_report_keeps_legacy_csv_compatibility(tmp_path):
    legacy = tmp_path / "pysystemtrade_cost_phase1_ib_20261002_120000.csv"
    _report().write_csv(legacy)

    path, report = load_latest_phase1_cost_report(tmp_path)

    assert path == legacy.resolve()
    assert report.height == _report().height


def test_latest_report_prefers_rule_cache_over_legacy_selection_screen(tmp_path):
    """Automatic discovery uses the scenario-neutral cache when both exist."""
    cache = tmp_path / "multi_speed_ewmac_rule_cache_20261015_120000.parquet"
    screen = tmp_path / "pysystemtrade_selection_screen_20261015_130000.parquet"
    _report().head(1).write_parquet(cache)
    _report().head(2).write_parquet(screen)

    path, report = load_latest_phase1_cost_report(tmp_path)

    assert path == cache.resolve()
    assert report.height == 1


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
        pl.Series("selection_eligible", [True, False, None, True, False])
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
        "asset_cls": ["ags", "ags", "equity", "vol", "fx"],
        "econ_subcls": ["grains", "grains", "us", "vol", "fx"],
        "ann_dvol": [500.0, 500.0, 25_000.0, 500.0, 500.0],
        "avg_daily_volume": [1_000.0, 50.0, 1_000.0, 1_000.0, None],
        "pct_mkt_volume": [0.1, 2.0, 0.1, 0.1, None],
        "init_cap_usd": [100_000.0] * 5,
        "target_vol": [0.2] * 5,
        "cost_lim_sr": [0.01] * 5,
        "rule_cost_lim_sr": [0.15] * 5,
        "liq_ann_trades": [25.0] * 5,
        "liq_days": [250] * 5,
        "min_daily_volume": [100.0] * 5,
        "max_pct_mkt_volume": [1.0] * 5,
        "trade_sr": [0.002] * 5,
        "roll_sr": [0.003] * 5,
        "comm": [2.5] * 5,
        "comm_ccy": ["USD"] * 5,
        "comm_usd": [2.5] * 5,
        "comm_src": ["ib_what_if"] * 5,
        "init_margin_usd": [500.0] * 5,
        "maint_margin_usd": [400.0] * 5,
        "margin_ccy": ["USD"] * 5,
        "whatif_attempted": [True] * 5,
        "whatif_elig": [True] * 5,
        "ref_ann_rolls": [4.0] * 5,
        "cost_elig": [True] * 5,
        "size_elig": [True, True, False, True, True],
        "liq_elig": [True, False, True, True, False],
        "data_elig": [True, True, True, True, False],
        "instr_has_elig_ewmac_rule": [True] * 5,
        "exec_elig": [True] * 5,
        "selection_elig": [True, False, False, True, False],
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
    assert selected.get_column("init_cap_usd").to_list() == [100_000.0]
    assert selected.get_column("target_vol").to_list() == [0.2]
    assert selected.select("trade_sr", "roll_sr", "ref_ann_rolls").row(0) == (
        0.002,
        0.003,
        4.0,
    )
    assert selected.select(
        "comm_usd", "init_margin_usd", "margin_ccy", "whatif_attempted"
    ).row(0) == (2.5, 500.0, "USD", True)
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


def test_phase2_step1_rejects_inconsistent_run_parameters():
    report = _phase2_step1_report().with_columns(
        pl.when(pl.col("symbol") == "ILLIQ")
        .then(200_000.0)
        .otherwise(pl.col("init_cap_usd"))
        .alias("init_cap_usd")
    )

    with pytest.raises(ValueError, match="inconsistent init_cap_usd"):
        phase2_step1_config(report)


def test_phase2_scenario_override_recalculates_target_dependent_gates():
    """Target-vol grids reuse persisted market data but refresh hard gates."""
    report = _phase2_step1_report().with_columns(
        pl.Series("mkt_risk_vol_usd_day", [1_000_000.0] * 5),
        pl.Series("volume_elig", [True, False, True, True, False]),
    )

    recalculated = override_phase2_selection_scenario(
        report,
        initial_capital_usd=75_000.0,
        target_vol=0.15,
    )

    assert recalculated.get_column("init_cap_usd").unique().to_list() == [
        75_000.0
    ]
    assert recalculated.get_column("target_vol").unique().to_list() == [0.15]
    assert recalculated.get_column("max_ann_dvol").unique().to_list() == [
        11_250.0
    ]
    assert recalculated.filter(pl.col("symbol") == "BIG").select(
        "size_elig"
    ).item() is False
    assert recalculated.filter(pl.col("symbol") == "PASS").select(
        "selection_elig"
    ).item() is True


def test_rule_cache_filters_resolve_defaults_without_cached_target_vol():
    """A rule cache needs no capital or target-volatility columns."""
    cache = pl.DataFrame({
        "symbol": ["PASS"],
        "asset_cls": ["ags"],
        "econ_subcls": ["grains"],
        "ann_dvol": [500.0],
        "avg_daily_volume": [1_000.0],
        "trade_sr": [0.002],
        "rule_cost_lim_sr": [0.15],
        "instr_has_elig_ewmac_rule": [True],
        "exec_elig": [True],
        "ib_avail": ["contract_qualified"],
        "cur_px": [500.0],
        "cur_fx_to_usd": [1.0],
        "mult": [5.0],
        "whatif_attempted": [True],
        "whatif_elig": [True],
    })

    config = resolve_phase2_selection_config(cache)
    filtered = apply_phase2_selection_filters(cache, config=config)

    assert config["init_cap_usd"] == pytest.approx(100_000.0)
    assert config["target_vol"] == pytest.approx(0.20)
    assert config["liq_ann_trades"] == pytest.approx(25.6)
    assert config["liq_days"] == 256
    assert filtered.select("selection_elig").item() is True
    assert "target_vol" not in cache.columns

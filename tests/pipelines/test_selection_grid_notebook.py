"""Tests for lazy notebook navigation across selection-grid scenarios."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.ewmac_notebook import (
    load_selection_grid_review_instruments,
    plot_selection_grid_rebalance_weights,
)


def test_grid_loader_reads_all_reviews_for_exact_scenario(tmp_path: Path):
    """Grid navigation loads dated reviews, not latest-book membership."""
    scenario_id = "eh25_mhnone"
    grid = tmp_path / "selection_grid"
    scenario = grid / scenario_id
    scenario.mkdir(parents=True)
    pl.DataFrame({
        "scenario_id": [scenario_id],
        "baseline_scenario": [True],
    }).write_parquet(grid / "selection_grid_summary.parquet")
    pl.DataFrame({
        "sel_date": [date(2024, 1, 1), date(2025, 1, 1)],
        "symbol": ["CL", "NQ"],
        "sel_active": [True, True],
        "weight": [0.5, 0.5],
        "limit_weight": [0.25, 0.25],
    }).write_parquet(
        scenario
        / "multi_speed_ewmac_selection_review_instruments_20261106.parquet"
    )

    reviews = load_selection_grid_review_instruments(grid, scenario_id)

    assert reviews.get_column("sel_date").to_list() == [
        date(2024, 1, 1),
        date(2025, 1, 1),
    ]
    assert reviews.get_column("scenario_id").unique().to_list() == [scenario_id]


def test_grid_loader_rejects_unknown_scenario(tmp_path: Path):
    """A typo cannot silently select another scenario directory."""
    grid = tmp_path / "selection_grid"
    grid.mkdir()
    pl.DataFrame({"scenario_id": ["known"]}).write_parquet(
        grid / "selection_grid_summary.parquet"
    )

    with pytest.raises(KeyError, match="no scenario"):
        load_selection_grid_review_instruments(grid, "unknown")


def test_grid_widget_defaults_to_baseline_and_switches_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The outer widget navigates scenarios before the nested year widget."""
    import ipywidgets as widgets

    grid = tmp_path / "selection_grid"
    grid.mkdir()
    scenario_ids = ["baseline", "eh25"]
    pl.DataFrame({
        "scenario_id": scenario_ids,
        "baseline_scenario": [True, False],
        "sel_elig_hyst_pct": [0.0, 25.0],
        "sel_membership_hyst": ["none", "none"],
        "sel_rank_buf_pct": [None, None],
        "sel_rank_n_ewma_hl": [None, None],
        "sel_book_score_buf_pct": [None, None],
        "n_instr": [2, 2],
        "construction_score": [1.0, 1.1],
        "jaccard_to_baseline": [1.0, 0.5],
    }).write_parquet(grid / "selection_grid_summary.parquet")
    for scenario_id in scenario_ids:
        scenario = grid / scenario_id
        scenario.mkdir()
        pl.DataFrame({
            "sel_date": [date(2026, 1, 1)],
            "symbol": ["CL"],
            "sel_active": [True],
            "weight": [1.0],
            "limit_weight": [0.25],
        }).write_parquet(
            scenario
            / "multi_speed_ewmac_selection_review_instruments_20261106.parquet"
        )

    displayed = []
    monkeypatch.setattr(
        "derivatives_bt_engine.pipelines.ewmac_notebook."
        "plot_selection_rebalance_weights",
        lambda *args, **kwargs: widgets.Label("dated books"),
    )
    monkeypatch.setattr(
        "IPython.display.display", lambda value: displayed.append(value)
    )

    dashboard = plot_selection_grid_rebalance_weights(grid)
    selector = dashboard.children[0]

    assert isinstance(dashboard, widgets.VBox)
    assert selector.value == "baseline"
    assert len(displayed) == 2
    selector.value = "eh25"
    assert len(displayed) == 4

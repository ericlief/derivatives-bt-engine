"""Tests for the command-line Phase 2 robustness-grid coordinator."""

from pathlib import Path

import polars as pl

from derivatives_bt_engine.pipelines.selection_grid import (
    SelectionScenario,
    _summary_row,
    build_scenarios,
    parse_args,
    run,
)


def test_grid_summary_recovers_score_from_accepted_passes_for_legacy_book(
    tmp_path: Path,
):
    """Older selected-book reports recover their score from pass audit."""
    book_path = tmp_path / "multi_speed_ewmac_selection_book_20261016.parquet"
    pl.DataFrame({
        "symbol": ["MZC"],
        "idm": [2.0],
        "margin_at_max_usd": [1000.0],
        "portfolio_margin_at_max_usd": [1000.0],
    }).write_parquet(book_path)
    pl.DataFrame({
        "selection_step": [1, 2, 2],
        "score": [0.40, 0.42, 0.42],
    }).write_parquet(
        tmp_path / "multi_speed_ewmac_selection_passes_20261016.parquet"
    )
    scenario = SelectionScenario(
        target_volatility=0.20,
        maximum_instrument_margin_percent=20.0,
        maximum_book_margin_percent=75.0,
        maximum_weight_idm=0.20,
        maximum_instruments_per_cluster=None,
        include_roll_cost_in_score=True,
        weight_policy="topdown_classif",
    )

    row, _ = _summary_row(
        scenario,
        status="complete",
        elapsed_seconds=1.0,
        book_path=book_path,
        rule_cache=pl.DataFrame({
            "symbol": ["MZC"],
            "asset_cls": ["ags"],
            "econ_subcls": ["grains"],
        }),
    )

    assert row["construction_score"] == 0.42


def test_default_grid_varies_construction_scenarios_not_performance_inputs():
    """The recommended first pass is the documented 3x3x2 grid."""
    args = parse_args(["--input", "screen.parquet"])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 18
    assert {scenario.target_volatility for scenario in scenarios} == {
        0.15, 0.20, 0.25
    }
    assert {
        scenario.maximum_instrument_margin_percent for scenario in scenarios
    } == {15.0, 20.0, 25.0}
    assert {scenario.include_roll_cost_in_score for scenario in scenarios} == {
        False, True
    }
    assert {scenario.maximum_book_margin_percent for scenario in scenarios} == {
        75.0
    }


def test_grid_accepts_secondary_constraint_lists():
    """Book margin, weight-IDM, and subclass caps can be swept separately."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--max-book-margin-pcts",
        "50,100",
        "--max-weight-idms",
        "0.15,0.25",
        "--max-instr-per-clusters",
        "2,none",
        "--roll-cost-modes",
        "include",
    ])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 8
    assert {scenario.maximum_instruments_per_cluster for scenario in scenarios} == {
        2, None
    }


def test_dry_run_writes_reproducible_commands_without_running_selection(
    tmp_path: Path,
):
    """Dry-run mode creates the shell plan but no Phase 2 book artifacts."""
    source = tmp_path / "screen.parquet"
    output = tmp_path / "grid"
    pl.DataFrame({"symbol": ["MZC"]}).write_parquet(source)

    commands_path, summary_path = run([
        "--input",
        str(source),
        "--output-dir",
        str(output),
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--dry-run",
    ])

    command_text = commands_path.read_text(encoding="utf-8")
    assert summary_path is None
    assert commands_path.parent.parent == output
    assert commands_path.parent.name.startswith("selection_grid_")
    assert "--target-vol 0.2" in command_text
    assert "--max-instr-margin-pct 20.0" in command_text
    assert "--include-roll-cost-in-score" in command_text
    assert not list(output.rglob("multi_speed_ewmac_selection_book_*.parquet"))


def test_grid_can_pin_an_exact_run_directory_for_resume(tmp_path: Path):
    """An explicit run directory supports resuming without another timestamp."""
    source = tmp_path / "screen.parquet"
    run_dir = tmp_path / "selection_grid_existing"
    pl.DataFrame({"symbol": ["MZC"]}).write_parquet(source)

    commands_path, summary_path = run([
        "--input",
        str(source),
        "--run-dir",
        str(run_dir),
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--dry-run",
    ])

    assert commands_path.parent == run_dir
    assert summary_path is None

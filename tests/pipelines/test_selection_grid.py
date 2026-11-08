"""Tests for the command-line Phase 2 robustness-grid coordinator."""

import subprocess
from pathlib import Path
from threading import Lock
from time import sleep

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.selection_grid import (
    SelectionScenario,
    _completed_book,
    _execute_planned_scenarios,
    _freeze_ib_history_input,
    _load_frozen_ib_history_input,
    _run_history_materialization,
    _summary_row,
    build_scenarios,
    parse_args,
    run,
)


def _test_scenario(score_threshold: float) -> SelectionScenario:
    """Return a minimal distinct scenario for coordinator execution tests."""
    return SelectionScenario(
        target_volatility=0.20,
        maximum_instrument_margin_percent=20.0,
        maximum_book_margin_percent=50.0,
        maximum_weight_idm=0.20,
        score_threshold=score_threshold,
        maximum_instruments_per_class=None,
        maximum_instruments_per_subclass=None,
        maximum_asset_class_weight_percent=None,
        maximum_economic_subclass_weight_percent=None,
        include_roll_cost_in_score=True,
        weight_policy="topdown_classif",
    )


def test_grid_requires_explicit_cache_root():
    """Grid runs must pass one cache root to every complete scenario."""
    assert parse_args([
        "--input", "screen.parquet", "--cache", "/tmp/selection-cache"
    ]).cache.as_posix() == (
        "/tmp/selection-cache"
    )
    with pytest.raises(ValueError, match="--cache is required"):
        run(["--input", "screen.parquet"])


def test_grid_rejects_nonpositive_worker_count(tmp_path: Path):
    """A worker pool must contain at least one supervising thread."""
    source = tmp_path / "screen.parquet"
    pl.DataFrame({"symbol": ["MZC"]}).write_parquet(source)
    with pytest.raises(ValueError, match="--max-workers must be at least one"):
        run([
            "--input",
            str(source),
            "--cache",
            str(tmp_path / "cache"),
            "--max-workers",
            "0",
        ])


def test_grid_runs_subprocesses_concurrently_after_serial_cache_warmup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Worker limit supervises concurrent child CLIs after one warm-up run."""
    active = 0
    maximum_active = 0
    starts = 0
    lock = Lock()

    def fake_run(command, **kwargs):
        nonlocal active, maximum_active, starts
        with lock:
            starts += 1
            active += 1
            maximum_active = max(maximum_active, active)
        sleep(0.04)
        with lock:
            active -= 1
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "derivatives_bt_engine.pipelines.selection_grid.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "derivatives_bt_engine.pipelines.selection_grid._latest_book",
        lambda scenario_dir: scenario_dir / "book.parquet",
    )
    monkeypatch.setattr(
        "derivatives_bt_engine.pipelines.selection_grid._core_outputs_for_book",
        lambda book: (book, book),
    )
    planned = []
    for index in range(5):
        scenario_dir = tmp_path / f"scenario_{index}"
        scenario_dir.mkdir()
        (scenario_dir / "book.parquet").touch()
        planned.append((
            _test_scenario(0.80 + index / 100),
            scenario_dir,
            ["selection", str(index)],
        ))

    outcomes = _execute_planned_scenarios(
        planned,
        resume=False,
        fail_fast=False,
        max_workers=3,
    )

    assert starts == 5
    assert maximum_active == 3
    assert set(outcomes) == {0, 1, 2, 3, 4}
    assert all(outcome.status == "complete" for outcome in outcomes.values())


def test_resume_rejects_book_from_interrupted_complete_run(tmp_path: Path):
    """A book alone must not cause an interrupted scenario to be skipped."""
    stamp = "20261105_225003"
    book = (
        tmp_path / f"multi_speed_ewmac_selection_book_{stamp}.parquet"
    )
    book.touch()
    (tmp_path / "command_output.log").touch()

    assert _completed_book(tmp_path) is None

    (
        tmp_path / f"multi_speed_ewmac_selection_cash_pnl_{stamp}.parquet"
    ).touch()
    (
        tmp_path
        / f"multi_speed_ewmac_selection_portfolio_curve_{stamp}.parquet"
    ).touch()
    assert _completed_book(tmp_path) == book


def test_resume_requires_matching_frozen_input_fingerprint(tmp_path: Path):
    """A successful artifact set is not reusable against different history."""
    stamp = "20261106_120000"
    book = tmp_path / f"multi_speed_ewmac_selection_book_{stamp}.parquet"
    book.touch()
    (
        tmp_path / f"multi_speed_ewmac_selection_cash_pnl_{stamp}.parquet"
    ).touch()
    (
        tmp_path
        / f"multi_speed_ewmac_selection_portfolio_curve_{stamp}.parquet"
    ).touch()
    (tmp_path / ".selection_grid_complete.json").write_text(
        '{"book": "multi_speed_ewmac_selection_book_20261106_120000.parquet", '
        '"input_fingerprint": "history-a"}\n',
        encoding="utf-8",
    )

    assert _completed_book(
        tmp_path, input_fingerprint="history-a"
    ) == book
    assert _completed_book(
        tmp_path, input_fingerprint="history-b"
    ) is None


def test_frozen_ib_history_manifest_detects_content_changes(tmp_path: Path):
    """The run-local IB snapshot has a verified immutable identity."""
    source = tmp_path / "source.duckdb"
    output = tmp_path / "grid"
    source.write_bytes(b"stable duckdb fixture")

    frozen, fingerprint = _freeze_ib_history_input(source, output)

    assert frozen.read_bytes() == source.read_bytes()
    assert _load_frozen_ib_history_input(output) == (frozen, fingerprint)
    frozen.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        _load_frozen_ib_history_input(output)


def test_history_materialization_rejects_swallowed_connection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A zero-exit selection may not hide an unusable live acquisition."""
    def fake_run(command, **kwargs):
        kwargs["stdout"].write(
            "selection_stitched_history unavailable reason=Not connected\n"
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "derivatives_bt_engine.pipelines.selection_grid.subprocess.run",
        fake_run,
    )

    with pytest.raises(RuntimeError, match="unusable shared input"):
        _run_history_materialization(
            ["selection", "--ib-history-live", "missing"],
            tmp_path,
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
        score_threshold=0.90,
        maximum_instruments_per_class=None,
        maximum_instruments_per_subclass=None,
        maximum_asset_class_weight_percent=None,
        maximum_economic_subclass_weight_percent=None,
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
    assert row["maximum_instrument_margin_usd"] == 1000.0
    assert row["book_margin_at_maximum_usd"] == 1000.0
    assert row["coverage_satisfied"] is None


def test_grid_summary_reads_current_compact_margin_columns(tmp_path: Path):
    """Current public book margin names populate the grid summary."""
    book_path = tmp_path / "multi_speed_ewmac_selection_book_now.parquet"
    pl.DataFrame({
        "symbol": ["NQ", "ES"],
        "marg_at_max": [1200.0, 800.0],
        "port_marg_at_max": [2000.0, 2000.0],
        "coverage_satisfied": [True, True],
    }).write_parquet(book_path)
    scenario = SelectionScenario(
        target_volatility=0.20,
        maximum_instrument_margin_percent=20.0,
        maximum_book_margin_percent=50.0,
        maximum_weight_idm=0.20,
        score_threshold=0.90,
        minimum_instruments_per_class=1,
        maximum_instruments_per_class=None,
        maximum_instruments_per_subclass=None,
        maximum_asset_class_weight_percent=None,
        maximum_economic_subclass_weight_percent=None,
        include_roll_cost_in_score=True,
        weight_policy="topdown_classif",
    )

    row, _ = _summary_row(
        scenario,
        status="complete",
        elapsed_seconds=1.0,
        book_path=book_path,
        rule_cache=pl.DataFrame({
            "symbol": ["NQ", "ES"],
            "asset_cls": ["equities", "equities"],
            "econ_subcls": ["equity_index", "equity_index"],
        }),
    )

    assert row["maximum_instrument_margin_usd"] == 1200.0
    assert row["book_margin_at_maximum_usd"] == 2000.0
    assert row["coverage_satisfied"] is True


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
    assert {scenario.score_threshold for scenario in scenarios} == {0.90}


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
    assert {
        scenario.maximum_instruments_per_subclass for scenario in scenarios
    } == {
        2, None
    }


def test_grid_sweeps_rank_count_ewma_halflives_for_yearly_reviews():
    """Rank-count smoothing is an explicit scheduled-selection dimension."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--selection-rebalance",
        "yearly",
        "--selection-membership-hysteresis",
        "rank",
        "--selection-rank-count-ewma-halflives",
        "1,3,5",
    ])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 3
    assert {
        scenario.effective_rank_count_ewma_halflife_reviews
        for scenario in scenarios
    } == {1.0, 3.0, 5.0}
    assert all(
        "_selyearly_hg31_eh0_mhrank_rb15_rhl" in scenario.scenario_id
        for scenario in scenarios
    )


def test_grid_sweeps_optional_hierarchy_coverage_minimums():
    """Coverage minima are explicit dimensions and contradictory bounds skip."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--min-instr-per-classes",
        "none,1,2",
        "--min-instr-per-subclasses",
        "none,1",
        "--selection-min-book-counts",
        "10,15",
        "--max-instr-per-classes",
        "1",
        "--max-instr-per-subclasses",
        "1",
    ])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 7
    assert {
        (scenario.minimum_instruments_per_class,
         scenario.minimum_instruments_per_subclass)
        for scenario in scenarios
    } == {(None, None), (None, 1), (1, None), (1, 1)}
    assert any(
        "_minc1_mins1_mb10_" in scenario.scenario_id
        for scenario in scenarios
    )
    assert {
        scenario.minimum_book_count
        for scenario in scenarios
        if scenario.minimum_instruments_per_class is not None
        or scenario.minimum_instruments_per_subclass is not None
    } == {10, 15}
    assert len({scenario.scenario_id for scenario in scenarios}) == 7


def test_grid_sweeps_only_active_hysteresis_dimensions():
    """Each membership mode expands only the controls that affect that mode."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--selection-rebalance",
        "yearly",
        "--selection-membership-hysteresis-modes",
        "none,rank,score,rank_score",
        "--selection-eligibility-hysteresis-pcts",
        "0,25",
        "--selection-rank-buffer-pcts",
        "10,15",
        "--selection-rank-count-ewma-halflives",
        "1,3",
        "--selection-book-score-buffer-pcts",
        "0,2",
    ])

    scenarios = build_scenarios(args)

    # none=2, rank=8, score=4, rank_score=16.
    assert len(scenarios) == 30
    assert len({scenario.scenario_id for scenario in scenarios}) == 30
    assert {
        scenario.selection_membership_hysteresis for scenario in scenarios
    } == {"none", "rank", "score", "rank_score"}


def test_grid_sweeps_greedy_score_acceptance_thresholds():
    """Acceptance thresholds are a first-class scenario dimension."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--score-thresholds",
        "0.90,0.95,0.975,0.99,1.00",
    ])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 5
    assert {scenario.score_threshold for scenario in scenarios} == {
        0.90, 0.95, 0.975, 0.99, 1.0
    }
    assert len({scenario.scenario_id for scenario in scenarios}) == 5


def test_grid_sweeps_full_contract_preference_with_hysteresis():
    """The route preference crosses membership modes as an independent axis."""
    args = parse_args([
        "--input",
        "screen.parquet",
        "--target-vols",
        "0.20",
        "--max-instr-margin-pcts",
        "20",
        "--roll-cost-modes",
        "include",
        "--selection-rebalance",
        "yearly",
        "--selection-membership-hysteresis-modes",
        "none,score",
        "--selection-prefer-full-contracts",
        "false,true",
    ])

    scenarios = build_scenarios(args)

    assert len(scenarios) == 4
    assert {scenario.selection_prefer_full_contracts for scenario in scenarios} == {
        False,
        True,
    }
    assert len({scenario.scenario_id for scenario in scenarios}) == 4


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
        "--cache",
        str(tmp_path / "cache"),
        "--selection-rebalance",
        "yearly",
        "--selection-membership-hysteresis",
        "rank",
        "--selection-prefer-full-contracts",
        "true",
        "--dry-run",
    ])

    command_text = commands_path.read_text(encoding="utf-8")
    assert summary_path is None
    assert commands_path.parent.parent == output
    assert commands_path.parent.name.startswith("selection_grid_")
    assert "--target-vol 0.2" in command_text
    assert "--max-instr-margin-pct 20.0" in command_text
    assert "--score-threshold 0.9" in command_text
    assert "--include-roll-cost-in-score" in command_text
    assert "--selection-rebalance yearly" in command_text
    assert "--selection-rank-buffer-pct 15.0" in command_text
    assert "--selection-rank-count-ewma-halflife-reviews 3.0" in command_text
    assert "--selection-prefer-full-contracts" in command_text
    assert "--selection-report-mode compact" in command_text
    assert not list(output.rglob("multi_speed_ewmac_selection_book_*.parquet"))


def test_live_grid_plan_keeps_measured_commands_offline(tmp_path: Path):
    """Only serial materialization may receive live IB connection flags."""
    source = tmp_path / "screen.parquet"
    ib_database = tmp_path / "ib.duckdb"
    output = tmp_path / "grid"
    pl.DataFrame({"symbol": ["MZC"]}).write_parquet(source)
    ib_database.write_bytes(b"dry-run fixture")

    commands_path, _ = run([
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
        "--cache",
        str(tmp_path / "cache"),
        "--ib-history-db",
        str(ib_database),
        "--ib-history-live",
        "missing",
        "--dry-run",
    ])

    command_text = commands_path.read_text(encoding="utf-8")
    expected_frozen = (
        commands_path.parent
        / ".selection_grid_inputs"
        / "ib_futures_history.duckdb"
    )
    assert f"--ib-history-db {expected_frozen}" in command_text
    assert "--ib-history-live" not in command_text
    assert "--ib-host" not in command_text
    assert "--ib-client-id" not in command_text


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
        "--cache",
        str(tmp_path / "cache"),
        "--dry-run",
    ])

    assert commands_path.parent == run_dir
    assert summary_path is None

"""Run Phase 2 construction-scenario grids from one cached selection screen.

This runner is deliberately a robustness tool rather than a performance
optimizer. It holds the executable market snapshot and historical inputs fixed,
varies explicit portfolio-construction assumptions, and saves each scenario's
normal Phase 2 audit reports in an isolated directory. The final summary reports
membership and feasibility; it does not rank scenarios by backtested returns.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from itertools import product
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from time import monotonic
from zoneinfo import ZoneInfo

import polars as pl

from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)
from derivatives_bt_engine.reports.formatting import (
    rename_public_report_columns,
    round_public_report,
)


REPORT_TIMEZONE = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class SelectionScenario:
    """One immutable set of Phase 2 portfolio-construction assumptions.

    Percent fields use human CLI units, so ``20`` means 20% of initial capital.
    Target volatility is a decimal, so ``0.20`` means a 20% annual target.
    """

    target_volatility: float
    maximum_instrument_margin_percent: float
    maximum_book_margin_percent: float
    maximum_weight_idm: float
    maximum_instruments_per_cluster: int | None
    include_roll_cost_in_score: bool
    weight_policy: str

    @property
    def scenario_id(self) -> str:
        """Return a stable filesystem-safe identifier for this scenario."""
        cluster = (
            "none"
            if self.maximum_instruments_per_cluster is None
            else str(self.maximum_instruments_per_cluster)
        )
        return "_".join((
            f"tv{_number_token(self.target_volatility)}",
            f"im{_number_token(self.maximum_instrument_margin_percent)}",
            f"bm{_number_token(self.maximum_book_margin_percent)}",
            f"wi{_number_token(self.maximum_weight_idm)}",
            f"cl{cluster}",
            f"roll{int(self.include_roll_cost_in_score)}",
            self.weight_policy,
        ))


def _number_token(value: float) -> str:
    """Return a compact decimal token without filesystem punctuation."""
    rendered = f"{value:.6f}".rstrip("0").rstrip(".")
    return rendered.replace("-", "m").replace(".", "p")


def _positive_float_list(value: str) -> tuple[float, ...]:
    """Parse a comma-separated list of unique positive floats."""
    values = tuple(dict.fromkeys(float(item.strip()) for item in value.split(",")))
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive numbers")
    return values


def _cluster_limit_list(value: str) -> tuple[int | None, ...]:
    """Parse comma-separated positive limits, accepting ``none`` as unlimited."""
    parsed: list[int | None] = []
    for raw in value.split(","):
        item = raw.strip().lower()
        if item in {"none", "unlimited"}:
            limit = None
        else:
            try:
                limit = int(item)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    "cluster limits must be positive integers or 'none'"
                ) from exc
            if limit <= 0:
                raise argparse.ArgumentTypeError(
                    "cluster limits must be positive integers or 'none'"
                )
        if limit not in parsed:
            parsed.append(limit)
    if not parsed:
        raise argparse.ArgumentTypeError("at least one cluster limit is required")
    return tuple(parsed)


def parse_args(argv=None) -> argparse.Namespace:
    """Parse the construction-grid command line."""
    parser = argparse.ArgumentParser(
        description=(
            "Run a Phase 2 robustness grid from one exact cached selection "
            "screen. Scenarios are compared by membership and feasibility, "
            "not treated as walk-forward performance tests."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Exact selection-screen Parquet to pin across every scenario.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Grid directory; defaults to "
            "results/selection_grid_<Chicago timestamp>."
        ),
    )
    parser.add_argument(
        "--pysystemtrade-db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
        help="Reviewed pysystemtrade history database used by Phase 2.",
    )
    parser.add_argument(
        "--initial-capital-usd",
        type=float,
        help="Optional fixed capital override; otherwise use the screen value.",
    )
    parser.add_argument(
        "--target-vols",
        type=_positive_float_list,
        default=(0.15, 0.20, 0.25),
        help="Comma-separated annual volatility targets (default: .15,.20,.25).",
    )
    parser.add_argument(
        "--max-instr-margin-pcts",
        type=_positive_float_list,
        default=(15.0, 20.0, 25.0),
        help="Comma-separated per-instrument margin caps (default: 15,20,25).",
    )
    parser.add_argument(
        "--max-book-margin-pcts",
        type=_positive_float_list,
        default=(75.0,),
        help="Comma-separated total book margin caps (default: 75).",
    )
    parser.add_argument(
        "--max-weight-idms",
        type=_positive_float_list,
        default=(0.20,),
        help="Comma-separated instrument weight-times-IDM caps (default: .20).",
    )
    parser.add_argument(
        "--max-instr-per-clusters",
        type=_cluster_limit_list,
        default=(None,),
        help="Comma-separated economic-subclass caps; use none for unlimited.",
    )
    parser.add_argument(
        "--roll-cost-modes",
        choices=("both", "include", "exclude"),
        default="both",
        help="Compare roll-cost attribution modes (default: both).",
    )
    parser.add_argument(
        "--weight-policy",
        choices=("topdown_classif", "legacy_handcraft", "equal"),
        default="topdown_classif",
        help="Fixed Phase 2 risk-weight policy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write and print the command plan without running Phase 2.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse completed scenario directories (default: true).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first failed scenario instead of recording it.",
    )
    return parser.parse_args(argv)


def build_scenarios(args: argparse.Namespace) -> list[SelectionScenario]:
    """Return the deterministic Cartesian product requested by the CLI."""
    roll_modes = {
        "both": (False, True),
        "include": (True,),
        "exclude": (False,),
    }[args.roll_cost_modes]
    scenarios = [
        SelectionScenario(
            target_volatility=target_volatility,
            maximum_instrument_margin_percent=instrument_margin,
            maximum_book_margin_percent=book_margin,
            maximum_weight_idm=weight_idm,
            maximum_instruments_per_cluster=cluster_limit,
            include_roll_cost_in_score=include_roll_cost,
            weight_policy=args.weight_policy,
        )
        for (
            target_volatility,
            instrument_margin,
            book_margin,
            weight_idm,
            cluster_limit,
            include_roll_cost,
        ) in product(
            args.target_vols,
            args.max_instr_margin_pcts,
            args.max_book_margin_pcts,
            args.max_weight_idms,
            args.max_instr_per_clusters,
            roll_modes,
        )
    ]
    return sorted(scenarios, key=lambda scenario: scenario.scenario_id)


def _selection_command(
    scenario: SelectionScenario,
    *,
    scenario_input: Path,
    candidates_output: Path,
    database: Path,
    initial_capital_usd: float | None,
) -> list[str]:
    """Build one auditable Phase 2 subprocess command."""
    command = [
        sys.executable,
        "-m",
        "derivatives_bt_engine.pipelines.phase2_selection",
        "--input",
        str(scenario_input),
        "--output",
        str(candidates_output),
        "--complete",
        "--pysystemtrade-db",
        str(database),
        "--target-vol",
        str(scenario.target_volatility),
        "--max-instr-margin-pct",
        str(scenario.maximum_instrument_margin_percent),
        "--max-book-margin-pct",
        str(scenario.maximum_book_margin_percent),
        "--max-weight-idm",
        str(scenario.maximum_weight_idm),
        "--weight-policy",
        scenario.weight_policy,
    ]
    if initial_capital_usd is not None:
        command.extend(("--initial-capital-usd", str(initial_capital_usd)))
    if scenario.maximum_instruments_per_cluster is not None:
        command.extend((
            "--max-instr-per-cluster",
            str(scenario.maximum_instruments_per_cluster),
        ))
    if scenario.include_roll_cost_in_score:
        command.append("--include-roll-cost-in-score")
    return command


def _latest_book(scenario_dir: Path) -> Path | None:
    """Return the newest completed Phase 2 book in a scenario directory."""
    books = sorted(scenario_dir.glob("pysystemtrade_selection_book_*.parquet"))
    return books[-1] if books else None


def _summary_row(
    scenario: SelectionScenario,
    *,
    status: str,
    elapsed_seconds: float,
    book_path: Path | None,
    screen: pl.DataFrame,
    error: str = "",
) -> tuple[dict[str, object], pl.DataFrame | None]:
    """Summarize one scenario and return its labelled membership frame."""
    row: dict[str, object] = {
        "scenario_id": scenario.scenario_id,
        "status": status,
        "target_volatility": scenario.target_volatility,
        "maximum_instrument_margin_percent": (
            scenario.maximum_instrument_margin_percent
        ),
        "maximum_book_margin_percent": scenario.maximum_book_margin_percent,
        "maximum_weight_idm": scenario.maximum_weight_idm,
        "maximum_instruments_per_cluster": (
            scenario.maximum_instruments_per_cluster
        ),
        "include_roll_cost_in_score": scenario.include_roll_cost_in_score,
        "weight_policy": scenario.weight_policy,
        "elapsed_seconds": elapsed_seconds,
        "book_path": str(book_path) if book_path is not None else "",
        "number_instruments": 0,
        "number_asset_classes": 0,
        "number_economic_subclasses": 0,
        "selected_symbols": "",
        "final_idm": None,
        "construction_score": None,
        "maximum_instrument_margin_usd": None,
        "book_margin_at_maximum_usd": None,
        "error": error,
    }
    if status != "complete" or book_path is None:
        return row, None

    book = pl.read_parquet(book_path)
    symbols = book.get_column("symbol").cast(pl.String).to_list()
    identity_columns = [
        name for name in ("symbol", "asset_cls", "econ_subcls")
        if name in screen.columns
    ]
    membership = book.join(
        screen.select(identity_columns).unique(subset=["symbol"]),
        on="symbol",
        how="left",
    ).with_columns(
        pl.lit(scenario.scenario_id).alias("scenario_id"),
    )
    row.update({
        "number_instruments": book.height,
        "number_asset_classes": (
            membership.get_column("asset_cls").drop_nulls().n_unique()
            if "asset_cls" in membership.columns else 0
        ),
        "number_economic_subclasses": (
            membership.get_column("econ_subcls").drop_nulls().n_unique()
            if "econ_subcls" in membership.columns else 0
        ),
        "selected_symbols": ",".join(symbols),
        "final_idm": _column_max(book, "idm"),
        "construction_score": _column_max(book, "score"),
        "maximum_instrument_margin_usd": _column_max(
            book, "margin_at_max_usd"
        ),
        "book_margin_at_maximum_usd": _column_max(
            book, "portfolio_margin_at_max_usd"
        ),
    })
    return row, membership


def _column_max(frame: pl.DataFrame, name: str) -> float | None:
    """Return a finite numeric column maximum when the report contains it."""
    if name not in frame.columns:
        return None
    values = frame.get_column(name).drop_nulls()
    return float(values.max()) if values.len() else None


def _attach_baseline_overlap(summary: pl.DataFrame) -> pl.DataFrame:
    """Add membership overlap against the central successful scenario.

    The central scenario is the successful run closest to 20% target
    volatility, 20% per-instrument margin, 75% book margin, 0.20 maximum
    weight-times-IDM, unlimited subclass count, and roll costs included.
    This is a stability reference, not an assertion that the scenario is best.
    """
    complete = summary.filter(pl.col("status") == "complete")
    if complete.is_empty():
        return summary.with_columns(
            pl.lit(False).alias("baseline_scenario"),
            pl.lit(None, dtype=pl.Float64).alias("jaccard_to_baseline"),
        )
    ranked = complete.with_columns(
        (
            (pl.col("target_volatility") - 0.20).abs()
            + (pl.col("maximum_instrument_margin_percent") - 20.0).abs() / 100.0
            + (pl.col("maximum_book_margin_percent") - 75.0).abs() / 100.0
            + (pl.col("maximum_weight_idm") - 0.20).abs()
            + pl.when(pl.col("maximum_instruments_per_cluster").is_null())
              .then(0.0).otherwise(1.0)
            + pl.when(pl.col("include_roll_cost_in_score"))
              .then(0.0).otherwise(1.0)
        ).alias("baseline_distance")
    ).sort("baseline_distance", "scenario_id")
    baseline_id = str(ranked.get_column("scenario_id")[0])
    baseline_symbols = set(
        str(ranked.get_column("selected_symbols")[0]).split(",")
    )

    jaccard: list[float | None] = []
    for status, serialized in summary.select(
        "status", "selected_symbols"
    ).iter_rows():
        if status != "complete":
            jaccard.append(None)
            continue
        symbols = set(str(serialized).split(","))
        union = baseline_symbols | symbols
        jaccard.append(
            len(baseline_symbols & symbols) / len(union) if union else 1.0
        )
    return summary.with_columns(
        (pl.col("scenario_id") == baseline_id).alias("baseline_scenario"),
        pl.Series("jaccard_to_baseline", jaccard, dtype=pl.Float64),
    )


def run(argv=None) -> tuple[Path, Path | None]:
    """Execute or print the requested grid and save typed comparison reports.

    Returns the command-plan path and, after execution, the summary Parquet
    path. Every scenario copies the small Phase 1 screen into its own directory
    so the existing Phase 2 writer naturally keeps all audit artifacts isolated.
    """
    args = parse_args(argv)
    source = args.input.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".parquet":
        raise ValueError("--input must be an exact existing Parquet report")
    database = args.pysystemtrade_db.expanduser().resolve()
    if args.initial_capital_usd is not None and args.initial_capital_usd <= 0:
        raise ValueError("--initial-capital-usd must be positive")

    stamp = datetime.now(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else (Path("results") / f"selection_grid_{stamp}").resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    screen = pl.read_parquet(source)
    scenarios = build_scenarios(args)

    planned: list[tuple[SelectionScenario, Path, list[str]]] = []
    for scenario in scenarios:
        scenario_dir = output_dir / scenario.scenario_id
        scenario_input = scenario_dir / "selection_screen.parquet"
        candidates_output = scenario_dir / "selection_candidates.parquet"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        if not scenario_input.exists():
            shutil.copy2(source, scenario_input)
        command = _selection_command(
            scenario,
            scenario_input=scenario_input,
            candidates_output=candidates_output,
            database=database,
            initial_capital_usd=args.initial_capital_usd,
        )
        planned.append((scenario, scenario_dir, command))

    commands_path = output_dir / "selection_grid_commands.sh"
    commands_path.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n"
        + "\n".join(shlex.join(command) for _, _, command in planned)
        + "\n",
        encoding="utf-8",
    )
    commands_path.chmod(0o744)
    print(f"Prepared {len(planned)} scenarios in {output_dir}")
    print(f"Saved command plan {commands_path}")
    if args.dry_run:
        for _, _, command in planned:
            print(shlex.join(command))
        return commands_path, None

    summary_rows: list[dict[str, object]] = []
    membership_frames: list[pl.DataFrame] = []
    for index, (scenario, scenario_dir, command) in enumerate(planned, start=1):
        existing_book = _latest_book(scenario_dir) if args.resume else None
        started = monotonic()
        status = "complete"
        error = ""
        book_path = existing_book
        if existing_book is None:
            print(f"[{index}/{len(planned)}] {scenario.scenario_id}")
            completed = subprocess.run(
                command,
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )
            (scenario_dir / "command_output.log").write_text(
                completed.stdout + completed.stderr,
                encoding="utf-8",
            )
            if completed.returncode:
                status = "failed"
                error = f"selection exited with status {completed.returncode}"
                if args.fail_fast:
                    raise RuntimeError(
                        f"{scenario.scenario_id}: {error}; see command_output.log"
                    )
            else:
                book_path = _latest_book(scenario_dir)
                if book_path is None:
                    status = "failed"
                    error = "selection completed without a book report"
        elapsed = monotonic() - started
        row, membership = _summary_row(
            scenario,
            status=status,
            elapsed_seconds=elapsed,
            book_path=book_path,
            screen=screen,
            error=error,
        )
        summary_rows.append(row)
        if membership is not None:
            membership_frames.append(membership)
        (scenario_dir / "scenario.json").write_text(
            json.dumps(row, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    summary = _attach_baseline_overlap(
        pl.DataFrame(summary_rows, infer_schema_length=None)
    )
    summary = round_public_report(rename_public_report_columns(summary))
    summary_path = output_dir / "selection_grid_summary.parquet"
    summary.write_parquet(summary_path)
    membership_path = output_dir / "selection_grid_membership.parquet"
    if membership_frames:
        membership = pl.concat(membership_frames, how="diagonal_relaxed")
        round_public_report(
            rename_public_report_columns(membership)
        ).write_parquet(membership_path)
    print(f"Saved grid summary {summary_path}")
    if membership_frames:
        print(f"Saved grid membership {membership_path}")
    return commands_path, summary_path


def main(argv=None) -> None:
    """CLI entry point for ``futures-selection-grid``."""
    run(argv)


if __name__ == "__main__":
    main()

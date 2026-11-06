"""Run construction-scenario grids from one cached rule-cost universe.

This runner is deliberately a robustness tool rather than a performance
optimizer. It holds the executable market snapshot and historical inputs fixed,
varies explicit portfolio-construction assumptions, and saves each scenario's
normal portfolio-selection audit reports in an isolated directory. The final summary reports
membership and feasibility; it does not rank scenarios by backtested returns.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)
from derivatives_bt_engine.data.ib_futures_dataloader import (
    DEFAULT_BROKER_MAPPING_PATH,
    DEFAULT_MAX_SOURCE_GAP_DAYS,
)
from derivatives_bt_engine.pipelines.portfolio_selection import (
    DEFAULT_SELECTION_MAX_HISTORY_GAP_DAYS,
)
from derivatives_bt_engine.reports.formatting import (
    rename_public_report_columns,
    round_public_report,
)


REPORT_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SelectionScenario:
    """One immutable set of portfolio-construction assumptions.

    Percent fields use human CLI units, so ``20`` means 20% of initial capital.
    Target volatility is a decimal, so ``0.20`` means a 20% annual target.
    """

    target_volatility: float
    maximum_instrument_margin_percent: float
    maximum_book_margin_percent: float
    maximum_weight_idm: float
    score_threshold: float
    maximum_instruments_per_class: int | None
    maximum_instruments_per_subclass: int | None
    maximum_asset_class_weight_percent: float | None
    maximum_economic_subclass_weight_percent: float | None
    include_roll_cost_in_score: bool
    weight_policy: str
    low_contract_penalty_scale: float = 0.0
    high_contract_penalty_scale: float = 0.0
    score_tie_tolerance_percent: float = 0.0
    tie_reference_book_count: int = 15
    selection_rebalance: str = "static"
    selection_max_history_gap_days: int = (
        DEFAULT_SELECTION_MAX_HISTORY_GAP_DAYS
    )
    selection_eligibility_hysteresis_percent: float = 0.0
    selection_membership_hysteresis: str = "none"
    selection_rank_buffer_percent: float = 15.0
    selection_rank_count_ewma_halflife_reviews: float | None = None
    selection_book_score_buffer_percent: float = 2.0

    @property
    def effective_rank_count_ewma_halflife_reviews(self) -> float | None:
        """Return the explicit or cadence-default rank-count half-life."""
        if self.selection_membership_hysteresis not in {"rank", "rank_score"}:
            return None
        if self.selection_rank_count_ewma_halflife_reviews is not None:
            return self.selection_rank_count_ewma_halflife_reviews
        if self.selection_rebalance == "yearly":
            return 3.0
        if self.selection_rebalance == "monthly":
            return 12.0
        return None

    @property
    def scenario_id(self) -> str:
        """Return a stable filesystem-safe identifier for this scenario."""
        subclass = (
            "none"
            if self.maximum_instruments_per_subclass is None
            else str(self.maximum_instruments_per_subclass)
        )
        tokens = [
            f"tv{_number_token(self.target_volatility)}",
            f"im{_number_token(self.maximum_instrument_margin_percent)}",
            f"bm{_number_token(self.maximum_book_margin_percent)}",
            f"wi{_number_token(self.maximum_weight_idm)}",
            f"st{_number_token(self.score_threshold)}",
        ]
        if (
            self.low_contract_penalty_scale
            or self.high_contract_penalty_scale
            or self.score_tie_tolerance_percent
            or self.tie_reference_book_count != 15
        ):
            tokens.extend((
                f"lp{_number_token(self.low_contract_penalty_scale)}",
                f"hp{_number_token(self.high_contract_penalty_scale)}",
                f"tie{_number_token(self.score_tie_tolerance_percent)}",
                f"refn{self.tie_reference_book_count}",
            ))
        if self.maximum_instruments_per_class is not None:
            tokens.append(f"ic{self.maximum_instruments_per_class}")
        if self.maximum_asset_class_weight_percent is not None:
            tokens.append(
                f"cw{_number_token(self.maximum_asset_class_weight_percent)}"
            )
        if self.maximum_economic_subclass_weight_percent is not None:
            tokens.append(
                "sw"
                + _number_token(
                    self.maximum_economic_subclass_weight_percent
                )
            )
        tokens.extend((
            f"cl{subclass}",
            f"roll{int(self.include_roll_cost_in_score)}",
            self.weight_policy,
        ))
        if self.selection_rebalance != "static":
            tokens.extend((
                f"sel{self.selection_rebalance}",
                f"hg{self.selection_max_history_gap_days}",
                "eh"
                f"{_number_token(self.selection_eligibility_hysteresis_percent)}",
                f"mh{self.selection_membership_hysteresis}",
            ))
            if self.selection_membership_hysteresis in {"rank", "rank_score"}:
                rank_count_halflife = (
                    self.effective_rank_count_ewma_halflife_reviews
                )
                tokens.extend((
                    "rb"
                    f"{_number_token(self.selection_rank_buffer_percent)}",
                    "rhl"
                    f"{_number_token(rank_count_halflife)}",
                ))
            if self.selection_membership_hysteresis in {"score", "rank_score"}:
                tokens.append(
                    "sb"
                    f"{_number_token(self.selection_book_score_buffer_percent)}"
                )
        return "_".join(tokens)


@dataclass(frozen=True)
class ScenarioExecution:
    """Outcome of one isolated selection CLI subprocess.

    ``elapsed_seconds`` measures only this scenario's subprocess wall time.
    A resumed scenario has zero elapsed time because the grid did not execute
    it during the current invocation.
    """

    status: str
    error: str
    book_path: Path | None
    elapsed_seconds: float


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


def _non_negative_float_list(value: str) -> tuple[float, ...]:
    """Parse a comma-separated list of unique non-negative floats."""
    try:
        values = tuple(
            dict.fromkeys(float(item.strip()) for item in value.split(","))
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "values must be comma-separated non-negative numbers"
        ) from exc
    if not values or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("values must be non-negative numbers")
    return values


def _percent_list(value: str) -> tuple[float, ...]:
    """Parse unique percentage values from zero through one hundred."""
    values = _non_negative_float_list(value)
    if any(item > 100 for item in values):
        raise argparse.ArgumentTypeError("percentage values must be in [0, 100]")
    return values


def _positive_int_list(value: str) -> tuple[int, ...]:
    """Parse a comma-separated list of unique positive integers."""
    try:
        values = tuple(
            dict.fromkeys(int(item.strip()) for item in value.split(","))
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "values must be comma-separated positive integers"
        ) from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive integers")
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


def _optional_percent_list(value: str) -> tuple[float | None, ...]:
    """Parse percentage caps, accepting ``none`` as an inactive limit."""
    parsed: list[float | None] = []
    for raw in value.split(","):
        item = raw.strip().lower()
        if item in {"none", "unlimited"}:
            limit = None
        else:
            try:
                limit = float(item)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    "weight caps must be percentages in (0, 100] or 'none'"
                ) from exc
            if not 0 < limit <= 100:
                raise argparse.ArgumentTypeError(
                    "weight caps must be percentages in (0, 100] or 'none'"
                )
        if limit not in parsed:
            parsed.append(limit)
    if not parsed:
        raise argparse.ArgumentTypeError("at least one weight cap is required")
    return tuple(parsed)


def _score_threshold_list(value: str) -> tuple[float, ...]:
    """Parse unique greedy acceptance thresholds from zero through one."""
    try:
        thresholds = tuple(
            dict.fromkeys(float(item.strip()) for item in value.split(","))
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "score thresholds must be comma-separated numbers in [0, 1]"
        ) from exc
    if not thresholds or any(not 0.0 <= item <= 1.0 for item in thresholds):
        raise argparse.ArgumentTypeError(
            "score thresholds must be comma-separated numbers in [0, 1]"
        )
    return thresholds


def _membership_mode_list(value: str) -> tuple[str, ...]:
    """Parse unique scheduled-selection membership modes."""
    allowed = {"none", "rank", "score", "rank_score"}
    modes = tuple(dict.fromkeys(item.strip() for item in value.split(",")))
    invalid = sorted(set(modes).difference(allowed))
    if not modes or invalid:
        raise argparse.ArgumentTypeError(
            "membership modes must be comma-separated values from "
            "none, rank, score, rank_score"
        )
    return modes


def parse_args(argv=None) -> argparse.Namespace:
    """Parse the construction-grid command line."""
    parser = argparse.ArgumentParser(
        description=(
            "Run a portfolio-construction robustness grid from one exact cached rule-cost "
            "report. Scenarios are compared by membership and feasibility, "
            "not treated as walk-forward performance tests."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Exact rule-cache Parquet to pin across every scenario.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Parent directory for timestamped grid-run folders; defaults to "
            "results/. Each run creates selection_grid_<Eastern timestamp>."
        ),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help=(
            "Use this exact existing/new grid-run directory. Intended for "
            "resuming a specific run; mutually exclusive with --output-dir."
        ),
    )
    parser.add_argument(
        "--history-db",
        "--pysystemtrade-db",
        dest="history_db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
        help="Historical signal database used to construct EWMAC forecasts.",
    )
    parser.add_argument(
        "--ib-history-db",
        type=Path,
        help=(
            "Optional stored IB dated-contract database forwarded to every "
            "scenario's final cash-performance report."
        ),
    )
    parser.add_argument(
        "--ib-history-live",
        choices=("missing", "update"),
        help="Live IB policy forwarded to each selection scenario.",
    )
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, action="append")
    parser.add_argument("--ib-client-id", type=int, default=24)
    parser.add_argument("--globex-db", type=Path, default=DEFAULT_GLOBEX_DB_PATH)
    parser.add_argument(
        "--broker-mapping", type=Path, default=DEFAULT_BROKER_MAPPING_PATH
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Required cache root shared by every complete selection scenario.",
    )
    parser.add_argument(
        "--max-source-gap-days",
        type=int,
        default=DEFAULT_MAX_SOURCE_GAP_DAYS,
    )
    parser.add_argument(
        "--initial-capital-usd",
        type=float,
        help=(
            "Optional fixed capital override; otherwise use a legacy cached "
            "value or the selection default."
        ),
    )
    parser.add_argument(
        "--selection-rebalance",
        choices=("static", "yearly", "monthly"),
        default="static",
        help=(
            "Fixed review cadence shared by every grid scenario (default: "
            "static)."
        ),
    )
    parser.add_argument(
        "--selection-max-history-gap-days",
        type=int,
        default=DEFAULT_SELECTION_MAX_HISTORY_GAP_DAYS,
        help="Maximum dated-history freshness gap shared by every scenario.",
    )
    parser.add_argument(
        "--selection-eligibility-hysteresis-pcts",
        "--selection-eligibility-hysteresis-pct",
        dest="selection_eligibility_hysteresis_pcts",
        type=_percent_list,
        default=(0.0,),
        help=(
            "Comma-separated incumbent eligibility hysteresis percentages "
            "(default: 0)."
        ),
    )
    parser.add_argument(
        "--selection-membership-hysteresis-modes",
        "--selection-membership-hysteresis",
        dest="selection_membership_hysteresis_modes",
        type=_membership_mode_list,
        default=("none",),
        help=(
            "Comma-separated scheduled membership modes from none, rank, "
            "score, rank_score (default: none)."
        ),
    )
    parser.add_argument(
        "--selection-rank-buffer-pcts",
        "--selection-rank-buffer-pct",
        dest="selection_rank_buffer_pcts",
        type=_percent_list,
        default=(15.0,),
        help="Comma-separated symmetric rank-band percentages (default: 15).",
    )
    parser.add_argument(
        "--selection-rank-count-ewma-halflives",
        "--selection-rank-count-ewma-halflife-reviews",
        dest="selection_rank_count_ewma_halflives",
        type=_positive_float_list,
        help=(
            "Optional comma-separated rank-count EWMA half-lives, in completed "
            "reviews. Omit for cadence defaults (3 yearly, 12 monthly)."
        ),
    )
    parser.add_argument(
        "--selection-book-score-buffer-pcts",
        "--selection-book-score-buffer-pct",
        dest="selection_book_score_buffer_pcts",
        type=_percent_list,
        default=(2.0,),
        help=(
            "Comma-separated prior-book score hurdle percentages "
            "(default: 2)."
        ),
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
        "--score-thresholds",
        type=_score_threshold_list,
        default=(0.90,),
        help=(
            "Comma-separated greedy acceptance floors as fractions of the best "
            "score seen (default: .90)."
        ),
    )
    parser.add_argument(
        "--low-con-penalty-scales",
        dest="low_contract_penalty_scales",
        type=_non_negative_float_list,
        default=(0.0,),
        help="Comma-separated low-position SR penalty scales (default: 0).",
    )
    parser.add_argument(
        "--high-con-penalty-scales",
        dest="high_contract_penalty_scales",
        type=_non_negative_float_list,
        default=(0.0,),
        help="Comma-separated high-position SR penalty scales (default: 0).",
    )
    parser.add_argument(
        "--score-tie-tolerance-pcts",
        dest="score_tie_tolerance_percents",
        type=_percent_list,
        default=(0.0,),
        help=(
            "Comma-separated within-iteration near-tie windows in percent "
            "(default: 0; suggested: .25,.5,1)."
        ),
    )
    parser.add_argument(
        "--tie-reference-book-counts",
        dest="tie_reference_book_counts",
        type=_positive_int_list,
        default=(15,),
        help=(
            "Comma-separated estimated final book counts for stable equal-"
            "weight candidate position comparisons (default: 15)."
        ),
    )
    parser.add_argument(
        "--max-instr-per-subclasses",
        "--max-instr-per-clusters",
        dest="max_instr_per_subclasses",
        type=_cluster_limit_list,
        default=(None,),
        help=(
            "Comma-separated economic-subclass count caps; use none for "
            "unlimited. The cluster spelling remains a compatibility alias."
        ),
    )
    parser.add_argument(
        "--max-instr-per-classes",
        type=_cluster_limit_list,
        default=(None,),
        help="Comma-separated asset-class count caps; use none for unlimited.",
    )
    parser.add_argument(
        "--max-class-weight-pcts",
        type=_optional_percent_list,
        default=(None,),
        help="Comma-separated asset-class weight caps; use none for unlimited.",
    )
    parser.add_argument(
        "--max-subclass-weight-pcts",
        type=_optional_percent_list,
        default=(None,),
        help=(
            "Comma-separated economic-subclass weight caps; use none for "
            "unlimited."
        ),
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
        help="Fixed portfolio risk-weight policy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write and print the command plan without running selection.",
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
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help=(
            "Maximum concurrent selection subprocesses (default: 1). "
            "Values above one run a serial cache warm-up scenario before "
            "starting the bounded worker pool."
        ),
    )
    return parser.parse_args(argv)


def build_scenarios(args: argparse.Namespace) -> list[SelectionScenario]:
    """Return the deterministic Cartesian product requested by the CLI."""
    roll_modes = {
        "both": (False, True),
        "include": (True,),
        "exclude": (False,),
    }[args.roll_cost_modes]
    hysteresis_variants: list[
        tuple[str, float, float, float | None, float]
    ] = []
    for membership_mode in args.selection_membership_hysteresis_modes:
        rank_active = membership_mode in {"rank", "rank_score"}
        score_active = membership_mode in {"score", "rank_score"}
        rank_buffers = (
            args.selection_rank_buffer_pcts if rank_active else (15.0,)
        )
        if rank_active:
            rank_count_halflives = (
                args.selection_rank_count_ewma_halflives or (None,)
            )
        else:
            rank_count_halflives = (None,)
        score_buffers = (
            args.selection_book_score_buffer_pcts
            if score_active else (2.0,)
        )
        hysteresis_variants.extend(
            (
                membership_mode,
                eligibility_buffer,
                rank_buffer,
                rank_count_halflife,
                score_buffer,
            )
            for (
                eligibility_buffer,
                rank_buffer,
                rank_count_halflife,
                score_buffer,
            ) in product(
                args.selection_eligibility_hysteresis_pcts,
                rank_buffers,
                rank_count_halflives,
                score_buffers,
            )
        )
    scenarios = [
        SelectionScenario(
            target_volatility=target_volatility,
            maximum_instrument_margin_percent=instrument_margin,
            maximum_book_margin_percent=book_margin,
            maximum_weight_idm=weight_idm,
            score_threshold=score_threshold,
            low_contract_penalty_scale=low_contract_penalty_scale,
            high_contract_penalty_scale=high_contract_penalty_scale,
            score_tie_tolerance_percent=score_tie_tolerance_percent,
            tie_reference_book_count=tie_reference_book_count,
            maximum_instruments_per_class=class_count_limit,
            maximum_instruments_per_subclass=subclass_count_limit,
            maximum_asset_class_weight_percent=class_weight_limit,
            maximum_economic_subclass_weight_percent=subclass_weight_limit,
            include_roll_cost_in_score=include_roll_cost,
            weight_policy=args.weight_policy,
            selection_rebalance=args.selection_rebalance,
            selection_max_history_gap_days=(
                args.selection_max_history_gap_days
            ),
            selection_eligibility_hysteresis_percent=(
                eligibility_buffer
            ),
            selection_membership_hysteresis=membership_mode,
            selection_rank_buffer_percent=rank_buffer,
            selection_rank_count_ewma_halflife_reviews=(
                rank_count_halflife
            ),
            selection_book_score_buffer_percent=score_buffer,
        )
        for (
            target_volatility,
            instrument_margin,
            book_margin,
            weight_idm,
            score_threshold,
            low_contract_penalty_scale,
            high_contract_penalty_scale,
            score_tie_tolerance_percent,
            tie_reference_book_count,
            class_count_limit,
            subclass_count_limit,
            class_weight_limit,
            subclass_weight_limit,
            include_roll_cost,
            hysteresis_variant,
        ) in product(
            args.target_vols,
            args.max_instr_margin_pcts,
            args.max_book_margin_pcts,
            args.max_weight_idms,
            args.score_thresholds,
            args.low_contract_penalty_scales,
            args.high_contract_penalty_scales,
            args.score_tie_tolerance_percents,
            args.tie_reference_book_counts,
            args.max_instr_per_classes,
            args.max_instr_per_subclasses,
            args.max_class_weight_pcts,
            args.max_subclass_weight_pcts,
            roll_modes,
            hysteresis_variants,
        )
        for (
            membership_mode,
            eligibility_buffer,
            rank_buffer,
            rank_count_halflife,
            score_buffer,
        ) in (hysteresis_variant,)
    ]
    return sorted(scenarios, key=lambda scenario: scenario.scenario_id)


def _selection_command(
    scenario: SelectionScenario,
    *,
    scenario_input: Path,
    candidates_output: Path,
    database: Path,
    ib_history_database: Path | None,
    globex_database: Path,
    broker_mapping: Path,
    cache: Path,
    max_source_gap_days: int,
    ib_history_live: str | None,
    ib_host: str,
    ib_ports: list[int] | None,
    ib_client_id: int,
    initial_capital_usd: float | None,
) -> list[str]:
    """Build one auditable portfolio-selection subprocess command."""
    command = [
        sys.executable,
        "-m",
        "derivatives_bt_engine.pipelines.portfolio_selection",
        "--input",
        str(scenario_input),
        "--output",
        str(candidates_output),
        "--complete",
        "--history-db",
        str(database),
        "--target-vol",
        str(scenario.target_volatility),
        "--max-instr-margin-pct",
        str(scenario.maximum_instrument_margin_percent),
        "--max-book-margin-pct",
        str(scenario.maximum_book_margin_percent),
        "--max-weight-idm",
        str(scenario.maximum_weight_idm),
        "--score-threshold",
        str(scenario.score_threshold),
        "--low-con-penalty-scale",
        str(scenario.low_contract_penalty_scale),
        "--high-con-penalty-scale",
        str(scenario.high_contract_penalty_scale),
        "--score-tie-tolerance-pct",
        str(scenario.score_tie_tolerance_percent),
        "--tie-reference-book-count",
        str(scenario.tie_reference_book_count),
        "--weight-policy",
        scenario.weight_policy,
        "--cache",
        str(cache),
    ]
    if initial_capital_usd is not None:
        command.extend(("--initial-capital-usd", str(initial_capital_usd)))
    if scenario.selection_rebalance != "static":
        command.extend((
            "--selection-rebalance",
            scenario.selection_rebalance,
            "--selection-max-history-gap-days",
            str(scenario.selection_max_history_gap_days),
            "--selection-eligibility-hysteresis-pct",
            str(scenario.selection_eligibility_hysteresis_percent),
            "--selection-membership-hysteresis",
            scenario.selection_membership_hysteresis,
        ))
        if scenario.selection_membership_hysteresis in {"rank", "rank_score"}:
            command.extend((
                "--selection-rank-buffer-pct",
                str(scenario.selection_rank_buffer_percent),
                "--selection-rank-count-ewma-halflife-reviews",
                str(scenario.effective_rank_count_ewma_halflife_reviews),
            ))
        if scenario.selection_membership_hysteresis in {"score", "rank_score"}:
            command.extend((
                "--selection-book-score-buffer-pct",
                str(scenario.selection_book_score_buffer_percent),
            ))
    if ib_history_database is not None:
        command.extend((
            "--ib-history-db",
            str(ib_history_database),
            "--globex-db",
            str(globex_database),
            "--broker-mapping",
            str(broker_mapping),
            "--max-source-gap-days",
            str(max_source_gap_days),
        ))
        if ib_history_live is not None:
            command.extend(("--ib-history-live", ib_history_live))
            command.extend(("--ib-host", ib_host))
            command.extend(("--ib-client-id", str(ib_client_id)))
            for ib_port in ib_ports or []:
                command.extend(("--ib-port", str(ib_port)))
    if scenario.maximum_instruments_per_class is not None:
        command.extend((
            "--max-instr-per-class",
            str(scenario.maximum_instruments_per_class),
        ))
    if scenario.maximum_instruments_per_subclass is not None:
        command.extend((
            "--max-instr-per-subclass",
            str(scenario.maximum_instruments_per_subclass),
        ))
    if scenario.maximum_asset_class_weight_percent is not None:
        command.extend((
            "--max-class-weight-pct",
            str(scenario.maximum_asset_class_weight_percent),
        ))
    if scenario.maximum_economic_subclass_weight_percent is not None:
        command.extend((
            "--max-subclass-weight-pct",
            str(scenario.maximum_economic_subclass_weight_percent),
        ))
    if scenario.include_roll_cost_in_score:
        command.append("--include-roll-cost-in-score")
    return command


def _latest_book(scenario_dir: Path) -> Path | None:
    """Return the newest completed selection book, including legacy filenames."""
    books = sorted(scenario_dir.glob("multi_speed_ewmac_selection_book_*.parquet"))
    if not books:
        books = sorted(scenario_dir.glob("pysystemtrade_selection_book_*.parquet"))
    return books[-1] if books else None


def _summary_row(
    scenario: SelectionScenario,
    *,
    status: str,
    elapsed_seconds: float,
    book_path: Path | None,
    rule_cache: pl.DataFrame,
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
        "score_threshold": scenario.score_threshold,
        "low_contract_penalty_scale": scenario.low_contract_penalty_scale,
        "high_contract_penalty_scale": scenario.high_contract_penalty_scale,
        "score_tie_tolerance_percent": scenario.score_tie_tolerance_percent,
        "tie_ref_book_count": scenario.tie_reference_book_count,
        "maximum_instruments_per_class": scenario.maximum_instruments_per_class,
        "maximum_instruments_per_subclass": (
            scenario.maximum_instruments_per_subclass
        ),
        "maximum_asset_class_weight_percent": (
            scenario.maximum_asset_class_weight_percent
        ),
        "maximum_economic_subclass_weight_percent": (
            scenario.maximum_economic_subclass_weight_percent
        ),
        "include_roll_cost_in_score": scenario.include_roll_cost_in_score,
        "weight_policy": scenario.weight_policy,
        "selection_rebalance": scenario.selection_rebalance,
        "selection_max_history_gap_days": (
            scenario.selection_max_history_gap_days
        ),
        "selection_eligibility_hysteresis_percent": (
            scenario.selection_eligibility_hysteresis_percent
        ),
        "selection_membership_hysteresis": (
            scenario.selection_membership_hysteresis
        ),
        "selection_rank_buffer_percent": (
            scenario.selection_rank_buffer_percent
            if scenario.selection_membership_hysteresis
            in {"rank", "rank_score"}
            else None
        ),
        "selection_rank_count_ewma_halflife_reviews": (
            scenario.effective_rank_count_ewma_halflife_reviews
        ),
        "selection_book_score_buffer_percent": (
            scenario.selection_book_score_buffer_percent
            if scenario.selection_membership_hysteresis
            in {"score", "rank_score"}
            else None
        ),
        "elapsed_seconds": elapsed_seconds,
        "book_path": str(book_path) if book_path is not None else "",
        "number_instruments": 0,
        "number_asset_classes": 0,
        "number_economic_subclasses": 0,
        "selected_symbols": "",
        "final_idm": None,
        "construction_score": None,
        "base_construction_score": None,
        "low_contract_weight": None,
        "band_contract_weight": None,
        "high_contract_weight": None,
        "maximum_instrument_margin_usd": None,
        "book_margin_at_maximum_usd": None,
        "error": error,
    }
    if status != "complete" or book_path is None:
        return row, None

    book = pl.read_parquet(book_path)
    construction_score = _column_max(book, "score")
    if construction_score is None:
        construction_score = _score_from_selection_passes(book_path.parent)
    symbols = book.get_column("symbol").cast(pl.String).to_list()
    identity_columns = [
        name for name in ("symbol", "asset_cls", "econ_subcls")
        if name in rule_cache.columns
    ]
    membership = book.join(
        rule_cache.select(identity_columns).unique(subset=["symbol"]),
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
        "construction_score": construction_score,
        "base_construction_score": _column_max(book, "base_score"),
        "low_contract_weight": _column_max(book, "low_con_weight"),
        "band_contract_weight": _column_max(book, "band_con_weight"),
        "high_contract_weight": _column_max(book, "high_con_weight"),
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


def _score_from_selection_passes(scenario_dir: Path) -> float | None:
    """Recover the final book score from accepted-pass reports.

    Older selection-book Parquets omitted the portfolio-level score, while
    their accepted-pass reports retained it. This keeps resumed grid scenarios
    auditable without requiring those selections to be rerun.
    """
    reports = sorted(scenario_dir.glob("*selection_passes_*.parquet"))
    if not reports:
        return None
    passes = pl.read_parquet(reports[-1])
    if "score" not in passes.columns or passes.is_empty():
        return None
    step_column = (
        "selection_step" if "selection_step" in passes.columns
        else "iteration" if "iteration" in passes.columns
        else None
    )
    if step_column is not None:
        last_step = passes.get_column(step_column).max()
        passes = passes.filter(pl.col(step_column) == last_step)
    scores = passes.get_column("score").drop_nulls()
    return float(scores[0]) if scores.len() else None


def _attach_baseline_overlap(summary: pl.DataFrame) -> pl.DataFrame:
    """Add membership overlap against the central successful scenario.

    The central scenario is the successful run closest to 20% target
    volatility, 20% per-instrument margin, 75% book margin, 0.20 maximum
    weight-times-IDM, 0.90 score threshold, unlimited class and subclass
    counts and weights, and roll costs included.
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
            + (pl.col("score_threshold") - 0.90).abs()
            + pl.col("low_contract_penalty_scale").abs()
            + pl.col("high_contract_penalty_scale").abs()
            + pl.col("score_tie_tolerance_percent").abs() / 100.0
            + (pl.col("tie_ref_book_count") - 15).abs() / 15.0
            + pl.when(pl.col("maximum_instruments_per_class").is_null())
              .then(0.0).otherwise(1.0)
            + pl.when(pl.col("maximum_instruments_per_subclass").is_null())
              .then(0.0).otherwise(1.0)
            + pl.when(pl.col("maximum_asset_class_weight_percent").is_null())
              .then(0.0).otherwise(1.0)
            + pl.when(
                pl.col("maximum_economic_subclass_weight_percent").is_null()
            )
              .then(0.0).otherwise(1.0)
            + pl.when(pl.col("include_roll_cost_in_score"))
              .then(0.0).otherwise(1.0)
            + pl.col("selection_eligibility_hysteresis_percent") / 100.0
            + pl.when(pl.col("selection_membership_hysteresis") == "none")
              .then(0.0)
              .when(
                  pl.col("selection_membership_hysteresis") == "rank_score"
              )
              .then(2.0)
              .otherwise(1.0)
            + pl.when(pl.col("selection_rank_buffer_percent").is_null())
              .then(0.0)
              .otherwise(
                  (pl.col("selection_rank_buffer_percent") - 15.0).abs()
                  / 15.0
              )
            + pl.when(
                pl.col("selection_book_score_buffer_percent").is_null()
            )
              .then(0.0)
              .otherwise(
                  (
                      pl.col("selection_book_score_buffer_percent") - 2.0
                  ).abs()
                  / 2.0
              )
            + pl.when(
                pl.col(
                    "selection_rank_count_ewma_halflife_reviews"
                ).is_null()
            )
              .then(0.0)
              .when(pl.col("selection_rebalance") == "yearly")
              .then(
                  (
                      pl.col(
                          "selection_rank_count_ewma_halflife_reviews"
                      )
                      - 3.0
                  ).abs()
                  / 3.0
              )
              .when(pl.col("selection_rebalance") == "monthly")
              .then(
                  (
                      pl.col(
                          "selection_rank_count_ewma_halflife_reviews"
                      )
                      - 12.0
                  ).abs()
                  / 12.0
              )
              .otherwise(0.0)
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


def _execute_selection_scenario(
    index: int,
    total: int,
    scenario: SelectionScenario,
    scenario_dir: Path,
    command: list[str],
) -> ScenarioExecution:
    """Run one selection command and persist its combined process output.

    The command is passed directly as an argument vector, never through a
    shell. Each worker owns a distinct scenario directory, so report and log
    writes do not overlap across concurrent subprocesses.
    """
    print(f"[{index}/{total}] {scenario.scenario_id}", flush=True)
    started = monotonic()
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
    book_path: Path | None = None
    status = "complete"
    error = ""
    if completed.returncode:
        status = "failed"
        error = f"selection exited with status {completed.returncode}"
    else:
        book_path = _latest_book(scenario_dir)
        if book_path is None:
            status = "failed"
            error = "selection completed without a book report"
    return ScenarioExecution(
        status=status,
        error=error,
        book_path=book_path,
        elapsed_seconds=monotonic() - started,
    )


def _execute_planned_scenarios(
    planned: list[tuple[SelectionScenario, Path, list[str]]],
    *,
    resume: bool,
    fail_fast: bool,
    max_workers: int,
) -> dict[int, ScenarioExecution]:
    """Execute planned scenarios serially or in a bounded subprocess pool.

    The executor uses threads only to supervise already-isolated CLI child
    processes. This avoids an unnecessary Python process wrapped around every
    selection subprocess and avoids forking a parent that has initialized
    Polars or DuckDB. When concurrency is requested, the first incomplete
    scenario runs alone to populate shared immutable history artifacts before
    the remaining scenarios read that cache concurrently.

    Returns outcomes keyed by the zero-based position in ``planned`` so final
    public reports remain deterministic regardless of completion order.
    """
    results: dict[int, ScenarioExecution] = {}
    pending: list[tuple[int, SelectionScenario, Path, list[str]]] = []
    total = len(planned)
    for offset, (scenario, scenario_dir, command) in enumerate(planned):
        existing_book = _latest_book(scenario_dir) if resume else None
        if existing_book is not None:
            results[offset] = ScenarioExecution(
                status="complete",
                error="",
                book_path=existing_book,
                elapsed_seconds=0.0,
            )
        else:
            pending.append((offset, scenario, scenario_dir, command))

    def execute(
        item: tuple[int, SelectionScenario, Path, list[str]],
    ) -> tuple[int, ScenarioExecution]:
        offset, scenario, scenario_dir, command = item
        outcome = _execute_selection_scenario(
            offset + 1,
            total,
            scenario,
            scenario_dir,
            command,
        )
        return offset, outcome

    def retain(offset: int, outcome: ScenarioExecution) -> None:
        results[offset] = outcome
        if fail_fast and outcome.status == "failed":
            scenario = planned[offset][0]
            raise RuntimeError(
                f"{scenario.scenario_id}: {outcome.error}; "
                "see command_output.log"
            )

    # One complete selection loads the common history universe and prevents
    # simultaneous first-writer races in shared source-history cache bundles.
    if max_workers > 1 and len(pending) > 1:
        offset, outcome = execute(pending.pop(0))
        retain(offset, outcome)

    if max_workers == 1 or len(pending) <= 1:
        for item in pending:
            offset, outcome = execute(item)
            retain(offset, outcome)
        return results

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(pending)),
        thread_name_prefix="selection-grid",
    ) as executor:
        futures = {executor.submit(execute, item): item[0] for item in pending}
        for future in as_completed(futures):
            try:
                offset, outcome = future.result()
                retain(offset, outcome)
            except Exception:
                for queued in futures:
                    queued.cancel()
                raise
    return results


def run(argv=None) -> tuple[Path, Path | None]:
    """Execute or print the requested grid and save typed comparison reports.

    Returns the command-plan path and, after execution, the summary Parquet
    path. Every scenario copies the small rule cache into its own directory so
    the selection writer naturally keeps all audit artifacts isolated.
    """
    args = parse_args(argv)
    if args.cache is None:
        raise ValueError("--cache is required for selection grid runs")
    if args.output_dir is not None and args.run_dir is not None:
        raise ValueError("--output-dir and --run-dir cannot be used together")
    source = args.input.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".parquet":
        raise ValueError("--input must be an exact existing Parquet report")
    database = args.history_db.expanduser().resolve()
    ib_history_database = (
        args.ib_history_db.expanduser().resolve()
        if args.ib_history_db is not None
        else None
    )
    if args.initial_capital_usd is not None and args.initial_capital_usd <= 0:
        raise ValueError("--initial-capital-usd must be positive")
    if args.max_workers < 1:
        raise ValueError("--max-workers must be at least one")
    if args.max_source_gap_days < 0:
        raise ValueError("--max-source-gap-days must be nonnegative")
    if args.selection_max_history_gap_days < 0:
        raise ValueError("--selection-max-history-gap-days must be nonnegative")
    if (
        args.selection_rebalance == "static"
        and (
            args.selection_membership_hysteresis_modes != ("none",)
            or args.selection_eligibility_hysteresis_pcts != (0.0,)
        )
    ):
        raise ValueError(
            "selection hysteresis requires yearly or monthly rebalancing"
        )
    membership_modes = set(args.selection_membership_hysteresis_modes)
    rank_mode_active = bool(
        membership_modes.intersection({"rank", "rank_score"})
    )
    score_mode_active = bool(
        membership_modes.intersection({"score", "rank_score"})
    )
    if not rank_mode_active and (
        args.selection_rank_count_ewma_halflives is not None
        or args.selection_rank_buffer_pcts != (15.0,)
    ):
        raise ValueError(
            "rank-buffer percentages and count half-lives require rank or "
            "rank_score membership hysteresis"
        )
    if (
        not score_mode_active
        and args.selection_book_score_buffer_pcts != (2.0,)
    ):
        raise ValueError(
            "score-buffer percentages require score or rank_score membership "
            "hysteresis"
        )
    if args.ib_history_live is not None and ib_history_database is None:
        raise ValueError("--ib-history-live requires --ib-history-db")

    stamp = datetime.now(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    if args.run_dir is not None:
        output_dir = args.run_dir.expanduser().resolve()
    else:
        output_root = (
            args.output_dir.expanduser().resolve()
            if args.output_dir is not None
            else Path("results").resolve()
        )
        output_dir = output_root / f"selection_grid_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    rule_cache = pl.read_parquet(source)
    scenarios = build_scenarios(args)

    planned: list[tuple[SelectionScenario, Path, list[str]]] = []
    for scenario in scenarios:
        scenario_dir = output_dir / scenario.scenario_id
        scenario_input = scenario_dir / "multi_speed_ewmac_rule_cache.parquet"
        candidates_output = scenario_dir / "multi_speed_ewmac_selection_candidates.parquet"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        if not scenario_input.exists():
            shutil.copy2(source, scenario_input)
        command = _selection_command(
            scenario,
            scenario_input=scenario_input,
            candidates_output=candidates_output,
            database=database,
            ib_history_database=ib_history_database,
            globex_database=args.globex_db.expanduser().resolve(),
            broker_mapping=args.broker_mapping.expanduser().resolve(),
            cache=args.cache.expanduser().resolve(),
            max_source_gap_days=args.max_source_gap_days,
            ib_history_live=args.ib_history_live,
            ib_host=args.ib_host,
            ib_ports=args.ib_port,
            ib_client_id=args.ib_client_id,
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

    executions = _execute_planned_scenarios(
        planned,
        resume=args.resume,
        fail_fast=args.fail_fast,
        max_workers=args.max_workers,
    )
    summary_rows: list[dict[str, object]] = []
    membership_frames: list[pl.DataFrame] = []
    for offset, (scenario, scenario_dir, _) in enumerate(planned):
        outcome = executions[offset]
        row, membership = _summary_row(
            scenario,
            status=outcome.status,
            elapsed_seconds=outcome.elapsed_seconds,
            book_path=outcome.book_path,
            rule_cache=rule_cache,
            error=outcome.error,
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

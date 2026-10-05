"""Notebook helpers for inspecting Phase 1 futures cost rankings."""

from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl


DEFAULT_REPORT_PATTERNS = (
    "pysystemtrade_cost_phase1_ib_*.csv",
    "pysystemtrade_cost_phase1_*.csv",
    "futures_cost_risk_*.csv",
)

RANK_COLUMNS = {
    "cost": "cost_rank_in_asset_cls",
    "affordability": "afford_rank_in_asset_cls",
    "notional": "notional_rank_in_asset_cls",
}

LEGACY_RANK_COLUMNS = {
    "cost": "cost_rank_in_asset_class",
    "affordability": "affordability_rank_in_asset_class",
    "notional": "notional_rank_in_asset_class",
}

DEFAULT_VIEW_COLUMNS = (
    "symbol",
    "hist_instr_code",
    "desc",
    "asset_cls",
    "region",
    "exec_prof",
    "exec_elig",
    "exec_restrict_reason",
    "cost_rank_in_asset_cls",
    "afford_rank_in_asset_cls",
    "notional_rank_in_asset_cls",
    "trade_sr",
    "spread_pts",
    "spread_src",
    "cur_px",
    "ccy",
    "ib_mult",
    "px_magnifier",
    "mult",
    "notional",
    "ann_dvol",
    "avg_daily_volume",
    "risk_traded_usd_day",
    "mkt_risk_vol_usd_day",
    "pct_mkt_volume",
    "volume_elig",
    "risk_volume_elig",
    "cost_elig",
    "size_elig",
    "data_elig",
    "sel_bucket",
    "phase2_elig",
    "phase2_excl",
    "afford_cluster_role",
    "counts_toward_main_instr_min",
    "afford_scen_cap",
    "afford_scen_idm",
    "afford_min_main_instrs",
    "afford_main_cluster_count",
    "equal_wt_dvol_budget",
    "equal_wt_avg_cons",
    "equal_wt_meets_min_cons",
    "main_instr_afford_for_scen",
    "min_cap_usd_full_wt_idm1",
    "min_cap_usd_equal_wt",
    "elig_ewmac_rule_count",
    "elig_ewmac_rules",
    "pool_role",
    "incl_default_pool",
    "ib_avail",
)

PHASE2_STEP1_GATE_COLUMNS = (
    "cost_elig",
    "size_elig",
    "liq_elig",
    "data_elig",
    "instr_has_elig_ewmac_rule",
    "exec_elig",
    "phase2_elig",
)

PHASE2_STEP1_COLUMNS = (
    "symbol",
    "signal_symbol",
    "hist_instr_code",
    "desc",
    "asset_cls",
    "region",
    "econ_fam_id",
    "roll_policy_id",
    "dup_grp_id",
    "pool_role",
    "rep_instr",
    "exec_prof",
    "con_id",
    "expiry",
    "ib_symbol",
    "ib_exch",
    "ib_avail",
    "cur_px",
    "ccy",
    "mult",
    "notional",
    "ann_dvol",
    "vol_src",
    "avg_daily_volume",
    "volume_n",
    "volume_start",
    "volume_end",
    "volume_src",
    "risk_traded_usd_day",
    "mkt_risk_vol_usd_day",
    "pct_mkt_volume",
    "min_daily_volume",
    "max_pct_mkt_volume",
    "volume_elig",
    "risk_volume_elig",
    "liq_elig",
    "trade_sr",
    "cost_lim_sr",
    "cost_elig",
    "max_ann_dvol",
    "size_elig",
    "data_elig",
    "elig_ewmac_rule_count",
    "elig_ewmac_rules",
    "instr_has_elig_ewmac_rule",
    "exec_elig",
    "phase2_elig",
    "report_ts_ct",
)


def phase2_step1_candidates(report: pl.DataFrame) -> pl.DataFrame:
    """Select Phase 2 candidates using only gates saved by Phase 1.

    This function deliberately performs no market-data request and no metric
    calculation. It filters the complete Phase 1 audit using its persisted
    cost, dollar-vol size, liquidity, data, rule, execution, and IB contract
    decisions, then returns the compact columns needed by the next stage.
    """
    required = {*PHASE2_STEP1_GATE_COLUMNS, "ib_avail", "symbol"}
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "Phase 1 report is missing Step 1 selection fields "
            f"{missing}; rerun the current futures-cost-risk audit"
        )

    gate = pl.lit(True)
    for column in PHASE2_STEP1_GATE_COLUMNS:
        gate &= pl.col(column).fill_null(False)
    gate &= pl.col("ib_avail").fill_null("") == "contract_qualified"

    selected = report.filter(gate)
    columns = [name for name in PHASE2_STEP1_COLUMNS if name in selected.columns]
    selected = selected.select(columns)
    sort_columns = [
        name for name in ("asset_cls", "symbol") if name in selected.columns
    ]
    return selected.sort(sort_columns) if sort_columns else selected


def phase2_search_universe(
    report: pl.DataFrame,
    *,
    eligible_column: str = "phase2_elig",
) -> pl.DataFrame:
    """Return only rows that passed the Phase 1 hard pre-selection gates."""
    if eligible_column not in report.columns and eligible_column == "phase2_elig":
        eligible_column = "phase2_eligible"
    if eligible_column not in report.columns:
        raise ValueError(
            f"Report has no {eligible_column} column; rerun the Phase 1 audit"
        )
    filtered = report.filter(pl.col(eligible_column).fill_null(False))
    sort_columns = [
        name for name in ("asset_cls", "asset_class", "symbol")
        if name in filtered.columns
    ]
    return filtered.sort(sort_columns) if sort_columns else filtered


def find_latest_phase1_cost_report(
    report_dir: Path | str = ".",
    *,
    patterns: Sequence[str] = DEFAULT_REPORT_PATTERNS,
) -> Path:
    """Find the newest Phase 1 CSV in ``report_dir`` or its results folder."""
    root = Path(report_dir).expanduser()
    search_dirs = (root, root / "results")
    candidates: list[Path] = []
    for pattern in patterns:
        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue
            for path in search_dir.glob(pattern):
                if path.is_file():
                    candidates.append(path.resolve())
        if candidates:
            # Prefer the most-specific pattern, particularly the online IB
            # report, before falling back to broader report names.
            break
    if not candidates:
        pattern_text = ", ".join(patterns)
        raise FileNotFoundError(
            f"No Phase 1 cost report found under {root.resolve()} "
            f"(patterns: {pattern_text})"
        )
    return max(set(candidates), key=lambda path: path.stat().st_mtime_ns)


def load_latest_phase1_cost_report(
    report_dir: Path | str = ".",
    *,
    patterns: Sequence[str] = DEFAULT_REPORT_PATTERNS,
) -> tuple[Path, pl.DataFrame]:
    """Load the newest Phase 1 cost report and return its resolved path."""
    path = find_latest_phase1_cost_report(report_dir, patterns=patterns)
    return path, pl.read_csv(path, infer_schema_length=None)


def _resolve_rank_column(report: pl.DataFrame, rank_by: str) -> str:
    rank_column = RANK_COLUMNS.get(rank_by, rank_by)
    if rank_column not in report.columns and rank_by in LEGACY_RANK_COLUMNS:
        rank_column = LEGACY_RANK_COLUMNS[rank_by]
    if rank_column not in report.columns:
        choices = ", ".join(RANK_COLUMNS)
        raise ValueError(
            f"Unknown or unavailable rank {rank_by!r}; use one of "
            f"{choices}, or an existing report column"
        )
    return rank_column


def top_n_by_asset_class(
    report: pl.DataFrame,
    *,
    n: int = 5,
    rank_by: str = "cost",
    asset_classes: Iterable[str] | None = None,
    columns: Sequence[str] | None = DEFAULT_VIEW_COLUMNS,
    eligible_only: bool = True,
) -> dict[str, pl.DataFrame]:
    """Return the top ``n`` rows in each asset class, sorted rank 1 first.

    ``rank_by`` accepts ``cost``, ``affordability``, ``notional``, or an
    explicit numeric column name. Pass ``columns=None`` to retain every source
    column rather than the compact notebook view. By default, rows explicitly
    restricted by our execution profile are excluded; older reports without
    the eligibility column remain readable. Rank 1 is the lowest value for
    cost, affordability, and notional.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    asset_class_column = (
        "asset_cls" if "asset_cls" in report.columns else "asset_class"
    )
    if asset_class_column not in report.columns:
        raise ValueError("Report has no asset class column")

    rank_column = _resolve_rank_column(report, rank_by)
    execution_eligible_column = (
        "exec_elig" if "exec_elig" in report.columns else "execution_eligible"
    )
    if eligible_only and execution_eligible_column in report.columns:
        report = report.filter(
            pl.col(execution_eligible_column).fill_null(False)
        )
    if asset_classes is None:
        classes = sorted(
            value
            for value in report.get_column(asset_class_column).unique().to_list()
            if value is not None
        )
    else:
        classes = list(dict.fromkeys(asset_classes))

    selected_columns = None
    if columns is not None:
        selected_columns = [name for name in columns if name in report.columns]
        if rank_column not in selected_columns:
            selected_columns.append(rank_column)

    rankings: dict[str, pl.DataFrame] = {}
    for asset_class in classes:
        ranked = (
            report
            .filter(pl.col(asset_class_column) == asset_class)
            .sort([rank_column, "symbol"], nulls_last=True)
            .head(n)
        )
        if selected_columns is not None:
            ranked = ranked.select(selected_columns)
        rankings[asset_class] = ranked
    return rankings


def display_asset_class_rankings(
    rankings: dict[str, pl.DataFrame],
) -> None:
    """Render each asset class as a separate table in a Jupyter notebook."""
    try:
        from IPython.display import Markdown, display
    except ImportError as exc:  # pragma: no cover - project includes ipykernel
        raise RuntimeError("Jupyter/IPython is required to display rankings") from exc

    for asset_class, frame in rankings.items():
        display(Markdown(f"### {asset_class}"))
        display(frame)

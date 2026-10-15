"""Notebook helpers for loading and inspecting Phase 1 futures cost reports."""

from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl


DEFAULT_REPORT_PATTERNS = (
    "pysystemtrade_selection_screen_*.parquet",
    "pysystemtrade_selection_screen_*.csv",
    "pysystemtrade_cost_phase1_ib_*.parquet",
    "pysystemtrade_cost_phase1_ib_*.csv",
    "pysystemtrade_cost_phase1_*.parquet",
    "pysystemtrade_cost_phase1_*.csv",
    "futures_cost_risk_*.parquet",
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
    "econ_subcls",
    "region",
    "exec_prof",
    "exec_elig",
    "exec_restrict_reason",
    "cost_rank_in_asset_cls",
    "afford_rank_in_asset_cls",
    "notional_rank_in_asset_cls",
    "trade_sr",
    "roll_sr",
    "ref_ann_rolls",
    "spread_pts",
    "spread_src",
    "comm",
    "comm_ccy",
    "comm_usd",
    "comm_src",
    "init_margin_usd",
    "maint_margin_usd",
    "margin_ccy",
    "whatif_attempted",
    "whatif_elig",
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

PHASE2_STEP1_PARAM_COLUMNS = (
    "init_cap_usd",
    "target_vol",
    "cost_lim_sr",
    "rule_cost_lim_sr",
    "liq_ann_trades",
    "liq_days",
    "min_daily_volume",
    "max_pct_mkt_volume",
)

PHASE2_STEP1_COLUMNS = (
    "symbol",
    "signal_symbol",
    "hist_instr_code",
    "desc",
    "asset_cls",
    "econ_subcls",
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
    "init_cap_usd",
    "target_vol",
    "cost_lim_sr",
    "rule_cost_lim_sr",
    "liq_ann_trades",
    "liq_days",
    "min_daily_volume",
    "max_pct_mkt_volume",
    "max_ann_dvol",
    "avg_daily_volume",
    "volume_n",
    "volume_start",
    "volume_end",
    "volume_src",
    "risk_traded_usd_day",
    "mkt_risk_vol_usd_day",
    "pct_mkt_volume",
    "volume_elig",
    "risk_volume_elig",
    "liq_elig",
    "trade_sr",
    "roll_sr",
    "comm",
    "comm_ccy",
    "comm_usd",
    "comm_src",
    "init_margin_usd",
    "maint_margin_usd",
    "margin_ccy",
    "whatif_attempted",
    "whatif_elig",
    "ref_ann_rolls",
    "cost_elig",
    "size_elig",
    "data_elig",
    "elig_ewmac_rule_count",
    "elig_ewmac_rules",
    "instr_has_elig_ewmac_rule",
    "exec_elig",
    "phase2_elig",
    "report_ts_ct",
)


def phase2_step1_config(report: pl.DataFrame) -> dict[str, object]:
    """Return the single persisted Phase 1 configuration for Step 1."""
    missing = sorted(set(PHASE2_STEP1_PARAM_COLUMNS).difference(report.columns))
    if missing:
        raise ValueError(
            "Phase 1 report is missing Step 1 parameter fields "
            f"{missing}; rerun the current futures-cost-risk audit"
        )

    config: dict[str, object] = {}
    for column in PHASE2_STEP1_PARAM_COLUMNS:
        values = report.get_column(column).drop_nulls().unique().to_list()
        if len(values) != 1 or report.get_column(column).null_count():
            raise ValueError(
                f"Phase 1 report has inconsistent {column}; expected one "
                "non-null run-level value"
            )
        config[column] = values[0]
    return config


def override_phase2_initial_capital(
    report: pl.DataFrame,
    initial_capital_usd: float,
) -> pl.DataFrame:
    """Recalculate capital-dependent Phase 2 gates from a Phase 1 report.

    The Phase 1 report already persists annual dollar volatility, market risk
    volume, and every non-capital gate.  This function changes only the run's
    initial capital and derives the corresponding maximum contract dollar
    volatility, daily risk traded, market-volume percentage, size gate,
    relative-liquidity gate, and final Phase 2 eligibility.  It performs no
    market-data or history request.

    Parameters
    ----------
    report
        Complete public Phase 1 cost report, before Step 1 filtering.
    initial_capital_usd
        Replacement account capital in USD; must be strictly positive.

    Returns
    -------
    polars.DataFrame
        A new report with consistent run-level parameters and gate columns.
    """
    if initial_capital_usd <= 0:
        raise ValueError("initial_capital_usd must be positive")
    config = phase2_step1_config(report)
    required = {
        "ann_dvol",
        "mkt_risk_vol_usd_day",
        "volume_elig",
        "cost_elig",
        "data_elig",
        "instr_has_elig_ewmac_rule",
        "exec_elig",
        "ib_avail",
    }
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "Phase 1 report cannot apply a capital override; missing fields "
            f"{missing}"
        )

    target_vol = float(config["target_vol"])
    liquidity_ann_trades = float(config["liq_ann_trades"])
    liquidity_days = int(config["liq_days"])
    max_pct_market_volume = float(config["max_pct_mkt_volume"])
    max_ann_dvol = initial_capital_usd * target_vol
    risk_traded_day = (
        initial_capital_usd
        * target_vol
        * liquidity_ann_trades
        / liquidity_days
    )

    recalculated = report.with_columns(
        pl.lit(initial_capital_usd).alias("init_cap_usd"),
        pl.lit(max_ann_dvol).alias("max_ann_dvol"),
        pl.lit(risk_traded_day).alias("risk_traded_usd_day"),
    ).with_columns(
        pl.when(
            pl.col("mkt_risk_vol_usd_day").is_not_null()
            & pl.col("mkt_risk_vol_usd_day").is_finite()
            & (pl.col("mkt_risk_vol_usd_day") > 0)
        )
        .then(
            100.0
            * pl.col("risk_traded_usd_day")
            / pl.col("mkt_risk_vol_usd_day")
        )
        .otherwise(None)
        .alias("pct_mkt_volume"),
        (
            pl.col("ann_dvol").is_not_null()
            & pl.col("ann_dvol").is_finite()
            & (pl.col("ann_dvol") <= max_ann_dvol)
        ).alias("size_elig"),
    ).with_columns(
        (
            pl.col("pct_mkt_volume").is_not_null()
            & pl.col("pct_mkt_volume").is_finite()
            & (pl.col("pct_mkt_volume") < max_pct_market_volume)
        ).alias("risk_volume_elig"),
    ).with_columns(
        (
            pl.col("volume_elig").fill_null(False)
            & pl.col("risk_volume_elig").fill_null(False)
        ).alias("liq_elig"),
    ).with_columns(
        (
            pl.col("cost_elig").fill_null(False)
            & pl.col("size_elig").fill_null(False)
            & pl.col("liq_elig").fill_null(False)
            & pl.col("data_elig").fill_null(False)
            & pl.col("instr_has_elig_ewmac_rule").fill_null(False)
            & pl.col("exec_elig").fill_null(False)
            & (pl.col("ib_avail").fill_null("") == "contract_qualified")
        ).alias("phase2_elig"),
    )

    # Keep the human-readable rejection column consistent when the source
    # report carries it. Other non-capital reasons remain unchanged because
    # their persisted gates are reused verbatim above.
    if "phase2_excl" in recalculated.columns:
        exclusions: list[str] = []
        for row in recalculated.iter_rows(named=True):
            reasons: list[str] = []
            if not row.get("data_elig"):
                reasons.append("data_unavailable")
            if not row.get("cost_elig"):
                reasons.append("cost")
            if not row.get("size_elig"):
                reasons.append("risk_size")
            if row.get("avg_daily_volume") is None:
                reasons.append("volume_unavailable")
            else:
                if not row.get("volume_elig"):
                    reasons.append("contract_volume")
                if not row.get("risk_volume_elig"):
                    reasons.append("risk_volume")
            if not row.get("instr_has_elig_ewmac_rule"):
                reasons.append("no_eligible_rule")
            if not row.get("exec_elig"):
                reasons.append("execution")
            if row.get("ib_avail") != "contract_qualified":
                reasons.append("ib_unavailable")
            exclusions.append(",".join(reasons))
        recalculated = recalculated.with_columns(
            pl.Series("phase2_excl", exclusions, dtype=pl.String)
        )
    return recalculated


def phase2_step1_candidates(report: pl.DataFrame) -> pl.DataFrame:
    """Select Phase 2 candidates using only gates saved by Phase 1.

    This function deliberately performs no market-data request and no metric
    calculation. It filters the complete Phase 1 audit using its persisted
    cost, dollar-vol size, liquidity, data, rule, execution, and IB contract
    decisions, then returns the compact columns needed by the next stage.
    """
    required = {
        *PHASE2_STEP1_GATE_COLUMNS,
        "ib_avail",
        "symbol",
        "asset_cls",
        "econ_subcls",
    }
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "Phase 1 report is missing Step 1 selection fields "
            f"{missing}; rerun the current futures-cost-risk audit"
        )
    phase2_step1_config(report)

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
    """Find the newest Phase 1 report in ``report_dir`` or its results folder.

    New reports are Parquet; CSV patterns remain available for legacy runs.
    Report-family precedence prefers the selection screen, then progressively
    broader Phase 1 cost report names.
    """
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
    """Load the newest Phase 1 report and return its resolved path.

    Parquet preserves the report's dates, booleans, and numeric schema. CSV is
    read only to maintain compatibility with previously saved reports.
    """
    path = find_latest_phase1_cost_report(report_dir, patterns=patterns)
    return path, read_phase1_cost_report(path)


def read_phase1_cost_report(path: Path | str) -> pl.DataFrame:
    """Read one Phase 1 report, preferring its native Parquet schema."""
    report_path = Path(path)
    if report_path.suffix.lower() == ".parquet":
        return pl.read_parquet(report_path)
    if report_path.suffix.lower() == ".csv":
        return pl.read_csv(report_path, infer_schema_length=None)
    raise ValueError(
        f"Unsupported Phase 1 report format {report_path.suffix!r}; "
        "expected .parquet or legacy .csv"
    )


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

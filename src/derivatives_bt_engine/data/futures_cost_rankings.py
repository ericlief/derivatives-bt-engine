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
    "cost": "cost_rank_in_asset_class",
    "affordability": "affordability_rank_in_asset_class",
    "notional": "notional_rank_in_asset_class",
}

DEFAULT_VIEW_COLUMNS = (
    "symbol",
    "description",
    "asset_class",
    "region",
    "cost_rank_in_asset_class",
    "affordability_rank_in_asset_class",
    "notional_rank_in_asset_class",
    "selected_sr_cost_per_trade",
    "selected_spread_points",
    "selected_spread_source",
    "cur_price",
    "currency",
    "notional_usd_per_contract",
    "selected_ann_dvol_usd_per_contract",
    "min_capital_usd_full_weight_idm1",
    "eligible_ewmac_rule_count",
    "eligible_ewmac_rules",
    "pooling_role",
    "include_default_pool",
    "ib_availability",
)


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
) -> dict[str, pl.DataFrame]:
    """Return the top ``n`` rows in each asset class, sorted best rank first.

    ``rank_by`` accepts ``cost``, ``affordability``, ``notional``, or an
    explicit numeric column name. Pass ``columns=None`` to retain every source
    column rather than the compact notebook view.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if "asset_class" not in report.columns:
        raise ValueError("Report has no asset_class column")

    rank_column = _resolve_rank_column(report, rank_by)
    if asset_classes is None:
        classes = sorted(
            value
            for value in report.get_column("asset_class").unique().to_list()
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
            .filter(pl.col("asset_class") == asset_class)
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

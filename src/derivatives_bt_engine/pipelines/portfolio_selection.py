"""Filter a cached rule universe and build the selected futures book.

The persisted rule-cost cache supplies executable symbols, their borrowed
signal-history identifiers, costs, dollar volatility, and eligible EWMAC
rules. This command first applies the requested account, cost, risk-size,
liquidity, data, and execution filters and saves that candidate set. It can
then run the full greedy selection and uses three deliberately different
dataframe collections:

``frames_by_history``
    One daily component-forecast frame per unique ``hist_instr_code``. Several
    executable symbols can reuse the same history, so these expensive EWMAC
    calculations are performed once.
``subsystem_reference_frames``
    One daily combined-forecast, equal-weight reference position, historical
    FX, and net-return frame per executable ``symbol``. Symbols sharing history
    may differ here because their market-screen cost gates can leave them with
    different eligible rules.
``reference_return_wide``
    One synchronized frame with dates in rows and executable symbols in
    columns. This is the input to the instrument-correlation estimator.
``cash_pnl_long``
    One long account-scaled frame for the final selected book. It applies the
    forecast buffer, rounds to contracts, and separates ordinary trade cost
    from realized and interval-average policy-roll cost. The generated Panama
    ``close`` is retained beside its roll-neutral ``point_change`` for audit.
``portfolio_curve``
    One portfolio row per date. It sums the selected instruments' executable
    cash P&L and reports daily returns against prior gross or net equity,
    cumulative returns, and gross and net equity paths. Static runs keep the
    final selected weights and IDM fixed. Scheduled runs replace the book at
    monthly or yearly review boundaries using prior history and lagged net
    equity, while retaining executable holdings between reviews.

The command persists each intermediate form so forecast gaps, turnover, and
greedy selection decisions remain inspectable after a run.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import tempfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
from ib_tools.ibpysync import IBPySync

from derivatives_bt_engine.data.futures_cost_rankings import (
    apply_selection_filters,
    load_latest_market_report,
    read_market_report,
    resolve_selection_config,
    selection_candidates,
    selection_rebalance_candidates,
)
from derivatives_bt_engine.data.fx_history import (
    align_fx_to_history,
    load_fx_to_usd_history,
)
from derivatives_bt_engine.strats.multi_speed_ewmac import (
    build_ewmac_component_frame,
    load_ewmac_component_frames,
    load_pooled_ewmac_rules,
)
from derivatives_bt_engine.reports.formatting import (
    order_cash_pnl_report,
    order_portfolio_curve_report,
    rename_public_report_columns,
    round_public_report,
)
from derivatives_bt_engine.calculations.correlation import (
    bounded_pairwise_ewm_correlation_matrix,
)
from derivatives_bt_engine.calculations.costs import calculate_annual_sr_cost
from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
)
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastEngine,
    ForecastCombinationConfig,
    SlowTiltEwmacWeights,
    median_forecast_correlation,
)
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_FUTURES_CACHE_ROOT,
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    FuturesHistory,
)
from derivatives_bt_engine.data.ib_futures_dataloader import (
    DEFAULT_BROKER_MAPPING_PATH,
    DEFAULT_MAX_SOURCE_GAP_DAYS,
    IBStitchedHistoryProvider,
    connect_ib,
)
from derivatives_bt_engine.data.ib_futures_history_store import (
    IBFuturesHistoryStore,
)
from derivatives_bt_engine.data.pysystemtrade_ib import (
    load_pysystemtrade_ib_instruments,
)
from derivatives_bt_engine.calculations.instrument_selection import (
    EqualPortfolioWeights,
    GreedyInstrumentSelector,
    GreedySelectionConfig,
    HandcraftedPortfolioWeights,
    SelectionCandidate,
)
from derivatives_bt_engine.calculations.pnl import (
    PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
    append_reference_position_turnover,
    apply_subsystem_reference_cost,
    build_equity_scaled_cash_pnl_curve,
    build_executable_cash_pnl_curve,
    build_portfolio_equity_curve,
    build_subsystem_reference_return_curve,
    summarize_return_significance,
)
from derivatives_bt_engine.logging_config import setup_logger


logger = logging.getLogger(__name__)
REPORT_TIMEZONE = ZoneInfo("America/Chicago")

# Production-inspired ordinary-EWMAC template. The two slowest rules receive
# 60% in total and the three faster rules receive 40%. Rules removed by the
# Market-screen cost ceiling are set to zero and the survivors are renormalised.
EWMAC_SLOW_TILT_60_40 = {
    "4/16": 0.05,
    "8/32": 0.15,
    "16/64": 0.20,
    "32/128": 0.30,
    "64/256": 0.30,
}


def slow_tilt_ewmac_weights(
    eligible_rules: str | Iterable[str],
) -> dict[str, float]:
    """Return slow-tilted weights for the rules that survived cost screening.

    ``eligible_rules`` may be a comma-separated report value or an iterable of
    ``fast/slow`` rule keys. Missing rules receive no allocation and the
    remaining template weights are renormalized to sum to one.
    """
    rules = _parse_rules(eligible_rules)
    return SlowTiltEwmacWeights(EWMAC_SLOW_TILT_60_40).weights(rules)


def _parse_rules(eligible_rules: str | Iterable[str]) -> list[str]:
    """Normalize a serialized report rule field or iterable into rule keys."""
    if isinstance(eligible_rules, str):
        return [rule.strip() for rule in eligible_rules.split(",") if rule.strip()]
    return [str(rule).strip() for rule in eligible_rules if str(rule).strip()]


def _resolve_stitched_signal_history_code(
    source_row: dict[str, object],
    instrument_rows: list[dict[str, object]],
) -> str:
    """Resolve the reference/IB history owner for an executable report row.

    A micro can borrow a full-size ``signal_symbol`` while retaining its own
    multiplier and costs. The screen's ``hist_instr_code`` remains the fallback
    for older reports that did not persist a signal ticker.
    """
    signal_symbol = str(source_row.get("signal_symbol") or "").strip()
    if not signal_symbol:
        return str(source_row["hist_instr_code"])
    matches = [
        row for row in instrument_rows if row.get("symbol") == signal_symbol
    ]
    if not matches:
        matches = [
            row
            for row in instrument_rows
            if row.get("ib_symbol") == signal_symbol
        ]
    codes = {str(row["pst_instr_code"]) for row in matches}
    if len(codes) != 1:
        raise KeyError(
            f"signal symbol {signal_symbol!r} does not resolve to one history "
            f"instrument for executable {source_row.get('symbol')!r}"
        )
    return codes.pop()


def parse_args(argv=None, *, complete_default: bool = False):
    """Parse candidate-filter and optional complete-selection arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Load a cached futures rule-cost Parquet report, apply selection "
            "filters, save the accepted candidates, and optionally build the "
            "complete greedy-selected book."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit rule-cache Parquet; legacy cost-report CSV is also accepted.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("."),
        help="Directory (and its results/ child) searched for the latest report.",
    )
    output_location = parser.add_mutually_exclusive_group()
    output_location.add_argument(
        "--output",
        type=Path,
        help="Exact candidate output path; defaults beside the input cache.",
    )
    output_location.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Parent directory for selection_run_<timestamp>/<parameters>; all "
            "candidate and complete-selection reports are written there. "
            "Without this option, reports default beside the input cache."
        ),
    )
    parser.add_argument(
        "--initial-capital-usd",
        type=float,
        help=(
            "Account capital used by the built-in risk-size and relative-"
            "liquidity filters; defaults to a legacy cached value or 100000"
        ),
    )
    parser.add_argument(
        "--target-vol",
        type=float,
        help=(
            "Annual portfolio volatility target used by selection filters; "
            "defaults to a legacy cached value or 0.20"
        ),
    )
    parser.add_argument(
        "--instrument-cost-limit-sr",
        type=float,
        help="Maximum cached one-way trade cost in SR units (default: 0.01).",
    )
    parser.add_argument(
        "--liquidity-ann-trades",
        type=float,
        help="Annual trades used by the market-participation screen (default: 25.6).",
    )
    parser.add_argument(
        "--liquidity-business-days",
        type=int,
        help="Business days used to convert annual trades to daily risk (default: 256).",
    )
    parser.add_argument(
        "--max-market-volume-pct",
        type=float,
        help="Maximum strategy share of daily market risk volume (default: 1).",
    )
    parser.add_argument(
        "--min-daily-volume",
        type=float,
        help="Minimum mean executable-contract volume (default: 100).",
    )
    completion = parser.add_mutually_exclusive_group()
    completion.add_argument(
        "--complete",
        dest="complete",
        action="store_true",
        help="Build combined forecasts and run the iterative greedy selector.",
    )
    completion.add_argument(
        "--candidates-only",
        dest="complete",
        action="store_false",
        help="Stop after saving the filtered candidate report.",
    )
    parser.set_defaults(complete=complete_default)
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
            "Optional stored IB dated-contract database. When supplied, the "
            "final book cash P&L and hist_* diagnostics use offline stitched "
            "reference/Globex/IB history; construction inputs remain unchanged."
        ),
    )
    parser.add_argument(
        "--ib-history-live",
        choices=("missing", "update"),
        help=(
            "Opt in to live IB history acquisition. 'missing' first uses "
            "stored history and retries a failed stitched load from IB; "
            "'update' refreshes every selected signal history from IB."
        ),
    )
    parser.add_argument(
        "--ib-host",
        default="127.0.0.1",
        help="TWS/IB Gateway host used by --ib-history-live.",
    )
    parser.add_argument(
        "--ib-port",
        type=int,
        action="append",
        help=(
            "TWS/IB Gateway port attempted by --ib-history-live; repeat to "
            "supply fallbacks (default: 7497, 4002, 7496, 4001)."
        ),
    )
    parser.add_argument(
        "--ib-client-id",
        type=int,
        default=24,
        help="IB client ID used by --ib-history-live (default: 24).",
    )
    parser.add_argument(
        "--globex-db",
        type=Path,
        default=DEFAULT_GLOBEX_DB_PATH,
        help="Globex database used as the optional middle history segment.",
    )
    parser.add_argument(
        "--broker-mapping",
        type=Path,
        default=DEFAULT_BROKER_MAPPING_PATH,
        help="Broker contract mapping used to read stored IB dated contracts.",
    )
    parser.add_argument(
        "--history-cache-root",
        type=Path,
        default=DEFAULT_FUTURES_CACHE_ROOT,
        help="Parquet cache root for final stitched history views.",
    )
    parser.add_argument(
        "--max-source-gap-days",
        type=int,
        default=DEFAULT_MAX_SOURCE_GAP_DAYS,
        help=(
            "Largest calendar gap allowed for a direct reference-to-IB "
            "stitch (default: 31); the unknown gap return is masked."
        ),
    )
    parser.add_argument(
        "--min-forecast-obs",
        type=int,
        default=256,
        help="Minimum valid combined-forecast observations.",
    )
    parser.add_argument("--score-threshold", type=float, default=0.90)
    parser.add_argument("--gross-sr", type=float, default=0.50)
    parser.add_argument(
        "--low-con-penalty-scale",
        dest="low_contract_penalty_scale",
        type=float,
        default=0.0,
        help=(
            "Log-squared SR penalty scale below --con-band-min; disabled at 0"
        ),
    )
    parser.add_argument(
        "--high-con-penalty-scale",
        dest="high_contract_penalty_scale",
        type=float,
        default=0.0,
        help=(
            "Log-squared SR penalty scale above --con-band-max; disabled at 0"
        ),
    )
    parser.add_argument(
        "--con-band-min",
        dest="contract_band_min",
        type=float,
        default=2.5,
        help="Lower continuous maximum-position boundary (default: 2.5).",
    )
    parser.add_argument(
        "--con-band-max",
        dest="contract_band_max",
        type=float,
        default=5.5,
        help="Upper continuous maximum-position boundary (default: 5.5).",
    )
    parser.add_argument(
        "--score-tie-tolerance-pct",
        dest="score_tie_tolerance_percent",
        type=float,
        default=0.0,
        help=(
            "Within-iteration score window in percent for the contract-band "
            "tie-break; disabled at 0 (suggested sensitivities: .25,.5,1)"
        ),
    )
    parser.add_argument(
        "--tie-reference-book-count",
        dest="tie_reference_book_count",
        type=int,
        default=15,
        help=(
            "Estimated final book count used for stable equal-weight candidate "
            "position comparisons inside the near-tie window (default: 15)."
        ),
    )
    parser.add_argument(
        "--include-roll-cost-in-score",
        action="store_true",
        help=(
            "Deduct ref_ann_rolls * roll_sr as well as combined-forecast "
            "trading cost in every greedy net-SR score"
        ),
    )
    parser.add_argument(
        "--max-weight-idm",
        "--starting-weight",
        dest="max_weight_idm",
        type=float,
        default=0.20,
        help=(
            "Maximum instrument weight multiplied by trial IDM for position, "
            "liquidity, and margin calculations; --starting-weight remains "
            "a backward-compatible alias"
        ),
    )
    parser.add_argument(
        "--max-instr-per-cluster",
        type=int,
        help=(
            "Maximum selected instruments in one economic subclass, such as "
            "grains; default is unlimited"
        ),
    )
    parser.add_argument(
        "--max-instr-margin-pct",
        "--max-margin-pct",
        dest="max_instr_margin_pct",
        type=float,
        help=(
            "Maximum IB what-if initial margin for any one instrument's "
            "rounded maximum-forecast position, as a percent of initial "
            "capital; --max-margin-pct remains a compatibility alias"
        ),
    )
    parser.add_argument(
        "--max-book-margin-pct",
        type=float,
        help=(
            "Maximum cumulative rounded book margin as a percent of initial "
            "capital; defaults to 100 when an instrument margin cap is set"
        ),
    )
    parser.add_argument(
        "--weight-policy",
        choices=("topdown_classif", "legacy_handcraft", "equal"),
        default="topdown_classif",
        help=(
            "Trial risk-weight policy; the reviewed asset/subclass hierarchy "
            "is the default"
        ),
    )
    parser.add_argument("--corr-window-years", type=float, default=20.0)
    parser.add_argument("--corr-halflife-days", type=float, default=250.0)
    parser.add_argument("--corr-min-rows", type=int, default=256)
    parser.add_argument(
        "--buffer-size",
        type=float,
        default=PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
        help=(
            "Executable-position no-trade band as a fraction of average "
            "contracts; default 0.10 matches pysystemtrade"
        ),
    )
    parser.add_argument(
        "--buffer-trade-to-edge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "On a buffer breach trade to the nearest edge (default), or use "
            "--no-buffer-trade-to-edge to trade to the rounded optimum"
        ),
    )
    parser.add_argument(
        "--rebalance-on-roll",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Reset executable contracts to the rounded target on roll dates "
            "(default); use --no-rebalance-on-roll to preserve the buffered "
            "holding"
        ),
    )
    parser.add_argument(
        "--selection-rebalance",
        choices=("static", "yearly", "monthly"),
        default="static",
        help=(
            "Cadence for rerunning instrument selection from lagged data and "
            "prior net equity. 'static' preserves one fixed book; 'yearly' "
            "or 'monthly' holds each selected book until the next scheduled "
            "review. Costs remain the saved fixed calibration."
        ),
    )
    return parser.parse_args(argv)


def _default_output_path(output_dir: Path, generated_at: datetime) -> Path:
    """Return the timestamped candidate path in ``output_dir``."""
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return output_dir / f"multi_speed_ewmac_selection_candidates_{stamp}.parquet"


def _number_token(value: float) -> str:
    """Return a compact decimal token without filesystem punctuation."""
    rendered = f"{value:.6f}".rstrip("0").rstrip(".")
    return rendered.replace("-", "m").replace(".", "p")


def _optional_number_token(value: float | None) -> str:
    """Return a numeric path token, using ``none`` for an inactive limit."""
    return "none" if value is None else _number_token(value)


def _selection_scenario_id(
    *,
    run_config: dict[str, object],
    args: argparse.Namespace,
) -> str:
    """Return the grid-style parameter identifier for one selection run."""
    effective_book_margin_pct = args.max_book_margin_pct
    if effective_book_margin_pct is None and args.max_instr_margin_pct is not None:
        effective_book_margin_pct = 100.0
    cluster = (
        "none"
        if args.max_instr_per_cluster is None
        else str(args.max_instr_per_cluster)
    )
    tokens = [
        f"tv{_number_token(float(run_config['target_vol']))}",
        f"im{_optional_number_token(args.max_instr_margin_pct)}",
        f"bm{_optional_number_token(effective_book_margin_pct)}",
        f"wi{_number_token(args.max_weight_idm)}",
        f"st{_number_token(args.score_threshold)}",
    ]
    if (
        args.low_contract_penalty_scale
        or args.high_contract_penalty_scale
        or args.score_tie_tolerance_percent
        or args.tie_reference_book_count != 15
    ):
        tokens.extend((
            f"lp{_number_token(args.low_contract_penalty_scale)}",
            f"hp{_number_token(args.high_contract_penalty_scale)}",
            f"tie{_number_token(args.score_tie_tolerance_percent)}",
            f"refn{args.tie_reference_book_count}",
        ))
    tokens.extend((
        f"cl{cluster}",
        f"roll{int(args.include_roll_cost_in_score)}",
        args.weight_policy,
    ))
    if args.selection_rebalance != "static":
        tokens.append(f"sel{args.selection_rebalance}")
    return "_".join(tokens)


def _selection_rebalance_dates(
    dates: Iterable[date],
    *,
    cadence: str,
) -> list[date]:
    """Return the first available session in each selection-review period.

    Parameters
    ----------
    dates
        Available portfolio sessions. Duplicate and unsorted values are
        accepted; timestamps are normalized to their calendar dates.
    cadence
        ``static`` selects only the first session, ``yearly`` selects the
        first session of each calendar year, and ``monthly`` selects the first
        session of each calendar month.

    Returns
    -------
    list[date]
        Strictly increasing effective review dates. Selection calculations on
        each date must use observations strictly before that date.
    """
    if cadence not in {"static", "yearly", "monthly"}:
        raise ValueError(
            "selection rebalance cadence must be static, yearly, or monthly"
        )
    normalized = sorted({
        value.date() if isinstance(value, datetime) else value
        for value in dates
    })
    if not normalized:
        return []
    if cadence == "static":
        return [normalized[0]]
    key = (
        (lambda value: value.year)
        if cadence == "yearly"
        else (lambda value: (value.year, value.month))
    )
    selected: list[date] = []
    prior_key: object | None = None
    for value in normalized:
        value_key = key(value)
        if value_key != prior_key:
            selected.append(value)
            prior_key = value_key
    return selected


def _selection_run_output_dir(
    output_root: Path,
    *,
    generated_at: datetime,
    run_config: dict[str, object],
    args: argparse.Namespace,
) -> Path:
    """Return the timestamped run and parameter directory below a root."""
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return (
        output_root
        / f"selection_run_{stamp}"
        / _selection_scenario_id(run_config=run_config, args=args)
    )


def _label_forecast_output_frame(
    frame: pl.DataFrame | pl.LazyFrame,
    *,
    symbol: str,
    hist_instr_code: str,
) -> pl.DataFrame | pl.LazyFrame:
    """Attach executable identity with ``symbol`` immediately after ``date``.

    Selection calculations keep executable identity outside each per-symbol
    dataframe. This helper adds it only at the persisted long-frame boundary
    and gives the identifiers a stable, scan-friendly leading order.
    """
    columns = (
        frame.columns
        if isinstance(frame, pl.DataFrame)
        else frame.collect_schema().names()
    )
    if "date" not in columns:
        raise ValueError("forecast output frame is missing date")
    labeled = frame.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(hist_instr_code).alias("hist_instr_code"),
    )
    leading = ["date", "symbol", "hist_instr_code"]
    trailing = [column for column in columns if column not in leading]
    return labeled.select(leading + trailing)


def _lazy_frame(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """Return ``frame`` as a lazy query without collecting its source."""
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def _subsystem_reference_correlation(
    subsystem_reference_frames: dict[str, pl.DataFrame | pl.LazyFrame],
    symbols: list[str],
    *,
    window_years: float,
    halflife_days: float,
    min_rows: int,
) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    """Estimate correlations from equal-weight subsystem-reference returns.

    ``subsystem_reference_frames`` is keyed by executable symbol, unlike
    ``frames_by_history`` above. The function outer-joins each symbol's
    ``ref_net_pct`` on date to form a sparse percentage-return panel. The
    estimator uses each pair's contemporaneous observations, so different
    history starts and exchange holidays do not erase the entire universe.

    Returns the dense correlation matrix, its per-symbol coverage mask, and
    the wide return frame that was actually used.
    """
    wide: pl.LazyFrame | None = None
    for symbol in symbols:
        returns = (
            _lazy_frame(subsystem_reference_frames[symbol])
            .select("date", pl.col("ref_net_pct").alias(symbol))
            .drop_nulls()
        )
        # Preserve the union of dates here. Requiring a date shared by every
        # candidate makes one young or differently calendared instrument erase
        # otherwise valid histories for the whole selection universe.
        wide = returns if wide is None else wide.join(
            returns, on="date", how="full", coalesce=True
        )
    if wide is None:
        return np.eye(len(symbols)), np.zeros(len(symbols), dtype=bool), pl.DataFrame()
    # Projection and predicate pushdown keep the much wider subsystem audit
    # frames out of memory. Only the sparse date/return panel is materialized.
    wide_frame = wide.sort("date").collect(engine="streaming")
    if wide_frame.is_empty():
        return np.eye(len(symbols)), np.zeros(len(symbols), dtype=bool), pl.DataFrame()
    last = wide_frame.get_column("date").max()
    as_of = (last.date() if isinstance(last, datetime) else last) + timedelta(days=1)
    correlation, covered = bounded_pairwise_ewm_correlation_matrix(
        wide_frame,
        symbols,
        as_of,
        window_years,
        halflife_days,
        min_rows=min_rows,
    )
    return correlation, covered, wide_frame


def _greedy_selector(
    *,
    capital: float,
    run_config: dict[str, object],
    args: argparse.Namespace,
) -> GreedyInstrumentSelector:
    """Build one selector using the supplied point-in-time account capital."""
    if args.weight_policy == "legacy_handcraft":
        weight_policy = HandcraftedPortfolioWeights()
    elif args.weight_policy == "equal":
        weight_policy = EqualPortfolioWeights()
    else:
        weight_policy = None
    effective_book_margin_pct = args.max_book_margin_pct
    if effective_book_margin_pct is None and args.max_instr_margin_pct is not None:
        effective_book_margin_pct = 100.0
    return GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=capital,
            target_vol=float(run_config["target_vol"]),
            gross_sr=args.gross_sr,
            score_threshold=args.score_threshold,
            low_contract_penalty_scale=args.low_contract_penalty_scale,
            high_contract_penalty_scale=args.high_contract_penalty_scale,
            contract_band_min=args.contract_band_min,
            contract_band_max=args.contract_band_max,
            score_tie_tolerance_percent=args.score_tie_tolerance_percent,
            tie_reference_book_count=args.tie_reference_book_count,
            max_weight_idm=args.max_weight_idm,
            max_pct_market_volume=float(run_config["max_pct_mkt_volume"]),
            liquidity_days=int(run_config["liq_days"]),
            include_roll_cost_in_score=args.include_roll_cost_in_score,
            max_instruments_per_cluster=args.max_instr_per_cluster,
            max_instrument_margin_pct=args.max_instr_margin_pct,
            max_book_margin_pct=effective_book_margin_pct,
        ),
        weight_policy=weight_policy,
    )


class _WalkForwardSelectionProvider:
    """Rerun greedy selection on scheduled dates using only prior history.

    Market spreads and commissions are fixed calibrations. Completed segments
    use the saved Carver configured spread and commission; the final open
    segment uses the current live-contract calibration saved in the market
    report. Historical dollar volatility and correlations are cut strictly
    before each review date, while the selector receives prior net equity from
    the executable cash loop.
    """

    def __init__(
        self,
        *,
        review_dates: Iterable[date],
        source_rows: dict[str, dict[str, object]],
        audit_by_symbol: dict[str, dict[str, object]],
        reference_frames: dict[str, pl.DataFrame | pl.LazyFrame],
        run_config: dict[str, object],
        args: argparse.Namespace,
    ) -> None:
        """Store immutable selection inputs and the review-date calendar."""
        self.review_dates = set(review_dates)
        self.latest_review_date = (
            max(self.review_dates) if self.review_dates else None
        )
        self.source_rows = source_rows
        self.audit_by_symbol = audit_by_symbol
        self.reference_frames = reference_frames
        self.run_config = run_config
        self.args = args
        self.latest_selection = None
        self.latest_candidates: dict[str, SelectionCandidate] = {}
        self.latest_date: date | None = None
        self.latest_equity: float | None = None
        self.review_rows: list[dict[str, object]] = []
        self.review_instrument_rows: list[dict[str, object]] = []
        self.prior_selected: set[str] = set()

    @staticmethod
    def _finite(value: object) -> float | None:
        """Return a finite float, otherwise ``None``."""
        if value is None:
            return None
        converted = float(value)
        return converted if math.isfinite(converted) else None

    def _costs(
        self,
        *,
        row: dict[str, object],
        fx_to_usd: float,
        use_live_calibration: bool,
    ) -> tuple[float, float]:
        """Return fixed one-way trade and complete-roll USD costs."""
        current_ann_dvol = self._finite(row.get("ann_dvol"))
        current_trade_sr = self._finite(row.get("trade_sr"))
        current_roll_sr = self._finite(row.get("roll_sr"))
        current_trade_cost = (
            current_ann_dvol * current_trade_sr
            if current_ann_dvol is not None
            and current_trade_sr is not None
            else None
        )
        current_roll_cost = (
            current_ann_dvol * current_roll_sr
            if current_ann_dvol is not None
            and current_roll_sr is not None
            else None
        )
        if use_live_calibration:
            if current_trade_cost is None or current_roll_cost is None:
                raise ValueError(
                    f"{row.get('symbol')}: live cost calibration is incomplete"
                )
            return current_trade_cost, current_roll_cost

        spread_points = self._finite(row.get("ref_spread_pts"))
        commission_usd = self._finite(row.get("ref_config_comm_usd"))
        multiplier = self._finite(row.get("mult"))
        if (
            spread_points is not None
            and commission_usd is not None
            and multiplier is not None
        ):
            spread_cash_usd = spread_points * multiplier * fx_to_usd
            return (
                spread_cash_usd + commission_usd,
                spread_cash_usd + 2.0 * commission_usd,
            )
        if current_trade_cost is None or current_roll_cost is None:
            raise ValueError(
                f"{row.get('symbol')}: historical fixed cost calibration is incomplete"
            )
        return current_trade_cost, current_roll_cost

    def __call__(
        self,
        date_value: object,
        prior_net_equity: float,
    ) -> dict[str, dict[str, float | int]] | None:
        """Return a replacement allocation on a review date, else ``None``."""
        review_date = (
            date_value.date() if isinstance(date_value, datetime) else date_value
        )
        if review_date not in self.review_dates:
            return None

        candidates: list[SelectionCandidate] = []
        histories: dict[str, pl.DataFrame] = {}
        candidate_costs: dict[str, tuple[float, float]] = {}
        for symbol, row in self.source_rows.items():
            frame = self.reference_frames.get(symbol)
            audit = self.audit_by_symbol.get(symbol)
            if frame is None or audit is None:
                continue
            lazy_frame = _lazy_frame(frame)
            frame_columns = lazy_frame.collect_schema().names()
            valid_return_column = (
                "ref_return_valid"
                if "ref_return_valid" in frame_columns
                else "ref_ret_valid"
            )
            history = (
                lazy_frame
                .filter(
                    (pl.col("date") < review_date)
                    & pl.col(valid_return_column).fill_null(False)
                )
                .select("date", "ref_net_pct")
                .collect(engine="streaming")
            )
            if history.height < self.args.min_forecast_obs:
                continue
            latest = (
                lazy_frame
                .filter(pl.col("date") < review_date)
                .select("date", "daily_dvol", "fx_to_usd")
                .sort("date")
                .tail(1)
                .collect(engine="streaming")
            )
            if latest.is_empty():
                continue
            daily_dollar_vol = self._finite(latest["daily_dvol"][0])
            fx_to_usd = self._finite(latest["fx_to_usd"][0])
            turnover = self._finite(audit.get("comb_turnover"))
            average_volume = self._finite(row.get("avg_daily_volume"))
            initial_margin = self._finite(row.get("init_margin_usd"))
            ann_rolls = self._finite(row.get("ref_ann_rolls"))
            if (
                daily_dollar_vol is None
                or daily_dollar_vol <= 0
                or fx_to_usd is None
                or fx_to_usd <= 0
                or turnover is None
                or average_volume is None
                or average_volume <= 0
                or ann_rolls is None
            ):
                continue
            annual_dollar_vol = (
                daily_dollar_vol * math.sqrt(MIXED_VOL_ANNUALIZATION_DAYS)
            )
            trade_cost, roll_cost = self._costs(
                row=row,
                fx_to_usd=fx_to_usd,
                use_live_calibration=review_date == self.latest_review_date,
            )
            trade_sr = trade_cost / annual_dollar_vol
            if trade_sr > float(self.run_config["cost_lim_sr"]):
                continue
            candidate = SelectionCandidate(
                symbol=symbol,
                ann_dvol=annual_dollar_vol,
                trade_sr=trade_sr,
                combined_turnover=turnover,
                asset_class=str(row["asset_cls"]),
                economic_subclass=str(row["econ_subcls"]),
                econ_family=str(row.get("econ_fam_id") or ""),
                mkt_risk_vol_day=average_volume * annual_dollar_vol,
                initial_margin_usd=initial_margin,
                roll_sr=roll_cost / annual_dollar_vol,
                ann_rolls=ann_rolls,
            )
            candidates.append(candidate)
            histories[symbol] = history
            candidate_costs[symbol] = (trade_cost, roll_cost)

        if not candidates:
            logger.warning(
                "walk_forward_selection skipped date=%s equity=%s reason=no_candidates",
                review_date,
                prior_net_equity,
            )
            return None
        symbols = [candidate.symbol for candidate in candidates]
        correlation, covered, _ = _subsystem_reference_correlation(
            histories,
            symbols,
            window_years=self.args.corr_window_years,
            halflife_days=self.args.corr_halflife_days,
            min_rows=self.args.corr_min_rows,
        )
        keep = [index for index, is_covered in enumerate(covered) if is_covered]
        if not keep:
            logger.warning(
                "walk_forward_selection skipped date=%s equity=%s "
                "reason=no_correlation_coverage",
                review_date,
                prior_net_equity,
            )
            return None
        candidates = [candidates[index] for index in keep]
        symbols = [candidate.symbol for candidate in candidates]
        correlation = correlation[np.ix_(keep, keep)]
        selection = _greedy_selector(
            capital=prior_net_equity,
            run_config=self.run_config,
            args=self.args,
        ).select(candidates, correlation)
        candidate_by_symbol = {
            candidate.symbol: candidate for candidate in candidates
        }
        detail_by_symbol = {
            str(detail["symbol"]): detail
            for detail in selection.final_score.details
        }
        allocations: dict[str, dict[str, float | int]] = {}
        for rank, symbol in enumerate(selection.selected, start=1):
            detail = detail_by_symbol[symbol]
            trade_cost, roll_cost = candidate_costs[symbol]
            allocations[symbol] = {
                "rank": rank,
                "weight": float(selection.final_score.weights[symbol]),
                "limit_weight": float(detail["limit_weight"]),
                "idm": float(selection.final_score.idm),
                "trade_cost_per_con": trade_cost,
                "roll_cost_per_con": roll_cost,
            }
        self.latest_selection = selection
        self.latest_candidates = candidate_by_symbol
        self.latest_date = review_date
        self.latest_equity = prior_net_equity
        selected_symbols = set(selection.selected)
        added_symbols = selected_symbols.difference(self.prior_selected)
        removed_symbols = self.prior_selected.difference(selected_symbols)
        self.review_rows.append({
            "selection_date": review_date,
            "selection_equity_usd": prior_net_equity,
            "selection_candidate_count": len(candidates),
            "selection_instrument_count": len(selection.selected),
            "selection_score": selection.final_score.score,
            "selection_idm": selection.final_score.idm,
            "selection_symbols": ",".join(selection.selected),
            "selection_added_symbols": ",".join(sorted(added_symbols)),
            "selection_removed_symbols": ",".join(sorted(removed_symbols)),
        })
        for symbol in sorted(selected_symbols | removed_symbols):
            active = symbol in selected_symbols
            candidate = candidate_by_symbol.get(symbol)
            allocation = allocations.get(symbol)
            source = self.source_rows[symbol]
            audit = self.audit_by_symbol[symbol]
            if active and symbol in added_symbols:
                status = "added"
            elif active:
                status = "retained"
            else:
                status = "removed"
            self.review_instrument_rows.append({
                "selection_date": review_date,
                "selection_year": review_date.year,
                "selection_equity_usd": prior_net_equity,
                "selection_candidate_count": len(candidates),
                "selection_instrument_count": len(selection.selected),
                "selection_score": selection.final_score.score,
                "selection_idm": selection.final_score.idm,
                "selection_status": status,
                "selection_active": active,
                "rank": allocation.get("rank") if allocation else None,
                "symbol": symbol,
                "hist_instr_code": source.get("hist_instr_code"),
                "pst_instr_code": source.get("pst_instr_code"),
                "asset_cls": source.get("asset_cls"),
                "econ_subcls": source.get("econ_subcls"),
                "weight": allocation.get("weight") if allocation else None,
                "limit_weight": (
                    allocation.get("limit_weight") if allocation else None
                ),
                "ann_dvol": candidate.ann_dvol if candidate else None,
                "trade_sr": candidate.trade_sr if candidate else None,
                "roll_sr": candidate.roll_sr if candidate else None,
                "comb_turnover": audit.get("comb_turnover"),
                "ann_trade_cost_sr": (
                    candidate.annual_trade_cost_sr if candidate else None
                ),
                "ann_roll_cost_sr": (
                    candidate.annual_roll_cost_sr if candidate else None
                ),
                "ann_cost_sr": candidate.annual_cost_sr if candidate else None,
                "trade_cost_per_con": (
                    allocation.get("trade_cost_per_con") if allocation else None
                ),
                "roll_cost_per_con": (
                    allocation.get("roll_cost_per_con") if allocation else None
                ),
            })
        self.prior_selected = selected_symbols
        logger.info(
            "walk_forward_selection complete date=%s equity=%s candidates=%d "
            "selected=%d idm=%s book=%s",
            review_date,
            prior_net_equity,
            len(candidates),
            len(selection.selected),
            selection.final_score.idm,
            ",".join(selection.selected),
        )
        return allocations


def _final_selection_frame(
    *,
    selection,
    candidate_by_symbol: dict[str, SelectionCandidate],
    source_rows: dict[str, dict[str, object]],
    audit_by_symbol: dict[str, dict[str, object]],
    args: argparse.Namespace,
    selection_date: date | None = None,
    selection_equity_usd: float | None = None,
) -> pl.DataFrame:
    """Build the public selected-book calculation frame for one review."""
    effective_book_margin_pct = args.max_book_margin_pct
    if effective_book_margin_pct is None and args.max_instr_margin_pct is not None:
        effective_book_margin_pct = 100.0
    final_details = {
        str(detail["symbol"]): detail
        for detail in selection.final_score.details
    }
    rows: list[dict[str, object]] = []
    for rank, symbol in enumerate(selection.selected, start=1):
        candidate = candidate_by_symbol[symbol]
        rows.append({
            "selection_date": selection_date,
            "selection_equity_usd": selection_equity_usd,
            "selection_rebalance": args.selection_rebalance,
            "rank": rank,
            "symbol": symbol,
            "pst_instr_code": source_rows[symbol].get("pst_instr_code"),
            "wt_policy": args.weight_policy,
            "max_instr_per_cluster": args.max_instr_per_cluster,
            "max_instr_margin_pct": args.max_instr_margin_pct,
            "max_book_margin_pct": effective_book_margin_pct,
            "low_contract_penalty_scale": args.low_contract_penalty_scale,
            "high_contract_penalty_scale": args.high_contract_penalty_scale,
            "contract_band_min": args.contract_band_min,
            "contract_band_max": args.contract_band_max,
            "score_tie_tolerance_percent": args.score_tie_tolerance_percent,
            "tie_ref_book_count": args.tie_reference_book_count,
            "roll_cost_in_score": args.include_roll_cost_in_score,
            "buffer_size": args.buffer_size,
            "buffer_trade_to_edge": args.buffer_trade_to_edge,
            "rebalance_on_roll": args.rebalance_on_roll,
            "weight": selection.final_score.weights[symbol],
            "idm": selection.final_score.idm,
            "score": selection.final_score.score,
            "base_score": selection.final_score.base_score,
            "low_contract_weight": selection.final_score.low_contract_weight,
            "band_contract_weight": selection.final_score.band_contract_weight,
            "high_contract_weight": selection.final_score.high_contract_weight,
            "ann_dvol": candidate.ann_dvol,
            "trade_sr": candidate.trade_sr,
            "roll_sr": candidate.roll_sr,
            "comb_turnover": candidate.combined_turnover,
            "ann_trade_cost_sr": candidate.annual_trade_cost_sr,
            "ann_roll_cost_sr": candidate.annual_roll_cost_sr,
            "ann_cost_sr": candidate.annual_cost_sr,
            **final_details[symbol],
        })
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if args.selection_rebalance == "static":
        frame = frame.drop(
            "selection_date",
            "selection_equity_usd",
            "selection_rebalance",
        )
    return frame


def _run_complete_selection(
    selected: pl.DataFrame,
    *,
    output_dir: Path,
    generated_at: datetime,
    run_config: dict[str, object],
    args: argparse.Namespace,
) -> tuple[Path, pl.DataFrame]:
    """Audit forecasts, exclude invalid instruments, and run greedy selection.

    ``selected`` contains one row per executable symbol from candidate filtering. The
    function first builds reusable history-level forecasts, then creates a
    symbol-level combined subsystem using that symbol's eligible-rule subset.
    It persists the daily forecast panel, audit, correlation input, greedy
    trials, and final accepted book.

    Returns the final selection Parquet path and its in-memory dataframe.
    """
    logger.info(
        "portfolio_selection start candidates=%d buffer_size=%s "
        "buffer_trade_to_edge=%s rebalance_on_roll=%s",
        selected.height,
        args.buffer_size,
        args.buffer_trade_to_edge,
        args.rebalance_on_roll,
    )
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    forecasts_path = (
        output_dir / f"multi_speed_ewmac_selection_forecasts_{stamp}.parquet"
    )
    reference_return_path = (
        output_dir
        / f"multi_speed_ewmac_selection_subsystem_reference_returns_{stamp}.parquet"
    )
    work_parent = (
        args.history_cache_root.expanduser().resolve() / "selection_work"
    )
    work_parent.mkdir(parents=True, exist_ok=True)
    work_tempdir = tempfile.TemporaryDirectory(
        prefix=f"run_{stamp}_",
        dir=work_parent,
    )
    work_dir = Path(work_tempdir.name)
    frames_by_history = load_ewmac_component_frames(
        selected.get_column("hist_instr_code").drop_nulls().to_list(),
        db_path=args.history_db.expanduser().resolve(),
    )
    forecast_correlation, correlation_rules = median_forecast_correlation(
        frames_by_history.values(), min_observations=args.min_forecast_obs
    )
    engine = CombinedForecastEngine(config=ForecastCombinationConfig(
        min_valid_observations=args.min_forecast_obs,
    ))

    audit_rows: list[dict[str, object]] = []
    # Long-form output copies: one symbol-labelled frame is appended here and
    # all are concatenated only when the daily forecast Parquet is written.
    forecast_output_frames: list[pl.LazyFrame] = []
    # Executable symbol -> combined forecast, equal-weight reference position,
    # FX, and net-return history.
    # MZC and CORN_mini can point to the same raw history but remain separate
    # because their eligible rules and transaction costs can differ.
    subsystem_reference_frames: dict[str, pl.LazyFrame] = {}
    # Historical FX is identical for every instrument in a currency. Cache it
    # at the pipeline boundary instead of reopening DuckDB for every symbol.
    fx_history_by_currency: dict[str, pl.DataFrame | None] = {}
    # Preserve each compact candidate row for later sizing and liquidity inputs.
    source_rows: dict[str, dict[str, object]] = {}
    for symbol_index, row in enumerate(selected.iter_rows(named=True), start=1):
        symbol = str(row["symbol"])
        source_rows[symbol] = row
        rules = _parse_rules(str(row["elig_ewmac_rules"] or ""))
        # Convert the shared history frame into this executable symbol's
        # subsystem by combining only the rules that passed its cost ceiling.
        result = engine.combine(
            frames_by_history[str(row["hist_instr_code"])],
            rules,
            forecast_correlation,
            correlation_rules,
        )
        required_cost_fields = ("trade_sr", "roll_sr", "ref_ann_rolls", "mult")
        missing_cost_fields = [
            name for name in required_cost_fields if row.get(name) is None
        ]
        if missing_cost_fields:
            raise ValueError(
                f"{symbol}: market-screen row is missing complete-selection fields "
                f"{missing_cost_fields}; rerun the current futures-cost-risk audit"
            )
        if not str(row.get("ccy") or "").strip():
            raise ValueError(
                f"{symbol}: market-screen row is missing ccy; rebuild the current "
                "futures-cost-risk audit"
            )

        currency = str(row["ccy"]).upper()
        fx_error = ""
        if currency not in fx_history_by_currency:
            try:
                fx_history_by_currency[currency] = (
                    None if currency == "USD" else load_fx_to_usd_history(
                        currency,
                        db_path=args.history_db.expanduser().resolve(),
                    )
                )
            except (ValueError, OSError) as exc:
                fx_history_by_currency[currency] = None
                fx_error = str(exc)
        try:
            fx_frame = align_fx_to_history(
                result.frame,
                currency,
                fx_history=fx_history_by_currency[currency],
                db_path=args.history_db.expanduser().resolve(),
            )
        except (ValueError, OSError) as exc:
            # Keep the symbol in the audit even when its FX history is absent.
            # Null conversions make the downstream P&L gate fail explicitly.
            fx_error = str(exc)
            fx_frame = result.frame.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("fx_to_usd"),
                pl.lit(f"{currency}USD").alias("fx_pair"),
                pl.lit(None, dtype=pl.Date).alias("fx_date"),
                pl.lit(None, dtype=pl.Datetime("us")).alias("fx_source_ts"),
                pl.lit(None, dtype=pl.String).alias("fx_src"),
            )
        reference_frame = build_subsystem_reference_return_curve(
            fx_frame,
            capital=float(run_config["init_cap_usd"]),
            target_vol=float(run_config["target_vol"]),
            reference_book_count=args.tie_reference_book_count,
            reference_idm=GreedySelectionConfig.max_idm,
            max_weight_idm=args.max_weight_idm,
            multiplier=float(row["mult"]),
            # Turnover must be measured from these sized positions before its
            # SR cost can be known. The zero placeholder is replaced below.
            ann_cost_sr=0.0,
            target_abs_forecast=engine.config.target_abs_forecast,
        )
        reference_frame, combined_turnover = (
            append_reference_position_turnover(reference_frame)
        )
        annual_cost = calculate_annual_sr_cost(
            trade_sr=float(row["trade_sr"]),
            roll_sr=float(row["roll_sr"]),
            subsystem_turnover=float(combined_turnover or 0.0),
            ann_rolls=float(row["ref_ann_rolls"]),
        )
        reference_frame = apply_subsystem_reference_cost(
            reference_frame,
            ann_cost_sr=annual_cost.ann_cost_sr,
        ).with_columns(
            pl.lit(annual_cost.ann_trade_cost_sr).alias("ann_trade_cost_sr"),
            pl.lit(annual_cost.ann_roll_cost_sr).alias("ann_roll_cost_sr"),
            pl.lit(annual_cost.ann_cost_sr).alias("ann_cost_sr"),
        )
        valid_reference_returns = reference_frame.filter(
            pl.col("ref_return_valid")
        )
        first_reference_return_date = (
            valid_reference_returns.get_column("date").min()
            if valid_reference_returns.height
            else None
        )
        reference_returns_after_start = (
            reference_frame.filter(
                pl.col("date") >= first_reference_return_date
            )
            if first_reference_return_date is not None
            else reference_frame
        )
        reference_return_post_start_invalid = (
            reference_returns_after_start.filter(
                pl.col("fcst_valid") & ~pl.col("ref_return_valid")
            ).height
        )
        fx_missing_obs = reference_frame.filter(
            pl.col("fcst_valid") & pl.col("fx_to_usd").is_null()
        ).height
        fx_used = reference_frame.filter(pl.col("fx_to_usd").is_not_null())
        fx_pairs = fx_used.get_column("fx_pair").drop_nulls().unique().to_list()
        fx_sources = fx_used.get_column("fx_src").drop_nulls().unique().to_list()
        subsystem_elig = bool(
            result.audit["forecast_elig"]
            and combined_turnover is not None
            and math.isfinite(combined_turnover)
            and valid_reference_returns.height >= args.min_forecast_obs
            and reference_return_post_start_invalid == 0
        )
        if not result.audit["forecast_elig"]:
            subsystem_excl = str(result.audit["forecast_excl"])
        elif combined_turnover is None or not math.isfinite(combined_turnover):
            subsystem_excl = "invalid_combined_turnover"
        elif valid_reference_returns.height < args.min_forecast_obs:
            subsystem_excl = "insufficient_valid_ref_return_obs"
        elif reference_return_post_start_invalid:
            subsystem_excl = "ref_return_gaps_after_start"
        else:
            subsystem_excl = ""
        audit = {
            "symbol": symbol,
            "hist_instr_code": row["hist_instr_code"],
            "elig_ewmac_rules": ",".join(result.weights),
            "forecast_weights": json.dumps(result.weights, sort_keys=True),
            "fdm": result.fdm,
            "comb_turnover": combined_turnover,
            "ann_trade_cost_sr": annual_cost.ann_trade_cost_sr,
            "ann_roll_cost_sr": annual_cost.ann_roll_cost_sr,
            "ann_cost_sr": annual_cost.ann_cost_sr,
            "ref_return_valid_obs": valid_reference_returns.height,
            "ref_return_post_start_invalid_obs": (
                reference_return_post_start_invalid
            ),
            "ref_return_start": first_reference_return_date,
            "ref_return_end": (
                valid_reference_returns.get_column("date").max()
                if valid_reference_returns.height
                else None
            ),
            "fx_missing_forecast_obs": fx_missing_obs,
            "fx_pair": ",".join(sorted(fx_pairs)) or f"{currency}USD",
            "fx_src": ",".join(sorted(fx_sources)),
            "fx_start": (
                fx_used.get_column("fx_date").min() if fx_used.height else None
            ),
            "fx_end": (
                fx_used.get_column("fx_date").max() if fx_used.height else None
            ),
            "fx_error": fx_error,
            "subsystem_elig": subsystem_elig,
            "subsystem_excl": subsystem_excl,
            **result.audit,
        }
        audit_rows.append(audit)
        # The complete reference frames are substantially wider than the
        # return panel used for selection. Persist each one once and retain a
        # scan so later correlation, walk-forward, and cash stages can push
        # their own projections into the Parquet reader.
        reference_path = work_dir / f"reference_{symbol_index:04d}.parquet"
        reference_frame.write_parquet(reference_path)
        reference_scan = pl.scan_parquet(reference_path)
        subsystem_reference_frames[symbol] = reference_scan
        forecast_output_frames.append(
            _label_forecast_output_frame(
                reference_scan,
                symbol=symbol,
                hist_instr_code=str(row["hist_instr_code"]),
            )
        )
        logger.log(
            logging.INFO if subsystem_elig else logging.DEBUG,
            "selection_subsystem_reference_audit symbol=%s eligible=%s "
            "forecast_obs=%s reference_return_obs=%s "
            "post_start_invalid_obs=%s comb_turnover=%s reason=%s",
            symbol,
            subsystem_elig,
            result.audit["forecast_valid_obs"],
            valid_reference_returns.height,
            reference_return_post_start_invalid,
            combined_turnover,
            subsystem_excl,
        )

    # Component forecasts have now been converted into disk-backed subsystem
    # frames. Releasing them before the cross-instrument stages avoids holding
    # both full representations of the 100+ instrument universe.
    del frames_by_history
    gc.collect()

    audit_frame = pl.DataFrame(audit_rows, infer_schema_length=None).sort("symbol")
    eligible_audit = audit_frame.filter(pl.col("subsystem_elig"))
    eligible_symbols = eligible_audit.get_column("symbol").to_list()
    if not eligible_symbols:
        raise ValueError("no instrument passed the combined-forecast audit")

    # Correlations belong to equal-weight subsystem-reference returns, not raw
    # market returns or the individual EWMAC component forecasts.
    correlation, covered, reference_return_wide = (
        _subsystem_reference_correlation(
            subsystem_reference_frames,
            eligible_symbols,
            window_years=args.corr_window_years,
            halflife_days=args.corr_halflife_days,
            min_rows=args.corr_min_rows,
        )
    )
    if not covered.all():
        uncovered = [
            symbol for symbol, is_covered in zip(eligible_symbols, covered)
            if not is_covered
        ]
        audit_frame = audit_frame.with_columns(
            pl.when(pl.col("symbol").is_in(uncovered))
            .then(pl.lit(False))
            .otherwise(pl.col("subsystem_elig"))
            .alias("subsystem_elig"),
            pl.when(pl.col("symbol").is_in(uncovered))
            .then(pl.lit("insufficient_pairwise_return_obs"))
            .otherwise(pl.col("subsystem_excl"))
            .alias("subsystem_excl"),
        )
        keep = [idx for idx, value in enumerate(covered) if value]
        eligible_symbols = [eligible_symbols[idx] for idx in keep]
        correlation = correlation[np.ix_(keep, keep)]
    if not eligible_symbols:
        raise ValueError(
            "no instrument has sufficient pairwise subsystem-reference returns"
        )

    # These two large daily artifacts no longer participate in later
    # calculations. Persist them while the rest of the pipeline is still
    # disk-backed, then release their materialized copies before cash replay.
    public_forecasts = round_public_report(
        rename_public_report_columns(
            pl.concat(forecast_output_frames, how="diagonal_relaxed")
            .collect(engine="streaming")
        )
    )
    public_forecasts.write_parquet(forecasts_path)
    reference_return_wide.write_parquet(reference_return_path)
    del public_forecasts, forecast_output_frames, reference_return_wide
    gc.collect()

    audit_by_symbol = {
        row["symbol"]: row for row in audit_frame.iter_rows(named=True)
    }
    candidate_list = []
    for symbol in eligible_symbols:
        row = source_rows[symbol]
        audit = audit_by_symbol[symbol]
        candidate_list.append(SelectionCandidate(
            symbol=symbol,
            ann_dvol=float(row["ann_dvol"]),
            trade_sr=float(row["trade_sr"]),
            combined_turnover=float(audit["comb_turnover"]),
            asset_class=str(row["asset_cls"]),
            economic_subclass=str(row["econ_subcls"]),
            econ_family=str(row.get("econ_fam_id") or ""),
            mkt_risk_vol_day=(
                float(row["mkt_risk_vol_usd_day"])
                if row.get("mkt_risk_vol_usd_day") is not None
                else None
            ),
            initial_margin_usd=(
                float(row["init_margin_usd"])
                if row.get("init_margin_usd") is not None
                else None
            ),
            roll_sr=float(row["roll_sr"]),
            ann_rolls=float(row["ref_ann_rolls"]),
        ))
    selector = _greedy_selector(
        capital=float(run_config["init_cap_usd"]),
        run_config=run_config,
        args=args,
    )
    selection = selector.select(candidate_list, correlation)
    for trial in selection.trials:
        logger.debug(
            "selection_trial iteration=%s candidate=%s book=%s score=%s "
            "base_score=%s candidate_ref_max_position=%s "
            "candidate_ref_con_band_distance=%s tie_eligible=%s "
            "tie_break_applied=%s idm=%s "
            "portfolio_cont_margin_at_max_usd=%s "
            "portfolio_margin_at_max_usd=%s instr_margin_limit_usd=%s "
            "book_margin_limit_usd=%s book_margin_remaining_usd=%s "
            "roll_cost_in_score=%s feasible=%s accepted=%s reason=%s",
            trial["iteration"], trial["candidate"], trial["book"], trial["score"],
            trial["base_score"], trial["candidate_ref_max_position"],
            trial["candidate_ref_contract_band_distance"],
            trial["tie_eligible"],
            trial["tie_break_applied"],
            trial["idm"], trial.get("portfolio_cont_margin_at_max_usd"),
            trial.get("portfolio_margin_at_max_usd"),
            trial.get("instr_margin_limit_usd"),
            trial.get("book_margin_limit_usd"),
            trial.get("book_margin_remaining_usd"),
            args.include_roll_cost_in_score,
            trial["feasible"], trial["accepted"],
            trial["reason"],
        )
    logger.info(
        "portfolio_selection complete selected=%d candidates=%d score=%s idm=%s book=%s",
        len(selection.selected), len(candidate_list), selection.final_score.score,
        selection.final_score.idm, ",".join(selection.selected),
    )

    candidate_by_symbol = {
        candidate.symbol: candidate for candidate in candidate_list
    }
    final_frame = _final_selection_frame(
        selection=selection,
        candidate_by_symbol=candidate_by_symbol,
        source_rows=source_rows,
        audit_by_symbol=audit_by_symbol,
        args=args,
    )
    cash_symbols = (
        list(selection.selected)
        if args.selection_rebalance == "static"
        else eligible_symbols
    )

    cash_reference_frames = subsystem_reference_frames
    historical_return_sources = {
        symbol: "reference" for symbol in cash_symbols
    }
    if args.ib_history_db is not None:
        # Construction remains pinned to the long reference histories above.
        # Only the final executable cash audit is rebuilt from the
        # duplicate-free stitched source so reported P&L reaches stored IB data.
        pooled_rules = load_pooled_ewmac_rules(
            db_path=args.history_db.expanduser().resolve(),
        )
        instrument_rows = load_pysystemtrade_ib_instruments(
            db_path=args.history_db.expanduser().resolve(),
            mapping_path=args.broker_mapping.expanduser().resolve(),
        )
        signal_symbol_by_history_code: dict[str, str] = {}
        for instrument in instrument_rows:
            history_code = str(instrument["pst_instr_code"])
            signal_symbol = str(instrument["signal_symbol"])
            signal_symbol_by_history_code.setdefault(
                history_code, signal_symbol
            )
        stitched_history_code_by_symbol = {
            symbol: _resolve_stitched_signal_history_code(
                source_rows[symbol],
                instrument_rows,
            )
            for symbol in cash_symbols
        }
        selected_history_codes = sorted(
            {
                stitched_history_code_by_symbol[symbol]
                for symbol in cash_symbols
            }
        )
        symbols_by_history_code: dict[str, list[str]] = {
            history_code: [] for history_code in selected_history_codes
        }
        for symbol, history_code in stitched_history_code_by_symbol.items():
            symbols_by_history_code[history_code].append(symbol)
        symbol_index = {
            symbol: index for index, symbol in enumerate(cash_symbols, start=1)
        }
        # Start from the valid long-reference fallback. Each successfully
        # stitched history replaces only its own symbols with a disk-backed
        # scan, so 100+ combined component frames never coexist in memory.
        cash_reference_frames = dict(subsystem_reference_frames)
        historical_return_sources.update({
            symbol: "reference_fallback" for symbol in cash_symbols
        })
        live_ib: IBPySync | None = None

        def connected_ib() -> IBPySync:
            """Return one shared live IB connection for selected histories."""
            nonlocal live_ib
            if live_ib is None:
                live_ib = IBPySync()
                connect_ib(
                    live_ib,
                    args.ib_host,
                    args.ib_port or [7497, 4002, 7496, 4001],
                    args.ib_client_id,
                )
            return live_ib

        def load_stitched_history(
            history_code: str,
            *,
            ib: IBPySync | None,
            update_history: bool,
        ) -> FuturesHistory:
            """Load one stitched history with explicit broker-update policy."""
            return IBStitchedHistoryProvider(
                ib=ib,
                reference_db_path=args.history_db.expanduser().resolve(),
                globex_db_path=args.globex_db.expanduser().resolve(),
                mapping_path=args.broker_mapping.expanduser().resolve(),
                history_db_path=args.ib_history_db.expanduser().resolve(),
                cache_root=args.history_cache_root.expanduser().resolve(),
                use_cache=True,
                save_cache=True,
                update_history=update_history,
                max_source_gap_days=args.max_source_gap_days,
            ).load(signal_symbol_by_history_code[history_code])

        try:
            for history_index, history_code in enumerate(
                selected_history_codes,
                start=1,
            ):
                logger.info(
                    "selection_stitched_history start hist_instr_code=%s "
                    "history_index=%d history_count=%d ib_mode=%s",
                    history_code,
                    history_index,
                    len(selected_history_codes),
                    args.ib_history_live,
                )
                try:
                    if args.ib_history_live == "update":
                        stitched_history = load_stitched_history(
                            history_code,
                            ib=connected_ib(),
                            update_history=True,
                        )
                    else:
                        try:
                            stitched_history = load_stitched_history(
                                history_code,
                                ib=None,
                                update_history=False,
                            )
                        except Exception as exc:
                            if args.ib_history_live != "missing":
                                raise
                            logger.info(
                                "selection_stitched_history live_retry "
                                "hist_instr_code=%s reason=%s",
                                history_code,
                                exc,
                            )
                            stitched_history = load_stitched_history(
                                history_code,
                                ib=connected_ib(),
                                update_history=True,
                            )
                except Exception as exc:
                    # Preserve the valid long reference curve when a broker
                    # source is unavailable, and expose that fallback in the
                    # public historical-return source field.
                    logger.warning(
                        "selection_stitched_history unavailable "
                        "hist_instr_code=%s fallback=reference reason=%s",
                        history_code,
                        exc,
                    )
                    continue
                stitched_component = build_ewmac_component_frame(
                    stitched_history,
                    pooled_rules,
                )
                stitched_symbols: list[str] = []
                for symbol in symbols_by_history_code[history_code]:
                    row = source_rows[symbol]
                    try:
                        rules = _parse_rules(
                            str(row["elig_ewmac_rules"] or "")
                        )
                        stitched_result = engine.combine(
                            stitched_component,
                            rules,
                            forecast_correlation,
                            correlation_rules,
                        )
                        currency = str(row["ccy"]).upper()
                        stitched_fx_frame = align_fx_to_history(
                            stitched_result.frame,
                            currency,
                            fx_history=fx_history_by_currency[currency],
                            db_path=args.history_db.expanduser().resolve(),
                        )
                        stitched_reference = (
                            build_subsystem_reference_return_curve(
                                stitched_fx_frame,
                                capital=float(run_config["init_cap_usd"]),
                                target_vol=float(run_config["target_vol"]),
                                reference_book_count=(
                                    args.tie_reference_book_count
                                ),
                                reference_idm=GreedySelectionConfig.max_idm,
                                max_weight_idm=args.max_weight_idm,
                                multiplier=float(row["mult"]),
                                ann_cost_sr=0.0,
                                target_abs_forecast=(
                                    engine.config.target_abs_forecast
                                ),
                            )
                        )
                        stitched_reference_path = work_dir / (
                            f"stitched_reference_{symbol_index[symbol]:04d}.parquet"
                        )
                        stitched_reference.write_parquet(
                            stitched_reference_path
                        )
                        cash_reference_frames[symbol] = pl.scan_parquet(
                            stitched_reference_path
                        )
                        historical_return_sources[symbol] = "stitched"
                        stitched_symbols.append(symbol)
                    except Exception as exc:
                        logger.warning(
                            "selection_stitched_reference unavailable "
                            "symbol=%s hist_instr_code=%s "
                            "fallback=reference reason=%s",
                            symbol,
                            history_code,
                            exc,
                        )
                logger.info(
                    "selection_stitched_history complete hist_instr_code=%s "
                    "rows=%d start=%s end=%s sources=%s symbols=%s",
                    history_code,
                    stitched_history.signal.height,
                    stitched_history.signal.get_column("date").min(),
                    stitched_history.signal.get_column("date").max(),
                    stitched_history.metadata.get("available_sources"),
                    ",".join(stitched_symbols),
                )
                del stitched_component, stitched_history
        finally:
            if live_ib is not None:
                live_ib.disconnect()
    else:
        stitched_history_code_by_symbol = {
            symbol: str(source_rows[symbol]["hist_instr_code"])
            for symbol in cash_symbols
        }

    walk_forward_reference_frames = (
        {
            symbol: _lazy_frame(cash_reference_frames[symbol]).select(
                "date",
                "ref_return_valid",
                "ref_net_pct",
                "daily_dvol",
                "fx_to_usd",
            )
            for symbol in cash_symbols
        }
        if args.selection_rebalance != "static"
        else {}
    )

    # The same reference or stitched subsystem histories feed the causal
    # review cuts and executable cash replay. This selected-book path scales
    # every active market from the same lagged portfolio equity before
    # buffering and rounding. Actual history-policy roll dates are charged in
    # cash.
    cash_output_frames: list[pl.LazyFrame] = []
    static_details = {
        str(detail["symbol"]): detail
        for detail in selection.final_score.details
    }
    for cash_index, symbol in enumerate(cash_symbols, start=1):
        candidate = candidate_by_symbol[symbol]
        detail = static_details.get(symbol, {
            "weight": 1.0,
            "limit_weight": 1.0,
        })
        row = source_rows[symbol]
        cash_reference_frame = _lazy_frame(
            cash_reference_frames[symbol]
        ).collect(engine="streaming")
        cash_frame = build_executable_cash_pnl_curve(
            cash_reference_frame,
            capital=float(run_config["init_cap_usd"]),
            target_vol=float(run_config["target_vol"]),
            weight=float(detail["limit_weight"]),
            idm=(
                selection.final_score.idm
                if args.selection_rebalance == "static"
                else 1.0
            ),
            multiplier=float(row["mult"]),
            trade_cost_usd_per_con=candidate.trade_cost_usd_per_con,
            roll_cost_usd_per_con=candidate.roll_cost_usd_per_con,
            target_abs_forecast=engine.config.target_abs_forecast,
            buffer_size=args.buffer_size,
            buffer_trade_to_edge=args.buffer_trade_to_edge,
            rebalance_on_roll=args.rebalance_on_roll,
        )
        roll_schedule_src = (
            "history_marks"
            if cash_frame.get_column("roll_event").any()
            else "no_history_roll_markers"
        )
        cash_output_frame = cash_frame.with_columns(
                pl.lit(symbol).alias("symbol"),
                pl.lit(stitched_history_code_by_symbol[symbol]).alias(
                    "hist_instr_code"
                ),
                pl.lit(historical_return_sources[symbol]).alias(
                    "historical_return_source"
                ),
                pl.lit(str(row.get("roll_policy_id") or "")).alias(
                    "roll_policy_id"
                ),
                pl.lit(roll_schedule_src).alias("roll_schedule_src"),
                pl.lit(float(detail["weight"])).alias("weight"),
                pl.lit(float(detail["limit_weight"])).alias("limit_weight"),
                pl.lit(
                    selection.final_score.idm
                    if args.selection_rebalance == "static"
                    else 1.0
                ).alias("idm"),
                pl.lit(candidate.trade_cost_usd_per_con).alias(
                    "trade_cost_usd_per_con"
                ),
                pl.lit(candidate.roll_cost_usd_per_con).alias(
                    "roll_cost_usd_per_con"
                ),
            ).select(
                "date",
                "symbol",
                "hist_instr_code",
                "historical_return_source",
                "roll_policy_id",
                "roll_schedule_src",
                "weight",
                "limit_weight",
                "idm",
                # This is the locally generated additive-Panama signal level,
                # not a raw executable dated-contract settlement.
                "close",
                # ``build_executable_cash_pnl_curve`` has already applied the
                # public rename, so retain the mark under its report name.
                "con_px",
                "comb_fcst",
                "pt_change",
                "pt_vol",
                "fx_to_usd",
                "fx_pair",
                "fx_src",
                "roll_event",
                "src_gap_before",
                "src_gap_days",
                "gap_pnl_omitted",
                "risk_budget",
                "ann_dvol",
                "vol_scalar",
                "tgt_pos",
                "buf_size",
                "buf_trade_to_edge",
                "rebalance_on_roll",
                "roll_rebalanced",
                "buf_width_pos",
                "buffer_top_con",
                "buffer_bot_con",
                "exec_con",
                "held_con",
                "trade_con",
                "roll_con",
                "trade_cost_per_con",
                "roll_cost_per_con",
                "gross_pnl",
                "trade_cost",
                "roll_cost",
                "gross_pct",
                "trade_cost_pct",
                "roll_cost_pct",
                "total_cost_pct",
                "net_pct",
                "net_pnl",
                "cum_gross_pnl",
                "cum_net_pnl",
                "cum_gross_pct",
                "cum_trade_cost_pct",
                "cum_roll_cost_pct",
                "cum_total_cost_pct",
                "cum_net_pct",
                "pnl_valid",
            )
        cash_path = work_dir / f"cash_{cash_index:04d}.parquet"
        cash_output_frame.write_parquet(cash_path)
        cash_output_frames.append(pl.scan_parquet(cash_path))
        del cash_reference_frame, cash_frame, cash_output_frame
    unscaled_cash_pnl = (
        pl.concat(cash_output_frames, how="diagonal_relaxed")
        .collect(engine="streaming")
    )
    del cash_output_frames, cash_reference_frames
    gc.collect()
    allocation_provider = None
    if args.selection_rebalance != "static":
        review_dates = _selection_rebalance_dates(
            unscaled_cash_pnl.get_column("date").drop_nulls().to_list(),
            cadence=args.selection_rebalance,
        )
        allocation_provider = _WalkForwardSelectionProvider(
            review_dates=review_dates,
            source_rows={symbol: source_rows[symbol] for symbol in cash_symbols},
            audit_by_symbol={
                symbol: audit_by_symbol[symbol] for symbol in cash_symbols
            },
            reference_frames=walk_forward_reference_frames,
            run_config=run_config,
            args=args,
        )
    del subsystem_reference_frames, walk_forward_reference_frames
    gc.collect()
    cash_pnl_long = build_equity_scaled_cash_pnl_curve(
        unscaled_cash_pnl,
        initial_capital_usd=float(run_config["init_cap_usd"]),
        target_vol=float(run_config["target_vol"]),
        multiplier_by_symbol={
            symbol: float(source_rows[symbol]["mult"])
            for symbol in cash_symbols
        },
        target_abs_forecast=engine.config.target_abs_forecast,
        allocation_provider=allocation_provider,
    )
    if allocation_provider is not None:
        if allocation_provider.latest_selection is None:
            raise ValueError(
                "scheduled selection never produced an eligible book"
            )
        selection = allocation_provider.latest_selection
        candidate_by_symbol = allocation_provider.latest_candidates
        final_frame = _final_selection_frame(
            selection=selection,
            candidate_by_symbol=candidate_by_symbol,
            source_rows=source_rows,
            audit_by_symbol=audit_by_symbol,
            args=args,
            selection_date=allocation_provider.latest_date,
            selection_equity_usd=allocation_provider.latest_equity,
        )
    historical_performance_rows: list[dict[str, object]] = []
    for symbol in selection.selected:
        significance = summarize_return_significance(
            cash_pnl_long.filter(pl.col("symbol") == symbol),
            return_col="net_pct",
        ).row(0, named=True)
        historical_performance_rows.append({
            "symbol": symbol,
            "historical_return_source": historical_return_sources[symbol],
            "historical_instrument_code": (
                stitched_history_code_by_symbol[symbol]
            ),
            "historical_return_start": significance["start"],
            "historical_return_end": significance["end"],
            "historical_return_observations": significance["n_obs"],
            "historical_years": significance["hist_years"],
            "historical_cumulative_net_percent": (
                significance["cum_return_pct"]
            ),
            "historical_annual_mean_percent": significance["ann_mean_pct"],
            "historical_annual_vol_percent": significance["ann_vol_pct"],
            "historical_sharpe": significance["sharpe"],
            "historical_iid_t_stat": significance["iid_t_stat"],
        })
    final_frame = final_frame.join(
        pl.DataFrame(historical_performance_rows, infer_schema_length=None),
        on="symbol",
        how="left",
        validate="1:1",
    )
    portfolio_curve = build_portfolio_equity_curve(
        cash_pnl_long,
        initial_capital_usd=float(run_config["init_cap_usd"]),
    )
    audit_path = output_dir / f"multi_speed_ewmac_selection_forecast_audit_{stamp}.parquet"
    trials_path = output_dir / f"multi_speed_ewmac_selection_trials_{stamp}.parquet"
    trial_allocations_path = (
        output_dir / f"multi_speed_ewmac_selection_trial_allocations_{stamp}.parquet"
    )
    selection_passes_path = (
        output_dir / f"multi_speed_ewmac_selection_passes_{stamp}.parquet"
    )
    selection_path = output_dir / f"multi_speed_ewmac_selection_book_{stamp}.parquet"
    selection_reviews_path = (
        output_dir
        / f"multi_speed_ewmac_selection_reviews_{stamp}.parquet"
    )
    selection_review_instruments_path = (
        output_dir
        / f"multi_speed_ewmac_selection_review_instruments_{stamp}.parquet"
    )
    cash_pnl_path = (
        output_dir / f"multi_speed_ewmac_selection_cash_pnl_{stamp}.parquet"
    )
    portfolio_curve_path = (
        output_dir
        / f"multi_speed_ewmac_selection_portfolio_curve_{stamp}.parquet"
    )
    # Round only persisted public report copies. Full-precision daily analytical
    # frames remain available in Parquet and calculations above are untouched.
    public_audit = round_public_report(rename_public_report_columns(audit_frame))
    public_trials = round_public_report(
        rename_public_report_columns(
            pl.DataFrame(selection.trials, infer_schema_length=None).with_columns(
                pl.lit(args.weight_policy).alias("wt_policy"),
                pl.lit(args.max_instr_per_cluster, dtype=pl.Int64).alias(
                    "max_instr_per_cluster"
                ),
                pl.lit(args.include_roll_cost_in_score).alias(
                    "roll_cost_in_score"
                ),
                pl.lit(args.low_contract_penalty_scale).alias(
                    "low_contract_penalty_scale"
                ),
                pl.lit(args.high_contract_penalty_scale).alias(
                    "high_contract_penalty_scale"
                ),
                pl.lit(args.contract_band_min).alias("contract_band_min"),
                pl.lit(args.contract_band_max).alias("contract_band_max"),
                pl.lit(args.score_tie_tolerance_percent).alias(
                    "score_tie_tolerance_percent"
                ),
                pl.lit(args.tie_reference_book_count).alias(
                    "tie_ref_book_count"
                ),
            )
        )
    )
    public_trial_allocations = round_public_report(
        rename_public_report_columns(
            pl.DataFrame(selection.trial_allocations, infer_schema_length=None)
            .with_columns(
                pl.lit(args.weight_policy).alias("wt_policy"),
                pl.lit(args.max_instr_per_cluster, dtype=pl.Int64).alias(
                    "max_instr_per_cluster"
                ),
                pl.lit(args.include_roll_cost_in_score).alias(
                    "roll_cost_in_score"
                ),
                pl.lit(args.low_contract_penalty_scale).alias(
                    "low_contract_penalty_scale"
                ),
                pl.lit(args.high_contract_penalty_scale).alias(
                    "high_contract_penalty_scale"
                ),
                pl.lit(args.contract_band_min).alias("contract_band_min"),
                pl.lit(args.contract_band_max).alias("contract_band_max"),
                pl.lit(args.score_tie_tolerance_percent).alias(
                    "score_tie_tolerance_percent"
                ),
                pl.lit(args.tie_reference_book_count).alias(
                    "tie_ref_book_count"
                ),
            )
        )
    )
    # The exhaustive trial-allocation audit is useful for reproducing rejected
    # decisions, but the accepted path is the readable answer to "what changed
    # on each selection pass?" Rename the two roles so the newly added symbol
    # cannot be confused with the existing members of its accepted book.
    selection_passes = (
        pl.DataFrame(selection.trial_allocations, infer_schema_length=None)
        .filter(pl.col("accepted"))
        .with_columns(
            pl.lit(args.weight_policy).alias("wt_policy"),
            pl.lit(args.max_instr_per_cluster, dtype=pl.Int64).alias(
                "max_instr_per_cluster"
            ),
            pl.lit(args.include_roll_cost_in_score).alias(
                "roll_cost_in_score"
            ),
            pl.lit(args.low_contract_penalty_scale).alias(
                "low_contract_penalty_scale"
            ),
            pl.lit(args.high_contract_penalty_scale).alias(
                "high_contract_penalty_scale"
            ),
            pl.lit(args.contract_band_min).alias("contract_band_min"),
            pl.lit(args.contract_band_max).alias("contract_band_max"),
            pl.lit(args.score_tie_tolerance_percent).alias(
                "score_tie_tolerance_percent"
            ),
            pl.lit(args.tie_reference_book_count).alias(
                "tie_ref_book_count"
            ),
        )
        .select(
            pl.col("iteration").alias("selection_step"),
            pl.col("candidate").alias("added_symbol"),
            "wt_policy",
            "max_instr_per_cluster",
            "book",
            pl.col("book_symbol").alias("symbol"),
            "asset_cls",
            "econ_subcls",
            "econ_fam_id",
            "cluster_path",
            "cluster_members",
            "cluster_weight",
            "weight",
            "limit_weight",
            "ref_weight",
            "ref_weight_idm",
            "ref_max_position",
            "ref_contract_band_distance",
            "idm",
            "score",
            "base_score",
            "low_contract_weight",
            "band_contract_weight",
            "high_contract_weight",
            "low_contract_penalty_scale",
            "high_contract_penalty_scale",
            "contract_band_min",
            "contract_band_max",
            "score_tie_tolerance_percent",
            "tie_ref_book_count",
            "iter_score_best",
            "iter_score_best_score",
            "tie_score_floor",
            "tie_eligible",
            "tie_break_applied",
            "roll_cost_in_score",
            "score_cost_sr",
            "ann_trade_cost_sr",
            "ann_roll_cost_sr",
            "ann_cost_sr",
            "net_sr",
            "base_net_sr",
            "size_penalty_sr",
            "low_contract_penalty_sr",
            "high_contract_penalty_sr",
            "contract_band_penalty_sr",
            "score_denom",
            "marginal_contribution_score",
            "scaled_contribution",
            "average_position",
            "ann_trade_cons",
            "ann_roll_cons",
            "annual_contract_load",
            "trade_cost_usd_per_con",
            "roll_cost_usd_per_con",
            "ann_trade_cost_usd",
            "ann_roll_cost_usd",
            "ann_cost_usd",
            "max_position",
            "max_cons",
            "cont_margin_at_max_usd",
            "margin_at_max_usd",
            "portfolio_cont_margin_at_max_usd",
            "portfolio_margin_at_max_usd",
            "book_margin_remaining_usd",
            "instr_margin_limit_usd",
            "book_margin_limit_usd",
        )
        .sort("selection_step", "cluster_path", "symbol")
    )
    if "pst_instr_code" in selected.columns:
        selection_passes = selection_passes.join(
            selected.select("symbol", "pst_instr_code").unique("symbol"),
            on="symbol",
            how="left",
            validate="m:1",
        )
        pass_columns = selection_passes.columns
        pass_columns.remove("pst_instr_code")
        pass_columns.insert(
            pass_columns.index("symbol") + 1, "pst_instr_code"
        )
        selection_passes = selection_passes.select(pass_columns)
    public_selection_passes = round_public_report(
        rename_public_report_columns(selection_passes)
    )
    public_selection = round_public_report(
        rename_public_report_columns(final_frame)
    )
    public_audit.write_parquet(audit_path)
    public_trials.write_parquet(trials_path)
    public_trial_allocations.write_parquet(trial_allocations_path)
    public_selection_passes.write_parquet(selection_passes_path)
    public_selection.write_parquet(selection_path)
    if allocation_provider is not None:
        public_selection_reviews = round_public_report(
            rename_public_report_columns(
                pl.DataFrame(
                    allocation_provider.review_rows,
                    infer_schema_length=None,
                )
            )
        )
        public_selection_reviews.write_parquet(selection_reviews_path)
        public_selection_review_instruments = round_public_report(
            rename_public_report_columns(
                pl.DataFrame(
                    allocation_provider.review_instrument_rows,
                    infer_schema_length=None,
                ).sort(
                    "selection_date",
                    "selection_active",
                    "rank",
                    "symbol",
                    descending=[False, True, False, False],
                    nulls_last=True,
                )
            )
        )
        public_selection_review_instruments.write_parquet(
            selection_review_instruments_path
        )
    public_cash_pnl = round_public_report(
        order_cash_pnl_report(rename_public_report_columns(cash_pnl_long))
    )
    public_cash_pnl.write_parquet(cash_pnl_path)
    public_portfolio_curve = round_public_report(
        order_portfolio_curve_report(
            rename_public_report_columns(portfolio_curve)
        )
    )
    public_portfolio_curve.write_parquet(portfolio_curve_path)
    work_tempdir.cleanup()
    print(
        f"Portfolio selection accepted {len(selection.selected)}/{len(candidate_list)} "
        f"forecast-eligible instruments"
    )
    print(f"Saved forecast audit {audit_path}")
    print(f"Saved daily forecasts {forecasts_path}")
    print(f"Saved synchronized subsystem reference returns {reference_return_path}")
    print(f"Saved executable cash P&L {cash_pnl_path}")
    print(f"Saved portfolio equity curve {portfolio_curve_path}")
    print(f"Saved trials {trials_path}")
    print(f"Saved trial allocations {trial_allocations_path}")
    print(f"Saved accepted selection passes {selection_passes_path}")
    if allocation_provider is not None:
        print(f"Saved selection reviews {selection_reviews_path}")
        print(
            "Saved selection review instruments "
            f"{selection_review_instruments_path}"
        )
    print(f"Saved selection {selection_path}")
    return selection_path, public_selection


def run(
    argv=None,
    *,
    complete_default: bool = False,
) -> tuple[Path, pl.DataFrame]:
    """Filter a rule cache and optionally run the complete greedy search.

    Returns the last output path and dataframe produced: the compact candidate
    universe normally, or the final greedy selection when ``--complete`` is
    supplied.
    """
    args = parse_args(argv, complete_default=complete_default)
    if (
        args.low_contract_penalty_scale < 0
        or args.high_contract_penalty_scale < 0
    ):
        raise ValueError("contract penalty scales must be non-negative")
    if (
        args.contract_band_min <= 0
        or args.contract_band_max <= args.contract_band_min
    ):
        raise ValueError("contract band must have positive increasing bounds")
    if not 0 <= args.score_tie_tolerance_percent <= 100:
        raise ValueError("score tie tolerance must be in [0, 100]")
    if args.tie_reference_book_count <= 0:
        raise ValueError("tie reference book count must be positive")
    if args.max_source_gap_days < 0:
        raise ValueError("max source gap days must be nonnegative")
    if args.ib_history_live is not None and args.ib_history_db is None:
        raise ValueError("--ib-history-live requires --ib-history-db")
    if args.ib_history_db is not None:
        ib_history_db = args.ib_history_db.expanduser().resolve()
        if not ib_history_db.is_file() and args.ib_history_live is None:
            raise FileNotFoundError(
                f"stored IB history database does not exist: {ib_history_db}"
            )
        if not ib_history_db.is_file():
            IBFuturesHistoryStore(ib_history_db).initialize()
    setup_logger()
    if args.output is not None and args.output.suffix.lower() != ".parquet":
        raise ValueError("--output must use a .parquet extension")

    if args.input is None:
        source, report = load_latest_market_report(args.report_dir)
    else:
        source = args.input.expanduser().resolve()
        report = read_market_report(source)

    logger.info(
        "selection_candidate_filter start source=%s instruments=%d",
        source,
        report.height,
    )
    config = resolve_selection_config(
        report,
        initial_capital_usd=args.initial_capital_usd,
        target_vol=args.target_vol,
        instrument_cost_limit_sr=args.instrument_cost_limit_sr,
        liquidity_ann_trades=args.liquidity_ann_trades,
        liquidity_business_days=args.liquidity_business_days,
        min_daily_volume=args.min_daily_volume,
        max_market_volume_pct=args.max_market_volume_pct,
    )
    report = apply_selection_filters(report, config=config)
    logger.info(
        "selection_candidate_filter config init_cap_usd=%s target_vol=%s "
        "cost_lim_sr=%s "
        "rule_cost_lim_sr=%s liq_ann_trades=%s liq_days=%s "
        "min_daily_volume_cons=%s max_pct_mkt_volume=%s",
        config["init_cap_usd"],
        config["target_vol"],
        config["cost_lim_sr"],
        config["rule_cost_lim_sr"],
        config["liq_ann_trades"],
        config["liq_days"],
        config["min_daily_volume"],
        config["max_pct_mkt_volume"],
    )
    selected = (
        selection_candidates(report)
        if args.selection_rebalance == "static"
        else selection_rebalance_candidates(report)
    )
    selected_symbols = set(selected.get_column("symbol").to_list())
    for row in report.select(
        name
        for name in (
            "symbol",
            "ann_dvol",
            "avg_daily_volume",
            "pct_mkt_volume",
            "selection_excl",
        )
        if name in report.columns
    ).iter_rows(named=True):
        symbol = row.get("symbol")
        if symbol in selected_symbols:
            logger.debug(
                "selection_candidate_filter accepted symbol=%s ann_dvol=%s "
                "avg_daily_volume_cons=%s pct_mkt_volume=%s",
                symbol,
                row.get("ann_dvol"),
                row.get("avg_daily_volume"),
                row.get("pct_mkt_volume"),
            )
        else:
            logger.debug(
                "selection_candidate_filter rejected symbol=%s ann_dvol=%s "
                "avg_daily_volume_cons=%s pct_mkt_volume=%s reason=%s",
                symbol,
                row.get("ann_dvol"),
                row.get("avg_daily_volume"),
                row.get("pct_mkt_volume"),
                row.get("selection_excl"),
            )

    generated_at = datetime.now(REPORT_TIMEZONE)
    report_output_dir = (
        _selection_run_output_dir(
            args.output_dir.expanduser().resolve(),
            generated_at=generated_at,
            run_config=config,
            args=args,
        )
        if args.output_dir is not None
        else source.parent
    )
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else _default_output_path(report_output_dir, generated_at)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    screen_path = report_output_dir / (
        "multi_speed_ewmac_selection_screen_"
        f"{generated_at.astimezone(REPORT_TIMEZONE).strftime('%Y%m%d_%H%M%S')}"
        ".parquet"
    )
    screen_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the complete instrument universe and its pass/fail reasons so the
    # embedded selection screen is auditable alongside the passing candidates.
    public_screen = round_public_report(report)
    public_screen.write_parquet(screen_path)
    # Candidate filtering is a public report boundary. Keep ``selected`` at calculation
    # precision for the optional complete search and round only its report copy.
    public_selected = round_public_report(selected)
    public_selected.write_parquet(output)
    logger.info(
        "selection_candidate_filter complete source=%s screen=%s output=%s "
        "selected=%d total=%d",
        source,
        screen_path,
        output,
        selected.height,
        report.height,
    )
    print(
        f"Candidate filter accepted {selected.height}/{report.height} instruments"
    )
    print(
        "Parameters: "
        f"init_cap_usd={config['init_cap_usd']} "
        f"target_vol={config['target_vol']} "
        f"cost_lim_sr={config['cost_lim_sr']} "
        f"rule_cost_lim_sr={config['rule_cost_lim_sr']} "
        f"liq_ann_trades={config['liq_ann_trades']} "
        f"liq_days={config['liq_days']} "
        f"min_daily_volume={config['min_daily_volume']} "
        f"max_pct_mkt_volume={config['max_pct_mkt_volume']}"
    )
    print(f"Saved selection screen {screen_path}")
    print(f"Saved {output}")
    if args.complete:
        return _run_complete_selection(
            selected,
            output_dir=report_output_dir,
            generated_at=generated_at,
            run_config=config,
            args=args,
        )
    return output, public_selected


def main(argv=None) -> None:
    """Backward-compatible candidate-only CLI entry point."""
    run(argv)


def build_selection_main(argv=None) -> None:
    """Build candidates and the complete book unless explicitly disabled."""
    run(argv, complete_default=True)


if __name__ == "__main__":
    main()

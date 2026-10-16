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
``subsystem_frames``
    One daily combined-forecast, position, historical-FX, and net-P&L frame per
    executable ``symbol``. Symbols sharing history may differ here because
    their Phase 1 cost gates can leave them with different eligible rules.
``net_pnl_wide``
    One synchronized frame with dates in rows and executable symbols in
    columns. This is the input to the instrument-correlation estimator.
``cash_pnl_long``
    One long account-scaled frame for the final selected book. It applies the
    forecast buffer, rounds to contracts, and separates ordinary trade cost
    from realized and interval-average policy-roll cost. The generated Panama
    ``close`` is retained beside its roll-neutral ``point_change`` for audit.

The command persists each intermediate form so forecast gaps, turnover, and
greedy selection decisions remain inspectable after a run.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from derivatives_bt_engine.data.futures_cost_rankings import (
    apply_phase2_selection_filters,
    load_latest_phase1_cost_report,
    phase2_step1_candidates,
    read_phase1_cost_report,
    resolve_phase2_selection_config,
)
from derivatives_bt_engine.data.fx_history import (
    align_fx_to_history,
    load_fx_to_usd_history,
)
from derivatives_bt_engine.pipelines.pysystemtrade_ewmac import (
    load_ewmac_component_frames,
)
from derivatives_bt_engine.reports.formatting import (
    rename_public_report_columns,
    round_public_report,
)
from derivatives_bt_engine.calculations.correlation import (
    bounded_pairwise_ewm_correlation_matrix,
)
from derivatives_bt_engine.calculations.costs import calculate_annual_sr_cost
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastEngine,
    ForecastCombinationConfig,
    SlowTiltEwmacWeights,
    median_forecast_correlation,
)
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
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
    build_executable_cash_pnl_curve,
    build_subsystem_pnl_curve,
)
from derivatives_bt_engine.logging_config import setup_logger


logger = logging.getLogger(__name__)
REPORT_TIMEZONE = ZoneInfo("America/Chicago")

# Production-inspired ordinary-EWMAC template. The two slowest rules receive
# 60% in total and the three faster rules receive 40%. Rules removed by the
# Phase 1 cost ceiling are set to zero and the survivors are renormalised.
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
    """Return slow-tilted weights for the rules that survived Phase 1.

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
        help="Explicit rule-cache Parquet; legacy Phase 1 CSV is also accepted.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("."),
        help="Directory (and its results/ child) searched for the latest report.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Exact candidate output path; defaults beside the input cache.",
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
        "--pysystemtrade-db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
        help="Reviewed pysystemtrade history database.",
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
    return parser.parse_args(argv)


def _default_output_path(source: Path, generated_at: datetime) -> Path:
    """Return the timestamped candidate path beside its rule-cache source."""
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return source.parent / f"pysystemtrade_selection_candidates_{stamp}.parquet"


def _label_forecast_output_frame(
    frame: pl.DataFrame,
    *,
    symbol: str,
    hist_instr_code: str,
) -> pl.DataFrame:
    """Attach executable identity with ``symbol`` immediately after ``date``.

    Phase 2 calculations keep executable identity outside each per-symbol
    dataframe. This helper adds it only at the persisted long-frame boundary
    and gives the identifiers a stable, scan-friendly leading order.
    """
    if "date" not in frame.columns:
        raise ValueError("forecast output frame is missing date")
    labeled = frame.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(hist_instr_code).alias("hist_instr_code"),
    )
    leading = ["date", "symbol", "hist_instr_code"]
    trailing = [column for column in labeled.columns if column not in leading]
    return labeled.select(leading + trailing)


def _subsystem_correlation(
    subsystem_frames: dict[str, pl.DataFrame],
    symbols: list[str],
    *,
    window_years: float,
    halflife_days: float,
    min_rows: int,
) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    """Estimate instrument correlations from combined subsystem net USD P&L.

    ``subsystem_frames`` is keyed by executable symbol, unlike
    ``frames_by_history`` above. The function outer-joins each symbol's
    ``net_pnl_usd`` on date to form a sparse ``net_pnl_wide`` panel. The
    estimator uses each pair's contemporaneous observations, so different
    history starts and exchange holidays do not erase the entire universe.

    Returns the dense correlation matrix, its per-symbol coverage mask, and
    the wide return frame that was actually used.
    """
    wide: pl.DataFrame | None = None
    for symbol in symbols:
        returns = (
            subsystem_frames[symbol]
            .select("date", pl.col("net_pnl_usd").alias(symbol))
            .drop_nulls()
        )
        # Preserve the union of dates here. Requiring a date shared by every
        # candidate makes one young or differently calendared instrument erase
        # otherwise valid histories for the whole Phase 2 universe.
        wide = returns if wide is None else wide.join(
            returns, on="date", how="full", coalesce=True
        )
    if wide is None or wide.is_empty():
        return np.eye(len(symbols)), np.zeros(len(symbols), dtype=bool), pl.DataFrame()
    wide = wide.sort("date")
    last = wide.get_column("date").max()
    as_of = (last.date() if isinstance(last, datetime) else last) + timedelta(days=1)
    correlation, covered = bounded_pairwise_ewm_correlation_matrix(
        wide,
        symbols,
        as_of,
        window_years,
        halflife_days,
        min_rows=min_rows,
    )
    return correlation, covered, wide


def _run_complete_selection(
    selected: pl.DataFrame,
    *,
    source: Path,
    generated_at: datetime,
    run_config: dict[str, object],
    args: argparse.Namespace,
) -> tuple[Path, pl.DataFrame]:
    """Audit forecasts, exclude invalid instruments, and run greedy Phase 2.

    ``selected`` contains one row per executable symbol from candidate filtering. The
    function first builds reusable history-level forecasts, then creates a
    symbol-level combined subsystem using that symbol's eligible-rule subset.
    It persists the daily forecast panel, audit, correlation input, greedy
    trials, and final accepted book.

    Returns the final selection Parquet path and its in-memory dataframe.
    """
    logger.info(
        "phase2_complete start candidates=%d buffer_size=%s "
        "buffer_trade_to_edge=%s",
        selected.height,
        args.buffer_size,
        args.buffer_trade_to_edge,
    )
    frames_by_history = load_ewmac_component_frames(
        selected.get_column("hist_instr_code").drop_nulls().to_list(),
        db_path=args.pysystemtrade_db.expanduser().resolve(),
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
    forecast_output_frames: list[pl.DataFrame] = []
    # Executable symbol -> combined forecast, position, FX, and net-P&L history.
    # MZC and CORN_mini can point to the same raw history but remain separate
    # because their eligible rules and transaction costs can differ.
    subsystem_frames: dict[str, pl.DataFrame] = {}
    # Historical FX is identical for every instrument in a currency. Cache it
    # at the pipeline boundary instead of reopening DuckDB for every symbol.
    fx_history_by_currency: dict[str, pl.DataFrame | None] = {}
    # Preserve each compact candidate row for later sizing and liquidity inputs.
    source_rows: dict[str, dict[str, object]] = {}
    for row in selected.iter_rows(named=True):
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
                f"{symbol}: Phase 1 row is missing complete-selection fields "
                f"{missing_cost_fields}; rerun the current futures-cost-risk audit"
            )
        if not str(row.get("ccy") or "").strip():
            raise ValueError(
                f"{symbol}: Phase 1 row is missing ccy; rerun the current "
                "futures-cost-risk audit"
            )

        annual_cost = calculate_annual_sr_cost(
            trade_sr=float(row["trade_sr"]),
            roll_sr=float(row["roll_sr"]),
            subsystem_turnover=float(result.turnover or 0.0),
            ann_rolls=float(row["ref_ann_rolls"]),
        )
        currency = str(row["ccy"]).upper()
        fx_error = ""
        if currency not in fx_history_by_currency:
            try:
                fx_history_by_currency[currency] = (
                    None if currency == "USD" else load_fx_to_usd_history(
                        currency,
                        db_path=args.pysystemtrade_db.expanduser().resolve(),
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
                db_path=args.pysystemtrade_db.expanduser().resolve(),
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
        pnl_frame = build_subsystem_pnl_curve(
            fx_frame,
            multiplier=float(row["mult"]),
            ann_cost_sr=annual_cost.ann_cost_sr,
        ).with_columns(
            pl.lit(annual_cost.ann_trade_cost_sr).alias("ann_trade_cost_sr"),
            pl.lit(annual_cost.ann_roll_cost_sr).alias("ann_roll_cost_sr"),
            pl.lit(annual_cost.ann_cost_sr).alias("ann_cost_sr"),
        )
        subsystem_frames[symbol] = pnl_frame

        pnl_valid = pnl_frame.filter(pl.col("pnl_valid"))
        first_pnl_date = (
            pnl_valid.get_column("date").min() if pnl_valid.height else None
        )
        pnl_after_start = (
            pnl_frame.filter(pl.col("date") >= first_pnl_date)
            if first_pnl_date is not None
            else pnl_frame
        )
        pnl_post_start_invalid = pnl_after_start.filter(
            pl.col("forecast_valid") & ~pl.col("pnl_valid")
        ).height
        fx_missing_obs = pnl_frame.filter(
            pl.col("forecast_valid") & pl.col("fx_to_usd").is_null()
        ).height
        fx_used = pnl_frame.filter(pl.col("fx_to_usd").is_not_null())
        fx_pairs = fx_used.get_column("fx_pair").drop_nulls().unique().to_list()
        fx_sources = fx_used.get_column("fx_src").drop_nulls().unique().to_list()
        subsystem_elig = bool(
            result.audit["forecast_elig"]
            and pnl_valid.height >= args.min_forecast_obs
            and pnl_post_start_invalid == 0
        )
        if not result.audit["forecast_elig"]:
            subsystem_excl = str(result.audit["forecast_excl"])
        elif pnl_valid.height < args.min_forecast_obs:
            subsystem_excl = "insufficient_valid_pnl_obs"
        elif pnl_post_start_invalid:
            subsystem_excl = "pnl_gaps_after_start"
        else:
            subsystem_excl = ""
        audit = {
            "symbol": symbol,
            "hist_instr_code": row["hist_instr_code"],
            "elig_ewmac_rules": ",".join(result.weights),
            "forecast_weights": json.dumps(result.weights, sort_keys=True),
            "fdm": result.fdm,
            "comb_turnover": result.turnover,
            "ann_trade_cost_sr": annual_cost.ann_trade_cost_sr,
            "ann_roll_cost_sr": annual_cost.ann_roll_cost_sr,
            "ann_cost_sr": annual_cost.ann_cost_sr,
            "pnl_valid_obs": pnl_valid.height,
            "pnl_post_start_invalid_obs": pnl_post_start_invalid,
            "pnl_start": first_pnl_date,
            "pnl_end": (
                pnl_valid.get_column("date").max() if pnl_valid.height else None
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
        forecast_output_frames.append(
            _label_forecast_output_frame(
                pnl_frame,
                symbol=symbol,
                hist_instr_code=str(row["hist_instr_code"]),
            )
        )
        logger.log(
            logging.INFO if subsystem_elig else logging.DEBUG,
            "phase2_subsystem_audit symbol=%s eligible=%s forecast_obs=%s "
            "pnl_obs=%s post_start_invalid_obs=%s comb_turnover=%s reason=%s",
            symbol,
            subsystem_elig,
            result.audit["forecast_valid_obs"],
            pnl_valid.height,
            pnl_post_start_invalid,
            result.turnover,
            subsystem_excl,
        )

    audit_frame = pl.DataFrame(audit_rows, infer_schema_length=None).sort("symbol")
    eligible_audit = audit_frame.filter(pl.col("subsystem_elig"))
    eligible_symbols = eligible_audit.get_column("symbol").to_list()
    if not eligible_symbols:
        raise ValueError("no instrument passed the combined-forecast audit")

    # Correlations belong to executable subsystem returns, not raw market
    # returns or the individual EWMAC component forecasts.
    correlation, covered, net_pnl_wide = _subsystem_correlation(
        subsystem_frames,
        eligible_symbols,
        window_years=args.corr_window_years,
        halflife_days=args.corr_halflife_days,
        min_rows=args.corr_min_rows,
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
        raise ValueError("no instrument has sufficient pairwise subsystem returns")

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
    if args.weight_policy == "legacy_handcraft":
        weight_policy = HandcraftedPortfolioWeights()
    elif args.weight_policy == "equal":
        weight_policy = EqualPortfolioWeights()
    else:
        weight_policy = None
    effective_book_margin_pct = args.max_book_margin_pct
    if effective_book_margin_pct is None and args.max_instr_margin_pct is not None:
        effective_book_margin_pct = 100.0
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=float(run_config["init_cap_usd"]),
            target_vol=float(run_config["target_vol"]),
            gross_sr=args.gross_sr,
            score_threshold=args.score_threshold,
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
    selection = selector.select(candidate_list, correlation)
    for trial in selection.trials:
        logger.debug(
            "phase2_trial iteration=%s candidate=%s book=%s score=%s idm=%s "
            "portfolio_cont_margin_at_max_usd=%s "
            "portfolio_margin_at_max_usd=%s instr_margin_limit_usd=%s "
            "book_margin_limit_usd=%s book_margin_remaining_usd=%s "
            "roll_cost_in_score=%s feasible=%s accepted=%s reason=%s",
            trial["iteration"], trial["candidate"], trial["book"], trial["score"],
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
        "phase2_selection complete selected=%d candidates=%d score=%s idm=%s book=%s",
        len(selection.selected), len(candidate_list), selection.final_score.score,
        selection.final_score.idm, ",".join(selection.selected),
    )

    candidate_by_symbol = {
        candidate.symbol: candidate for candidate in candidate_list
    }
    final_details = {
        detail["symbol"]: detail for detail in selection.final_score.details
    }
    final_rows = []
    for rank, symbol in enumerate(selection.selected, start=1):
        candidate = candidate_by_symbol[symbol]
        final_rows.append({
            "rank": rank,
            "symbol": symbol,
            "wt_policy": args.weight_policy,
            "max_instr_per_cluster": args.max_instr_per_cluster,
            "max_instr_margin_pct": args.max_instr_margin_pct,
            "max_book_margin_pct": effective_book_margin_pct,
            "roll_cost_in_score": args.include_roll_cost_in_score,
            "buffer_size": args.buffer_size,
            "buffer_trade_to_edge": args.buffer_trade_to_edge,
            "weight": selection.final_score.weights[symbol],
            "idm": selection.final_score.idm,
            "ann_dvol": candidate.ann_dvol,
            "trade_sr": candidate.trade_sr,
            "comb_turnover": candidate.combined_turnover,
            "ann_trade_cost_sr": audit_by_symbol[symbol]["ann_trade_cost_sr"],
            "ann_roll_cost_sr": audit_by_symbol[symbol]["ann_roll_cost_sr"],
            "ann_cost_sr": audit_by_symbol[symbol]["ann_cost_sr"],
            **final_details[symbol],
        })
    final_frame = pl.DataFrame(final_rows, infer_schema_length=None)

    # Preserve the normalized cost-adjusted P&L above for correlations. This
    # second path restores account capital and final-book sizing, rounds to
    # executable contracts, and charges actual history-policy roll dates.
    cash_output_frames: list[pl.DataFrame] = []
    for symbol in selection.selected:
        candidate = candidate_by_symbol[symbol]
        detail = final_details[symbol]
        row = source_rows[symbol]
        cash_frame = build_executable_cash_pnl_curve(
            subsystem_frames[symbol],
            capital=float(run_config["init_cap_usd"]),
            target_vol=float(run_config["target_vol"]),
            weight=float(detail["limit_weight"]),
            idm=selection.final_score.idm,
            multiplier=float(row["mult"]),
            trade_cost_usd_per_con=candidate.trade_cost_usd_per_con,
            roll_cost_usd_per_con=candidate.roll_cost_usd_per_con,
            target_abs_forecast=engine.config.target_abs_forecast,
            buffer_size=args.buffer_size,
            buffer_trade_to_edge=args.buffer_trade_to_edge,
        )
        roll_schedule_src = (
            "history_marks"
            if cash_frame.get_column("roll_event").any()
            else "no_history_roll_markers"
        )
        cash_output_frames.append(
            cash_frame.with_columns(
                pl.lit(symbol).alias("symbol"),
                pl.lit(str(row["hist_instr_code"])).alias("hist_instr_code"),
                pl.lit(str(row.get("roll_policy_id") or "")).alias(
                    "roll_policy_id"
                ),
                pl.lit(roll_schedule_src).alias("roll_schedule_src"),
                pl.lit(float(detail["weight"])).alias("weight"),
                pl.lit(float(detail["limit_weight"])).alias("limit_weight"),
                pl.lit(selection.final_score.idm).alias("idm"),
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
                "roll_policy_id",
                "roll_schedule_src",
                "weight",
                "limit_weight",
                "idm",
                # This is the locally generated additive-Panama signal level,
                # not a raw executable dated-contract settlement.
                "close",
                "combined_forecast",
                "point_change",
                "point_vol",
                "fx_to_usd",
                "fx_pair",
                "fx_src",
                "roll_event",
                "roll_interval",
                "risk_budget_usd",
                "ann_dvol",
                "avg_cons",
                "target_cons",
                "buffer_size",
                "buffer_trade_to_edge",
                "buffer_width_cons",
                "buffer_top_cons",
                "buffer_bot_cons",
                "exec_cons",
                "held_cons",
                "trade_cons",
                "realized_roll_cons",
                "interval_avg_abs_cons",
                "expected_roll_cons",
                "trade_cost_usd_per_con",
                "roll_cost_usd_per_con",
                "cash_gross_pnl_usd",
                "cash_trade_cost_usd",
                "cash_realized_roll_cost_usd",
                "cash_expected_roll_cost_usd",
                "cash_realized_net_pnl_usd",
                "cash_expected_net_pnl_usd",
                "cash_pnl_valid",
            )
        )
    cash_pnl_long = pl.concat(cash_output_frames, how="diagonal_relaxed")
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    output_dir = source.parent
    audit_path = output_dir / f"pysystemtrade_selection_forecast_audit_{stamp}.parquet"
    forecasts_path = output_dir / f"pysystemtrade_selection_forecasts_{stamp}.parquet"
    trials_path = output_dir / f"pysystemtrade_selection_trials_{stamp}.parquet"
    trial_allocations_path = (
        output_dir / f"pysystemtrade_selection_trial_allocations_{stamp}.parquet"
    )
    selection_passes_path = (
        output_dir / f"pysystemtrade_selection_passes_{stamp}.parquet"
    )
    selection_path = output_dir / f"pysystemtrade_selection_book_{stamp}.parquet"
    net_pnl_path = (
        output_dir / f"pysystemtrade_selection_subsystem_net_pnl_{stamp}.parquet"
    )
    cash_pnl_path = (
        output_dir / f"pysystemtrade_selection_cash_pnl_{stamp}.parquet"
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
            "idm",
            "score",
            "roll_cost_in_score",
            "score_cost_sr",
            "ann_trade_cost_sr",
            "ann_roll_cost_sr",
            "ann_cost_sr",
            "net_sr",
            "size_penalty_sr",
            "score_denom",
            "marginal_contribution_score",
            "scaled_contribution",
            "avg_cons",
            "ann_trade_cons",
            "ann_roll_cons",
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
    public_selection_passes = round_public_report(
        rename_public_report_columns(selection_passes)
    )
    public_selection = round_public_report(
        rename_public_report_columns(final_frame)
    )
    public_audit.write_parquet(audit_path)
    pl.concat(forecast_output_frames, how="diagonal_relaxed").write_parquet(
        forecasts_path
    )
    public_trials.write_parquet(trials_path)
    public_trial_allocations.write_parquet(trial_allocations_path)
    public_selection_passes.write_parquet(selection_passes_path)
    public_selection.write_parquet(selection_path)
    net_pnl_wide.write_parquet(net_pnl_path)
    cash_pnl_long.write_parquet(cash_pnl_path)
    print(
        f"Phase 2 selected {len(selection.selected)}/{len(candidate_list)} "
        f"forecast-eligible instruments"
    )
    print(f"Saved forecast audit {audit_path}")
    print(f"Saved daily forecasts {forecasts_path}")
    print(f"Saved synchronized net P&L {net_pnl_path}")
    print(f"Saved executable cash P&L {cash_pnl_path}")
    print(f"Saved trials {trials_path}")
    print(f"Saved trial allocations {trial_allocations_path}")
    print(f"Saved accepted selection passes {selection_passes_path}")
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
    setup_logger()
    if args.output is not None and args.output.suffix.lower() != ".parquet":
        raise ValueError("--output must use a .parquet extension")

    if args.input is None:
        source, report = load_latest_phase1_cost_report(args.report_dir)
    else:
        source = args.input.expanduser().resolve()
        report = read_phase1_cost_report(source)

    logger.info(
        "selection_candidate_filter start source=%s instruments=%d",
        source,
        report.height,
    )
    config = resolve_phase2_selection_config(
        report,
        initial_capital_usd=args.initial_capital_usd,
        target_vol=args.target_vol,
        instrument_cost_limit_sr=args.instrument_cost_limit_sr,
        liquidity_ann_trades=args.liquidity_ann_trades,
        liquidity_business_days=args.liquidity_business_days,
        min_daily_volume=args.min_daily_volume,
        max_market_volume_pct=args.max_market_volume_pct,
    )
    report = apply_phase2_selection_filters(report, config=config)
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
    selected = phase2_step1_candidates(report)
    selected_symbols = set(selected.get_column("symbol").to_list())
    for row in report.select(
        name
        for name in (
            "symbol",
            "ann_dvol",
            "avg_daily_volume",
            "pct_mkt_volume",
            "phase2_excl",
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
                row.get("phase2_excl"),
            )

    generated_at = datetime.now(REPORT_TIMEZONE)
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else _default_output_path(source, generated_at)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # Candidate filtering is a public report boundary. Keep ``selected`` at calculation
    # precision for the optional complete search and round only its report copy.
    public_selected = round_public_report(selected)
    public_selected.write_parquet(output)
    logger.info(
        "selection_candidate_filter complete source=%s output=%s "
        "selected=%d total=%d",
        source,
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
    print(f"Saved {output}")
    if args.complete:
        return _run_complete_selection(
            selected,
            source=source,
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

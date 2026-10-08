"""Build and score the Phase 2 instrument-selection universe.

The persisted Phase 1 CSV supplies executable symbols, their borrowed signal
history identifiers, costs, dollar volatility, and eligibility flags. Phase 2
then uses three deliberately different dataframe collections:

``frames_by_history``
    One daily component-forecast frame per unique ``hist_instr_code``. Several
    executable symbols can reuse the same history, so these expensive EWMAC
    calculations are performed once.
``subsystem_frames``
    One daily combined-forecast, position, and return frame per executable
    ``symbol``. Symbols sharing history may differ here because their Phase 1
    cost gates can leave them with different eligible rules.
``returns_wide``
    One synchronized frame with dates in rows and executable symbols in
    columns. This is the input to the instrument-correlation estimator.

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
    load_latest_phase1_cost_report,
    phase2_step1_candidates,
    phase2_step1_config,
)
from derivatives_bt_engine.data.pysystemtrade_ewmac import (
    load_ewmac_component_frames,
)
from derivatives_bt_engine.data.report_formatting import round_public_report
from derivatives_bt_engine.domain.correlation import bounded_ewm_correlation_matrix
from derivatives_bt_engine.domain.forecast_combination import (
    CombinedForecastEngine,
    ForecastCombinationConfig,
    SlowTiltEwmacWeights,
    median_forecast_correlation,
)
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)
from derivatives_bt_engine.domain.instrument_selection import (
    GreedyInstrumentSelector,
    GreedySelectionConfig,
    SelectionCandidate,
)
from derivatives_bt_engine.utils.logger import setup_logger


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
    """Normalize a CSV rule field or iterable into non-empty rule keys."""
    if isinstance(eligible_rules, str):
        return [rule.strip() for rule in eligible_rules.split(",") if rule.strip()]
    return [str(rule).strip() for rule in eligible_rules if str(rule).strip()]


def parse_args(argv=None):
    """Parse the Step 1 and optional complete Phase 2 command arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Load a Phase 1 futures cost CSV and save the instruments that "
            "already passed its cost, dollar-vol, liquidity, data, rule, "
            "execution, and IB-availability gates."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit Phase 1 CSV; otherwise use the latest report.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("."),
        help="Directory (and its results/ child) searched for the latest CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Exact Step 1 output path; defaults beside the input report.",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Build combined forecasts and run the iterative greedy selector.",
    )
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
    parser.add_argument("--starting-weight", type=float, default=0.20)
    parser.add_argument("--corr-window-years", type=float, default=20.0)
    parser.add_argument("--corr-halflife-days", type=float, default=250.0)
    parser.add_argument("--corr-min-rows", type=int, default=256)
    return parser.parse_args(argv)


def _default_output_path(source: Path, generated_at: datetime) -> Path:
    """Return the timestamped Step 1 CSV path beside its Phase 1 source."""
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return source.parent / f"pysystemtrade_phase2_step1_{stamp}.csv"


def _subsystem_correlation(
    subsystem_frames: dict[str, pl.DataFrame],
    symbols: list[str],
    *,
    window_years: float,
    halflife_days: float,
    min_rows: int,
) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    """Estimate instrument correlations from combined subsystem returns.

    ``subsystem_frames`` is keyed by executable symbol, unlike
    ``frames_by_history`` above. The function inner-joins each symbol's
    ``subsystem_return`` on date to form ``returns_wide`` and passes that
    synchronized panel to the allocation layer's bounded EWM estimator.

    Returns the dense correlation matrix, its per-symbol coverage mask, and
    the wide return frame that was actually used.
    """
    wide: pl.DataFrame | None = None
    for symbol in symbols:
        returns = (
            subsystem_frames[symbol]
            .select("date", pl.col("subsystem_return").alias(symbol))
            .drop_nulls()
        )
        # Inner joining makes every matrix row a genuinely contemporaneous
        # observation across the candidate universe.
        wide = returns if wide is None else wide.join(
            returns, on="date", how="inner"
        )
    if wide is None or wide.is_empty():
        return np.eye(len(symbols)), np.zeros(len(symbols), dtype=bool), pl.DataFrame()
    wide = wide.sort("date").drop_nulls()
    last = wide.get_column("date").max()
    as_of = (last.date() if isinstance(last, datetime) else last) + timedelta(days=1)
    correlation, covered = bounded_ewm_correlation_matrix(
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

    ``selected`` contains one row per executable symbol from Step 1. The
    function first builds reusable history-level forecasts, then creates a
    symbol-level combined subsystem using that symbol's eligible-rule subset.
    It persists the daily forecast panel, audit, correlation input, greedy
    trials, and final accepted book.

    Returns the final selection CSV path and its in-memory dataframe.
    """
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
    # Executable symbol -> its combined forecast, position, and return history.
    # MZC and CORN_mini can point to the same raw history but remain separate
    # because their eligible rules and transaction costs can differ.
    subsystem_frames: dict[str, pl.DataFrame] = {}
    # Preserve each compact Step 1 row for later sizing and liquidity inputs.
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
        subsystem_frames[symbol] = result.frame
        audit = {
            "symbol": symbol,
            "hist_instr_code": row["hist_instr_code"],
            "elig_ewmac_rules": ",".join(result.weights),
            "forecast_weights": json.dumps(result.weights, sort_keys=True),
            "fdm": result.fdm,
            "comb_turnover": result.turnover,
            "ann_trade_cost_sr": (
                float(row["trade_sr"]) * result.turnover
                if row.get("trade_sr") is not None and result.turnover is not None
                else None
            ),
            **result.audit,
        }
        audit_rows.append(audit)
        forecast_output_frames.append(
            result.frame.with_columns(
                pl.lit(symbol).alias("symbol"),
                pl.lit(str(row["hist_instr_code"])).alias("hist_instr_code"),
            )
        )
        logger.log(
            logging.INFO if result.audit["forecast_elig"] else logging.DEBUG,
            "phase2_forecast_audit symbol=%s eligible=%s valid_obs=%s "
            "post_warmup_invalid_obs=%s comb_turnover=%s reason=%s",
            symbol,
            result.audit["forecast_elig"],
            result.audit["forecast_valid_obs"],
            result.audit["forecast_post_warmup_invalid_obs"],
            result.turnover,
            result.audit["forecast_excl"],
        )

    audit_frame = pl.DataFrame(audit_rows, infer_schema_length=None).sort("symbol")
    eligible_audit = audit_frame.filter(pl.col("forecast_elig"))
    eligible_symbols = eligible_audit.get_column("symbol").to_list()
    if not eligible_symbols:
        raise ValueError("no instrument passed the combined-forecast audit")

    # Correlations belong to executable subsystem returns, not raw market
    # returns or the individual EWMAC component forecasts.
    correlation, covered, returns_wide = _subsystem_correlation(
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
            .otherwise(pl.col("forecast_elig"))
            .alias("forecast_elig"),
            pl.when(pl.col("symbol").is_in(uncovered))
            .then(pl.lit("insufficient_synchronised_return_obs"))
            .otherwise(pl.col("forecast_excl"))
            .alias("forecast_excl"),
        )
        keep = [idx for idx, value in enumerate(covered) if value]
        eligible_symbols = [eligible_symbols[idx] for idx in keep]
        correlation = correlation[np.ix_(keep, keep)]
    if not eligible_symbols:
        raise ValueError("no instrument has sufficient synchronized subsystem returns")

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
            econ_family=str(row.get("econ_fam_id") or ""),
            mkt_risk_vol_day=(
                float(row["mkt_risk_vol_usd_day"])
                if row.get("mkt_risk_vol_usd_day") is not None
                else None
            ),
        ))
    selector = GreedyInstrumentSelector(config=GreedySelectionConfig(
        capital=float(run_config["init_cap_usd"]),
        target_vol=float(run_config["target_vol"]),
        gross_sr=args.gross_sr,
        score_threshold=args.score_threshold,
        starting_weight=args.starting_weight,
        max_pct_market_volume=float(run_config["max_pct_mkt_volume"]),
        liquidity_days=int(run_config["liq_days"]),
    ))
    selection = selector.select(candidate_list, correlation)
    for trial in selection.trials:
        logger.debug(
            "phase2_trial iteration=%s candidate=%s book=%s score=%s idm=%s "
            "feasible=%s accepted=%s reason=%s",
            trial["iteration"], trial["candidate"], trial["book"], trial["score"],
            trial["idm"], trial["feasible"], trial["accepted"], trial["reason"],
        )
    logger.info(
        "phase2_selection complete selected=%d candidates=%d score=%s idm=%s book=%s",
        len(selection.selected), len(candidate_list), selection.final_score.score,
        selection.final_score.idm, ",".join(selection.selected),
    )

    final_details = {
        detail["symbol"]: detail for detail in selection.final_score.details
    }
    final_rows = []
    for rank, symbol in enumerate(selection.selected, start=1):
        candidate = next(item for item in candidate_list if item.symbol == symbol)
        final_rows.append({
            "rank": rank,
            "symbol": symbol,
            "weight": selection.final_score.weights[symbol],
            "idm": selection.final_score.idm,
            "ann_dvol": candidate.ann_dvol,
            "trade_sr": candidate.trade_sr,
            "comb_turnover": candidate.combined_turnover,
            **final_details[symbol],
        })
    final_frame = pl.DataFrame(final_rows, infer_schema_length=None)
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    output_dir = source.parent
    audit_path = output_dir / f"pysystemtrade_phase2_forecast_audit_{stamp}.csv"
    forecasts_path = output_dir / f"pysystemtrade_phase2_forecasts_{stamp}.parquet"
    trials_path = output_dir / f"pysystemtrade_phase2_trials_{stamp}.csv"
    selection_path = output_dir / f"pysystemtrade_phase2_selection_{stamp}.csv"
    returns_path = output_dir / f"pysystemtrade_phase2_subsystem_returns_{stamp}.parquet"
    # Round only persisted public CSV copies. Full-precision daily analytical
    # frames remain available in Parquet and calculations above are untouched.
    public_audit = round_public_report(audit_frame)
    public_trials = round_public_report(
        pl.DataFrame(selection.trials, infer_schema_length=None)
    )
    public_selection = round_public_report(final_frame)
    public_audit.write_csv(audit_path)
    pl.concat(forecast_output_frames, how="diagonal_relaxed").write_parquet(
        forecasts_path
    )
    public_trials.write_csv(trials_path)
    public_selection.write_csv(selection_path)
    returns_wide.write_parquet(returns_path)
    print(
        f"Phase 2 selected {len(selection.selected)}/{len(candidate_list)} "
        f"forecast-eligible instruments"
    )
    print(f"Saved forecast audit {audit_path}")
    print(f"Saved daily forecasts {forecasts_path}")
    print(f"Saved trials {trials_path}")
    print(f"Saved selection {selection_path}")
    return selection_path, public_selection


def run(argv=None) -> tuple[Path, pl.DataFrame]:
    """Run persisted-gate Step 1 and optionally the complete Phase 2 search.

    Returns the last output path and dataframe produced: the compact Step 1
    universe normally, or the final greedy selection when ``--complete`` is
    supplied.
    """
    args = parse_args(argv)
    setup_logger()

    if args.input is None:
        source, report = load_latest_phase1_cost_report(args.report_dir)
    else:
        source = args.input.expanduser().resolve()
        report = pl.read_csv(source, infer_schema_length=None)

    logger.info(
        "phase2_step1 start source=%s instruments=%d",
        source,
        report.height,
    )
    config = phase2_step1_config(report)
    logger.info(
        "phase2_step1 config init_cap_usd=%s target_vol=%s cost_lim_sr=%s "
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
                "phase2_step1 accepted symbol=%s ann_dvol=%s "
                "avg_daily_volume_cons=%s pct_mkt_volume=%s",
                symbol,
                row.get("ann_dvol"),
                row.get("avg_daily_volume"),
                row.get("pct_mkt_volume"),
            )
        else:
            logger.debug(
                "phase2_step1 rejected symbol=%s ann_dvol=%s "
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
    # Step 1 is a public report boundary. Keep ``selected`` at calculation
    # precision for the optional complete search and round only its CSV copy.
    public_selected = round_public_report(selected)
    public_selected.write_csv(output)
    logger.info(
        "phase2_step1 complete source=%s output=%s selected=%d total=%d",
        source,
        output,
        selected.height,
        report.height,
    )
    print(
        f"Phase 2 Step 1 selected {selected.height}/{report.height} instruments"
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
    """CLI entry point for ``futures-select-phase2``."""
    run(argv)


if __name__ == "__main__":
    main()

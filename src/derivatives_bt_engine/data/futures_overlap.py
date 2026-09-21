"""Generate reproducible Carver-versus-Globex overlap diagnostics.

The report compares normalized daily changes, positive-index trend forecasts,
selected contract months, and roll timing.  It never compares Panama-adjusted
levels directly with raw contract prices and never mutates either source DB.
"""

from __future__ import annotations

import argparse
import bisect
import math
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Iterable, Optional

import polars as pl

from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_FUTURES_CACHE_ROOT,
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    FuturesHistory,
    GlobexHistoryProvider,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.signal import (
    build_features,
    continuous_momentum,
)
from derivatives_bt_engine.utils.logger import setup_logger


logger = setup_logger()

DEFAULT_OVERLAP_START = date(2010, 6, 7)
DEFAULT_OVERLAP_END = date(2024, 3, 29)
VOLATILITY_WINDOW_DAYS = 20


@dataclass(frozen=True)
class MarketMapping:
    canonical_market_id: str
    carver_instrument: str
    globex_asset: str
    notes: str = ""
    known_issue: str = ""
    mapping_status: str = "candidate"
    usage_status: str = "signal_research"
    carver_date_alignment: str = ""
    carver_date_alignment_through: Optional[date] = None


def load_mappings(path: Optional[Path | str] = None) -> tuple[MarketMapping, ...]:
    """Load the canonical crosswalk from data rather than Python aliases."""
    if path is None:
        resource = files("derivatives_bt_engine.data").joinpath(
            "pysystemtrade_mappings.csv"
        )
        with resource.open("rb") as handle:
            frame = pl.read_csv(handle)
    else:
        frame = pl.read_csv(path)
    required = {
        "canonical_market_id",
        "carver_instrument",
        "globex_asset",
        "mapping_status",
        "usage_status",
        "carver_date_alignment",
        "carver_date_alignment_through",
        "notes",
        "known_issue",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Mapping crosswalk is missing columns: {sorted(missing)}")
    duplicates = frame.group_by("canonical_market_id").len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError("Mapping crosswalk has duplicate canonical market IDs")
    invalid_status = frame.filter(
        ~pl.col("mapping_status").is_in(["candidate", "approved", "rejected"])
    )
    if invalid_status.height:
        raise ValueError("Mapping crosswalk contains an invalid mapping_status")
    return tuple(
        MarketMapping(
            canonical_market_id=row["canonical_market_id"],
            carver_instrument=row["carver_instrument"],
            globex_asset=row["globex_asset"],
            notes=row["notes"] or "",
            known_issue=row["known_issue"] or "",
            mapping_status=row["mapping_status"],
            usage_status=row["usage_status"],
            carver_date_alignment=row["carver_date_alignment"] or "",
            carver_date_alignment_through=(
                date.fromisoformat(str(row["carver_date_alignment_through"]))
                if row["carver_date_alignment_through"] is not None
                else None
            ),
        )
        for row in frame.iter_rows(named=True)
    )


DEFAULT_MAPPINGS = load_mappings()


@dataclass(frozen=True)
class OverlapReport:
    summary: pl.DataFrame
    rolls: pl.DataFrame
    missing_dates: pl.DataFrame
    details: dict[str, pl.DataFrame]


def _bounded(frame: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return frame.filter(pl.col("trade_date").is_between(start, end)).sort(
        "trade_date"
    )


def _trend_frame(history: FuturesHistory, prefix: str) -> pl.DataFrame:
    trend = continuous_momentum(build_features(history.signal_bars()))
    return trend.select(
        pl.col("ts_event").alias("trade_date"),
        pl.col("ts_fast").alias(f"{prefix}_ts_fast"),
        pl.col("ts_slow").alias(f"{prefix}_ts_slow"),
        pl.col("signal").alias(f"{prefix}_trend_signal"),
    )


def _replace_trade_dates(
    frame: pl.DataFrame,
    date_map: dict[date, date],
) -> tuple[pl.DataFrame, int]:
    if frame.is_empty():
        return frame, 0
    before = frame.height
    mapped = (
        frame.with_columns(
            pl.col("trade_date")
            .replace_strict(date_map, default=pl.col("trade_date"))
            .alias("trade_date")
        )
        .sort("trade_date", "source_timestamp")
        .unique(subset="trade_date", keep="last", maintain_order=True)
    )
    return mapped, before - mapped.height


def _align_carver_dates(
    mapping: MarketMapping,
    carver: FuturesHistory,
    globex: FuturesHistory,
) -> tuple[FuturesHistory, int]:
    """Apply an explicitly configured cross-provider session alignment."""
    if not mapping.carver_date_alignment:
        return carver, 0
    if mapping.carver_date_alignment != "previous_globex_session":
        raise ValueError(
            f"Unsupported Carver date alignment: {mapping.carver_date_alignment}"
        )
    if mapping.carver_date_alignment_through is None:
        raise ValueError("Carver date alignment requires an inclusive end date")

    globex_dates = sorted(set(globex.signal.get_column("trade_date").to_list()))
    date_map: dict[date, date] = {}
    for value in carver.signal.get_column("trade_date").to_list():
        if value > mapping.carver_date_alignment_through:
            continue
        position = bisect.bisect_left(globex_dates, value) - 1
        if position >= 0:
            date_map[value] = globex_dates[position]

    signal, collapsed = _replace_trade_dates(carver.signal, date_map)
    marks, _ = _replace_trade_dates(carver.marks, date_map)
    carry, _ = _replace_trade_dates(carver.carry, date_map)
    panama, _ = _replace_trade_dates(carver.panama, date_map)
    marks = marks.with_columns(
        (pl.col("contract_id") != pl.col("contract_id").shift(1))
        .fill_null(False)
        .alias("is_roll")
    )
    if {"adjusted_price", "current_price"}.issubset(signal.columns):
        signal = signal.sort("trade_date").with_columns(
            pl.col("adjusted_price").diff().alias("adjusted_point_change")
        )
        signal = signal.with_columns(
            pl.when(
                pl.col("adjusted_point_change").is_not_null()
                & (pl.col("current_price") > 0)
            )
            .then(pl.col("adjusted_point_change") / pl.col("current_price"))
            .otherwise(None)
            .alias("ret_1d")
        )
    signal = signal.sort("trade_date").with_columns(
        (
            (1.0 + pl.col("ret_1d").fill_null(0.0)).cum_prod()
            * 100.0
        ).alias("signal_index"),
        (pl.col("contract_id") != pl.col("contract_id").shift(1))
        .fill_null(False)
        .alias("is_roll"),
    )
    metadata = dict(carver.metadata)
    metadata.update(
        {
            "date_alignment": mapping.carver_date_alignment,
            "date_alignment_through": mapping.carver_date_alignment_through,
            "date_alignment_collapsed_rows": collapsed,
        }
    )
    return FuturesHistory(
        source=carver.source,
        instrument_code=carver.instrument_code,
        schema_version=carver.schema_version,
        signal=signal,
        marks=marks,
        carry=carry,
        metadata=metadata,
        panama=panama,
    ), collapsed


def _comparison_frame(
    carver: FuturesHistory,
    globex: FuturesHistory,
    start: date,
    end: date,
) -> pl.DataFrame:
    carver_signal = _bounded(carver.signal, start, end).select(
        "trade_date",
        pl.col("ret_1d").alias("carver_return"),
        pl.col("signal_index").alias("carver_signal_index"),
        pl.col("contract_id").alias("carver_contract"),
        pl.col("is_roll").alias("carver_is_roll"),
    )
    globex_signal = _bounded(globex.signal, start, end).select(
        "trade_date",
        pl.col("ret_1d").alias("globex_return"),
        pl.col("signal_index").alias("globex_signal_index"),
        pl.col("contract_id").alias("globex_contract"),
        pl.col("is_roll").alias("globex_is_roll"),
    )
    return (
        carver_signal.join(globex_signal, on="trade_date", how="inner")
        .join(_trend_frame(carver, "carver"), on="trade_date", how="left")
        .join(_trend_frame(globex, "globex"), on="trade_date", how="left")
        .with_columns(
            pl.col("carver_contract").str.slice(0, 6).alias("carver_contract_month"),
            pl.col("globex_contract").str.slice(0, 6).alias("globex_contract_month"),
        )
        .sort("trade_date")
    )


def _correlation(frame: pl.DataFrame, left: str, right: str) -> Optional[float]:
    clean = frame.drop_nulls([left, right])
    if clean.height < 2:
        return None
    value = clean.select(pl.corr(left, right)).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _shifted_return_correlation(
    carver: FuturesHistory,
    globex: FuturesHistory,
    start: date,
    end: date,
    globex_shift_days: int,
) -> tuple[Optional[float], int]:
    left = _bounded(carver.signal, start, end).select(
        "trade_date",
        pl.col("ret_1d").alias("carver_return"),
        pl.col("is_roll").alias("carver_is_roll"),
    )
    right = _bounded(globex.signal, start, end).select(
        (pl.col("trade_date") + pl.duration(days=globex_shift_days)).alias(
            "trade_date"
        ),
        pl.col("ret_1d").alias("globex_return"),
        pl.col("is_roll").alias("globex_is_roll"),
    )
    joined = left.join(right, on="trade_date", how="inner").filter(
        ~pl.col("carver_is_roll") & ~pl.col("globex_is_roll")
    ).drop_nulls(["carver_return", "globex_return"])
    return _correlation(joined, "carver_return", "globex_return"), joined.height


def _volatility_frame(history: FuturesHistory, prefix: str) -> pl.DataFrame:
    return history.signal.sort("trade_date").select(
        "trade_date",
        (
            pl.col("ret_1d")
            .rolling_std(
                window_size=VOLATILITY_WINDOW_DAYS,
                min_samples=VOLATILITY_WINDOW_DAYS,
            )
            * math.sqrt(252)
        ).alias(f"{prefix}_volatility"),
    )


def _shifted_volatility_correlation(
    carver: FuturesHistory,
    globex: FuturesHistory,
    start: date,
    end: date,
    globex_shift_days: int,
) -> tuple[Optional[float], int]:
    left = _bounded(_volatility_frame(carver, "carver"), start, end)
    right = _bounded(_volatility_frame(globex, "globex"), start, end).with_columns(
        (pl.col("trade_date") + pl.duration(days=globex_shift_days)).alias(
            "trade_date"
        )
    )
    joined = left.join(right, on="trade_date", how="inner").drop_nulls(
        ["carver_volatility", "globex_volatility"]
    )
    return _correlation(joined, "carver_volatility", "globex_volatility"), joined.height


def _roll_events(
    history: FuturesHistory,
    prefix: str,
    start: date,
    end: date,
) -> pl.DataFrame:
    return (
        history.marks.sort("trade_date")
        .with_columns(
            pl.col("contract_id").shift(1).alias(f"{prefix}_from_contract")
        )
        .filter(
            pl.col("trade_date").is_between(start, end) & pl.col("is_roll")
        )
        .select(
            pl.col("trade_date").alias(f"{prefix}_roll_date"),
            f"{prefix}_from_contract",
            pl.col("contract_id").alias(f"{prefix}_to_contract"),
        )
    )


def _match_rolls(
    mapping: MarketMapping,
    carver: FuturesHistory,
    globex: FuturesHistory,
    start: date,
    end: date,
) -> pl.DataFrame:
    carver_rolls = _roll_events(carver, "carver", start, end)
    globex_rolls = _roll_events(globex, "globex", start, end)
    schema = {
        "canonical_market_id": pl.String,
        "carver_instrument": pl.String,
        "globex_asset": pl.String,
        "carver_roll_date": pl.Date,
        "carver_from_contract": pl.String,
        "carver_to_contract": pl.String,
        "nearest_globex_roll_date": pl.Date,
        "globex_from_contract": pl.String,
        "globex_to_contract": pl.String,
        "roll_date_distance_days": pl.Int64,
    }
    if carver_rolls.is_empty() or globex_rolls.is_empty():
        return pl.DataFrame(schema=schema)

    candidates = carver_rolls.join(globex_rolls, how="cross").with_columns(
        (
            pl.col("carver_roll_date") - pl.col("globex_roll_date")
        ).dt.total_days().abs().alias("roll_date_distance_days")
    )
    matched = candidates.group_by(
        "carver_roll_date",
        "carver_from_contract",
        "carver_to_contract",
        maintain_order=True,
    ).agg(
        pl.col("globex_roll_date")
        .sort_by("roll_date_distance_days")
        .first()
        .alias("nearest_globex_roll_date"),
        pl.col("globex_from_contract")
        .sort_by("roll_date_distance_days")
        .first(),
        pl.col("globex_to_contract")
        .sort_by("roll_date_distance_days")
        .first(),
        pl.col("roll_date_distance_days").min(),
    )
    return matched.with_columns(
        pl.lit(mapping.canonical_market_id).alias("canonical_market_id"),
        pl.lit(mapping.carver_instrument).alias("carver_instrument"),
        pl.lit(mapping.globex_asset).alias("globex_asset"),
    ).select(*schema)


def _missing_dates(
    mapping: MarketMapping,
    carver: FuturesHistory,
    globex: FuturesHistory,
    start: date,
    end: date,
) -> pl.DataFrame:
    carver_dates = _bounded(carver.signal, start, end).select("trade_date")
    globex_dates = _bounded(globex.signal, start, end).select("trade_date")
    missing_from_globex = carver_dates.join(
        globex_dates, on="trade_date", how="anti"
    ).with_columns(
        pl.lit("pysystemtrade").alias("present_in"),
        pl.lit("globex").alias("missing_from"),
    )
    missing_from_carver = globex_dates.join(
        carver_dates, on="trade_date", how="anti"
    ).with_columns(
        pl.lit("globex").alias("present_in"),
        pl.lit("pysystemtrade").alias("missing_from"),
    )
    return pl.concat([missing_from_globex, missing_from_carver]).with_columns(
        pl.lit(mapping.canonical_market_id).alias("canonical_market_id"),
        pl.lit(mapping.carver_instrument).alias("carver_instrument"),
        pl.lit(mapping.globex_asset).alias("globex_asset"),
    ).select(
        "canonical_market_id",
        "carver_instrument",
        "globex_asset",
        "trade_date",
        "present_in",
        "missing_from",
    ).sort("trade_date", "missing_from")


def _mean_boolean(frame: pl.DataFrame, expression: pl.Expr) -> Optional[float]:
    if frame.is_empty():
        return None
    value = frame.select(expression.mean()).item()
    return None if value is None else float(value)


def _recommendation(metrics: dict, mapping: MarketMapping) -> str:
    if mapping.known_issue:
        return "investigate_known_issue"
    if metrics["common_dates"] < 1_000:
        return "insufficient_overlap"
    return_corr = metrics["best_nonroll_return_correlation"]
    signal_corr = metrics["trend_signal_correlation"]
    direction = metrics["trend_direction_agreement"]
    if (
        return_corr is not None
        and return_corr >= 0.90
        and signal_corr is not None
        and signal_corr >= 0.80
        and direction is not None
        and direction >= 0.80
    ):
        return "proceed_to_manual_review"
    return "investigate"


def compare_market(
    mapping: MarketMapping,
    carver: FuturesHistory,
    globex: FuturesHistory,
    *,
    start: date = DEFAULT_OVERLAP_START,
    end: date = DEFAULT_OVERLAP_END,
) -> tuple[dict, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Compare one mapped market without making an approval decision."""
    unaligned_shift_results = (
        {
            shift: _shifted_return_correlation(carver, globex, start, end, shift)
            for shift in (-1, 0, 1)
        }
        if mapping.carver_date_alignment
        else {}
    )
    carver, collapsed_alignment_rows = _align_carver_dates(mapping, carver, globex)
    detail = _comparison_frame(carver, globex, start, end)
    carver_dates = _bounded(carver.signal, start, end).select("trade_date")
    globex_dates = _bounded(globex.signal, start, end).select("trade_date")
    nonroll = detail.filter(
        ~pl.col("carver_is_roll") & ~pl.col("globex_is_roll")
    )

    shift_results = {
        shift: _shifted_return_correlation(carver, globex, start, end, shift)
        for shift in (-1, 0, 1)
    }
    volatility_shift_results = {
        shift: _shifted_volatility_correlation(carver, globex, start, end, shift)
        for shift in (-1, 0, 1)
    }
    for shift, (correlation, observations) in shift_results.items():
        logger.debug(
            "futures_overlap candidate canonical_market_id=%s "
            "globex_shift_days=%d observations=%d return_correlation=%s",
            mapping.canonical_market_id,
            shift,
            observations,
            correlation,
        )
    finite_shifts = {
        shift: result for shift, result in shift_results.items() if result[0] is not None
    }
    if finite_shifts:
        best_shift, (best_corr, best_shift_rows) = max(
            finite_shifts.items(), key=lambda item: item[1][0]
        )
    else:
        best_shift, best_corr, best_shift_rows = None, None, 0
    finite_volatility_shifts = {
        shift: result
        for shift, result in volatility_shift_results.items()
        if result[0] is not None
    }
    if finite_volatility_shifts:
        best_volatility_shift, (best_volatility_corr, best_volatility_rows) = max(
            finite_volatility_shifts.items(), key=lambda item: item[1][0]
        )
    else:
        best_volatility_shift, best_volatility_corr, best_volatility_rows = None, None, 0

    trend_clean = detail.drop_nulls(
        ["carver_trend_signal", "globex_trend_signal"]
    )
    rolls = _match_rolls(mapping, carver, globex, start, end)
    missing_dates = _missing_dates(mapping, carver, globex, start, end)
    metrics = {
        "canonical_market_id": mapping.canonical_market_id,
        "carver_instrument": mapping.carver_instrument,
        "globex_asset": mapping.globex_asset,
        "carver_hold_roll_cycle": carver.metadata.get("hold_roll_cycle"),
        "globex_active_months": "".join(
            globex.metadata.get("active_months") or []
        ),
        "history_schema_version": carver.schema_version,
        "carver_source_git_commit": carver.metadata.get("source_git_commit"),
        "globex_dataset_fingerprint": globex.metadata.get("dataset_fingerprint"),
        "review_status": mapping.mapping_status,
        "usage_status": mapping.usage_status,
        "carver_date_alignment": mapping.carver_date_alignment or "none",
        "carver_date_alignment_through": mapping.carver_date_alignment_through,
        "date_alignment_collapsed_rows": collapsed_alignment_rows,
        "unaligned_shift_0_return_correlation": (
            unaligned_shift_results[0][0] if unaligned_shift_results else None
        ),
        "unaligned_best_return_correlation": (
            max(
                result[0]
                for result in unaligned_shift_results.values()
                if result[0] is not None
            )
            if unaligned_shift_results
            else None
        ),
        "overlap_start": detail.get_column("trade_date").min(),
        "overlap_end": detail.get_column("trade_date").max(),
        "carver_dates": carver_dates.height,
        "globex_dates": globex_dates.height,
        "common_dates": detail.height,
        "carver_only_dates": carver_dates.join(
            globex_dates, on="trade_date", how="anti"
        ).height,
        "globex_only_dates": globex_dates.join(
            carver_dates, on="trade_date", how="anti"
        ).height,
        "same_day_nonroll_return_correlation": _correlation(
            nonroll, "carver_return", "globex_return"
        ),
        "shift_minus_1_return_correlation": shift_results[-1][0],
        "shift_0_return_correlation": shift_results[0][0],
        "shift_plus_1_return_correlation": shift_results[1][0],
        "best_globex_shift_days": best_shift,
        "best_nonroll_return_correlation": best_corr,
        "best_shift_common_returns": best_shift_rows,
        "shift_minus_1_volatility_correlation": volatility_shift_results[-1][0],
        "shift_0_volatility_correlation": volatility_shift_results[0][0],
        "shift_plus_1_volatility_correlation": volatility_shift_results[1][0],
        "best_volatility_shift_days": best_volatility_shift,
        "best_volatility_correlation": best_volatility_corr,
        "best_shift_common_volatilities": best_volatility_rows,
        "carver_annualized_volatility": (
            nonroll.get_column("carver_return").drop_nulls().std() * math.sqrt(252)
        ),
        "globex_annualized_volatility": (
            nonroll.get_column("globex_return").drop_nulls().std() * math.sqrt(252)
        ),
        "trend_signal_correlation": _correlation(
            trend_clean, "carver_trend_signal", "globex_trend_signal"
        ),
        "trend_direction_agreement": _mean_boolean(
            trend_clean,
            (pl.col("carver_trend_signal") >= 0)
            == (pl.col("globex_trend_signal") >= 0),
        ),
        "contract_month_agreement": _mean_boolean(
            detail.drop_nulls(["carver_contract_month", "globex_contract_month"]),
            pl.col("carver_contract_month") == pl.col("globex_contract_month"),
        ),
        "carver_rolls": int(
            _bounded(carver.marks, start, end).get_column("is_roll").sum()
        ),
        "globex_rolls": int(
            _bounded(globex.marks, start, end).get_column("is_roll").sum()
        ),
        "matched_carver_rolls": rolls.height,
        "rolls_within_5_days": (
            int(
                rolls.filter(pl.col("roll_date_distance_days") <= 5).height
            )
            if rolls.height
            else 0
        ),
        "median_roll_distance_days": (
            float(rolls.get_column("roll_date_distance_days").median())
            if rolls.height
            else None
        ),
        "known_issue": mapping.known_issue,
        "notes": mapping.notes,
    }
    metrics["automated_recommendation"] = _recommendation(metrics, mapping)
    logger.debug(
        "futures_overlap evaluation canonical_market_id=%s common_dates=%d "
        "return_correlation=%s trend_correlation=%s direction_agreement=%s "
        "known_issue=%s recommendation=%s",
        mapping.canonical_market_id,
        metrics["common_dates"],
        metrics["best_nonroll_return_correlation"],
        metrics["trend_signal_correlation"],
        metrics["trend_direction_agreement"],
        bool(mapping.known_issue),
        metrics["automated_recommendation"],
    )
    detail = detail.with_columns(
        pl.lit(mapping.canonical_market_id).alias("canonical_market_id")
    ).select("canonical_market_id", pl.all().exclude("canonical_market_id"))
    return metrics, rolls, missing_dates, detail


def build_overlap_report(
    mappings: Iterable[MarketMapping],
    carver_provider: PysystemtradeHistoryProvider,
    globex_provider: GlobexHistoryProvider,
    *,
    start: date = DEFAULT_OVERLAP_START,
    end: date = DEFAULT_OVERLAP_END,
) -> OverlapReport:
    summaries: list[dict] = []
    roll_frames: list[pl.DataFrame] = []
    missing_date_frames: list[pl.DataFrame] = []
    details: dict[str, pl.DataFrame] = {}
    for mapping in mappings:
        logger.info(
            "futures_overlap start canonical_market_id=%s carver=%s globex=%s",
            mapping.canonical_market_id,
            mapping.carver_instrument,
            mapping.globex_asset,
        )
        carver = carver_provider.load(mapping.carver_instrument)
        globex = globex_provider.load(mapping.globex_asset)
        metrics, rolls, missing_dates, detail = compare_market(
            mapping, carver, globex, start=start, end=end
        )
        summaries.append(metrics)
        roll_frames.append(rolls)
        missing_date_frames.append(missing_dates)
        details[mapping.canonical_market_id] = detail
        logger.info(
            "futures_overlap complete canonical_market_id=%s common_dates=%d "
            "return_corr=%s trend_corr=%s recommendation=%s",
            mapping.canonical_market_id,
            metrics["common_dates"],
            metrics["best_nonroll_return_correlation"],
            metrics["trend_signal_correlation"],
            metrics["automated_recommendation"],
        )
    summary = pl.DataFrame(summaries, infer_schema_length=None).sort(
        "canonical_market_id"
    )
    rolls = pl.concat(roll_frames, how="vertical_relaxed") if roll_frames else pl.DataFrame()
    missing_dates = (
        pl.concat(missing_date_frames, how="vertical_relaxed")
        if missing_date_frames
        else pl.DataFrame()
    )
    return OverlapReport(
        summary=summary,
        rolls=rolls,
        missing_dates=missing_dates,
        details=details,
    )


def _format_metric(value: object, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "n/a"
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(
    report: OverlapReport,
    *,
    start: date,
    end: date,
) -> str:
    lines = [
        "# pysystemtrade versus Globex overlap report",
        "",
        f"Comparison window: {start.isoformat()} through {end.isoformat()}.",
        f"History schema: v{_format_metric(report.summary['history_schema_version'][0], 0)}; "
        f"Carver source commit: "
        f"`{_format_metric(report.summary['carver_source_git_commit'][0])}`; "
        f"Globex fingerprint: "
        f"`{_format_metric(report.summary['globex_dataset_fingerprint'][0])}`.",
        "",
        "Both providers use contract-consistent arithmetic returns: the current "
        "selected-contract price divided by its prior same-contract reference, "
        "including the matched forward quote on a Carver roll. Invalid nonpositive "
        "references are flagged rather than coerced. Valid returns are compounded "
        "into positive signal indices before trend forecasts are computed.",
        "",
        "Carver source timestamps are preserved, while Sunday observations are assigned "
        "to the following Monday trading session during sidecar import.",
        "Daily volatility is the annualized trailing 20-session standard deviation of "
        "normalized returns. The tables report every tested -1/0/+1 calendar-day "
        "correlation for both returns and volatility.",
        "",
        "Every mapping remains `candidate`. Automated recommendations are triage only, "
        "not mapping approvals.",
        "",
        "## Summary",
        "",
        "| Market | Carver | Globex | Common | Return corr. -1 / 0 / +1 | "
        "Vol corr. -1 / 0 / +1 | Trend | Direction | Date alignment | Recommendation |",
        "|---|---|---:|---:|---|---|---:|---:|---|---|",
    ]
    for row in report.summary.iter_rows(named=True):
        lines.append(
            "| {canonical_market_id} | {carver_instrument} | {globex_asset} | "
            "{common_dates} | {return_corrs} | {vol_corrs} | {trend_corr} | "
            "{direction} | {alignment} | {recommendation} |".format(
                **row,
                return_corrs=" / ".join(
                    _format_metric(row[name])
                    for name in (
                        "shift_minus_1_return_correlation",
                        "shift_0_return_correlation",
                        "shift_plus_1_return_correlation",
                    )
                ),
                vol_corrs=" / ".join(
                    _format_metric(row[name])
                    for name in (
                        "shift_minus_1_volatility_correlation",
                        "shift_0_volatility_correlation",
                        "shift_plus_1_volatility_correlation",
                    )
                ),
                trend_corr=_format_metric(row["trend_signal_correlation"]),
                direction=_format_metric(row["trend_direction_agreement"]),
                alignment=row["carver_date_alignment"],
                recommendation=row["automated_recommendation"],
            )
        )
    lines.extend(["", "## Per-market diagnostics", ""])
    for row in report.summary.iter_rows(named=True):
        lines.extend(
            [
                f"### {row['canonical_market_id']}: "
                f"{row['carver_instrument']} / {row['globex_asset']}",
                "",
                f"- Overlap: {_format_metric(row['overlap_start'])} to "
                f"{_format_metric(row['overlap_end'])}; "
                f"{row['common_dates']:,} common dates, "
                f"{row['carver_only_dates']:,} Carver-only and "
                f"{row['globex_only_dates']:,} Globex-only dates.",
                f"- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: "
                f"{_format_metric(row['shift_minus_1_return_correlation'])} / "
                f"{_format_metric(row['shift_0_return_correlation'])} / "
                f"{_format_metric(row['shift_plus_1_return_correlation'])}; best "
                f"{_format_metric(row['best_nonroll_return_correlation'])} at "
                f"{_format_metric(row['best_globex_shift_days'], 0)} day(s).",
                f"- Trailing {VOLATILITY_WINDOW_DAYS}-session annualized-volatility "
                f"correlation at -1/0/+1 shifts: "
                f"{_format_metric(row['shift_minus_1_volatility_correlation'])} / "
                f"{_format_metric(row['shift_0_volatility_correlation'])} / "
                f"{_format_metric(row['shift_plus_1_volatility_correlation'])}; best "
                f"{_format_metric(row['best_volatility_correlation'])} at "
                f"{_format_metric(row['best_volatility_shift_days'], 0)} day(s).",
                f"- Annualized normalized-return volatility: Carver "
                f"{_format_metric(row['carver_annualized_volatility'])}; Globex "
                f"{_format_metric(row['globex_annualized_volatility'])}.",
                f"- Trend forecast correlation "
                f"{_format_metric(row['trend_signal_correlation'])}; direction agreement "
                f"{_format_metric(row['trend_direction_agreement'])}.",
                f"- Contract-month agreement "
                f"{_format_metric(row['contract_month_agreement'])}; Carver/Globex rolls "
                f"{row['carver_rolls']}/{row['globex_rolls']}; "
                f"{row['rolls_within_5_days']} of {row['matched_carver_rolls']} Carver "
                f"rolls have a nearest Globex roll within five calendar days.",
                f"- Configured contract cycles: Carver hold cycle "
                f"`{row['carver_hold_roll_cycle'] or 'n/a'}`; repository Globex active "
                f"months `{row['globex_active_months'] or 'unrestricted/unconfirmed'}`.",
                f"- Date alignment: `{row['carver_date_alignment']}`"
                + (
                    f" through {row['carver_date_alignment_through']} inclusive; "
                    f"{row['date_alignment_collapsed_rows']} duplicate mapped rows collapsed."
                    if row["carver_date_alignment"] != "none"
                    else "."
                ),
                f"- Status: {row['review_status']}; automated recommendation: "
                f"`{row['automated_recommendation']}`.",
            ]
        )
        if row["known_issue"]:
            lines.append(f"- Known issue: {row['known_issue']}.")
        if row["carver_date_alignment"] != "none":
            lines.append(
                "- Alignment audit: before the configured correction, same-date return "
                f"correlation was {_format_metric(row['unaligned_shift_0_return_correlation'])} "
                "and the best naive calendar shift produced "
                f"{_format_metric(row['unaligned_best_return_correlation'])}; after "
                f"actual-session alignment, the same-date result is "
                f"{_format_metric(row['shift_0_return_correlation'])}."
            )
        if row["notes"]:
            lines.append(f"- Mapping note: {row['notes']}.")
        lines.append("")
    lines.extend(
        [
            "## Interpretation safeguards",
            "",
            "- Silver alone has an explicit date adjustment: legacy observations through "
            "2021-06-30 map to the preceding actual SI session. The other 13 mappings use "
            "their normalized dates without a cross-provider shift.",
            "- Contract-month disagreement can reflect intentionally different roll rules, "
            "not a bad price series.",
            "- `proceed_to_manual_review` requires at least 1,000 common dates, return "
            "correlation >= 0.90, trend correlation >= 0.80, direction agreement >= "
            "0.80, and no registered known issue.",
            "- The report does not validate multipliers, currency conversion, or executable "
            "slippage and therefore does not make any series live-trading eligible.",
            "- Detailed common-date rows can be regenerated with `--write-details`; source "
            "databases remain read-only.",
            "- `missing_dates.csv` identifies each non-common date and which source lacks it.",
            "",
        ]
    )
    return "\n".join(lines)


def write_overlap_report(
    report: OverlapReport,
    output_dir: Path | str,
    *,
    start: date,
    end: date,
    write_details: bool = False,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report.summary.write_csv(output_dir / "summary.csv")
    report.rolls.write_csv(output_dir / "rolls.csv")
    report.missing_dates.write_csv(output_dir / "missing_dates.csv")
    (output_dir / "report.md").write_text(
        render_markdown(report, start=start, end=end), encoding="utf-8"
    )
    if write_details:
        detail_dir = output_dir / "details"
        detail_dir.mkdir(parents=True, exist_ok=True)
        for market, frame in report.details.items():
            frame.write_csv(detail_dir / f"{market}.csv")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carver-db", default=str(DEFAULT_PYSYSTEMTRADE_DB_PATH))
    parser.add_argument("--globex-db", default=str(DEFAULT_GLOBEX_DB_PATH))
    parser.add_argument("--cache-root", default=str(DEFAULT_FUTURES_CACHE_ROOT))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_OVERLAP_START)
    parser.add_argument("--end", type=_parse_date, default=DEFAULT_OVERLAP_END)
    parser.add_argument(
        "--markets",
        help="Comma-separated canonical market IDs; defaults to all mappings",
    )
    parser.add_argument("--write-details", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    mappings = DEFAULT_MAPPINGS
    if args.markets:
        selected = {item.strip() for item in args.markets.split(",") if item.strip()}
        known = {mapping.canonical_market_id for mapping in DEFAULT_MAPPINGS}
        unknown = selected - known
        if unknown:
            raise ValueError(f"Unknown canonical market IDs: {sorted(unknown)}")
        mappings = tuple(
            mapping
            for mapping in DEFAULT_MAPPINGS
            if mapping.canonical_market_id in selected
        )
    report = build_overlap_report(
        mappings,
        PysystemtradeHistoryProvider(
            db_path=args.carver_db,
            cache_root=args.cache_root,
        ),
        GlobexHistoryProvider(
            db_path=args.globex_db,
            cache_root=args.cache_root,
        ),
        start=args.start,
        end=args.end,
    )
    write_overlap_report(
        report,
        args.output_dir,
        start=args.start,
        end=args.end,
        write_details=args.write_details,
    )
    print(
        f"Wrote {report.summary.height} market overlap reports to {args.output_dir}"
    )


if __name__ == "__main__":
    main()

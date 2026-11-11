"""Adapt pooled monthly Goulding forecasts to the selection backtest.

The selection pipeline sizes and costs daily source-neutral futures histories,
whereas the Goulding model forms one forecast per calendar month and estimates
its disagreement-state weights across related markets.  This module builds
those causal pooled monthly forecasts, assigns them to the prior month-end
decision session, and joins them to the same Panama, volatility, roll, and
contract-mark frame used by the other selection signal models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.calculations.goulding import (
    GOULDING_FAST_MONTHS,
    GOULDING_SIGNAL_MODES,
    GOULDING_SLOW_MONTHS,
    _goulding_direction,
    estimate_goulding_forecast_scalar,
    estimate_mixing_params,
    goulding_continuous_raw,
    goulding_forecast_legs,
    goulding_monthly,
    normalize_goulding_forecast,
)
from derivatives_bt_engine.calculations.return_tsmom_forecast import (
    _history_execution_frame,
)

if TYPE_CHECKING:
    from derivatives_bt_engine.data.futures_history import FuturesHistory


GOULDING_MODEL = "goulding"
# The selection pipeline consumes forecasts directly as multiples of its
# average risk-sized position. Use the same normalized scale as EWMAC here.
# The standalone TSMOM engine retains its own half-scale directional input
# because it applies an additional downstream risk scalar.
GOULDING_SELECTION_FORECAST_TARGET_ABS = 1.0
GOULDING_SELECTION_FORECAST_CAP = 2.0


@dataclass
class GouldingCalibrationContext:
    """Reusable pooled calibration state for stitched Goulding histories.

    The frozen reference universe determines monthly cluster mixing parameters
    and continuous forecast scaling. Stitched targets share that calibration,
    so retaining its monthly inputs and memoized parameters avoids rebuilding
    the full universe once for every target market.
    """

    monthly_by_history: dict[str, pl.DataFrame]
    cluster_by_history: dict[str, str]
    outcome_history: pl.DataFrame
    signal_mode: str
    fast_months: int
    slow_months: int
    target_abs_forecast: float
    parameters_by_month: dict[
        date,
        tuple[dict[str, tuple[float, float]], float | None],
    ] = field(default_factory=dict)


def _monthly_signal_frame(
    history: FuturesHistory,
    *,
    fast_months: int,
    slow_months: int,
) -> pl.DataFrame:
    """Return one Goulding input row per month with its decision session.

    ``goulding_monthly`` is a legacy calculation API and therefore receives
    ``ts_event`` only at this boundary.  Its month label denotes the month to
    be held.  The forecast becomes effective on the final observed session
    before that label, after the preceding month's close is known.
    """
    signal_bars = history.signal_bars().rename({"date": "ts_event"})
    monthly = (
        goulding_monthly(
            signal_bars,
            fast_months=fast_months,
            slow_months=slow_months,
        )
        .rename({
            "ts_event": "forecast_month",
            "fast": "g_fast",
            "slow": "g_slow",
            "fast_rms_vol": "g_fast_rms_vol",
            "slow_rms_vol": "g_slow_rms_vol",
            "fast_sharpe": "g_fast_sharpe",
            "slow_sharpe": "g_slow_sharpe",
            "regime": "g_regime",
        })
        .sort("forecast_month")
    )
    sessions = signal_bars.select(
        pl.col("ts_event").alias("session_date")
    ).sort("session_date")
    return (
        monthly.with_columns(
            (pl.col("forecast_month") - pl.duration(days=1)).alias(
                "decision_anchor"
            )
        )
        .sort("decision_anchor")
        .join_asof(
            sessions,
            left_on="decision_anchor",
            right_on="session_date",
            strategy="backward",
        )
        .select(
            "forecast_month",
            pl.col("session_date").alias("decision_date"),
            "ret",
            "g_fast",
            "g_slow",
            "g_fast_rms_vol",
            "g_slow_rms_vol",
            "g_fast_sharpe",
            "g_slow_sharpe",
            "g_regime",
        )
    )


def _monthly_outcome_history(
    monthly_by_history: dict[str, pl.DataFrame],
    cluster_by_history: dict[str, str],
) -> pl.DataFrame:
    """Build causal state/next-return observations for mixing estimation.

    A month's return is available at the next month label.  The shared
    estimator applies a strict ``date < as_of`` filter, reproducing the
    standalone backtest's conservative rule that excludes the return ending
    on the current decision date from that decision's calibration.
    """
    frames: list[pl.DataFrame] = []
    for history_code, monthly in monthly_by_history.items():
        frames.append(
            monthly.select(
                pl.col("forecast_month").shift(-1).alias("date"),
                pl.col("decision_date").alias("decided_at"),
                pl.lit(history_code).alias("symbol"),
                pl.lit(cluster_by_history[history_code]).alias("cluster"),
                pl.col("g_regime").alias("state"),
                pl.col("ret").alias("monthly_return"),
            ).drop_nulls(["date", "state", "monthly_return"])
        )
    if not frames:
        return pl.DataFrame(schema={
            "date": pl.Date,
            "decided_at": pl.Date,
            "symbol": pl.Utf8,
            "cluster": pl.Utf8,
            "state": pl.Utf8,
            "monthly_return": pl.Float64,
        })
    return pl.concat(frames, how="vertical")


def _raw_goulding_forecast(
    row: dict[str, object],
    a_co: float,
    a_re: float,
    signal_mode: str,
) -> float | None:
    """Calculate one mode-specific continuous forecast from a monthly row."""
    fast, slow = goulding_forecast_legs(
        signal_mode,
        row.get("g_fast"),
        row.get("g_slow"),
        row.get("g_fast_sharpe"),
        row.get("g_slow_sharpe"),
    )
    return goulding_continuous_raw(
        row.get("g_regime"), a_co, a_re, fast, slow
    )


def _build_calibration_context_from_monthly(
    monthly_by_history: dict[str, pl.DataFrame],
    cluster_by_history: dict[str, str],
    *,
    signal_mode: str,
    fast_months: int,
    slow_months: int,
    target_abs_forecast: float,
) -> GouldingCalibrationContext:
    """Build reusable pooled state from precomputed monthly histories."""
    return GouldingCalibrationContext(
        monthly_by_history=monthly_by_history,
        cluster_by_history=dict(cluster_by_history),
        outcome_history=_monthly_outcome_history(
            monthly_by_history,
            cluster_by_history,
        ),
        signal_mode=signal_mode,
        fast_months=fast_months,
        slow_months=slow_months,
        target_abs_forecast=target_abs_forecast,
    )


def build_goulding_calibration_context(
    calibration_histories: dict[str, FuturesHistory],
    cluster_by_history: dict[str, str],
    *,
    signal_mode: str,
    fast_months: int = GOULDING_FAST_MONTHS,
    slow_months: int = GOULDING_SLOW_MONTHS,
    target_abs_forecast: float = GOULDING_SELECTION_FORECAST_TARGET_ABS,
) -> GouldingCalibrationContext:
    """Build pooled monthly calibration once for many stitched targets."""
    missing = set(calibration_histories).difference(cluster_by_history)
    if missing:
        raise ValueError(
            "cluster_by_history is missing calibration histories: "
            f"{sorted(missing)}"
        )
    monthly_by_history = {
        history_code: _monthly_signal_frame(
            history,
            fast_months=fast_months,
            slow_months=slow_months,
        )
        for history_code, history in calibration_histories.items()
    }
    return _build_calibration_context_from_monthly(
        monthly_by_history,
        cluster_by_history,
        signal_mode=signal_mode,
        fast_months=fast_months,
        slow_months=slow_months,
        target_abs_forecast=target_abs_forecast,
    )


def _calibration_parameters(
    context: GouldingCalibrationContext,
    forecast_month: date,
) -> tuple[dict[str, tuple[float, float]], float | None]:
    """Return memoized cluster mixing and pooled scale for one month."""
    cached = context.parameters_by_month.get(forecast_month)
    if cached is not None:
        return cached
    clusters = sorted(set(context.cluster_by_history.values()))
    mixing_by_cluster = {
        cluster: estimate_mixing_params(
            context.outcome_history,
            forecast_month,
            cluster,
        )
        for cluster in clusters
    }
    scalar = None
    if context.signal_mode in {"continuous", "sharpe"}:
        raw_history: list[float | None] = []
        for history_code, monthly in context.monthly_by_history.items():
            a_co, a_re = mixing_by_cluster[
                context.cluster_by_history[history_code]
            ]
            for prior in monthly.filter(
                pl.col("forecast_month") < forecast_month
            ).iter_rows(named=True):
                raw_history.append(
                    _raw_goulding_forecast(
                        prior,
                        a_co,
                        a_re,
                        context.signal_mode,
                    )
                )
        scalar = estimate_goulding_forecast_scalar(
            raw_history,
            target_abs=context.target_abs_forecast,
        )
    resolved = (mixing_by_cluster, scalar)
    context.parameters_by_month[forecast_month] = resolved
    return resolved


def _forecast_rows(
    monthly_by_history: dict[str, pl.DataFrame],
    cluster_by_history: dict[str, str],
    *,
    signal_mode: str,
    fast_months: int,
    slow_months: int,
    target_abs_forecast: float,
    forecast_cap: float,
    calibration_context: GouldingCalibrationContext | None = None,
) -> dict[str, pl.DataFrame]:
    """Calculate monthly binary, continuous, or Sharpe-like forecasts.

    Mixing weights are estimated in an expanding window within economic
    subclass.  Continuous forecasts share one causal scalar across the
    universe, matching the standalone Goulding implementation's pooled
    normalization convention. Sharpe mode keeps raw-return regime labels but
    supplies horizon-normalized fast/slow legs to the continuous blend.
    """
    context = calibration_context or _build_calibration_context_from_monthly(
        monthly_by_history,
        cluster_by_history,
        signal_mode=signal_mode,
        fast_months=fast_months,
        slow_months=slow_months,
        target_abs_forecast=target_abs_forecast,
    )
    forecast_months = sorted({
        value
        for monthly in monthly_by_history.values()
        for value in monthly.get_column("forecast_month").drop_nulls().to_list()
    })
    rows_by_history: dict[str, list[dict[str, object]]] = {
        history_code: [] for history_code in monthly_by_history
    }

    for forecast_month in forecast_months:
        mixing_by_cluster, scalar = _calibration_parameters(
            context,
            forecast_month,
        )

        for history_code, monthly in monthly_by_history.items():
            current = monthly.filter(
                pl.col("forecast_month") == forecast_month
            )
            if current.is_empty():
                continue
            source = current.row(0, named=True)
            a_co, a_re = mixing_by_cluster[cluster_by_history[history_code]]
            current_raw_forecast = _raw_goulding_forecast(
                source,
                a_co,
                a_re,
                signal_mode,
            )
            if signal_mode in {"continuous", "sharpe"}:
                combined_forecast = normalize_goulding_forecast(
                    current_raw_forecast,
                    scalar,
                    cap=forecast_cap,
                )
            else:
                resolved = _goulding_direction(
                    source.get("g_regime"),
                    a_co,
                    a_re,
                    source.get("g_fast"),
                    source.get("g_slow"),
                )
                combined_forecast = resolved[0] if resolved is not None else None
            rows_by_history[history_code].append({
                **source,
                "a_co": a_co,
                "a_re": a_re,
                "g_raw_forecast": current_raw_forecast,
                "g_forecast_scalar": scalar,
                "combined_forecast": combined_forecast,
            })

    return {
        history_code: (
            pl.DataFrame(rows, infer_schema_length=None).sort("decision_date")
            if rows
            else monthly_by_history[history_code].head(0).with_columns(
                pl.lit(None, dtype=pl.Float64).alias("a_co"),
                pl.lit(None, dtype=pl.Float64).alias("a_re"),
                pl.lit(None, dtype=pl.Float64).alias("g_raw_forecast"),
                pl.lit(None, dtype=pl.Float64).alias("g_forecast_scalar"),
                pl.lit(None, dtype=pl.Float64).alias("combined_forecast"),
            )
        )
        for history_code, rows in rows_by_history.items()
    }


def build_goulding_selection_forecasts(
    histories: dict[str, FuturesHistory],
    cluster_by_history: dict[str, str],
    *,
    calibration_histories: dict[str, FuturesHistory] | None = None,
    calibration_context: GouldingCalibrationContext | None = None,
    signal_mode: str = "binary",
    fast_months: int = GOULDING_FAST_MONTHS,
    slow_months: int = GOULDING_SLOW_MONTHS,
    target_abs_forecast: float = GOULDING_SELECTION_FORECAST_TARGET_ABS,
    forecast_cap: float = GOULDING_SELECTION_FORECAST_CAP,
    min_valid_observations: int = 256,
) -> dict[str, CombinedForecastResult]:
    """Return selection-compatible Goulding forecasts for a history universe.

    Parameters
    ----------
    histories
        Unique reference-history code to source-neutral daily history.
    cluster_by_history
        Economic subclass used to pool each history's causal mixing estimates.
    calibration_histories
        Optional frozen universe used for mixing weights and continuous
        scaling. This lets a stitched target extend past the shared reference
        endpoint without giving each market a different pooled calibration.
    calibration_context
        Optional reusable pooled state built from ``calibration_histories``.
        Supply this when calculating several stitched targets sequentially.
    signal_mode
        ``binary`` emits full signed forecasts; ``continuous`` retains raw
        return magnitude; ``sharpe`` retains magnitude after dividing each
        horizon by its own lagged RMS monthly volatility. Both continuous
        modes apply the pooled expanding forecast scalar.
    fast_months, slow_months
        Calendar-month momentum horizons.
    target_abs_forecast, forecast_cap
        Continuous-mode pooled normalization. Selection defaults match the
        EWMAC mean-absolute-one, cap-two position-factor convention.
    min_valid_observations
        Minimum daily observations required for selection eligibility.

    Returns
    -------
    dict[str, CombinedForecastResult]
        One result per history, all calibrated from the same supplied universe.
    """
    if signal_mode not in GOULDING_SIGNAL_MODES:
        raise ValueError(
            f"signal_mode must be one of {GOULDING_SIGNAL_MODES}, "
            f"got {signal_mode!r}"
        )
    if fast_months <= 0 or slow_months <= fast_months:
        raise ValueError(
            "fast_months and slow_months must be positive and increasing"
        )
    if min_valid_observations < 2:
        raise ValueError("min_valid_observations must be at least 2")
    if target_abs_forecast <= 0 or forecast_cap <= 0:
        raise ValueError("forecast target and cap must be positive")
    if calibration_context is not None:
        if calibration_context.signal_mode != signal_mode:
            raise ValueError(
                "calibration context signal mode does not match request"
            )
        if calibration_context.target_abs_forecast != target_abs_forecast:
            raise ValueError(
                "calibration context forecast target does not match request"
            )
        if (
            calibration_context.fast_months != fast_months
            or calibration_context.slow_months != slow_months
        ):
            raise ValueError(
                "calibration context momentum horizons do not match request"
            )
        calibration_sources = calibration_context.monthly_by_history
    else:
        calibration_sources = calibration_histories or histories
    required_cluster_keys = set(histories) | set(calibration_sources)
    if not required_cluster_keys <= set(cluster_by_history):
        raise ValueError(
            "cluster_by_history must cover target and calibration histories"
        )

    monthly_by_history = {
        history_code: _monthly_signal_frame(
            history,
            fast_months=fast_months,
            slow_months=slow_months,
        )
        for history_code, history in histories.items()
    }
    if calibration_context is not None:
        resolved_calibration_context = calibration_context
    elif calibration_sources is histories:
        resolved_calibration_context = _build_calibration_context_from_monthly(
            monthly_by_history,
            cluster_by_history,
            signal_mode=signal_mode,
            fast_months=fast_months,
            slow_months=slow_months,
            target_abs_forecast=target_abs_forecast,
        )
    else:
        calibration_monthly_by_history = {
            history_code: _monthly_signal_frame(
                history,
                fast_months=fast_months,
                slow_months=slow_months,
            )
            for history_code, history in calibration_sources.items()
        }
        resolved_calibration_context = _build_calibration_context_from_monthly(
            calibration_monthly_by_history,
            cluster_by_history,
            signal_mode=signal_mode,
            fast_months=fast_months,
            slow_months=slow_months,
            target_abs_forecast=target_abs_forecast,
        )
    forecast_by_history = _forecast_rows(
        monthly_by_history,
        cluster_by_history,
        signal_mode=signal_mode,
        fast_months=fast_months,
        slow_months=slow_months,
        target_abs_forecast=target_abs_forecast,
        forecast_cap=forecast_cap,
        calibration_context=resolved_calibration_context,
    )
    results: dict[str, CombinedForecastResult] = {}
    for history_code, history in histories.items():
        monthly_forecast = forecast_by_history[history_code]
        signal_columns = [
            column
            for column in monthly_forecast.columns
            if column not in {"decision_date", "ret"}
        ]
        frame = (
            _history_execution_frame(history)
            .join(
                monthly_forecast.drop("ret").rename({"decision_date": "date"}),
                on="date",
                how="left",
            )
            .with_columns([
                pl.col(column).forward_fill() for column in signal_columns
            ])
        )
        valid_expr = (
            pl.col("combined_forecast").is_not_null()
            & pl.col("combined_forecast").is_finite()
            & pl.col("point_vol").is_not_null()
            & pl.col("point_vol").is_finite()
            & (pl.col("point_vol") > 0)
            & pl.col("point_change").is_not_null()
            & pl.col("point_change").is_finite()
        )
        frame = frame.with_columns(
            valid_expr.alias("forecast_valid")
        ).with_columns(
            pl.when(pl.col("forecast_valid"))
            .then(pl.col("combined_forecast"))
            .otherwise(None)
            .alias("combined_forecast")
        )
        valid = frame.filter(pl.col("forecast_valid"))
        first_valid = valid.get_column("date").min() if valid.height else None
        after_warmup = (
            frame.filter(pl.col("date") >= first_valid)
            if first_valid is not None
            else frame
        )
        audit: dict[str, object] = {
            "history_obs": frame.height,
            "forecast_valid_obs": valid.height,
            "forecast_invalid_obs": frame.height - valid.height,
            "forecast_post_warmup_invalid_obs": after_warmup.filter(
                ~pl.col("forecast_valid")
            ).height,
            "forecast_start": first_valid,
            "forecast_end": (
                valid.get_column("date").max() if valid.height else None
            ),
            "combined_nulls": frame.get_column(
                "combined_forecast"
            ).null_count(),
            "combined_nonfinite": frame.filter(
                pl.col("combined_forecast").is_not_null()
                & ~pl.col("combined_forecast").is_finite()
            ).height,
            "goulding_signal_mode": signal_mode,
            "goulding_fast_months": fast_months,
            "goulding_slow_months": slow_months,
            "goulding_target_abs_forecast": target_abs_forecast,
            "goulding_forecast_cap": forecast_cap,
            "goulding_cluster": cluster_by_history[history_code],
        }
        audit["forecast_elig"] = bool(
            valid.height >= min_valid_observations
            and audit["forecast_post_warmup_invalid_obs"] == 0
            and audit["combined_nonfinite"] == 0
        )
        if valid.height < min_valid_observations:
            audit["forecast_excl"] = "insufficient_valid_forecast_obs"
        elif audit["forecast_post_warmup_invalid_obs"]:
            audit["forecast_excl"] = "forecast_gaps_after_warmup"
        elif audit["combined_nonfinite"]:
            audit["forecast_excl"] = "nonfinite_combined_forecast"
        else:
            audit["forecast_excl"] = ""
        results[history_code] = CombinedForecastResult(
            frame=frame,
            weights={GOULDING_MODEL: 1.0},
            fdm=1.0,
            audit=audit,
        )
    return results

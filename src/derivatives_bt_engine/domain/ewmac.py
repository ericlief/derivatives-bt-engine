"""EWMAC forecast construction and causal pooled forecast normalization.

This module owns the exponentially weighted moving-average crossover signal
used by the futures research and backtest pipelines.  It consumes additive
continuous prices plus daily changes in the same point units, normalizes the
crossover by mixed point volatility, and produces a bounded unitless forecast.

Source-neutral futures history supplies ``point_change`` directly.  Generic
close-only callers remain supported: :func:`ewmac` derives ``close.diff()``
only when that column is absent.  Keeping this fallback here makes the input
contract explicit and avoids silently replacing history-provider changes.
"""

from __future__ import annotations

import math

import polars as pl

from derivatives_bt_engine.domain.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
    MIXED_VOL_FAST_SPAN,
    MIXED_VOL_MIN_SAMPLES,
    MIXED_VOL_SLOW_WEIGHT,
    MIXED_VOL_SLOW_YEARS,
    mixed_point_volatility,
)


EWMAC_FORECAST_TARGET_ABS = 0.5
EWMAC_FORECAST_CAP = 1.0
EWMAC_SCALAR_MIN_PERIODS = 500
EWMAC_SCALAR_POOLS = ("fixed", "global", "cluster", "instrument")


def ewmac(
    frame: pl.DataFrame,
    fast_span: int = 16,
    slow_span: int = 64,
    vol_span: int = MIXED_VOL_FAST_SPAN,
    vol_slow_years: int = MIXED_VOL_SLOW_YEARS,
    vol_slow_weight: float = MIXED_VOL_SLOW_WEIGHT,
    vol_min_samples: int = MIXED_VOL_MIN_SAMPLES,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    forecast_scalar: float = 1.0,
    forecast_cap: float = EWMAC_FORECAST_CAP,
) -> pl.DataFrame:
    """Build a volatility-normalized EWMAC forecast on additive prices.

    Parameters
    ----------
    frame
        Daily rows containing ``ts_event`` and additive ``close`` prices.
        If ``point_change`` is present, it is the authoritative daily price-
        point change.  Otherwise the function derives it from sorted closes.
    fast_span, slow_span
        EWM spans in observations; the fast span must be smaller.
    vol_span, vol_slow_years, vol_slow_weight, vol_min_samples
        Settings for the mixed point-volatility denominator.
    annualization_days
        Trading observations per year used only to size the slow-vol span.
    forecast_scalar, forecast_cap
        Multiplicative normalization and absolute cap for the final unitless
        ``signal``.  The default scalar intentionally exposes the raw rule.

    Returns
    -------
    polars.DataFrame
        The input rows sorted by ``ts_event`` with EWMAs, ``point_change``,
        point-volatility diagnostics, raw forecast, and bounded ``signal``.

    Notes
    -----
    ``close`` must be a Panama or additively equivalent point-price series,
    never a positive return index.  When supplied, ``point_change`` and
    ``close`` are assumed to describe the same history; this function does not
    overwrite or independently revalidate the provider-owned change series.
    """
    required = {"ts_event", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"EWMAC input missing columns: {sorted(missing)}")
    if fast_span <= 0 or slow_span <= 0 or vol_span <= 0:
        raise ValueError("EWMAC spans must be positive")
    if fast_span >= slow_span:
        raise ValueError("fast_span must be less than slow_span")
    if forecast_cap <= 0:
        raise ValueError("forecast_cap must be positive")

    result = frame.sort("ts_event")
    if "point_change" not in result.columns:
        # Close-only inputs have no provider-owned return stream.  Derive the
        # point change after sorting so diff never crosses out-of-order rows.
        result = result.with_columns(
            pl.col("close").cast(pl.Float64).diff().alias("point_change")
        )
    else:
        result = result.with_columns(pl.col("point_change").cast(pl.Float64))

    result = result.with_columns(
        pl.col("close").ewm_mean(span=fast_span, adjust=False).alias("fast_ewma"),
        pl.col("close").ewm_mean(span=slow_span, adjust=False).alias("slow_ewma"),
    ).with_columns(
        (pl.col("fast_ewma") - pl.col("slow_ewma")).alias("raw_ewmac"),
    )
    result = mixed_point_volatility(
        result,
        point_change_col="point_change",
        annualization_days=annualization_days,
        fast_span=vol_span,
        slow_years=vol_slow_years,
        slow_weight=vol_slow_weight,
        min_samples=vol_min_samples,
    ).with_columns(pl.col("mixed_point_vol").alias("point_vol"))
    result = result.with_columns(
        pl.when(pl.col("point_vol") > 0)
        # Both terms are in price points, so their ratio is unitless.  Risk
        # targeting and conversion to contracts remain downstream concerns.
        .then(pl.col("raw_ewmac") / pl.col("point_vol"))
        .otherwise(None)
        .alias("raw_forecast")
    )
    return result.with_columns(
        (pl.col("raw_forecast") * forecast_scalar)
        .clip(-forecast_cap, forecast_cap)
        .alias("signal")
    )


def estimate_ewmac_scalar_history(
    raw_forecasts: pl.DataFrame,
    *,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
) -> pl.DataFrame:
    """Causally estimate one pooled EWMAC scalar history per ``pool_key``.

    ``raw_forecasts`` is a long panel with ``ts_event``, ``instrument_code``,
    ``pool_key``, and unscaled ``raw_forecast``.  Zero forecasts are omitted
    from the magnitude sample, each instrument is forward-filled only after
    its first usable observation, and the daily cross-sectional median
    absolute forecast is averaged over time.  The daily statistic is shifted
    before accumulation, so the scalar stamped on date ``t`` uses dates
    strictly before ``t``.

    The returned dataframe has one row per pool/date with observation counts,
    the historical mean magnitude, scalar, and validity flag.  No backfill is
    performed; rows before ``min_periods`` prior daily samples remain null.
    """
    required = {"ts_event", "instrument_code", "pool_key", "raw_forecast"}
    missing = required - set(raw_forecasts.columns)
    if missing:
        raise ValueError(f"EWMAC scalar input missing columns: {sorted(missing)}")
    if target_abs_forecast <= 0 or not math.isfinite(target_abs_forecast):
        raise ValueError("target_abs_forecast must be finite and positive")
    if min_periods <= 0:
        raise ValueError("min_periods must be positive")
    if raw_forecasts.is_empty():
        return pl.DataFrame(
            schema={
                "ts_event": pl.Date,
                "pool_key": pl.String,
                "n_instruments": pl.UInt32,
                "cs_median_abs_forecast": pl.Float64,
                "prior_daily_observations": pl.UInt32,
                "historical_mean_abs_forecast": pl.Float64,
                "forecast_scalar": pl.Float64,
                "scalar_valid": pl.Boolean,
            }
        )

    identities = raw_forecasts.select("instrument_code", "pool_key").unique()
    dates = raw_forecasts.select("ts_event").unique().sort("ts_event")
    aligned = (
        identities.join(dates, how="cross")
        .join(
            raw_forecasts.select(
                "ts_event", "instrument_code", "pool_key", "raw_forecast"
            ),
            on=["ts_event", "instrument_code", "pool_key"],
            how="left",
        )
        .sort("pool_key", "instrument_code", "ts_event")
        .with_columns(
            pl.when(pl.col("raw_forecast") != 0.0)
            .then(pl.col("raw_forecast"))
            .otherwise(None)
            .forward_fill()
            .over("pool_key", "instrument_code")
            .alias("_scalar_observation")
        )
    )
    daily = (
        aligned.group_by("pool_key", "ts_event")
        .agg(
            pl.col("_scalar_observation")
            .is_not_null()
            .sum()
            .cast(pl.UInt32)
            .alias("n_instruments"),
            pl.col("_scalar_observation")
            .abs()
            .median()
            .alias("cs_median_abs_forecast"),
        )
        .sort("pool_key", "ts_event")
        .with_columns(
            pl.col("cs_median_abs_forecast")
            .shift(1)
            .over("pool_key")
            .alias("_prior_daily_median")
        )
        .with_columns(
            pl.col("_prior_daily_median")
            .fill_null(0.0)
            .cum_sum()
            .over("pool_key")
            .alias("_prior_sum"),
            pl.col("_prior_daily_median")
            .is_not_null()
            .cast(pl.UInt32)
            .cum_sum()
            .over("pool_key")
            .alias("prior_daily_observations"),
        )
        .with_columns(
            pl.when(pl.col("prior_daily_observations") >= min_periods)
            .then(pl.col("_prior_sum") / pl.col("prior_daily_observations"))
            .otherwise(None)
            .alias("historical_mean_abs_forecast")
        )
        .with_columns(
            pl.when(pl.col("historical_mean_abs_forecast") > 0)
            .then(target_abs_forecast / pl.col("historical_mean_abs_forecast"))
            .otherwise(None)
            .alias("forecast_scalar")
        )
        .with_columns(pl.col("forecast_scalar").is_not_null().alias("scalar_valid"))
    )
    return daily.select(
        "ts_event",
        "pool_key",
        "n_instruments",
        "cs_median_abs_forecast",
        "prior_daily_observations",
        "historical_mean_abs_forecast",
        "forecast_scalar",
        "scalar_valid",
    )

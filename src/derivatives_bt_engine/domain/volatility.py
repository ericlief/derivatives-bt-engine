"""Reusable futures volatility estimators.

The mixed estimator follows the specification used in *Advanced Futures
Trading*: a fast exponentially weighted standard deviation of roll-neutral
daily point changes, blended with a very slow exponentially weighted mean of
that fast volatility.  It deliberately operates in price-point units; callers
apply the executable contract's point value and FX conversion afterwards.
"""

from __future__ import annotations

import math

import polars as pl


CARVER_FAST_VOL_SPAN = 32
CARVER_SLOW_VOL_YEARS = 10
CARVER_SLOW_VOL_WEIGHT = 0.30
CARVER_VOL_MIN_SAMPLES = 10
CARVER_BUSINESS_DAYS_PER_YEAR = 256


def carver_mixed_point_volatility(
    frame: pl.DataFrame,
    *,
    point_change_col: str = "pt_change_1d",
    annualization_days: int = CARVER_BUSINESS_DAYS_PER_YEAR,
    fast_span: int = CARVER_FAST_VOL_SPAN,
    slow_years: int = CARVER_SLOW_VOL_YEARS,
    slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
    min_samples: int = CARVER_VOL_MIN_SAMPLES,
) -> pl.DataFrame:
    """Append causal fast, slow, and blended point-volatility columns.

    This mirrors pysystemtrade's ``mixed_vol_calc`` mechanics, with the
    book's 32-session fast span: ``adjust=True`` EWM standard deviation,
    followed by an ``adjust=True`` EWM mean whose span is ``slow_years``
    times the instrument's trading days per year.  The blend is 70% fast and
    30% slow at the defaults.

    The slow component starts as soon as fast volatility exists, exactly as
    pandas/pysystemtrade does; ``slow_history_years`` makes a short warm-up
    explicit rather than pretending the estimator already contains ten years
    of observations.
    """
    if point_change_col not in frame.columns:
        raise ValueError(f"frame is missing point-change column {point_change_col!r}")
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")
    if fast_span < 2:
        raise ValueError("fast_span must be at least 2")
    if slow_years <= 0:
        raise ValueError("slow_years must be positive")
    if not 0.0 <= slow_weight <= 1.0:
        raise ValueError("slow_weight must be between zero and one")
    if min_samples < 2:
        raise ValueError("min_samples must be at least 2")

    slow_span = slow_years * annualization_days
    result = frame.with_columns(
        pl.col(point_change_col)
        .cast(pl.Float64)
        .ewm_std(span=fast_span, adjust=True, min_samples=min_samples)
        .alias("fast_point_vol")
    ).with_columns(
        pl.col("fast_point_vol")
        .ewm_mean(span=slow_span, adjust=True, min_samples=1)
        .alias("slow_point_vol"),
        pl.col("fast_point_vol").is_not_null().cum_sum().alias("vol_observations"),
    )
    return result.with_columns(
        (
            pl.col("fast_point_vol") * (1.0 - slow_weight)
            + pl.col("slow_point_vol") * slow_weight
        ).alias("mixed_point_vol"),
        (pl.col("vol_observations") / float(annualization_days)).alias(
            "slow_history_years"
        ),
        pl.lit(fast_span).alias("fast_vol_span"),
        pl.lit(slow_span).alias("slow_vol_span"),
        pl.lit(slow_weight).alias("slow_vol_weight"),
        pl.lit(math.sqrt(annualization_days)).alias("annualization_sqrt"),
    )

"""Adapt the return based TSMOM signal to the selection backtest pipeline.

The standalone TSMOM backtester and the instrument selection pipeline have
different dataframe contracts.  This module joins the original continuous
return signal to the reference history's Panama point changes, mixed point
volatility, executable contract marks, and roll schedule.  The resulting
frame can therefore use the same reference return, allocation, buffering,
cost, and portfolio replay code as the multi speed EWMAC model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from derivatives_bt_engine.calculations.continuous_momentum import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
    build_features,
    continuous_momentum,
)
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.calculations.ewmac import (
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_SCALAR_MIN_PERIODS,
    estimate_ewmac_scalar_history,
)
from derivatives_bt_engine.calculations.volatility import mixed_point_volatility

if TYPE_CHECKING:
    from derivatives_bt_engine.data.futures_history import FuturesHistory


RETURN_TSMOM_MODEL = "return_tsmom"
RETURN_TSMOM_SCALAR_POOL_KEY = RETURN_TSMOM_MODEL


def _history_execution_frame(history: FuturesHistory) -> pl.DataFrame:
    """Build point volatility, contract marks, and roll events for one history.

    The Panama stream owns roll neutral point changes and the marks stream owns
    executable prices and roll dates.  Source gap fields are retained solely
    for audit; they never enter the signal calculation.
    """
    frame = mixed_point_volatility(
        history.panama_bars(),
    ).with_columns(pl.col("mixed_point_vol").alias("point_vol"))

    marks = history.marks
    if {"date", "mark_price"} <= set(marks.columns):
        contract_marks = (
            marks.select("date", pl.col("mark_price").alias("contract_close"))
            .sort("date")
            .unique(subset="date", keep="last", maintain_order=True)
        )
        frame = frame.join(contract_marks, on="date", how="left")
    else:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("contract_close")
        )

    if {"date", "is_roll"} <= set(marks.columns):
        roll_markers = (
            marks.select(
                "date",
                pl.col("is_roll").fill_null(False).cast(pl.Boolean),
            )
            .group_by("date")
            .agg(pl.col("is_roll").any())
        )
        frame = frame.join(roll_markers, on="date", how="left")
    else:
        frame = frame.with_columns(pl.lit(False).alias("is_roll"))

    gap_columns = [
        column
        for column in ("source_gap_before", "source_gap_days", "gap_pnl_omitted")
        if column in history.signal.columns
    ]
    if gap_columns:
        frame = frame.join(
            history.signal.select("date", *gap_columns),
            on="date",
            how="left",
        )
    return frame.with_columns(pl.col("is_roll").fill_null(False)).sort("date")


def build_return_tsmom_forecast(
    history: FuturesHistory,
    *,
    fast_window: int = DEFAULT_FAST_WINDOW,
    slow_window: int = DEFAULT_SLOW_WINDOW,
    vol_fast_window: int | None = None,
    vol_slow_window: int | None = None,
    regime_discount: float = 0.5,
    annualization_days: int = DEFAULT_ANNUALIZATION_DAYS,
    min_valid_observations: int = 256,
) -> CombinedForecastResult:
    """Return a selection compatible continuous return TSMOM forecast.

    Parameters are the return and volatility horizons used by
    :func:`continuous_momentum`.  The return signal is calculated from the
    positive reference signal index.  Position risk and cash P&L use Panama
    point changes and the same 256 day mixed point volatility estimator as the
    EWMAC selection variant.  Missing return observations hold the last valid
    signal, matching the standalone TSMOM backtester.

    Returns a standard :class:`CombinedForecastResult` containing the raw
    bounded signal.  Selection callers subsequently apply one causal pooled
    scalar so the continuous model shares EWMAC's mean-absolute-one, cap-two
    convention without estimating a different scale per instrument.
    """
    if min_valid_observations < 2:
        raise ValueError("min_valid_observations must be at least 2")

    signal_bars = history.signal_bars()
    momentum = (
        continuous_momentum(
            build_features(signal_bars),
            fast_window=fast_window,
            slow_window=slow_window,
            vol_fast_window=vol_fast_window,
            vol_slow_window=vol_slow_window,
            annualization_days=annualization_days,
            discount=regime_discount,
        )
        .rename({"signal": "combined_forecast"})
        .select(
            "date",
            "r_fast",
            "r_slow",
            "std_fast",
            "std_slow",
            "ts_fast",
            "ts_slow",
            "ts",
            "regime",
            "combined_forecast",
        )
    )
    signal_columns = [column for column in momentum.columns if column != "date"]
    frame = _history_execution_frame(history).join(
        momentum,
        on="date",
        how="left",
    ).with_columns(
        [pl.col(column).forward_fill() for column in signal_columns]
    ).with_columns(
        # Preserve the rule output independently of execution-data validity.
        # Scalar calibration is a signal operation and should not change when
        # one contract history has a locally absent daily point change.
        pl.col("combined_forecast").alias("raw_forecast")
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
    frame = frame.with_columns(valid_expr.alias("forecast_valid")).with_columns(
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
        "forecast_end": valid.get_column("date").max() if valid.height else None,
        "combined_nulls": frame.get_column("combined_forecast").null_count(),
        "combined_nonfinite": frame.filter(
            pl.col("combined_forecast").is_not_null()
            & ~pl.col("combined_forecast").is_finite()
        ).height,
        "return_tsmom_fast_window": fast_window,
        "return_tsmom_slow_window": slow_window,
        "return_tsmom_vol_fast_window": vol_fast_window or fast_window,
        "return_tsmom_vol_slow_window": vol_slow_window or slow_window,
        "return_tsmom_regime_discount": regime_discount,
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
    return CombinedForecastResult(
        frame=frame,
        weights={RETURN_TSMOM_MODEL: 1.0},
        fdm=1.0,
        audit=audit,
    )


def estimate_return_tsmom_scalar_history(
    raw_results: dict[str, CombinedForecastResult],
    *,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
) -> pl.DataFrame:
    """Estimate one causal pooled scalar for return TSMOM histories.

    Each input result contains the bounded but unnormalized return signal.
    The shared EWMAC estimator takes the daily cross-sectional median absolute
    forecast and uses only prior dates, putting return TSMOM on the same
    mean-absolute-one convention without using future observations.

    Parameters
    ----------
    raw_results
        Reference-history code to unscaled forecast result.
    target_abs_forecast
        Target historical mean absolute forecast. The selection convention is
        one, corresponding to Carver's forecast 10 after division by ten.
    min_periods
        Required prior daily cross-sectional observations before scaling.

    Returns
    -------
    polars.DataFrame
        One row per date with the pooled scalar and its causal diagnostics.
    """
    if not raw_results:
        raise ValueError("return TSMOM scalar estimation requires histories")
    panel = pl.concat(
        [
            result.frame.select(
                pl.col("date").alias("ts_event"),
                pl.lit(history_code).alias("instrument_code"),
                pl.lit(RETURN_TSMOM_SCALAR_POOL_KEY).alias("pool_key"),
                pl.col("raw_forecast"),
            )
            for history_code, result in raw_results.items()
        ],
        how="vertical",
    )
    return estimate_ewmac_scalar_history(
        panel,
        target_abs_forecast=target_abs_forecast,
        min_periods=min_periods,
    ).rename({"ts_event": "date"})


def scale_return_tsmom_forecast(
    raw_result: CombinedForecastResult,
    scalar_history: pl.DataFrame,
    *,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    forecast_cap: float = EWMAC_FORECAST_CAP,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    min_valid_observations: int = 256,
) -> CombinedForecastResult:
    """Apply a frozen causal pooled scalar to one return TSMOM result.

    The scalar history is normally estimated from the complete reference
    universe. An as-of join carries its final reference value into a later IB
    tail, keeping live extension on the same frozen calibration. The forecast
    is capped once at ``forecast_cap`` and then re-audited because scalar
    warm-up can begin later than the raw return signal.
    """
    required = {"date", "forecast_scalar"}
    missing = sorted(required.difference(scalar_history.columns))
    if missing:
        raise ValueError(
            f"return TSMOM scalar history missing columns: {missing}"
        )
    if target_abs_forecast <= 0 or forecast_cap <= 0:
        raise ValueError("return TSMOM forecast scale and cap must be positive")
    if scalar_min_periods <= 0 or min_valid_observations < 2:
        raise ValueError("return TSMOM observation minimums must be positive")

    frame = (
        raw_result.frame.drop("combined_forecast").sort("date")
        .join_asof(
            scalar_history.select("date", "forecast_scalar").sort("date"),
            on="date",
            strategy="backward",
        )
        .with_columns(
            (pl.col("raw_forecast") * pl.col("forecast_scalar"))
            .clip(-forecast_cap, forecast_cap)
            .alias("combined_forecast")
        )
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
    frame = frame.with_columns(valid_expr.alias("forecast_valid")).with_columns(
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
    audit = dict(raw_result.audit)
    audit.update({
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
        "combined_nulls": frame.get_column("combined_forecast").null_count(),
        "combined_nonfinite": frame.filter(
            pl.col("combined_forecast").is_not_null()
            & ~pl.col("combined_forecast").is_finite()
        ).height,
        "return_tsmom_target_abs_forecast": target_abs_forecast,
        "return_tsmom_forecast_cap": forecast_cap,
        "return_tsmom_scalar_min_periods": scalar_min_periods,
    })
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
    return CombinedForecastResult(
        frame=frame,
        weights=raw_result.weights,
        fdm=raw_result.fdm,
        audit=audit,
    )

"""Build reusable multi-speed EWMAC component frames from daily prices.

This module is the provider-neutral layer between raw daily histories and the
forecast-combination engine.  IB, database, live, backtest, and notebook code
can use the same builders without depending on the pysystemtrade futures
provider or Phase 2 selection orchestration.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import polars as pl

from derivatives_bt_engine.domain.ewmac import (
    CANONICAL_EWMAC_RULES,
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_RULE_BY_KEY,
    EWMAC_SCALAR_MIN_PERIODS,
    EwmacRule,
    estimate_ewmac_scalar_history,
    ewmac,
)
from derivatives_bt_engine.domain.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
    MIXED_VOL_FAST_SPAN,
    MIXED_VOL_MIN_SAMPLES,
    MIXED_VOL_SLOW_WEIGHT,
    MIXED_VOL_SLOW_YEARS,
)


_INTERNAL_DATE_COLUMN = "ts_event"


def resolve_daily_date_column(
    history_frame: pl.DataFrame,
    date_col: str | None,
) -> str:
    """Return the caller-owned daily date column used by a history frame."""
    if date_col is not None:
        if date_col not in history_frame.columns:
            raise ValueError(f"history frame is missing date column {date_col!r}")
        return date_col
    if "date" in history_frame.columns:
        return "date"
    if _INTERNAL_DATE_COLUMN in history_frame.columns:
        return _INTERNAL_DATE_COLUMN
    raise ValueError("history frame must contain 'date' or 'ts_event'")


def _to_internal_date_column(
    history_frame: pl.DataFrame,
    date_col: str,
) -> pl.DataFrame:
    """Adapt a public daily date key to the legacy EWMAC engine key."""
    if date_col == _INTERNAL_DATE_COLUMN:
        return history_frame
    if _INTERNAL_DATE_COLUMN in history_frame.columns:
        raise ValueError(
            f"history frame contains both {date_col!r} and "
            f"{_INTERNAL_DATE_COLUMN!r}; choose one date identity"
        )
    return history_frame.rename({date_col: _INTERNAL_DATE_COLUMN})


def build_ewmac_rule_component(
    history_frame: pl.DataFrame,
    rule: EwmacRule,
    *,
    date_col: str | None = None,
    instrument_code: str = "instrument",
    forecast_scalar: float | None = None,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    forecast_cap: float = EWMAC_FORECAST_CAP,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    vol_span: int = MIXED_VOL_FAST_SPAN,
    vol_slow_years: int = MIXED_VOL_SLOW_YEARS,
    vol_slow_weight: float = MIXED_VOL_SLOW_WEIGHT,
    vol_min_samples: int = MIXED_VOL_MIN_SAMPLES,
) -> pl.DataFrame:
    """Calculate one scaled EWMAC rule and retain its diagnostics.

    ``history_frame`` contains one instrument with ``date`` and ``close``;
    legacy ``ts_event`` input is also accepted.  ``date_col`` can identify a
    differently named daily key.  An existing ``point_change`` is retained as
    authoritative.  A fixed ``forecast_scalar`` is used when supplied;
    otherwise it is estimated causally from this instrument's prior raw
    forecasts after ``scalar_min_periods`` observations.

    The returned frame retains the original columns, common mixed-volatility
    fields, and rule-specific EMA, raw forecast, scalar, and final forecast
    columns.  Forecasts use normalized units with target absolute magnitude
    ``target_abs_forecast`` and bounds ``[-forecast_cap, forecast_cap]``.
    """
    if rule.key not in EWMAC_RULE_BY_KEY:
        raise ValueError(f"unsupported EWMAC rule: {rule.key}")
    if not instrument_code.strip():
        raise ValueError("instrument_code must not be empty")
    if scalar_min_periods <= 0:
        raise ValueError("scalar_min_periods must be positive")
    if target_abs_forecast <= 0 or not math.isfinite(target_abs_forecast):
        raise ValueError("target_abs_forecast must be finite and positive")
    if forecast_cap <= 0 or not math.isfinite(forecast_cap):
        raise ValueError("forecast_cap must be finite and positive")
    if forecast_scalar is not None and (
        not math.isfinite(forecast_scalar) or forecast_scalar <= 0
    ):
        raise ValueError("forecast_scalar must be finite and positive")

    public_date_column = resolve_daily_date_column(history_frame, date_col)
    internal_history = _to_internal_date_column(
        history_frame, public_date_column
    )
    raw_rule = ewmac(
        internal_history,
        fast_span=rule.fast,
        slow_span=rule.slow,
        vol_span=vol_span,
        vol_slow_years=vol_slow_years,
        vol_slow_weight=vol_slow_weight,
        vol_min_samples=vol_min_samples,
        annualization_days=annualization_days,
        forecast_scalar=1.0,
        forecast_cap=forecast_cap,
    )
    if forecast_scalar is None:
        # A one-instrument panel reuses the causal normalizer without claiming
        # that this history belongs to the futures normalization pool.
        scalar_history = estimate_ewmac_scalar_history(
            raw_rule.select(
                "ts_event",
                pl.lit(instrument_code).alias("instrument_code"),
                pl.lit(rule.key).alias("pool_key"),
                "raw_forecast",
            ),
            target_abs_forecast=target_abs_forecast,
            min_periods=scalar_min_periods,
        ).select("ts_event", "forecast_scalar")
        scaled_rule = raw_rule.join(scalar_history, on="ts_event", how="left")
    else:
        scaled_rule = raw_rule.with_columns(
            pl.lit(float(forecast_scalar)).alias("forecast_scalar")
        )

    suffix = f"{rule.fast}_{rule.slow}"
    result = (
        scaled_rule.with_columns(
            (pl.col("raw_forecast") * pl.col("forecast_scalar"))
            .clip(-forecast_cap, forecast_cap)
            .alias(rule.column)
        )
        .rename({
            "fast_ewma": f"fast_ewma_{suffix}",
            "slow_ewma": f"slow_ewma_{suffix}",
            "raw_ewmac": f"raw_ewmac_{suffix}",
            "raw_forecast": f"raw_fcst_{suffix}",
            "forecast_scalar": f"scalar_{suffix}",
        })
        # ``signal`` was the unscaled intermediate produced by ewmac(); the
        # named component column above is the authoritative scaled forecast.
        .drop("signal")
    )
    if public_date_column != _INTERNAL_DATE_COLUMN:
        result = result.rename({_INTERNAL_DATE_COLUMN: public_date_column})
    return result


def build_ewmac_components(
    history_frame: pl.DataFrame,
    *,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
    date_col: str | None = None,
    instrument_code: str = "instrument",
    forecast_scalars: Mapping[str, float] | None = None,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    forecast_cap: float = EWMAC_FORECAST_CAP,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    vol_span: int = MIXED_VOL_FAST_SPAN,
    vol_slow_years: int = MIXED_VOL_SLOW_YEARS,
    vol_slow_weight: float = MIXED_VOL_SLOW_WEIGHT,
    vol_min_samples: int = MIXED_VOL_MIN_SAMPLES,
) -> pl.DataFrame:
    """Return one wide, scaled EWMAC component frame for an instrument.

    This is the reusable multi-speed construction layer.  It calls
    :func:`build_ewmac_rule_component` for every requested speed and joins the
    rule-owned columns by ``ts_event``.  Fixed scalars must cover every rule;
    when ``forecast_scalars`` is omitted, each rule is normalized causally
    from the instrument's own prior history.
    """
    public_date_column = resolve_daily_date_column(history_frame, date_col)
    rule_tuple = tuple(rules)
    if not rule_tuple:
        raise ValueError("at least one EWMAC rule is required")
    rule_keys = [rule.key for rule in rule_tuple]
    if len(set(rule_keys)) != len(rule_keys):
        raise ValueError("EWMAC rules must not contain duplicates")
    unsupported = sorted(set(rule_keys).difference(EWMAC_RULE_BY_KEY))
    if unsupported:
        raise ValueError(f"unsupported EWMAC rules: {', '.join(unsupported)}")
    if forecast_scalars is not None:
        missing = sorted(set(rule_keys).difference(forecast_scalars))
        if missing:
            raise ValueError("forecast_scalars missing rules: " + ", ".join(missing))

    combined: pl.DataFrame | None = None
    for rule in rule_tuple:
        component = build_ewmac_rule_component(
            history_frame,
            rule,
            date_col=public_date_column,
            instrument_code=instrument_code,
            forecast_scalar=(
                None if forecast_scalars is None else forecast_scalars[rule.key]
            ),
            scalar_min_periods=scalar_min_periods,
            target_abs_forecast=target_abs_forecast,
            forecast_cap=forecast_cap,
            annualization_days=annualization_days,
            vol_span=vol_span,
            vol_slow_years=vol_slow_years,
            vol_slow_weight=vol_slow_weight,
            vol_min_samples=vol_min_samples,
        )
        suffix = f"{rule.fast}_{rule.slow}"
        owned_columns = [
            public_date_column,
            f"fast_ewma_{suffix}",
            f"slow_ewma_{suffix}",
            f"raw_ewmac_{suffix}",
            f"raw_fcst_{suffix}",
            f"scalar_{suffix}",
            rule.column,
        ]
        if combined is None:
            combined = component
        else:
            # Common history and volatility belong to the instrument; each
            # subsequent rule contributes only its speed-specific columns.
            combined = combined.join(
                component.select(owned_columns),
                on=public_date_column,
                how="left",
            )
    assert combined is not None
    return combined.sort(public_date_column)

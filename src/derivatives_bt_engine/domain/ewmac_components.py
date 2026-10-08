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

_VOLATILITY_COLUMNS = (
    "fast_point_vol",
    "slow_point_vol",
    "vol_observations",
    "mixed_point_vol",
    "slow_history_years",
    "fast_vol_span",
    "slow_vol_span",
    "slow_vol_weight",
    "annualization_sqrt",
)


def prepare_daily_price_frame(history_frame: pl.DataFrame) -> pl.DataFrame:
    """Validate and normalize one daily price history before rule calculation.

    The frame must contain ``date`` and ``close`` with at most one distinct
    observation per date.  Completely identical duplicate rows are harmless
    provider duplication and are collapsed.  Same-date rows that differ in
    any field are rejected because choosing a close or aggregating volume is a
    data-policy decision that must happen before daily EWMAC calculation.

    Returns a date-sorted Polars dataframe retaining all caller-owned columns.
    """
    required = {"date", "close"}
    missing = sorted(required.difference(history_frame.columns))
    if missing:
        raise ValueError(f"daily price frame missing columns: {missing}")
    if history_frame.schema["date"] != pl.Date:
        raise ValueError("daily price frame 'date' column must have Polars Date type")
    if history_frame.get_column("date").null_count():
        raise ValueError("daily price frame contains null dates")

    # Remove only rows identical across every field.  If a date remains
    # duplicated afterward, the source contains competing daily observations
    # and silently keeping one would make the signal depend on row order.
    deduplicated = history_frame.unique(maintain_order=True)
    conflicting_dates = (
        deduplicated.group_by("date")
        .len()
        .filter(pl.col("len") > 1)
        .get_column("date")
    )
    if conflicting_dates.len():
        examples = ", ".join(
            str(value) for value in conflicting_dates.sort().head(5).to_list()
        )
        raise ValueError(
            "daily price frame has conflicting rows for the same date; "
            "aggregate intraday/provider rows before EWMAC calculation "
            f"(examples: {examples})"
        )
    return deduplicated.sort("date")


def _to_internal_date_column(
    history_frame: pl.DataFrame,
) -> pl.DataFrame:
    """Adapt the public daily ``date`` key to the legacy EWMAC engine key."""
    if "date" not in history_frame.columns:
        raise ValueError("history frame is missing date column 'date'")
    if _INTERNAL_DATE_COLUMN in history_frame.columns:
        raise ValueError(
            "history frame contains both 'date' and 'ts_event'; "
            "the public component API requires one 'date' identity"
        )
    return history_frame.rename({"date": _INTERNAL_DATE_COLUMN})


def build_ewmac_rule_component(
    history_frame: pl.DataFrame,
    rule: EwmacRule,
    *,
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

    ``history_frame`` contains one instrument with ``date`` and ``close``.
    An existing ``point_change`` is retained as authoritative.  A fixed
    ``forecast_scalar`` is used when supplied; otherwise it is estimated
    causally from this instrument's prior raw forecasts after
    ``scalar_min_periods`` observations.

    The returned frame retains the original columns, common mixed-volatility
    fields, and rule-specific EMA, raw forecast, scalar, and final forecast
    columns. ``mixed_point_vol`` is the one authoritative volatility series;
    the lower-level EWMAC engine's identical ``point_vol`` compatibility alias
    is omitted. Forecasts use normalized units with target absolute magnitude
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

    daily_history = prepare_daily_price_frame(history_frame)
    internal_history = _to_internal_date_column(daily_history)
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
        .drop("signal", "point_vol")
        .rename({_INTERNAL_DATE_COLUMN: "date"})
    )
    rule_columns = [
        f"fast_ewma_{suffix}",
        f"slow_ewma_{suffix}",
        f"raw_ewmac_{suffix}",
        f"raw_fcst_{suffix}",
        f"scalar_{suffix}",
        rule.column,
    ]
    history_columns = list(daily_history.columns)
    if "point_change" not in history_columns:
        history_columns.append("point_change")
    # Keep instrument-wide price and volatility inputs together before the
    # complete diagnostic block owned by this particular EWMAC speed.
    volatility_columns = [
        column for column in _VOLATILITY_COLUMNS if column in result.columns
    ]
    return result.select(history_columns + volatility_columns + rule_columns)


def build_ewmac_components(
    history_frame: pl.DataFrame,
    *,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
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
    rule-owned columns by ``date``.  Fixed scalars must cover every rule;
    when ``forecast_scalars`` is omitted, each rule is normalized causally
    from the instrument's own prior history.
    """
    daily_history = prepare_daily_price_frame(history_frame)
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
            daily_history,
            rule,
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
            "date",
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
                on="date",
                how="left",
            )
    assert combined is not None
    return combined.sort("date")

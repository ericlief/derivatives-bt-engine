"""Build AFTS futures carry forecasts from pysystemtrade curve data.

The source-neutral history layer supplies same-session prices for a currently
held contract and a second dated contract.  This module annualises that curve
slope, divides it by annualised point volatility, smooths four carry rules,
and combines the resulting forecasts on the repository's divided-by-ten
Carver scale.  It also provides the AFTS final-rule-count diversification
table and the default 60/40 trend/carry combination used by strategy eleven.

The carry legs and volatility are matched on the same date. The EWMA skips
null raw carry and retains its state. The forecast stage forward-fills the
resulting scaled forecasts without filling raw curve observations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

import polars as pl

from derivatives_bt_engine.calculations.ewmac import EWMAC_RULE_BY_KEY
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.calculations.return_tsmom_forecast import (
    _history_execution_frame,
)
from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
)

if TYPE_CHECKING:
    from derivatives_bt_engine.data.futures_history import FuturesHistory


CALENDAR_DAYS_IN_YEAR = 365.25
CARRY_MODEL = "carry"
EWMAC_CARRY_MODEL = "ewmac_carry"
# AFTS strategy ten defines Carry5/20/60/120 as pandas EWMA spans. The pinned
# pysystemtrade implementation instead passes its own 10/30/60/125 values as
# positional pandas ``com`` arguments; that separate convention is useful for
# parity research but is not the final AFTS rule set implemented here.
CARRY_SMOOTH_SPANS = (5, 20, 60, 120)
CARRY_FORECAST_SCALAR = 3.0
CARRY_FORECAST_CAP = 2.0
CARRY_MIN_POST_WARMUP_COVERAGE = 0.90
CARRY_TURNOVER_SOURCE = "pooled_pst_history_afts_span_continuous_v3"
DEFAULT_TREND_FAMILY_WEIGHT = 0.60
DEFAULT_CARRY_FAMILY_WEIGHT = 0.40

# AFTS table 40 annual forecast turnovers. These published values are retained
# as an external benchmark for the pooled PST estimates persisted in the rule
# cache; production rule eligibility uses the pooled estimates.
AFT_CARRY_ANNUAL_TRADES: dict[int, float] = {
    5: 5.75,
    20: 3.12,
    60: 1.82,
    120: 1.22,
}

# Table 52 in Advanced Futures Trading Strategies.  Counts omitted by the
# printed table are linearly interpolated by ``aft_final_rule_fdm``.
AFT_FINAL_RULE_FDM: dict[int, float] = {
    1: 1.00,
    2: 1.02,
    3: 1.03,
    4: 1.23,
    5: 1.25,
    6: 1.27,
    7: 1.29,
    8: 1.32,
    9: 1.34,
    10: 1.35,
    11: 1.36,
    12: 1.38,
    13: 1.39,
    14: 1.41,
    15: 1.42,
    16: 1.44,
    17: 1.46,
    18: 1.48,
    19: 1.50,
    20: 1.53,
    21: 1.54,
    22: 1.55,
    25: 1.69,
    30: 1.81,
    35: 1.93,
    40: 2.00,
}


def contract_year_fraction(contract_id: str | int) -> float:
    """Return the pysystemtrade year-plus-month fraction for a contract ID.

    Parameters
    ----------
    contract_id
        A ``YYYYMM`` or ``YYYYMMDD`` contract identifier.  The day suffix is
        deliberately ignored, matching ``rawCarryData`` in pysystemtrade.

    Returns
    -------
    float
        ``year + month / 12``.  January is therefore ``year + 1/12``.

    Raises
    ------
    ValueError
        If the identifier does not contain a valid year and month.
    """
    value = str(contract_id).strip()
    if len(value) not in (6, 8) or not value.isdigit():
        raise ValueError(f"invalid futures contract ID: {contract_id!r}")
    year = int(value[:4])
    month = int(value[4:6])
    if year < 1000 or not 1 <= month <= 12:
        raise ValueError(f"invalid futures contract ID: {contract_id!r}")
    return year + month / 12.0


def _contract_year_fraction_or_none(contract_id: object) -> float | None:
    """Return a contract fraction or null for dataframe validation paths."""
    if contract_id is None:
        return None
    try:
        return contract_year_fraction(str(contract_id))
    except ValueError:
        return None


def aft_final_rule_fdm(rule_count: int) -> float:
    """Return the AFTS approximate FDM for a final trading-rule count.

    Exact printed table entries are returned unchanged.  Intermediate counts
    above 22 are linearly interpolated between the surrounding published
    anchors, and counts of 40 or more are capped at 2.0.
    """
    if rule_count < 1:
        raise ValueError("rule_count must be positive")
    if rule_count >= 40:
        return 2.0
    exact = AFT_FINAL_RULE_FDM.get(rule_count)
    if exact is not None:
        return exact
    anchors = sorted(AFT_FINAL_RULE_FDM)
    lower = max(count for count in anchors if count < rule_count)
    upper = min(count for count in anchors if count > rule_count)
    fraction = (rule_count - lower) / float(upper - lower)
    return AFT_FINAL_RULE_FDM[lower] + fraction * (
        AFT_FINAL_RULE_FDM[upper] - AFT_FINAL_RULE_FDM[lower]
    )


def _validate_daily_frame(frame: pl.DataFrame, required: set[str], name: str) -> None:
    """Validate required fields and unique public daily identities."""
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")
    if frame.schema["date"] != pl.Date:
        raise ValueError(f"{name} 'date' column must have Polars Date type")
    if frame.get_column("date").null_count():
        raise ValueError(f"{name} contains null dates")
    if frame.group_by("date").len().filter(pl.col("len") > 1).height:
        raise ValueError(f"{name} contains duplicate dates")


def build_raw_carry_frame(
    carry_frame: pl.DataFrame,
    point_vol_frame: pl.DataFrame,
    *,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    point_vol_column: str = "mixed_point_vol",
) -> pl.DataFrame:
    """Calculate annualised roll and volatility-normalised raw carry.

    ``carry_frame`` must contain same-timestamp current and carry prices and
    contract IDs. ``point_vol_frame`` supplies daily point volatility under
    ``point_vol_column``. By default that is the mixed estimator built from
    roll-neutral Panama point changes: a 32-session fast EWM standard
    deviation blended 70/30 with a 10-year EWM average of the fast volatility.
    This function annualizes that daily point volatility using
    ``sqrt(annualization_days)`` (normally ``sqrt(256)``). The result retains
    every observed carry row and adds reproducible inputs, a validity flag,
    and an explicit exclusion reason.
    The signed price slope is ``current_price - carry_price`` over the signed
    contract-year gap ``carry_contract - current_contract``. This is distinct
    from the Panama roll adjustment, which is normally ``forward - price`` at
    a contract switch and therefore has the opposite sign for the same pair.
    Zero price differentials are invalid, matching pysystemtrade's conversion
    of zero raw roll to missing.
    """
    _validate_daily_frame(
        carry_frame,
        {
            "date",
            "current_price",
            "current_contract",
            "carry_price",
            "carry_contract",
        },
        "carry frame",
    )
    _validate_daily_frame(
        point_vol_frame,
        {"date", point_vol_column},
        "point-volatility frame",
    )
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")

    current_fraction = pl.col("current_contract").map_elements(
        _contract_year_fraction_or_none,
        return_dtype=pl.Float64,
    )
    carry_fraction = pl.col("carry_contract").map_elements(
        _contract_year_fraction_or_none,
        return_dtype=pl.Float64,
    )
    frame = (
        carry_frame.sort("date")
        .join(
            point_vol_frame.select("date", point_vol_column),
            on="date",
            how="left",
        )
        .with_columns(
            current_fraction.alias("current_con_yr"),
            carry_fraction.alias("carry_con_yr"),
            (pl.col("current_price") - pl.col("carry_price")).alias("raw_roll"),
        )
        .with_columns(
            # Keep the contract gap signed. If the two legs are swapped, both
            # price and time differences reverse, leaving the curve slope
            # unchanged; an unexpected negative gap can instead expose
            # mismatched/reversed contract IDs in the source row.
            (pl.col("carry_con_yr") - pl.col("current_con_yr")).alias(
                "con_yr_frac"
            ),
            # mixed_point_vol is daily point volatility from the roll-neutral
            # Panama point-change stream. Annualize it in the same price-point
            # units as raw_roll; contract marks are joined for execution checks
            # elsewhere and are not the volatility input.
            (
                pl.col(point_vol_column).cast(pl.Float64)
                * math.sqrt(annualization_days)
            ).alias("ann_point_vol"),
        )
    )
    valid_contracts = (
        pl.col("current_con_yr").is_not_null()
        & pl.col("carry_con_yr").is_not_null()
        & (pl.col("con_yr_frac") != 0)
    )
    valid_prices = (
        pl.col("current_price").is_not_null()
        & pl.col("current_price").is_finite()
        & pl.col("carry_price").is_not_null()
        & pl.col("carry_price").is_finite()
        & (pl.col("raw_roll") != 0)
    )
    valid_vol = (
        pl.col("ann_point_vol").is_not_null()
        & pl.col("ann_point_vol").is_finite()
        & (pl.col("ann_point_vol") > 0)
    )
    valid = valid_contracts & valid_prices & valid_vol
    reason = (
        pl.when(
            pl.col("current_con_yr").is_null()
            | pl.col("carry_con_yr").is_null()
        )
        .then(pl.lit("invalid_contract"))
        .when(pl.col("con_yr_frac") == 0)
        .then(pl.lit("zero_contract_gap"))
        .when(
            pl.col("current_price").is_null()
            | ~pl.col("current_price").is_finite()
            | pl.col("carry_price").is_null()
            | ~pl.col("carry_price").is_finite()
        )
        .then(pl.lit("invalid_price"))
        .when(pl.col("raw_roll") == 0)
        .then(pl.lit("zero_raw_roll"))
        .when(~valid_vol)
        .then(pl.lit("invalid_point_vol"))
        .otherwise(pl.lit(""))
    )
    return (
        frame.with_columns(
            pl.when(valid)
            .then(pl.col("raw_roll") / pl.col("con_yr_frac"))
            .otherwise(None)
            .alias("ann_roll"),
            valid.alias("carry_valid"),
            reason.alias("carry_excl"),
        )
        .with_columns(
            pl.when(pl.col("carry_valid"))
            .then(pl.col("ann_roll") / pl.col("ann_point_vol"))
            .otherwise(None)
            .alias("raw_carry")
        )
    )


def build_carry_components(
    raw_carry_frame: pl.DataFrame,
    *,
    smooth_spans: Iterable[int] = CARRY_SMOOTH_SPANS,
    forecast_scalar: float = CARRY_FORECAST_SCALAR,
    forecast_cap: float = CARRY_FORECAST_CAP,
) -> CombinedForecastResult:
    """Smooth, scale, equally weight, and diversify carry forecasts.

    Smoothing uses ``span=N, adjust=True, ignore_nulls=True`` for the AFTS
    Carry5/20/60/120 variants. Missing observations take no weight and
    leave that day's forecast null. History carries through provider
    handoffs and calendar gaps. Every component uses a fixed scalar of
    ``3.0`` for Carver's 30 on this project's normalized forecast scale.
    Each scaled component is capped at +/-2, then components receive
    equal weights. One AFTS final-rule-count FDM is applied to that weighted
    average, followed by the portfolio forecast cap. In trend/carry mode the
    caller uses these component forecasts and applies a single FDM across the
    full EWMAC-plus-carry rule set instead of using this carry-only FDM.
    """
    _validate_daily_frame(
        raw_carry_frame,
        {"date", "raw_carry", "carry_valid"},
        "raw carry frame",
    )
    rules = tuple(int(value) for value in smooth_spans)
    if not rules or any(value <= 0 for value in rules):
        raise ValueError("carry smoothing spans must be positive")
    if len(set(rules)) != len(rules):
        raise ValueError("carry smoothing spans must not contain duplicates")
    if not math.isfinite(forecast_scalar) or forecast_scalar <= 0:
        raise ValueError("forecast_scalar must be finite and positive")
    if not math.isfinite(forecast_cap) or forecast_cap <= 0:
        raise ValueError("forecast_cap must be finite and positive")

    frame = raw_carry_frame.sort("date")
    valid_raw_carry = pl.when(pl.col("carry_valid")).then(pl.col("raw_carry"))
    component_columns: list[str] = []
    for smooth_span in rules:
        smooth_column = f"smooth_carry_{smooth_span}"
        forecast_column = f"fcst_carry_{smooth_span}"
        frame = frame.with_columns(
            valid_raw_carry.ewm_mean(
                span=smooth_span,
                adjust=True,
                ignore_nulls=True,
                min_samples=1,
            )
            .alias(smooth_column)
        ).with_columns(
            pl.when(pl.col("carry_valid"))
            .then(
                # Scalar and per-rule cap apply before equal weighting and FDM.
                (pl.col(smooth_column) * forecast_scalar).clip(
                    -forecast_cap,
                    forecast_cap,
                )
            )
            .otherwise(None)
            .alias(forecast_column)
        )
        component_columns.append(forecast_column)

    weight = 1.0 / len(rules)
    weights = {f"carry{rule}": weight for rule in rules}
    fdm = aft_final_rule_fdm(len(rules))
    component_valid = pl.all_horizontal(
        [
            pl.col(column).is_not_null() & pl.col(column).is_finite()
            for column in component_columns
        ]
    )
    weighted = pl.sum_horizontal(
        [pl.col(column) * weight for column in component_columns]
    )
    frame = frame.with_columns(
        component_valid.alias("forecast_valid"),
        pl.lit(forecast_scalar).alias("carry_forecast_scalar"),
        pl.lit(forecast_cap).alias("carry_forecast_cap"),
        pl.lit(fdm).alias("carry_fdm"),
        pl.lit(fdm).alias("forecast_fdm"),
    ).with_columns(
        pl.when(pl.col("forecast_valid"))
        # FDM diversifies the weighted rule combination; it is not a separate
        # multiplier embedded in each smoothed speed. Cap again after FDM.
        .then((weighted * fdm).clip(-forecast_cap, forecast_cap))
        .otherwise(None)
        .alias("combined_forecast")
    )
    valid = frame.filter(pl.col("forecast_valid"))
    audit: dict[str, object] = {
        "history_obs": frame.height,
        "forecast_valid_obs": valid.height,
        "forecast_invalid_obs": frame.height - valid.height,
        "forecast_start": valid.get_column("date").min() if valid.height else None,
        "forecast_end": valid.get_column("date").max() if valid.height else None,
        "carry_smooth_spans": rules,
        "carry_forecast_scalar": forecast_scalar,
        "carry_forecast_cap": forecast_cap,
        "combined_nulls": frame.get_column("combined_forecast").null_count(),
        "combined_nonfinite": frame.filter(
            pl.col("combined_forecast").is_not_null()
            & ~pl.col("combined_forecast").is_finite()
        ).height,
    }
    return CombinedForecastResult(frame, weights, fdm, audit)


def build_carry_forecast(
    history: FuturesHistory,
    *,
    smooth_spans: Iterable[int] = CARRY_SMOOTH_SPANS,
    forecast_scalar: float = CARRY_FORECAST_SCALAR,
    forecast_cap: float = CARRY_FORECAST_CAP,
    turnover_max_stale_days: int | None = None,
    min_post_warmup_coverage: float = CARRY_MIN_POST_WARMUP_COVERAGE,
    min_valid_observations: int = 256,
) -> CombinedForecastResult:
    """Return a selection-compatible carry forecast for one futures history.

    A valid same-session curve supplies a fresh forecast independently of
    the execution contract. Forward-fill the scaled rule forecasts on the
    daily execution grid, including across rolls and long gaps. Keep raw curve
    fields on their source date and report the age in calendar days of the
    forecast used. Dates before the first valid forecast remain null.
    ``turnover_max_stale_days`` preserves the historical observation alignment
    used by rule-cost calibration when supplied, in calendar days.
    """
    if min_valid_observations < 2:
        raise ValueError("min_valid_observations must be at least 2")
    if not 0 < min_post_warmup_coverage <= 1:
        raise ValueError("min_post_warmup_coverage must be in (0, 1]")
    execution = _history_execution_frame(history)
    # Keep the execution contract for diagnostics and legacy turnover calibration.
    mark_contracts = history.marks.select(
        "date",
        pl.col("contract_id").alias("mark_contract"),
    )
    execution = execution.join(mark_contracts, on="date", how="left").sort("date")
    raw = build_raw_carry_frame(
        history.carry,
        execution.select("date", "mixed_point_vol"),
    )
    components = build_carry_components(
        raw,
        smooth_spans=smooth_spans,
        forecast_scalar=forecast_scalar,
        forecast_cap=forecast_cap,
    )
    carry_owned = components.frame.drop("mixed_point_vol").with_columns(
        pl.col("date").alias("carry_obs_date"),
    ).rename({"forecast_valid": "carry_obs_valid"})
    forecast_columns = [
        *(f"fcst_carry_{rule[5:]}" for rule in components.weights),
        "combined_forecast",
    ]
    if turnover_max_stale_days is None:
        frame = execution.join(carry_owned, on="date", how="left").sort("date")
    else:
        # Keep the rule-cache calibration on its original bounded sample.
        frame = execution.join_asof(
            carry_owned.drop("date").sort("carry_obs_date"),
            left_on="date",
            right_on="carry_obs_date",
            strategy="backward",
            tolerance=f"{turnover_max_stale_days}d",
        )
    usable_observation = pl.col("carry_obs_valid")
    if turnover_max_stale_days is not None:
        usable_observation &= pl.col("mark_contract") == pl.col("current_contract")
    forecast_observation_date = pl.when(usable_observation).then(pl.col("carry_obs_date"))
    if turnover_max_stale_days is None:
        forecast_observation_date = forecast_observation_date.forward_fill()
        frame = frame.with_columns(
            pl.when(usable_observation).then(pl.col(column)).forward_fill().alias(column)
            for column in forecast_columns
        )
    frame = frame.with_columns(
        forecast_observation_date.alias("carry_forecast_obs_date"),
    ).with_columns(
        (pl.col("date") - pl.col("carry_forecast_obs_date"))
        .dt.total_days()
        .alias("carry_stale_days")
    )
    execution_valid = (
        pl.col("carry_forecast_obs_date").is_not_null()
        & pl.col("combined_forecast").is_not_null()
        & pl.col("combined_forecast").is_finite()
        & pl.col("point_vol").is_not_null()
        & pl.col("point_vol").is_finite()
        & (pl.col("point_vol") > 0)
        & pl.col("point_change").is_not_null()
        & pl.col("point_change").is_finite()
    )
    frame = frame.with_columns(execution_valid.alias("forecast_valid")).with_columns(
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
    post_warmup_valid_obs = after_warmup.filter(pl.col("forecast_valid")).height
    post_warmup_coverage = (
        post_warmup_valid_obs / after_warmup.height
        if after_warmup.height
        else 0.0
    )
    audit = dict(components.audit)
    audit.update(
        {
            "history_obs": frame.height,
            "forecast_valid_obs": valid.height,
            "forecast_invalid_obs": frame.height - valid.height,
            "forecast_post_warmup_invalid_obs": after_warmup.filter(
                ~pl.col("forecast_valid")
            ).height,
            "forecast_post_warmup_coverage": post_warmup_coverage,
            "forecast_min_post_warmup_coverage": min_post_warmup_coverage,
            "forecast_start": first_valid,
            "forecast_end": (
                valid.get_column("date").max() if valid.height else None
            ),
            "carry_stale_obs": valid.filter(
                pl.col("carry_stale_days") > 0
            ).height,
            "carry_contract_mismatch_obs": frame.filter(
                pl.col("carry_obs_date").is_not_null()
                & (pl.col("mark_contract") != pl.col("current_contract"))
            ).height,
            "combined_nulls": frame.get_column("combined_forecast").null_count(),
        }
    )
    audit["forecast_elig"] = bool(
        valid.height >= min_valid_observations
        and post_warmup_coverage >= min_post_warmup_coverage
        and audit["combined_nonfinite"] == 0
    )
    if valid.height < min_valid_observations:
        audit["forecast_excl"] = "insufficient_valid_forecast_obs"
    elif post_warmup_coverage < min_post_warmup_coverage:
        audit["forecast_excl"] = "insufficient_carry_coverage"
    elif audit["combined_nonfinite"]:
        audit["forecast_excl"] = "nonfinite_combined_forecast"
    else:
        audit["forecast_excl"] = ""
    return CombinedForecastResult(frame, components.weights, components.fdm, audit)


def expire_stale_carry_forecasts(
    result: CombinedForecastResult,
    max_stale_days: int | None,
    *,
    flat_when_expired: bool = False,
) -> CombinedForecastResult:
    """Mark filled carry rules unavailable after the calendar-day cutoff.

    The last usable curve date is shared by the carry speeds. Keep that date,
    the raw observations, and the EWMA state for auditing and eventual resumption.
    Standalone carry can request a valid zero combined forecast on expired
    sessions to target a flat position. An unset cutoff returns the original result.
    """
    if max_stale_days is None:
        return result
    if max_stale_days < 0:
        raise ValueError("forecast staleness cutoff must be nonnegative")
    stale = (pl.col("carry_stale_days") > max_stale_days).fill_null(False)
    frame = result.frame.with_columns(
        stale.alias("carry_stale"),
        pl.when(stale & pl.col("forecast_valid"))
        .then(0.0 if flat_when_expired else None)
        .otherwise(pl.col("combined_forecast"))
        .alias("combined_forecast"),
        (pl.col("forecast_valid") & (~stale | flat_when_expired))
        .alias("forecast_valid"),
        *(
            pl.when(~stale).then(pl.col(f"fcst_carry_{rule[5:]}"))
            .alias(f"fcst_carry_{rule[5:]}")
            for rule in result.weights
        ),
    )
    return CombinedForecastResult(frame, result.weights, result.fdm, result.audit)


def combine_trend_and_carry_forecasts(
    trend: CombinedForecastResult,
    carry: CombinedForecastResult,
    *,
    trend_weight: float = DEFAULT_TREND_FAMILY_WEIGHT,
    carry_weight: float = DEFAULT_CARRY_FAMILY_WEIGHT,
    forecast_cap: float = CARRY_FORECAST_CAP,
    min_valid_observations: int = 256,
    max_stale_days: int | None = None,
) -> CombinedForecastResult:
    """Combine EWMAC trend and carry components with top-down family weights.

    The function reconstructs each family before its family-only FDM, applies
    60% to the caller's EWMAC rule weights and 40% equally across carry rules,
    and normalizes those base weights over finite forecasts each session.
    The fixed AFTS FDM uses the count of positively weighted configured rules.
    Both families supply scaled, capped components. The trend frame supplies
    the daily execution grid. A row with no available weighted rules remains
    null until a rule has been observed. A calendar-day cutoff makes expired
    rules unavailable, with zero weight. Each EWMAC rule has its own
    observation date. If expiry leaves no usable rules, target flat.
    """
    if trend_weight < 0 or carry_weight < 0 or not math.isclose(
        trend_weight + carry_weight,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "trend and carry family weights must be nonnegative and sum to one"
        )
    if min_valid_observations < 2:
        raise ValueError("min_valid_observations must be at least 2")
    if not math.isfinite(forecast_cap) or forecast_cap <= 0:
        raise ValueError("forecast_cap must be finite and positive")

    trend_columns: dict[str, str] = {}
    for rule in trend.weights:
        definition = EWMAC_RULE_BY_KEY.get(rule)
        if definition is None:
            raise ValueError(f"unsupported trend rule for carry blend: {rule}")
        trend_columns[rule] = definition.column
    carry_columns: dict[str, str] = {}
    for rule in carry.weights:
        if not rule.startswith("carry") or not rule[5:].isdigit():
            raise ValueError(f"unsupported carry rule for trend blend: {rule}")
        carry_columns[rule] = f"fcst_carry_{rule[5:]}"
    if not trend_columns or not carry_columns:
        raise ValueError("trend and carry results must each contain component rules")

    missing_trend = sorted(set(trend_columns.values()).difference(trend.frame.columns))
    missing_carry = sorted(set(carry_columns.values()).difference(carry.frame.columns))
    if missing_trend or missing_carry:
        raise ValueError(
            "trend/carry blend missing component columns: "
            f"trend={missing_trend}, carry={missing_carry}"
        )
    carry = expire_stale_carry_forecasts(carry, max_stale_days)

    # The trend frame owns the daily marks. Label overlapping carry inputs
    # explicitly because they belong to the potentially older curve observation.
    carry_diagnostics = (
        "carry_obs_date", "carry_forecast_obs_date", "carry_stale_days",
        "carry_obs_valid", "carry_stale",
        "carry_price", "carry_contract",
        "current_con_yr", "carry_con_yr", "con_yr_frac", "ann_point_vol",
        "raw_roll", "ann_roll", "raw_carry", "carry_valid", "carry_excl",
        "carry_forecast_scalar", "carry_forecast_cap",
        *(
            name
            for rule, column in carry_columns.items()
            for name in (f"smooth_carry_{rule[5:]}", column)
        ),
    )
    carry_provenance = {
        "source_timestamp": "carry_source_timestamp",
        "source_segment": "carry_source_segment",
        "source_segment_right": "carry_curve_source_segment",
        "quality_flag": "carry_quality_flag",
        "current_price": "carry_current_price",
        "current_contract": "carry_current_contract",
    }
    carry_owned = [
        "date",
        *(
            pl.col(name).alias(carry_provenance.get(name, name))
            for name in (*carry_diagnostics, *carry_provenance)
            if name in carry.frame.columns
        ),
        pl.col("combined_forecast").alias("carry_family_forecast"),
        pl.col("forecast_valid").alias("carry_forecast_valid"),
    ]
    frame = trend.frame.rename(
        {
            "combined_forecast": "trend_family_forecast",
            "forecast_valid": "trend_forecast_valid",
        }
    ).join(carry.frame.select(carry_owned), on="date", how="left")

    final_weights = {
        **{
            f"trend:{rule}": trend_weight * weight
            for rule, weight in trend.weights.items()
        },
        **{
            f"carry:{rule}": carry_weight * weight
            for rule, weight in carry.weights.items()
        },
    }
    # As in the PST combiner, use the prior scaled rule forecast on missing
    # dates. Carry has already been filled on the execution grid upstream.
    frame = frame.sort("date").with_columns(
        pl.any_horizontal(
            pl.col(column).is_null() | ~pl.col(column).is_finite()
            for column in trend_columns.values()
        ).alias("trend_forecast_missing"),
    )
    filled_columns = []
    stale_flags = []
    for column in trend_columns.values():
        finite = pl.col(column).is_finite()
        filled = pl.when(finite).then(pl.col(column)).forward_fill()
        if max_stale_days is not None:
            last_date = pl.when(finite).then(pl.col("date")).forward_fill()
            stale = (
                (pl.col("date") - last_date).dt.total_days() > max_stale_days
            ).fill_null(False)
            stale_flags.append(stale.alias(f"stale_{column}"))
            filled = pl.when(~stale).then(filled)
        filled_columns.append(filled.alias(column))
    frame = frame.with_columns(*filled_columns, *stale_flags)
    if stale_flags:
        frame = frame.with_columns(
            pl.any_horizontal(
                f"stale_{column}" for column in trend_columns.values()
            ).alias("trend_stale"),
        )
    component_weights = {
        column: final_weights[f"{family}:{rule}"]
        for family, columns in (("trend", trend_columns), ("carry", carry_columns))
        for rule, column in columns.items()
    }
    available_weights = {
        column: pl.when(pl.col(column).is_finite()).then(weight).otherwise(0.0)
        for column, weight in component_weights.items()
    }
    available_total = pl.sum_horizontal(list(available_weights.values()))
    forecast_valid = available_total > 0
    if max_stale_days is not None:
        # A zero combined target can flatten an existing position. Component
        # forecasts and weights still show that no rule supplied the target.
        expired_rules = [
            pl.col(f"stale_{column}")
            for column in trend_columns.values() if component_weights[column] > 0
        ]
        if carry_weight > 0:
            expired_rules.append(pl.col("carry_stale").fill_null(False))
        forecast_valid |= pl.any_horizontal(expired_rules)
    active_rule_count = sum(weight > 0 for weight in component_weights.values())
    fdm = aft_final_rule_fdm(active_rule_count)
    frame = frame.with_columns(
        forecast_valid.alias("forecast_valid"),
        pl.lit(trend.fdm).alias("trend_fdm"),
        pl.lit(carry.fdm).alias("carry_fdm"),
        pl.lit(fdm).alias("forecast_fdm"),
        pl.lit(forecast_cap).alias("forecast_cap"),
        *(
            pl.when(available_total > 0)
            .then(weight / available_total)
            .otherwise(0.0)
            .alias(f"wt_{column}")
            for column, weight in available_weights.items()
        ),
    ).with_columns(
        pl.sum_horizontal([f"wt_{column}" for column in trend_columns.values()])
        .alias("trend_family_weight"),
        pl.sum_horizontal([f"wt_{column}" for column in carry_columns.values()])
        .alias("carry_family_weight"),
        pl.when(pl.col("forecast_valid"))
        .then(
            (
                pl.sum_horizontal([
                    pl.when(pl.col(f"wt_{column}") > 0)
                    .then(pl.col(column) * pl.col(f"wt_{column}"))
                    .otherwise(0.0)
                    for column in component_weights
                ]) * fdm
            ).clip(-forecast_cap, forecast_cap)
        )
        .otherwise(None)
        .alias("combined_forecast")
    )
    if stale_flags:
        # Report the trend-only blend from the same effective EWMAC inputs.
        trend_rule_weights = {
            column: pl.when(pl.col(column).is_finite())
            .then(trend.weights[rule]).otherwise(0.0)
            for rule, column in trend_columns.items()
        }
        trend_available = pl.sum_horizontal(
            trend_rule_weights.values()
        )
        trend_sum = pl.sum_horizontal(
            pl.col(column).fill_null(0.0) * weight
            for column, weight in trend_rule_weights.items()
        )
        frame = frame.with_columns(
            (trend_available > 0).alias("trend_forecast_valid"),
            pl.when(trend_available > 0)
            .then(
                (trend_sum / trend_available * trend.fdm).clip(
                    -forecast_cap, forecast_cap
                )
            )
            .alias("trend_family_forecast"),
        )
    valid = frame.filter(pl.col("forecast_valid"))
    audit: dict[str, object] = {
        "history_obs": frame.height,
        "forecast_valid_obs": valid.height,
        "forecast_invalid_obs": frame.height - valid.height,
        "forecast_start": valid.get_column("date").min() if valid.height else None,
        "forecast_end": valid.get_column("date").max() if valid.height else None,
        "trend_family_weight": trend_weight,
        "carry_family_weight": carry_weight,
        "trend_forecast_elig": bool(trend.audit.get("forecast_elig", True)),
        "carry_forecast_elig": bool(carry.audit.get("forecast_elig", True)),
        "final_rule_n": active_rule_count,
        "combined_nulls": frame.get_column("combined_forecast").null_count(),
        "combined_nonfinite": frame.filter(
            pl.col("combined_forecast").is_not_null()
            & ~pl.col("combined_forecast").is_finite()
        ).height,
    }
    audit["forecast_elig"] = bool(
        valid.height >= min_valid_observations
        and audit["combined_nonfinite"] == 0
    )
    audit["forecast_excl"] = (
        "nonfinite_combined_forecast"
        if audit["combined_nonfinite"]
        else "insufficient_valid_forecast_obs"
        if not audit["forecast_elig"]
        else ""
    )
    return CombinedForecastResult(frame, final_weights, fdm, audit)

"""Build pysystemtrade-compatible futures carry forecasts in Polars.

The source-neutral history layer supplies same-session prices for a currently
held contract and a second dated contract.  This module annualises that curve
slope, divides it by annualised point volatility, smooths four carry rules,
and combines the resulting forecasts on the repository's divided-by-ten
Carver scale.  It also provides the AFTS final-rule-count diversification
table and the default 60/40 trend/carry combination used by strategy eleven.

No function fills a missing carry observation or joins legs from different
dates.  Smoothing restarts at source changes, invalid observations, and long
calendar gaps so an unexplained stitch gap cannot silently become history for
the next provider segment.
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
CARRY_SMOOTH_COMS = (10, 30, 60, 125)
CARRY_FORECAST_SCALAR = 3.0
CARRY_FORECAST_CAP = 2.0
CARRY_MAX_SMOOTHING_GAP_DAYS = 7
CARRY_MAX_STALE_DAYS = 7
DEFAULT_TREND_FAMILY_WEIGHT = 0.60
DEFAULT_CARRY_FAMILY_WEIGHT = 0.40

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
    ``point_vol_column``.  The result retains every observed carry row and
    adds reproducible inputs, a validity flag, and an explicit exclusion
    reason.  Zero price differentials are invalid, matching pysystemtrade's
    conversion of zero raw roll to missing.
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
            (pl.col("carry_con_yr") - pl.col("current_con_yr")).alias(
                "con_yr_frac"
            ),
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


def _carry_segment_expression(max_gap_days: int, has_source: bool) -> pl.Expr:
    """Return a cumulative segment ID for safe carry EWMA ownership."""
    gap_break = (
        pl.col("date").diff().dt.total_days().gt(max_gap_days).fill_null(True)
    )
    invalid_break = (
        (~pl.col("carry_valid"))
        | (~pl.col("carry_valid").shift(1).fill_null(False))
    )
    source_break = (
        (pl.col("source_segment") != pl.col("source_segment").shift(1))
        .fill_null(True)
        if has_source
        else pl.lit(False)
    )
    return (gap_break | invalid_break | source_break).cast(pl.UInt32).cum_sum()


def build_carry_components(
    raw_carry_frame: pl.DataFrame,
    *,
    smooth_coms: Iterable[int] = CARRY_SMOOTH_COMS,
    forecast_scalar: float = CARRY_FORECAST_SCALAR,
    forecast_cap: float = CARRY_FORECAST_CAP,
    max_gap_days: int = CARRY_MAX_SMOOTHING_GAP_DAYS,
) -> CombinedForecastResult:
    """Smooth, scale, equally weight, and diversify carry forecasts.

    Smoothing uses ``com=N, adjust=True``, exactly matching PST's positional
    ``Series.ewm(N).mean()`` call rather than interpreting ``N`` as a span.
    Every component uses the same fixed scalar; on this project's normalized
    forecast convention PST's approximately 30 scalar is ``3.0``.  The final
    FDM comes from the AFTS final-rule-count table.
    """
    _validate_daily_frame(
        raw_carry_frame,
        {"date", "raw_carry", "carry_valid"},
        "raw carry frame",
    )
    rules = tuple(int(value) for value in smooth_coms)
    if not rules or any(value <= 0 for value in rules):
        raise ValueError("carry smoothing COMs must be positive")
    if len(set(rules)) != len(rules):
        raise ValueError("carry smoothing COMs must not contain duplicates")
    if not math.isfinite(forecast_scalar) or forecast_scalar <= 0:
        raise ValueError("forecast_scalar must be finite and positive")
    if not math.isfinite(forecast_cap) or forecast_cap <= 0:
        raise ValueError("forecast_cap must be finite and positive")
    if max_gap_days < 1:
        raise ValueError("max_gap_days must be positive")

    frame = raw_carry_frame.sort("date").with_columns(
        _carry_segment_expression(
            max_gap_days,
            "source_segment" in raw_carry_frame.columns,
        ).alias("carry_segment")
    )
    component_columns: list[str] = []
    for smooth_com in rules:
        smooth_column = f"smooth_carry_{smooth_com}"
        forecast_column = f"fcst_carry_{smooth_com}"
        frame = frame.with_columns(
            pl.col("raw_carry")
            .ewm_mean(com=smooth_com, adjust=True, min_samples=1)
            .over("carry_segment")
            .alias(smooth_column)
        ).with_columns(
            pl.when(pl.col("carry_valid"))
            .then(
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
    frame = frame.with_columns(component_valid.alias("forecast_valid")).with_columns(
        pl.when(pl.col("forecast_valid"))
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
        "carry_smooth_coms": rules,
        "carry_forecast_scalar": forecast_scalar,
        "carry_forecast_cap": forecast_cap,
        "carry_max_gap_days": max_gap_days,
        "carry_segment_n": frame.get_column("carry_segment").n_unique(),
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
    smooth_coms: Iterable[int] = CARRY_SMOOTH_COMS,
    forecast_scalar: float = CARRY_FORECAST_SCALAR,
    forecast_cap: float = CARRY_FORECAST_CAP,
    max_gap_days: int = CARRY_MAX_SMOOTHING_GAP_DAYS,
    max_stale_days: int = CARRY_MAX_STALE_DAYS,
    min_valid_observations: int = 256,
) -> CombinedForecastResult:
    """Return a selection-compatible carry forecast for one futures history.

    Carry is calculated only on observed same-session curve rows.  The most
    recent forecast may persist for at most ``max_stale_days`` calendar days
    when the executable current contract is unchanged.  The observation date
    and staleness are retained, so this bounded PST-style persistence is
    explicit rather than an unbounded or silent forward fill.
    """
    if min_valid_observations < 2:
        raise ValueError("min_valid_observations must be at least 2")
    if max_stale_days < 0:
        raise ValueError("max_stale_days must not be negative")
    execution = _history_execution_frame(history)
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
        smooth_coms=smooth_coms,
        forecast_scalar=forecast_scalar,
        forecast_cap=forecast_cap,
        max_gap_days=max_gap_days,
    )
    carry_owned = components.frame.drop("mixed_point_vol").rename(
        {
            "date": "carry_obs_date",
            "forecast_valid": "carry_obs_valid",
        }
    )
    # The tolerance makes forecast persistence auditable and bounded.  A
    # current-contract equality check below prevents carrying a curve pair
    # through an executable roll even inside the tolerance window.
    frame = execution.join_asof(
        carry_owned.sort("carry_obs_date"),
        left_on="date",
        right_on="carry_obs_date",
        strategy="backward",
        tolerance=f"{max_stale_days}d",
    ).with_columns(
        (pl.col("date") - pl.col("carry_obs_date"))
        .dt.total_days()
        .alias("carry_stale_days")
    )
    execution_valid = (
        pl.col("carry_obs_valid").fill_null(False)
        & (pl.col("mark_contract") == pl.col("current_contract"))
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
    audit = dict(components.audit)
    audit.update(
        {
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
            "carry_max_stale_days": max_stale_days,
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
        and audit["forecast_post_warmup_invalid_obs"] == 0
        and audit["combined_nonfinite"] == 0
    )
    if valid.height < min_valid_observations:
        audit["forecast_excl"] = "insufficient_valid_forecast_obs"
    elif audit["forecast_post_warmup_invalid_obs"]:
        audit["forecast_excl"] = "carry_gaps_after_warmup"
    elif audit["combined_nonfinite"]:
        audit["forecast_excl"] = "nonfinite_combined_forecast"
    else:
        audit["forecast_excl"] = ""
    return CombinedForecastResult(frame, components.weights, components.fdm, audit)


def combine_trend_and_carry_forecasts(
    trend: CombinedForecastResult,
    carry: CombinedForecastResult,
    *,
    trend_weight: float = DEFAULT_TREND_FAMILY_WEIGHT,
    carry_weight: float = DEFAULT_CARRY_FAMILY_WEIGHT,
    forecast_cap: float = CARRY_FORECAST_CAP,
    min_valid_observations: int = 256,
) -> CombinedForecastResult:
    """Combine EWMAC trend and carry components with top-down family weights.

    The function reconstructs each family before its family-only FDM, applies
    60% to the caller's EWMAC rule weights and 40% equally across carry rules,
    then applies one AFTS FDM based on the total final component count.  This
    avoids double-counting the carry-only and trend-only diversification
    multipliers.
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

    carry_owned = [
        "date",
        *carry_columns.values(),
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
    weighted_components = [
        pl.col(trend_columns[rule]) * final_weights[f"trend:{rule}"]
        for rule in trend.weights
    ] + [
        pl.col(carry_columns[rule]) * final_weights[f"carry:{rule}"]
        for rule in carry.weights
    ]
    component_columns = list(trend_columns.values()) + list(carry_columns.values())
    valid_expr = pl.all_horizontal(
        [
            pl.col(column).is_not_null() & pl.col(column).is_finite()
            for column in component_columns
        ]
    )
    fdm = aft_final_rule_fdm(len(final_weights))
    frame = frame.with_columns(valid_expr.alias("forecast_valid")).with_columns(
        pl.when(pl.col("forecast_valid"))
        .then(
            (pl.sum_horizontal(weighted_components) * fdm).clip(
                -forecast_cap,
                forecast_cap,
            )
        )
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
        "trend_family_weight": trend_weight,
        "carry_family_weight": carry_weight,
        "final_rule_n": len(final_weights),
        "combined_nulls": frame.get_column("combined_forecast").null_count(),
        "combined_nonfinite": frame.filter(
            pl.col("combined_forecast").is_not_null()
            & ~pl.col("combined_forecast").is_finite()
        ).height,
    }
    audit["forecast_elig"] = bool(
        valid.height >= min_valid_observations and audit["combined_nonfinite"] == 0
    )
    audit["forecast_excl"] = (
        ""
        if audit["forecast_elig"]
        else (
            "insufficient_valid_forecast_obs"
            if valid.height < min_valid_observations
            else "nonfinite_combined_forecast"
        )
    )
    return CombinedForecastResult(frame, final_weights, fdm, audit)

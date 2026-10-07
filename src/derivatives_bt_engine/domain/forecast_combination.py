"""Combine cost-eligible EWMAC forecasts and audit the resulting subsystem."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
import polars as pl

from derivatives_bt_engine.domain.signal import (
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
)
from derivatives_bt_engine.domain.volatility import CARVER_BUSINESS_DAYS_PER_YEAR


@dataclass(frozen=True)
class EwmacRule:
    """One canonical EWMAC speed and its stable report key."""

    fast: int
    slow: int

    @property
    def key(self) -> str:
        return f"{self.fast}/{self.slow}"

    @property
    def column(self) -> str:
        return f"fcst_{self.fast}_{self.slow}"


CANONICAL_EWMAC_RULES = tuple(
    EwmacRule(fast, fast * 4) for fast in (4, 8, 16, 32, 64)
)
EWMAC_RULE_BY_KEY = {rule.key: rule for rule in CANONICAL_EWMAC_RULES}


class ForecastWeightPolicy(ABC):
    """Configurable rule-weight policy used before forecast diversification."""

    @abstractmethod
    def weights(self, eligible_rules: Iterable[str]) -> dict[str, float]:
        """Return normalized weights for the supplied rule keys."""


@dataclass(frozen=True)
class SlowTiltEwmacWeights(ForecastWeightPolicy):
    """Allocate 60% to the two slowest and 40% to the faster three rules."""

    template: Mapping[str, float] | None = None

    def weights(self, eligible_rules: Iterable[str]) -> dict[str, float]:
        base = dict(self.template or {
            "4/16": 0.05,
            "8/32": 0.15,
            "16/64": 0.20,
            "32/128": 0.30,
            "64/256": 0.30,
        })
        rules = [str(rule).strip() for rule in eligible_rules if str(rule).strip()]
        if not rules:
            raise ValueError("at least one eligible EWMAC rule is required")
        if len(set(rules)) != len(rules):
            raise ValueError("eligible EWMAC rules must not contain duplicates")
        unsupported = sorted(set(rules).difference(base))
        if unsupported:
            raise ValueError(f"unsupported EWMAC rules: {', '.join(unsupported)}")
        total = sum(base[rule] for rule in rules)
        if not math.isfinite(total) or total <= 0:
            raise ValueError("eligible EWMAC rule weights must have positive mass")
        return {
            rule: base[rule] / total
            for rule in base
            if rule in rules
        }


class ForecastDiversificationPolicy(ABC):
    """Policy boundary for the forecast diversification multiplier (FDM)."""

    @abstractmethod
    def multiplier(
        self,
        rules: list[str],
        weights: Mapping[str, float],
        correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> float:
        """Return the multiplier for one active rule set."""


@dataclass(frozen=True)
class CorrelationForecastDiversification(ForecastDiversificationPolicy):
    """Use ``1/sqrt(w' C w)`` after flooring negative correlations at zero."""

    cap: float = 2.5

    def multiplier(
        self,
        rules: list[str],
        weights: Mapping[str, float],
        correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> float:
        if self.cap <= 0:
            raise ValueError("FDM cap must be positive")
        indices = [correlation_rules.index(rule) for rule in rules]
        active = correlation[np.ix_(indices, indices)].copy()
        active = np.nan_to_num(active, nan=0.0, posinf=1.0, neginf=0.0)
        active = np.clip(active, 0.0, 1.0)
        np.fill_diagonal(active, 1.0)
        vector = np.array([weights[rule] for rule in rules], dtype=float)
        variance = float(vector @ active @ vector)
        return min(self.cap, 1.0 / math.sqrt(variance)) if variance > 0 else 1.0


@dataclass(frozen=True)
class ForecastCombinationConfig:
    """Parameters governing combined-forecast validity and turnover."""

    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS
    forecast_cap: float = EWMAC_FORECAST_CAP
    annualization_days: int = CARVER_BUSINESS_DAYS_PER_YEAR
    # This is pysystemtrade turnover()'s ``smooth_y_days``: the EWM ``com``
    # applied only to the changing average-position/volatility denominator.
    # It does not smooth the EWMAC forecast or the optimal position.
    average_position_ewm_com: int = 250
    min_valid_observations: int = 256

    def __post_init__(self) -> None:
        if self.target_abs_forecast <= 0 or self.forecast_cap <= 0:
            raise ValueError("forecast target and cap must be positive")
        if self.annualization_days <= 0 or self.average_position_ewm_com <= 0:
            raise ValueError("turnover day parameters must be positive")
        if self.min_valid_observations < 2:
            raise ValueError("min_valid_observations must be at least 2")


@dataclass(frozen=True)
class CombinedForecastResult:
    frame: pl.DataFrame
    weights: dict[str, float]
    fdm: float
    turnover: float | None
    audit: dict[str, object]


class CombinedForecastEngine:
    """Combine rule forecasts, calculate subsystem turnover, and audit gaps."""

    def __init__(
        self,
        *,
        weight_policy: ForecastWeightPolicy | None = None,
        diversification_policy: ForecastDiversificationPolicy | None = None,
        config: ForecastCombinationConfig | None = None,
    ) -> None:
        self.weight_policy = weight_policy or SlowTiltEwmacWeights()
        self.diversification_policy = (
            diversification_policy or CorrelationForecastDiversification()
        )
        self.config = config or ForecastCombinationConfig()

    def combine(
        self,
        frame: pl.DataFrame,
        eligible_rules: Iterable[str],
        forecast_correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> CombinedForecastResult:
        """Return a combined forecast and the turnover of its actual position.

        Warm-up nulls are retained in the audit.  A row is usable only when
        every active component, point volatility, and the point change are
        finite; this prevents a partially missing weighted sum from silently
        changing the strategy represented by the row.
        """
        rules = [str(rule).strip() for rule in eligible_rules if str(rule).strip()]
        weights = self.weight_policy.weights(rules)
        required = {"ts_event", "point_vol", "pt_change_1d"}
        required.update(EWMAC_RULE_BY_KEY[rule].column for rule in weights)
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"combined forecast input missing columns: {missing}")

        fdm = self.diversification_policy.multiplier(
            list(weights), weights, forecast_correlation, correlation_rules
        )
        component_columns = [EWMAC_RULE_BY_KEY[rule].column for rule in weights]
        valid_expr = (
            pl.col("point_vol").is_not_null()
            & pl.col("point_vol").is_finite()
            & (pl.col("point_vol") > 0)
            & pl.col("pt_change_1d").is_not_null()
            & pl.col("pt_change_1d").is_finite()
        )
        for column in component_columns:
            valid_expr &= pl.col(column).is_not_null() & pl.col(column).is_finite()

        weighted = pl.sum_horizontal([
            pl.col(EWMAC_RULE_BY_KEY[rule].column) * weight
            for rule, weight in weights.items()
        ])
        result = (
            frame.sort("ts_event")
            .with_columns(valid_expr.alias("forecast_valid"))
            .with_columns(
                pl.when(pl.col("forecast_valid"))
                .then((weighted * fdm).clip(
                    -self.config.forecast_cap, self.config.forecast_cap
                ))
                .otherwise(None)
                .alias("combined_forecast")
            )
            .with_columns(
                pl.when(pl.col("forecast_valid"))
                .then(1.0 / pl.col("point_vol"))
                .otherwise(None)
                .alias("avg_position"),
            )
            .with_columns(
                (
                    pl.col("avg_position")
                    * pl.col("combined_forecast")
                    / self.config.target_abs_forecast
                ).alias("subsystem_position"),
                pl.col("avg_position")
                .ewm_mean(
                    com=self.config.average_position_ewm_com,
                    adjust=True,
                    min_samples=2,
                )
                .alias("smooth_avg_position"),
            )
            .with_columns(
                pl.when(pl.col("smooth_avg_position") > 0)
                .then(pl.col("subsystem_position") / pl.col("smooth_avg_position"))
                .otherwise(None)
                .alias("normalized_position"),
                (
                    pl.col("subsystem_position").shift(1)
                    * pl.col("pt_change_1d")
                ).alias("subsystem_return"),
            )
        )
        changes = result.get_column("normalized_position").drop_nulls().diff().abs()
        changes = changes.filter(changes.is_not_null() & changes.is_finite())
        turnover = (
            float(changes.mean() * self.config.annualization_days)
            if changes.len()
            else None
        )

        valid = result.filter(pl.col("forecast_valid"))
        first_valid = valid.get_column("ts_event").min() if valid.height else None
        after_warmup = (
            result.filter(pl.col("ts_event") >= first_valid)
            if first_valid is not None
            else result
        )
        audit: dict[str, object] = {
            "history_obs": result.height,
            "forecast_valid_obs": valid.height,
            "forecast_invalid_obs": result.height - valid.height,
            "forecast_post_warmup_invalid_obs": (
                after_warmup.filter(~pl.col("forecast_valid")).height
            ),
            "forecast_start": first_valid,
            "forecast_end": valid.get_column("ts_event").max() if valid.height else None,
            "combined_nulls": result.get_column("combined_forecast").null_count(),
            "combined_nonfinite": result.filter(
                pl.col("combined_forecast").is_not_null()
                & ~pl.col("combined_forecast").is_finite()
            ).height,
        }
        for rule in weights:
            column = EWMAC_RULE_BY_KEY[rule].column
            audit[f"{column}_nulls"] = result.get_column(column).null_count()
            audit[f"{column}_nonfinite"] = result.filter(
                pl.col(column).is_not_null() & ~pl.col(column).is_finite()
            ).height
        audit["forecast_elig"] = bool(
            valid.height >= self.config.min_valid_observations
            and audit["forecast_post_warmup_invalid_obs"] == 0
            and audit["combined_nonfinite"] == 0
            and turnover is not None
            and math.isfinite(turnover)
        )
        if valid.height < self.config.min_valid_observations:
            audit["forecast_excl"] = "insufficient_valid_forecast_obs"
        elif audit["forecast_post_warmup_invalid_obs"]:
            audit["forecast_excl"] = "forecast_gaps_after_warmup"
        elif audit["combined_nonfinite"]:
            audit["forecast_excl"] = "nonfinite_combined_forecast"
        elif turnover is None or not math.isfinite(turnover):
            audit["forecast_excl"] = "invalid_combined_turnover"
        else:
            audit["forecast_excl"] = ""
        return CombinedForecastResult(result, weights, fdm, turnover, audit)


def median_forecast_correlation(
    frames: Iterable[pl.DataFrame],
    *,
    rules: tuple[EwmacRule, ...] = CANONICAL_EWMAC_RULES,
    min_observations: int = 256,
) -> tuple[np.ndarray, list[str]]:
    """Estimate a robust pooled component-forecast correlation matrix."""
    matrices: list[np.ndarray] = []
    columns = [rule.column for rule in rules]
    for frame in frames:
        if not set(columns).issubset(frame.columns):
            continue
        usable = frame.select(columns).drop_nulls()
        usable = usable.filter(pl.all_horizontal(pl.all().is_finite()))
        if usable.height < min_observations:
            continue
        matrix = np.corrcoef(usable.to_numpy(), rowvar=False)
        if np.isfinite(matrix).all():
            matrices.append(matrix)
    if not matrices:
        return np.eye(len(rules)), [rule.key for rule in rules]
    correlation = np.median(np.stack(matrices), axis=0)
    correlation = np.nan_to_num(correlation, nan=0.0, posinf=1.0, neginf=-1.0)
    correlation = np.clip(correlation, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)
    return correlation, [rule.key for rule in rules]

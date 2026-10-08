"""Turn one instrument history into an auditable combined EWMAC subsystem.

The input is a single daily signal-history dataframe containing the five
already-scaled EWMAC component forecasts. The engine selects the rules that
passed the executable contract's Phase 1 cost gate, combines them, converts the
forecast to a volatility-scaled position, measures turnover, and returns both
the daily calculations and a compact validity audit.

Forecast diversification and instrument diversification are intentionally
separate. This module calculates FDM across component rules for one history;
the Phase 2 selector later calculates IDM across executable instruments.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
import polars as pl

from derivatives_bt_engine.domain.ewmac import (
    CANONICAL_EWMAC_RULES,
    EWMAC_FORECAST_CAP,
    EWMAC_SCALAR_MIN_PERIODS,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_RULE_BY_KEY,
    EwmacRule,
)
from derivatives_bt_engine.domain.ewmac_components import (
    build_ewmac_components,
)
from derivatives_bt_engine.domain.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
    MIXED_VOL_FAST_SPAN,
    MIXED_VOL_MIN_SAMPLES,
    MIXED_VOL_SLOW_WEIGHT,
    MIXED_VOL_SLOW_YEARS,
)


class ForecastWeightPolicy(ABC):
    """Choose pre-FDM weights for an executable symbol's eligible rules."""

    @abstractmethod
    def weights(self, eligible_rules: Iterable[str]) -> dict[str, float]:
        """Return normalized weights for the supplied rule keys."""


@dataclass(frozen=True)
class SlowTiltEwmacWeights(ForecastWeightPolicy):
    """Allocate 60% to the two slowest and 40% to the faster three rules.

    ``template`` optionally replaces the default five-rule allocation. Rules
    absent from ``eligible_rules`` receive zero and surviving weights are
    renormalized to one.
    """

    template: Mapping[str, float] | None = None

    def weights(self, eligible_rules: Iterable[str]) -> dict[str, float]:
        """Validate rule keys and return their renormalized template weights."""
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
    """Calculate FDM for a weighted subset of component forecasts."""

    @abstractmethod
    def multiplier(
        self,
        rules: list[str],
        weights: Mapping[str, float],
        correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> float:
        """Return FDM using the active rules' block of a larger matrix.

        ``correlation`` is ordered by ``correlation_rules``; ``rules`` selects
        and orders the block needed for this executable symbol.
        """


@dataclass(frozen=True)
class CorrelationForecastDiversification(ForecastDiversificationPolicy):
    """Use ``1/sqrt(w' C w)`` after flooring negative correlations at zero.

    ``cap`` limits leverage from forecast diversification; the default 2.5 is
    applied after extracting the active rule-correlation block.
    """

    cap: float = 2.5

    def multiplier(
        self,
        rules: list[str],
        weights: Mapping[str, float],
        correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> float:
        """Calculate capped FDM for ``rules`` in their supplied weight order."""
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
class FixedForecastDiversification(ForecastDiversificationPolicy):
    """Return one fixed FDM independently of rule correlations.

    ``value=1.0`` disables forecast diversification while retaining the same
    combination and audit path used by the portfolio research pipeline.  A
    fixed policy is preferable to manufacturing a correlation matrix merely
    to force a desired multiplier.
    """

    value: float = 1.0

    def __post_init__(self) -> None:
        """Require a finite, positive forecast diversification multiplier."""
        if not math.isfinite(self.value) or self.value <= 0:
            raise ValueError("fixed FDM must be finite and positive")

    def multiplier(
        self,
        rules: list[str],
        weights: Mapping[str, float],
        correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> float:
        """Return the configured multiplier; other arguments are unused."""
        return self.value


@dataclass(frozen=True)
class ForecastCombinationConfig:
    """Configure combined-forecast scaling, turnover, and validity gates.

    Forecasts use the project's normalized Carver convention: target average
    absolute forecast 0.5 and cap 1.0 correspond to conventional 10 and 20.
    ``average_position_ewm_com`` is the pandas-compatible EWM ``com`` applied
    only to the average-position denominator in turnover. It never smooths
    the forecast. ``min_valid_observations`` excludes short histories.
    """

    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS
    forecast_cap: float = EWMAC_FORECAST_CAP
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS
    # This is pysystemtrade turnover()'s ``smooth_y_days``: the EWM ``com``
    # applied only to the changing average-position/volatility denominator.
    # It does not smooth the EWMAC forecast or the optimal position.
    average_position_ewm_com: int = 250
    min_valid_observations: int = 256

    def __post_init__(self) -> None:
        """Reject nonsensical forecast, turnover, and history parameters."""
        if self.target_abs_forecast <= 0 or self.forecast_cap <= 0:
            raise ValueError("forecast target and cap must be positive")
        if self.annualization_days <= 0 or self.average_position_ewm_com <= 0:
            raise ValueError("turnover day parameters must be positive")
        if self.min_valid_observations < 2:
            raise ValueError("min_valid_observations must be at least 2")


@dataclass(frozen=True)
class CombinedForecastResult:
    """Hold one executable symbol's daily subsystem and summary diagnostics.

    ``frame`` contains component forecasts, the combined forecast, its signed
    multiple of the target-average forecast, average and optimal position
    proxies, normalized position, and subsystem return. ``weights`` and
    ``fdm`` explain the combination; ``turnover`` is annualized; ``audit``
    contains null/non-finite counts and the eligibility decision.
    """

    frame: pl.DataFrame
    weights: dict[str, float]
    fdm: float
    turnover: float | None
    audit: dict[str, object]


class CombinedForecastEngine:
    """Combine rule forecasts, calculate subsystem turnover, and audit gaps.

    The three constructor policies make forecast weighting, FDM, and numeric
    conventions replaceable without changing the Phase 2 orchestration code.
    """

    def __init__(
        self,
        *,
        weight_policy: ForecastWeightPolicy | None = None,
        diversification_policy: ForecastDiversificationPolicy | None = None,
        config: ForecastCombinationConfig | None = None,
    ) -> None:
        """Initialize the engine with supplied policies or project defaults."""
        self.weight_policy = weight_policy or SlowTiltEwmacWeights()
        self.diversification_policy = (
            diversification_policy or CorrelationForecastDiversification()
        )
        self.config = config or ForecastCombinationConfig()

    def combine(
        self,
        history_frame: pl.DataFrame,
        eligible_rules: Iterable[str],
        forecast_correlation: np.ndarray,
        correlation_rules: list[str],
    ) -> CombinedForecastResult:
        """Return a combined forecast and its unbuffered subsystem turnover.

        Parameters
        ----------
        history_frame
            One signal history with ``date``, ``point_change``, one point-
            volatility column, and a ``fcst_<fast>_<slow>`` column for every
            eligible rule. ``mixed_point_vol`` is preferred; legacy compact
            Phase 2 frames containing ``point_vol`` remain supported. It
            contains one history, not the whole universe.
        eligible_rules
            Rule keys retained for this executable symbol by Phase 1 costs.
        forecast_correlation
            Pooled component-forecast correlation matrix.
        correlation_rules
            Rule-key order of ``forecast_correlation``.

        Returns
        -------
        CombinedForecastResult
            The daily subsystem frame, applied weights and FDM, annualized
            turnover, and forecast-data audit.

        Warm-up nulls are retained in the audit.  A row is usable only when
        every active component, point volatility, and the point change are
        finite; this prevents a partially missing weighted sum from silently
        changing the strategy represented by the row.
        """
        rules = [str(rule).strip() for rule in eligible_rules if str(rule).strip()]
        weights = self.weight_policy.weights(rules)
        if "mixed_point_vol" in history_frame.columns:
            point_vol_column = "mixed_point_vol"
        elif "point_vol" in history_frame.columns:
            # Phase 2's compact pooled-history frame predates the descriptive
            # mixed-volatility name. It carries the same blended estimator.
            point_vol_column = "point_vol"
        else:
            raise ValueError(
                "combined forecast input missing point volatility; expected "
                "'mixed_point_vol' or legacy 'point_vol'"
            )

        required = {"date", "point_change", point_vol_column}
        required.update(EWMAC_RULE_BY_KEY[rule].column for rule in weights)
        missing = sorted(required.difference(history_frame.columns))
        if missing:
            raise ValueError(f"combined forecast input missing columns: {missing}")

        fdm = self.diversification_policy.multiplier(
            list(weights), weights, forecast_correlation, correlation_rules
        )
        component_columns = [EWMAC_RULE_BY_KEY[rule].column for rule in weights]
        valid_expr = (
            pl.col(point_vol_column).is_not_null()
            & pl.col(point_vol_column).is_finite()
            & (pl.col(point_vol_column) > 0)
            & pl.col("point_change").is_not_null()
            & pl.col("point_change").is_finite()
        )
        for column in component_columns:
            valid_expr &= pl.col(column).is_not_null() & pl.col(column).is_finite()

        # Every component forecast is already causally scaled and capped.
        # Weighting happens before FDM; the diversified result is capped again.
        weighted = pl.sum_horizontal([
            pl.col(EWMAC_RULE_BY_KEY[rule].column) * weight
            for rule, weight in weights.items()
        ])
        subsystem_frame = (
            history_frame.sort("date")
            .with_columns(valid_expr.alias("forecast_valid"))
            .with_columns(
                # Combine the active components in forecast units, apply FDM,
                # and enforce the same cap used by each component.
                pl.when(pl.col("forecast_valid"))
                .then((weighted * fdm).clip(
                    -self.config.forecast_cap, self.config.forecast_cap
                ))
                .otherwise(None)
                .alias("combined_forecast")
            )
            .with_columns(
                # Express the combined signal as a signed multiple of the
                # target-average position. With our 0.5 target, forecasts of
                # +0.5 and +1.0 mean +1x and +2x average position; negative
                # values represent short positions of the same magnitude.
                (
                    pl.col("combined_forecast")
                    / self.config.target_abs_forecast
                ).alias("forecast_multiplier"),
                # Inverse daily point volatility has the same relative path as
                # the properly risk-sized number of contracts or shares. This
                # is deliberately a position proxy, not an executable count:
                # capital, risk target, annualization, contract multiplier,
                # and constant FX terms are omitted because they cancel when
                # turnover is expressed in average-position units.
                pl.when(pl.col("forecast_valid"))
                .then(1.0 / pl.col(point_vol_column))
                .otherwise(None)
                .alias("avg_position"),
            )
            .with_columns(
                # The unbuffered optimal position is the average risk-sized
                # position multiplied by current forecast conviction. It has
                # the same arbitrary contract/share scale as avg_position.
                (
                    pl.col("avg_position")
                    * pl.col("forecast_multiplier")
                ).alias("subsystem_position"),
                # Volatility changes make avg_position move even with a flat
                # forecast. Carver smooths this denominator so daily volatility
                # estimation noise does not redefine one unit of turnover.
                pl.col("avg_position")
                .ewm_mean(
                    com=self.config.average_position_ewm_com,
                    adjust=True,
                    min_samples=2,
                )
                .alias("smooth_avg_position"),
            )
            .with_columns(
                # This dimensionless series says how many smoothed average-
                # position units the strategy currently wants. Turnover is the
                # annualized mean absolute daily change in this series.
                pl.when(pl.col("smooth_avg_position") > 0)
                .then(pl.col("subsystem_position") / pl.col("smooth_avg_position"))
                .otherwise(None)
                .alias("normalized_position"),
                # Lag the position so today's price change earns P&L on the
                # position known at yesterday's close. Values remain in proxy
                # P&L units because account-sizing constants were omitted.
                (
                    pl.col("subsystem_position").shift(1)
                    * pl.col("point_change")
                ).alias("subsystem_return"),
            )
        )
        # Each absolute change is a fraction of an average risk-sized position;
        # multiplying its daily mean by the trading-day count annualizes it.
        changes = (
            subsystem_frame.get_column("normalized_position")
            .drop_nulls()
            .diff()
            .abs()
        )
        changes = changes.filter(changes.is_not_null() & changes.is_finite())
        turnover = (
            float(changes.mean() * self.config.annualization_days)
            if changes.len()
            else None
        )

        valid = subsystem_frame.filter(pl.col("forecast_valid"))
        first_valid = valid.get_column("date").min() if valid.height else None
        after_warmup = (
            subsystem_frame.filter(pl.col("date") >= first_valid)
            if first_valid is not None
            else subsystem_frame
        )
        audit: dict[str, object] = {
            "history_obs": subsystem_frame.height,
            "forecast_valid_obs": valid.height,
            "forecast_invalid_obs": subsystem_frame.height - valid.height,
            "forecast_post_warmup_invalid_obs": (
                after_warmup.filter(~pl.col("forecast_valid")).height
            ),
            "forecast_start": first_valid,
            "forecast_end": valid.get_column("date").max() if valid.height else None,
            "combined_nulls": (
                subsystem_frame.get_column("combined_forecast").null_count()
            ),
            "combined_nonfinite": subsystem_frame.filter(
                pl.col("combined_forecast").is_not_null()
                & ~pl.col("combined_forecast").is_finite()
            ).height,
        }
        for rule in weights:
            column = EWMAC_RULE_BY_KEY[rule].column
            audit[f"{column}_nulls"] = (
                subsystem_frame.get_column(column).null_count()
            )
            audit[f"{column}_nonfinite"] = subsystem_frame.filter(
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
        return CombinedForecastResult(
            subsystem_frame, weights, fdm, turnover, audit
        )


def combine_ewmac_forecasts(
    history_frame: pl.DataFrame,
    *,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
    instrument_code: str = "instrument",
    forecast_scalars: Mapping[str, float] | None = None,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    weight_policy: ForecastWeightPolicy | None = None,
    fdm: float = 1.0,
    config: ForecastCombinationConfig | None = None,
    vol_span: int = MIXED_VOL_FAST_SPAN,
    vol_slow_years: int = MIXED_VOL_SLOW_YEARS,
    vol_slow_weight: float = MIXED_VOL_SLOW_WEIGHT,
    vol_min_samples: int = MIXED_VOL_MIN_SAMPLES,
) -> CombinedForecastResult:
    """Build and combine every requested EWMAC speed from one price frame.

    Parameters
    ----------
    history_frame
        One instrument's daily Polars dataframe with ``date`` and ``close``.
        A supplied ``point_change`` is authoritative; otherwise sorted
        ``close.diff()`` is used.  For equities, ``close`` should be adjusted
        for splits so corporate actions do not become false signals.
    rules
        EWMAC speed pairs to calculate and combine.  The five canonical
        ``4/16`` through ``64/256`` rules are used by default.
    instrument_code
        Stable identity used only when causally estimating per-instrument
        forecast scalars.
    forecast_scalars
        Optional fixed scalar by rule key.  When omitted, each rule receives
        a causal scalar estimated solely from this instrument's prior raw
        forecasts.  This is a single-instrument normalization, not pooling.
    scalar_min_periods
        Prior daily observations required before a causal scalar is valid.
    weight_policy
        Pre-FDM forecast weights.  The standard slow-tilted EWMAC weights are
        used when omitted and are renormalized over ``rules``.
    fdm
        Fixed forecast diversification multiplier.  The default ``1.0``
        disables FDM, which is useful for a standalone stock dataframe.
    config
        Forecast cap, turnover, and validity settings for the combined engine.
    vol_span, vol_slow_years, vol_slow_weight, vol_min_samples
        Mixed point-volatility settings shared by every component rule.

    Returns
    -------
    CombinedForecastResult
        The usual combined-forecast result.  Its frame also retains the input
        columns, common volatility diagnostics, and rule-specific EMA, raw
        forecast, scalar, and final component columns for notebook inspection.

    Notes
    -----
    This helper deliberately fixes FDM rather than estimating one from a
    single market.  Cross-rule correlation FDM for portfolio research remains
    the responsibility of :class:`CombinedForecastEngine` with a pooled
    correlation matrix.
    """
    rule_tuple = tuple(rules)
    combination_config = config or ForecastCombinationConfig()
    component_frame = build_ewmac_components(
        history_frame,
        rules=rule_tuple,
        instrument_code=instrument_code,
        forecast_scalars=forecast_scalars,
        scalar_min_periods=scalar_min_periods,
        target_abs_forecast=combination_config.target_abs_forecast,
        forecast_cap=combination_config.forecast_cap,
        annualization_days=combination_config.annualization_days,
        vol_span=vol_span,
        vol_slow_years=vol_slow_years,
        vol_slow_weight=vol_slow_weight,
        vol_min_samples=vol_min_samples,
    )
    rule_keys = [rule.key for rule in rule_tuple]
    engine = CombinedForecastEngine(
        weight_policy=weight_policy,
        diversification_policy=FixedForecastDiversification(fdm),
        config=combination_config,
    )
    # Fixed FDM ignores correlation inputs; these identity placeholders keep
    # the lower-level engine API uniform without implying estimated diversity.
    return engine.combine(
        component_frame,
        rule_keys,
        np.eye(len(rule_keys)),
        rule_keys,
    )


def median_forecast_correlation(
    history_frames: Iterable[pl.DataFrame],
    *,
    rules: tuple[EwmacRule, ...] = CANONICAL_EWMAC_RULES,
    min_observations: int = 256,
) -> tuple[np.ndarray, list[str]]:
    """Estimate a robust pooled correlation matrix across EWMAC components.

    Each member of ``history_frames`` is one distinct instrument history with
    all requested component columns. Correlations are calculated within each
    history so long-lived markets do not dominate short-lived ones, then the
    matrices are combined elementwise by their median.

    Returns the matrix and the rule-key order describing its rows and columns.
    If no history meets ``min_observations``, the conservative fallback is an
    identity matrix rather than invented cross-rule diversification.
    """
    matrices: list[np.ndarray] = []
    columns = [rule.column for rule in rules]
    for history_frame in history_frames:
        if not set(columns).issubset(history_frame.columns):
            continue
        usable = history_frame.select(columns).drop_nulls()
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

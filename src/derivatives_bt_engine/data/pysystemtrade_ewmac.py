"""Reusable pysystemtrade EWMAC loading, construction, and notebook views.

This module is the common boundary between the reviewed pysystemtrade history
database and EWMAC consumers. Phase 1 uses it for rule-cost baselines, Phase 2
uses it for component forecasts, and notebooks use :func:`load_ewmac_research`
to inspect one instrument without importing either report orchestrator.

The notebook facade exposes raw history streams, causal pooled scalar history,
raw EWMAC momentum, volatility-normalized forecasts, scaled/capped forecasts,
and a small plotting helper. It deliberately does not estimate an FDM from a
single instrument because Phase 2's FDM belongs to a pooled forecast-
correlation universe.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from derivatives_bt_engine.data.pysystemtrade_pooling import (
    DEFAULT_POOLING_MAPPING_PATH,
)
from derivatives_bt_engine.domain.forecast_combination import (
    CANONICAL_EWMAC_RULES,
    EwmacRule,
)
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    FuturesHistory,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.ewmac import (
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_SCALAR_MIN_PERIODS,
    ewmac,
)
from derivatives_bt_engine.domain.volatility import (
    CARVER_FAST_VOL_SPAN,
    CARVER_SLOW_VOL_WEIGHT,
    CARVER_SLOW_VOL_YEARS,
    CARVER_VOL_MIN_SAMPLES,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PooledEwmacRuleData:
    """Hold one rule's causal pooled normalization and construction settings.

    ``scalar_history`` is the complete pool-labelled daily normalization frame;
    ``coverage`` describes the instruments used to estimate it. The remaining
    fields preserve the volatility and forecast settings required to reproduce
    the component forecast exactly.
    """

    rule: EwmacRule
    scalar_history: pl.DataFrame
    coverage: pl.DataFrame
    cache_metadata: Mapping[str, object]
    vol_span: int
    vol_slow_years: int
    vol_slow_weight: float
    vol_min_samples: int
    target_abs_forecast: float
    forecast_cap: float

    def global_scalar(self) -> pl.DataFrame:
        """Return the sorted global pool's daily scalar and audit columns."""
        return (
            self.scalar_history.filter(pl.col("pool_key") == "global")
            .sort("ts_event")
        )


def load_pooled_ewmac_rule(
    rule: EwmacRule,
    *,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    vol_span: int = CARVER_FAST_VOL_SPAN,
    vol_slow_years: int = CARVER_SLOW_VOL_YEARS,
    vol_slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
    vol_min_samples: int = CARVER_VOL_MIN_SAMPLES,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    forecast_cap: float = EWMAC_FORECAST_CAP,
) -> PooledEwmacRuleData:
    """Load one rule's reviewed causal global normalization from its cache.

    Parameters are the same EWMAC volatility, scalar, and cap settings used by
    the backtester. The returned scalar remains pool-labelled so notebook users
    can audit it; :meth:`PooledEwmacRuleData.global_scalar` selects the global
    series used by Phase 1 and Phase 2.
    """
    # Keep the full backtester import off the lightweight history/plot import
    # path; normalization calls still reuse its versioned production caches.
    from derivatives_bt_engine.domain.tsmom_backtester import (
        TsmomBacktestConfig,
        load_pysystemtrade_ewmac_normalization,
    )

    config = TsmomBacktestConfig(
        symbols=[],
        data_source="pysystemtrade",
        signal_weighting="carver_ewmac",
        pysystemtrade_db_path=db_path,
        pysystemtrade_pooling_mapping_path=pooling_mapping_path,
        ewmac_fast_span=rule.fast,
        ewmac_slow_span=rule.slow,
        ewmac_vol_span=vol_span,
        ewmac_vol_slow_years=vol_slow_years,
        ewmac_vol_slow_weight=vol_slow_weight,
        ewmac_vol_min_samples=vol_min_samples,
        ewmac_scalar_min_periods=scalar_min_periods,
        ewmac_forecast_target_abs=target_abs_forecast,
        ewmac_forecast_cap=forecast_cap,
    )
    scalar_history, coverage, cache_metadata = (
        load_pysystemtrade_ewmac_normalization(config)
    )
    return PooledEwmacRuleData(
        rule=rule,
        scalar_history=scalar_history,
        coverage=coverage,
        cache_metadata=cache_metadata,
        vol_span=vol_span,
        vol_slow_years=vol_slow_years,
        vol_slow_weight=vol_slow_weight,
        vol_min_samples=vol_min_samples,
        target_abs_forecast=target_abs_forecast,
        forecast_cap=forecast_cap,
    )


def load_pooled_ewmac_rules(
    *,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
    **kwargs,
) -> dict[str, PooledEwmacRuleData]:
    """Load pooled normalization objects keyed by ``fast/slow`` rule name.

    Extra keyword arguments are forwarded to :func:`load_pooled_ewmac_rule`,
    allowing notebooks and reports to change volatility or scalar settings in
    one place while retaining the same output shape.
    """
    return {
        rule.key: load_pooled_ewmac_rule(
            rule,
            db_path=db_path,
            pooling_mapping_path=pooling_mapping_path,
            **kwargs,
        )
        for rule in rules
    }


def build_ewmac_rule_forecast(
    history: FuturesHistory,
    pooled: PooledEwmacRuleData,
    *,
    forecast_column: str | None = None,
) -> pl.DataFrame:
    """Build one detailed, causally scaled EWMAC rule frame.

    ``history`` supplies the additive Panama price. ``pooled`` supplies the
    rule definition, causal global scalar, and construction settings. The
    result retains raw momentum, point volatility, raw forecast, joined scalar,
    and the final scaled/capped forecast. Rows before scalar or volatility
    warm-up remain null and are never backfilled.
    """
    column = forecast_column or pooled.rule.column
    scalar = pooled.global_scalar().select("ts_event", "forecast_scalar")
    return (
        ewmac(
            history.panama_bars(),
            fast_span=pooled.rule.fast,
            slow_span=pooled.rule.slow,
            vol_span=pooled.vol_span,
            vol_slow_years=pooled.vol_slow_years,
            vol_slow_weight=pooled.vol_slow_weight,
            vol_min_samples=pooled.vol_min_samples,
            forecast_scalar=1.0,
            forecast_cap=pooled.forecast_cap,
        )
        .join_asof(scalar, on="ts_event", strategy="backward")
        .with_columns(
            (pl.col("raw_forecast") * pl.col("forecast_scalar"))
            .clip(-pooled.forecast_cap, pooled.forecast_cap)
            .alias(column)
        )
    )


def build_ewmac_component_frame(
    history: FuturesHistory,
    pooled_rules: Mapping[str, PooledEwmacRuleData],
    *,
    detailed: bool = False,
) -> pl.DataFrame:
    """Build a wide component-forecast frame for one instrument history.

    The compact shape contains ``ts_event``, ``point_vol``, ``point_change``,
    and one ``fcst_<fast>_<slow>`` column per rule and is consumed by Phase 2.
    With ``detailed=True`` the frame additionally exposes the Panama price,
    raw EWMAC difference, raw normalized forecast, and daily pooled scalar for
    every rule, using rule-suffixed column names suitable for a notebook.
    """
    if not pooled_rules:
        raise ValueError("at least one pooled EWMAC rule is required")
    combined: pl.DataFrame | None = None
    for key, pooled in pooled_rules.items():
        if key != pooled.rule.key:
            raise ValueError(
                f"pooled rule key {key!r} does not match {pooled.rule.key!r}"
            )
        frame = build_ewmac_rule_forecast(history, pooled)
        suffix = f"{pooled.rule.fast}_{pooled.rule.slow}"
        if combined is None:
            base_columns = [
                "ts_event",
                "point_vol",
                "point_change",
                pooled.rule.column,
            ]
            if detailed:
                base_columns = [
                    "ts_event",
                    "close",
                    "fast_point_vol",
                    "slow_point_vol",
                    "point_vol",
                    "point_change",
                    "quality_flag",
                    pl.col("raw_ewmac").alias(f"raw_ewmac_{suffix}"),
                    pl.col("raw_forecast").alias(f"raw_fcst_{suffix}"),
                    pl.col("forecast_scalar").alias(f"scalar_{suffix}"),
                    pooled.rule.column,
                ]
            combined = frame.select(base_columns)
            continue

        columns: list[str | pl.Expr] = ["ts_event", pooled.rule.column]
        if detailed:
            columns.extend([
                pl.col("raw_ewmac").alias(f"raw_ewmac_{suffix}"),
                pl.col("raw_forecast").alias(f"raw_fcst_{suffix}"),
                pl.col("forecast_scalar").alias(f"scalar_{suffix}"),
            ])
        # All rules are calculated from the same Panama history; joining on
        # date makes that ownership explicit while retaining one common vol.
        combined = combined.join(frame.select(columns), on="ts_event", how="left")
    assert combined is not None
    return combined.sort("ts_event")


def load_ewmac_component_frames(
    history_codes: Iterable[str],
    *,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
) -> dict[str, pl.DataFrame]:
    """Load compact component frames once per distinct history identifier.

    This is the reusable batch path used by Phase 2. The returned mapping is
    keyed by history instrument rather than executable symbol, allowing MZC
    and CORN_mini, for example, to share one expensive forecast calculation.
    """
    rule_tuple = tuple(rules)
    pooled = load_pooled_ewmac_rules(
        db_path=db_path,
        pooling_mapping_path=pooling_mapping_path,
        rules=rule_tuple,
    )
    provider = PysystemtradeHistoryProvider(db_path=db_path)
    codes = sorted(set(str(code) for code in history_codes))
    result: dict[str, pl.DataFrame] = {}
    for position, code in enumerate(codes, start=1):
        result[code] = build_ewmac_component_frame(provider.load(code), pooled)
        logger.debug(
            "ewmac_component_frame built hist_instr_code=%s rows=%d "
            "completed=%d total=%d",
            code,
            result[code].height,
            position,
            len(codes),
        )
    return result


def _coerce_date(value: date | datetime | str | None) -> date | None:
    """Normalize an optional notebook date boundary to ``datetime.date``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_slice(
    frame: pl.DataFrame,
    *,
    start: date | datetime | str | None,
    end: date | datetime | str | None,
) -> pl.DataFrame:
    """Filter a ``ts_event`` dataframe to optional inclusive boundaries."""
    start_date = _coerce_date(start)
    end_date = _coerce_date(end)
    result = frame
    if start_date is not None:
        result = result.filter(pl.col("ts_event") >= start_date)
    if end_date is not None:
        result = result.filter(pl.col("ts_event") <= end_date)
    return result


@dataclass(frozen=True)
class EwmacResearchView:
    """Notebook-facing history, pooled normalization, and forecast bundle.

    ``history`` exposes the original signal, Panama, mark, and carry streams.
    ``forecasts`` is a detailed wide daily momentum frame. ``pooled_scalars``
    is long by ``rule`` and date, while ``normalization_coverage`` and
    ``cache_metadata`` retain the pool membership and reproducibility audit.
    """

    instrument_code: str
    rules: tuple[EwmacRule, ...]
    history: FuturesHistory
    forecasts: pl.DataFrame
    pooled_scalars: pl.DataFrame
    normalization_coverage: Mapping[str, pl.DataFrame]
    cache_metadata: Mapping[str, Mapping[str, object]]

    def forecast_table(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        columns: Iterable[str] | None = None,
    ) -> pl.DataFrame:
        """Return a date-filtered forecast table for notebook display.

        When ``columns`` is omitted, the table contains price, point
        volatility, and final scaled component forecasts. Pass explicit raw or
        scalar column names from ``self.forecasts.columns`` for deeper audits.
        """
        frame = _date_slice(self.forecasts, start=start, end=end)
        if columns is None:
            selected = ["ts_event", "close", "point_vol"]
            selected.extend(
                rule.column for rule in self.rules
                if rule.column in frame.columns
            )
        else:
            selected = list(columns)
            if "ts_event" not in selected:
                selected.insert(0, "ts_event")
        missing = sorted(set(selected).difference(frame.columns))
        if missing:
            raise ValueError(f"forecast table has no columns: {missing}")
        return frame.select(selected)

    def scalar_table(
        self,
        rule: str | None = None,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return pooled-scalar history for one rule or all canonical rules."""
        frame = _date_slice(self.pooled_scalars, start=start, end=end)
        if rule is not None:
            if rule not in {item.key for item in self.rules}:
                raise ValueError(f"unsupported EWMAC rule: {rule}")
            frame = frame.filter(pl.col("rule") == rule)
        return frame

    def cache_status(self) -> pl.DataFrame:
        """Return history, forecast-panel, and scalar cache status by rule."""
        rows = []
        for rule in self.rules:
            metadata = self.cache_metadata.get(rule.key, {})
            rows.append({
                "rule": rule.key,
                "history_cache_hit": self.history.metadata.get("cache_hit"),
                "forecast_panel_cache_hit": metadata.get(
                    "forecast_panel_cache_hit"
                ),
                "scalar_cache_hit": metadata.get("scalar_cache_hit"),
                "source_commit": metadata.get("source_commit"),
                "source_range_key": metadata.get("source_range_key"),
            })
        return pl.DataFrame(rows, infer_schema_length=None)

    def plot(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        rules: Iterable[str] | None = None,
        figsize: tuple[float, float] = (14.0, 8.0),
    ):
        """Plot Panama price above scaled component forecasts.

        The method imports Matplotlib lazily and returns ``(figure, axes)`` so
        notebooks can further customize or save the plot. Forecasts use the
        project's normalized -1 to +1 convention. This plot contains component
        rules only; it does not claim a portfolio-derived FDM or combination.
        """
        import matplotlib.pyplot as plt

        active = (
            [item.key for item in self.rules]
            if rules is None
            else [str(rule) for rule in rules]
        )
        known = {item.key: item for item in self.rules}
        unsupported = sorted(set(active).difference(known))
        if unsupported:
            raise ValueError(f"unsupported EWMAC rules: {unsupported}")
        frame = _date_slice(self.forecasts, start=start, end=end)
        if frame.is_empty():
            raise ValueError("plot date range contains no forecast observations")

        x = frame.get_column("ts_event").to_list()
        figure, axes = plt.subplots(2, 1, sharex=True, figsize=figsize)
        axes[0].plot(x, frame.get_column("close").to_list(), color="black")
        axes[0].set_ylabel("Panama price")
        axes[0].set_title(f"{self.instrument_code} EWMAC research")
        for key in active:
            rule = known[key]
            axes[1].plot(
                x,
                frame.get_column(rule.column).to_list(),
                label=key,
                linewidth=1.0,
            )
        axes[1].axhline(0.0, color="black", linewidth=0.6)
        axes[1].set_ylabel("Forecast (-1 to +1)")
        axes[1].legend(ncol=min(len(active), 5))
        figure.tight_layout()
        return figure, axes


def load_ewmac_research(
    instrument_code: str,
    *,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
) -> EwmacResearchView:
    """Load one instrument's complete EWMAC notebook research bundle.

    This reads only local reviewed/cached data: it does not connect to IB or
    invoke the cost or selection reports. The returned detailed frame includes
    all requested raw and scaled momentum columns, while pooled scalar rows are
    kept separately in long form for direct display and plotting.
    """
    rule_tuple = tuple(rules)
    # Load the requested history before normalization so ``cache_hit`` reports
    # the state encountered by this notebook call, rather than a cache that a
    # first-time full-universe normalization build may create incidentally.
    history = PysystemtradeHistoryProvider(db_path=db_path).load(instrument_code)
    pooled = load_pooled_ewmac_rules(
        db_path=db_path,
        pooling_mapping_path=pooling_mapping_path,
        rules=rule_tuple,
    )
    forecasts = build_ewmac_component_frame(history, pooled, detailed=True)
    scalar_frames = []
    for key, item in pooled.items():
        scalar_frames.append(
            item.global_scalar().with_columns(pl.lit(key).alias("rule"))
        )
    pooled_scalars = pl.concat(scalar_frames, how="diagonal_relaxed").sort(
        "rule", "ts_event"
    )
    return EwmacResearchView(
        instrument_code=instrument_code,
        rules=rule_tuple,
        history=history,
        forecasts=forecasts,
        pooled_scalars=pooled_scalars,
        normalization_coverage={
            key: item.coverage for key, item in pooled.items()
        },
        cache_metadata={
            key: item.cache_metadata for key, item in pooled.items()
        },
    )

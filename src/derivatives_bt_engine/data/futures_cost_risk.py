"""IBKR futures contract cost and risk diagnostic.

The diagnostic deliberately separates research history from execution data:

* by default, the full roll-neutral pysystemtrade history supplies Carver's
  70% fast/30% slow mixed point-volatility estimate (32-session EWM standard
  deviation plus a ten-year EWM anchor); and
* Carver's IB mapping resolves the dated contract that supplies current price,
  expiry, and an optional live bid/ask snapshot.  Point value, commission,
  native currency, and FX conversion remain explicit report inputs.
* the reviewed normalization pool supplies causal EWMAC baselines for the
  4/16, 8/32, 16/64, 32/128, and 64/256 rules; and
* each execution row combines its own one-way costs and current dollar risk
  with pooled rule turnover and its representative roll policy.

All 252 instruments with usable Carver history have candidate IB identities.
Five local CBOT grain micros and two Treasury micros absent from Carver are
added as execution-only overlays, producing 259 candidate rows; qualification
against the connected account determines actual availability.
IB dated and continuous history remain explicit comparison modes.  A missing
quote is never treated as a zero spread, and spread-dependent costs remain
null.

Run with TWS/IB Gateway available::

    .venv/bin/python -m derivatives_bt_engine.data.futures_cost_risk \
      --instruments all-pysystemtrade

The public CSV leads with the selected current price, FX, volatility, spread,
and cost path.  Snapshot alternatives use ``snap_*`` and imported Carver
references use ``ref_*`` so neither can be mistaken for the selected inputs.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from ib_tools.ibpysync import IBPySync

from derivatives_bt_engine.data.pysystemtrade_ib import (
    add_local_execution_overlays,
    load_pysystemtrade_ib_instruments,
)
from derivatives_bt_engine.data.pysystemtrade_pooling import (
    DEFAULT_POOLING_MAPPING_PATH,
)
from derivatives_bt_engine.data.report_formatting import round_public_report
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.instruments import (
    get_spec,
    resolve_annualization_days,
    resolve_signal_symbol,
)
from derivatives_bt_engine.domain.signal import (
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_SCALAR_MIN_PERIODS,
    carver_ewmac,
)
from derivatives_bt_engine.domain.volatility import (
    CARVER_BUSINESS_DAYS_PER_YEAR,
    CARVER_FAST_VOL_SPAN,
    CARVER_SLOW_VOL_WEIGHT,
    CARVER_SLOW_VOL_YEARS,
    CARVER_VOL_MIN_SAMPLES,
    carver_mixed_point_volatility,
)
from derivatives_bt_engine.live.tsmom_rebalance import (
    _format_ib_multiplier,
    _resolve_contract,
    build_instruments,
)
from derivatives_bt_engine.utils.logger import setup_logger


log = logging.getLogger("derivatives_bt_engine.data.futures_cost_risk")

DEFAULT_DURATION = "1 Y"
DEFAULT_SPREAD_DURATION = "5 D"
REPORT_TIMEZONE = ZoneInfo("America/Chicago")
DEFAULT_SPREAD_BAR_SIZES = ("15 mins",)
DEFAULT_DELAYED_SPREAD_REQUEST_PLAN = (
    ("15 mins", None),
    ("5 mins", 5),
    ("1 min", 1),
)
DEFAULT_MIN_DAYS = 7
DEFAULT_QUOTE_WAIT_SECONDS = 3.0
DEFAULT_CONTRACT_DETAILS_TIMEOUT = 8.0
DEFAULT_ROLL_AUDIT_RECENT_YEARS = 5
MIN_ROLL_AUDIT_RECENT_YEARS = 3
ROLL_RATE_MATCH_TOLERANCE = 0.25
# ib_insync's historical request coroutine times out internally after 60
# seconds, cancels the request, and can return an empty BarDataList instead of
# raising.  Treat an empty result this close to that boundary as a timeout so
# the cost audit does not immediately issue another expensive history request.
IB_INSYNC_HISTORICAL_TIMEOUT_FLOOR_SECONDS = 55.0
RETIRED_IB_INSTRUMENTS = {
    "BB3M": "CME BSBY futures were permanently delisted in October 2024",
}
DEFAULT_COST_EWMAC_FAST_SPANS = (4, 8, 16, 32, 64)
DEFAULT_COST_EWMAC_FAST_SPAN = 16
DEFAULT_COST_EWMAC_SLOW_SPAN = 64
DEFAULT_COST_EWMAC_VOL_SPAN = CARVER_FAST_VOL_SPAN
DEFAULT_RULE_COST_LIMIT_SR = 0.15
DEFAULT_AFFORDABILITY_TARGET_VOL = 0.20
DEFAULT_AFFORDABILITY_MIN_CONTRACTS = 4
DEFAULT_AFFORDABILITY_CAPITAL_USD = 100_000.0
DEFAULT_AFFORDABILITY_IDM = 1.0
DEFAULT_AFFORDABILITY_MIN_MAIN_INSTRUMENTS = 15
DEFAULT_AFFORDABILITY_MAIN_ASSET_CLASSES = (
    "Equity",
    "Ags",
    "Vol",
    "OilGas",
    "FX",
    "Metals",
    "Bond",
)
DEFAULT_INSTRUMENT_COST_LIMIT_SR = 0.01
DEFAULT_VOLUME_LOOKBACK_DAYS = 20
DEFAULT_VOLUME_DURATION = "2 M"
DEFAULT_LIQUIDITY_ANNUAL_TRADES = 25.0
DEFAULT_LIQUIDITY_BUSINESS_DAYS = 250
DEFAULT_MAX_MARKET_VOLUME_PCT = 1.0
DEFAULT_MIN_DAILY_VOLUME_CONTRACTS = 100.0


def _pooling_report_identity(instr: dict) -> dict:
    return {
        "history_instrument_code": (
            instr.get("history_instrument_code")
            or instr.get("instrument_code")
        ),
        "description": instr.get("description"),
        "asset_class": instr.get("asset_class"),
        "region": instr.get("region"),
        "economic_family_id": instr.get("economic_family_id"),
        "roll_policy_id": instr.get("roll_policy_id"),
        "duplicate_group_id": instr.get("duplicate_group_id"),
        "pooling_role": instr.get("pooling_role"),
        "representative_instrument": instr.get("representative_instrument"),
        "include_default_pool": instr.get("include_default_pool"),
        "pooling_decision_basis": instr.get("pooling_decision_basis"),
        "execution_profile": instr.get("execution_profile"),
        "execution_eligible": instr.get("execution_eligible", True),
        "execution_restriction_reason": instr.get(
            "execution_restriction_reason"
        ),
    }


def _positive_finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _nonnegative_finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _parse_fast_spans(value: str) -> tuple[int, ...]:
    try:
        spans = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("EWMAC fast spans must be integers") from exc
    if not spans or any(span <= 0 for span in spans):
        raise argparse.ArgumentTypeError("EWMAC fast spans must be positive")
    return spans


def _parse_asset_classes(value: str) -> tuple[str, ...]:
    asset_classes = tuple(
        item.strip() for item in value.split(",") if item.strip()
    )
    if not asset_classes:
        raise argparse.ArgumentTypeError("asset classes cannot be empty")
    return asset_classes


def _ib_contract_currency(instr: dict, contract=None) -> str:
    """Prefer explicit broker currency, then the qualified/native currency."""
    return (
        instr.get("ib_currency")
        or getattr(contract, "currency", None)
        or instr.get("currency")
        or "USD"
    )


def _historical_request_timed_out(failures: object) -> bool:
    text = str(failures or "").lower()
    return any(
        marker in text
        for marker in ("timeout", "timed out", "query cancelled")
    )


def _cap_ib_duration(duration: str, max_days: Optional[int]) -> str:
    """Cap an IB duration while preserving shorter user requests."""
    if max_days is None:
        return duration
    parts = duration.strip().upper().split()
    if len(parts) != 2:
        return f"{max_days} D"
    try:
        amount = int(parts[0])
    except ValueError:
        return f"{max_days} D"
    days_per_unit = {
        "S": 1.0 / 86_400.0,
        "D": 1.0,
        "W": 7.0,
        "M": 30.0,
        "Y": 365.0,
    }
    requested_days = amount * days_per_unit.get(parts[1], math.inf)
    return duration if requested_days <= max_days else f"{max_days} D"


def _spread_request_plan(
    duration: str,
    market_data_type: str,
) -> tuple[tuple[str, str], ...]:
    """Use bounded 15-minute history, with shorter delayed fallbacks."""
    if market_data_type != "delayed":
        return tuple((bar_size, duration) for bar_size in DEFAULT_SPREAD_BAR_SIZES)
    return tuple(
        (bar_size, _cap_ib_duration(duration, max_days))
        for bar_size, max_days in DEFAULT_DELAYED_SPREAD_REQUEST_PLAN
    )


def _annualized_sharpe(values: pl.Series, annualization_days: int) -> Optional[float]:
    clean = values.drop_nulls()
    if clean.len() < 2:
        return None
    standard_deviation = clean.std()
    if standard_deviation is None or standard_deviation <= 0:
        return None
    return float(clean.mean() / standard_deviation * math.sqrt(annualization_days))


def _roll_rate_audit(
    marks: pl.DataFrame,
    *,
    hold_roll_cycle: object,
    recent_years: int = DEFAULT_ROLL_AUDIT_RECENT_YEARS,
) -> dict[str, object]:
    """Compare historical roll transitions with the current configured cycle.

    Forward holding costs follow pysystemtrade's current roll configuration,
    whose rolls-per-year value is the number of months in the hold cycle.
    Full-history and recent observed rates remain diagnostics because older
    histories can embody a different roll policy.
    """
    if recent_years <= 0:
        raise ValueError("recent_years must be positive")
    required = {"trade_date", "is_roll"}
    missing = required - set(marks.columns)
    if missing:
        raise ValueError(f"roll audit marks missing columns: {sorted(missing)}")

    history_start = marks.get_column("trade_date").min()
    history_end = marks.get_column("trade_date").max()
    roll_dates = marks.filter(pl.col("is_roll")).get_column("trade_date")
    observed_rolls = roll_dates.len()
    elapsed_years = (
        (history_end - history_start).days / 365.25
        if history_start is not None and history_end is not None
        else 0.0
    )
    observed_full_rate = (
        observed_rolls / elapsed_years if elapsed_years > 0 else None
    )

    cycle = str(hold_roll_cycle or "").strip().upper()
    configured_rate = float(len(cycle)) if cycle else None

    recent_start_year = None
    recent_end_year = None
    recent_year_count = 0
    recent_roll_count = 0
    observed_recent_rate = None
    if history_start is not None and history_end is not None:
        # Exclude both boundary calendar years: either can be partial.
        first_complete_year = history_start.year + 1
        last_complete_year = history_end.year - 1
        recent_start_year = max(
            first_complete_year,
            last_complete_year - recent_years + 1,
        )
        if recent_start_year <= last_complete_year:
            recent_end_year = last_complete_year
            recent_year_count = recent_end_year - recent_start_year + 1
            recent_roll_count = roll_dates.filter(
                roll_dates.dt.year().is_between(
                    recent_start_year,
                    recent_end_year,
                )
            ).len()
            observed_recent_rate = recent_roll_count / recent_year_count

    full_difference = (
        observed_full_rate - configured_rate
        if observed_full_rate is not None and configured_rate is not None
        else None
    )
    recent_difference = (
        observed_recent_rate - configured_rate
        if observed_recent_rate is not None and configured_rate is not None
        else None
    )
    if configured_rate is None:
        audit_status = "missing_configured_hold_cycle"
    elif recent_year_count < MIN_ROLL_AUDIT_RECENT_YEARS:
        audit_status = "insufficient_recent_complete_years"
    elif abs(recent_difference) > ROLL_RATE_MATCH_TOLERANCE:
        audit_status = (
            "recent_above_configured"
            if recent_difference > 0
            else "recent_below_configured"
        )
    elif (
        full_difference is not None
        and abs(full_difference) > ROLL_RATE_MATCH_TOLERANCE
    ):
        audit_status = "historical_policy_change"
    else:
        audit_status = "consistent_with_configured_cycle"

    return {
        "strategy_rolls": observed_rolls,
        # Retain the legacy name for downstream compatibility. It is an
        # observed full-history rate, never the selected forward cost input.
        "strategy_rolls_per_year": observed_full_rate,
        "strategy_observed_rolls_per_year_full": observed_full_rate,
        "strategy_recent_rolls": recent_roll_count,
        "strategy_observed_rolls_per_year_recent": observed_recent_rate,
        "strategy_recent_roll_start_year": recent_start_year,
        "strategy_recent_roll_end_year": recent_end_year,
        "strategy_recent_roll_years": recent_year_count,
        "strategy_hold_roll_cycle": cycle or None,
        "strategy_configured_rolls_per_year": configured_rate,
        "strategy_full_minus_configured_rolls_per_year": full_difference,
        "strategy_recent_minus_configured_rolls_per_year": recent_difference,
        "strategy_roll_rate_audit": audit_status,
    }


def _ewmac_rule_performance(
    frame: pl.DataFrame,
    *,
    annualization_days: int = CARVER_BUSINESS_DAYS_PER_YEAR,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
) -> tuple[dict[str, object], pl.DataFrame]:
    """Return one instrument's pre-cost EWMAC Sharpe and forecast turnover.

    The forecast is delayed by one observation before earning the canonical
    matched-contract point change.  Point volatility is delayed with the
    forecast, matching the ex-ante position sizing used by pysystemtrade.
    Turnover follows Carver's forecast convention: mean absolute daily change
    divided by the average-absolute-forecast target, annualized by 256.
    """
    required = {
        "ts_event", "ewmac_forecast", "point_vol", "pt_change_1d",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"EWMAC performance input missing columns: {sorted(missing)}")
    if target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be positive")

    performance = (
        frame.sort("ts_event")
        .with_columns(
            (pl.col("ewmac_forecast").shift(1) / target_abs_forecast).alias(
                "normalized_position"
            ),
            pl.col("point_vol").shift(1).alias("sizing_point_vol"),
        )
        .with_columns(
            pl.when(
                pl.col("normalized_position").is_not_null()
                & pl.col("pt_change_1d").is_not_null()
                & (pl.col("sizing_point_vol") > 0)
            )
            .then(
                pl.col("normalized_position")
                * pl.col("pt_change_1d")
                / pl.col("sizing_point_vol")
            )
            .otherwise(None)
            .alias("risk_adjusted_pnl")
        )
    )
    usable_forecasts = performance.get_column("ewmac_forecast").drop_nulls()
    forecast_changes = usable_forecasts.diff().abs().drop_nulls()
    annual_turnover = (
        float(forecast_changes.mean() / target_abs_forecast * annualization_days)
        if forecast_changes.len()
        else None
    )
    pnl = performance.get_column("risk_adjusted_pnl").drop_nulls()
    metrics = {
        "strategy_history_start": performance.get_column("ts_event").min(),
        "strategy_history_end": performance.get_column("ts_event").max(),
        "strategy_history_observations": performance.height,
        "strategy_forecast_observations": usable_forecasts.len(),
        "strategy_pnl_observations": pnl.len(),
        "ewmac_pre_cost_sharpe": _annualized_sharpe(pnl, annualization_days),
        "ewmac_forecast_turnover": annual_turnover,
    }
    return metrics, performance.select("ts_event", "risk_adjusted_pnl").drop_nulls()


def estimate_pooled_ewmac_cost_baseline(
    provider: PysystemtradeHistoryProvider,
    *,
    db_path: Path | str,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    fast_span: int = DEFAULT_COST_EWMAC_FAST_SPAN,
    slow_span: int = DEFAULT_COST_EWMAC_SLOW_SPAN,
    vol_span: int = DEFAULT_COST_EWMAC_VOL_SPAN,
    vol_slow_years: int = CARVER_SLOW_VOL_YEARS,
    vol_slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
    vol_min_samples: int = CARVER_VOL_MIN_SAMPLES,
    scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    forecast_cap: float = EWMAC_FORECAST_CAP,
) -> tuple[dict[str, object], pl.DataFrame]:
    """Estimate a pooled pre-cost EWMAC baseline from reviewed histories."""
    # Import lazily: the cost diagnostic can still be imported without loading
    # the full backtester, while the actual run reuses its versioned cache.
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
        ewmac_fast_span=fast_span,
        ewmac_slow_span=slow_span,
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
    scalar = (
        scalar_history.filter(pl.col("pool_key") == "global")
        .select("ts_event", "forecast_scalar")
        .sort("ts_event")
    )
    metric_rows = []
    pooled_pnl = []
    for position, coverage_row in enumerate(coverage.iter_rows(named=True), start=1):
        instrument_code = coverage_row["instrument_code"]
        history = provider.load(instrument_code)
        rule = (
            carver_ewmac(
                history.panama_bars(),
                fast_span=fast_span,
                slow_span=slow_span,
                vol_span=vol_span,
                vol_slow_years=vol_slow_years,
                vol_slow_weight=vol_slow_weight,
                vol_min_samples=vol_min_samples,
                forecast_scalar=1.0,
                forecast_cap=forecast_cap,
            )
            .join_asof(scalar, on="ts_event", strategy="backward")
            .with_columns(
                (pl.col("raw_forecast") * pl.col("forecast_scalar"))
                .clip(-forecast_cap, forecast_cap)
                .alias("ewmac_forecast")
            )
        )
        metrics, pnl = _ewmac_rule_performance(
            rule,
            annualization_days=CARVER_BUSINESS_DAYS_PER_YEAR,
            target_abs_forecast=target_abs_forecast,
        )
        metrics.update(
            instrument_code=instrument_code,
            **_roll_rate_audit(
                history.marks,
                hold_roll_cycle=history.metadata.get("hold_roll_cycle"),
            ),
        )
        metric_rows.append(metrics)
        pooled_pnl.append(
            pnl.with_columns(pl.lit(instrument_code).alias("instrument_code"))
        )
        if position % 25 == 0 or position == coverage.height:
            log.info(
                "cost_ewmac_baseline progress completed=%d total=%d",
                position,
                coverage.height,
            )

    metrics = pl.DataFrame(metric_rows, infer_schema_length=None).sort(
        "instrument_code"
    )
    roll_audit_counts = dict(
        sorted(
            {
                row["strategy_roll_rate_audit"]: row["len"]
                for row in metrics.group_by("strategy_roll_rate_audit")
                .len()
                .iter_rows(named=True)
            }.items()
        )
    )
    log.info(
        "roll_rate_audit ewmac_fast_span=%d instruments=%d "
        "configured_unit=roll_events_per_year recent_window_years=%d "
        "status_counts=%s",
        fast_span,
        metrics.height,
        DEFAULT_ROLL_AUDIT_RECENT_YEARS,
        roll_audit_counts,
    )
    pooled_returns = pl.concat(pooled_pnl, how="vertical").get_column(
        "risk_adjusted_pnl"
    )
    valid_metrics = metrics.filter(
        pl.col("ewmac_forecast_turnover").is_not_null()
        & pl.col("ewmac_pre_cost_sharpe").is_not_null()
    )
    turnover_weight = pl.col("strategy_history_observations").cast(pl.Float64)
    weight_total = valid_metrics.select(turnover_weight.sum()).item()
    pooled_turnover = (
        valid_metrics.select(
            (pl.col("ewmac_forecast_turnover") * turnover_weight).sum()
            / turnover_weight.sum()
        ).item()
        if weight_total
        else None
    )
    mean_instrument_sharpe = valid_metrics.get_column(
        "ewmac_pre_cost_sharpe"
    ).mean()
    median_instrument_sharpe = valid_metrics.get_column(
        "ewmac_pre_cost_sharpe"
    ).median()
    history_weighted_mean_sharpe = (
        valid_metrics.select(
            (pl.col("ewmac_pre_cost_sharpe") * turnover_weight).sum()
            / turnover_weight.sum()
        ).item()
        if weight_total
        else None
    )
    stacked_observation_sharpe = _annualized_sharpe(
        pooled_returns, CARVER_BUSINESS_DAYS_PER_YEAR
    )
    mapping_hash = coverage.get_column("pooling_mapping_hash").unique().to_list()
    summary = {
        "ewmac_pool_instruments": coverage.height,
        "ewmac_pool_mapping_hash": mapping_hash[0] if len(mapping_hash) == 1 else None,
        # The affordability baseline gives every reviewed strategy history
        # one vote. Concatenating all daily observations would instead let the
        # oldest markets dominate merely because they have more rows.
        "ewmac_pooled_pre_cost_sharpe": mean_instrument_sharpe,
        "ewmac_mean_instrument_pre_cost_sharpe": mean_instrument_sharpe,
        "ewmac_median_instrument_pre_cost_sharpe": median_instrument_sharpe,
        "ewmac_history_weighted_mean_pre_cost_sharpe": (
            history_weighted_mean_sharpe
        ),
        "ewmac_stacked_observation_pre_cost_sharpe": stacked_observation_sharpe,
        "ewmac_pooled_forecast_turnover": pooled_turnover,
        "roll_rate_audit_status_counts": json.dumps(
            roll_audit_counts,
            sort_keys=True,
        ),
        "ewmac_fast_span": fast_span,
        "ewmac_slow_span": slow_span,
        "ewmac_vol_span": vol_span,
        "ewmac_vol_slow_years": vol_slow_years,
        "ewmac_vol_slow_weight": vol_slow_weight,
        "ewmac_vol_min_samples": vol_min_samples,
        "ewmac_target_abs_forecast": target_abs_forecast,
        "ewmac_forecast_cap": forecast_cap,
        "ewmac_scalar_min_periods": scalar_min_periods,
        "ewmac_normalization_source_commit": cache_metadata["source_commit"],
        "ewmac_normalization_range_key": cache_metadata["source_range_key"],
        "ewmac_forecast_panel_cache_hit": cache_metadata[
            "forecast_panel_cache_hit"
        ],
        "ewmac_scalar_cache_hit": cache_metadata["scalar_cache_hit"],
    }
    log.info(
        "cost_ewmac_baseline complete instruments=%d pooled_sharpe=%s "
        "history_weighted_mean_sharpe=%s stacked_observation_sharpe=%s "
        "pooled_turnover=%s",
        coverage.height,
        summary["ewmac_pooled_pre_cost_sharpe"],
        history_weighted_mean_sharpe,
        stacked_observation_sharpe,
        summary["ewmac_pooled_forecast_turnover"],
    )
    return summary, metrics


def estimate_pooled_ewmac_cost_baselines(
    provider: PysystemtradeHistoryProvider,
    *,
    db_path: Path | str,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    fast_spans: tuple[int, ...] = DEFAULT_COST_EWMAC_FAST_SPANS,
    **kwargs,
) -> tuple[dict[int, dict[str, object]], dict[int, pl.DataFrame]]:
    """Estimate the five canonical EWMAC rule baselines independently.

    Rule turnover is pooled across the reviewed research representatives.
    Slow spans follow Carver's four-to-one convention.  The returned mapping
    keeps each rule's diagnostic Sharpe separate; those Sharpes do not decide
    whether a rule is affordable.
    """
    if not fast_spans or any(span <= 0 for span in fast_spans):
        raise ValueError("fast_spans must contain positive integers")
    summaries: dict[int, dict[str, object]] = {}
    metrics: dict[int, pl.DataFrame] = {}
    for fast_span in fast_spans:
        summary, rule_metrics = estimate_pooled_ewmac_cost_baseline(
            provider,
            db_path=db_path,
            pooling_mapping_path=pooling_mapping_path,
            fast_span=fast_span,
            slow_span=fast_span * 4,
            **kwargs,
        )
        summaries[fast_span] = summary
        metrics[fast_span] = rule_metrics
    return summaries, metrics


def _configured_cost_estimate(
    instr: dict,
    report_row: dict,
    *,
    pooled_summaries: Optional[dict[int, dict[str, object]]],
    strategy_metrics_by_rule: Optional[dict[int, dict[str, object]]],
    rule_cost_limit_sr: float,
) -> dict[str, object]:
    """Calculate one-way trade and complete-roll costs in annual SR units."""
    common: dict[str, object] = {
        "strategy_reference_instrument": (
            instr.get("representative_instrument")
            or instr.get("instrument_code")
            or instr.get("symbol")
        ),
        "rule_cost_limit_sr": rule_cost_limit_sr,
    }
    first_metrics = next(iter((strategy_metrics_by_rule or {}).values()), None)
    if first_metrics is not None:
        common.update(
            strategy_reference_history_start=first_metrics.get(
                "strategy_history_start"
            ),
            strategy_reference_history_end=first_metrics.get(
                "strategy_history_end"
            ),
            strategy_reference_history_observations=first_metrics.get(
                "strategy_history_observations"
            ),
            strategy_reference_rolls=first_metrics.get("strategy_rolls"),
            strategy_reference_observed_rolls_per_year_full=first_metrics.get(
                "strategy_observed_rolls_per_year_full"
            ),
            strategy_reference_recent_rolls=first_metrics.get(
                "strategy_recent_rolls"
            ),
            strategy_reference_observed_rolls_per_year_recent=first_metrics.get(
                "strategy_observed_rolls_per_year_recent"
            ),
            strategy_reference_recent_roll_start_year=first_metrics.get(
                "strategy_recent_roll_start_year"
            ),
            strategy_reference_recent_roll_end_year=first_metrics.get(
                "strategy_recent_roll_end_year"
            ),
            strategy_reference_recent_roll_years=first_metrics.get(
                "strategy_recent_roll_years"
            ),
            strategy_reference_hold_roll_cycle=first_metrics.get(
                "strategy_hold_roll_cycle"
            ),
            strategy_reference_configured_rolls_per_year=first_metrics.get(
                "strategy_configured_rolls_per_year"
            ),
            strategy_reference_full_minus_configured_rolls_per_year=(
                first_metrics.get(
                    "strategy_full_minus_configured_rolls_per_year"
                )
            ),
            strategy_reference_recent_minus_configured_rolls_per_year=(
                first_metrics.get(
                    "strategy_recent_minus_configured_rolls_per_year"
                )
            ),
            strategy_reference_roll_rate_audit=first_metrics.get(
                "strategy_roll_rate_audit"
            ),
        )
    configured_rolls_per_year = _nonnegative_finite(
        (first_metrics or {}).get("strategy_configured_rolls_per_year")
    )
    recent_rolls_per_year = _nonnegative_finite(
        (first_metrics or {}).get("strategy_observed_rolls_per_year_recent")
    )
    observed_rolls_per_year = _nonnegative_finite(
        (first_metrics or {}).get("strategy_observed_rolls_per_year_full")
    )
    if observed_rolls_per_year is None:
        observed_rolls_per_year = _nonnegative_finite(
            (first_metrics or {}).get("strategy_rolls_per_year")
        )
    if configured_rolls_per_year is not None:
        rolls_per_year = configured_rolls_per_year
        roll_rate_source = "configured_hold_cycle"
    elif recent_rolls_per_year is not None:
        rolls_per_year = recent_rolls_per_year
        roll_rate_source = "recent_observed_fallback"
    else:
        rolls_per_year = observed_rolls_per_year
        roll_rate_source = (
            "full_history_observed_fallback"
            if rolls_per_year is not None
            else "unavailable"
        )
    common.update(
        strategy_reference_ann_rolls=rolls_per_year,
        strategy_reference_roll_rate_source=roll_rate_source,
        roll_cost_model="calendar_spread",
    )

    carver_spread_points = _nonnegative_finite(instr.get("carver_spread_points"))
    spread_points = _nonnegative_finite(
        report_row.get("selected_one_way_spread_points")
    )
    spread_source = report_row.get("selected_spread_source")
    if spread_points is None:
        spread_points = carver_spread_points
        spread_source = "carver_configured"
    multiplier = _positive_finite(report_row.get("multiplier"))
    price = _positive_finite(report_row.get("price"))
    fx_to_usd = _positive_finite(report_row.get("fx_to_usd"))
    annual_dollar_vol = _positive_finite(
        report_row.get("annual_dollar_vol_per_contract")
    )
    per_block = _nonnegative_finite(instr.get("commission"))
    per_trade = _nonnegative_finite(instr.get("per_trade_cost"))
    percentage = _nonnegative_finite(instr.get("percentage_cost"))
    if (
        spread_points is None
        or multiplier is None
        or price is None
        or fx_to_usd is None
        or annual_dollar_vol is None
    ):
        return {
            **common,
            "configured_cost_quality": "incomplete_static_inputs",
        }

    percentage_commission = (
        percentage * price * multiplier if percentage is not None else 0.0
    )
    commission_native = max(
        per_block if per_block is not None else 0.0,
        per_trade if per_trade is not None else 0.0,
        percentage_commission,
    )
    spread_cash_native = spread_points * multiplier
    one_way_native = spread_cash_native + commission_native
    one_way_usd = one_way_native * fx_to_usd
    trade_sr = one_way_usd / annual_dollar_vol
    roll_native = spread_cash_native + 2.0 * commission_native
    roll_usd = roll_native * fx_to_usd
    roll_sr = roll_usd / annual_dollar_vol
    result: dict[str, object] = {
        **common,
        "carver_configured_one_way_spread_points": carver_spread_points,
        "selected_one_way_spread_points": spread_points,
        "selected_spread_source": spread_source,
        "configured_commission_native": commission_native,
        "configured_commission": commission_native * fx_to_usd,
        "configured_spread_cash_native": spread_cash_native,
        "configured_one_way_cost_native": one_way_native,
        "configured_one_way_cost": one_way_usd,
        "configured_trade_sr": trade_sr,
        "configured_roll_cost_native": roll_native,
        "configured_roll_cost": roll_usd,
        "configured_roll_sr": roll_sr,
        "configured_cost_quality": (
            "ib_historical_bid_ask"
            if spread_source in {"ib_dated_contract", "ib_continuous_fallback"}
            else "ib_snapshot_bid_ask"
            if spread_source == "ib_snapshot"
            else "static_config_zero_spread"
            if spread_points == 0
            else "static_config_not_historical_quotes"
        ),
    }
    eligible_rules: list[str] = []
    for fast_span, summary in sorted((pooled_summaries or {}).items()):
        slow_span = fast_span * 4
        prefix = f"ewmac_{fast_span}_{slow_span}"
        metrics = (strategy_metrics_by_rule or {}).get(fast_span, {})
        pooled_turnover = _positive_finite(
            summary.get("ewmac_pooled_forecast_turnover")
        )
        trade_ann_cost_sr = (
            trade_sr * pooled_turnover
            if pooled_turnover is not None
            else None
        )
        roll_ann_cost_sr = (
            roll_sr * rolls_per_year
            if rolls_per_year is not None
            else None
        )
        tot_ann_cost_sr = (
            trade_ann_cost_sr + roll_ann_cost_sr
            if trade_ann_cost_sr is not None and roll_ann_cost_sr is not None
            else None
        )
        eligible = (
            tot_ann_cost_sr <= rule_cost_limit_sr
            if tot_ann_cost_sr is not None
            else None
        )
        if eligible:
            eligible_rules.append(f"{fast_span}/{slow_span}")
        result.update({
            f"{prefix}_ann_trades": pooled_turnover,
            f"{prefix}_ann_rolls": rolls_per_year,
            f"{prefix}_trade_ann_cost_sr": trade_ann_cost_sr,
            f"{prefix}_roll_ann_cost_sr": roll_ann_cost_sr,
            f"{prefix}_tot_ann_cost_sr": tot_ann_cost_sr,
            f"{prefix}_cost_eligible": eligible,
            f"{prefix}_median_instrument_pre_cost_sharpe": summary.get(
                "ewmac_median_instrument_pre_cost_sharpe"
            ),
            f"{prefix}_reference_pre_cost_sharpe": metrics.get(
                "ewmac_pre_cost_sharpe"
            ),
        })
    baselines_available = bool(pooled_summaries)
    result.update({
        "eligible_ewmac_rule_count": (
            len(eligible_rules) if baselines_available else None
        ),
        "eligible_ewmac_rules": (
            ",".join(eligible_rules) if baselines_available else None
        ),
        "instrument_has_eligible_ewmac_rule": (
            bool(eligible_rules) if baselines_available else None
        ),
    })
    return result


def _attach_pooled_cost_estimates(
    rows: list[dict],
    instruments: list[dict],
    *,
    pooled_summaries: Optional[dict[int, dict[str, object]]],
    strategy_metrics_by_rule: Optional[dict[int, pl.DataFrame]],
    rule_cost_limit_sr: float,
) -> list[dict]:
    instrument_by_symbol = {instr["symbol"]: instr for instr in instruments}
    metrics_by_rule = {
        fast_span: {
            row["instrument_code"]: row
            for row in metrics.iter_rows(named=True)
        }
        for fast_span, metrics in (strategy_metrics_by_rule or {}).items()
    }
    enriched = []
    for row in rows:
        instr = instrument_by_symbol.get(row.get("symbol"), {})
        reference = (
            instr.get("representative_instrument")
            or instr.get("instrument_code")
            or instr.get("symbol")
        )
        reference_metrics_by_rule: dict[int, dict[str, object]] = {}
        for fast_span, metrics in metrics_by_rule.items():
            reference_metrics = metrics.get(reference)
            if reference_metrics is not None:
                reference_metrics_by_rule[fast_span] = reference_metrics
        row.update(
            _configured_cost_estimate(
                instr,
                row,
                pooled_summaries=pooled_summaries,
                strategy_metrics_by_rule=reference_metrics_by_rule,
                rule_cost_limit_sr=rule_cost_limit_sr,
            )
        )
        enriched.append(row)
    return enriched


def _attach_affordability_ranks(
    report: pl.DataFrame,
    *,
    target_vol: float,
    min_contracts: int,
    capital_usd: float = DEFAULT_AFFORDABILITY_CAPITAL_USD,
    idm: float = DEFAULT_AFFORDABILITY_IDM,
    min_main_instruments: int = DEFAULT_AFFORDABILITY_MIN_MAIN_INSTRUMENTS,
    main_asset_classes: tuple[str, ...] = (
        DEFAULT_AFFORDABILITY_MAIN_ASSET_CLASSES
    ),
) -> pl.DataFrame:
    """Add cost ranks and an equal-weight main-instrument sizing scenario.

    Rank 1 means the lowest value for cost, affordability, and notional, so the
    cheapest and smallest contracts appear first. The scenario assumes the
    portfolio must fund at least ``min_main_instruments`` ordinary instruments;
    special-cluster additions do not satisfy that breadth requirement.
    """
    if target_vol <= 0:
        raise ValueError("target_vol must be positive")
    if min_contracts <= 0:
        raise ValueError("min_contracts must be positive")
    if capital_usd <= 0:
        raise ValueError("capital_usd must be positive")
    if idm <= 0:
        raise ValueError("idm must be positive")
    if min_main_instruments <= 0:
        raise ValueError("min_main_instruments must be positive")
    if not main_asset_classes:
        raise ValueError("main_asset_classes cannot be empty")
    required = {
        "symbol",
        "asset_class",
        "notional_per_contract",
        "annual_dollar_vol_per_contract",
        "configured_trade_sr",
    }
    if not required.issubset(report.columns):
        return report
    equal_weight_dvol_budget = (
        capital_usd * target_vol * idm / min_main_instruments
    )
    ranked = report.with_columns(
        pl.lit(target_vol).alias("affordability_target_vol"),
        pl.lit(min_contracts).alias("affordability_min_contracts"),
        pl.lit(capital_usd).alias("affordability_scenario_capital"),
        pl.lit(idm).alias("affordability_scenario_idm"),
        pl.lit(min_main_instruments).alias(
            "affordability_min_main_instruments"
        ),
        pl.lit(len(set(main_asset_classes))).alias(
            "affordability_main_cluster_count"
        ),
        pl.lit(",".join(main_asset_classes)).alias(
            "affordability_main_asset_classes"
        ),
        pl.col("asset_class").is_in(main_asset_classes).alias(
            "counts_toward_main_instrument_minimum"
        ),
        (
            pl.when(pl.col("asset_class").is_in(main_asset_classes))
            .then(pl.lit("main"))
            .otherwise(pl.lit("special"))
        ).alias("affordability_cluster_role"),
        pl.lit(1.0 / min_main_instruments).alias(
            "affordability_equal_weight"
        ),
        pl.lit(equal_weight_dvol_budget).alias("equal_weight_dvol_budget"),
        (pl.col("notional_per_contract") * min_contracts).alias(
            "min_contract_notional"
        ),
        (pl.col("annual_dollar_vol_per_contract") * min_contracts).alias(
            "min_contract_annual_dollar_vol"
        ),
        (
            pl.col("annual_dollar_vol_per_contract")
            * min_contracts
            / target_vol
        ).alias("min_capital_full_weight_idm1"),
        (
            pl.col("annual_dollar_vol_per_contract")
            * min_contracts
            * min_main_instruments
            / (target_vol * idm)
        ).alias("min_capital_equal_weight"),
        (
            equal_weight_dvol_budget
            / pl.col("annual_dollar_vol_per_contract")
        ).alias("equal_weight_average_contracts"),
        (
            equal_weight_dvol_budget
            / pl.col("annual_dollar_vol_per_contract")
            >= min_contracts
        ).alias("equal_weight_meets_min_contracts"),
        (
            pl.col("asset_class").is_in(main_asset_classes)
            & (
                equal_weight_dvol_budget
                / pl.col("annual_dollar_vol_per_contract")
                >= min_contracts
            )
        ).alias("main_instrument_affordable_for_scenario"),
    )
    return ranked.with_columns(
        pl.col("configured_trade_sr")
        .rank("ordinal")
        .over("asset_class")
        .alias("cost_rank_in_asset_class"),
        pl.col("annual_dollar_vol_per_contract")
        .rank("ordinal")
        .over("asset_class")
        .alias("affordability_rank_in_asset_class"),
        pl.col("notional_per_contract")
        .rank("ordinal")
        .over("asset_class")
        .alias("notional_rank_in_asset_class"),
    )


def _attach_phase2_prefilters(
    report: pl.DataFrame,
    *,
    initial_capital_usd: float,
    target_vol: float,
    instrument_cost_limit_sr: float,
    liquidity_ann_trades: float,
    liquidity_business_days: int,
    max_market_volume_pct: float,
    min_daily_volume_contracts: float,
) -> pl.DataFrame:
    """Attach hard cost, granularity, and relative-liquidity gates.

    The report remains a complete Phase 1 audit. ``phase2_eligible`` is the
    compact search-universe flag consumed before the greedy Phase 2 search.
    """
    numeric_args = {
        "initial_capital_usd": initial_capital_usd,
        "target_vol": target_vol,
        "instrument_cost_limit_sr": instrument_cost_limit_sr,
        "liquidity_ann_trades": liquidity_ann_trades,
        "liquidity_business_days": liquidity_business_days,
        "max_market_volume_pct": max_market_volume_pct,
        "min_daily_volume_contracts": min_daily_volume_contracts,
    }
    for name, value in numeric_args.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive")

    required = {
        "annual_dollar_vol_per_contract",
        "configured_trade_sr",
        "avg_daily_volume_contracts",
        "instrument_has_eligible_ewmac_rule",
        "execution_eligible",
        "ib_availability",
        "price",
        "fx_to_usd",
        "multiplier",
    }
    for name in required.difference(report.columns):
        if name in {
            "instrument_has_eligible_ewmac_rule",
            "execution_eligible",
        }:
            dtype = pl.Boolean
        elif name == "ib_availability":
            dtype = pl.String
        else:
            dtype = pl.Float64
        report = report.with_columns(pl.lit(None, dtype=dtype).alias(name))

    max_contract_ann_dvol_usd = initial_capital_usd * target_vol
    risk_traded_usd_day = (
        initial_capital_usd
        * target_vol
        * liquidity_ann_trades
        / liquidity_business_days
    )
    min_mkt_risk_vol_usd_day = risk_traded_usd_day / (
        max_market_volume_pct / 100.0
    )
    report = report.with_columns(
        pl.lit(initial_capital_usd).alias("initial_capital_usd"),
        pl.lit(target_vol).alias("selection_target_vol"),
        pl.lit(instrument_cost_limit_sr).alias("instrument_cost_limit_sr"),
        pl.lit(liquidity_ann_trades).alias("liquidity_ann_trades"),
        pl.lit(liquidity_business_days).alias("liquidity_business_days"),
        pl.lit(max_market_volume_pct).alias("max_market_volume_pct"),
        pl.lit(min_daily_volume_contracts).alias(
            "min_daily_volume_contracts"
        ),
        pl.lit(max_contract_ann_dvol_usd).alias(
            "max_contract_ann_dvol_usd"
        ),
        pl.lit(risk_traded_usd_day).alias("risk_traded_usd_day"),
        pl.lit(min_mkt_risk_vol_usd_day).alias(
            "min_mkt_risk_vol_usd_day"
        ),
        (
            pl.col("avg_daily_volume_contracts")
            * pl.col("annual_dollar_vol_per_contract")
        ).alias("mkt_risk_vol_usd_day"),
    ).with_columns(
        pl.when(pl.col("mkt_risk_vol_usd_day") > 0)
        .then(
            100.0
            * pl.col("risk_traded_usd_day")
            / pl.col("mkt_risk_vol_usd_day")
        )
        .otherwise(None)
        .alias("pct_mkt_volume"),
        (
            pl.col("configured_trade_sr").is_not_null()
            & pl.col("configured_trade_sr").is_finite()
            & (pl.col("configured_trade_sr") <= instrument_cost_limit_sr)
        ).alias("instrument_cost_eligible"),
        (
            pl.col("annual_dollar_vol_per_contract").is_not_null()
            & pl.col("annual_dollar_vol_per_contract").is_finite()
            & (
                pl.col("annual_dollar_vol_per_contract")
                <= max_contract_ann_dvol_usd
            )
        ).alias("risk_size_eligible"),
        (
            pl.col("avg_daily_volume_contracts").is_not_null()
            & pl.col("avg_daily_volume_contracts").is_finite()
            & (
                pl.col("avg_daily_volume_contracts")
                > min_daily_volume_contracts
            )
        ).alias("contract_volume_eligible"),
        (
            pl.col("price").is_not_null()
            & pl.col("price").is_finite()
            & (pl.col("price") > 0)
            & pl.col("fx_to_usd").is_not_null()
            & pl.col("fx_to_usd").is_finite()
            & (pl.col("fx_to_usd") > 0)
            & pl.col("multiplier").is_not_null()
            & pl.col("multiplier").is_finite()
            & (pl.col("multiplier").abs() > 0)
            & pl.col("avg_daily_volume_contracts").is_not_null()
            & pl.col("annual_dollar_vol_per_contract").is_not_null()
            & (pl.col("ib_availability") == "contract_qualified")
        ).alias("data_eligible"),
    ).with_columns(
        (
            pl.col("pct_mkt_volume").is_not_null()
            & pl.col("pct_mkt_volume").is_finite()
            & (pl.col("pct_mkt_volume") < max_market_volume_pct)
        ).alias("risk_volume_eligible"),
    ).with_columns(
        (
            pl.col("contract_volume_eligible")
            & pl.col("risk_volume_eligible")
        ).alias("liquidity_eligible"),
        (
            pl.when(~pl.col("data_eligible"))
            .then(pl.lit("dont_add_no_data"))
            .when(
                pl.col("instrument_cost_eligible")
                & pl.col("risk_size_eligible")
                & pl.col("contract_volume_eligible")
                & pl.col("risk_volume_eligible")
            )
            .then(pl.lit("add_first"))
            .otherwise(pl.lit("add_later"))
        ).alias("selection_bucket"),
    ).with_columns(
        (
            (pl.col("selection_bucket") == "add_first")
            & pl.col("instrument_has_eligible_ewmac_rule").fill_null(False)
            & pl.col("execution_eligible").fill_null(False)
        ).alias("phase2_eligible"),
    )

    exclusions: list[str] = []
    for row in report.iter_rows(named=True):
        reasons = []
        if not row.get("data_eligible"):
            reasons.append("data_unavailable")
        if not row.get("instrument_cost_eligible"):
            reasons.append("cost")
        if not row.get("risk_size_eligible"):
            reasons.append("risk_size")
        if row.get("avg_daily_volume_contracts") is None:
            reasons.append("volume_unavailable")
        else:
            if not row.get("contract_volume_eligible"):
                reasons.append("contract_volume")
            if not row.get("risk_volume_eligible"):
                reasons.append("risk_volume")
        if not row.get("instrument_has_eligible_ewmac_rule"):
            reasons.append("no_eligible_rule")
        if not row.get("execution_eligible"):
            reasons.append("execution")
        if row.get("ib_availability") != "contract_qualified":
            reasons.append("ib_unavailable")
        exclusions.append(",".join(reasons))
    return report.with_columns(
        pl.Series("phase2_exclusion", exclusions, dtype=pl.String)
    )


def volatility_from_bars(
    bars: pl.DataFrame,
    *,
    annualization_days: int,
    fast_span: int = CARVER_FAST_VOL_SPAN,
    slow_years: int = CARVER_SLOW_VOL_YEARS,
    slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
) -> dict:
    """Calculate Carver mixed point volatility from ordinary price bars."""
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")
    if bars is None or bars.height == 0:
        raise ValueError("price history is empty")

    date_col = "ts_event" if "ts_event" in bars.columns else "date"
    if date_col not in bars.columns or "close" not in bars.columns:
        raise ValueError("price history requires date/ts_event and close columns")

    clean = (
        bars.select(pl.col(date_col).alias("ts_event"), pl.col("close").cast(pl.Float64))
        .filter(pl.col("close").is_finite() & (pl.col("close") > 0))
        .unique(subset=["ts_event"], keep="last")
        .sort("ts_event")
    )
    mixed = carver_mixed_point_volatility(
        clean.with_columns(pl.col("close").diff().alias("pt_change_1d")),
        point_change_col="pt_change_1d",
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )
    last = mixed.tail(1)
    mixed_point_vol = _positive_finite(last["mixed_point_vol"][0])
    if mixed_point_vol is None:
        raise ValueError("latest price history does not produce a valid volatility estimate")

    returns = clean["close"].pct_change().drop_nulls().tail(fast_span)
    zero_return_fraction = (
        int((returns == 0).sum()) / returns.len()
        if returns.len() else None
    )

    start = clean["ts_event"].min()
    end = clean["ts_event"].max()
    return {
        "history_rows": clean.height,
        "history_start": start,
        "history_end": end,
        "fast_point_vol": last["fast_point_vol"][0],
        "slow_point_vol": last["slow_point_vol"][0],
        "mixed_point_vol": mixed_point_vol,
        "vol_observations": last["vol_observations"][0],
        "slow_history_years": last["slow_history_years"][0],
        "fast_vol_span": last["fast_vol_span"][0],
        "slow_vol_span": last["slow_vol_span"][0],
        "slow_vol_weight": last["slow_vol_weight"][0],
        "zero_return_fraction": zero_return_fraction,
        "reference_price": clean["close"][-1],
    }


def _recent_dated_return_volatility(
    ib,
    contract,
    *,
    duration: str,
    use_rth: bool,
    fast_span: int,
    volume_lookback_days: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
    min_samples: int = CARVER_VOL_MIN_SAMPLES,
) -> dict[str, object]:
    """Estimate recent fast return volatility from the executable contract."""
    bars = ib.get_historical_bars(
        contract,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=use_rth,
    )
    if bars is None or bars.height == 0:
        raise ValueError("dated contract price history is empty")
    date_col = "ts_event" if "ts_event" in bars.columns else "date"
    if date_col not in bars.columns or "close" not in bars.columns:
        raise ValueError("dated contract history requires date and close")
    clean = (
        bars.select(
            pl.col(date_col).alias("ts_event"),
            pl.col("close").cast(pl.Float64),
        )
        .filter(pl.col("close").is_finite() & (pl.col("close") > 0))
        .unique(subset=["ts_event"], keep="last")
        .sort("ts_event")
        .with_columns(pl.col("close").pct_change().alias("ret_1d"))
        .with_columns(
            pl.col("ret_1d")
            .ewm_std(span=fast_span, adjust=True, min_samples=min_samples)
            .alias("fast_return_vol")
        )
    )
    fast_return_vol = _positive_finite(clean.tail(1)["fast_return_vol"][0])
    if fast_return_vol is None:
        raise ValueError("dated contract history does not produce fast return vol")
    result = {
        "ib_recent_fast_return_vol": fast_return_vol,
        "ib_recent_vol_observations": clean.get_column("ret_1d").drop_nulls().len(),
        "ib_recent_vol_start": clean.get_column("ts_event").min(),
        "ib_recent_vol_end": clean.get_column("ts_event").max(),
        "ib_recent_vol_duration": duration,
    }
    try:
        result.update(_volume_stats_from_trade_bars(
            bars,
            lookback_days=volume_lookback_days,
            source="ib_dated_contract",
        ))
    except ValueError as exc:
        result["volume_error"] = str(exc)
    return result


def _volume_stats_from_trade_bars(
    bars: pl.DataFrame,
    *,
    lookback_days: int,
    source: str,
) -> dict[str, object]:
    """Return Carver's mean volume over the latest daily contract bars."""
    if lookback_days <= 0:
        raise ValueError("volume lookback must be positive")
    if bars is None or bars.height == 0:
        raise ValueError("dated contract volume history is empty")
    date_col = "ts_event" if "ts_event" in bars.columns else "date"
    if date_col not in bars.columns or "volume" not in bars.columns:
        raise ValueError("dated contract history requires date and volume")
    clean = (
        bars.select(
            pl.col(date_col).alias("ts_event"),
            pl.col("volume").cast(pl.Float64, strict=False),
        )
        .filter(pl.col("volume").is_finite() & (pl.col("volume") >= 0))
        .unique(subset=["ts_event"], keep="last")
        .sort("ts_event")
        .tail(lookback_days)
    )
    if clean.is_empty():
        raise ValueError("dated contract history has no valid volume")
    return {
        "avg_daily_volume_contracts": clean.get_column("volume").mean(),
        "volume_observations": clean.height,
        "volume_start": clean.get_column("ts_event").min(),
        "volume_end": clean.get_column("ts_event").max(),
        "volume_source": source,
    }


def _recent_dated_volume(
    ib,
    contract,
    *,
    duration: str,
    use_rth: bool,
    lookback_days: int,
) -> dict[str, object]:
    """Request daily TRADES bars solely for the executable contract's volume."""
    bars = ib.get_historical_bars(
        contract,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=use_rth,
    )
    return _volume_stats_from_trade_bars(
        bars,
        lookback_days=lookback_days,
        source="ib_dated_contract",
    )


def _blend_recent_and_historical_return_volatility(
    recent: dict[str, object],
    historical: dict[str, object],
    *,
    slow_weight: float,
) -> dict[str, object]:
    """Blend current-contract fast return vol with Carver's slow anchor."""
    if not 0 <= slow_weight <= 1:
        raise ValueError("slow_weight must be between zero and one")
    fast_return_vol = _positive_finite(recent.get("ib_recent_fast_return_vol"))
    slow_point_vol = _positive_finite(historical.get("slow_point_vol"))
    reference_price = _positive_finite(historical.get("reference_price"))
    if fast_return_vol is None or slow_point_vol is None or reference_price is None:
        raise ValueError("recent fast and historical slow volatility are required")
    slow_return_vol = slow_point_vol / reference_price
    mixed_return_vol = (
        (1.0 - slow_weight) * fast_return_vol
        + slow_weight * slow_return_vol
    )
    return {
        **recent,
        "carver_slow_return_vol": slow_return_vol,
        "risk_daily_return_vol": mixed_return_vol,
        "risk_return_vol_source": "ib_dated_fast_carver_slow",
    }


def volatility_from_pysystemtrade_history(
    provider: PysystemtradeHistoryProvider,
    instrument_code: str,
    *,
    annualization_days: int,
    fast_span: int = CARVER_FAST_VOL_SPAN,
    slow_years: int = CARVER_SLOW_VOL_YEARS,
    slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
) -> dict:
    """Calculate mixed point vol from the full roll-neutral Carver history."""
    history = provider.load(instrument_code)
    bars = history.panama_bars()
    mixed = carver_mixed_point_volatility(
        bars,
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )
    last = mixed.tail(1)
    value = _positive_finite(last["mixed_point_vol"][0])
    if value is None:
        raise ValueError(f"{instrument_code}: no valid mixed point volatility")
    changes = bars["pt_change_1d"].drop_nulls().tail(fast_span)
    if history.carry.height:
        reference_price = history.carry.sort("trade_date")["current_price"][-1]
    else:
        reference_price = history.marks.sort("trade_date")["mark_price"][-1]
    return {
        "history_rows": bars.height,
        "history_start": bars["ts_event"].min(),
        "history_end": bars["ts_event"].max(),
        "fast_point_vol": last["fast_point_vol"][0],
        "slow_point_vol": last["slow_point_vol"][0],
        "mixed_point_vol": value,
        "vol_observations": last["vol_observations"][0],
        "slow_history_years": last["slow_history_years"][0],
        "fast_vol_span": last["fast_vol_span"][0],
        "slow_vol_span": last["slow_vol_span"][0],
        "slow_vol_weight": last["slow_vol_weight"][0],
        "zero_return_fraction": (
            int((changes == 0).sum()) / changes.len() if changes.len() else None
        ),
        "reference_price": reference_price,
    }


def build_cost_risk_row(
    *,
    symbol: str,
    signal_symbol: str,
    contract_id: str,
    expiration: str,
    current_price: float,
    multiplier: float,
    commission_per_side: float,
    mixed_point_vol: float,
    annualization_days: int,
    history_rows: int,
    history_start,
    history_end,
    vol_source: str = "dated_contract",
    vol_reference_price: Optional[float] = None,
    daily_return_vol_override: Optional[float] = None,
    fast_point_vol: Optional[float] = None,
    slow_point_vol: Optional[float] = None,
    vol_observations: Optional[int] = None,
    slow_history_years: Optional[float] = None,
    fast_vol_span: int = CARVER_FAST_VOL_SPAN,
    slow_vol_span: Optional[int] = None,
    slow_vol_weight: float = CARVER_SLOW_VOL_WEIGHT,
    zero_return_fraction: Optional[float] = None,
    currency: str = "USD",
    fx_to_usd: float = 1.0,
    fx_asof=None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    price_source: str = "dated_contract",
    quote_timestamp_ct: Optional[str] = None,
    quote_quality: str = "live_snapshot",
) -> dict:
    """Calculate notional, dollar vol, and risk-scaled execution costs.

    A valid live bid/ask is interpreted as a full quoted width. Expected
    one-way crossing cost is half that width from mid. Commission is per
    contract per side. Historical point volatility is converted to return
    volatility and applied to the current IB price before calculating dollar
    volatility, so a stale history cutoff does not freeze present contract
    risk at the old price level.
    """
    price = _positive_finite(current_price)
    mult = _positive_finite(multiplier)
    point_vol = _positive_finite(mixed_point_vol)
    fx = _positive_finite(fx_to_usd)
    commission = _positive_finite(commission_per_side)
    if price is None or mult is None or point_vol is None or fx is None:
        raise ValueError(
            "current_price, multiplier, mixed_point_vol, and fx_to_usd must be positive"
        )
    # A genuinely zero commission is allowed even though _positive_finite
    # intentionally rejects zero for prices, multipliers, and vol.
    if commission is None:
        try:
            commission = float(commission_per_side)
        except (TypeError, ValueError) as exc:
            raise ValueError("commission_per_side must be finite and non-negative") from exc
        if not math.isfinite(commission) or commission < 0:
            raise ValueError("commission_per_side must be finite and non-negative")

    bid_value = _positive_finite(bid)
    ask_value = _positive_finite(ask)
    spread_valid = bid_value is not None and ask_value is not None and ask_value >= bid_value
    full_spread_points = ask_value - bid_value if spread_valid else None
    one_way_spread_cash_native = full_spread_points * mult / 2.0 if spread_valid else None

    notional_native = price * mult
    notional = notional_native * fx
    vol_reference = _positive_finite(vol_reference_price) or price
    daily_return_vol = (
        _positive_finite(daily_return_vol_override)
        or point_vol / abs(vol_reference)
    )
    annual_return_vol = daily_return_vol * math.sqrt(annualization_days)
    price_scale_to_vol_reference = price / vol_reference
    current_mixed_point_vol = daily_return_vol * price
    daily_dollar_vol = current_mixed_point_vol * mult * fx
    annual_dollar_vol = daily_dollar_vol * math.sqrt(annualization_days)
    one_way_commission_native = commission
    one_way_commission = one_way_commission_native * fx
    one_way_spread_cash = (
        one_way_spread_cash_native * fx if one_way_spread_cash_native is not None else None
    )
    one_way_total = (
        one_way_commission + one_way_spread_cash
        if one_way_spread_cash is not None else None
    )

    return {
        "symbol": symbol,
        "signal_symbol": signal_symbol,
        "contract_id": contract_id,
        "expiration": expiration,
        "price": price,
        "price_source": price_source,
        "currency": currency,
        "fx_to_usd": fx,
        "fx_asof": fx_asof,
        "multiplier": mult,
        "notional_native_per_contract": notional_native,
        "notional_per_contract": notional,
        "fast_point_vol": fast_point_vol,
        "slow_point_vol": slow_point_vol,
        "mixed_point_vol": point_vol,
        "vol_reference_price": vol_reference,
        "price_scale_to_vol_reference": price_scale_to_vol_reference,
        "current_mixed_point_vol": current_mixed_point_vol,
        "daily_return_vol": daily_return_vol,
        "annual_return_vol": annual_return_vol,
        "daily_dollar_vol_per_contract": daily_dollar_vol,
        "annual_dollar_vol_per_contract": annual_dollar_vol,
        "commission_per_side": one_way_commission,
        "bid": bid_value,
        "ask": ask_value,
        "full_spread_points": full_spread_points,
        "one_way_spread_cash": one_way_spread_cash,
        "one_way_total_cost": one_way_total,
        "one_way_cost_per_annual_dollar_vol": (
            one_way_total / annual_dollar_vol if one_way_total is not None else None
        ),
        "one_way_cost_bps_notional": (
            one_way_total / notional * 10_000.0 if one_way_total is not None else None
        ),
        "spread_quality": quote_quality if spread_valid else "unknown_no_bid_ask",
        "quote_timestamp_ct": quote_timestamp_ct,
        "annualization_days": annualization_days,
        "history_rows": history_rows,
        "history_start": history_start,
        "history_end": history_end,
        "vol_source": vol_source,
        "vol_observations": vol_observations,
        "slow_history_years": slow_history_years,
        "fast_vol_span": fast_vol_span,
        "slow_vol_span": slow_vol_span,
        "slow_vol_weight": slow_vol_weight,
        "zero_return_fraction": zero_return_fraction,
    }


def _latest_dated_mark(
    ib,
    contract,
    *,
    market_data_type: str,
) -> tuple[Optional[float], Optional[str]]:
    """Get a recent mark even when delayed snapshots are unavailable."""
    request_order = (
        ("BID_ASK", "1 D", "5 mins", True),
        ("TRADES", "5 D", "1 day", False),
    )
    if market_data_type != "delayed":
        request_order = tuple(reversed(request_order))
    for what_to_show, duration, bar_size, use_rth in request_order:
        try:
            bars = ib.get_historical_bars(
                contract,
                duration=duration,
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
            )
        except Exception as exc:
            log.debug(
                "latest_dated_mark_failed contract=%s source=%s reason=%s",
                contract,
                what_to_show,
                exc,
            )
            continue
        if bars is None or bars.height == 0:
            continue
        date_col = "date" if "date" in bars.columns else None
        latest = bars.sort(date_col).tail(1) if date_col else bars.tail(1)
        if what_to_show == "TRADES" and "close" in latest.columns:
            value = _positive_finite(latest["close"][0])
            if value is not None:
                return value, "dated_contract_daily_close"
        if {"open", "close"}.issubset(latest.columns):
            bid = _positive_finite(latest["open"][0])
            ask = _positive_finite(latest["close"][0])
            if bid is not None and ask is not None and ask >= bid:
                return (bid + ask) / 2.0, "dated_contract_historical_bid_ask_mid"
    return None, None


def _spread_stats_from_bid_ask_bars(
    bars: pl.DataFrame,
    *,
    duration: str,
    bar_size: str,
    source: str,
) -> dict[str, object]:
    """Summarize IB BID_ASK bars, whose open/close are average bid/ask."""
    if bars is None or bars.height == 0:
        raise ValueError("BID_ASK history is empty")
    required = {"open", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"BID_ASK history missing columns: {sorted(missing)}")
    clean = (
        bars.with_columns(
            (pl.col("close").cast(pl.Float64) - pl.col("open").cast(pl.Float64))
            .alias("spread_points")
        )
        .filter(
            pl.col("spread_points").is_finite()
            & (pl.col("spread_points") >= 0)
        )
    )
    if clean.height == 0:
        raise ValueError("BID_ASK history has no valid non-negative spreads")
    date_col = "date" if "date" in clean.columns else None
    spread_start = clean.get_column(date_col).min() if date_col else None
    spread_end = clean.get_column(date_col).max() if date_col else None
    return {
        "ib_historical_spread_source": source,
        "ib_historical_spread_duration": duration,
        "ib_historical_spread_bar_size": bar_size,
        "ib_historical_spread_observations": clean.height,
        # IB timestamps carry each exchange's local timezone. Report rows
        # span exchanges, and Polars cannot construct one datetime column
        # from values such as MET and US/Eastern. Central-time ISO text is
        # portable across CSV output and aligns report inspection with CME
        # hours while preserving the absolute instant and UTC offset.
        "ib_historical_spread_start": _timestamp_as_central_iso(spread_start),
        "ib_historical_spread_end": _timestamp_as_central_iso(spread_end),
        "ib_historical_spread_mean_points": clean.get_column(
            "spread_points"
        ).mean(),
        "ib_historical_spread_median_points": clean.get_column(
            "spread_points"
        ).median(),
        "ib_historical_spread_p90_points": clean.get_column(
            "spread_points"
        ).quantile(0.90),
    }


def _timestamp_as_central_iso(value: object) -> Optional[str]:
    """Return a report-safe Chicago timestamp for mixed exchange timezones."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=REPORT_TIMEZONE)
        else:
            value = value.astimezone(REPORT_TIMEZONE)
        return value.isoformat(timespec="seconds")
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _historical_bid_ask_spread(
    ib,
    contract,
    *,
    duration: str,
    use_rth: bool,
    source: str,
    market_data_type: str = "live",
    bar_sizes: Optional[tuple[str, ...]] = None,
) -> dict[str, object]:
    """Try bounded BID_ASK requests without cascading after a timeout."""
    attempts: list[str] = []
    failures: list[str] = []
    request_plan = (
        tuple((bar_size, duration) for bar_size in bar_sizes)
        if bar_sizes is not None
        else _spread_request_plan(duration, market_data_type)
    )
    for bar_size, attempt_duration in request_plan:
        attempt_label = f"{bar_size}@{attempt_duration}"
        attempts.append(attempt_label)
        request_started = time.monotonic()
        try:
            bars = ib.get_historical_bars(
                contract,
                duration=attempt_duration,
                bar_size=bar_size,
                what_to_show="BID_ASK",
                use_rth=use_rth,
            )
            request_elapsed = time.monotonic() - request_started
            if (
                (bars is None or bars.height == 0)
                and request_elapsed
                >= IB_INSYNC_HISTORICAL_TIMEOUT_FLOOR_SECONDS
            ):
                raise TimeoutError(
                    "IB historical request returned empty after "
                    f"{request_elapsed:.1f}s; treating as ib_insync timeout"
                )
            result = _spread_stats_from_bid_ask_bars(
                bars,
                duration=attempt_duration,
                bar_size=bar_size,
                source=source,
            )
            result["ib_historical_spread_attempts"] = ",".join(attempts)
            result["ib_historical_spread_failures"] = ";".join(failures)
            return result
        except Exception as exc:
            failures.append(f"{attempt_label}:{exc}")
            log.debug(
                "historical_spread_attempt_failed contract=%s source=%s "
                "duration=%s bar_size=%s reason=%s",
                contract,
                source,
                attempt_duration,
                bar_size,
                exc,
            )
            if _historical_request_timed_out(exc):
                break
    return {
        "ib_historical_spread_source": None,
        "ib_historical_spread_duration": duration,
        "ib_historical_spread_bar_size": None,
        "ib_historical_spread_observations": 0,
        "ib_historical_spread_start": None,
        "ib_historical_spread_end": None,
        "ib_historical_spread_mean_points": None,
        "ib_historical_spread_median_points": None,
        "ib_historical_spread_p90_points": None,
        "ib_historical_spread_attempts": ",".join(attempts),
        "ib_historical_spread_failures": ";".join(failures),
    }


def _skipped_historical_spread(
    *,
    duration: str,
    reason: str,
) -> dict[str, object]:
    return {
        "ib_historical_spread_source": None,
        "ib_historical_spread_duration": duration,
        "ib_historical_spread_bar_size": None,
        "ib_historical_spread_observations": 0,
        "ib_historical_spread_start": None,
        "ib_historical_spread_end": None,
        "ib_historical_spread_mean_points": None,
        "ib_historical_spread_median_points": None,
        "ib_historical_spread_p90_points": None,
        "ib_historical_spread_attempts": "",
        "ib_historical_spread_failures": reason,
    }


def load_latest_fx_to_usd(db_path: Path | str) -> dict[str, dict]:
    """Reference FX conversions from the immutable Carver import."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        fx = con.execute(
            """
            SELECT currency_pair, source_timestamp, price
            FROM raw.fx_prices
            QUALIFY row_number() OVER (
                PARTITION BY currency_pair ORDER BY source_timestamp DESC
            ) = 1
            """
        ).pl()
    finally:
        con.close()
    result = {
        "USD": {
            "rate": 1.0,
            "asof": None,
            "source": "identity",
            "pair": "USDUSD",
            "market_data_type": None,
            "error": None,
            "reference_rate": 1.0,
            "reference_asof": None,
            "reference_source": "identity",
        }
    }
    for row in fx.iter_rows(named=True):
        pair = row["currency_pair"]
        if pair.endswith("USD"):
            currency = pair[:-3]
            result[currency] = {
                "rate": row["price"],
                "asof": row["source_timestamp"],
                "source": "pysystemtrade_reference",
                "pair": pair,
                "market_data_type": None,
                "error": None,
                "reference_rate": row["price"],
                "reference_asof": row["source_timestamp"],
                "reference_source": "pysystemtrade_reference",
            }
    return result


def load_current_fx_to_usd(
    ib,
    currencies: set[str],
    reference_fx: dict[str, dict],
    *,
    market_data_type: str,
    quote_wait_seconds: float,
) -> dict[str, dict]:
    """Select current IB FX, falling back explicitly to Carver reference FX."""
    selected: dict[str, dict] = {}
    for currency in sorted(currencies | {"USD"}):
        reference = reference_fx.get(currency)
        if currency == "USD":
            selected[currency] = dict(reference or {
                "rate": 1.0,
                "asof": None,
                "source": "identity",
                "pair": "USDUSD",
                "market_data_type": None,
                "error": None,
                "reference_rate": 1.0,
                "reference_asof": None,
                "reference_source": "identity",
            })
            continue

        try:
            ib_fx = ib.get_fx_to_usd(
                currency,
                wait_seconds=quote_wait_seconds,
                market_data_type=market_data_type,
            )
            current = {
                "rate": ib_fx["rate"],
                "asof": ib_fx["queried_at_utc"],
                "source": ib_fx["source"],
                "pair": ib_fx["pair"],
                "market_data_type": ib_fx["market_data_type"],
                "error": None,
                "reference_rate": (
                    reference.get("reference_rate", reference.get("rate"))
                    if reference is not None
                    else None
                ),
                "reference_asof": (
                    reference.get("reference_asof", reference.get("asof"))
                    if reference is not None
                    else None
                ),
                "reference_source": (
                    reference.get("reference_source", reference.get("source"))
                    if reference is not None
                    else None
                ),
            }
            selected[currency] = current
            log.info(
                "current_fx selected currency=%s pair=%s rate_to_usd=%.8f "
                "market_data_type=%s source=%s",
                currency,
                current["pair"],
                current["rate"],
                current["market_data_type"],
                current["source"],
            )
        except Exception as exc:
            log.debug(
                "current_fx_rejected currency=%s reason=%s",
                currency,
                exc,
            )
            if reference is None:
                continue
            fallback = dict(reference)
            fallback.update({
                "source": "pysystemtrade_reference_fallback",
                "market_data_type": None,
                "error": str(exc),
            })
            selected[currency] = fallback
            log.info(
                "current_fx fallback currency=%s rate_to_usd=%.8f asof=%s reason=%s",
                currency,
                fallback["rate"],
                fallback.get("asof"),
                fallback["error"],
            )
    return selected


def _fx_report_fields(fx_info: dict[str, object]) -> dict[str, object]:
    return {
        "cur_fx_source": fx_info.get("source"),
        "cur_fx_pair": fx_info.get("pair"),
        "cur_fx_market_data_type": fx_info.get("market_data_type"),
        "cur_fx_error": fx_info.get("error"),
        "ref_fx_to_usd": fx_info.get("reference_rate", fx_info.get("rate")),
        "ref_fx_asof": fx_info.get("reference_asof", fx_info.get("asof")),
        "ref_fx_source": fx_info.get("reference_source", fx_info.get("source")),
    }


def _history_volatility(
    ib,
    instr: dict,
    *,
    vol_source: str,
    duration: str,
    use_rth: bool,
    fast_span: int,
    slow_years: int,
    slow_weight: float,
    pysystemtrade_provider: Optional[PysystemtradeHistoryProvider],
    dated_contract=None,
) -> tuple[int, dict]:
    symbol = instr["symbol"]
    if vol_source == "pysystemtrade":
        if pysystemtrade_provider is None:
            raise ValueError("pysystemtrade vol source requires a provider")
        annualization_days = CARVER_BUSINESS_DAYS_PER_YEAR
        return annualization_days, volatility_from_pysystemtrade_history(
            pysystemtrade_provider,
            instr.get("instrument_code", symbol),
            annualization_days=annualization_days,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
        )

    if vol_source == "continuous":
        signal_symbol = resolve_signal_symbol(instr)
        vol_contract = IBPySync.cont_future(
            signal_symbol,
            exchange=instr.get("exchange", "CME"),
            currency=_ib_contract_currency(instr),
        )
        ib.qualify_contracts(vol_contract)
    elif vol_source == "dated":
        if dated_contract is None:
            raise ValueError("dated vol source requires the resolved dated contract")
        vol_contract = dated_contract
    else:
        raise ValueError(f"Unknown vol_source {vol_source!r}")

    bars = ib.get_historical_bars(
        vol_contract,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=use_rth,
    )
    annualization_days = int(instr.get("annualization_days", resolve_annualization_days(symbol)))
    return annualization_days, volatility_from_bars(
        bars,
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )


def diagnose_instrument(
    ib,
    instr: dict,
    *,
    duration: str,
    spread_duration: str,
    spread_use_rth: bool,
    volume_duration: str = DEFAULT_VOLUME_DURATION,
    volume_lookback_days: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
    min_days: int,
    contract_details_timeout: float,
    quote_wait_seconds: float,
    use_rth: bool,
    vol_source: str,
    fast_span: int,
    slow_years: int,
    slow_weight: float,
    market_data_type: str,
    pysystemtrade_provider: Optional[PysystemtradeHistoryProvider],
    fx_by_currency: dict[str, dict],
) -> dict:
    symbol = instr["symbol"]
    try:
        spec = get_spec(symbol)
    except KeyError:
        # Explicit JSON configs may describe an IBKR contract outside the
        # repository registry.  Such rows must carry their own executable
        # multiplier and commission; never invent either value.
        spec = {}
    multiplier = instr.get("multiplier", spec.get("multiplier"))
    commission = instr.get("commission", spec.get("commission"))
    if multiplier is None or commission is None:
        raise ValueError(
            f"{symbol}: multiplier and commission are required in the registry or JSON config"
        )
    signal_symbol = resolve_signal_symbol(instr)
    retired_reason = RETIRED_IB_INSTRUMENTS.get(
        instr.get("instrument_code", symbol)
    )
    if retired_reason:
        raise RuntimeError(f"{symbol}: {retired_reason}")
    annualization_days = vol = None
    if vol_source == "pysystemtrade":
        annualization_days, vol = _history_volatility(
            ib,
            instr,
            vol_source=vol_source,
            duration=duration,
            use_rth=use_rth,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
            pysystemtrade_provider=pysystemtrade_provider,
        )
    if instr.get("ignore_weekly"):
        raise RuntimeError(
            f"{symbol}: IB mapping requires specialised weekly/daily expiry filtering; "
            "candidate mapping retained but live contract selection skipped"
        )
    contract = _resolve_contract(
        ib,
        instr,
        min_days,
        contract_details_timeout=contract_details_timeout,
    )
    quote = ib.get_quote(
        contract,
        wait_seconds=quote_wait_seconds,
        market_data_type=market_data_type,
        generic_ticks="",
    )
    resolved_market_data_type = quote["market_data_type"]
    quote_source = resolved_market_data_type.replace("-", "_")
    historical_requests_allowed = ib.quote_allows_historical(quote)
    historical_market_data_type = None
    if historical_requests_allowed:
        historical_market_data_type = ib.select_historical_market_data_type(quote)
    if quote["mid"] is not None:
        price = quote["mid"]
        price_source = f"{quote_source}_bid_ask_mid"
    elif quote["last"] is not None:
        price = quote["last"]
        price_source = f"{quote_source}_last"
    elif quote["close"] is not None:
        price = quote["close"]
        price_source = f"{quote_source}_previous_close"
    elif historical_requests_allowed:
        price, price_source = _latest_dated_mark(
            ib,
            contract,
            market_data_type=historical_market_data_type or "live",
        )
    else:
        price = None
        price_source = "unavailable_no_historical_entitlement"
    if price is None:
        raise RuntimeError(f"{symbol}: no usable price for resolved dated contract")

    if vol_source == "continuous" and not historical_requests_allowed:
        raise RuntimeError(
            f"{symbol}: --vol-source continuous requires live or delayed "
            "IB historical data"
        )
    if vol_source == "continuous":
        annualization_days, vol = _history_volatility(
            ib,
            instr,
            vol_source=vol_source,
            duration=duration,
            use_rth=use_rth,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
            pysystemtrade_provider=pysystemtrade_provider,
        )

    risk_vol: dict[str, object] = {}
    if vol_source == "pysystemtrade":
        if historical_requests_allowed:
            try:
                recent_vol = _recent_dated_return_volatility(
                    ib,
                    contract,
                    duration=duration,
                    use_rth=use_rth,
                    fast_span=fast_span,
                    volume_lookback_days=volume_lookback_days,
                )
                risk_vol = _blend_recent_and_historical_return_volatility(
                    recent_vol,
                    vol,
                    slow_weight=slow_weight,
                )
            except Exception as exc:
                risk_vol = {"ib_recent_vol_error": str(exc)}
                log.debug(
                    "recent_dated_vol_fallback symbol=%s reason=%s",
                    symbol,
                    exc,
                )
        else:
            risk_vol = {
                "ib_recent_vol_error": "skipped_no_historical_entitlement",
            }
        if "risk_daily_return_vol" not in risk_vol:
            risk_vol.update({
                "ib_recent_fast_return_vol": None,
                "ib_recent_vol_observations": 0,
                "ib_recent_vol_start": None,
                "ib_recent_vol_end": None,
                "ib_recent_vol_duration": duration,
                "carver_slow_return_vol": (
                    vol["slow_point_vol"] / vol["reference_price"]
                    if _positive_finite(vol.get("slow_point_vol")) is not None
                    and _positive_finite(vol.get("reference_price")) is not None
                    else None
                ),
                "risk_daily_return_vol": (
                    vol["mixed_point_vol"] / vol["reference_price"]
                ),
                "risk_return_vol_source": "carver_mixed_scaled_fallback",
            })

    if (
        historical_requests_allowed
        and "avg_daily_volume_contracts" not in risk_vol
        and "volume_error" not in risk_vol
    ):
        try:
            risk_vol.update(_recent_dated_volume(
                ib,
                contract,
                duration=volume_duration,
                use_rth=use_rth,
                lookback_days=volume_lookback_days,
            ))
        except Exception as exc:
            risk_vol["volume_error"] = str(exc)
            log.debug(
                "dated_volume_unavailable symbol=%s reason=%s",
                symbol,
                exc,
            )

    if historical_requests_allowed:
        spread_stats = _historical_bid_ask_spread(
            ib,
            contract,
            duration=spread_duration,
            use_rth=spread_use_rth,
            source="ib_dated_contract",
            market_data_type=historical_market_data_type or "live",
        )
    else:
        spread_stats = _skipped_historical_spread(
            duration=spread_duration,
            reason="skipped_no_historical_entitlement",
        )
    dated_spread_timed_out = _historical_request_timed_out(
        spread_stats.get("ib_historical_spread_failures")
    )
    if (
        historical_requests_allowed
        and not dated_spread_timed_out
        and spread_stats["ib_historical_spread_median_points"] is None
    ):
        try:
            continuous = IBPySync.cont_future(
                instr.get("ib_symbol") or getattr(contract, "symbol", symbol),
                exchange=instr.get("exchange", "CME"),
                currency=_ib_contract_currency(instr, contract),
                multiplier=_format_ib_multiplier(
                    instr.get("ib_multiplier") or multiplier
                ),
            )
            qualified = ib.qualify_contracts(continuous)
            if qualified:
                continuous = qualified[0]
            continuous_stats = _historical_bid_ask_spread(
                ib,
                continuous,
                duration=spread_duration,
                use_rth=spread_use_rth,
                source="ib_continuous_fallback",
                market_data_type=historical_market_data_type or "live",
            )
            if (
                continuous_stats["ib_historical_spread_median_points"]
                is not None
            ):
                spread_stats = continuous_stats
        except Exception as exc:
            log.debug(
                "continuous_spread_fallback_failed symbol=%s reason=%s",
                symbol,
                exc,
            )
    if vol_source == "dated" and not historical_requests_allowed:
        raise RuntimeError(
            f"{symbol}: --vol-source dated requires IB historical entitlement"
        )
    if vol_source == "dated":
        annualization_days, vol = _history_volatility(
            ib,
            instr,
            vol_source=vol_source,
            duration=duration,
            use_rth=use_rth,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
            pysystemtrade_provider=pysystemtrade_provider,
            dated_contract=contract,
        )
    assert annualization_days is not None and vol is not None

    expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
    local_symbol = getattr(contract, "localSymbol", "") or ""
    con_id = getattr(contract, "conId", "") or ""
    contract_id = local_symbol or f"{getattr(contract, 'symbol', symbol)}:{con_id}"
    currency = instr.get("currency", "USD")
    fx_info = fx_by_currency.get(currency)
    if fx_info is None:
        raise ValueError(f"{symbol}: no {currency}USD conversion available")
    row = build_cost_risk_row(
        symbol=symbol,
        signal_symbol=signal_symbol,
        contract_id=contract_id,
        expiration=expiry,
        current_price=price,
        multiplier=multiplier,
        commission_per_side=commission,
        mixed_point_vol=vol["mixed_point_vol"],
        annualization_days=annualization_days,
        history_rows=vol["history_rows"],
        history_start=vol["history_start"],
        history_end=vol["history_end"],
        vol_source={
            "pysystemtrade": "pysystemtrade_roll_neutral",
            "continuous": "ib_continuous_explicit",
            "dated": "ib_dated_contract",
        }[vol_source],
        daily_return_vol_override=risk_vol.get("risk_daily_return_vol"),
        fast_point_vol=vol["fast_point_vol"],
        slow_point_vol=vol["slow_point_vol"],
        vol_observations=vol["vol_observations"],
        slow_history_years=vol["slow_history_years"],
        fast_vol_span=vol["fast_vol_span"],
        slow_vol_span=vol["slow_vol_span"],
        slow_vol_weight=vol["slow_vol_weight"],
        zero_return_fraction=vol["zero_return_fraction"],
        currency=currency,
        fx_to_usd=fx_info["rate"],
        fx_asof=fx_info["asof"],
        bid=quote["bid"],
        ask=quote["ask"],
        price_source=price_source,
        quote_timestamp_ct=datetime.now(REPORT_TIMEZONE).isoformat(
            timespec="seconds"
        ),
        quote_quality=f"{resolved_market_data_type}_snapshot",
        vol_reference_price=vol["reference_price"],
    )
    row.update(_fx_report_fields(fx_info))
    historical_full_spread = _nonnegative_finite(
        spread_stats.get("ib_historical_spread_median_points")
    )
    snapshot_full_spread = _nonnegative_finite(row.get("full_spread_points"))
    if historical_full_spread is not None:
        selected_one_way_spread = historical_full_spread / 2.0
        selected_spread_source = spread_stats["ib_historical_spread_source"]
    elif snapshot_full_spread is not None:
        selected_one_way_spread = snapshot_full_spread / 2.0
        selected_spread_source = "ib_snapshot"
    else:
        selected_one_way_spread = None
        selected_spread_source = None
    row.update({
        "ib_symbol": instr.get("ib_symbol", symbol),
        "ib_exchange": instr.get("exchange"),
        "ib_currency": instr.get("ib_currency"),
        "ib_multiplier": (
            instr.get("ib_multiplier")
            or getattr(contract, "multiplier", None)
        ),
        "price_magnifier": instr.get("price_magnifier"),
        "mapping_status": instr.get("mapping_status", "local_registry"),
        "ib_availability": "contract_qualified",
        "quote_market_data_type": resolved_market_data_type,
        "quote_market_data_attempts": quote["market_data_attempts"],
        "quote_error_codes": ",".join(str(code) for code in quote["error_codes"]),
        "quote_selected_error_codes": ",".join(
            str(code) for code in quote.get("selected_error_codes", [])
        ),
        "ib_historical_requests_allowed": historical_requests_allowed,
        "ib_historical_market_data_type": historical_market_data_type,
        "carver_spread_points": instr.get("carver_spread_points"),
        **risk_vol,
        **spread_stats,
        "selected_one_way_spread_points": selected_one_way_spread,
        "selected_spread_source": selected_spread_source,
        **_pooling_report_identity(instr),
    })
    return row


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--instruments",
        default="all-pysystemtrade",
        help="Carver instrument codes, local traded symbols, JSON config, or all-pysystemtrade (default)",
    )
    parser.add_argument("--duration", default=DEFAULT_DURATION,
                        help="IB history request duration for the selected vol source (default: %(default)s)")
    parser.add_argument("--fast-vol-span", type=int, default=CARVER_FAST_VOL_SPAN,
                        help="Fast EWM point-vol span (default: %(default)s, Advanced Futures Trading)")
    parser.add_argument("--slow-vol-years", type=int, default=CARVER_SLOW_VOL_YEARS)
    parser.add_argument("--slow-vol-weight", type=float, default=CARVER_SLOW_VOL_WEIGHT)
    parser.add_argument(
        "--cost-ewmac-fast-spans",
        type=_parse_fast_spans,
        default=DEFAULT_COST_EWMAC_FAST_SPANS,
        help=(
            "Comma-separated EWMAC fast spans; slow spans are four times fast "
            "(default: 4,8,16,32,64)"
        ),
    )
    parser.add_argument(
        "--cost-ewmac-vol-span",
        type=int,
        default=DEFAULT_COST_EWMAC_VOL_SPAN,
        help="Fast span of the EWMAC mixed point-vol denominator",
    )
    parser.add_argument(
        "--cost-ewmac-vol-slow-years",
        type=int,
        default=CARVER_SLOW_VOL_YEARS,
    )
    parser.add_argument(
        "--cost-ewmac-vol-slow-weight",
        type=float,
        default=CARVER_SLOW_VOL_WEIGHT,
    )
    parser.add_argument(
        "--cost-ewmac-vol-min-samples",
        type=int,
        default=CARVER_VOL_MIN_SAMPLES,
    )
    parser.add_argument(
        "--cost-ewmac-scalar-min-periods",
        type=int,
        default=EWMAC_SCALAR_MIN_PERIODS,
    )
    parser.add_argument(
        "--rule-cost-limit-sr",
        type=float,
        default=DEFAULT_RULE_COST_LIMIT_SR,
        help="Maximum total annual SR cost for each EWMAC rule",
    )
    parser.add_argument(
        "--skip-ewmac-cost-baseline",
        action="store_true",
        help="Skip the reviewed-pool EWMAC Sharpe and turnover calculation",
    )
    parser.add_argument(
        "--vol-source",
        choices=["pysystemtrade", "dated", "continuous"],
        default="pysystemtrade",
        help="Roll-neutral Carver history (default), or explicit IB comparison surface",
    )
    parser.add_argument("--pysystemtrade-db", type=Path, default=DEFAULT_PYSYSTEMTRADE_DB_PATH)
    parser.add_argument(
        "--pysystemtrade-pooling-mapping",
        type=Path,
        default=DEFAULT_POOLING_MAPPING_PATH,
        help="Reviewed classification retained in the all-variant cost report",
    )
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS,
                        help="Minimum days to expiry for resolved traded contract (default: %(default)s)")
    parser.add_argument(
        "--contract-details-timeout",
        type=float,
        default=DEFAULT_CONTRACT_DETAILS_TIMEOUT,
        help="Seconds allowed for each broad-universe IB contract lookup (default: %(default)s)",
    )
    parser.add_argument("--quote-wait-seconds", type=float, default=DEFAULT_QUOTE_WAIT_SECONDS)
    parser.add_argument(
        "--spread-duration",
        default=DEFAULT_SPREAD_DURATION,
        help=(
            "IB BID_ASK history duration for spread estimates; passed directly "
            "to IBPySync.get_historical_bars (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--spread-all-hours",
        action="store_true",
        help="Use the full futures session for BID_ASK history; default uses RTH",
    )
    parser.add_argument(
        "--volume-duration",
        default=DEFAULT_VOLUME_DURATION,
        help="IB daily TRADES history duration used for contract volume",
    )
    parser.add_argument(
        "--volume-lookback-days",
        type=int,
        default=DEFAULT_VOLUME_LOOKBACK_DAYS,
        help="Latest daily contract-volume observations to average",
    )
    parser.add_argument(
        "--market-data-type",
        choices=["auto", "live", "frozen", "delayed", "delayed-frozen"],
        default="auto",
        help="IB quote mode (default: auto tries live, delayed, then delayed-frozen)",
    )
    parser.add_argument("--use-rth", action="store_true",
                        help="Use regular-hours-only volatility bars; default uses the full futures session")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=23)
    parser.add_argument("--offline", action="store_true",
                        help="Build the full mixed-vol/mapping comparison without connecting to IB")
    parser.add_argument(
        "--target-vol",
        "--affordability-target-vol",
        dest="affordability_target_vol",
        type=float,
        default=DEFAULT_AFFORDABILITY_TARGET_VOL,
        help="Risk target used for the capital-independent minimum-contract diagnostic",
    )
    parser.add_argument(
        "--affordability-min-contracts",
        type=int,
        default=DEFAULT_AFFORDABILITY_MIN_CONTRACTS,
        help="Minimum average contract count used by the affordability diagnostic",
    )
    parser.add_argument(
        "--initial-capital-usd",
        "--affordability-capital-usd",
        dest="affordability_capital_usd",
        type=float,
        default=DEFAULT_AFFORDABILITY_CAPITAL_USD,
        help="Capital for the equal-weight affordability scenario",
    )
    parser.add_argument(
        "--instrument-cost-limit-sr",
        type=float,
        default=DEFAULT_INSTRUMENT_COST_LIMIT_SR,
        help="Maximum one-way SR cost per trade for Phase 2 eligibility",
    )
    parser.add_argument(
        "--liquidity-ann-trades",
        type=float,
        default=DEFAULT_LIQUIDITY_ANNUAL_TRADES,
        help="Annual strategy turnover used to estimate daily risk traded",
    )
    parser.add_argument(
        "--liquidity-business-days",
        type=int,
        default=DEFAULT_LIQUIDITY_BUSINESS_DAYS,
        help="Trading days used to convert annual turnover to daily risk traded",
    )
    parser.add_argument(
        "--max-market-volume-pct",
        type=float,
        default=DEFAULT_MAX_MARKET_VOLUME_PCT,
        help="Maximum strategy risk traded as a percent of market risk volume",
    )
    parser.add_argument(
        "--min-daily-volume",
        type=float,
        default=DEFAULT_MIN_DAILY_VOLUME_CONTRACTS,
        help="Minimum 20-day average contracts traded per day",
    )
    parser.add_argument(
        "--affordability-idm",
        type=float,
        default=DEFAULT_AFFORDABILITY_IDM,
        help="IDM for the equal-weight affordability scenario",
    )
    parser.add_argument(
        "--affordability-min-main-instruments",
        type=int,
        default=DEFAULT_AFFORDABILITY_MIN_MAIN_INSTRUMENTS,
        help=(
            "Minimum ordinary instruments sharing the scenario risk budget; "
            "special-cluster additions do not satisfy this count"
        ),
    )
    parser.add_argument(
        "--affordability-main-asset-classes",
        type=_parse_asset_classes,
        default=DEFAULT_AFFORDABILITY_MAIN_ASSET_CLASSES,
        help=(
            "Comma-separated core asset classes counted toward the minimum "
            "(default: Equity,Ags,Vol,OilGas,FX,Metals,Bond)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "CSV output base path; a Chicago-time run timestamp is appended to the "
            "filename (default base: results/futures_cost_risk.csv)"
        ),
    )
    parser.add_argument("--no-save", action="store_true")
    return parser.parse_args(argv)


def _error_row(
    instr: dict,
    exc: Exception,
    *,
    vol: Optional[dict] = None,
    annualization_days: int = CARVER_BUSINESS_DAYS_PER_YEAR,
    fx_by_currency: Optional[dict[str, dict]] = None,
) -> dict:
    identity = {
        "symbol": instr.get("symbol"),
        "signal_symbol": resolve_signal_symbol(instr),
        "ib_symbol": instr.get("ib_symbol"),
        "ib_exchange": instr.get("exchange"),
        "ib_currency": instr.get("ib_currency"),
        "ib_multiplier": instr.get("ib_multiplier"),
        "price_magnifier": instr.get("price_magnifier"),
        "currency": instr.get("currency", "USD"),
        "mapping_status": instr.get("mapping_status", "local_registry"),
        "ib_availability": "unavailable_or_unverified",
        "carver_spread_points": instr.get("carver_spread_points"),
        **_pooling_report_identity(instr),
        "spread_quality": "error",
        "error": str(exc),
    }
    if vol is None or vol.get("reference_price") is None:
        return identity
    currency = instr.get("currency", "USD")
    fx_info = (fx_by_currency or {}).get(currency)
    if fx_info is None:
        return identity
    try:
        row = build_cost_risk_row(
            symbol=instr["symbol"],
            signal_symbol=resolve_signal_symbol(instr),
            contract_id="",
            expiration="",
            current_price=vol["reference_price"],
            multiplier=instr["multiplier"],
            commission_per_side=instr["commission"],
            mixed_point_vol=vol["mixed_point_vol"],
            annualization_days=annualization_days,
            history_rows=vol["history_rows"],
            history_start=vol["history_start"],
            history_end=vol["history_end"],
            vol_source="pysystemtrade_roll_neutral",
            vol_reference_price=vol["reference_price"],
            fast_point_vol=vol["fast_point_vol"],
            slow_point_vol=vol["slow_point_vol"],
            vol_observations=vol["vol_observations"],
            slow_history_years=vol["slow_history_years"],
            fast_vol_span=vol["fast_vol_span"],
            slow_vol_span=vol["slow_vol_span"],
            slow_vol_weight=vol["slow_vol_weight"],
            zero_return_fraction=vol["zero_return_fraction"],
            currency=currency,
            fx_to_usd=fx_info["rate"],
            fx_asof=fx_info["asof"],
            price_source="pysystemtrade_last_current_price",
        )
    except Exception:
        return identity
    row.update(_fx_report_fields(fx_info))
    row["risk_return_vol_source"] = "pysystemtrade_reference_fallback"
    row.update(identity)
    return row


def _load_instruments(
    spec: str,
    pysystemtrade_db: Path | str,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> list[dict]:
    path = Path(spec)
    if path.exists() and path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("instrument JSON must contain a list of instrument objects")
        return loaded
    carver = load_pysystemtrade_ib_instruments(
        pysystemtrade_db,
        pooling_mapping_path=pooling_mapping_path,
    )
    execution_universe = add_local_execution_overlays(carver)
    if spec == "all-pysystemtrade":
        return execution_universe
    carver_by_code = {row["instrument_code"]: row for row in carver}
    overlay_by_symbol = {
        row["symbol"]: row
        for row in execution_universe
        if row.get("mapping_status") == "local_execution_overlay"
    }
    requested = [value.strip() for value in spec.split(",") if value.strip()]
    selected = []
    local = []
    for value in requested:
        if value in carver_by_code:
            selected.append(carver_by_code[value])
        elif value.upper() in overlay_by_symbol:
            selected.append(overlay_by_symbol[value.upper()])
        else:
            local.append(value)
    if local:
        selected.extend(build_instruments(local, max_notional=None, max_contracts=1))
    return selected


PUBLIC_REPORT_RENAMES = {
    "price": "cur_price",
    "price_source": "cur_price_source",
    "expiration": "expiry",
    "fx_to_usd": "cur_fx_to_usd",
    "fx_asof": "cur_fx_asof",
    "notional_native_per_contract": "notional_native",
    "notional_per_contract": "notional",
    "current_mixed_point_vol": "daily_point_vol",
    "daily_dollar_vol_per_contract": "daily_dvol",
    "annual_dollar_vol_per_contract": "ann_dvol",
    "selected_one_way_spread_points": "spread_points",
    "selected_spread_source": "spread_source",
    "configured_commission_native": "commission_native",
    "configured_commission": "commission_usd",
    "configured_spread_cash_native": "spread_cash_native",
    "configured_one_way_cost_native": "cost_native",
    "configured_one_way_cost": "cost_usd",
    "configured_trade_sr": "trade_sr",
    "configured_roll_cost_native": "roll_cost_native",
    "configured_roll_cost": "roll_cost_usd",
    "configured_roll_sr": "roll_sr",
    "configured_cost_quality": "cost_quality",
    "risk_return_vol_source": "vol_source",
    "bid": "snap_bid",
    "ask": "snap_ask",
    "full_spread_points": "snap_spread_points",
    "commission_per_side": "snap_commission_usd",
    "one_way_spread_cash": "snap_spread_cash_usd",
    "one_way_total_cost": "snap_cost_usd",
    "one_way_cost_per_annual_dollar_vol": "snap_sr_cost_per_trade",
    "one_way_cost_bps_notional": "snap_cost_bps_notional",
    "spread_quality": "snap_spread_quality",
    "quote_timestamp_ct": "snap_quote_timestamp_ct",
    "quote_market_data_type": "snap_market_data_type",
    "quote_market_data_attempts": "snap_market_data_attempts",
    "quote_error_codes": "snap_error_codes",
    "quote_selected_error_codes": "snap_selected_error_codes",
    "fast_point_vol": "ref_fast_point_vol",
    "slow_point_vol": "ref_slow_point_vol",
    "mixed_point_vol": "ref_mixed_point_vol",
    "vol_reference_price": "ref_price",
    "price_scale_to_vol_reference": "ref_cur_price_ratio",
    "history_rows": "ref_history_n",
    "history_start": "ref_history_start",
    "history_end": "ref_history_end",
    "vol_source": "ref_vol_source",
    "vol_observations": "ref_vol_n",
    "slow_history_years": "ref_slow_history_years",
    "fast_vol_span": "ref_fast_vol_span",
    "slow_vol_span": "ref_slow_vol_span",
    "slow_vol_weight": "ref_slow_vol_weight",
    "zero_return_fraction": "ref_zero_return_fraction",
    "carver_slow_return_vol": "ref_slow_return_vol",
    "carver_configured_one_way_spread_points": "ref_spread_points",
    "strategy_reference_instrument": "ref_strategy_instrument",
    "strategy_reference_history_start": "ref_strategy_history_start",
    "strategy_reference_history_end": "ref_strategy_history_end",
    "strategy_reference_history_observations": "ref_strategy_history_n",
    "strategy_reference_rolls": "ref_strategy_observed_rolls_full",
    "strategy_reference_observed_rolls_per_year_full": (
        "ref_strategy_observed_rolls_per_year_full"
    ),
    "strategy_reference_recent_rolls": "ref_strategy_recent_rolls",
    "strategy_reference_observed_rolls_per_year_recent": (
        "ref_strategy_observed_rolls_per_year_recent"
    ),
    "strategy_reference_recent_roll_start_year": (
        "ref_strategy_recent_roll_start_year"
    ),
    "strategy_reference_recent_roll_end_year": (
        "ref_strategy_recent_roll_end_year"
    ),
    "strategy_reference_recent_roll_years": "ref_strategy_recent_roll_years",
    "strategy_reference_hold_roll_cycle": "ref_strategy_hold_roll_cycle",
    "strategy_reference_configured_rolls_per_year": (
        "ref_strategy_configured_rolls_per_year"
    ),
    "strategy_reference_full_minus_configured_rolls_per_year": (
        "ref_strategy_full_minus_configured_rolls_per_year"
    ),
    "strategy_reference_recent_minus_configured_rolls_per_year": (
        "ref_strategy_recent_minus_configured_rolls_per_year"
    ),
    "strategy_reference_roll_rate_audit": "ref_strategy_roll_rate_audit",
    "strategy_reference_ann_rolls": "ref_ann_rolls",
    "strategy_reference_roll_rate_source": "ref_strategy_roll_rate_source",
    "ib_recent_fast_return_vol": "ib_fast_return_vol",
    "ib_recent_vol_observations": "ib_fast_n",
    "ib_recent_vol_start": "ib_fast_start",
    "ib_recent_vol_end": "ib_fast_end",
    "ib_recent_vol_duration": "ib_fast_duration",
    "ib_historical_requests_allowed": "ib_hist_requests_allowed",
    "ib_historical_market_data_type": "ib_hist_market_data_type",
    "ib_historical_spread_source": "ib_hspread_source",
    "ib_historical_spread_duration": "ib_hspread_duration",
    "ib_historical_spread_bar_size": "ib_hspread_bar_size",
    "ib_historical_spread_observations": "ib_hspread_n",
    "ib_historical_spread_start": "ib_hspread_start",
    "ib_historical_spread_end": "ib_hspread_end",
    "ib_historical_spread_mean_points": "ib_hspread_mean_points",
    "ib_historical_spread_median_points": "ib_hspread_median_points",
    "ib_historical_spread_p90_points": "ib_hspread_p90_points",
    "ib_historical_spread_attempts": "ib_hspread_attempts",
    "ib_historical_spread_failures": "ib_hspread_failures",
    "annualization_days": "ann_days",
    "min_contract_notional": "min_con_notional",
    "min_contract_annual_dollar_vol": "min_con_ann_dvol",
    "min_capital_full_weight_idm1": "min_capital_usd_full_weight_idm1",
    "affordability_scenario_capital": "afford_scen_cap",
    "min_capital_equal_weight": "min_capital_usd_equal_weight",
    "avg_daily_volume_contracts": "avg_daily_volume",
    "volume_observations": "volume_n",
    "initial_capital_usd": "init_capital_usd",
    "selection_target_vol": "target_vol",
    "selection_bucket": "sel_bucket",
    "liquidity_business_days": "liq_days",
    "min_daily_volume_contracts": "min_daily_volume",
    "max_market_volume_pct": "max_pct_mkt_volume",
    "instrument_cost_limit_sr": "cost_limit_sr",
    "instrument_cost_eligible": "cost_eligible",
    "risk_size_eligible": "size_eligible",
    "contract_volume_eligible": "volume_eligible",
    "max_contract_ann_dvol_usd": "max_ann_dvol",
    "report_generated_at_ct": "report_ts_ct",
}

PUBLIC_COLUMN_TOKEN_ABBREVIATIONS = {
    "affordability": "afford",
    "affordable": "afford",
    "average": "avg",
    "availability": "avail",
    "capital": "cap",
    "class": "cls",
    "commission": "comm",
    "configured": "cfg",
    "contract": "con",
    "contracts": "cons",
    "currency": "ccy",
    "description": "desc",
    "duplicate": "dup",
    "duration": "dur",
    "economic": "econ",
    "error": "err",
    "eligible": "elig",
    "eligibility": "elig",
    "exclusion": "excl",
    "execution": "exec",
    "exchange": "exch",
    "expiration": "expiry",
    "historical": "hist",
    "history": "hist",
    "include": "incl",
    "instrument": "instr",
    "instruments": "instrs",
    "family": "fam",
    "fraction": "frac",
    "generated": "gen",
    "group": "grp",
    "liquidity": "liq",
    "market": "mkt",
    "minimum": "min",
    "limit": "lim",
    "multiplier": "mult",
    "observations": "n",
    "point": "pt",
    "pooling": "pool",
    "points": "pts",
    "price": "px",
    "quality": "qual",
    "representative": "rep",
    "restriction": "restrict",
    "profile": "prof",
    "return": "ret",
    "scenario": "scen",
    "status": "stat",
    "source": "src",
    "strategy": "strat",
    "transactions": "trades",
    "transaction": "trade",
    "timestamp": "ts",
    "volatility": "vol",
    "weight": "wt",
    "years": "yrs",
}


def _compact_public_column_name(name: str) -> str:
    compact = PUBLIC_REPORT_RENAMES.get(name, name)
    compact = compact.replace("one_year", "1y")
    compact = compact.replace("total", "tot").replace("annual", "ann")
    compact = compact.replace("sharpe", "sr")
    compact = compact.replace("strategy_reference", "ref_strategy")
    compact = compact.replace("reference_pre_cost", "ref_pre_cost")
    return "_".join(
        PUBLIC_COLUMN_TOKEN_ABBREVIATIONS.get(token, token)
        for token in compact.split("_")
    )


def _public_report_schema(report: pl.DataFrame) -> pl.DataFrame:
    """Expose one selected path first and move alternatives to audit sections."""
    half_spread_columns = [
        "full_spread_points",
        "ib_historical_spread_mean_points",
        "ib_historical_spread_median_points",
        "ib_historical_spread_p90_points",
    ]
    report = report.with_columns(
        (pl.col(name) / 2.0).alias(name)
        for name in half_spread_columns
        if name in report.columns
    )
    report = report.drop(
        column
        for column in ("carver_spread_points", "risk_daily_return_vol")
        if column in report.columns
    )
    renames = {
        name: _compact_public_column_name(name)
        for name in report.columns
        if _compact_public_column_name(name) != name
    }
    report = report.rename(renames)

    selected_first = [
        "symbol",
        "signal_symbol",
        "desc",
        "asset_cls",
        "region",
        "exec_prof",
        "exec_elig",
        "exec_restrict_reason",
        "con_id",
        "expiry",
        "ib_symbol",
        "ib_exch",
        "ib_ccy",
        "ib_mult",
        "ib_avail",
        "cur_px",
        "cur_px_src",
        "ccy",
        "cur_fx_to_usd",
        "cur_fx_asof",
        "cur_fx_src",
        "cur_fx_pair",
        "cur_fx_mkt_data_type",
        "cur_fx_err",
        "mult",
        "notional_native",
        "notional",
        "daily_ret_vol",
        "ann_ret_vol",
        "daily_pt_vol",
        "daily_dvol",
        "ann_dvol",
        "vol_src",
        "avg_daily_volume",
        "volume_n",
        "volume_start",
        "volume_end",
        "volume_src",
        "risk_traded_usd_day",
        "mkt_risk_vol_usd_day",
        "pct_mkt_volume",
        "min_daily_volume",
        "max_pct_mkt_volume",
        "volume_elig",
        "risk_volume_elig",
        "liq_elig",
        "data_elig",
        "sel_bucket",
        "spread_pts",
        "spread_src",
        "comm_native",
        "comm_usd",
        "spread_cash_native",
        "cost_native",
        "cost_usd",
        "trade_sr",
        "roll_sr",
        "cost_qual",
        "rule_cost_lim_sr",
        "cost_lim_sr",
        "cost_elig",
        "max_ann_dvol",
        "size_elig",
        "phase2_elig",
        "phase2_excl",
        "ann_days",
    ]
    selected = [name for name in selected_first if name in report.columns]
    selected_set = set(selected)
    snap_suffix = [
        name for name in report.columns
        if name.startswith("snap_") and name not in selected_set
    ]
    ref_suffix = [
        name for name in report.columns
        if name.startswith("ref_") and name not in selected_set
    ]
    audit_suffix = [*snap_suffix, *ref_suffix]
    middle = [
        name for name in report.columns
        if name not in selected_set
        and name not in audit_suffix
        and name != "report_ts_ct"
    ]
    tail = audit_suffix
    if "report_ts_ct" in report.columns:
        tail = [*tail, "report_ts_ct"]
    return report.select(*selected, *middle, *tail)


def _timestamped_output_path(output: Path, generated_at: datetime) -> Path:
    """Append the report's Chicago generation time without losing extensions."""
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return output.with_name(f"{output.stem}_{stamp}{output.suffix}")


def _emit_report(report: pl.DataFrame, args) -> pl.DataFrame:
    generated_at = datetime.now(REPORT_TIMEZONE)
    report = report.with_columns(
        pl.lit(generated_at.isoformat(timespec="seconds")).alias(
            "report_generated_at_ct"
        )
    )
    output_report = round_public_report(_public_report_schema(report))
    summary_columns = [
        "symbol",
        "asset_cls",
        "ib_symbol",
        "ib_exch",
        "cur_px",
        "ccy",
        "cur_fx_to_usd",
        "cur_fx_src",
        "notional",
        "notional_rank_in_asset_cls",
        "daily_pt_vol",
        "ann_dvol",
        "avg_daily_volume",
        "risk_traded_usd_day",
        "mkt_risk_vol_usd_day",
        "pct_mkt_volume",
        "volume_elig",
        "risk_volume_elig",
        "cost_elig",
        "size_elig",
        "data_elig",
        "sel_bucket",
        "phase2_elig",
        "phase2_excl",
        "afford_rank_in_asset_cls",
        "afford_cluster_role",
        "counts_toward_main_instr_min",
        "afford_scen_cap",
        "afford_scen_idm",
        "afford_min_main_instrs",
        "afford_main_cluster_count",
        "equal_wt_dvol_budget",
        "equal_wt_avg_cons",
        "equal_wt_meets_min_cons",
        "main_instr_afford_for_scen",
        "min_cap_usd_full_wt_idm1",
        "min_cap_usd_equal_wt",
        "pool_role",
        "incl_default_pool",
        "spread_pts",
        "spread_src",
        "trade_sr",
        "cost_rank_in_asset_cls",
        "elig_ewmac_rule_count",
        "elig_ewmac_rules",
        "instr_has_elig_ewmac_rule",
        "ib_avail",
        "exec_elig",
        "exec_restrict_reason",
        "err",
    ]
    print(output_report.select(
        column for column in summary_columns if column in output_report.columns
    ))
    print(f"Report rows={output_report.height} columns={output_report.width}")
    if not args.no_save:
        output_base = args.output
        if output_base is None:
            output_base = (
                Path(__file__).resolve().parents[3]
                / "results"
                / "futures_cost_risk.csv"
            )
        output = _timestamped_output_path(output_base, generated_at)
        output.parent.mkdir(parents=True, exist_ok=True)
        output_report.write_csv(output)
        log.info("futures_cost_risk complete output=%s rows=%d", output, output_report.height)
        print(f"Saved {output}")
    return output_report


def run(argv=None) -> pl.DataFrame:
    """Build, emit, and return the report for Python/notebook callers."""
    args = parse_args(argv)
    setup_logger()
    instruments = _load_instruments(
        args.instruments,
        args.pysystemtrade_db,
        args.pysystemtrade_pooling_mapping,
    )
    pysystemtrade_provider = (
        PysystemtradeHistoryProvider(db_path=args.pysystemtrade_db)
        if args.vol_source == "pysystemtrade" else None
    )
    reference_fx_by_currency = load_latest_fx_to_usd(args.pysystemtrade_db)
    fx_by_currency = reference_fx_by_currency
    if args.rule_cost_limit_sr <= 0:
        raise ValueError("--rule-cost-limit-sr must be positive")
    if args.affordability_target_vol <= 0:
        raise ValueError("--affordability-target-vol must be positive")
    if args.affordability_min_contracts <= 0:
        raise ValueError("--affordability-min-contracts must be positive")
    if args.affordability_capital_usd <= 0:
        raise ValueError("--affordability-capital-usd must be positive")
    if args.affordability_idm <= 0:
        raise ValueError("--affordability-idm must be positive")
    if args.affordability_min_main_instruments <= 0:
        raise ValueError(
            "--affordability-min-main-instruments must be positive"
        )
    phase2_positive_args = {
        "--instrument-cost-limit-sr": args.instrument_cost_limit_sr,
        "--volume-lookback-days": args.volume_lookback_days,
        "--liquidity-ann-trades": args.liquidity_ann_trades,
        "--liquidity-business-days": args.liquidity_business_days,
        "--max-market-volume-pct": args.max_market_volume_pct,
        "--min-daily-volume": args.min_daily_volume,
    }
    for option, value in phase2_positive_args.items():
        if value <= 0:
            raise ValueError(f"{option} must be positive")

    pooled_summaries = None
    strategy_metrics_by_rule = None
    if not args.skip_ewmac_cost_baseline:
        strategy_provider = pysystemtrade_provider or PysystemtradeHistoryProvider(
            db_path=args.pysystemtrade_db
        )
        pooled_summaries, strategy_metrics_by_rule = (
            estimate_pooled_ewmac_cost_baselines(
            strategy_provider,
            db_path=args.pysystemtrade_db,
            pooling_mapping_path=args.pysystemtrade_pooling_mapping,
            fast_spans=args.cost_ewmac_fast_spans,
            vol_span=args.cost_ewmac_vol_span,
            vol_slow_years=args.cost_ewmac_vol_slow_years,
            vol_slow_weight=args.cost_ewmac_vol_slow_weight,
            vol_min_samples=args.cost_ewmac_vol_min_samples,
            scalar_min_periods=args.cost_ewmac_scalar_min_periods,
            )
        )

    if args.offline:
        if pysystemtrade_provider is None:
            raise ValueError("--offline currently requires --vol-source pysystemtrade")
        rows = []
        for instr in instruments:
            try:
                vol = volatility_from_pysystemtrade_history(
                    pysystemtrade_provider,
                    instr.get("instrument_code", instr["symbol"]),
                    annualization_days=CARVER_BUSINESS_DAYS_PER_YEAR,
                    fast_span=args.fast_vol_span,
                    slow_years=args.slow_vol_years,
                    slow_weight=args.slow_vol_weight,
                )
                row = _error_row(
                    instr,
                    RuntimeError("IB contract qualification not run (--offline)"),
                    vol=vol,
                    fx_by_currency=fx_by_currency,
                )
                row["ib_availability"] = "not_checked_offline"
                row["spread_quality"] = "unknown_no_bid_ask"
                row["error"] = None
                rows.append(row)
            except Exception as exc:
                rows.append(_error_row(instr, exc, fx_by_currency=fx_by_currency))
        rows = _attach_pooled_cost_estimates(
            rows,
            instruments,
            pooled_summaries=pooled_summaries,
            strategy_metrics_by_rule=strategy_metrics_by_rule,
            rule_cost_limit_sr=args.rule_cost_limit_sr,
        )
        report = _attach_affordability_ranks(
            pl.DataFrame(rows, infer_schema_length=None),
            target_vol=args.affordability_target_vol,
            min_contracts=args.affordability_min_contracts,
            capital_usd=args.affordability_capital_usd,
            idm=args.affordability_idm,
            min_main_instruments=args.affordability_min_main_instruments,
            main_asset_classes=args.affordability_main_asset_classes,
        )
        report = _attach_phase2_prefilters(
            report,
            initial_capital_usd=args.affordability_capital_usd,
            target_vol=args.affordability_target_vol,
            instrument_cost_limit_sr=args.instrument_cost_limit_sr,
            liquidity_ann_trades=args.liquidity_ann_trades,
            liquidity_business_days=args.liquidity_business_days,
            max_market_volume_pct=args.max_market_volume_pct,
            min_daily_volume_contracts=args.min_daily_volume,
        )
        return _emit_report(report, args)

    log.info(
        "futures_cost_risk start instruments=%d duration=%s fast_vol_span=%d "
        "slow_vol_years=%d slow_vol_weight=%.3f vol_source=%s "
        "market_data_type=%s use_rth=%s",
        len(instruments), args.duration, args.fast_vol_span, args.slow_vol_years,
        args.slow_vol_weight, args.vol_source, args.market_data_type,
        args.use_rth,
    )
    ib = IBPySync()
    ib.connect(args.host, args.port, args.client_id)
    fx_by_currency = load_current_fx_to_usd(
        ib,
        {str(instr.get("currency") or "USD") for instr in instruments},
        reference_fx_by_currency,
        market_data_type=args.market_data_type,
        quote_wait_seconds=args.quote_wait_seconds,
    )
    rows = []
    try:
        for instr in instruments:
            symbol = instr["symbol"]
            try:
                row = diagnose_instrument(
                    ib,
                    instr,
                    duration=args.duration,
                    spread_duration=args.spread_duration,
                    spread_use_rth=not args.spread_all_hours,
                    volume_duration=args.volume_duration,
                    volume_lookback_days=args.volume_lookback_days,
                    min_days=args.min_days,
                    contract_details_timeout=args.contract_details_timeout,
                    quote_wait_seconds=args.quote_wait_seconds,
                    use_rth=args.use_rth,
                    vol_source=args.vol_source,
                    fast_span=args.fast_vol_span,
                    slow_years=args.slow_vol_years,
                    slow_weight=args.slow_vol_weight,
                    market_data_type=args.market_data_type,
                    pysystemtrade_provider=pysystemtrade_provider,
                    fx_by_currency=fx_by_currency,
                )
                rows.append(row)
                log.info(
                    "futures_cost_risk selected symbol=%s contract=%s notional_usd=%.2f "
                    "annual_dvol_usd=%.2f one_way_cost_usd=%s spread_source=%s",
                    symbol, row["contract_id"], row["notional_per_contract"],
                    row["annual_dollar_vol_per_contract"], row["one_way_total_cost"],
                    row["selected_spread_source"],
                )
            except Exception as exc:
                log.warning("futures_cost_risk rejected symbol=%s reason=%s", symbol, exc)
                fallback_vol = None
                if pysystemtrade_provider is not None:
                    try:
                        _, fallback_vol = _history_volatility(
                            ib,
                            instr,
                            vol_source="pysystemtrade",
                            duration=args.duration,
                            use_rth=args.use_rth,
                            fast_span=args.fast_vol_span,
                            slow_years=args.slow_vol_years,
                            slow_weight=args.slow_vol_weight,
                            pysystemtrade_provider=pysystemtrade_provider,
                        )
                    except Exception as vol_exc:
                        log.warning(
                            "futures_cost_risk volatility_fallback_failed symbol=%s reason=%s",
                            symbol, vol_exc,
                        )
                rows.append(_error_row(
                    instr,
                    exc,
                    vol=fallback_vol,
                    fx_by_currency=fx_by_currency,
                ))
    finally:
        ib.disconnect()

    rows = _attach_pooled_cost_estimates(
        rows,
        instruments,
        pooled_summaries=pooled_summaries,
        strategy_metrics_by_rule=strategy_metrics_by_rule,
        rule_cost_limit_sr=args.rule_cost_limit_sr,
    )
    report = _attach_affordability_ranks(
        pl.DataFrame(rows, infer_schema_length=None),
        target_vol=args.affordability_target_vol,
        min_contracts=args.affordability_min_contracts,
        capital_usd=args.affordability_capital_usd,
        idm=args.affordability_idm,
        min_main_instruments=args.affordability_min_main_instruments,
        main_asset_classes=args.affordability_main_asset_classes,
    )
    report = _attach_phase2_prefilters(
        report,
        initial_capital_usd=args.affordability_capital_usd,
        target_vol=args.affordability_target_vol,
        instrument_cost_limit_sr=args.instrument_cost_limit_sr,
        liquidity_ann_trades=args.liquidity_ann_trades,
        liquidity_business_days=args.liquidity_business_days,
        max_market_volume_pct=args.max_market_volume_pct,
        min_daily_volume_contracts=args.min_daily_volume,
    )
    return _emit_report(report, args)


def main(argv=None) -> None:
    """Console-script boundary; successful commands must return exit status zero."""
    run(argv)


if __name__ == "__main__":
    main()

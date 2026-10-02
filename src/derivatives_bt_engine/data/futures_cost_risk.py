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
Five local CBOT grain micros absent from Carver are added as execution-only
overlays, producing 257 candidate rows; qualification against the connected
account determines actual availability.
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
# ib_insync's historical request coroutine times out internally after 60
# seconds, cancels the request, and can return an empty BarDataList instead of
# raising.  Treat an empty result this close to that boundary as a timeout so
# the cost audit does not immediately issue another expensive history request.
IB_INSYNC_HISTORICAL_TIMEOUT_FLOOR_SECONDS = 55.0
RETIRED_IB_INSTRUMENTS = {
    "BB3M": "CME BSBY futures were permanently delisted in October 2024",
}
TWO_DECIMAL_MONEY_COLUMNS = {
    "notional_native_per_contract",
    "notional_per_contract",
    "daily_dollar_vol_per_contract",
    "annual_dollar_vol_per_contract",
    "min_contract_notional",
    "min_contract_annual_dollar_vol",
    "min_capital_full_weight_idm1",
    "commission_per_side",
    "one_way_spread_cash",
    "one_way_total_cost",
    "configured_commission_native",
    "configured_commission",
    "configured_spread_cash_native",
    "configured_one_way_cost_native",
    "configured_one_way_cost",
    "notional_usd_per_contract",
    "selected_daily_dvol_usd_per_contract",
    "selected_ann_dvol_usd_per_contract",
    "selected_commission_native",
    "selected_commission_usd",
    "selected_spread_cash_native",
    "selected_cost_native",
    "selected_cost_usd",
    "snap_spread_cash_usd",
    "snap_cost_usd",
    "min_contract_notional_usd",
    "min_contract_ann_dvol_usd",
    "min_capital_usd_full_weight_idm1",
}
SIX_DECIMAL_RATE_COLUMNS = {
    "fx_to_usd",
    "daily_return_vol",
    "annual_return_vol",
    "cur_fx_to_usd",
    "ref_fx_to_usd",
    "selected_daily_return_vol",
    "selected_ann_return_vol",
}

DEFAULT_COST_EWMAC_FAST_SPANS = (4, 8, 16, 32, 64)
DEFAULT_COST_EWMAC_FAST_SPAN = 16
DEFAULT_COST_EWMAC_SLOW_SPAN = 64
DEFAULT_COST_EWMAC_VOL_SPAN = CARVER_FAST_VOL_SPAN
DEFAULT_RULE_COST_LIMIT_SR = 0.15
DEFAULT_AFFORDABILITY_TARGET_VOL = 0.20
DEFAULT_AFFORDABILITY_MIN_CONTRACTS = 4


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
        roll_count = history.marks.filter(pl.col("is_roll")).height
        history_start = history.marks.get_column("trade_date").min()
        history_end = history.marks.get_column("trade_date").max()
        elapsed_years = (
            (history_end - history_start).days / 365.25
            if history_start is not None and history_end is not None
            else 0.0
        )
        metrics.update(
            instrument_code=instrument_code,
            strategy_rolls=roll_count,
            strategy_rolls_per_year=(
                roll_count / elapsed_years if elapsed_years > 0 else None
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
    """Calculate one-way rule and roll costs in annual SR units."""
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
            strategy_reference_rolls_per_year=first_metrics.get(
                "strategy_rolls_per_year"
            ),
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
    sr_cost_per_trade = one_way_usd / annual_dollar_vol
    rolls_per_year = _nonnegative_finite(
        (first_metrics or {}).get("strategy_rolls_per_year")
    )
    roll_transactions = 2.0 * rolls_per_year if rolls_per_year is not None else None
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
        "configured_sr_cost_per_trade": sr_cost_per_trade,
        "strategy_reference_roll_transactions_per_year": roll_transactions,
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
        forecast_sr_cost = (
            sr_cost_per_trade * pooled_turnover
            if pooled_turnover is not None
            else None
        )
        roll_sr_cost = (
            sr_cost_per_trade * roll_transactions
            if roll_transactions is not None
            else None
        )
        total_transactions = (
            pooled_turnover + roll_transactions
            if pooled_turnover is not None and roll_transactions is not None
            else None
        )
        total_sr_cost = (
            sr_cost_per_trade * total_transactions
            if total_transactions is not None
            else None
        )
        eligible = (
            total_sr_cost <= rule_cost_limit_sr
            if total_sr_cost is not None
            else None
        )
        if eligible:
            eligible_rules.append(f"{fast_span}/{slow_span}")
        result.update({
            f"{prefix}_pooled_rule_turnover": pooled_turnover,
            f"{prefix}_roll_transactions_per_year": roll_transactions,
            f"{prefix}_total_transactions_per_year": total_transactions,
            f"{prefix}_forecast_annual_sr_cost": forecast_sr_cost,
            f"{prefix}_roll_annual_sr_cost": roll_sr_cost,
            f"{prefix}_total_annual_sr_cost": total_sr_cost,
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
        row.update(
            _configured_cost_estimate(
                instr,
                row,
                pooled_summaries=pooled_summaries,
                strategy_metrics_by_rule={
                    fast_span: metrics.get(reference)
                    for fast_span, metrics in metrics_by_rule.items()
                    if metrics.get(reference) is not None
                },
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
) -> pl.DataFrame:
    """Add transparent cost and contract-granularity ranks by asset class."""
    if target_vol <= 0:
        raise ValueError("target_vol must be positive")
    if min_contracts <= 0:
        raise ValueError("min_contracts must be positive")
    required = {
        "symbol",
        "asset_class",
        "notional_per_contract",
        "annual_dollar_vol_per_contract",
        "configured_sr_cost_per_trade",
    }
    if not required.issubset(report.columns):
        return report
    ranked = report.with_columns(
        pl.lit(target_vol).alias("affordability_target_vol"),
        pl.lit(min_contracts).alias("affordability_min_contracts"),
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
    )
    return ranked.with_columns(
        pl.col("configured_sr_cost_per_trade")
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
    return {
        "ib_recent_fast_return_vol": fast_return_vol,
        "ib_recent_vol_observations": clean.get_column("ret_1d").drop_nulls().len(),
        "ib_recent_vol_start": clean.get_column("ts_event").min(),
        "ib_recent_vol_end": clean.get_column("ts_event").max(),
        "ib_recent_vol_duration": duration,
    }


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
        "--affordability-target-vol",
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


def _round_report_decimals(report: pl.DataFrame) -> pl.DataFrame:
    """Round report floats for human-facing CSV output.

    Monetary contract/cost fields use cents. FX and return-volatility rates
    retain six decimal places so displayed notionals and rates reproduce the
    reported dollar volatility without material rounding drift. Other
    floating-point diagnostics retain four decimal places. Counts and
    identifiers are not cast or rounded.
    """
    expressions = []
    for name, dtype in report.schema.items():
        if dtype in (pl.Float32, pl.Float64):
            if name in TWO_DECIMAL_MONEY_COLUMNS:
                decimals = 2
            elif name in SIX_DECIMAL_RATE_COLUMNS:
                decimals = 6
            else:
                decimals = 4
            expressions.append(pl.col(name).round(decimals))
    return report.with_columns(expressions)


PUBLIC_REPORT_RENAMES = {
    "price": "cur_price",
    "price_source": "cur_price_source",
    "fx_to_usd": "cur_fx_to_usd",
    "fx_asof": "cur_fx_asof",
    "notional_per_contract": "notional_usd_per_contract",
    "current_mixed_point_vol": "selected_daily_point_vol",
    "daily_return_vol": "selected_daily_return_vol",
    "annual_return_vol": "selected_ann_return_vol",
    "daily_dollar_vol_per_contract": "selected_daily_dvol_usd_per_contract",
    "annual_dollar_vol_per_contract": "selected_ann_dvol_usd_per_contract",
    "selected_one_way_spread_points": "selected_spread_points",
    "configured_commission_native": "selected_commission_native",
    "configured_commission": "selected_commission_usd",
    "configured_spread_cash_native": "selected_spread_cash_native",
    "configured_one_way_cost_native": "selected_cost_native",
    "configured_one_way_cost": "selected_cost_usd",
    "configured_sr_cost_per_trade": "selected_sr_cost_per_trade",
    "configured_cost_quality": "selected_cost_quality",
    "risk_return_vol_source": "selected_vol_source",
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
    "strategy_reference_rolls": "ref_strategy_rolls",
    "strategy_reference_rolls_per_year": "ref_strategy_rolls_per_year",
    "strategy_reference_roll_transactions_per_year": "ref_strategy_roll_tx_per_year",
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
    "min_contract_notional": "min_contract_notional_usd",
    "min_contract_annual_dollar_vol": "min_contract_ann_dvol_usd",
    "min_capital_full_weight_idm1": "min_capital_usd_full_weight_idm1",
}


def _compact_public_column_name(name: str) -> str:
    if name in PUBLIC_REPORT_RENAMES:
        return PUBLIC_REPORT_RENAMES[name]
    compact = name.replace("transactions", "tx").replace("transaction", "tx")
    compact = compact.replace("total", "tot").replace("annual", "ann")
    compact = compact.replace("sharpe", "sr")
    compact = compact.replace("strategy_reference", "ref_strategy")
    compact = compact.replace("reference_pre_cost", "ref_pre_cost")
    return compact


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
        "description",
        "asset_class",
        "region",
        "execution_profile",
        "execution_eligible",
        "execution_restriction_reason",
        "contract_id",
        "expiration",
        "ib_symbol",
        "ib_exchange",
        "ib_currency",
        "ib_multiplier",
        "ib_availability",
        "cur_price",
        "cur_price_source",
        "currency",
        "cur_fx_to_usd",
        "cur_fx_asof",
        "cur_fx_source",
        "cur_fx_pair",
        "cur_fx_market_data_type",
        "cur_fx_error",
        "multiplier",
        "notional_native_per_contract",
        "notional_usd_per_contract",
        "selected_daily_return_vol",
        "selected_ann_return_vol",
        "selected_daily_point_vol",
        "selected_daily_dvol_usd_per_contract",
        "selected_ann_dvol_usd_per_contract",
        "selected_vol_source",
        "selected_spread_points",
        "selected_spread_source",
        "selected_commission_native",
        "selected_commission_usd",
        "selected_spread_cash_native",
        "selected_cost_native",
        "selected_cost_usd",
        "selected_sr_cost_per_trade",
        "selected_cost_quality",
        "rule_cost_limit_sr",
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
        and name != "report_generated_at_ct"
    ]
    tail = audit_suffix
    if "report_generated_at_ct" in report.columns:
        tail = [*tail, "report_generated_at_ct"]
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
    output_report = _round_report_decimals(_public_report_schema(report))
    summary_columns = [
        "symbol",
        "asset_class",
        "ib_symbol",
        "ib_exchange",
        "cur_price",
        "currency",
        "cur_fx_to_usd",
        "cur_fx_source",
        "notional_usd_per_contract",
        "notional_rank_in_asset_class",
        "selected_daily_point_vol",
        "selected_ann_dvol_usd_per_contract",
        "affordability_rank_in_asset_class",
        "min_capital_usd_full_weight_idm1",
        "pooling_role",
        "include_default_pool",
        "selected_spread_points",
        "selected_spread_source",
        "selected_sr_cost_per_trade",
        "cost_rank_in_asset_class",
        "eligible_ewmac_rule_count",
        "eligible_ewmac_rules",
        "instrument_has_eligible_ewmac_rule",
        "ib_availability",
        "execution_eligible",
        "execution_restriction_reason",
        "error",
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
        )
        return _emit_report(report, args)

    log.info(
        "futures_cost_risk start instruments=%d duration=%s fast_vol_span=%d "
        "slow_vol_years=%d slow_vol_weight=%.3f vol_source=%s "
        "market_data_type=%s use_rth=%s",
        len(instruments), args.duration, args.fast_vol_span, args.slow_vol_years,
        args.slow_vol_weight, args.vol_source, args.market_data_type, args.use_rth,
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
    )
    return _emit_report(report, args)


def main(argv=None) -> None:
    """Console-script boundary; successful commands must return exit status zero."""
    run(argv)


if __name__ == "__main__":
    main()

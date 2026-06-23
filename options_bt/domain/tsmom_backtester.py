"""
Multi-symbol TSMOM backtest: monthly rebalance to a vol-targeted contract
count per symbol, with a spot-VIX regime gate, in a simple form.

Deliberately separate from Backtester/TradeManager/FuturesPosition: those
are built around discrete "open position, hold until roll/expiry, then
close" trades, shared with the still-pandas option backtest path. TSMOM's
lifecycle (continuously-sized monthly rebalance toward a target contract
count, no roll/expiry-driven open-close cycle) doesn't fit that model, and
retrofitting it would risk regressing the shared option path. This module
reuses the existing pure signal math (tsmom_signal.py) and FuturesDataLoader
but implements its own portfolio loop.

No VX futures (CFE) history is available locally (the Globex MDP3.0 duckdb
is CME-only) -- the regime gate below uses spot VIX vs its own trailing
63-day MA as the closest available analog to the live system's VX
front-month / VX-63d-MA ratio (see options_bt.live.tsmom_rebalance).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import polars as pl

from options_bt.domain.enums import FuturesType
from options_bt.domain.futures_dataloader import FuturesDataLoader
from options_bt.domain.tsmom_signal import calculate_trend_strength, classify_regime, compute_position_scalar
from options_bt.utils.logger import setup_logger

logger = setup_logger()

VIX_FILE_PATH = "/home/dev/data/fin/market/index/VIX/historical/vix.parquet"

# Same band thresholds as options_bt.live.tsmom_rebalance's VX-futures gate,
# applied to spot-VIX-current / spot-VIX-63d-MA instead.
VIX_ELEVATED_RATIO = 1.3
VIX_SPIKE_RATIO = 1.5
VIX_EXTREME_RATIO = 2.0
VIX_ELEVATED_SCALE = 0.6


@dataclass
class TsmomBacktestConfig:
    symbols: list[str]
    initial_capital: float = 100_000.0
    vol_target: float = 0.15
    max_contracts: int = 5
    max_notional: float = 25_000.0
    long_only: bool = False
    regime_discount: float = 0.5
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def check_vol_regime(vix_ratio: Optional[float]) -> str:
    """'normal' | 'elevated' | 'spike' | 'extreme' from vix_current / vix_ma63."""
    if vix_ratio is None:
        return 'normal'
    if vix_ratio > VIX_EXTREME_RATIO:
        return 'extreme'
    if vix_ratio > VIX_SPIKE_RATIO:
        return 'spike'
    if vix_ratio > VIX_ELEVATED_RATIO:
        return 'elevated'
    return 'normal'


def load_portfolio_data(symbols: list[str]) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Loads each symbol's continuous front-month OHLCV (via the existing
    FuturesDataLoader, parquet-cached) plus one shared spot-VIX series,
    read directly as polars (covers 1990-present, unlike the older
    pandas/CSV vix_file BaseDataLoader.vix_data still uses for the option
    path, which is stale past 2024-12-31)."""
    cache_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '.cache', 'futures'))
    os.makedirs(cache_dir, exist_ok=True)
    price_data = {s: FuturesDataLoader(asset=s, data_dir=cache_dir, use_preprocessed=True, save_preprocessed=True).ohlcv
                  for s in symbols}
    vix = pl.read_parquet(VIX_FILE_PATH).select(['date', 'close']).rename({'close': 'vix_close'}).sort('date')
    return price_data, vix


def _compute_vix_regime_series(vix: pl.DataFrame) -> pl.DataFrame:
    """Adds vix_ma63 / vix_ratio / vol_regime columns to a raw VIX frame."""
    vix = vix.with_columns(vix_ma63=pl.col('vix_close').rolling_mean(63))
    vix = vix.with_columns(
        vix_ratio=pl.when(pl.col('vix_ma63') > 0)
        .then(pl.col('vix_close') / pl.col('vix_ma63'))
        .otherwise(None)
    )
    regimes = [check_vol_regime(r) for r in vix['vix_ratio'].to_list()]
    return vix.with_columns(vol_regime=pl.Series(regimes))


def _round(x: Optional[float], ndigits: int) -> Optional[float]:
    """round() that passes None through, for optional diagnostic fields
    that may not have been computable (e.g. gated/skipped rebalances)."""
    return None if x is None else round(x, ndigits)


def _month_end_dates(price_data: dict[str, pl.DataFrame]) -> set[date]:
    """Last trading day of each calendar month, across the union of every
    loaded symbol's dates."""
    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in price_data.values())))
    dates_df = pl.DataFrame({'ts_event': all_dates}).with_columns(
        ym=pl.col('ts_event').dt.strftime('%Y-%m')
    )
    month_ends = dates_df.group_by('ym').agg(pl.col('ts_event').max().alias('month_end'))
    return set(month_ends['month_end'].to_list())


def _vix_regime_at(vix: pl.DataFrame, d: date) -> tuple[str, Optional[float]]:
    """(vol_regime, vix_close) as of the latest available VIX row at or
    before `d`. ('normal', None) if no VIX data is available yet."""
    row = vix.filter(pl.col('date') <= d).tail(1)
    if row.height == 0:
        return 'normal', None
    return row['vol_regime'][0], row['vix_close'][0]


def _compute_target(symbol: str, d: date, full_price_data: dict[str, pl.DataFrame],
                     futures_types: dict[str, FuturesType], config: TsmomBacktestConfig,
                     position_scale: float) -> Optional[dict]:
    """Signal + vol-targeted sizing for one symbol as of date `d`, using
    full unbounded history for lookback. None if there isn't yet enough
    history (< 64 bars) to compute a signal at all."""
    df = full_price_data[symbol].filter(pl.col('ts_event') <= d)
    if df.height < 64:
        return None
    signal_df = calculate_trend_strength(df)
    last = signal_df.tail(1)
    trend_strength = last['trend_strength'][0]
    ts3m, ts1y = last['ts3m'][0], last['ts1y'][0]
    daily_std_last = last['daily_std'][0] if 'daily_std' in last.columns else None
    last_close = float(last['close'][0])
    regime = classify_regime(ts3m, ts1y)

    signal_for_scalar = trend_strength
    if config.long_only and signal_for_scalar is not None and not (
        isinstance(signal_for_scalar, float) and math.isnan(signal_for_scalar)
    ):
        signal_for_scalar = max(0.0, signal_for_scalar)

    # Recomputed here (mirrors compute_position_scalar's own internal math
    # exactly) purely so the event log can show *why* a given
    # trend_strength did or didn't turn into a trade -- compute_position_
    # scalar itself stays untouched.
    hv = daily_std_last * math.sqrt(252) if daily_std_last and daily_std_last > 0 else None
    vol_scalar = max(0.25, min(2.0, config.vol_target / hv)) if hv else 1.0
    discount = config.regime_discount if regime in ('Correction', 'Rebound') else 1.0

    scalar = compute_position_scalar(
        signal_for_scalar, daily_std_last, config.vol_target, regime,
        regime_discount=config.regime_discount,
    ) * position_scale

    mult = futures_types[symbol].mult
    contract_notional = last_close * mult
    target = round((config.max_notional * scalar) / contract_notional) if contract_notional else 0
    target = max(-config.max_contracts, min(config.max_contracts, target))

    return {
        'target': target, 'trend_strength': trend_strength, 'regime': regime,
        'hv': hv, 'vol_scalar': vol_scalar * position_scale, 'discount': discount,
    }


def run_tsmom_backtest(config: TsmomBacktestConfig) -> dict:
    """Runs the monthly-rebalance TSMOM backtest. Returns a dict with
    'stats' (daily portfolio capital/drawdown, polars DataFrame) and
    'events' (per-rebalance-event log, list of dicts)."""
    # `full_price_data` stays unbounded -- calculate_trend_strength's 252-day
    # lookback needs real history before config.start_date, not just
    # whatever falls inside the requested window. Only the iterated date
    # range (and what counts as a rebalance/MTM date) is bounded.
    full_price_data, vix = load_portfolio_data(config.symbols)
    vix = _compute_vix_regime_series(vix)
    futures_types = {s: FuturesType.from_symbol(s) for s in config.symbols}

    windowed = {}
    for symbol, df in full_price_data.items():
        if config.start_date:
            df = df.filter(pl.col('ts_event') >= config.start_date)
        if config.end_date:
            df = df.filter(pl.col('ts_event') <= config.end_date)
        windowed[symbol] = df.sort('ts_event')

    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in windowed.values())))
    # A position opened on the very last date in the window has zero
    # subsequent days to mark to market -- it shows up as a pure commission
    # cost with no chance of P&L, which is misleading rather than meaningful.
    # Mirrors Backtester's own bounded-window handling (backtester.py:152-160)
    # in spirit: don't let the backtest take an action it can't show the
    # result of within the requested range.
    rebalance_dates = _month_end_dates(windowed) - ({all_dates[-1]} if all_dates else set())

    held_contracts = {s: 0 for s in config.symbols}
    prior_close = {s: None for s in config.symbols}
    events: list[dict] = []
    daily_rows = []
    capital = config.initial_capital

    def _rebalance_to(symbol: str, target: int, rebalance_date: date, trend_strength, regime, vol_regime,
                       vix_close=None, hv=None, vol_scalar=None, discount=None, is_seed=False):
        nonlocal capital
        prior = held_contracts[symbol]
        if target != prior:
            fee = futures_types[symbol].commission * 2 * abs(target - prior)
            capital -= fee
        held_contracts[symbol] = target
        events.append({
            'date': rebalance_date, 'symbol': symbol, 'trend_strength': _round(trend_strength, 4),
            'regime': regime, 'vol_regime': vol_regime, 'vix_close': _round(vix_close, 2),
            'hv': _round(hv, 4), 'vol_scalar': _round(vol_scalar, 4), 'discount': _round(discount, 2),
            'prior_contracts': prior, 'target_contracts': target, 'is_seed': is_seed,
        })

    # Seed the position from the last completed month-end *before*
    # start_date, using full unbounded history -- otherwise the backtest
    # starts flat and wastes its entire first calendar month sitting in
    # cash even when a perfectly valid prior-month signal already called
    # for a position, only entering at the window's first in-range
    # month-end. This makes start_date behave like "continuing an
    # already-running strategy," not "day one of trading."
    if config.start_date:
        prior_month_ends = [d for d in _month_end_dates(full_price_data) if d < config.start_date]
        if prior_month_ends:
            seed_date = max(prior_month_ends)
            vol_regime, vix_close = _vix_regime_at(vix, seed_date)
            if vol_regime not in ('spike', 'extreme'):  # held_contracts are all 0 here -- hold/halve would be a no-op anyway
                position_scale = VIX_ELEVATED_SCALE if vol_regime == 'elevated' else 1.0
                for symbol in config.symbols:
                    result = _compute_target(symbol, seed_date, full_price_data, futures_types, config, position_scale)
                    if result is None:
                        continue
                    _rebalance_to(symbol, result['target'], seed_date, result['trend_strength'], result['regime'],
                                  vol_regime, vix_close=vix_close, hv=result['hv'],
                                  vol_scalar=result['vol_scalar'], discount=result['discount'], is_seed=True)

    for d in all_dates:
        # 1. Mark existing holdings to market: today's close vs yesterday's,
        # the same diff-based daily MTM approach Backtester.
        # calculate_futures_mtm_drawdown already uses for single-symbol.
        for symbol in config.symbols:
            row = windowed[symbol].filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            close = row['close'][0]
            if prior_close[symbol] is not None and held_contracts[symbol] != 0:
                capital += held_contracts[symbol] * (close - prior_close[symbol]) * futures_types[symbol].mult
            prior_close[symbol] = close

        # 2. On rebalance dates, resize toward the vol-targeted signal,
        # gated by the spot-VIX regime (mirrors
        # tsmom_rebalance.compute_rebalance_targets' early-return shape).
        if d in rebalance_dates:
            vol_regime, vix_close = _vix_regime_at(vix, d)

            if vol_regime in ('spike', 'extreme'):
                for symbol in config.symbols:
                    prior = held_contracts[symbol]
                    target = round(prior / 2) if vol_regime == 'extreme' else prior
                    _rebalance_to(symbol, target, d, None, None, vol_regime, vix_close=vix_close)
            else:
                position_scale = VIX_ELEVATED_SCALE if vol_regime == 'elevated' else 1.0
                for symbol in config.symbols:
                    result = _compute_target(symbol, d, full_price_data, futures_types, config, position_scale)
                    if result is None:
                        continue
                    _rebalance_to(symbol, result['target'], d, result['trend_strength'], result['regime'],
                                  vol_regime, vix_close=vix_close, hv=result['hv'],
                                  vol_scalar=result['vol_scalar'], discount=result['discount'])

        daily_rows.append({'date': d, 'capital': round(capital, 2)})

    stats = pl.DataFrame(daily_rows)
    stats = stats.with_columns(running_max=pl.col('capital').cum_max())
    stats = stats.with_columns(
        cum_pnl=(pl.col('capital') - config.initial_capital).round(2),
        drawdown_usd=(pl.col('capital') - pl.col('running_max')).round(2),
    )
    stats = stats.with_columns(
        drawdown_pct=pl.when(pl.col('running_max') > 0)
        .then((pl.col('drawdown_usd') / pl.col('running_max') * 100).round(2))
        .otherwise(0.0)
    )

    return {'stats': stats, 'events': events}

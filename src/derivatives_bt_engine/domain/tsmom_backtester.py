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
front-month / VX-63d-MA ratio (see derivatives_bt_engine.live.tsmom_rebalance).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import duckdb
import polars as pl

from derivatives_bt_engine.domain.enums import TrendRegime, VolRegime
from derivatives_bt_engine.domain.futures_dataloader import FuturesDataLoader
from derivatives_bt_engine.domain.futures_signal_generator import FuturesSignalGenerator
from derivatives_bt_engine.domain.instruments import get_spec, resolve_price_symbol
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength, classify_regime, compute_position_scalar
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# Same VIX_PATH convention as naked_futures.py/the options strategies -- a
# directory resolves to {dir}/processed/vix.parquet (see
# BaseDataLoader._resolve_source_paths). The old hardcoded
# .../VIX/historical/vix.parquet path no longer exists (stale, pre-dates a
# data-directory reorg) and would raise FileNotFoundError.
VIX_FILE_PATH = os.path.join(
    os.path.expanduser(os.getenv('VIX_PATH', '~/data/fin/market/index/VIX/eod')),
    'processed', 'vix.parquet',
)

# Same band thresholds as derivatives_bt_engine.live.tsmom_rebalance's VX-futures gate,
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
    momentum_discount: float = 0.5
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Signal-based entry/exit gate -- same mechanism as FuturesStrategyConfig/
    # TradeManager's ts_exit_threshold/ts_entry_threshold/exit_on_ts_crossover
    # (domain/trade_manager.py), adapted here for TSMOM's variable-direction
    # sizing: direction is derived from whichever side actually matters (the
    # currently-held position for exit, the newly-proposed target for entry)
    # rather than a fixed config field, since a TSMOM symbol can go long or
    # short depending on the sign of its own signal. signal_gate_mode picks
    # the cadence at which the gate is checked -- 'monthly' only at the
    # existing rebalance points (near-zero extra cost, reuses signal/ts3m/
    # ts1y _compute_signal_row already computed that day); 'daily'
    # additionally checks both entry AND exit every day in between,
    # off-cycle from the monthly resize, so a flat symbol can open the day
    # its entry gate first clears (not just at month-end) and a held
    # symbol can flatten the day its exit gate fires. Resizing (magnitude
    # changes to an already-open position) stays strictly monthly-only in
    # BOTH modes either way -- 'daily' only adds off-cycle open/flatten
    # transitions, never off-cycle resizing.
    ts_exit_threshold: Optional[float] = None
    ts_entry_threshold: Optional[float] = None
    exit_on_ts_crossover: bool = False
    signal_gate_mode: str = 'off'  # 'off' | 'monthly' | 'daily'
    # Opt-in "no rebalancing" mode: a positional list of fixed contract
    # counts, one per symbol (index-aligned with `symbols`, e.g.
    # symbols=['ES','GC','CL'], fixed_quantities=[4,3,2] means ES always
    # trades in units of 4, GC in units of 3, CL in units of 2). When set,
    # _compute_target skips vol-targeted/notional-scaled sizing entirely --
    # direction still comes from the sign of that symbol's own raw signal
    # (there's no other principled way to know when to go short without
    # it), but magnitude is just this fixed count (times market_stress_scale
    # during an elevated-VIX regime, rounded, same as the vol-targeted
    # path's own elevated-VIX scaling) instead of vol_target/max_notional
    # math. Entry/exit gates (ts_exit_threshold/ts_entry_threshold/
    # exit_on_ts_crossover) and the VIX spike/extreme hold-or-halve
    # override still apply exactly as before -- this only replaces the
    # continuous vol-targeted scalar with a constant, it doesn't touch
    # the rest of the day loop.
    fixed_quantities: Optional[list[int]] = None
    # Portfolio-wide VIX regime gate (spot-VIX-vs-63d-MA spike/extreme
    # hold-or-halve override, elevated -> market_stress_scale de-risking)
    # -- on by default (matches all prior behavior). Toggle off to isolate
    # the effect of ts_exit_threshold/ts_entry_threshold/exit_on_ts_crossover
    # alone, without VIX-driven interference -- particularly relevant for
    # signal_gate_mode='daily', which is already a departure from
    # traditional monthly-only TSMOM, and where mixing in a second,
    # differently-cadenced portfolio-wide gate makes it harder to isolate
    # what's actually being tested.
    vix_gating: bool = True

    def __post_init__(self):
        if self.signal_gate_mode not in ('off', 'monthly', 'daily'):
            raise ValueError(f"signal_gate_mode must be 'off', 'monthly', or 'daily', got {self.signal_gate_mode!r}")
        if self.fixed_quantities is not None and len(self.fixed_quantities) != len(self.symbols):
            raise ValueError(
                f"fixed_quantities must have exactly one entry per symbol (positional, same order): "
                f"got {len(self.fixed_quantities)} quantities for {len(self.symbols)} symbols "
                f"({self.symbols})"
            )


def check_vol_regime(vix_ratio: Optional[float]) -> VolRegime:
    """Normal | Elevated | Spike | Extreme from vix_current / vix_ma63.

    Deliberately one-sided (mirrors derivatives_bt_engine.live.tsmom_rebalance's
    version): every threshold checks vix_ratio being HIGH -- no symmetric
    low-vix_ratio bucket, not an oversight. This is a portfolio-wide risk-
    management gate (feeds market_stress_scale / the spike-extreme hold-
    or-halve bypass), not a regime-confidence detector. Per-instrument,
    asset-specific vol state (including a low-vol bucket) is a separate
    mechanism -- see SignalConfidenceRegime in tsmom_signal.py."""
    if vix_ratio is None:
        return VolRegime.NORMAL
    if vix_ratio > VIX_EXTREME_RATIO:
        return VolRegime.EXTREME
    if vix_ratio > VIX_SPIKE_RATIO:
        return VolRegime.SPIKE
    if vix_ratio > VIX_ELEVATED_RATIO:
        return VolRegime.ELEVATED
    return VolRegime.NORMAL


def load_portfolio_data(symbols: list[str]) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Loads each symbol's continuous front-month OHLCV (via the existing
    FuturesDataLoader, parquet-cached) plus one shared spot-VIX series,
    read directly as polars (covers 1990-present, unlike the older
    pandas/CSV vix_file BaseDataLoader.vix_data still uses for the option
    path, which is stale past 2024-12-31).

    Some micros (MES, MNQ, MTN, ...) have no db history under their own
    symbol -- resolve_price_symbol borrows the full-size sibling's (ES,
    NQ, ZN, ...) via instruments.py's db_symbol field, so the cache/query
    below runs against the resolved symbol while price_data stays keyed by
    the raw traded symbol (matching futures_types/windowed elsewhere in
    this module, which always use the traded symbol for margin/commission/
    mult -- MES stays sized as MES, never as ES)."""
    cache_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.cache', 'futures'))
    os.makedirs(cache_dir, exist_ok=True)
    price_symbols = {s: resolve_price_symbol(s) for s in symbols}
    _validate_symbols_exist(set(price_symbols.values()), cache_dir)
    price_data = {s: FuturesDataLoader(asset=price_symbols[s], data_dir=cache_dir, use_preprocessed=True, save_preprocessed=True).daily
                  for s in symbols}
    vix = pl.read_parquet(VIX_FILE_PATH).select(['date', 'close']).rename({'close': 'vix_close'}).sort('date')
    return price_data, vix


def _validate_symbols_exist(price_symbols, cache_dir: str) -> None:
    """The continuous-front-month query (FuturesDataLoader.daily) has no
    early-exit for a non-matching asset -- it's an unindexed full-table scan
    that takes minutes either way, so a typo'd or IB-only symbol (e.g. the
    live rebalance's IBKR ticker 'JPY'/'BRE' rather than this db's real
    CME/Globex root '6J'/'6L') would otherwise silently come back as an
    empty frame after minutes of waiting. Check the cheap `DISTINCT asset`
    list up front instead, skipping symbols that are already parquet-cached
    (no need to hit duckdb at all for those). Takes already-resolved price
    symbols (see load_portfolio_data), not raw traded symbols -- a micro
    like MES is expected to be absent from `daily` and shouldn't raise."""
    uncached = [s for s in price_symbols
                if not os.path.exists(os.path.join(cache_dir, f'{s}_daily.parquet'))]
    if not uncached:
        return
    # FuturesDataLoader.db_path is a dataclass field with a default_factory,
    # so it isn't readable as a class attribute (FuturesDataLoader.db_path
    # raises AttributeError) -- call the same factory instances use, so this
    # doesn't duplicate the GLOBEX_DB_PATH env-var/default logic.
    db_path = FuturesDataLoader.__dataclass_fields__['db_path'].default_factory()
    con = duckdb.connect(db_path, read_only=True)
    try:
        known = set(con.sql('SELECT DISTINCT asset FROM daily').pl()['asset'].to_list())
    finally:
        con.close()
    missing = [s for s in uncached if s not in known]
    if missing:
        raise ValueError(
            f'No data found for symbol(s) {missing} in the futures db -- '
            f'note this must be the real CME/Globex ticker (e.g. 6J, 6L, '
            f'6M), not whatever symbol IBKR uses for live contract resolution.'
        )


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


def _signal_gate_reason(sig_val, ts3m_val, ts1y_val, is_long: bool, threshold: Optional[float],
                         exit_on_ts_crossover: bool) -> Optional[str]:
    """Same shape as TradeManager's per-position gate check
    (domain/trade_manager.py's _signal_gate_reason), standalone here since
    TSMOM has no per-position `self.config` to close over and this module
    is deliberately kept separate from TradeManager. Bails out (never
    gates) if either ts3m or ts1y is still null -- calculate_trend_strength
    only requires ts3m to be non-null to emit a `signal` value at all, so
    early in any backtest window `signal` can look like a real number
    while being an unreliable ts3m-only estimate."""
    if ts3m_val is None or ts1y_val is None:
        return None
    if threshold is not None and sig_val is not None:
        if is_long and sig_val < threshold:
            return 'signal_ts_threshold'
        if not is_long and sig_val > -threshold:
            return 'signal_ts_threshold'
    if exit_on_ts_crossover:
        if is_long and ts3m_val < ts1y_val:
            return 'signal_crossover'
        if not is_long and ts3m_val > ts1y_val:
            return 'signal_crossover'
    return None


def _apply_signal_gate(prior_contracts: int, proposed_target: int, result: dict,
                        config: TsmomBacktestConfig) -> tuple[int, Optional[str]]:
    """Overrides `proposed_target` to 0 if the signal gate fires. Direction
    is derived from whichever side actually matters: the CURRENTLY-HELD
    position's sign for the exit check (an existing long/short that's
    weakened), the PROPOSED target's sign for the entry check (a new
    position sizing wants to open, in that direction) -- not a fixed
    config field, since a TSMOM symbol's direction comes from its own
    signal sign, unlike a naked single-direction FuturesStrategyConfig.
    Returns (final_target, gate_reason)."""
    if config.signal_gate_mode == 'off':
        return proposed_target, None
    sig_val, ts3m_val, ts1y_val = result.get('signal'), result.get('ts3m'), result.get('ts1y')

    if prior_contracts != 0:
        is_long = prior_contracts > 0
        reason = _signal_gate_reason(sig_val, ts3m_val, ts1y_val, is_long,
                                      config.ts_exit_threshold, config.exit_on_ts_crossover)
        if reason is not None:
            return 0, reason
        if config.fixed_quantities is not None:
            # fixed_quantities has no "resize" concept -- magnitude is a
            # constant, so if the exit gate didn't fire, ANY difference
            # between prior_contracts and proposed_target here can only be
            # an implicit sign flip driven by the raw composite signal's
            # own sign changing independently of whatever specific exit
            # condition is configured (e.g. exit_on_ts_crossover checks
            # ts3m-vs-ts1y directly, which isn't guaranteed to coincide
            # with tanh(0.4*ts3m+0.6*ts1y) crossing zero) -- stay held,
            # unchanged; a flip must go through the gate, never happen
            # silently just because the vol-targeted path's own "let the
            # freshly computed target through" fallthrough doesn't
            # distinguish resizing from flipping.
            return prior_contracts, None

    if prior_contracts == 0 and proposed_target != 0:
        is_long = proposed_target > 0
        blocked = _signal_gate_reason(sig_val, ts3m_val, ts1y_val, is_long,
                                       config.ts_entry_threshold, config.exit_on_ts_crossover) is not None
        if blocked:
            return 0, 'signal_entry_blocked'

    return proposed_target, None


def _month_end_dates(price_data: dict[str, pl.DataFrame]) -> set[date]:
    """Last trading day of each calendar month, across the union of every
    loaded symbol's dates."""
    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in price_data.values())))
    dates_df = pl.DataFrame({'ts_event': all_dates}).with_columns(
        ym=pl.col('ts_event').dt.strftime('%Y-%m')
    )
    month_ends = dates_df.group_by('ym').agg(pl.col('ts_event').max().alias('month_end'))
    return set(month_ends['month_end'].to_list())


def _vix_regime_at(vix: pl.DataFrame, d: date) -> tuple[VolRegime, Optional[float], Optional[float]]:
    """(vol_regime, vix_close, vix_ratio) as of the latest available VIX
    row at or before `d`. (Normal, None, None) if no VIX data is
    available yet."""
    row = vix.filter(pl.col('date') <= d).tail(1)
    if row.height == 0:
        return VolRegime.NORMAL, None, None
    return VolRegime(row['vol_regime'][0]), row['vix_close'][0], row['vix_ratio'][0]


def _compute_signal_row(symbol: str, precomputed: dict[str, pl.DataFrame], d: date,
                         futures_types: dict[str, dict], config: TsmomBacktestConfig,
                         market_stress_scale: float) -> Optional[dict]:
    """Signal + vol-targeted (or fixed-quantity) sizing for one symbol as of
    date `d`, reading from `precomputed` -- each symbol's full
    calculate_trend_strength output, computed ONCE for the whole unbounded
    history (see run_tsmom_backtest). calculate_trend_strength's rolling/
    diff functions are strictly backward-looking, so a given date's row is
    identical whether computed from the full series or from a series
    truncated to that date -- precomputing once and looking up by date is
    exactly equivalent to (and far cheaper than) this function's old
    per-call recompute, which used to run fresh at every rebalance and
    would otherwise need to run for every symbol on every calendar day to
    support daily entry/exit checking. None if there isn't yet enough
    history for a signal at all (calculate_trend_strength's own `signal`
    column is null until ts3m has 63 bars)."""
    row = precomputed[symbol].filter(pl.col('ts_event') == d)
    if row.height == 0:
        return None

    def _col(name):
        return row[name][0] if name in row.columns else None

    trend_strength = _col('signal')
    if trend_strength is None:
        return None
    ts3m, ts1y = _col('ts3m'), _col('ts1y')
    daily_std_last = _col('daily_std')
    last_close = float(row['close'][0])
    # `dd` is already a (close - peak) / peak fraction from
    # calculate_trend_strength -- express as a percentage to match
    # `stats`' own drawdown_pct convention.
    dd_raw = _col('dd')
    dd_pct = dd_raw * 100 if dd_raw is not None else None
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
    risk_scalar = max(0.25, min(2.0, config.vol_target / hv)) if hv else 1.0
    momentum_discount = config.momentum_discount if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0

    scalar = compute_position_scalar(
        signal_for_scalar, daily_std_last, config.vol_target, regime,
        momentum_discount=config.momentum_discount,
    ) * market_stress_scale

    mult = futures_types[symbol]['multiplier']
    if config.fixed_quantities is not None:
        # No-rebalancing mode: direction is still signal-driven (there's no
        # other principled way to know when to go short without it), but
        # magnitude is this symbol's own fixed contract count -- scaled by
        # market_stress_scale (the same elevated-VIX de-risking the vol-
        # targeted path applies) and rounded, not derived from vol_target/
        # max_notional at all. max_contracts still clamps as a sanity
        # backstop (raise it if it's below your configured fixed_quantities
        # -- it silently truncates otherwise, same as the vol-targeted path).
        fixed_qty = config.fixed_quantities[config.symbols.index(symbol)]
        if signal_for_scalar is None or (isinstance(signal_for_scalar, float) and math.isnan(signal_for_scalar)) or signal_for_scalar == 0:
            target = 0
        else:
            direction = 1 if signal_for_scalar > 0 else -1
            target = direction * round(fixed_qty * market_stress_scale)
    else:
        contract_notional = last_close * mult
        target = round((config.max_notional * scalar) / contract_notional) if contract_notional else 0
    target = max(-config.max_contracts, min(config.max_contracts, target))

    return {
        'target': target, 'signal': trend_strength, 'regime': regime,
        'hv': hv, 'risk_scalar': risk_scalar * market_stress_scale, 'momentum_discount': momentum_discount,
        'close': last_close, 'dd_pct': dd_pct,
        # Raw signal-row fields, straight from calculate_trend_strength,
        # purely for debugging/sanity-checking the sizing math end to end.
        'peak': _col('peak'), 'avg_r3m': _col('avg_r3m'), 'avg_r1y': _col('avg_r1y'),
        'r3m': _col('r3m'), 'r1y': _col('r1y'), 'ts3m': ts3m, 'ts1y': ts1y,
        'r1y_pct': _col('r1y_pct'),
    }


def run_tsmom_backtest(config: TsmomBacktestConfig) -> dict:
    """Runs the monthly-rebalance TSMOM backtest. Returns a dict with
    'stats' (daily portfolio capital/drawdown, polars DataFrame), 'events'
    (per-rebalance-event log, list of dicts), 'transactions' (one row per
    rebalance that actually changed a symbol's contract count -- what was
    bought/sold, when, at what price/fee), and 'trades' (reconstructed
    round-trips: TSMOM has no discrete open/close lifecycle like
    FuturesPosition -- a symbol's exposure is continuously resized, not
    "opened then closed" -- so a trade here is defined as one continuous
    span of nonzero exposure in a single direction: 0->nonzero opens it,
    nonzero->0 or a direct sign flip closes it; resizing within the same
    direction extends the same trade rather than starting a new one. The
    quarterly contract roll is the one exception forced regardless of
    exposure direction: a held span is always closed and immediately
    reopened at each scheduled roll date (close_reason='roll'), same as
    FuturesPosition's own roll_date handling, so 'trades' never reports a
    holding period spanning more than one actual futures contract even
    when the signal itself never triggers a close)."""
    # `full_price_data` stays unbounded -- calculate_trend_strength's 252-day
    # lookback needs real history before config.start_date, not just
    # whatever falls inside the requested window. Only the iterated date
    # range (and what counts as a rebalance/MTM date) is bounded.
    full_price_data, vix = load_portfolio_data(config.symbols)
    vix = _compute_vix_regime_series(vix)
    # Precomputed once per symbol, unconditionally (not just for
    # signal_gate_mode == 'daily') -- see _compute_signal_row's own
    # docstring for why this is exactly equivalent to (and much cheaper
    # than) recomputing calculate_trend_strength fresh at every rebalance.
    precomputed = {s: calculate_trend_strength(full_price_data[s].sort('ts_event')) for s in config.symbols}
    futures_types = {s: get_spec(s) for s in config.symbols}

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
    # Quarterly contract roll schedule -- same static method (Monday prior
    # to the third Friday of Mar/Jun/Sep/Dec) the naked single-position path
    # already uses to tag FuturesPosition.roll_date. Unlike naked's roll_date
    # (checked per-position via `current_date >= pos.roll_date`, so a
    # non-trading scheduled date still fires on the next available day),
    # this is fired via a monotonic pointer below rather than an `in` check,
    # for the same reason.
    sorted_roll_dates = FuturesSignalGenerator._get_quarterly_roll_dates(
        all_dates[0], all_dates[-1]) if all_dates else []
    roll_ptr = 0

    held_contracts = {s: 0 for s in config.symbols}
    prior_close = {s: None for s in config.symbols}
    events: list[dict] = []
    transactions: list[dict] = []
    trades: list[dict] = []
    # Per-symbol open trade accumulator: None when flat, else a dict
    # tracking the currently-open span's entry info plus running realized
    # MTM pnl/fees, closed out (appended to `trades`) on a return to flat,
    # a direct sign flip, or a final force-close after the day loop ends.
    open_trade: dict[str, Optional[dict]] = {s: None for s in config.symbols}
    daily_rows = []
    capital = config.initial_capital

    def _close_trade(symbol: str, exit_date: date, exit_price: Optional[float]) -> None:
        ot = open_trade[symbol]
        if ot is None:
            return
        net_pnl = round(ot['mtm_pnl'] - ot['fees'], 2)
        trades.append({
            'symbol': symbol, 'direction': ot['direction'],
            'entry_date': ot['entry_date'], 'entry_price': _round(ot['entry_price'], 2),
            'exit_date': exit_date, 'exit_price': _round(exit_price, 2),
            'days_held': (exit_date - ot['entry_date']).days,
            'max_contracts': ot['max_contracts'], 'fees': round(ot['fees'], 2),
            'pnl': net_pnl, 'close_reason': ot['close_reason'],
        })
        open_trade[symbol] = None

    def _rebalance_to(symbol: str, target: int, rebalance_date: date, vol_regime,
                       vix_close=None, vix_ratio=None, signal: Optional[dict] = None, is_seed=False):
        """`signal` is _compute_target's full result dict (or None on a
        spike/extreme-gated event, where signal computation is skipped
        entirely -- every signal-derived field below is then None, same
        as before)."""
        nonlocal capital
        s = signal or {}
        prior = held_contracts[symbol]
        fee = 0.0
        if target != prior:
            # Commission is charged only on the quantity actually closed out
            # -- opening a position, or adding to one, is free (matches
            # FuturesPosition.calculate_pnl / _process_roll's convention:
            # fees = commission * 2 * quantity, charged entirely at close).
            # A flip closes the *entire* prior side (the new opposite-
            # direction open that follows is then free, same as any other
            # open); a same-direction resize only charges for the portion
            # that shrinks back toward zero, not the portion added.
            if prior == 0:
                closed_qty = 0
            elif target == 0 or (prior > 0) != (target > 0):
                closed_qty = abs(prior)
            else:
                closed_qty = max(0, abs(prior) - abs(target))
            fee = futures_types[symbol]['commission'] * 2 * closed_qty
            capital -= fee
            price = s.get('close')
            transactions.append({
                'symbol': symbol, 'date': rebalance_date,
                'action': 'buy' if target > prior else 'sell',
                'quantity': abs(target - prior), 'price': _round(price, 2),
                'fee': round(fee, 2), 'prior_contracts': prior, 'target_contracts': target,
                'gate_reason': s.get('gate_reason'), 'is_seed': is_seed,
            })

            flipped = prior != 0 and target != 0 and (prior > 0) != (target > 0)
            if flipped or target == 0:
                # This transaction's fee is the closing leg's cost alone (the
                # whole prior side on a flip -- the new opposite-direction
                # open that follows is free, same as any other open) --
                # must be folded in before _close_trade reads ot['fees'], or
                # it's silently dropped from the trade's own total (portfolio
                # capital stays correct regardless, since that deduction
                # already happened above; only the per-trade fees/pnl
                # fields were at risk of undercounting).
                ot = open_trade[symbol]
                if ot is not None:
                    ot['fees'] += fee
                _close_trade(symbol, rebalance_date, price)
            ot = open_trade[symbol]  # re-read post-close: _close_trade above may have just cleared it
            if target != 0 and ot is None:
                open_trade[symbol] = {
                    'entry_date': rebalance_date, 'entry_price': price,
                    'direction': 'long' if target > 0 else 'short',
                    'max_contracts': abs(target), 'mtm_pnl': 0.0,
                    'fees': 0.0 if flipped else fee,
                    'close_reason': None,
                }
            elif target != 0 and ot is not None:
                # Resize within the same direction -- extend the existing
                # span rather than starting a new trade; fold in this
                # resize's own fee (zero unless this shrank toward zero) and
                # track the largest size held.
                ot['fees'] += fee
                ot['max_contracts'] = max(ot['max_contracts'], abs(target))
            if (flipped or target == 0) and s.get('gate_reason'):
                # _close_trade already ran above and cleared open_trade;
                # the reason belongs on the trade that just closed, so
                # patch the just-appended row rather than re-opening it.
                trades[-1]['close_reason'] = s.get('gate_reason')

        held_contracts[symbol] = target
        events.append({
            'date': rebalance_date, 'symbol': symbol,
            'close': _round(s.get('close'), 2), 'peak': _round(s.get('peak'), 2),
            'dd_pct': _round(s.get('dd_pct'), 2),
            'avg_r3m': _round(s.get('avg_r3m'), 4), 'avg_r1y': _round(s.get('avg_r1y'), 4),
            'r3m': _round(s.get('r3m'), 4), 'r1y': _round(s.get('r1y'), 4),
            'ts3m': _round(s.get('ts3m'), 4), 'ts1y': _round(s.get('ts1y'), 4),
            'signal': _round(s.get('signal'), 4), 'r1y_pct': _round(s.get('r1y_pct'), 2),
            'regime': s.get('regime'), 'vix_close': _round(vix_close, 2), 'vix_ratio': _round(vix_ratio, 4),
            'vol_regime': vol_regime, 'hv': _round(s.get('hv'), 4), 'risk_scalar': _round(s.get('risk_scalar'), 4),
            'momentum_discount': _round(s.get('momentum_discount'), 2),
            'prior_contracts': prior, 'target_contracts': target, 'is_seed': is_seed,
            'gate_reason': s.get('gate_reason'),
            # Portfolio-level capital snapshot as of this event (after
            # today's mark-to-market and this event's own commission fee,
            # both already applied above) -- previously only available in
            # the separate daily `stats` table (keyed by date only, not
            # per-symbol), so reading an event required cross-referencing
            # a different table by date to see its $ context.
            'capital': round(capital, 2),
            'cum_pnl': round(capital - config.initial_capital, 2),
        })

    def _process_roll(symbol: str, roll_date: date) -> None:
        """Mandatory quarterly contract roll for a currently-held symbol:
        close the expiring contract (full round-trip commission on its own
        quantity, close_reason='roll') and immediately reopen the identical
        size under the new contract at the same price -- net zero PnL/size
        effect, cost is the fee alone. Mirrors FuturesPosition.close()
        (position.py:1785-1877): a roll is a mechanical consequence of the
        contract's own expiration, not a signal decision, so it fires
        regardless of signal_gate_mode/fixed_quantities and doesn't touch
        held_contracts or go through _rebalance_to (which would incorrectly
        charge a second commission for the "reopen" leg -- opening a
        position is free in this fee model, only closing charges, exactly
        as in FuturesPosition.calculate_pnl)."""
        nonlocal capital
        prior = held_contracts[symbol]
        if prior == 0:
            return
        price = prior_close[symbol]
        fee = futures_types[symbol]['commission'] * 2 * abs(prior)
        capital -= fee
        transactions.append({
            'symbol': symbol, 'date': roll_date, 'action': 'roll',
            'quantity': abs(prior), 'price': _round(price, 2),
            'fee': round(fee, 2), 'prior_contracts': prior, 'target_contracts': prior,
            'gate_reason': None, 'is_seed': False,
        })
        ot = open_trade[symbol]
        if ot is not None:
            ot['fees'] += fee
            ot['close_reason'] = 'roll'
            _close_trade(symbol, roll_date, price)
        open_trade[symbol] = {
            'entry_date': roll_date, 'entry_price': price,
            'direction': 'long' if prior > 0 else 'short',
            'max_contracts': abs(prior), 'mtm_pnl': 0.0,
            'fees': 0.0, 'close_reason': None,
        }

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
            vol_regime, vix_close, vix_ratio = _vix_regime_at(vix, seed_date)
            if not config.vix_gating:
                vol_regime = VolRegime.NORMAL  # vix_close/vix_ratio still logged, just not acted on
            if vol_regime not in (VolRegime.SPIKE, VolRegime.EXTREME):  # held_contracts are all 0 here -- hold/halve would be a no-op anyway
                market_stress_scale = VIX_ELEVATED_SCALE if vol_regime == VolRegime.ELEVATED else 1.0
                for symbol in config.symbols:
                    result = _compute_signal_row(symbol, precomputed, seed_date, futures_types, config, market_stress_scale)
                    if result is None:
                        continue
                    target, gate_reason = _apply_signal_gate(held_contracts[symbol], result['target'], result, config)
                    _rebalance_to(symbol, target, seed_date, vol_regime, vix_close=vix_close,
                                  vix_ratio=vix_ratio, signal={**result, 'gate_reason': gate_reason}, is_seed=True)

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
                day_pnl = held_contracts[symbol] * (close - prior_close[symbol]) * futures_types[symbol]['multiplier']
                capital += day_pnl
                ot = open_trade[symbol]
                if ot is not None:
                    ot['mtm_pnl'] += day_pnl
            prior_close[symbol] = close

        # 1.25. Mandatory quarterly contract roll -- unconditional (not
        # gated by signal_gate_mode/fixed_quantities), since it's a
        # mechanical consequence of the contract's own expiration, exactly
        # like the naked path's FuturesPosition.roll_date. Fires on the
        # first actual trading day >= each scheduled roll date (a monotonic
        # pointer rather than an `in` check, in case the scheduled date
        # itself isn't a trading day for these symbols).
        while roll_ptr < len(sorted_roll_dates) and sorted_roll_dates[roll_ptr] <= d:
            for symbol in config.symbols:
                _process_roll(symbol, d)
            roll_ptr += 1

        # 1.5. Daily signal-gate check (signal_gate_mode == 'daily' only),
        # off-cycle from the monthly resize below -- BOTH entry and exit,
        # not exit-only: a currently-held symbol can only be flattened here
        # (its own resize/magnitude stays monthly-only in both modes, so a
        # weakening vol-targeted position doesn't get continuously
        # rebalanced mid-month just because this loop now also checks
        # daily); a currently-flat symbol can open the very day its entry
        # gate first clears, instead of waiting for month-end. Skipped on
        # rebalance_dates themselves since the monthly block below already
        # re-evaluates the same gate that day (avoids a duplicate event).
        # VIX spike/extreme hold-or-halve intentionally stays a monthly-
        # only mechanism -- not extended to off-cycle days here.
        if config.signal_gate_mode == 'daily' and d not in rebalance_dates:
            vol_regime_d, vix_close_d, vix_ratio_d = _vix_regime_at(vix, d)
            if not config.vix_gating:
                vol_regime_d = VolRegime.NORMAL
            market_stress_scale_d = VIX_ELEVATED_SCALE if vol_regime_d == VolRegime.ELEVATED else 1.0

            for symbol in config.symbols:
                prior = held_contracts[symbol]
                result = _compute_signal_row(symbol, precomputed, d, futures_types, config, market_stress_scale_d)
                if result is None:
                    continue

                if prior != 0:
                    is_long = prior > 0
                    reason = _signal_gate_reason(result['signal'], result['ts3m'], result['ts1y'], is_long,
                                                  config.ts_exit_threshold, config.exit_on_ts_crossover)
                    if reason is not None:
                        _rebalance_to(symbol, 0, d, vol_regime_d, vix_close=vix_close_d, vix_ratio=vix_ratio_d,
                                      signal={**result, 'gate_reason': reason})
                elif result['target'] != 0:
                    target, gate_reason = _apply_signal_gate(0, result['target'], result, config)
                    if target != 0:
                        _rebalance_to(symbol, target, d, vol_regime_d, vix_close=vix_close_d,
                                      vix_ratio=vix_ratio_d, signal={**result, 'gate_reason': gate_reason})

        # 2. On rebalance dates, resize toward the vol-targeted signal,
        # gated by the spot-VIX regime (mirrors
        # tsmom_rebalance.compute_rebalance_targets' early-return shape).
        if d in rebalance_dates:
            vol_regime, vix_close, vix_ratio = _vix_regime_at(vix, d)
            if not config.vix_gating:
                vol_regime = VolRegime.NORMAL  # vix_close/vix_ratio still logged, just not acted on

            if vol_regime in (VolRegime.SPIKE, VolRegime.EXTREME):
                for symbol in config.symbols:
                    prior = held_contracts[symbol]
                    target = round(prior / 2) if vol_regime == VolRegime.EXTREME else prior
                    close_row = full_price_data[symbol].filter(pl.col('ts_event') <= d).tail(1)
                    close = float(close_row['close'][0]) if close_row.height > 0 else None
                    _rebalance_to(symbol, target, d, vol_regime, vix_close=vix_close, vix_ratio=vix_ratio,
                                  signal={'close': close})
            else:
                market_stress_scale = VIX_ELEVATED_SCALE if vol_regime == VolRegime.ELEVATED else 1.0
                for symbol in config.symbols:
                    result = _compute_signal_row(symbol, precomputed, d, futures_types, config, market_stress_scale)
                    if result is None:
                        continue
                    target, gate_reason = _apply_signal_gate(held_contracts[symbol], result['target'], result, config)
                    _rebalance_to(symbol, target, d, vol_regime, vix_close=vix_close,
                                  vix_ratio=vix_ratio, signal={**result, 'gate_reason': gate_reason})

        daily_rows.append({'date': d, 'capital': round(capital, 2)})

    # Force-close any position still open at the end of the window -- same
    # spirit as Backtester's close_all sweep: a trade that's still running
    # when the backtest ends isn't abandoned, it's marked at the last
    # available price so `trades` doesn't silently drop it.
    if all_dates:
        last_date = all_dates[-1]
        for symbol in config.symbols:
            ot = open_trade[symbol]
            if ot is not None:
                close_row = full_price_data[symbol].filter(pl.col('ts_event') <= last_date).tail(1)
                last_price = float(close_row['close'][0]) if close_row.height > 0 else None
                ot['close_reason'] = 'end_of_backtest'
                _close_trade(symbol, last_date, last_price)

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

    return {
        'stats': stats, 'events': events,
        'transactions': pl.DataFrame(transactions),
        'trades': pl.DataFrame(trades).sort('entry_date') if trades else pl.DataFrame(trades),
    }

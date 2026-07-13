"""
TSMOM monthly rebalancing orchestrator — has an IB dependency (contract
resolution, historical bars, live VX/VIX fallback, current positions),
via the ib_tools.ibpysync connectivity layer.

Pure signal math lives in options_bt.domain.tsmom_signal (used by both this
live orchestrator and the duckdb-backed backtest); this module wires that
signal up to IBPySync, applies the VX vol-spike gate, and turns the result
into a per-instrument rebalance plan (contract counts), without placing any
orders itself.

The VX/expiry-resolution helpers below intentionally mirror the equivalent
logic in ib_tools' combined_monitor.py (live VX front-month via CFE, fall
back to VIX spot's last RTH close when VX is unavailable, e.g. weekend
close), kept local so this module only depends on ib_tools.ibpysync, not on
any of ib_tools' monitor-specific scripts.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ib_tools.ibpysync import IBPySync

from options_bt.domain.enums import TrendRegime, VolRegime
from options_bt.domain.tsmom_signal import (
    apply_cluster_risk_cap,
    calculate_trend_strength,
    classify_regime,
    classify_signal_confidence,
    compute_desired_risk_budget,
    compute_n_effective,
    compute_position_scalar,
    compute_signal_confidence,
    compute_vol_ratio,
)

log = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')

# VX spike ratio bands (vx_current / vx_ma63) -> regime
VX_ELEVATED_RATIO = 1.3
VX_SPIKE_RATIO    = 1.5
VX_EXTREME_RATIO  = 2.0
VX_ELEVATED_SCALE = 0.6   # reduce all positions to this fraction of target when 'elevated'


# ------------------------------------------------------------------
# VX / expiry resolution
# ------------------------------------------------------------------

def _vx_is_stale() -> bool:
    """VX futures (CFE) trade Sun 6pm - Fri 4:15pm ET with no daily break."""
    now = datetime.now(ET)
    weekday = now.weekday()
    t = now.time()
    if weekday == 5:
        return True
    if weekday == 6 and t.hour < 18:
        return True
    if weekday == 4 and (t.hour, t.minute) >= (16, 15):
        return True
    return False


def _get_nearest_vx_expiry(ib: IBPySync, min_days: int = 3) -> str:
    vx = IBPySync.future('VIX', exchange='CFE')
    vx.tradingClass = 'VX'
    details = ib.req_contract_details(vx)
    cutoff = date.today() + timedelta(days=min_days)
    expiries = sorted(
        d.contract.lastTradeDateOrContractMonth
        for d in details
        if d.contract.tradingClass == 'VX'
        and d.contract.lastTradeDateOrContractMonth
        and datetime.strptime(
            d.contract.lastTradeDateOrContractMonth[:8], '%Y%m%d'
        ).date() >= cutoff
    )
    if not expiries:
        raise RuntimeError('No VX monthly contracts found beyond min_days cutoff')
    nearest = expiries[0][:6]
    log.info('Auto-resolved VX expiry: %s (min_days=%d)', nearest, min_days)
    return nearest


def _get_vx_future(ib: IBPySync, expiry: str):
    vx = IBPySync.future('VIX', exchange='CFE', expiration=expiry)
    vx.tradingClass = 'VX'
    ib.qualify_contracts(vx)
    log.info('VX contract: %s', vx.localSymbol)
    return vx


def get_nearest_quarterly_expiry(ib: IBPySync, symbol: str, exchange: str, min_days: int = 7,
                                  multiplier: str = '') -> str:
    """Nearest expiry with at least min_days remaining, as the full
    YYYYMMDD IB already gave us in req_contract_details -- truncating to
    YYYYMM and letting IB re-resolve from the partial month was observed to
    fail qualify_contracts for some symbols (MCL/MZC/MZW) even though the
    exact same month was a real, listed contract a moment earlier; passing
    back the untruncated date IB itself returned avoids that re-resolution
    step entirely."""
    c = IBPySync.future(symbol, exchange=exchange, multiplier=multiplier)
    details = ib.req_contract_details(c)
    log.debug(
        'req_contract_details(%s, %s) returned %d contract(s): %s',
        symbol, exchange, len(details),
        [(d.contract.lastTradeDateOrContractMonth, d.contract.localSymbol,
          d.contract.tradingClass, d.contract.multiplier) for d in details],
    )
    cutoff = date.today() + timedelta(days=min_days)
    expiries = sorted(
        d.contract.lastTradeDateOrContractMonth
        for d in details
        if d.contract.lastTradeDateOrContractMonth
        and datetime.strptime(
            d.contract.lastTradeDateOrContractMonth[:8], '%Y%m%d'
        ).date() >= cutoff
    )
    if not expiries:
        raise RuntimeError(f'No {symbol} ({exchange}) contracts found beyond {min_days}d')
    nearest = expiries[0]
    log.info('Auto-resolved %s (%s) expiry: %s (all candidates: %s)', symbol, exchange, nearest, expiries)
    return nearest


# ------------------------------------------------------------------
# VX vol-spike gate
# ------------------------------------------------------------------

def fetch_vx_spike_ratio(ib: IBPySync, vx_expiry: str = 'auto', min_days: int = 3) -> tuple[float, float]:
    """
    Returns (vx_current, vx_ma63). Raises RuntimeError if no usable VX/VIX
    data is available at all.

    vx_ma63 always comes from VX historical daily bars (historical data is
    available even when the market/contract has no live price, e.g.
    weekends). vx_current prefers the live VX front-month price; if that is
    unavailable (stale/weekend close) it falls back to VIX spot's last
    close, then as a last resort to the most recent VX historical close.
    """
    expiry = _get_nearest_vx_expiry(ib, min_days) if vx_expiry == 'auto' else vx_expiry
    vx = _get_vx_future(ib, expiry)

    bars = ib.get_historical_bars(vx, duration='90 d', bar_size='1 day')
    if bars is None or bars.height == 0:
        raise RuntimeError('No VX historical bars available — cannot compute vx_ma63')

    closes = bars['close'].tail(70)
    vx_ma63 = closes.tail(63).mean()
    if vx_ma63 is None or math.isnan(vx_ma63) or vx_ma63 <= 0:
        raise RuntimeError('Insufficient VX history to compute a 63-day MA')

    # Delayed data type, not live: this account has no CFE/CBOE real-time
    # subscription, so reqMktData on VX/VIX would otherwise hit error 10168
    # and burn ~100s per get_price call before falling through (see
    # combined_monitor.py, which sidesteps the same issue the same way).
    ib.set_market_data_type(3)

    vx_current = None
    if not _vx_is_stale():
        try:
            vx_current = ib.get_price(vx)
        except Exception as exc:
            log.warning('VX live price unavailable (%s) — falling back to VIX spot close', exc)

    if vx_current is None:
        try:
            vix = IBPySync.index('VIX', exchange='CBOE')
            ib.qualify_contracts(vix)
            vx_current = ib.get_price(vix)
            log.info('Using VIX spot last close as vx_current fallback: %.2f', vx_current)
        except Exception as exc:
            log.warning('VIX spot also unavailable (%s) — using last VX historical close', exc)
            vx_current = float(closes[-1])

    return float(vx_current), float(vx_ma63)


def check_vol_regime(vx_ratio: float) -> VolRegime:
    """Normal | Elevated | Spike | Extreme from vx_current / vx_ma63.

    Deliberately one-sided: every threshold here checks vx_ratio being
    HIGH (1.3/1.5/2.0) -- there is no symmetric low-vx_ratio bucket, and
    that's not an oversight. This function's job is a portfolio-wide risk-
    management gate (feeds market_stress_scale, and the spike/extreme
    hold-or-halve bypass), not a regime-confidence detector -- "the market
    looks unusually calm" isn't a risk to manage the same way "the market
    looks dangerous" is, so nothing here classifies it. (Per-instrument,
    asset-specific vol state -- including a low-vol-ratio bucket -- is a
    different, independent mechanism: see SignalConfidenceRegime /
    classify_signal_confidence in tsmom_signal.py.)"""
    if vx_ratio > VX_EXTREME_RATIO:
        return VolRegime.EXTREME
    if vx_ratio > VX_SPIKE_RATIO:
        return VolRegime.SPIKE
    if vx_ratio > VX_ELEVATED_RATIO:
        return VolRegime.ELEVATED
    return VolRegime.NORMAL


# ------------------------------------------------------------------
# Positions
# ------------------------------------------------------------------

def _current_contracts(ib: IBPySync, contract) -> int:
    """Net signed position size for `contract`'s conId across the account."""
    try:
        positions = ib.ib.positions()
    except Exception as exc:
        log.warning('Could not fetch current positions (%s) — assuming 0', exc)
        return 0
    total = 0.0
    for p in positions:
        if p.contract.conId == contract.conId:
            total += p.position
    return int(round(total))


# ------------------------------------------------------------------
# Main orchestration
# ------------------------------------------------------------------

def _compute_signal(ib: IBPySync, instr: dict, min_days: int, vol_target: float, long_only: bool,
                     momentum_discount: float, signal_confidence_cfg: dict):
    """Stage 1: resolve the contract, fetch bars, compute the TSMOM signal
    for one instrument. No budget/sizing here -- that needs to know every
    instrument's signal first (to derive n_effective), so it happens in a
    later pass. Returns a dict of everything sizing/reporting need.

    signal_confidence_cfg: {'enabled': bool, 'low_threshold': float,
    'high_threshold': float, 'high_vol': float, 'low_vol': float} -- see
    compute_signal_confidence(). When disabled (the default), this stage
    still returns signal_confidence=1.0 (no-op) and vol_ratio=None."""
    contract = _resolve_contract(ib, instr, min_days)
    # Historical bars come from the continuous front-month contract, not the
    # dated one -- a single expiry-specific Future only has bars back to
    # when that contract was listed (well under a year), which silently
    # starves the 252-day (ts1y) momentum calc and makes classify_regime()
    # return 'Unknown' for every symbol whose nearest contract hasn't been
    # listed a full year yet.
    #
    # signal_symbol lets a recently-listed thin contract (e.g. the CBOT
    # micro grains, all launched ~Feb 2025) borrow the full-size contract's
    # much longer history instead -- same cents/bushel quote scale, just a
    # different multiplier -- while sizing/orders still use the actually-
    # traded micro contract (instr['ib_symbol']/`contract` above).
    signal_symbol = instr.get('signal_symbol') or instr.get('ib_symbol') or instr['symbol']
    cont = IBPySync.cont_future(signal_symbol, exchange=instr.get('exchange', 'CME'))
    ib.qualify_contracts(cont)
    bars = ib.get_historical_bars(cont, duration='3 y', bar_size='1 day')
    if bars is None or bars.height < 64:
        raise RuntimeError(f"Insufficient bar history for {instr['symbol']} ({bars.height if bars is not None else 0} rows)")

    df = calculate_trend_strength(bars)
    last = df.tail(1)
    trend_strength = last['ts'][0]
    ts3m = last['ts3m'][0]
    ts1y = last['ts1y'][0]
    daily_std_last = last['daily_std'][0] if 'daily_std' in last.columns else None
    last_close = float(last['close'][0])
    dd_raw = last['dd'][0] if 'dd' in last.columns else None
    dd_pct = dd_raw * 100 if dd_raw is not None else None

    regime = classify_regime(ts3m, ts1y)

    signal_for_scalar = trend_strength
    if long_only and signal_for_scalar is not None and not math.isnan(signal_for_scalar):
        signal_for_scalar = max(0.0, signal_for_scalar)

    # hv/risk_scalar/momentum_discount recomputed here (mirrors compute_
    # position_scalar's own internal math) purely for reporting -- so the
    # printed report shows *why* a given trend_strength did or didn't turn
    # into a trade.
    hv = daily_std_last * math.sqrt(252) if daily_std_last and daily_std_last > 0 else None
    risk_scalar = max(0.25, min(2.0, vol_target / hv)) if hv else 1.0
    momentum_discount = momentum_discount if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0

    # signal_confidence: opt-in, per-instrument discount on trust in THIS
    # instrument's signal when ITS OWN vol_ratio (short/long realized vol,
    # asset-specific) is unusual -- not VIX/VX-driven, orthogonal to
    # market_stress_scale (portfolio-wide) and momentum_discount (fast/
    # slow sign disagreement). Computed off the same continuous-front-
    # month bars already fetched above, no extra IB calls.
    vol_ratio = None
    signal_confidence_regime = None
    signal_confidence = 1.0
    if signal_confidence_cfg.get('enabled'):
        conf_df = compute_vol_ratio(df)
        vol_ratio = conf_df.tail(1)['vol_ratio'][0]
        signal_confidence_regime = classify_signal_confidence(
            vol_ratio, signal_confidence_cfg['low_threshold'], signal_confidence_cfg['high_threshold'],
        )
        signal_confidence = compute_signal_confidence(
            vol_ratio, signal_confidence_cfg['low_threshold'], signal_confidence_cfg['high_threshold'],
            high_vol_discount=signal_confidence_cfg['high_vol'], low_vol_discount=signal_confidence_cfg['low_vol'],
        )

    return {
        'contract': contract,
        'signal': trend_strength,
        'signal_for_scalar': signal_for_scalar,
        'ts3m': ts3m,
        'ts1y': ts1y,
        'daily_std': daily_std_last,
        'hv': hv,
        'risk_scalar': risk_scalar,
        'momentum_discount': momentum_discount,
        'vol_ratio': vol_ratio,
        'signal_confidence_regime': signal_confidence_regime,
        'signal_confidence': signal_confidence,
        'close': last_close,
        'dd_pct': dd_pct,
        'regime': regime,
        'cluster': instr.get('cluster', 'other'),
        'multiplier': instr.get('multiplier'),
    }


def compute_rebalance_targets(ib: IBPySync, instruments: list[dict], config: dict) -> list[dict]:
    """
    Runs the VX spike gate first. If a spike/extreme regime is detected,
    returns early with target_contracts == current_contracts (held
    unchanged), halved on 'extreme', and skips signal computation entirely.

    Otherwise this runs in three stages:
      1. _compute_signal() for every instrument -- resolves the contract,
         fetches bars, computes trend_strength/regime/hv. No budget yet.
      2. Determine which clusters have a live signal (abs(signal_for_scalar)
         above min_conviction) -> n_effective -> desired_risk_budget
         (account_equity * target_portfolio_vol / sqrt(n_effective)) ->
         budget_constant = desired_risk_budget / vol_target. This replaces
         the old flat --max-notional as the dollar figure that converts
         scalar -> target_notional, so instruments aren't all sized off the
         same flat budget regardless of how many other clusters are active.
      3. Per instrument: scalar -> target_notional (budget_constant * scalar,
         optionally capped by instr['max_notional'] if set as a hard
         ceiling) -> target_contracts, clamped to max_contracts (now just a
         sanity backstop). Then apply_cluster_risk_cap() rescales any
         cluster whose aggregate dollar-vol risk exceeds
         max_cluster_risk_pct of total portfolio risk -- e.g. 4 grain
         micros that each individually look fine can still collectively be
         one oversized bet on the shared ag-complex factor.

    config keys: vol_target (float), max_contracts (int, per-instrument
    default/backstop), vx_expiry (str, 'auto' or YYYYMM), long_only (bool),
    momentum_discount (float), min_days (int, expiry-resolution margin),
    account_equity (float, required for sizing), target_portfolio_vol
    (float), max_cluster_risk_pct (float), min_conviction (float),
    max_lot_overrun_pct (float, lot-size exception tolerance for
    apply_cluster_risk_cap's conviction-priority allocation),
    enable_signal_confidence (bool, default False), signal_confidence_
    low_threshold/signal_confidence_high_threshold (float), signal_
    confidence_high_vol/signal_confidence_low_vol (float, discount factors).
    """
    vol_target = config.get('vol_target', 0.15)
    long_only = config.get('long_only', False)
    momentum_discount = config.get('momentum_discount', 0.5)
    default_max_contracts = config.get('max_contracts', 15)
    min_days = config.get('min_days', 7)
    account_equity = config.get('account_equity')
    target_portfolio_vol = config.get('target_portfolio_vol', 0.15)
    max_cluster_risk_pct = config.get('max_cluster_risk_pct', 0.25)
    min_conviction = config.get('min_conviction', 0.05)
    max_lot_overrun_pct = config.get('max_lot_overrun_pct', 0.5)
    signal_confidence_cfg = {
        'enabled': config.get('enable_signal_confidence', False),
        'low_threshold': config.get('signal_confidence_low_threshold', 0.7),
        'high_threshold': config.get('signal_confidence_high_threshold', 1.5),
        'high_vol': config.get('signal_confidence_high_vol', 0.5),
        'low_vol': config.get('signal_confidence_low_vol', 1.0),
    }

    vx_current, vx_ma63 = fetch_vx_spike_ratio(ib, config.get('vx_expiry', 'auto'))
    vx_ratio = vx_current / vx_ma63
    vol_regime = check_vol_regime(vx_ratio)
    log.info('VX spike gate — vx_current=%.2f  vx_ma63=%.2f  ratio=%.3f  regime=%s',
             vx_current, vx_ma63, vx_ratio, vol_regime.value)

    if vol_regime in (VolRegime.SPIKE, VolRegime.EXTREME):
        log.warning('VX %s detected (ratio=%.3f) — holding existing positions, skipping rebalance',
                     vol_regime.value, vx_ratio)
        targets = []
        for instr in instruments:
            try:
                contract = _resolve_contract(ib, instr, min_days)
                current = _current_contracts(ib, contract)
            except Exception as exc:
                log.warning('Could not resolve %s during VX %s (%s) — reporting current=0',
                            instr['symbol'], vol_regime.value, exc)
                current = 0
            target = round(current / 2) if vol_regime == VolRegime.EXTREME else current
            targets.append({
                'symbol': instr['symbol'],
                'target_contracts': target,
                'current_contracts': current,
                'signal': None,
                'regime': None,
                'vx_current': vx_current,
                'vx_ma63': vx_ma63,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
                # n_effective/risk_budget/budget_constant aren't computed on
                # this early-return path (signal computation, which they
                # depend on, is skipped entirely during a spike/extreme) --
                # only what's already in scope from config is available.
                'account_equity': account_equity,
                'vol_target': vol_target,
                'target_portfolio_vol': target_portfolio_vol,
                'max_cluster_risk_pct': max_cluster_risk_pct,
                'max_lot_overrun_pct': max_lot_overrun_pct,
            })
        return targets

    market_stress_scale = VX_ELEVATED_SCALE if vol_regime == VolRegime.ELEVATED else 1.0

    # Stage 1: signal for every instrument, no sizing yet.
    signals: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for instr in instruments:
        symbol = instr['symbol']
        try:
            signals[symbol] = _compute_signal(ib, instr, min_days, vol_target, long_only, momentum_discount,
                                              signal_confidence_cfg)
        except Exception as exc:
            log.error('Failed to compute signal for %s: %s', symbol, exc)
            errors[symbol] = str(exc)

    # Stage 2: derive the risk budget from which clusters actually have a
    # live signal right now, not from the raw instrument count.
    active_clusters = {
        s['cluster'] for s in signals.values()
        if s['signal_for_scalar'] is not None
        and not (isinstance(s['signal_for_scalar'], float) and math.isnan(s['signal_for_scalar']))
        and abs(s['signal_for_scalar']) > min_conviction
    }
    n_effective = compute_n_effective(active_clusters)
    if account_equity:
        desired_risk_budget = compute_desired_risk_budget(account_equity, target_portfolio_vol, n_effective)
        budget_constant = desired_risk_budget / vol_target if vol_target else 0.0
    else:
        desired_risk_budget = None
        budget_constant = None
    log.info('Risk budget — active_clusters=%s  n_effective=%d  desired_risk_budget=%s  budget_constant=%s',
             sorted(active_clusters), n_effective,
             f'{desired_risk_budget:.0f}' if desired_risk_budget is not None else 'N/A (no account_equity)',
             f'{budget_constant:.0f}' if budget_constant is not None else 'N/A')

    # Stage 3: per-instrument sizing off the derived budget, then the
    # cluster risk cap as a second pass.
    targets = []
    for instr in instruments:
        symbol = instr['symbol']
        if symbol in errors:
            targets.append({
                'symbol': symbol,
                'target_contracts': None,
                'current_contracts': None,
                'signal': None,
                'regime': None,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
                'error': errors[symbol],
                'account_equity': account_equity,
                'n_effective': n_effective,
                'risk_budget': desired_risk_budget,
                'vol_target': vol_target,
                'target_portfolio_vol': target_portfolio_vol,
                'budget_constant': budget_constant,
                'max_cluster_risk_pct': max_cluster_risk_pct,
                'max_lot_overrun_pct': max_lot_overrun_pct,
            })
            continue

        s = signals[symbol]
        try:
            multiplier = s['multiplier']
            max_contracts = instr.get('max_contracts', default_max_contracts)
            max_notional_ceiling = instr.get('max_notional')

            if multiplier is None:
                raise ValueError(f'{symbol}: instrument config missing multiplier')
            if budget_constant is None:
                raise ValueError(f'{symbol}: account_equity not configured — cannot derive a risk budget')

            scalar = compute_position_scalar(
                s['signal_for_scalar'], s['daily_std'], vol_target, s['regime'],
                momentum_discount=momentum_discount, signal_confidence=s['signal_confidence'],
            )
            scalar *= market_stress_scale

            # raw_notional is budget_constant * scalar before the optional
            # per-instrument max_notional ceiling clamp; target_notional is
            # what actually drives target_contracts below. They only differ
            # when max_notional_ceiling clips raw_notional.
            raw_notional = budget_constant * scalar
            target_notional = raw_notional
            if max_notional_ceiling is not None:
                target_notional = max(-max_notional_ceiling, min(max_notional_ceiling, target_notional))

            contract_notional_value = s['close'] * multiplier
            # continuous_contracts is the unrounded, unclamped value the
            # cluster cap operates on -- rescaling and rounding an already-
            # rounded-and-clamped integer (the old target_contracts below)
            # double-rounds, which can zero out large-multiplier instruments
            # (full-size ES/NQ/JPY/etc) that would survive on the true
            # continuous math. target_contracts is still computed the same
            # way here for any caller that wants a pre-cluster-cap integer
            # (e.g. granularity-tracking instrumentation) -- apply_cluster_
            # risk_cap is what now does the real, single round+clamp.
            continuous_contracts = target_notional / contract_notional_value if contract_notional_value else 0.0
            target_contracts = round(target_notional / contract_notional_value) if contract_notional_value else 0
            target_contracts = max(-max_contracts, min(max_contracts, target_contracts))

            current_contracts = _current_contracts(ib, s['contract'])

            targets.append({
                'symbol': symbol,
                'target_contracts': target_contracts,
                'continuous_contracts': continuous_contracts,
                'max_contracts': max_contracts,
                'current_contracts': current_contracts,
                'signal': s['signal'],
                'scalar': scalar,
                'ts3m': s['ts3m'],
                'ts1y': s['ts1y'],
                'daily_std': s['daily_std'],
                'hv': s['hv'],
                'risk_scalar': s['risk_scalar'],
                'momentum_discount': s['momentum_discount'],
                'vol_ratio': s['vol_ratio'],
                'signal_confidence_regime': s['signal_confidence_regime'],
                'signal_confidence': s['signal_confidence'],
                'market_stress_scale': market_stress_scale,
                'close': s['close'],
                'multiplier': multiplier,
                'raw_notional': raw_notional,
                'target_notional': target_notional,
                'cluster': s['cluster'],
                'dd_pct': s['dd_pct'],
                'regime': s['regime'],
                'vx_current': vx_current,
                'vx_ma63': vx_ma63,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
                # Portfolio-level context, identical across every
                # instrument this run -- included per-row so each CSV row
                # is self-contained (no need to cross-reference the log for
                # what budget/equity this run used).
                'account_equity': account_equity,
                'n_effective': n_effective,
                'risk_budget': desired_risk_budget,
                'vol_target': vol_target,
                'target_portfolio_vol': target_portfolio_vol,
                'budget_constant': budget_constant,
                'max_cluster_risk_pct': max_cluster_risk_pct,
                'max_lot_overrun_pct': max_lot_overrun_pct,
            })
        except Exception as exc:
            log.error('Failed to compute rebalance target for %s: %s', symbol, exc)
            targets.append({
                'symbol': symbol,
                'target_contracts': None,
                'current_contracts': None,
                'signal': None,
                'regime': None,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
                'error': str(exc),
            })

    total_risk_target = account_equity * target_portfolio_vol if account_equity else None
    apply_cluster_risk_cap(targets, max_cluster_risk_pct, total_risk_target, n_effective,
                          max_lot_overrun_pct=max_lot_overrun_pct)
    return targets


def _resolve_contract(ib: IBPySync, instr: dict, min_days: int):
    ib_symbol = instr.get('ib_symbol') or instr['symbol']
    # Only pass multiplier when ib_symbol diverges from our local symbol
    # (i.e. a genuine same-ticker collision like SI/SIL) -- passing it
    # unconditionally risks breaking already-working contracts if our
    # multiplier's string formatting doesn't exactly match what IB has on
    # file (e.g. "0.5" vs "0.50").
    multiplier = str(instr.get('multiplier', '') or '') if ib_symbol != instr['symbol'] else ''
    expiry = instr.get('expiry', 'auto')
    if expiry == 'auto':
        expiry = get_nearest_quarterly_expiry(ib, ib_symbol, instr.get('exchange', 'CME'), min_days,
                                               multiplier=multiplier)
    contract = IBPySync.future(ib_symbol, exchange=instr.get('exchange', 'CME'), expiration=expiry,
                               multiplier=multiplier)
    ib.qualify_contracts(contract)
    return contract


def _fmt(v, spec='+.3f'):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 'N/A'
    return format(v, spec)


def print_rebalance_report(targets: list[dict]) -> str:
    """Pretty-print (and return as a string) the rebalancing plan."""
    lines = ['TSMOM Rebalance Report', '=' * 60]
    for t in targets:
        if t.get('error'):
            lines.append(f"{t['symbol']:6s}  ERROR: {t['error']}")
            continue
        lines.append(
            f"{t['symbol']:6s}  target={t['target_contracts']!s:>4}  "
            f"current={t['current_contracts']!s:>4}  signal={_fmt(t.get('signal')):>7}  "
            f"ts3m={_fmt(t.get('ts3m')):>7}  ts1y={_fmt(t.get('ts1y')):>7}  "
            f"close={_fmt(t.get('close'), '.2f'):>9}  dd_pct={_fmt(t.get('dd_pct'), '.2f'):>7}  "
            f"daily_std={_fmt(t.get('daily_std'), '.4f'):>7}  hv={_fmt(t.get('hv'), '.3f'):>6}  "
            f"risk_scalar={_fmt(t.get('risk_scalar'), '.3f'):>6}  momentum_discount={_fmt(t.get('momentum_discount'), '.2f'):>5}  "
            f"signal_confidence={_fmt(t.get('signal_confidence'), '.2f'):>5}  "
            f"regime={t['regime'].capitalize() if t.get('regime') else 'N/A':<10}  "
            f"vx_current={_fmt(t.get('vx_current'), '.2f'):>6}  vx_ma63={_fmt(t.get('vx_ma63'), '.2f'):>6}  "
            f"vx_ratio={t['vx_ratio']:.3f}  vol_regime={t['vol_regime'].capitalize()}  "
            f"market_stress_scale={_fmt(t.get('market_stress_scale'), '.2f')}"
            + ("  INFEASIBLE (cluster cap < min contract risk in this cluster)" if t.get('infeasible') else "")
        )
    report = '\n'.join(lines)
    print(report)
    return report

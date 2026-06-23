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

from options_bt.domain.tsmom_signal import calculate_trend_strength, classify_regime, compute_position_scalar

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


def get_nearest_quarterly_expiry(ib: IBPySync, symbol: str, exchange: str, min_days: int = 7) -> str:
    """Nearest quarterly expiry (YYYYMM) with at least min_days remaining."""
    c = IBPySync.future(symbol, exchange=exchange)
    details = ib.req_contract_details(c)
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
    nearest = expiries[0][:6]
    log.info('Auto-resolved %s (%s) expiry: %s', symbol, exchange, nearest)
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


def check_vol_regime(vx_ratio: float) -> str:
    """'normal' | 'elevated' | 'spike' | 'extreme' from vx_current / vx_ma63."""
    if vx_ratio > VX_EXTREME_RATIO:
        return 'extreme'
    if vx_ratio > VX_SPIKE_RATIO:
        return 'spike'
    if vx_ratio > VX_ELEVATED_RATIO:
        return 'elevated'
    return 'normal'


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

def compute_rebalance_targets(ib: IBPySync, instruments: list[dict], config: dict) -> list[dict]:
    """
    Runs the VX spike gate first. If a spike/extreme regime is detected,
    returns early with target_contracts == current_contracts (held
    unchanged), halved on 'extreme', and skips signal computation entirely.

    Otherwise, for each instrument: resolves the contract, fetches 3y of
    daily bars, computes the TSMOM signal, applies vol targeting + regime
    discount (+ the 'elevated' 60% scale-down), and turns the resulting
    scalar into a target contract count clamped to the instrument's
    max_contracts.

    config keys: vol_target (float), max_contracts (int, per-instrument
    default), vx_expiry (str, 'auto' or YYYYMM), long_only (bool),
    regime_discount (float), min_days (int, expiry-resolution margin).
    """
    vol_target = config.get('vol_target', 0.15)
    long_only = config.get('long_only', False)
    regime_discount = config.get('regime_discount', 0.5)
    default_max_contracts = config.get('max_contracts', 5)
    min_days = config.get('min_days', 7)

    vx_current, vx_ma63 = fetch_vx_spike_ratio(ib, config.get('vx_expiry', 'auto'))
    vx_ratio = vx_current / vx_ma63
    vol_regime = check_vol_regime(vx_ratio)
    log.info('VX spike gate — vx_current=%.2f  vx_ma63=%.2f  ratio=%.3f  regime=%s',
             vx_current, vx_ma63, vx_ratio, vol_regime)

    if vol_regime in ('spike', 'extreme'):
        log.warning('VX %s detected (ratio=%.3f) — holding existing positions, skipping rebalance',
                     vol_regime, vx_ratio)
        targets = []
        for instr in instruments:
            try:
                contract = _resolve_contract(ib, instr, min_days)
                current = _current_contracts(ib, contract)
            except Exception as exc:
                log.warning('Could not resolve %s during VX %s (%s) — reporting current=0',
                            instr['symbol'], vol_regime, exc)
                current = 0
            target = round(current / 2) if vol_regime == 'extreme' else current
            targets.append({
                'symbol': instr['symbol'],
                'target_contracts': target,
                'current_contracts': current,
                'signal': None,
                'regime': None,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
            })
        return targets

    position_scale = VX_ELEVATED_SCALE if vol_regime == 'elevated' else 1.0

    targets = []
    for instr in instruments:
        symbol = instr['symbol']
        try:
            contract = _resolve_contract(ib, instr, min_days)
            bars = ib.get_historical_bars(contract, duration='3 y', bar_size='1 day')
            if bars is None or bars.height < 64:
                raise RuntimeError(f'Insufficient bar history for {symbol} ({bars.height if bars is not None else 0} rows)')

            df = calculate_trend_strength(bars)
            last = df.tail(1)
            trend_strength = last['trend_strength'][0]
            ts3m = last['ts3m'][0]
            ts1y = last['ts1y'][0]
            daily_std_last = last['daily_std'][0] if 'daily_std' in last.columns else None
            last_close = float(last['close'][0])

            regime = classify_regime(ts3m, ts1y)

            signal_for_scalar = trend_strength
            if long_only and signal_for_scalar is not None and not math.isnan(signal_for_scalar):
                signal_for_scalar = max(0.0, signal_for_scalar)

            scalar = compute_position_scalar(
                signal_for_scalar, daily_std_last, vol_target, regime,
                regime_discount=regime_discount,
            )
            scalar *= position_scale

            max_notional = instr.get('max_notional')
            multiplier = instr.get('multiplier')
            max_contracts = instr.get('max_contracts', default_max_contracts)

            if max_notional is None or multiplier is None:
                raise ValueError(f'{symbol}: instrument config missing max_notional/multiplier')

            target_notional = max_notional * scalar
            contract_notional_value = last_close * multiplier
            target_contracts = round(target_notional / contract_notional_value) if contract_notional_value else 0
            target_contracts = max(-max_contracts, min(max_contracts, target_contracts))

            current_contracts = _current_contracts(ib, contract)

            targets.append({
                'symbol': symbol,
                'target_contracts': target_contracts,
                'current_contracts': current_contracts,
                'signal': trend_strength,
                'regime': regime,
                'vx_ratio': vx_ratio,
                'vol_regime': vol_regime,
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

    return targets


def _resolve_contract(ib: IBPySync, instr: dict, min_days: int):
    expiry = instr.get('expiry', 'auto')
    if expiry == 'auto':
        expiry = get_nearest_quarterly_expiry(ib, instr['symbol'], instr.get('exchange', 'CME'), min_days)
    contract = IBPySync.future(instr['symbol'], exchange=instr.get('exchange', 'CME'), expiration=expiry)
    ib.qualify_contracts(contract)
    return contract


def print_rebalance_report(targets: list[dict]) -> str:
    """Pretty-print (and return as a string) the rebalancing plan."""
    lines = ['TSMOM Rebalance Report', '=' * 60]
    for t in targets:
        if t.get('error'):
            lines.append(f"{t['symbol']:6s}  ERROR: {t['error']}")
            continue
        signal_str = f"{t['signal']:+.3f}" if t['signal'] is not None else 'N/A'
        lines.append(
            f"{t['symbol']:6s}  target={t['target_contracts']!s:>4}  "
            f"current={t['current_contracts']!s:>4}  signal={signal_str:>7}  "
            f"regime={t['regime'] or 'N/A':<10}  "
            f"vx_ratio={t['vx_ratio']:.3f}  vol_regime={t['vol_regime']}"
        )
    report = '\n'.join(lines)
    print(report)
    return report

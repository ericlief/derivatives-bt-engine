"""
CLI entry point for the TSMOM monthly rebalance (live, via IB).

Run (dry-run, default — no orders placed):
    python -m options_bt.live.run_tsmom_rebalance --instruments MES,MNQ

Live (places orders after a typed confirmation):
    python -m options_bt.live.run_tsmom_rebalance --instruments MES,MNQ --live

--instruments accepts either a comma-separated symbol list (defaults below
fill in exchange/multiplier/max_notional) or a path to a JSON file with the
full instrument config list (see tsmom_rebalance.py docstring for the
format: symbol, exchange, expiry, multiplier, max_contracts, max_notional).

This depends only on ib_tools.ibpysync / ib_tools.alerts for IB
connectivity — all strategy/signal logic lives in options_bt.
"""

import argparse
import atexit
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from ib_insync import Order

from ib_tools.alerts import send_telegram
from ib_tools.ibpysync import IBPySync

from options_bt.live.tsmom_rebalance import compute_rebalance_targets, print_rebalance_report, _resolve_contract

load_dotenv()

log = logging.getLogger(__name__)

# Known instrument defaults, keyed by our local symbol. Each entry is a
# dict, not a positional tuple -- ib_symbol/signal_symbol were already
# fragile optional positional slots, and adding 'cluster' on top of that
# would make it worse.
#
# ib_symbol is only set when IBKR's actual contract ticker differs from our
# local key (e.g. SIL: IBKR has no separate Micro Silver symbol -- it's
# traded under the same root symbol 'SI' as full-size silver, disambiguated
# only by the contract's multiplier field, confirmed via IBKR users hitting
# this exact mismatch: https://www.quantconnect.com/forum/discussion/19622/).
#
# signal_symbol is only set when the TRADED contract's own continuous
# front-month history is too short/thin to trust for the TSMOM signal (e.g.
# the CBOT micro grains below, all launched ~Feb 2025) -- in that case the
# signal is computed off the full-size contract's much longer history
# (same cents/bushel quote scale, just a different contract multiplier),
# while sizing/orders still use the traded micro contract.
#
# cluster groups instruments that move on a shared factor (ag complex,
# EM/risk-on FX, etc) -- used by apply_cluster_risk_cap so a handful of
# individually-fine-looking positions don't add up to one oversized bet on
# that shared factor. Static tags, not a rolling correlation estimate (out
# of scope for a small-account retail system) -- review/adjust by hand if
# the traded universe changes meaningfully.
KNOWN_INSTRUMENTS = {
    'ES':  {'exchange': 'CME', 'multiplier': 50, 'cluster': 'equity'},
    'MES': {'exchange': 'CME', 'multiplier': 5, 'cluster': 'equity'},
    'NQ':  {'exchange': 'CME', 'multiplier': 20, 'cluster': 'equity'},
    'MNQ': {'exchange': 'CME', 'multiplier': 2, 'cluster': 'equity'},

    # CL/MCL: NYMEX, not COMEX -- COMEX is metals only (CME Group splits its
    # exchanges by product class; crude oil clears on NYMEX).
    'CL':  {'exchange': 'NYMEX', 'multiplier': 1000, 'cluster': 'energy'},
    'MCL': {'exchange': 'NYMEX', 'multiplier': 100, 'cluster': 'energy'},
    'GC':  {'exchange': 'COMEX', 'multiplier': 100, 'cluster': 'metal'},
    'MGC': {'exchange': 'COMEX', 'multiplier': 10, 'cluster': 'metal'},
    'SI':  {'exchange': 'COMEX', 'multiplier': 5000, 'cluster': 'metal'}, # margin estimated, verify
    'SIL': {'exchange': 'COMEX', 'multiplier': 1000, 'ib_symbol': 'SI', 'cluster': 'metal'}, # IBKR ticker is 'SI', not 'SIL'; multiplier disambiguates

    'ZN': {'exchange': 'CBOT', 'multiplier': 1000, 'cluster': 'rates'}, # 10-Year T-Note -- margin estimated, verify
    'TN': {'exchange': 'CBOT', 'multiplier': 1000, 'cluster': 'rates'}, # 10-Year T-Note -- margin estimated, verify
    'MTN': {'exchange': 'CBOT', 'multiplier': 100, 'cluster': 'rates'}, # 10-Year T-Note -- margin estimated, verify
    'ZT': {'exchange': 'CBOT', 'multiplier': 2000, 'cluster': 'rates'}, # 2-Year T-Note -- margin estimated, verify

    'ZL':  {'exchange': 'CBOT', 'multiplier': 600, 'cluster': 'grain'}, # Soybean Oil -- 60K lbs, 0.01 cent/lb
    'MZL': {'exchange': 'CBOT', 'multiplier': 60, 'signal_symbol': 'ZL', 'cluster': 'grain'}, # Micro Soybean Oil -- 6K lbs, 0.02 cent/lb
    'ZC':  {'exchange': 'CBOT', 'multiplier': 50, 'cluster': 'grain'}, # Corn -- 5000 bushels, 0.0025 cent/bu
    'MZC': {'exchange': 'CBOT', 'multiplier': 5, 'signal_symbol': 'ZC', 'cluster': 'grain'}, # Micro Corn -- 500 bushels, 0.005 cent/bu = $2.50
    'ZS':  {'exchange': 'CBOT', 'multiplier': 50, 'cluster': 'grain'}, # Soybeans -- 5000 bushels, 0.0025 cent/bu
    'MZS': {'exchange': 'CBOT', 'multiplier': 5, 'signal_symbol': 'ZS', 'cluster': 'grain'}, # Micro Soybeans -- 500 bushels, 0.005 cent/bu = $2.50
    'ZW':  {'exchange': 'CBOT', 'multiplier': 50, 'cluster': 'grain'}, # Wheat -- 5000 bushels, 0.0025 cent/bu
    'MZW': {'exchange': 'CBOT', 'multiplier': 5, 'signal_symbol': 'ZW', 'cluster': 'grain'}, # Micro Wheat -- 500 bushels, 0.005 cent/bu = $2.50

    # Nikkei is its own factor (Japan equity, JPY-adjacent), not lumped into
    # the US-equity cluster with ES/MES/NQ/MNQ.
    'NKD': {'exchange': 'CME', 'multiplier': 5, 'cluster': 'intl_equity'},
    'MNK': {'exchange': 'CME', 'multiplier': 0.5, 'cluster': 'intl_equity'},

    # No leading underscore here, unlike enums.py's FuturesType -- that
    # workaround exists only because Python enum members can't start with a
    # digit. This is a plain dict, so the key is the real IBKR ticker; using
    # '_6J' previously meant --instruments 6J couldn't match this entry, and
    # even a literal '_6J' lookup would have sent the wrong symbol to IB.
    'JPY': {'exchange': 'CME', 'multiplier': 12_500_000, 'cluster': 'fx'},
    'J7':  {'exchange': 'CME', 'multiplier': 6_250_000, 'cluster': 'fx'},
    'BRE': {'exchange': 'CME', 'multiplier': 100_000, 'cluster': 'fx'},
    '6M':  {'exchange': 'CME', 'multiplier': 500_000, 'cluster': 'fx'},
}

DEFAULT_MAX_NOTIONAL = float(os.getenv('TSMOM_DEFAULT_MAX_NOTIONAL', '0')) or None


def configure_logging():
    fmt = logging.Formatter('%(asctime)s %(name)s [%(levelname)s] %(message)s')
    level = os.getenv('LOG_LEVEL', 'INFO').upper()

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.WARNING)

    for name in ('__main__', 'options_bt'):
        logging.getLogger(name).setLevel(level)
    logging.getLogger('ib_insync').setLevel(logging.WARNING)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def connect_with_retry(ib: IBPySync, host, ports, client_id, interval=30):
    """Try each port in order (IBG then TWS). Loops until one accepts."""
    while True:
        for port in ports:
            try:
                ib.connect(host, port, client_id)
                log.info('Connected at %s:%s client_id=%s', host, port, client_id)
                return
            except Exception as exc:
                log.warning('Cannot connect to %s:%s (%s)', host, port, exc)
        log.info('No gateway available — retrying in %ss', interval)
        time.sleep(interval)


def _build_instruments(spec: str, max_notional: float, max_contracts: int) -> list[dict]:
    path = Path(spec)
    if path.exists() and path.suffix == '.json':
        with open(path) as f:
            return json.load(f)

    instruments = []
    for symbol in (s.strip().upper() for s in spec.split(',') if s.strip()):
        if symbol not in KNOWN_INSTRUMENTS:
            raise ValueError(
                f'Unknown symbol {symbol!r} — pass a JSON config path for '
                f'instruments outside {sorted(KNOWN_INSTRUMENTS)}'
            )
        known = KNOWN_INSTRUMENTS[symbol]
        ib_symbol = known.get('ib_symbol') or symbol
        signal_symbol = known.get('signal_symbol') or ib_symbol
        instruments.append({
            'symbol': symbol,
            'ib_symbol': ib_symbol,
            'signal_symbol': signal_symbol,
            'exchange': known['exchange'],
            'expiry': 'auto',
            'multiplier': known['multiplier'],
            'cluster': known.get('cluster', 'other'),
            'max_contracts': max_contracts,
            # max_notional is now an optional hard per-instrument ceiling,
            # not the main sizing lever (see --account-equity) -- None
            # unless the caller explicitly passed one.
            'max_notional': max_notional,
        })
    return instruments


def _save_report(report: str, targets: list[dict]) -> None:
    """Persists each run's report (plain text, matches stdout) and targets
    (CSV, one row per instrument) to options-bt/results/, timestamped --
    mirrors tsmom_backtest.py's results dir so live and backtest output
    live in the same place. Previously this only ever printed to stdout/
    Telegram and was lost the moment the terminal scrolled."""
    results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'results'))
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    txt_path = os.path.join(results_dir, f'tsmom_live_rebalance_{ts}.txt')
    with open(txt_path, 'w') as f:
        f.write(report)

    # symbol -> current/target position -> signal/regime first (the columns
    # you actually scan a rebalance report for), then the sizing-math trail
    # (scalar -> budget inputs -> raw/target notional -> position risk) in
    # the order you'd actually want to follow the calculation, everything
    # else after in a stable, predictable order.
    priority = ['symbol', 'current_contracts', 'target_contracts', 'infeasible', 'signal',
                'regime', 'vol_regime', 'scalar', 'risk_scalar', 'momentum_discount',
                'signal_confidence_regime', 'signal_confidence', 'vol_ratio', 'market_stress_scale',
                'account_equity', 'n_effective',
                'risk_budget', 'vol_target', 'target_portfolio_vol', 'budget_constant',
                'position_risk', 'raw_notional', 'target_notional', 'max_cluster_risk_pct',
                'max_lot_overrun_pct']
    all_keys = {key for t in targets for key in t}
    fieldnames = [k for k in priority if k in all_keys] + sorted(all_keys - set(priority))
    rounded_rows = [
        {k: (round(v, 4) if isinstance(v, float) and not math.isnan(v) else v) for k, v in t.items()}
        for t in targets
    ]
    csv_path = os.path.join(results_dir, f'tsmom_live_rebalance_{ts}.csv')
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rounded_rows)

    log.info('Saved rebalance report to %s, %s', txt_path, csv_path)


def _execute_rebalance_order(ib: IBPySync, contract, delta_contracts: int):
    """Simple limit-at-mid (falls back to market) order for the size delta
    needed to reach target_contracts from current_contracts."""
    action = 'BUY' if delta_contracts > 0 else 'SELL'
    qty = abs(delta_contracts)
    ticker = ib.req_mkt_data(contract)
    ib.sleep(3)
    bid = ticker.bid if ticker.bid and not math.isnan(ticker.bid) else None
    ask = ticker.ask if ticker.ask and not math.isnan(ticker.ask) else None
    if bid and ask:
        mid = round((bid + ask) / 2, 2)
        log.info('%s %d %s LMT %.2f (bid=%.2f ask=%.2f)', action, qty, contract.symbol, mid, bid, ask)
        order = Order(action=action, orderType='LMT', totalQuantity=qty, lmtPrice=mid, tif='DAY')
    else:
        log.warning('No quote for %s — placing MKT order', contract.symbol)
        order = Order(action=action, orderType='MKT', totalQuantity=qty)
    trade = ib.place_order(contract, order)
    ib.wait_fill(trade, 60)
    ib.cancel_mkt_data(contract)
    return trade


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--instruments', required=True,
                   help='Comma-separated symbols (e.g. MES,MNQ) or path to a JSON instrument config')
    p.add_argument('--vol-target', type=float, default=0.15,
                   help='Annualized vol target (default: %(default)s = 15%%)')
    p.add_argument('--account-equity', type=float, required=True,
                   help='Account equity USD -- the primary sizing input: drives the derived '
                        'per-cluster risk budget (account_equity * target_portfolio_vol / sqrt(n_effective))')
    p.add_argument('--target-portfolio-vol', type=float, default=0.15,
                   help='Target total portfolio vol, used to derive the risk budget (default: %(default)s = 15%%)')
    p.add_argument('--max-cluster-risk-pct', type=float, default=0.25,
                   help='Max share of total portfolio dollar-vol risk any one cluster '
                        '(e.g. grains, FX) may carry before being rescaled down (default: %(default)s)')
    p.add_argument('--min-conviction', type=float, default=0.05,
                   help='Min abs(trend_strength) for a cluster to count as "active" '
                        'when deriving n_effective (default: %(default)s)')
    p.add_argument('--max-lot-overrun-pct', type=float, default=0.5,
                   help='Lot-size exception tolerance: the top-priority instrument in an '
                        'over-budget cluster still gets 1 contract (instead of 0) if its own '
                        'single-contract risk is within this fraction over the cluster cap '
                        '(default: %(default)s = 50%%)')
    p.add_argument('--max-contracts', type=int, default=15,
                   help='Per-instrument sanity backstop (not the primary sizing lever -- '
                        'that is the derived risk budget + cluster cap), used when '
                        '--instruments is a symbol list (default: %(default)s)')
    p.add_argument('--max-notional', type=float, default=DEFAULT_MAX_NOTIONAL,
                   help='Optional hard per-instrument notional USD ceiling, used when '
                        '--instruments is a symbol list (default: %(default)s, i.e. no ceiling '
                        'beyond the derived risk budget)')
    p.add_argument('--vx-expiry', default='auto',
                   help='VX futures expiry YYYYMM or "auto" for nearest >=3d (default: %(default)s)')
    p.add_argument('--long-only', action='store_true',
                   help='Disable short positions (signal_scalar = max(0, trend_strength))')
    p.add_argument('--momentum-discount', type=float, default=0.5,
                   help='Position discount for Correction/Rebound regimes (fast/slow trend sign '
                        'disagreement); 1.0 disables (default: %(default)s)')
    p.add_argument('--enable-signal-confidence', action='store_true',
                   help='Opt in to signal_confidence: a per-instrument discount on trust in that '
                        "instrument's own trend signal when ITS OWN vol_ratio (short/long realized "
                        'vol, asset-specific, NOT VIX/VX-driven) is unusual relative to its own '
                        'history. Off by default -- existing behavior is unchanged unless set.')
    p.add_argument('--signal-confidence-low-threshold', type=float, default=0.7,
                   help='vol_ratio (hv_short/hv_long) at or below this is classified "low" '
                        '(default: %(default)s)')
    p.add_argument('--signal-confidence-high-threshold', type=float, default=1.5,
                   help='vol_ratio (hv_short/hv_long) at or above this is classified "high" '
                        '(default: %(default)s)')
    p.add_argument('--signal-confidence-high-vol', type=float, default=0.5,
                   help='Discount factor applied when vol_ratio is "high" -- vol spikes specifically '
                        'damage momentum reliability (default: %(default)s)')
    p.add_argument('--signal-confidence-low-vol', type=float, default=1.0,
                   help='Discount factor applied when vol_ratio is "low" -- no settled answer for '
                        'whether low vol should discount trend confidence, so this defaults to a '
                        'no-op (default: %(default)s)')
    p.add_argument('--dry-run', action='store_true', default=True,
                   help='Print targets only, no orders (default — this is the safe default)')
    p.add_argument('--no-save', action='store_true',
                   help='Skip saving the report/targets to options-bt/results/ (saved by default)')
    p.add_argument('--paper',    action='store_true')
    p.add_argument('--live', action='store_true',
                   help='Place real orders to reach target_contracts, after a typed confirmation')
    conn = p.add_argument_group('Connection')
    conn.add_argument('--host', default=os.getenv('IB_HOST', '127.0.0.1'))
    conn.add_argument('--port', default=int(os.getenv('IB_PORT', '7497')), type=int)
    conn.add_argument('--client-id', default=int(os.getenv('IB_CLIENT_ID', '15')), type=int)
    return p.parse_args()


def main():
    args = parse_args()
    dry_run = not args.live

    configure_logging()

    instruments = _build_instruments(args.instruments, args.max_notional, args.max_contracts)
    config = {
        'vol_target': args.vol_target,
        'max_contracts': args.max_contracts,
        'vx_expiry': args.vx_expiry,
        'long_only': args.long_only,
        'momentum_discount': args.momentum_discount,
        'account_equity': args.account_equity,
        'target_portfolio_vol': args.target_portfolio_vol,
        'max_cluster_risk_pct': args.max_cluster_risk_pct,
        'min_conviction': args.min_conviction,
        'max_lot_overrun_pct': args.max_lot_overrun_pct,
        'enable_signal_confidence': args.enable_signal_confidence,
        'signal_confidence_low_threshold': args.signal_confidence_low_threshold,
        'signal_confidence_high_threshold': args.signal_confidence_high_threshold,
        'signal_confidence_high_vol': args.signal_confidence_high_vol,
        'signal_confidence_low_vol': args.signal_confidence_low_vol,
    }

    if args.live:
        print('=' * 60)
        print('  LIVE REBALANCE — REAL ORDERS WILL BE PLACED')
        print('=' * 60)
        if sys.stdin.isatty():
            confirm = input("Type 'YES REBALANCE LIVE' to confirm: ")
            if confirm != 'YES REBALANCE LIVE':
                sys.exit('Live rebalance not confirmed — exiting.')
        else:
            log.warning('LIVE rebalance — non-interactive, skipping confirmation prompt')
    else:
        print('DRY RUN — targets only, no orders will be placed')

    ib = IBPySync()
    host      = '127.0.0.1' if args.paper else args.host
    ports     = [4002, 7497] if args.paper else [7496, 4001]
    client_id = 1            if args.paper else args.client_id

    connect_with_retry(ib, host, ports, client_id)
    atexit.register(ib.disconnect)

    targets = compute_rebalance_targets(ib, instruments, config)
    report = print_rebalance_report(targets)

    if not args.no_save:
        _save_report(report, targets)

    if not dry_run:
        send_telegram(f'TSMOM Rebalance\n{report}')

        instr_by_symbol = {i['symbol']: i for i in instruments}
        for t in targets:
            if t.get('error') or t['target_contracts'] is None or t['current_contracts'] is None:
                log.warning('Skipping %s — no valid target', t['symbol'])
                continue
            delta = t['target_contracts'] - t['current_contracts']
            if delta == 0:
                log.info('%s already at target (%d) — no order', t['symbol'], t['target_contracts'])
                continue
            instr = instr_by_symbol[t['symbol']]
            contract = _resolve_contract(ib, instr, min_days=7)
            log.info('%s: current=%d target=%d delta=%+d', t['symbol'],
                     t['current_contracts'], t['target_contracts'], delta)
            trade = _execute_rebalance_order(ib, contract, delta)
            status = trade.orderStatus.status
            log.info('%s order status: %s', t['symbol'], status)

    ib.disconnect()


if __name__ == '__main__':
    main()

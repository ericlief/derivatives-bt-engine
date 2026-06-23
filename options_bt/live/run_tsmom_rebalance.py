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
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from ib_insync import Order

from ib_tools.alerts import send_telegram
from ib_tools.ibpysync import IBPySync

from options_bt.live.tsmom_rebalance import compute_rebalance_targets, print_rebalance_report, _resolve_contract

load_dotenv()

log = logging.getLogger(__name__)

# Known CME equity-index futures defaults: (exchange, multiplier)
KNOWN_INSTRUMENTS = {    
    'ES':  ('CME', 50),
    'MES': ('CME', 5),
    'NQ':  ('CME', 20),
    'MNQ': ('CME', 2),

    'CL': ('COMEX', 1000),
    'MCL': ('COMEX', 100),
    'GC': ('COMEX', 100),
    'MGC': ('COMEX', 10),
    'SI': ('COMEX', 5000), # Silver (COMEX) -- margin estimated, verify 
    'SIL': ('COMEX', 1000), # Micro Silver (COMEX) -- margin estimated, verify, same symbol at IB: SI?

    'ZN': ('CBOT', 1000), # 10-Year T-Note (CBOT) -- margin estimated, verify
    'ZT': ('CBOT', 2000),  # 2-Year T-Note (CBOT) -- margin estimated, verify
    
    'ZL': ('CBOT', 600), # Soybean Oil (CBOT) -- 60K lbs, 0.01 cent/lb
    'MZL': ('CBOT', 60), # Micro Soybean Oil (CBOT) -- 6K lbs, 0.02 cent/lb
    'ZC': ('CBOT', 50), # Corn (CBOT) -- 5000 bushels, 0.0025 cent/bu
    'MZC': ('CBOT', 5), # Micro Corn (CBOT) -- 500 bushels, 0.005 cent/bu = $2.50
    'ZS': ('CBOT', 50), # Soy (CBOT) -- 5000 bushels, 0.0025 cent/bu
    'MZS': ('CBOT', 5), # Micro Soy (CBOT) -- 500 bushels, 0.005 cent/bu = $2.50
    'ZW': ('CBOT', 50), # Wheatoy (CBOT) -- 5000 bushels, 0.0025 cent/bu
    'MZW': ('CBOT', 5), # Micro Wheat (CBOT) -- 500 bushels, 0.005 cent/bu = $2.50
     
    # 'NIY': ('CME', 10000),  
    'NKD': ('CME', 5),   
    'MNK': ('CME', 0.5),   

    '_6J': ('CME', 12_500_000),   
    '_6L': ('CME', 100_000), 
    '_6M': ('CME', 500_000), 

    
}

DEFAULT_MAX_NOTIONAL = float(os.getenv('TSMOM_DEFAULT_MAX_NOTIONAL', '25000'))


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
        exchange, multiplier = KNOWN_INSTRUMENTS[symbol]
        instruments.append({
            'symbol': symbol,
            'exchange': exchange,
            'expiry': 'auto',
            'multiplier': multiplier,
            'max_contracts': max_contracts,
            'max_notional': max_notional,
        })
    return instruments


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
    p.add_argument('--max-contracts', type=int, default=5,
                   help='Per-instrument hard cap, used when --instruments is a symbol list (default: %(default)s)')
    p.add_argument('--max-notional', type=float, default=DEFAULT_MAX_NOTIONAL,
                   help='Per-instrument max notional USD, used when --instruments is a symbol list (default: %(default)s)')
    p.add_argument('--vx-expiry', default='auto',
                   help='VX futures expiry YYYYMM or "auto" for nearest >=3d (default: %(default)s)')
    p.add_argument('--long-only', action='store_true',
                   help='Disable short positions (signal_scalar = max(0, trend_strength))')
    p.add_argument('--regime-discount', type=float, default=0.5,
                   help='Position discount for Correction/Rebound regimes; 1.0 disables (default: %(default)s)')
    p.add_argument('--dry-run', action='store_true', default=True,
                   help='Print targets only, no orders (default — this is the safe default)')
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
        'regime_discount': args.regime_discount,
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
    connect_with_retry(ib, args.host, [args.port], args.client_id)
    atexit.register(ib.disconnect)

    targets = compute_rebalance_targets(ib, instruments, config)
    report = print_rebalance_report(targets)

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

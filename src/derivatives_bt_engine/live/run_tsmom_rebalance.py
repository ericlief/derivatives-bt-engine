"""
CLI entry point for the TSMOM monthly rebalance (live, via IB).

Run (dry-run, default — no orders placed):
    python -m derivatives_bt_engine.live.run_tsmom_rebalance --instruments MES,MNQ

Live (places orders after a typed confirmation):
    python -m derivatives_bt_engine.live.run_tsmom_rebalance --instruments MES,MNQ --live

--instruments accepts either a comma-separated symbol list (defaults below
fill in exchange/multiplier/max_notional) or a path to a JSON file with the
full instrument config list (see tsmom_rebalance.py docstring for the
format: symbol, exchange, expiry, multiplier, max_contracts, max_notional).

This depends only on ib_tools.ibpysync / ib_tools.alerts for IB
connectivity — all strategy/signal logic lives in derivatives_bt_engine.
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

from derivatives_bt_engine.live.tsmom_rebalance import (
    DATA_SOURCES,
    MIXING_POOLS,
    RISK_BUDGET_MODES,
    SIGNAL_WEIGHTINGS,
    TsmomLiveConfig,
    compute_rebalance_targets,
    print_rebalance_report,
    _resolve_contract,
)
from derivatives_bt_engine.domain.allocation import NOTIONAL_WEIGHTING_SCHEMES

load_dotenv()

log = logging.getLogger(__name__)

from derivatives_bt_engine.domain.instruments import INSTRUMENTS as KNOWN_INSTRUMENTS  # noqa: E402

DEFAULT_MAX_NOTIONAL = float(os.getenv('TSMOM_DEFAULT_MAX_NOTIONAL', '0')) or None


def configure_logging():
    fmt = logging.Formatter('%(asctime)s %(name)s [%(levelname)s] %(message)s')
    level = os.getenv('LOG_LEVEL', 'INFO').upper()

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.WARNING)

    for name in ('__main__', 'derivatives_bt_engine'):
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
        # db_symbol: Globex root symbol in the duckdb (daily.asset).
        # Explicit when IB and Globex names diverge (e.g. J7→6J, BRE→6L);
        # falls back to signal_symbol (thin contracts borrow their full-size
        # sibling's duckdb data too) then ib_symbol.
        db_symbol = known.get('db_symbol') or signal_symbol
        instruments.append({
            'symbol': symbol,
            'ib_symbol': ib_symbol,
            'signal_symbol': signal_symbol,
            'db_symbol': db_symbol,
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
    (CSV, one row per instrument) to results/ at the project root,
    timestamped -- mirrors tsmom.py's results dir so live and
    backtest output live in the same place. Previously this only ever
    printed to stdout/Telegram and was lost the moment the terminal
    scrolled."""
    results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
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
                'regime', 'vol_regime', 'scalar', 'risk_scalar', 'regime_discount',
                'signal_confidence_regime', 'signal_confidence', 'vol_ratio', 'vix_scalar',
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
    p.add_argument('--disable-vix-gating', action='store_true',
                   help="Turn off the portfolio-wide VX/VIX spike gate entirely -- no read of any "
                        "VX/VIX source is attempted (proceeds as vol_regime=Normal). Use this when no "
                        "VX/VIX data source is available at all (default: gating on, matching all "
                        "prior behavior)")
    p.add_argument('--long-only', action='store_true',
                   help='Disable short positions (signal_scalar = max(0, trend_strength))')
    p.add_argument('--regime-discount', type=float, default=0.5,
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
    p.add_argument('--signal-weighting', choices=SIGNAL_WEIGHTINGS, default='continuous',
                   help="Signal DIRECTION source (default: %(default)s). 'continuous': "
                        "continuous_momentum's daily trend_strength + --regime-discount. "
                        "'goulding': monthly Bull/Correction/Bear/Rebound classification with "
                        "a_Co/a_Re mixing weights re-estimated from all available prior history "
                        "(--mixing-pool) -- --regime-discount is ignored in this mode. Position "
                        "size/vol-targeting is unaffected either way; see "
                        "TsmomLiveConfig.signal_weighting's own docstring")
    p.add_argument('--mixing-pool', choices=MIXING_POOLS, default='cluster',
                   help="Only used with --signal-weighting goulding (default: %(default)s). 'cluster': "
                        "a_Co/a_Re estimated separately per instrument cluster. 'global': one shared "
                        "estimate pooled across every --instruments regardless of cluster")
    p.add_argument('--risk-budget-mode', choices=RISK_BUDGET_MODES, default='cluster',
                   help="How the risk budget is derived (default: %(default)s). 'cluster': "
                        "compute_n_effective/compute_desired_risk_budget -- one shared budget per "
                        "active cluster (zero-correlation assumption). 'idm': "
                        "compute_symbol_notional_budget -- one correlation-aware budget PER ACTIVE "
                        "SYMBOL, via a bounded trailing EWM correlation matrix (see "
                        "--notional-weighting/--use-idm/--idm-window-years/--idm-halflife-days)")
    p.add_argument('--notional-weighting', choices=NOTIONAL_WEIGHTING_SCHEMES, default='flat',
                   help="Only used with --risk-budget-mode idm (default: %(default)s). How the "
                        "IDM-derived total is split across active symbols -- 'flat': equal split. "
                        "'erc'/'hrp': data-driven, correlation-aware splits -- see "
                        "domain.allocation.compute_symbol_notional_budget's own docstring")
    p.add_argument('--use-idm', action=argparse.BooleanOptionalAction, default=True,
                   help="Only used with --risk-budget-mode idm (default: %(default)s). Whether the "
                        "total budget is scaled by IDM before being split, or left as account_equity "
                        "* --target-portfolio-vol with no correlation-based up/down-sizing")
    p.add_argument('--idm-window-years', type=float, default=3.0,
                   help='Only used with --risk-budget-mode idm: bounded trailing window for the EWM '
                        'correlation estimate (default: %(default)s)')
    p.add_argument('--idm-halflife-days', type=float, default=63.0,
                   help='Only used with --risk-budget-mode idm: EWM halflife within the bounded '
                        'window (default: %(default)s)')
    p.add_argument('--data-source', choices=DATA_SOURCES, default='ib',
                   help="Where signal/correlation/mixing-param history comes from (default: "
                        "%(default)s). 'ib': live IB historical bars + the live VX/VIX spike gate -- "
                        "requires a connection. 'database': the same local futures duckdb/VIX parquet "
                        "the backtest reads from, no IB connection anywhere -- runnable in a notebook "
                        "for signal/regime inspection with no live account (current_contracts is "
                        "always None in this mode; --live/order placement requires 'ib')")
    p.add_argument('--as-of', default=None,
                   help='Only used with --data-source database: YYYY-MM-DD to compute signals as of '
                        '(no lookahead past this date) -- default: latest available bar')
    p.add_argument('--bar-years', type=float, default=3.0,
                   help='Historical window: IB request duration / database lookback (default: %(default)s)')
    p.add_argument('--vx-ma-window-days', type=int, default=63,
                   help="Trailing window for the VX/VIX moving average the spike gate compares "
                        "vx_current against, in BOTH --data-source modes (default: %(default)s) -- "
                        "the VX_ELEVATED_RATIO/VX_SPIKE_RATIO/VX_EXTREME_RATIO bands were calibrated "
                        "against this default; changing it changes what those bands mean")
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

    if args.live and args.data_source != 'ib':
        sys.exit(f"--live requires --data-source ib (real orders need a live account) — got "
                 f"--data-source {args.data_source!r}")

    configure_logging()

    instruments = _build_instruments(args.instruments, args.max_notional, args.max_contracts)
    as_of = datetime.strptime(args.as_of, '%Y-%m-%d').date() if args.as_of else None
    config = TsmomLiveConfig(
        vol_target=args.vol_target,
        max_contracts=args.max_contracts,
        vx_expiry=args.vx_expiry,
        vix_gating=not args.disable_vix_gating,
        long_only=args.long_only,
        regime_discount=args.regime_discount,
        account_equity=args.account_equity,
        target_portfolio_vol=args.target_portfolio_vol,
        max_cluster_risk_pct=args.max_cluster_risk_pct,
        min_conviction=args.min_conviction,
        max_lot_overrun_pct=args.max_lot_overrun_pct,
        enable_signal_confidence=args.enable_signal_confidence,
        signal_confidence_low_threshold=args.signal_confidence_low_threshold,
        signal_confidence_high_threshold=args.signal_confidence_high_threshold,
        signal_confidence_high_vol=args.signal_confidence_high_vol,
        signal_confidence_low_vol=args.signal_confidence_low_vol,
        signal_weighting=args.signal_weighting,
        mixing_pool=args.mixing_pool,
        risk_budget_mode=args.risk_budget_mode,
        notional_weighting=args.notional_weighting,
        use_idm=args.use_idm,
        idm_window_years=args.idm_window_years,
        idm_halflife_days=args.idm_halflife_days,
        data_source=args.data_source,
        bar_years=args.bar_years,
        vx_ma_window_days=args.vx_ma_window_days,
        as_of=as_of,
    )

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

    ib = None
    if config.data_source == 'ib':
        ib = IBPySync()
        host      = '127.0.0.1' if args.paper else args.host
        ports     = [4002, 7497] if args.paper else [7496, 4001]
        client_id = 2            if args.paper else args.client_id

        connect_with_retry(ib, host, ports, client_id)
        atexit.register(ib.disconnect)
    else:
        log.info('data_source=database — no IB connection made')

    targets = compute_rebalance_targets(instruments, config, ib=ib)
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

    if ib is not None:
        ib.disconnect()


if __name__ == '__main__':
    main()

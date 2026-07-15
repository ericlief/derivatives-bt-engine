"""
CLI for the multi-symbol TSMOM monthly-rebalance backtest.

Run:
    tsmom-bt --symbols ES,NQ --years 2015-2022
    tsmom-bt --symbols ES,NQ --years 2015-2022 --vol-target 0.10 --long-only
    tsmom-bt --symbols ES,GC --years 2010-2026 --signal-gate-mode monthly --ts-exit-threshold 0 --ts-entry-threshold 0.5
    tsmom-bt --symbols ES,GC --years 2010-2026 --signal-gate-mode daily --ts-exit-threshold 0 --ts-entry-threshold 0.5

Note: MES/MNQ (the live system's default micro-contract universe) have no
data in the local Globex duckdb -- only the full-size ES/NQ contracts are
available, so --max-notional needs to be sized accordingly (a single ES
contract is ~$130k+ notional, vs MES's ~$13k).
"""
import argparse
import os

from derivatives_bt_engine.domain.tsmom_backtester import TsmomBacktestConfig, run_tsmom_backtest


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default='ES,NQ',
                   help='Comma-separated futures symbols, must be known instruments.py symbols (default: %(default)s)')
    p.add_argument('--years', default='2015-2025',
                   help='Year range as START-END (inclusive) or a single YEAR (default: %(default)s)')
    p.add_argument('--initial-capital', type=float, default=100000)
    p.add_argument('--vol-target', type=float, default=0.15,
                   help='Annualized vol target (default: %(default)s = 15%%)')
    p.add_argument('--max-contracts', type=int, default=10,
                   help='Per-symbol hard cap on contract count (default: %(default)s)')
    p.add_argument('--max-notional', type=float, default=250000,
                   help='Per-symbol max notional USD (default: %(default)s -- sized for full-size ES/NQ, not micros)')
    p.add_argument('--long-only', action='store_true',
                   help='Disable short positions (signal_scalar = max(0, trend_strength))')
    p.add_argument('--momentum-discount', type=float, default=0.5,
                   help='Position discount for Correction/Rebound regimes; 1.0 disables (default: %(default)s)')
    p.add_argument('--signal-gate-mode', choices=['off', 'monthly', 'daily'], default='off',
                   help="'off': no gate (default). 'monthly': check ts-exit/entry-threshold and "
                        "exit-on-ts-crossover only at the existing monthly rebalance. 'daily': also "
                        "check the exit half every day in between, off-cycle from the monthly resize "
                        "-- entry stays monthly-only either way.")
    p.add_argument('--ts-exit-threshold', type=float, default=None,
                   help='Force a symbol flat if its own raw signal weakens past this threshold, '
                        'direction-aware (default: disabled)')
    p.add_argument('--ts-entry-threshold', type=float, default=None,
                   help='Block a currently-flat symbol from opening a new position until its '
                        'signal recovers past this threshold, direction-aware -- typically '
                        'stronger than --ts-exit-threshold to avoid close/reopen thrashing at '
                        'one shared line (default: disabled)')
    p.add_argument('--exit-on-ts-crossover', action='store_true',
                   help="Also gate on ts3m crossing to the wrong side of ts1y for the position's "
                        "direction (default: disabled)")
    p.add_argument('--no-save', action='store_true', help='Skip saving stats/events to results/')
    return p.parse_args()


def main():
    args = parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    parts = args.years.split('-')
    if len(parts) == 1:
        start_year = end_year = parts[0]
    elif len(parts) == 2:
        start_year, end_year = parts
    else:
        raise ValueError(f"--years must be YYYY or YYYY-YYYY, got {args.years!r}")

    from datetime import date
    config = TsmomBacktestConfig(
        symbols=symbols,
        initial_capital=args.initial_capital,
        vol_target=args.vol_target,
        max_contracts=args.max_contracts,
        max_notional=args.max_notional,
        long_only=args.long_only,
        momentum_discount=args.momentum_discount,
        start_date=date(int(start_year), 1, 1),
        end_date=date(int(end_year), 12, 31),
        signal_gate_mode=args.signal_gate_mode,
        ts_exit_threshold=args.ts_exit_threshold,
        ts_entry_threshold=args.ts_entry_threshold,
        exit_on_ts_crossover=args.exit_on_ts_crossover,
    )

    result = run_tsmom_backtest(config)
    stats = result['stats']
    events = result['events']
    transactions = result['transactions']
    trades = result['trades']

    print(stats.tail(10))
    print()
    print(f"Final capital: ${stats['capital'][-1]:,.2f}  "
          f"Cumulative PnL: ${stats['cum_pnl'][-1]:,.2f}  "
          f"Max drawdown: ${stats['drawdown_usd'].min():,.2f} ({stats['drawdown_pct'].min():.2f}%)")
    print()
    print(f"{len(events)} rebalance events, {sum(1 for e in events if e['target_contracts'] != e['prior_contracts'])} caused a position change")
    if args.signal_gate_mode != 'off':
        gated = [e for e in events if e.get('gate_reason')]
        print(f"{len(gated)} events triggered the signal gate "
              f"({sum(1 for e in gated if e['gate_reason'] == 'signal_ts_threshold')} ts_threshold, "
              f"{sum(1 for e in gated if e['gate_reason'] == 'signal_crossover')} crossover, "
              f"{sum(1 for e in gated if e['gate_reason'] == 'signal_entry_blocked')} entry_blocked)")
    print()
    print(f"{transactions.height} transactions (buys/sells that changed a contract count)")
    print(f"{trades.height} trades (reconstructed open/close spans of nonzero exposure per symbol)")
    if trades.height > 0:
        print(trades.tail(10))

    if not args.no_save:
        # Anchored to the project root rather than a bare relative
        # "results" -- a bare relative path silently creates results/results
        # (and setup_logger's own relative "logs") if this is ever invoked
        # from inside results/ itself, e.g. after cd-ing there to inspect
        # prior output.
        results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
        os.makedirs(results_dir, exist_ok=True)
        symbol_str = '_'.join(symbols)
        stats.write_csv(os.path.join(results_dir, f"tsmom_stats_{symbol_str}_{start_year}-{end_year}.csv"))
        import polars as pl
        pl.DataFrame(events).write_csv(
            os.path.join(results_dir, f"tsmom_events_{symbol_str}_{start_year}-{end_year}.csv"))
        transactions.write_csv(os.path.join(results_dir, f"tsmom_transactions_{symbol_str}_{start_year}-{end_year}.csv"))
        trades.write_csv(os.path.join(results_dir, f"tsmom_trades_{symbol_str}_{start_year}-{end_year}.csv"))


if __name__ == "__main__":
    main()

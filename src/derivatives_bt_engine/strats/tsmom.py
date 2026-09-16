"""
CLI for the multi-symbol TSMOM monthly-rebalance backtest.

Run:
    tsmom --symbols ES,NQ --years 2015-2022
    tsmom --symbols ES,NQ --years 2015-2022 --vol-target 0.10 --long-only
    tsmom --symbols ES,GC --years 2010-2026 --signal-gate-mode monthly --ts-exit-threshold 0 --ts-entry-threshold 0.5
    tsmom --symbols ES,GC --years 2010-2026 --signal-gate-mode daily --ts-exit-threshold 0 --ts-entry-threshold 0.5
    tsmom --symbols ES,GC,CL --years 2010-2026 --fixed-quantities 4,3,2 --signal-gate-mode monthly --ts-exit-threshold 0 --ts-entry-threshold 0.5

Note: MES/MNQ (the live system's default micro-contract universe) have no
data in the local Globex duckdb -- only the full-size ES/NQ contracts are
available, so --max-notional needs to be sized accordingly (a single ES
contract is ~$130k+ notional, vs MES's ~$13k).
"""
import argparse
import json
import os
from dataclasses import asdict
from datetime import date, datetime

import polars as pl

from derivatives_bt_engine.domain.tsmom_backtester import TsmomBacktestConfig, run_tsmom_backtest
from derivatives_bt_engine.domain.tsmom_reporting import clean_signal_rows, portfolio_rows_from_signals
from derivatives_bt_engine.domain.tsmom_window_reporting import (
    score_causal_windows,
    summarize_causal_windows,
)


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
    p.add_argument('--fixed-quantities', default=None,
                   help='Comma-separated fixed contract counts, positionally matched to --symbols '
                        '(e.g. --symbols ES,GC,CL --fixed-quantities 4,3,2). When set, disables '
                        'vol-targeted/notional-scaled sizing entirely -- each symbol always trades '
                        'this many contracts (still direction-aware from its own signal, still '
                        'gated by ts-exit/entry-threshold and VIX spike/extreme); --vol-target/'
                        '--max-notional are ignored (default: disabled, normal vol-targeted sizing)')
    p.add_argument('--long-only', action='store_true',
                   help='Disable short positions (signal_scalar = max(0, trend_strength))')
    p.add_argument('--regime-discount', type=float, default=0.5,
                   help='Position discount for Correction/Rebound regimes; 1.0 disables (default: %(default)s)')
    p.add_argument('--signal-gate-mode', choices=['off', 'monthly', 'daily'], default='off',
                   help="'off': no gate (default). 'monthly': check ts-exit/entry-threshold and "
                        "exit-on-ts-crossover only at the existing monthly rebalance. 'daily': check "
                        "both entry and exit every day, off-cycle from the monthly resize -- a flat "
                        "symbol can open the day its entry gate first clears, a held symbol can "
                        "flatten the day its exit gate fires. Resizing an already-open position's "
                        "magnitude stays strictly monthly-only in both modes.")
    p.add_argument('--ts-exit-threshold', type=float, default=None,
                   help='Force a symbol flat if its own raw signal weakens past this threshold, '
                        'direction-aware (default: disabled)')
    p.add_argument('--ts-entry-threshold', type=float, default=None,
                   help='Block a currently-flat symbol from opening a new position until its '
                        'signal recovers past this threshold, direction-aware -- typically '
                        'stronger than --ts-exit-threshold to avoid close/reopen thrashing at '
                        'one shared line (default: disabled)')
    p.add_argument('--exit-on-ts-crossover', action='store_true',
                   help="Also gate on ts_fast crossing to the wrong side of ts_slow for the position's "
                        "direction (default: disabled)")
    p.add_argument('--disable-vix-gating', action='store_true',
                   help="Turn off the portfolio-wide VIX regime gate (spike/extreme hold-or-halve, "
                        "elevated de-risking) entirely -- isolates the effect of "
                        "ts-exit/entry-threshold/exit-on-ts-crossover alone, without VIX interference "
                        "(default: VIX gating on, matching all prior behavior)")
    p.add_argument('--vix-ma-window-days', type=int, default=63,
                   help="Trailing window for the spot-VIX moving average vix_ratio is computed "
                        "against (default: %(default)s) -- matches "
                        "live.tsmom_rebalance.TsmomLiveConfig.vx_ma_window_days when you want an "
                        "apples-to-apples comparison against a live run")
    p.add_argument('--target-portfolio-vol', type=float, default=None,
                   help="Correlation-aware sizing (default: off, reproducing this module's original "
                        "per-symbol max-notional sizing, which has NO cross-instrument diversification "
                        "correction at all). When set (e.g. 0.15), each rebalance's per-symbol budget "
                        "is instead derived from current capital * this value * a real Carver-style IDM "
                        "computed from a bounded trailing-window EWM correlation matrix over that "
                        "rebalance's own active symbols -- see TsmomBacktestConfig's own docstring for "
                        "the full derivation and a confirmed, honestly-documented calibration caveat "
                        "(fixes the unbounded-overstatement direction of the bug, not exactly precise)")
    p.add_argument('--corr-window-years', type=float, default=3.0,
                   help='Only used with --target-portfolio-vol: bounded trailing window for the EWM '
                        'correlation estimate (default: %(default)s)')
    p.add_argument('--corr-halflife-days', type=float, default=63.0,
                   help='Only used with --target-portfolio-vol: EWM halflife within the bounded window '
                        '(default: %(default)s)')
    p.add_argument('--notional-weighting', choices=['flat', 'erc', 'hrp'], default='flat',
                   help="Only used with --target-portfolio-vol: how the total IDM-derived dollar-vol "
                        "budget is split ACROSS active symbols (default: %(default)s). 'flat': every "
                        "active symbol gets the same budget, regardless of correlation structure -- "
                        "IDM itself already prices the whole active set's own diversification, but a "
                        "correlated cluster's members each separately claim an equal share of the "
                        "result. 'erc'/'hrp': data-driven alternatives (Equal Risk Contribution / "
                        "Hierarchical Risk Parity, solved on that same bounded correlation matrix) "
                        "that split so a correlated cluster collectively earns roughly one "
                        "undiversified bet's worth of budget instead -- see "
                        "compute_symbol_notional_budget's own docstring")
    p.add_argument('--use-idm', action=argparse.BooleanOptionalAction, default=True,
                   help="Only used with --target-portfolio-vol (default: %(default)s). Whether the "
                        "total dollar-vol budget is scaled by IDM before being split per "
                        "--notional-weighting, or left as capital * --target-portfolio-vol with no "
                        "correlation-based up/down-sizing. Feeding IDM the SAME weights as the "
                        "--notional-weighting split (the default when this is on) avoids "
                        "double-counting diversification across the two steps; --no-use-idm isolates "
                        "diversification to the --notional-weighting split alone -- see "
                        "TsmomBacktestConfig.use_idm's own docstring")
    p.add_argument('--apply-cluster-cap', action='store_true',
                   help="After IDM/ERC sizing, cap a cluster's aggregate dollar-vol at "
                        "--max-cluster-risk-pct of account equity × --target-portfolio-vol, "
                        "redistributing an over-budget cluster by raw model conviction "
                        "(default: off; only meaningful with --target-portfolio-vol)")
    p.add_argument('--max-active-per-cluster', type=int, default=None,
                   help="Before IDM/ERC sizing, retain only the top N active raw-conviction "
                        "signals in each instrument cluster (default: unlimited). Goulding "
                        "ranks Bull/Bear by |(g_fast + g_slow)/2| and Correction/Rebound "
                        "by |g_blend|; it never ranks the binary +/-1 direction.")
    p.add_argument('--max-cluster-risk-pct', type=float, default=0.25,
                   help='Cluster cap as a fraction of portfolio dollar-vol target (default: %(default)s)')
    p.add_argument('--max-lot-overrun-pct', type=float, default=0.5,
                   help='Allow the cap priority leader one lot this far over its cap (default: %(default)s)')
    p.add_argument('--signal-weighting', choices=['continuous', 'goulding'], default='continuous',
                   help="Signal DIRECTION source (default: %(default)s). 'continuous': "
                        "continuous_momentum's daily trend_strength + --regime-discount. "
                        "'goulding': Goulding/Harvey/Mazzoleni (2023)'s own monthly Bull/Correction/"
                        "Bear/Rebound classification with a_Co/a_Re mixing weights re-estimated at "
                        "every rebalance from all prior pooled history -- --regime-discount is "
                        "ignored in this mode. Position size/vol-targeting is unaffected either way; "
                        "see TsmomBacktestConfig.signal_weighting's own docstring")
    p.add_argument('--mixing-pool', choices=['cluster', 'global'], default='cluster',
                   help="Only used with --signal-weighting goulding. 'cluster' (default): a_Co/a_Re "
                        "estimated separately per instruments.py cluster. 'global': one shared "
                        "estimate pooled across every --symbols regardless of cluster")
    p.add_argument('--fast-window', type=int, default=63,
                   help="Only used with --signal-weighting continuous: continuous_momentum's fast "
                        "return-horizon window in trading days (default: %(default)s)")
    p.add_argument('--slow-window', type=int, default=252,
                   help="Only used with --signal-weighting continuous: continuous_momentum's slow "
                        "return-horizon window in trading days (default: %(default)s)")
    p.add_argument('--vol-fast-window', type=int, default=None,
                   help="Only used with --signal-weighting continuous: vol-normalization window for "
                        "the fast leg (default: None -> horizon-matched to --fast-window). Pass "
                        "explicitly only to deliberately decouple the return horizon from the vol "
                        "estimation horizon")
    p.add_argument('--vol-slow-window', type=int, default=None,
                   help="Only used with --signal-weighting continuous: vol-normalization window for "
                        "the slow leg (default: None -> horizon-matched to --slow-window)")
    p.add_argument('--window-report', action='store_true',
                   help='Score annual OOS, anchored-expanding, and capped-rolling return windows from '
                        'this one causal backtest path; requires --oos-start')
    p.add_argument('--oos-start', default=None,
                   help='Shared YYYY-MM-DD evaluation boundary for --window-report; it must be the '
                        'same across grid parameter combinations')
    p.add_argument('--window-report-max-years', type=int, default=5,
                   help='Capped rolling-window width used by --window-report (default: %(default)s)')
    p.add_argument('--sheets-spreadsheet', default=None, metavar='NAME',
                   help='Upload compact TSMOM report tabs to this existing Google spreadsheet. '
                        'Requires GSPREAD_KEY and the service account to have Editor access; '
                        'omitting this flag keeps the run local-only.')
    p.add_argument('--no-save', action='store_true',
                   help='Skip saving MTM, clean signals, portfolio snapshots, trades, transactions, and run manifest to results/')
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

    fixed_quantities = None
    if args.fixed_quantities:
        fixed_quantities = [int(q.strip()) for q in args.fixed_quantities.split(',') if q.strip()]

    config = TsmomBacktestConfig(
        symbols=symbols,
        initial_capital=args.initial_capital,
        vol_target=args.vol_target,
        max_contracts=args.max_contracts,
        max_notional=args.max_notional,
        long_only=args.long_only,
        regime_discount=args.regime_discount,
        start_date=date(int(start_year), 1, 1),
        end_date=date(int(end_year), 12, 31),
        signal_gate_mode=args.signal_gate_mode,
        ts_exit_threshold=args.ts_exit_threshold,
        ts_entry_threshold=args.ts_entry_threshold,
        exit_on_ts_crossover=args.exit_on_ts_crossover,
        fixed_quantities=fixed_quantities,
        vix_gating=not args.disable_vix_gating,
        vix_ma_window_days=args.vix_ma_window_days,
        target_portfolio_vol=args.target_portfolio_vol,
        corr_window_years=args.corr_window_years,
        corr_halflife_days=args.corr_halflife_days,
        notional_weighting=args.notional_weighting,
        use_idm=args.use_idm,
        apply_cluster_cap=args.apply_cluster_cap,
        max_active_per_cluster=args.max_active_per_cluster,
        max_cluster_risk_pct=args.max_cluster_risk_pct,
        max_lot_overrun_pct=args.max_lot_overrun_pct,
        signal_weighting=args.signal_weighting,
        mixing_pool=args.mixing_pool,
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        vol_fast_window=args.vol_fast_window,
        vol_slow_window=args.vol_slow_window,
    )

    result = run_tsmom_backtest(config)
    stats = result['daily_mtm']
    events = result['trend_signals']
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

    # Summary table -- same shape as tsmom_binary_vol_parity_backtest.py's
    # own "=== Summary (all runs) ===" print/save (ann_ret_pct/ann_vol_pct/
    # sharpe/max_dd_pct/total_fees/n_days), which this CLI previously had
    # no equivalent of: only final capital/cum_pnl/max_dd_usd were ever
    # printed, no Sharpe ratio was computed anywhere, and nothing was saved
    # to a comparable summary CSV.
    summary_values = {
        'symbols': ','.join(symbols), 'years': args.years,
        'n_days': result['n_days'], 'ann_ret_pct': result['ann_ret_pct'],
        'ann_vol_pct': result['ann_vol_pct'], 'sharpe': result['sharpe'],
        'max_dd_pct': result['max_dd_pct'], 'total_fees': result['total_fees'],
    }
    summary_df = pl.DataFrame([summary_values])
    print()
    print("=== Summary ===")
    print(summary_df)

    window_metrics = window_summary = None
    if args.window_report:
        if args.oos_start is None:
            raise ValueError('--window-report requires --oos-start YYYY-MM-DD')
        oos_start = date.fromisoformat(args.oos_start)
        window_metrics = score_causal_windows(
            result, initial_capital=config.initial_capital, oos_start=oos_start,
            max_width_years=args.window_report_max_years,
        )
        window_summary = summarize_causal_windows(window_metrics)
        print('\n=== Causal OOS window summary ===')
        with pl.Config(tbl_rows=-1):
            print(window_summary)

    # Build the compact cross-mode report contract once. --no-save controls
    # local files only; an explicitly named spreadsheet remains an opt-in
    # external destination for the same report frames.
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_id = f'backtest_{ts}'
    symbol_str = '_'.join(symbols)
    clean_signals = clean_signal_rows(events, run_id)
    equity_by_as_of = {}
    portfolio_fields_by_as_of = {}
    for event in events:
        as_of = event['date'].isoformat()
        equity_by_as_of[as_of] = event['capital']
    for as_of in equity_by_as_of:
        last_event = next(event for event in reversed(events) if event['date'].isoformat() == as_of)
        portfolio_fields_by_as_of[as_of] = {
            'portfolio_risk_target': last_event.get('portfolio_risk_target'),
            'idm_risk_target': last_event.get('idm_risk_target'),
            'realized_portfolio_risk': last_event.get('realized_portfolio_risk'),
            'idm_multiplier': last_event.get('idm_multiplier'),
        }
    portfolio_rows = portfolio_rows_from_signals(
        clean_signals, run_id, equity_by_as_of=equity_by_as_of,
        portfolio_fields_by_as_of=portfolio_fields_by_as_of,
    )
    summary_df = pl.DataFrame([{'run_id': run_id, **summary_values}])
    sheet_frames = {
        'signals': pl.DataFrame(clean_signals),
        'portfolio': pl.DataFrame(portfolio_rows),
        'summary': summary_df,
    }
    if window_metrics is not None:
        sheet_frames['window_metrics'] = window_metrics.with_columns(run_id=pl.lit(run_id)).select(
            'run_id', *window_metrics.columns
        )
        sheet_frames['window_summary'] = window_summary.with_columns(run_id=pl.lit(run_id)).select(
            'run_id', *window_summary.columns
        )

    if not args.no_save:
        # Anchored to the project root rather than a bare relative
        # "results" -- a bare relative path silently creates results/results
        # (and setup_logger's own relative "logs") if this is ever invoked
        # from inside results/ itself, e.g. after cd-ing there to inspect
        # prior output.
        results_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))
        os.makedirs(results_dir, exist_ok=True)
        # Every artifact is timestamped -- confirmed directly that without
        # this, re-running the same --symbols/--years with different other
        # params (e.g. --signal-gate-mode/--target-portfolio-vol variants
        # in the same shell script, back to back) silently overwrote the
        # PREVIOUS run's mtm/signals/transactions/trades files, since their
        # names only ever encoded symbols+years: only summary_df had a
        # timestamp in an earlier version of this, so two runs in the same
        # script left two summary CSVs but only one (the last) set of
        # detail files -- not the intended behavior, fixed by timestamping
        # all artifacts the same way.
        stats.write_csv(os.path.join(results_dir, f"{ts}_tsmom_mtm_{symbol_str}_{start_year}-{end_year}.csv"))
        pl.DataFrame(clean_signals).write_csv(
            os.path.join(results_dir, f"{ts}_tsmom_signals_{symbol_str}_{start_year}-{end_year}.csv"))
        pl.DataFrame(portfolio_rows).write_csv(
            os.path.join(results_dir, f"{ts}_tsmom_portfolio_{symbol_str}_{start_year}-{end_year}.csv"))
        transactions.write_csv(os.path.join(results_dir, f"{ts}_tsmom_transactions_{symbol_str}_{start_year}-{end_year}.csv"))
        trades.write_csv(os.path.join(results_dir, f"{ts}_tsmom_trades_{symbol_str}_{start_year}-{end_year}.csv"))
        summary_path = os.path.join(results_dir, f"{ts}_tsmom_summary_{symbol_str}_{start_year}-{end_year}.csv")
        summary_df.write_csv(summary_path)
        if window_metrics is not None:
            sheet_frames['window_metrics'].write_csv(os.path.join(
                results_dir, f"{ts}_tsmom_window_metrics_{symbol_str}_{start_year}-{end_year}.csv"
            ))
            sheet_frames['window_summary'].write_csv(os.path.join(
                results_dir, f"{ts}_tsmom_window_summary_{symbol_str}_{start_year}-{end_year}.csv"
            ))
        manifest_path = os.path.join(results_dir, f"{ts}_tsmom_run_{symbol_str}_{start_year}-{end_year}.json")
        with open(manifest_path, 'w') as f:
            json.dump({
                'run_id': run_id,
                'mode': 'backtest',
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'data_start': str(stats['date'][0]),
                'data_end': str(stats['date'][-1]),
                'config': asdict(config),
                'window_report': {
                    'oos_start': args.oos_start,
                    'max_width_years': args.window_report_max_years,
                } if args.window_report else None,
            }, f, indent=2, default=str)
        print(f"\nSaved summary to {summary_path}; clean signal, portfolio, and run-manifest files share {run_id}")

    if args.sheets_spreadsheet:
        from derivatives_bt_engine.utils.tsmom_sheets import upload_tsmom_frames
        upload_tsmom_frames(
            spreadsheet_name=args.sheets_spreadsheet,
            run_label=f'tsmom_backtest_{symbol_str}', frames=sheet_frames,
        )


if __name__ == "__main__":
    main()

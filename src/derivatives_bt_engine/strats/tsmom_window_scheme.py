"""Cold-start Scheme-B/Scheme-C sensitivity sweep for main TSMOM.

Uses the same expanding and expand-then-cap-and-roll generators as
``tsmom_vol_parity_window_scheme``.  Each row is a fresh, independent
backtest over that row's date bounds. That deliberately resets Goulding's
available mixing history, capital path, and portfolio state at each row's
start. It is therefore a cold-start/history-sensitivity experiment, *not* the
normal causal performance-window report; use
``domain.tsmom_window_reporting.score_causal_windows`` for the latter.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.tsmom_backtester import (
    TsmomBacktestConfig,
    load_portfolio_data,
    run_tsmom_backtest,
)
from derivatives_bt_engine.strats.window_scheme_naked_futures import (
    generate_capped_rolling_windows,
    generate_expanding_windows,
)
from derivatives_bt_engine.utils.logger import setup_logger


logger = setup_logger()

DEFAULT_SYMBOLS = 'MES,MNQ,MCL,MZL,MZC,MZS,MZW,MGC,SIL,J7,6M,MTN'
DEFAULT_ANCHOR_START = '2010-01-01'
DEFAULT_STEP_YEARS = 1
DEFAULT_MAX_WIDTH_YEARS = 5


def _data_end(symbols: list[str]) -> str:
    """Latest common price date, preventing a silently partial universe."""
    price_data, _ = load_portfolio_data(symbols)
    return min(frame['ts_event'].max() for frame in price_data.values()).isoformat()


def run_window_scheme(config: TsmomBacktestConfig, *, anchor_start: str,
                      data_end: str, step_years: int, max_width_years: int,
                      schemes: tuple[str, ...] = ('B', 'C'),
                      window_index_start: int = 0,
                      window_index_end: Optional[int] = None) -> pl.DataFrame:
    """Run fresh Scheme-B and Scheme-C main-TSMOM backtests per window.

    The fields deliberately mirror ``tsmom_vol_parity_window_scheme``:
    scheme/window bounds/width/capital/error plus the normal backtest
    statistics.  The underlying strategy configuration is otherwise held
    constant across every row.
    """
    all_schemes = {
        'B': ('B_expanding', generate_expanding_windows(anchor_start, data_end, step_years)),
        'C': ('C_capped_rolling', generate_capped_rolling_windows(
            anchor_start, data_end, max_width_years, step_years)),
    }
    selected_schemes = []
    for scheme in schemes:
        if scheme not in all_schemes:
            raise ValueError(f'Unknown window scheme {scheme!r}; choose B and/or C')
        name, windows = all_schemes[scheme]
        selected_schemes.append((name, windows[window_index_start:window_index_end]))
    total = sum(len(windows) for _, windows in selected_schemes)
    if total == 0:
        raise ValueError('Window selection produced no runs')
    rows = []
    done = 0
    for scheme, windows in selected_schemes:
        for window_start, window_end in windows:
            done += 1
            start, end = date.fromisoformat(window_start), date.fromisoformat(window_end)
            try:
                result = run_tsmom_backtest(replace(config, start_date=start, end_date=end))
                stats = result['daily_mtm']
                row = {
                    'scheme': scheme,
                    'window_start': window_start,
                    'window_end': window_end,
                    'window_years': round((end - start).days / 365.25, 2),
                    'capital': round(config.initial_capital, 2),
                    'final_capital': round(float(stats['capital'][-1]), 2),
                    'rebalance_events': len(result['trend_signals']),
                    'error': None,
                    **{key: result[key] for key in (
                        'n_days', 'ann_ret_pct', 'ann_vol_pct', 'sharpe', 'max_dd_pct', 'total_fees',
                    )},
                }
            except Exception as exc:
                logger.exception('%s %s..%s failed', scheme, window_start, window_end)
                row = {
                    'scheme': scheme, 'window_start': window_start, 'window_end': window_end,
                    'window_years': round((end - start).days / 365.25, 2),
                    'capital': round(config.initial_capital, 2), 'final_capital': None,
                    'rebalance_events': None, 'error': str(exc), 'n_days': None,
                    'ann_ret_pct': None, 'ann_vol_pct': None, 'sharpe': None,
                    'max_dd_pct': None, 'total_fees': None,
                }
            rows.append(row)
            logger.info('%d/%d scheme=%s window=%s..%s sharpe=%s',
                        done, total, scheme, window_start, window_end, row['sharpe'])
    return pl.DataFrame(rows)


def summarize(results: pl.DataFrame) -> pl.DataFrame:
    """Mean/std across window rows, matching the existing window scheme."""
    return (
        results.filter(pl.col('sharpe').is_not_null())
        .group_by('scheme')
        .agg(
            pl.len().alias('n_windows'),
            pl.col('sharpe').mean().round(3).alias('sharpe_mean'),
            pl.col('sharpe').std().round(3).alias('sharpe_std'),
            pl.col('sharpe').min().round(3).alias('sharpe_min'),
            pl.col('sharpe').max().round(3).alias('sharpe_max'),
            pl.col('ann_ret_pct').mean().round(3).alias('ann_ret_pct_mean'),
            pl.col('ann_vol_pct').mean().round(3).alias('ann_vol_pct_mean'),
            pl.col('max_dd_pct').mean().round(3).alias('max_dd_pct_mean'),
        )
        .sort('scheme')
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--symbols', default=DEFAULT_SYMBOLS)
    parser.add_argument('--initial-capital', type=float, default=90_000)
    parser.add_argument('--anchor-start', default=DEFAULT_ANCHOR_START)
    parser.add_argument('--data-end', default=None,
                        help='Latest common data date by default; pass YYYY-MM-DD to pin the sweep')
    parser.add_argument('--step-years', type=int, default=DEFAULT_STEP_YEARS)
    parser.add_argument('--max-width-years', type=int, default=DEFAULT_MAX_WIDTH_YEARS,
                        help='Scheme-C cap before its date window rolls (default: %(default)s)')
    parser.add_argument('--schemes', default='B,C',
                        help='Comma-separated schemes to run: B (expanding), C (capped rolling)')
    parser.add_argument('--window-index-start', type=int, default=0,
                        help='Zero-based first window per selected scheme (default: %(default)s)')
    parser.add_argument('--window-index-end', type=int, default=None,
                        help='Zero-based exclusive final window per selected scheme (default: all)')
    parser.add_argument('--vol-target', type=float, default=.15)
    parser.add_argument('--target-portfolio-vol', type=float, default=.15)
    parser.add_argument('--notional-weighting', choices=['flat', 'erc', 'hrp'], default='erc')
    parser.add_argument('--use-idm', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--signal-weighting', choices=['continuous', 'goulding'], default='goulding')
    parser.add_argument('--mixing-pool', choices=['cluster', 'global'], default='cluster')
    parser.add_argument('--fast-window', type=int, default=42)
    parser.add_argument('--slow-window', type=int, default=252)
    parser.add_argument('--vol-fast-window', type=int, default=42)
    parser.add_argument('--corr-window-years', type=float, default=3)
    parser.add_argument('--corr-halflife-days', type=float, default=42)
    parser.add_argument('--vix-gating', action=argparse.BooleanOptionalAction, default=False,
                        help='Apply the VX/VIX portfolio de-risking gate (default: off)')
    parser.add_argument('--max-contracts', type=int, default=15)
    parser.add_argument('--max-notional', type=float, default=100_000)
    parser.add_argument('--apply-cluster-cap', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--max-active-per-cluster', type=int, default=2)
    parser.add_argument('--max-cluster-risk-pct', type=float, default=.25)
    parser.add_argument('--max-lot-overrun-pct', type=float, default=.5)
    parser.add_argument('--save-results', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(',') if symbol.strip()]
    schemes = tuple(scheme.strip().upper() for scheme in args.schemes.split(',') if scheme.strip())
    if not schemes:
        raise ValueError('At least one of schemes B,C is required')
    if args.window_index_start < 0 or (
            args.window_index_end is not None and args.window_index_end <= args.window_index_start):
        raise ValueError('window index bounds must satisfy 0 <= start < end')
    data_end = args.data_end or _data_end(symbols)
    config = TsmomBacktestConfig(
        symbols=symbols, initial_capital=args.initial_capital, vol_target=args.vol_target,
        target_portfolio_vol=args.target_portfolio_vol, notional_weighting=args.notional_weighting,
        use_idm=args.use_idm, signal_weighting=args.signal_weighting, mixing_pool=args.mixing_pool,
        fast_window=args.fast_window, slow_window=args.slow_window, vol_fast_window=args.vol_fast_window,
        corr_window_years=args.corr_window_years, corr_halflife_days=args.corr_halflife_days,
        vix_gating=args.vix_gating, max_contracts=args.max_contracts,
        max_notional=args.max_notional, apply_cluster_cap=args.apply_cluster_cap,
        max_active_per_cluster=args.max_active_per_cluster,
        max_cluster_risk_pct=args.max_cluster_risk_pct, max_lot_overrun_pct=args.max_lot_overrun_pct,
    )
    results = run_window_scheme(config, anchor_start=args.anchor_start, data_end=data_end,
                                step_years=args.step_years, max_width_years=args.max_width_years,
                                schemes=schemes, window_index_start=args.window_index_start,
                                window_index_end=args.window_index_end)
    summary = summarize(results)
    with pl.Config(tbl_rows=-1):
        print('\n=== Main TSMOM window-scheme Sharpe stability ===')
        print(summary)
    if args.save_results:
        results_dir = Path(__file__).resolve().parents[3] / 'results'
        results_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        detail_path = results_dir / f'{stamp}_tsmom_window_scheme.csv'
        summary_path = results_dir / f'{stamp}_tsmom_window_scheme_summary.csv'
        results.write_csv(detail_path)
        summary.write_csv(summary_path)
        logger.info('Saved %s and %s', detail_path, summary_path)


if __name__ == '__main__':
    main()

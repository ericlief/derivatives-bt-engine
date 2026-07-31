"""
Window-scheme robustness check for tsmom_binary_vol_parity_backtest.py's
capital-level Sharpe sensitivity.

Cross product of Scheme B (expanding, anchored at a start date) and Scheme C
(expand-then-cap-and-roll) windows -- the same two generators
window_scheme_naked_futures.py uses for its own single-symbol window-slicing
comparison, reused here rather than reimplemented -- against several
--capital-levels. Scheme A (90-day rolling slide) is deliberately NOT
included: at monthly rebalance cadence with this project's own
MIN_MONTHS_PER_PHASE=12 dynamic-mode warmup, a 1-year Scheme-A window would
almost never accumulate enough Correction/Rebound history to escape the
uninformed (0.5, 0.5) a_Co/a_Re fallback, making it a weaker test of the
actual question here than for the naked single-symbol case that scheme was
designed for.

Written to answer directly: is a given capital level's Sharpe (e.g. $150k's
0.30 in the single full-history 2010-2026 run) a stable function of how much
history is included, or a rounding-driven fluke specific to that one window?
Answered by comparing each capital level's Sharpe MEAN and STD across every
window in both schemes -- a real, capital-driven effect should show a
consistent ranking across windows; a fluke should show high std/no stable
ranking.

Run (registered console script -- see pyproject.toml's [project.scripts];
lives in strats/, not scripts/, specifically so this cross-import resolves
as an installed package regardless of invocation directory -- scripts/
only works via `python -m scripts.X` run from the repo root):
    tsmom-vol-parity-windows
    tsmom-vol-parity-windows --capital-levels 80000,150000,300000,1000000
    tsmom-vol-parity-windows --weighting-mode flat_discount --regime-discount 1.0
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import polars as pl

from derivatives_bt_engine.strats.tsmom_binary_vol_parity_backtest import DEFAULT_SYMBOLS, RESULTS_DIR, run
from derivatives_bt_engine.strats.window_scheme_naked_futures import (
    generate_capped_rolling_windows,
    generate_expanding_windows,
)
from derivatives_bt_engine.domain.tsmom_backtester import load_portfolio_data
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ─────────────────────────────────────────────────────
# Matches window_scheme_naked_futures.py's own defaults, not independently
# chosen -- this reuses that script's exact window-generation functions, so
# using the same anchor/step keeps the two comparable.
ANCHOR_START_DATE        = '2010-01-01'
STEP_YEARS               = 1
SCHEME_C_MAX_WIDTH_YEARS = 5
DEFAULT_CAPITAL_LEVELS   = [80_000.0, 150_000.0, 300_000.0, 1_000_000.0]
DEFAULT_WEIGHTING_MODE   = 'dynamic'
DEFAULT_WINDOW_REGIME_DISCOUNT = 1.0  # only used when --weighting-mode flat_discount


def _data_end(symbols: list[str]) -> str:
    """Latest date every symbol in `symbols` actually has data through --
    the MIN of each symbol's own max date, not the max -- so every
    generated window's own end date is guaranteed real for the whole
    universe, rather than silently truncating per-symbol mid-window the
    way a window running past one symbol's own data end already does
    inside run() itself (see tsmom_binary_vol_parity_backtest.py's own
    "frozen position" behavior once a symbol's data runs out)."""
    price_data, _ = load_portfolio_data(symbols)
    return min(df['ts_event'].max() for df in price_data.values()).strftime('%Y-%m-%d')


def run_window_scheme(symbols: list[str], capital_levels: list[float],
                       weighting_mode: str = DEFAULT_WEIGHTING_MODE,
                       regime_discount: float = DEFAULT_WINDOW_REGIME_DISCOUNT,
                       target_portfolio_vol: Optional[float] = None,
                       data_end: Optional[str] = None) -> pl.DataFrame:
    """One row per (scheme, window, capital). Each run() call is
    independent/stateless (same reasoning tsmom_grid_search.py's own
    _run_one docstring gives), so a window/capital combo that fails
    (e.g. a window too short for any symbol to have data at all) is
    caught and recorded with sharpe=None rather than aborting the whole
    sweep -- matches window_scheme_naked_futures.py's run_windows' own
    per-window try/except.

    target_portfolio_vol: None (default) leaves each capital level's own
    flat_per_asset_vol_target_usd exactly as run()'s own default derives it
    (DEFAULT_VOL_TARGET_PCT_OF_CAPITAL of that capital). When set (e.g.
    0.15), every (window, capital) combo instead calibrates its own budget
    to hit this SAME realized portfolio vol -- see run()'s own docstring.
    Directly answers the capital-level Sharpe investigation's open
    question: once every capital level targets the same realized vol
    (removing the confound of low capital implicitly running at LOWER
    effective leverage/vol than high capital), does the earlier
    capital-driven Sharpe gap (e.g. $80k trailing $300k) shrink, or
    persist as a genuinely separate effect (rounding/cluster-floor
    breadth, not leverage)?"""
    if data_end is None:
        data_end = _data_end(symbols)
    windows_b = generate_expanding_windows(ANCHOR_START_DATE, data_end, STEP_YEARS)
    windows_c = generate_capped_rolling_windows(ANCHOR_START_DATE, data_end, SCHEME_C_MAX_WIDTH_YEARS, STEP_YEARS)

    rows = []
    schemes = [('B_expanding', windows_b), ('C_capped_rolling', windows_c)]
    total = sum(len(w) for _, w in schemes) * len(capital_levels)
    done = 0
    for scheme_name, windows in schemes:
        for w_start, w_end in windows:
            start, end = date.fromisoformat(w_start), date.fromisoformat(w_end)
            window_years = round((end - start).days / 365.25, 2)
            for cap in capital_levels:
                done += 1
                try:
                    result = run(symbols, start, end, regime_discount,
                                 weighting_mode=weighting_mode, initial_capital=cap,
                                 target_portfolio_vol=target_portfolio_vol)
                    row = {'scheme': scheme_name, 'window_start': w_start, 'window_end': w_end,
                           'window_years': window_years, 'capital': cap, 'error': None, **result}
                except Exception as e:
                    logger.error(f"{scheme_name} {w_start}..{w_end} @ ${cap:,.0f} failed: {e}")
                    row = {'scheme': scheme_name, 'window_start': w_start, 'window_end': w_end,
                           'window_years': window_years, 'capital': cap, 'error': str(e),
                           'mode': weighting_mode, 'sharpe': None, 'ann_ret_pct': None,
                           'ann_vol_pct': None, 'max_dd_pct': None, 'total_fees': None, 'n_days': None}
                rows.append(row)
                if done % 10 == 0 or done == total:
                    logger.info(f"{done}/{total} (scheme={scheme_name}, window={w_start}..{w_end}, capital=${cap:,.0f})")

    return pl.DataFrame(rows)


def summarize(df: pl.DataFrame) -> pl.DataFrame:
    """Per (scheme, capital): mean/std Sharpe across every window in that
    scheme -- the actual answer to "is this capital level's performance
    stable across how much history is included, or a fluke." A real,
    capital-driven effect should hold a consistent ranking across
    capital levels within a scheme; a fluke shows up as high std with no
    stable ranking."""
    return (
        df.filter(pl.col('sharpe').is_not_null())
        .group_by(['scheme', 'capital'])
        .agg(
            pl.len().alias('n_windows'),
            pl.col('sharpe').mean().round(3).alias('sharpe_mean'),
            pl.col('sharpe').std().round(3).alias('sharpe_std'),
            pl.col('sharpe').min().round(3).alias('sharpe_min'),
            pl.col('sharpe').max().round(3).alias('sharpe_max'),
            pl.col('ann_ret_pct').mean().round(3).alias('ann_ret_pct_mean'),
        )
        .sort(['scheme', 'capital'])
    )


def _param_str(symbols: list[str], capital_levels: list[float],
                weighting_mode: str, regime_discount: float,
                target_portfolio_vol: Optional[float] = None) -> str:
    """Compact param string for the saved filenames -- same convention
    bull_put_param_search.py/iron_condor_param_search.py already use
    (param values embedded in the filename itself, not just a bare
    timestamp), so a directory of saved runs is distinguishable without
    opening each file. Symbols spelled out in full for a normal-sized
    sweep; abbreviated to a count for a large universe (e.g. the full
    12-symbol DEFAULT_SYMBOLS) so the filename doesn't become unwieldy."""
    symbol_str = '-'.join(symbols) if len(symbols) <= 6 else f"{len(symbols)}syms"

    def _fmt_cap(c: float) -> str:
        return f"{c / 1_000_000:g}M" if c >= 1_000_000 else f"{int(c / 1000)}k"
    cap_str = '-'.join(_fmt_cap(c) for c in capital_levels)

    mode_str = weighting_mode if weighting_mode == 'dynamic' else f"{weighting_mode}{regime_discount}"
    vol_str = f"_vt{target_portfolio_vol:g}" if target_portfolio_vol is not None else ''
    return f"{symbol_str}_{mode_str}_cap{cap_str}{vol_str}"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                    help='Comma-separated futures symbols (default: %(default)s)')
    p.add_argument('--capital-levels', default=','.join(str(int(c)) for c in DEFAULT_CAPITAL_LEVELS),
                    help='Comma-separated --initial-capital values to sweep per window (default: %(default)s)')
    p.add_argument('--weighting-mode', choices=['flat_discount', 'dynamic'], default=DEFAULT_WEIGHTING_MODE,
                    help='(default: %(default)s)')
    p.add_argument('--regime-discount', type=float, default=DEFAULT_WINDOW_REGIME_DISCOUNT,
                    help='Only used when --weighting-mode flat_discount (default: %(default)s)')
    p.add_argument('--target-portfolio-vol', type=float, default=None,
                    help='Calibrate every (window, capital) combo\'s own flat_per_asset_vol_target_usd '
                         'to hit this SAME realized annualized portfolio vol (e.g. 0.15), instead of '
                         "each capital level using run()'s own default (a fixed %% of that capital) "
                         'as-is -- see run_window_scheme()/run()\'s own docstrings. Roughly doubles '
                         'this sweep\'s total runtime (default: off)')
    p.add_argument('--save-results', action='store_true',
                    help='Write {tag}_{params}_window_scheme.csv (every run) and '
                         '{tag}_{params}_window_scheme_summary.csv (per scheme/capital mean/std) to '
                         'results/ -- {params} encodes symbols/weighting_mode/capital_levels so a '
                         "directory of saved runs is distinguishable without opening each file "
                         '(default: off, still printed)')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    capital_levels = [float(c.strip()) for c in args.capital_levels.split(',') if c.strip()]

    df = run_window_scheme(symbols, capital_levels, weighting_mode=args.weighting_mode,
                            regime_discount=args.regime_discount,
                            target_portfolio_vol=args.target_portfolio_vol)
    summary = summarize(df)

    print()
    print(f"=== Window-scheme sharpe stability, {args.weighting_mode} mode ===")
    with pl.Config(tbl_rows=-1):
        print(summary)

    n_errors = df['error'].is_not_null().sum()
    if n_errors:
        print(f"\n{n_errors}/{df.height} (scheme, window, capital) combos failed -- see 'error' column.")

    if args.save_results:
        results_dir = Path(RESULTS_DIR)
        results_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        param_str = _param_str(symbols, capital_levels, args.weighting_mode, args.regime_discount,
                                target_portfolio_vol=args.target_portfolio_vol)
        detail_path = results_dir / f"{ts}_{param_str}_window_scheme.csv"
        summary_path = results_dir / f"{ts}_{param_str}_window_scheme_summary.csv"
        df.with_columns(pl.col(pl.Float64).round(4)).write_csv(detail_path)
        summary.write_csv(summary_path)
        logger.info(f"Saved {detail_path} ({df.height} rows) and {summary_path} ({summary.height} rows)")


if __name__ == '__main__':
    main()

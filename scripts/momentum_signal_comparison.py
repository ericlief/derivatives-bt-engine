"""
Run the continuous (daily, volatility-normalized) and Goulding et al.
(monthly, arithmetic-average) momentum models independently from the same
raw OHLCV bars, save each separately, and -- only when both are requested
-- join them afterward for side-by-side comparison. Neither model's
calculation depends on the other's intermediate columns (signal_spec.py's
build_features/continuous_momentum/goulding_monthly); this script is just
the CLI/orchestration layer over those three functions.

Run:
    python -m scripts.momentum_signal_comparison --model continuous --symbols ES,NQ
    python -m scripts.momentum_signal_comparison --model goulding --symbols ES,NQ
    python -m scripts.momentum_signal_comparison --model both --symbols ES,NQ --save-csv out/compare

--model continuous: build_features + continuous_momentum only.
--model goulding:    build_features + goulding_monthly only.
--model both:        both, independently, then join_asof on ts_event (each
    continuous daily row matched to the Goulding monthly period containing
    it) purely for comparison -- this is the only place the two models'
    outputs ever meet.
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.instruments import resolve_annualization_days
from derivatives_bt_engine.domain.signal_spec import SignalSpec, build_features, continuous_momentum, goulding_monthly
from derivatives_bt_engine.domain.tsmom_backtester import load_portfolio_data
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ─────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ['MES', 'MNQ', 'MCL', 'MGC']
DEFAULT_YEARS = '2010-2026'
DEFAULT_MODEL = 'both'

_CONTINUOUS_COLS = {
    'ts_fast': 'continuous_fast', 'ts_slow': 'continuous_slow',
    'regime': 'continuous_regime', 'signal': 'continuous_signal',
}
_GOULDING_COLS = {'fast': 'goulding_fast', 'slow': 'goulding_slow', 'regime': 'goulding_regime'}


def run(symbols: list[str], start: date, end: date, model: str, spec: SignalSpec,
        save_prefix: Optional[str] = None) -> dict[str, pl.DataFrame]:
    """model: 'continuous' | 'goulding' | 'both'. Returns {symbol: DataFrame}
    -- the comparison frame (both models' columns + ts_event) when
    model == 'both', otherwise that single model's own output. save_prefix,
    if given, writes "{save_prefix}_{symbol}_{continuous|goulding|comparison}.csv"
    for whichever frame(s) were actually computed."""
    price_data, _ = load_portfolio_data(symbols)

    results: dict[str, pl.DataFrame] = {}
    for sym, df in price_data.items():
        df = df.filter((pl.col('ts_event') >= start) & (pl.col('ts_event') <= end))
        feat = build_features(df)

        continuous_out, goulding_out = None, None

        if model in ('continuous', 'both'):
            # annualization_days is resolved per-instrument (different
            # trading calendars -- 252 for CBOT grains, 259 for the rest of
            # this project's confirmed universe), never a hardcoded/shared
            # constant, per instruments.py's own resolve_annualization_days.
            kwargs = spec.continuous_kwargs()
            kwargs['annualization_days'] = resolve_annualization_days(sym)
            cm = continuous_momentum(feat, **kwargs)
            continuous_out = cm.select(['ts_event', 'close', *_CONTINUOUS_COLS.keys()]).rename(_CONTINUOUS_COLS)
            if save_prefix:
                path = f"{save_prefix}_{sym}_continuous.csv"
                continuous_out.write_csv(path)
                logger.info(f"Saved {path} ({continuous_out.height} rows)")

        if model in ('goulding', 'both'):
            gm = goulding_monthly(feat, **spec.goulding_kwargs())
            goulding_out = gm.select(['ts_event', *_GOULDING_COLS.keys()]).rename(_GOULDING_COLS)
            if save_prefix:
                path = f"{save_prefix}_{sym}_goulding.csv"
                goulding_out.write_csv(path)
                logger.info(f"Saved {path} ({goulding_out.height} rows)")

        if model == 'both':
            # Comparison only -- join_asof(backward) maps each continuous
            # daily row onto the Goulding monthly period containing it, so
            # goulding_fast/slow/regime repeat across every day of that
            # month. This is the ONLY place the two models' outputs meet;
            # neither calculation above used the other's columns.
            joined = continuous_out.sort('ts_event').join_asof(
                goulding_out.sort('ts_event'), on='ts_event', strategy='backward',
            )
            results[sym] = joined
            if save_prefix:
                path = f"{save_prefix}_{sym}_comparison.csv"
                joined.write_csv(path)
                logger.info(f"Saved {path} ({joined.height} rows)")
        else:
            results[sym] = continuous_out if model == 'continuous' else goulding_out

    return results


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                    help='Comma-separated futures symbols, must be known instruments.py symbols (default: %(default)s)')
    p.add_argument('--years', default=DEFAULT_YEARS, help='Year range as START-END, inclusive (default: %(default)s)')
    p.add_argument('--model', choices=['continuous', 'goulding', 'both'], default=DEFAULT_MODEL,
                    help='Which model(s) to run -- the pipeline never requires one to exist for '
                         'the other to run (default: %(default)s)')
    p.add_argument('--fast-window', type=int, default=SignalSpec().fast_window,
                    help='continuous_momentum return/vol horizon, trading days (default: %(default)s)')
    p.add_argument('--slow-window', type=int, default=SignalSpec().slow_window,
                    help='continuous_momentum return/vol horizon, trading days (default: %(default)s)')
    p.add_argument('--w-fast', type=float, default=SignalSpec().w_fast, help='(default: %(default)s)')
    p.add_argument('--w-slow', type=float, default=SignalSpec().w_slow, help='(default: %(default)s)')
    p.add_argument('--discount', type=float, default=SignalSpec().discount,
                    help='continuous_momentum Correction/Rebound discount multiplier (default: %(default)s)')
    p.add_argument('--fast-months', type=int, default=SignalSpec().fast_months,
                    help='goulding_monthly return horizon, calendar months (default: %(default)s)')
    p.add_argument('--slow-months', type=int, default=SignalSpec().slow_months,
                    help='goulding_monthly return horizon, calendar months (default: %(default)s)')
    p.add_argument('--a-co', type=float, default=SignalSpec().a_co, help='eq. 7 Correction mixing weight (default: %(default)s)')
    p.add_argument('--a-re', type=float, default=SignalSpec().a_re, help='eq. 7 Rebound mixing weight (default: %(default)s)')
    p.add_argument('--save-csv', default=None, metavar='PREFIX',
                    help='Write per-symbol/per-model CSVs under this prefix (default: off, nothing saved)')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    start_year, end_year = args.years.split('-')
    start, end = date(int(start_year), 1, 1), date(int(end_year), 12, 31)
    spec = SignalSpec(
        fast_window=args.fast_window, slow_window=args.slow_window,
        w_fast=args.w_fast, w_slow=args.w_slow, discount=args.discount,
        fast_months=args.fast_months, slow_months=args.slow_months,
        a_co=args.a_co, a_re=args.a_re,
    )
    results = run(symbols, start, end, args.model, spec, save_prefix=args.save_csv)
    for sym, out in results.items():
        print(f"{sym}: {out.height} rows")
        print(out.tail(3))


if __name__ == '__main__':
    main()

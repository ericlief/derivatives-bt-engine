"""
Stripped-down TSMOM test: binary sign(signal) direction, flat per-asset
vol-parity sizing (Levine & Pedersen 2016, "Which Trend Is Your Friend?",
Table 1 methodology -- every asset gets the SAME flat annualized-$-vol
target, no cluster/bucket hierarchy, no risk_scalar clamp, no cluster risk
cap, no max_notional/max_contracts ceiling). Monthly rebalance, matching
tsmom_backtester.py's cadence. Reuses only pure data-loading/signal
functions (load_portfolio_data, calculate_trend_strength, _month_end_dates)
-- none of tsmom_backtester.py's own position-sizing.

Written to answer a specific question (see
research/research_trend_strength_crossover_signal.md, Part 2 §6): does
momentum_discount (the flat Correction/Rebound de-risking multiplier in
tsmom_signal.py's compute_position_scalar) actually help on this project's
own recent data? The existing tsmom-bt CLI (tsmom_backtester.py) turned out
to be unsuitable for this -- it sizes each symbol independently against its
own vol_target/max_notional/max_contracts with NO cross-instrument risk
cap (unlike live/tsmom_rebalance.py's compute_desired_risk_budget/
apply_cluster_risk_cap), so a correlated multi-symbol run there produces
wildly overstated realized vol (82-90% against a 15% target was observed).
This script sidesteps that gap entirely with a much simpler, literature-
literal sizing scheme instead of trying to patch it.

Sharpe is invariant to FLAT_PER_ASSET_VOL_TARGET_USD's absolute level (a
uniform leverage rescale) -- the specific value only matters for realistic
contract-count rounding, not for the Sharpe comparison across
--momentum-discounts. Reported returns are additionally post-hoc rescaled
to TARGET_PORTFOLIO_VOL, matching Figure 4's own stated methodology in
Goulding/Harvey/Mazzoleni ("returns scaled to achieve 10% annualized
monthly volatility") -- again, this only restates the return/vol figures,
it does not change Sharpe.

Run:
    python -m scripts.tsmom_binary_vol_parity_backtest
    python -m scripts.tsmom_binary_vol_parity_backtest --momentum-discounts 0.5,1.0 --years 2023-2026
    python -m scripts.tsmom_binary_vol_parity_backtest --symbols ES,NQ,GC --years 2015-2026
"""
from __future__ import annotations

import argparse
from datetime import date

import polars as pl

from derivatives_bt_engine.domain.instruments import get_spec
from derivatives_bt_engine.domain.tsmom_backtester import _month_end_dates, load_portfolio_data
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ['ES', 'NQ', 'CL', 'GC', 'SI', 'ZN', 'ZT', 'ZL', 'ZC', 'ZS', 'ZW', 'JPY', 'BRE', '6M']
DEFAULT_YEARS = '2023-2026'
DEFAULT_WARMUP_START = date(2018, 1, 1)   # signal history buffer before --years' start
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD = 10_000.0  # 1% of default capital, same for every asset -- no clustering
DEFAULT_TARGET_PORTFOLIO_VOL = 0.10
DEFAULT_MOMENTUM_DISCOUNTS = [0.5, 1.0]


def run(symbols: list[str], start: date, end: date, momentum_discount: float,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        flat_per_asset_vol_target_usd: float = DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD,
        target_portfolio_vol: float = DEFAULT_TARGET_PORTFOLIO_VOL,
        warmup_start: date = DEFAULT_WARMUP_START) -> dict:
    price_data, _ = load_portfolio_data(symbols)

    signals = {}
    for sym, df in price_data.items():
        df = df.filter((pl.col('ts_event') >= warmup_start) & (pl.col('ts_event') <= end))
        sig = calculate_trend_strength(df)
        signals[sym] = sig.select(['ts_event', 'close', 'ts', 'daily_std', 'regime']).sort('ts_event')

    rebal_dates = sorted(d for d in _month_end_dates(price_data) if start <= d <= end)
    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in signals.values())))
    all_dates = [d for d in all_dates if start <= d <= end]
    rebal_set = set(rebal_dates)

    held = {s: 0 for s in symbols}
    prev_close: dict[str, float] = {}
    capital = initial_capital
    rows = []

    for d in all_dates:
        # Mark-to-market with yesterday's held contracts against today's move.
        pnl = 0.0
        for s in symbols:
            row = signals[s].filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            close = row['close'][0]
            if s in prev_close and held[s] != 0:
                spec = get_spec(s)
                pnl += held[s] * spec['multiplier'] * (close - prev_close[s])
            prev_close[s] = close
        capital += pnl

        # Rebalance at month-end using today's just-observed signal.
        if d in rebal_set:
            for s in symbols:
                row = signals[s].filter(pl.col('ts_event') == d)
                if row.height == 0:
                    continue
                ts_val, dstd, regime = row['ts'][0], row['daily_std'][0], row['regime'][0]
                if ts_val is None or dstd is None or dstd <= 0:
                    continue
                direction = 1.0 if ts_val > 0 else (-1.0 if ts_val < 0 else 0.0)
                discount = momentum_discount if regime in ('correction', 'rebound') else 1.0
                weight = direction * discount

                spec = get_spec(s)
                close = row['close'][0]
                dollar_vol_per_contract = close * spec['multiplier'] * dstd * (252 ** 0.5)
                if dollar_vol_per_contract <= 0:
                    continue
                held[s] = round(weight * flat_per_asset_vol_target_usd / dollar_vol_per_contract)

        rows.append({'date': d, 'capital': capital})

    stats = pl.DataFrame(rows).with_columns(
        ret=pl.col('capital') / pl.col('capital').shift(1) - 1
    ).drop_nulls('ret')

    mean_ret, std_ret = stats['ret'].mean(), stats['ret'].std()
    ann_ret, ann_vol = mean_ret * 252, std_ret * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol else None
    running_max = stats['capital'].cum_max()
    dd_pct = ((stats['capital'] - running_max) / running_max * 100).min()

    rescale = target_portfolio_vol / ann_vol if ann_vol else None
    return {
        'discount': momentum_discount,
        'n_days': stats.height,
        'raw_ann_ret_pct': round(ann_ret * 100, 2),
        'raw_ann_vol_pct': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd_pct': round(dd_pct, 2),
        f'ann_ret_at_{target_portfolio_vol * 100:.0f}pct_vol': round(ann_ret * rescale * 100, 2) if rescale else None,
    }


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                    help='Comma-separated futures symbols, must be known instruments.py symbols (default: %(default)s)')
    p.add_argument('--years', default=DEFAULT_YEARS, help='Year range as START-END, inclusive (default: %(default)s)')
    p.add_argument('--momentum-discounts', default=','.join(str(d) for d in DEFAULT_MOMENTUM_DISCOUNTS),
                    help='Comma-separated momentum_discount values to compare, one run each (default: %(default)s)')
    p.add_argument('--initial-capital', type=float, default=DEFAULT_INITIAL_CAPITAL)
    p.add_argument('--flat-vol-target', type=float, default=DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD,
                    help='Flat annualized $ vol target, same for every asset -- no clustering (default: %(default)s)')
    p.add_argument('--target-portfolio-vol', type=float, default=DEFAULT_TARGET_PORTFOLIO_VOL,
                    help='Post-hoc rescale target for the reported return figure only, does not affect Sharpe (default: %(default)s)')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    start_year, end_year = args.years.split('-')
    start, end = date(int(start_year), 1, 1), date(int(end_year), 12, 31)
    discounts = [float(d.strip()) for d in args.momentum_discounts.split(',') if d.strip()]

    for discount in discounts:
        result = run(symbols, start, end, discount,
                      initial_capital=args.initial_capital,
                      flat_per_asset_vol_target_usd=args.flat_vol_target,
                      target_portfolio_vol=args.target_portfolio_vol)
        print(result)


if __name__ == '__main__':
    main()

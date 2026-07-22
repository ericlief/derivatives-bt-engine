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
--momentum-discounts. An earlier version also reported a return figure
post-hoc rescaled to a 10% vol target (matching Figure 4's own stated
methodology in Goulding/Harvey/Mazzoleni, "returns scaled to achieve 10%
annualized monthly volatility") -- this was REMOVED after it was flagged
as misleading: the rescale multiplied only the return figure, while
total_fees (reported alongside it) stayed at its actual, un-rescaled
dollar value. That's only truly consistent under an idealized assumption
(fees scale exactly linearly with position size, same as gross P&L) that
integer contract-count rounding breaks in practice. Sharpe alone is the
valid, scale-invariant comparison and needs no such rescale/asterisk --
report raw ann_ret/ann_vol/fees together instead, all at the same actual
scale, or explicitly rescale fees by the same factor if a vol-normalized
return figure is ever needed again.

Transaction costs: get_spec(symbol)['commission'] is charged using the
SAME asymmetric convention as tsmom_backtester.py's own _rebalance_to
(mirrored from FuturesPosition.calculate_pnl) -- opening or adding to a
position is free; only the closing/shrinking leg is charged, at
2 * commission * closed_qty (both round-trip legs bundled into the
close). A same-direction resize charges only the portion that shrinks
back toward zero; a full close or a sign flip charges the entire prior
side. Plus a mandatory quarterly roll charge (2 * abs(held) * commission,
same Mon-before-3rd-Friday Mar/Jun/Sep/Dec schedule
FuturesPosition.roll_date uses, same "close old + reopen new, reopening
is free" logic as tsmom_backtester.py's _process_roll) -- a roll is a
real close-old/open-new round trip and costs commission twice even on a
quarter where the continuous price series happens not to jump.

Two corrections here, both flagged directly by the user rather than
caught in review: (1) an earlier version of this script had NO
transaction costs at all, which inflated its Sharpe/return figures versus
a realistic backtest; (2) the version right after that charged commission
symmetrically on every monthly resize (abs(new_target - held) * 1x
commission, in both directions) -- this overcharged every position
INCREASE (which should be free, same as opening) and used the wrong
quantity/multiplier on decreases and flips (e.g. a full sign flip from
+5 to -3 charged abs(-3-5)=8 units at 1x, instead of the correct
closed_qty=5 at 2x -- closing the whole long side, with the new short
open free). Fixed to match tsmom_backtester.py's _rebalance_to exactly.

Known limitation NOT fixed here, and not unique to this script: the
continuous front-month price series this project uses everywhere
(FuturesDataLoader.daily / futures_dataloader._CONTINUOUS_FRONT_MONTH_SQL)
picks, independently for EVERY date, whichever not-yet-expired contract
has the soonest expiration AMONG THOSE WITH A PRINTED BAR THAT DAY
(`row_number() OVER (PARTITION BY ts_event ORDER BY expiration ASC)`).
This has no memory of which contract was "front" yesterday -- it's
recomputed from scratch each day. Confirmed directly against ZN, March
2023: the Mar'23 contract (instrument_id 397730) trades a genuinely thin
few hundred contracts/day even at its most active, has NO printed bar at
all on 2023-03-19 (so that day's front-month price is silently read from
Jun'23 instead, already the real liquid contract by volume), reappears
with one more trade on 2023-03-20 (so the ranking flips BACK to Mar'23,
a lower price level), then goes quiet for good from 2023-03-21 onward
(ranking settles on Jun'23). The result is a pure contract-switch
artifact -- not a real price move -- read by every downstream consumer as
if it were one instrument's continuous price path. Same root phenomenon
(thin trading right before a contract's own expiration) as the BRE/6L
stuck-roll bug fixed earlier this project's history, surfacing as a
different defect: there, a position got stuck waiting for an exact-date
match that never came; here, the continuous *price series itself*
silently swaps which contract it's quoting. Both naked_futures.py's
Backtester/FuturesPosition path and tsmom_backtester.py's own multi-symbol
path read from this exact same series, so this affects all three backtest
paths equally -- worth its own separate investigation/fix, not patched
here.

Run:
    python -m scripts.tsmom_binary_vol_parity_backtest
    python -m scripts.tsmom_binary_vol_parity_backtest --momentum-discounts 0.5,1.0 --years 2023-2026
    python -m scripts.tsmom_binary_vol_parity_backtest --symbols ES,NQ,GC --years 2015-2026
"""
from __future__ import annotations

import argparse
import math
from datetime import date

import polars as pl

from derivatives_bt_engine.domain.futures_signal_generator import FuturesSignalGenerator
from derivatives_bt_engine.domain.instruments import get_spec
from derivatives_bt_engine.domain.tsmom_backtester import _month_end_dates, load_portfolio_data
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ['ES', 'NQ', 'CL', 'GC', 'SI', 'ZN', 'ZT', 'ZL', 'ZC', 'ZS', 'ZW', 'JPY', 'BRE', '6M']
DEFAULT_YEARS = '2023-2026'
DEFAULT_WARMUP_START = date(2018, 1, 1)   # signal history buffer before --years' start
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD = 10_000.0  # 1% of default capital, same for every asset -- no clustering
DEFAULT_MOMENTUM_DISCOUNTS = [0.5, 1.0]


def run(symbols: list[str], start: date, end: date, momentum_discount: float,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        flat_per_asset_vol_target_usd: float = DEFAULT_FLAT_PER_ASSET_VOL_TARGET_USD,
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

    # Mandatory quarterly contract roll (same schedule FuturesSignalGenerator/
    # tsmom_backtester.py use for FuturesPosition.roll_date -- Monday prior to
    # the third Friday of Mar/Jun/Sep/Dec) -- a real close-old/open-new round
    # trip that costs commission twice even when this project's continuous
    # front-month price series (FuturesDataLoader.daily) doesn't itself show a
    # price change that day. Snapped onto the nearest date actually present in
    # the loaded data (a specific calendar roll_date can fall on a non-trading
    # day for a given symbol).
    roll_dates = set(FuturesSignalGenerator._get_quarterly_roll_dates(start, end))

    held = {s: 0 for s in symbols}
    prev_close: dict[str, float] = {}
    capital = initial_capital
    total_fees = 0.0
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

        # Mandatory quarterly roll: commission on a full close+reopen of
        # whatever is currently held, no price/quantity effect (see note above).
        if d in roll_dates:
            fees = 0.0
            for s in symbols:
                if held[s] != 0:
                    fees += 2 * abs(held[s]) * get_spec(s)['commission']
            capital -= fees
            total_fees += fees

        # Rebalance at month-end using today's just-observed signal.
        if d in rebal_set:
            fees = 0.0
            for s in symbols:
                row = signals[s].filter(pl.col('ts_event') == d)
                if row.height == 0:
                    continue
                ts_val, dstd, regime = row['ts'][0], row['daily_std'][0], row['regime'][0]
                # `dstd <= 0` alone doesn't catch NaN -- comparisons with NaN
                # are always False in Python, so a NaN daily_std (e.g. from a
                # bad/missing price on some date) silently slipped through
                # this guard, propagated into dollar_vol_per_contract, and
                # crashed round() downstream with "cannot convert float NaN
                # to integer" -- confirmed on a real run.
                if (ts_val is None or (isinstance(ts_val, float) and math.isnan(ts_val))
                        or dstd is None or (isinstance(dstd, float) and math.isnan(dstd))
                        or dstd <= 0):
                    continue
                direction = 1.0 if ts_val > 0 else (-1.0 if ts_val < 0 else 0.0)
                discount = momentum_discount if regime in ('correction', 'rebound') else 1.0
                weight = direction * discount

                spec = get_spec(s)
                close = row['close'][0]
                dollar_vol_per_contract = close * spec['multiplier'] * dstd * (252 ** 0.5)
                if dollar_vol_per_contract <= 0:
                    continue
                new_target = round(weight * flat_per_asset_vol_target_usd / dollar_vol_per_contract)
                prior = held[s]
                # Same asymmetric convention as tsmom_backtester.py's
                # _rebalance_to: opening or adding to a position is free;
                # only the closing/shrinking leg charges commission, at 2x
                # (both round-trip legs bundled into the close), matching
                # FuturesPosition.calculate_pnl's fee model. A same-
                # direction resize only charges for the portion that
                # shrinks back toward zero, not the portion added.
                if prior == 0:
                    closed_qty = 0
                elif new_target == 0 or (prior > 0) != (new_target > 0):
                    closed_qty = abs(prior)
                else:
                    closed_qty = max(0, abs(prior) - abs(new_target))
                fees += 2 * closed_qty * spec['commission']
                held[s] = new_target
            capital -= fees
            total_fees += fees

        rows.append({'date': d, 'capital': capital})

    stats = pl.DataFrame(rows).with_columns(
        ret=pl.col('capital') / pl.col('capital').shift(1) - 1
    ).drop_nulls('ret')

    mean_ret, std_ret = stats['ret'].mean(), stats['ret'].std()
    ann_ret, ann_vol = mean_ret * 252, std_ret * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol else None
    running_max = stats['capital'].cum_max()
    dd_pct = ((stats['capital'] - running_max) / running_max * 100).min()

    return {
        'discount': momentum_discount,
        'n_days': stats.height,
        'ann_ret_pct': round(ann_ret * 100, 2),
        'ann_vol_pct': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd_pct': round(dd_pct, 2),
        'total_fees': round(total_fees, 2),
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
                      flat_per_asset_vol_target_usd=args.flat_vol_target)
        print(result)


if __name__ == '__main__':
    main()

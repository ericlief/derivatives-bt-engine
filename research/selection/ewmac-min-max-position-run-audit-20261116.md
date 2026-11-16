# EWMAC minimum-max-position run audit: November 16, 2026

## Scope and conclusion

This audit compares the completed fixed-cap multi-speed EWMAC run with the new
whole-book maximum-position floor of 2.0 against the latest compatible 0.5-floor
baseline:

- baseline `20261113_153041`, scenario without a nondefault floor token and
  persisted `min_max_pos=0.5`;
- test `20261116_124641`, scenario token `minmaxpos2` and persisted
  `min_max_pos=2.0`.

Both runs use $75,000 fixed sizing capital, 20% target volatility, yearly
selection, the same full-contract preference, class/subclass limits, margin
limits, hysteresis, and cached November 13 snapshot. Each current candidate
report has 177 rows, 109 selection-eligible markets, the same snapshot
timestamp, and 176 forecast-eligible markets. M1MSA is the sole forecast
exclusion in both. This is therefore a useful policy comparison. Nine annual
reviews have a one-candidate count difference after the paths diverge, which is
consistent with prior-book eligibility hysteresis; it is not an independent
candidate-universe change.

The implementation works: all 589 saved annual final allocations satisfy the
2.0 floor, and the smallest saved `max_position` is 2.0053. The new
`weight_idm` field is also present and internally consistent in every intended
selection report.

The 2.0 policy is **too restrictive for this $75,000 book**. Average book size
falls from 18.17 to 10.91, no annual review satisfies the configured coverage
constraints, concentration rises materially, and full-history Sharpe falls
from 1.067 to 0.951. The floor removes structurally undersized contracts, but
it does not resolve weak-signal inactivity: TSEMOTHR remains in the 2026 book
because its maximum position is 4.23 even if its realized forecast produces no
held contracts. The causal forecast-activity probation remains a separate
policy requirement.

## Floor and `weight_idm` validation

The 2.0 run persists the active floor in the current book, trials, accepted
passes, annual reviews, and annual final allocations. Across all 589 annual
final-allocation rows:

- floor violations: **0**;
- minimum `max_position`: **2.0053**;
- persisted floor range: **2.0 to 2.0**;
- maximum displayed `weight_idm - limit_weight * idm` absolute residual:
  **0.00013**, explained by independent public-report rounding.

`weight_idm` is present in the saved current `book`, accepted `passes`,
`trial_allocations`, `review_final_allocations`, and `review_instruments`
reports. The current book has `idm=2.5`; capped members therefore show
`limit_weight=0.08` and `weight_idm=0.20`, while half-class-weight members show
`0.0625 * 2.5 = 0.1562` after public rounding.

The 2026 book is:

| Rank | Symbol | Weight | Limit weight | IDM | Weight x IDM | Avg position | Max position | Max con |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | M2K | 0.0625 | 0.0625 | 2.5 | 0.1562 | 1.1346 | 2.2692 | 2 |
| 2 | RS | 0.1250 | 0.0800 | 2.5 | 0.2000 | 1.5956 | 3.1912 | 3 |
| 3 | MWN | 0.0625 | 0.0625 | 2.5 | 0.1562 | 1.8973 | 3.7946 | 4 |
| 4 | BRE | 0.1250 | 0.0800 | 2.5 | 0.2000 | 1.1135 | 2.2269 | 2 |
| 5 | MCL | 0.1250 | 0.0800 | 2.5 | 0.2000 | 2.3392 | 4.6784 | 5 |
| 6 | SCI | 0.1250 | 0.0800 | 2.5 | 0.2000 | 1.6573 | 3.3147 | 3 |
| 7 | MET | 0.1250 | 0.0800 | 2.5 | 0.2000 | 15.1989 | 30.3977 | 30 |
| 8 | V2TX | 0.1250 | 0.0800 | 2.5 | 0.2000 | 4.3009 | 8.6019 | 9 |
| 9 | TSEMOTHR | 0.0625 | 0.0625 | 2.5 | 0.1562 | 2.1142 | 4.2284 | 4 |
| 10 | SOFR3 | 0.0625 | 0.0625 | 2.5 | 0.1562 | 1.3721 | 2.7442 | 3 |

RS behaves as intended. Its final `weight_idm` is 0.20 and its maximum
position is 3.19, so the floor does not reject it merely because its raw weight
changes during construction.

## Why the current book stops at ten

The current greedy path accepts M2K, RS, MWN, BRE, MCL, SCI, MET, V2TX,
TSEMOTHR, and SOFR3. At the attempted eleventh addition, all 153 remaining
candidates are infeasible:

- 144 proposed books breach the 2.0 whole-book floor;
- 74 breach the portfolio margin limit, including 65 that also breach the
  floor;
- the remaining nine are margin-only failures.

The accepted ten-member book already uses $37,420.25 of the $37,500 maximum
margin, leaving $79.75. Thus the observed stop is jointly produced by the new
floor and the pre-existing margin constraint, not a hidden fixed book-count
limit.

Examples make the distinction clear. MGC is rejected with a proposed maximum
position of 0.5576 and excessive margin. MHG is rejected at 1.1269 and
excessive margin. Adding MCD would reduce incumbent BRE to 1.7398 and exceed
margin. Adding SRFXON3 would halve the STIR allocation and reduce incumbent
SOFR3 to 1.3721. MZM itself would have a maximum position of 8.5415 and would
pass the floor, but its proposed book margin is $38,769.17, so it is rejected
by margin alone.

This confirms that the floor is evaluated on every member of each proposed
book after weights and IDM are recalculated, which was the intended behavior.

## Full-history performance

Both curves cover February 3, 1970 through November 12, 2026, with the first
valid return on January 2, 1973 and a complete active book through the common
endpoint. Returns are fixed-cap daily contributions against the fixed $75,000
sizing capital. Annualized mean is arithmetic, not a reinvested CAGR. Fixed-cap
adjusted Calmar is annualized arithmetic mean divided by absolute maximum
drawdown; drawdown is saved net equity divided by its running peak.

| Metric | Floor 0.5 baseline | Floor 2.0 | Floor 2.0 minus baseline |
|---|---:|---:|---:|
| Valid return observations | 13,993 | 13,868 | -125 |
| Ending net equity | $914,232.12 | $745,419.33 | -$168,812.79 |
| Total net P&L | $839,232.12 | $670,419.34 | -$168,812.79 |
| Annualized arithmetic mean | 20.47% | 16.50% | -3.97 pp |
| Annualized volatility | 19.18% | 17.34% | -1.84 pp |
| Sharpe | 1.067 | 0.951 | -0.116 |
| Fixed-cap adjusted Calmar | 2.065 | 1.271 | -0.794 |
| Maximum drawdown | -9.91% | -12.98% | -3.07 pp |
| Maximum drawdown dollars | -$17,306.16 | -$16,978.44 | +$327.72 |
| Drawdown peak to trough | 1974-02-26 to 1974-04-11 | 1973-08-14 to 1973-10-30 | changed |

There are 13,842 common valid-return dates. On those dates, baseline annualized
mean/volatility are 20.71%/19.29% and floor-2 mean/volatility are
16.54%/17.36%. Daily return correlation is 0.815 and sign agreement is 80.84%.
The low correlation relative to a simple sizing change reflects the different
annual books and the path dependence introduced by yearly hysteresis.

## Selection and concentration

| Metric | Floor 0.5 baseline | Floor 2.0 |
|---|---:|---:|
| Annual reviews | 54 | 54 |
| Average candidate count | 75.30 | 75.17 |
| Average book size | 18.17 | 10.91 |
| Minimum / maximum book size | 7 / 26 | 5 / 16 |
| Coverage satisfied | 31 / 54 | 0 / 54 |
| Average effective instrument count | 13.77 | 9.08 |
| Average top-five raw-weight share | 48.75% | 63.20% |
| Average represented classes | 6.00 | 5.67 |
| Average represented subclasses | 9.69 | 7.31 |
| 2026 book size | 23 | 10 |
| 2026 effective instrument count | 14.13 | 9.14 |
| 2026 top-five raw-weight share | 47.62% | 62.50% |

All 54 annual books differ. The floor-2 book is smaller in every year, by one
to thirteen instruments. The 2026 book retains only M2K, RS, MWN, BRE, MCL,
and TSEMOTHR from the baseline book and adds SCI, MET, V2TX, and SOFR3 through
the changed greedy path. Its final score is 1.1294 versus 1.1400 baseline, but
neither score captures the material coverage loss.

Most importantly, `min_book_n=15` does not override the hard feasibility
floor. The floor-2 run has five to sixteen members but zero covered annual
reviews. This is valid implementation behavior, but it makes 2.0 unsuitable as
the default for this capital level unless the user intentionally accepts an
uncovered and more concentrated book.

## P&L and cost reconciliation

Saved cost columns are negative cash flows; the table displays positive fee
magnitudes. Net P&L is gross P&L less trade and roll fees.

| Metric | Floor 0.5 baseline | Floor 2.0 | Difference |
|---|---:|---:|---:|
| Gross P&L | $910,289.74 | $735,983.34 | -$174,306.40 |
| Trade fees | $46,113.25 | $41,975.87 | -$4,137.38 |
| Roll fees | $24,939.16 | $23,582.67 | -$1,356.49 |
| Total fees | $71,052.41 | $65,558.54 | -$5,493.87 |
| Net P&L | $839,232.12 | $670,419.34 | -$168,812.79 |
| Fees / gross P&L | 7.81% | 8.91% | +1.10 pp |
| Fees / net movement | 8.47% | 9.78% | +1.31 pp |

The floor-2 instrument reconciliation residual is -$5.46 after summing public
rows rounded independently. Portfolio-curve gross/net sums differ from the
instrument sums by -$0.11/-$0.08. There are no duplicate `(symbol, date)` cash
keys, no duplicate portfolio dates, no active invalid P&L rows, and no invalid
portfolio returns while a book is active.

Lower absolute fees offset $5,494 of the $174,306 gross-P&L reduction. Costs do
not explain the performance shortfall; the smaller, different books generate
less gross P&L and lower volatility, but lose more return than risk.

The largest floor-2 positive instrument contributors are M6B ($48.60k), MGC
($48.08k), I ($42.73k), MCL ($33.57k), and MCD ($33.30k). The largest negative
contributors are MET (-$8.20k), MYM (-$5.99k), TSEREIT (-$5.00k), V2TX
(-$4.18k), and M2K (-$4.12k). Relative to baseline, the largest losses are SB
(-$71.47k), PL (-$33.69k), ZN (-$27.18k), ZF (-$27.00k), and CT (-$22.90k).
By class, the largest reductions are bonds (-$81.98k), agriculture (-$51.82k),
and metals (-$47.02k).

## Calendar-year OOS slices

Each cell is **return / within-year max drawdown / Sharpe / fixed-cap adjusted
Calmar / gross P&L / positive fees**. Return is the sum of saved daily fixed-cap
contributions against $75,000. Drawdown begins at prior year-end equity. These
are OOS slices of the saved walk-forward backtests, not separate annual refits.
The 2026 slice is partial through November 12.

| Year | Floor 0.5 baseline | Floor 2.0 | Return delta |
|---:|---:|---:|---:|
| 1973 | 80.5% / -7.4% / 3.75 / 11.05 / $61.32k / $0.96k | 56.4% / -13.0% / 2.97 / 4.44 / $43.20k / $0.86k | -24.0 pp |
| 1974 | 61.3% / -9.9% / 2.60 / 6.26 / $47.08k / $1.09k | 29.0% / -11.2% / 1.45 / 2.62 / $22.94k / $1.16k | -32.3 pp |
| 1975 | 1.3% / -6.8% / 0.11 / 0.19 / $2.27k / $1.29k | 3.5% / -7.8% / 0.31 / 0.46 / $3.51k / $0.85k | +2.3 pp |
| 1976 | 8.0% / -4.5% / 0.66 / 1.78 / $7.14k / $1.16k | 6.2% / -6.4% / 0.52 / 0.98 / $5.85k / $1.20k | -1.8 pp |
| 1977 | 44.2% / -2.5% / 2.83 / 18.04 / $35.21k / $2.07k | 47.8% / -3.4% / 2.99 / 14.39 / $37.73k / $1.90k | +3.6 pp |
| 1978 | 59.5% / -7.1% / 2.02 / 8.53 / $46.14k / $1.50k | 54.5% / -8.0% / 1.92 / 6.96 / $42.43k / $1.55k | -5.0 pp |
| 1979 | 85.6% / -3.1% / 3.49 / 28.02 / $65.20k / $1.03k | 75.3% / -4.3% / 3.47 / 17.66 / $57.26k / $0.80k | -10.3 pp |
| 1980 | 35.3% / -5.9% / 1.49 / 6.10 / $27.16k / $0.65k | 11.9% / -3.3% / 0.79 / 3.66 / $9.70k / $0.81k | -23.5 pp |
| 1981 | 61.4% / -3.7% / 2.03 / 16.70 / $47.33k / $1.27k | 30.8% / -3.0% / 1.77 / 10.38 / $24.15k / $1.01k | -30.6 pp |
| 1982 | 35.9% / -3.1% / 1.45 / 11.75 / $28.04k / $1.08k | 15.8% / -1.8% / 1.07 / 8.84 / $12.80k / $0.94k | -20.1 pp |
| 1983 | -4.9% / -2.9% / -0.54 / -1.72 / -$3.06k / $0.58k | 0.1% / -1.9% / 0.01 / 0.06 / $0.84k / $0.75k | +5.0 pp |
| 1984 | 38.7% / -2.0% / 1.75 / 19.77 / $30.22k / $1.21k | 21.5% / -2.2% / 1.47 / 9.94 / $16.87k / $0.78k | -17.2 pp |
| 1985 | 23.2% / -2.5% / 1.31 / 9.32 / $18.61k / $1.22k | 13.8% / -3.9% / 0.86 / 3.57 / $11.29k / $0.95k | -9.4 pp |
| 1986 | 30.8% / -2.8% / 1.31 / 10.85 / $24.32k / $1.24k | 22.1% / -2.6% / 1.19 / 8.44 / $18.05k / $1.47k | -8.7 pp |
| 1987 | 39.3% / -1.9% / 1.74 / 20.55 / $30.62k / $1.16k | 42.7% / -2.2% / 2.03 / 19.19 / $33.00k / $0.98k | +3.4 pp |
| 1988 | -13.7% / -2.2% / -1.17 / -6.22 / -$9.06k / $1.18k | 16.8% / -2.1% / 1.29 / 8.19 / $13.39k / $0.82k | +30.4 pp |
| 1989 | 19.8% / -1.7% / 0.95 / 11.76 / $16.55k / $1.69k | 5.1% / -2.3% / 0.33 / 2.27 / $4.85k / $1.04k | -14.7 pp |
| 1990 | 10.9% / -1.6% / 0.66 / 6.68 / $9.51k / $1.32k | 29.3% / -1.4% / 1.92 / 21.07 / $23.14k / $1.14k | +18.4 pp |
| 1991 | 15.8% / -2.7% / 0.95 / 5.88 / $13.51k / $1.67k | 4.0% / -2.1% / 0.34 / 1.87 / $4.28k / $1.30k | -11.8 pp |
| 1992 | -6.9% / -3.1% / -0.43 / -2.20 / -$3.58k / $1.63k | -3.3% / -2.9% / -0.27 / -1.16 / -$0.67k / $1.83k | +3.6 pp |
| 1993 | 27.0% / -2.3% / 1.44 / 11.48 / $21.90k / $1.66k | 14.2% / -2.2% / 1.00 / 6.57 / $12.27k / $1.60k | -12.8 pp |
| 1994 | 6.2% / -1.6% / 0.43 / 3.83 / $6.27k / $1.60k | 14.6% / -1.9% / 1.07 / 7.47 / $12.66k / $1.68k | +8.4 pp |
| 1995 | 23.3% / -1.1% / 1.24 / 21.09 / $19.01k / $1.54k | 10.2% / -1.7% / 0.70 / 5.95 / $9.34k / $1.72k | -13.1 pp |
| 1996 | 10.1% / -2.3% / 0.44 / 4.32 / $9.59k / $2.04k | 26.8% / -1.9% / 1.40 / 14.10 / $21.84k / $1.75k | +16.7 pp |
| 1997 | 12.8% / -1.4% / 0.69 / 8.87 / $11.02k / $1.44k | 7.4% / -1.6% / 0.55 / 4.49 / $6.92k / $1.39k | -5.4 pp |
| 1998 | 48.0% / -1.4% / 2.44 / 34.33 / $38.18k / $2.21k | 31.7% / -1.8% / 1.87 / 17.49 / $25.64k / $1.83k | -16.2 pp |
| 1999 | -26.3% / -3.8% / -1.33 / -6.79 / -$17.91k / $1.80k | -21.7% / -4.2% / -1.24 / -5.29 / -$14.71k / $1.60k | +4.5 pp |
| 2000 | 34.7% / -1.2% / 1.96 / 27.70 / $27.50k / $1.51k | 22.4% / -1.6% / 1.28 / 13.80 / $18.20k / $1.41k | -12.3 pp |
| 2001 | -3.3% / -2.4% / -0.16 / -1.38 / -$0.87k / $1.62k | 3.7% / -3.3% / 0.18 / 1.11 / $4.43k / $1.62k | +7.1 pp |
| 2002 | 21.6% / -1.8% / 1.12 / 11.52 / $17.72k / $1.55k | 18.7% / -1.3% / 1.32 / 14.10 / $15.36k / $1.34k | -2.9 pp |
| 2003 | 53.5% / -2.6% / 2.24 / 20.31 / $41.84k / $1.69k | 44.8% / -2.7% / 2.08 / 16.16 / $35.20k / $1.59k | -8.7 pp |
| 2004 | 36.3% / -1.2% / 1.45 / 28.56 / $28.69k / $1.44k | 38.4% / -2.0% / 1.40 / 19.23 / $30.07k / $1.29k | +2.0 pp |
| 2005 | 31.4% / -0.8% / 1.61 / 38.67 / $25.02k / $1.49k | 23.9% / -1.6% / 1.35 / 15.28 / $19.49k / $1.56k | -7.5 pp |
| 2006 | 49.4% / -2.2% / 2.13 / 21.85 / $38.12k / $1.04k | 34.8% / -3.5% / 1.46 / 10.00 / $27.29k / $1.18k | -14.6 pp |
| 2007 | -3.4% / -2.0% / -0.16 / -1.64 / -$1.46k / $1.07k | 25.3% / -2.3% / 1.09 / 10.77 / $19.99k / $1.02k | +28.7 pp |
| 2008 | 54.1% / -1.5% / 1.77 / 36.30 / $41.37k / $0.83k | 55.0% / -1.7% / 1.81 / 32.62 / $41.88k / $0.64k | +0.9 pp |
| 2009 | -6.6% / -2.6% / -0.31 / -2.46 / -$4.19k / $0.76k | -4.5% / -3.1% / -0.22 / -1.46 / -$2.63k / $0.75k | +2.1 pp |
| 2010 | 18.5% / -1.2% / 1.10 / 15.61 / $14.75k / $0.90k | 16.6% / -1.4% / 1.02 / 11.60 / $13.22k / $0.76k | -1.9 pp |
| 2011 | 18.6% / -1.4% / 0.86 / 13.26 / $14.78k / $0.86k | 2.1% / -1.7% / 0.10 / 1.25 / $2.19k / $0.62k | -16.5 pp |
| 2012 | -4.4% / -1.3% / -0.36 / -3.29 / -$2.60k / $0.67k | -11.6% / -1.8% / -0.97 / -6.29 / -$7.90k / $0.82k | -7.3 pp |
| 2013 | 7.0% / -0.9% / 0.58 / 7.75 / $6.30k / $1.02k | 11.3% / -1.2% / 0.98 / 9.58 / $9.26k / $0.77k | +4.3 pp |
| 2014 | 18.3% / -0.8% / 1.38 / 22.14 / $14.91k / $1.18k | 8.7% / -1.5% / 0.67 / 5.65 / $7.55k / $1.01k | -9.6 pp |
| 2015 | 12.9% / -1.5% / 0.70 / 8.70 / $11.02k / $1.36k | 8.3% / -1.5% / 0.40 / 5.55 / $7.53k / $1.28k | -4.5 pp |
| 2016 | -0.7% / -1.8% / -0.05 / -0.38 / $0.84k / $1.37k | -3.6% / -2.4% / -0.24 / -1.45 / -$1.27k / $1.44k | -2.9 pp |
| 2017 | 12.4% / -1.0% / 0.83 / 12.69 / $11.22k / $1.92k | 14.6% / -0.8% / 1.07 / 16.94 / $12.56k / $1.61k | +2.2 pp |
| 2018 | -2.0% / -2.2% / -0.14 / -0.90 / $0.24k / $1.77k | -9.7% / -3.2% / -0.64 / -2.97 / -$5.64k / $1.64k | -7.7 pp |
| 2019 | -8.4% / -1.3% / -0.77 / -6.26 / -$4.65k / $1.63k | -9.1% / -1.7% / -0.71 / -5.40 / -$5.33k / $1.53k | -0.8 pp |
| 2020 | 24.4% / -1.0% / 1.44 / 22.85 / $19.94k / $1.62k | 34.6% / -1.4% / 1.83 / 24.08 / $27.70k / $1.79k | +10.1 pp |
| 2021 | 23.1% / -0.9% / 1.20 / 25.56 / $18.50k / $1.21k | 9.9% / -1.5% / 0.53 / 6.46 / $8.56k / $1.16k | -13.2 pp |
| 2022 | 23.4% / -1.6% / 1.27 / 13.74 / $18.62k / $1.05k | 27.0% / -2.1% / 1.41 / 12.90 / $21.31k / $1.02k | +3.6 pp |
| 2023 | 6.0% / -1.0% / 0.35 / 4.84 / $5.99k / $1.50k | -12.9% / -1.6% / -1.08 / -7.22 / -$8.38k / $1.27k | -18.8 pp |
| 2024 | -5.8% / -1.1% / -0.39 / -4.52 / -$3.17k / $1.16k | 4.2% / -1.2% / 0.28 / 3.48 / $4.00k / $0.81k | +10.0 pp |
| 2025 | -16.0% / -1.5% / -1.49 / -10.78 / -$11.20k / $0.81k | -17.8% / -1.9% / -1.63 / -9.22 / -$12.56k / $0.79k | -1.8 pp |
| 2026 | -3.0% / -1.5% / -0.20 / -2.31 / -$1.51k / $0.72k | -7.8% / -1.3% / -0.69 / -6.17 / -$4.66k / $1.16k | -4.8 pp |

The largest improvements are 1988 (+30.4 pp), 2007 (+28.7 pp), 1990
(+18.4 pp), and 1996 (+16.7 pp). The largest reductions are 1974 (-32.3 pp),
1981 (-30.6 pp), 1973 (-24.0 pp), and 1980 (-23.5 pp). This dispersion is
consistent with path-dependent book replacement, not a uniform reduction in
risk.

## Decision

Keep the parameter and report fields, but do not promote 2.0 as the default for
the $75,000 book from this run. The next controlled test should compare 0.5,
1.0, 1.5, and 2.0 and treat coverage satisfaction as a first-class acceptance
criterion, not performance alone. A 1.0 floor is the most natural next point:
it requires the forecast maximum to reach one continuous contract while
allowing the executable half-up rounded quantity to be one.

The position floor should remain structural. Weak-signal probation should be
based on causal realized forecast/held-contract activity over the prior
window. TSEMOTHR surviving this floor demonstrates why the two policies cannot
be substituted for each other.

## Artifacts

The source reports are the `multi_speed_ewmac_selection_*.parquet` files under:

- `/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261113_153041/tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone/`
- `/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_124641/tv0p2_im20_bm50_wi0p2_st0p85_fdm1_minmaxpos2_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone/`

This audit read the saved `portfolio_curve`, `cash_pnl`, `book`, `candidates`,
`forecast_audit`, `passes`, `trials`, `trial_allocations`, `reviews`,
`review_instruments`, and `review_final_allocations` reports. It did not
regenerate or overwrite either backtest.

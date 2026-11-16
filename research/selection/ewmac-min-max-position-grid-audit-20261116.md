# EWMAC minimum-max-position grid audit: November 16, 2026

## Scope and conclusion

This audit compares the completed fixed-cap EWMAC selection runs with
`min_max_pos` equal to **1.0, 1.25, and 2.0**. It does not use the 0.5 baseline
as a grid member. The source scenarios are:

- 1.0: run `20261116_135922`, scenario token `minmaxpos1`;
- 1.25: run `20261116_135403`, scenario token `minmaxpos1p25`;
- 2.0: run `20261116_124641`, scenario token `minmaxpos2`.

All three runs use $75,000 fixed sizing capital, 20% target volatility, yearly
selection, full-contract preference, the same class/subclass and margin limits,
and the same cached snapshot. Each current candidate and forecast-audit report
contains 177 rows. Candidate counts differ slightly after books diverge because
prior-book hysteresis is path dependent.

The **1.0 floor is the clear full-history winner**. It has the highest net P&L,
annualized arithmetic mean, Sharpe, adjusted Calmar, book count, coverage, and
effective instrument count. Moving from 1.0 to 1.25 removes the remaining
zero-held 2026 selection, BTS, but costs $86.3k of full-history net P&L and
reduces Sharpe from 1.075 to 0.979. Moving to 2.0 costs a further $56.7k,
concentrates the book materially, and leaves every annual review short of its
coverage requirements.

The result favors a 1.0 structural floor plus a separate causal forecast-
activity probation rule. Raising the structural floor is an expensive way to
remove weak or inactive signals because it changes every member's recomputed
weight and the entire greedy selection path.

## Mechanical and data validation

All saved final allocations satisfy their configured floor:

| Floor | Final-allocation rows | Violations | Smallest `max_position` |
|---:|---:|---:|---:|
| 1.0 | 788 | 0 | 1.0007 |
| 1.25 | 708 | 0 | 1.2517 |
| 2.0 | 589 | 0 | 2.0053 |

All curves run from February 3, 1970 through November 12, 2026. The first valid
return is January 2, 1973, and each curve has a complete active book through the
common endpoint. The valid-return counts are 13,963, 13,952, and 13,868 for
floors 1.0, 1.25, and 2.0 respectively; 13,867 dates are common to all three.

There are no duplicate portfolio dates, no duplicate `(symbol, date)` cash-P&L
keys, no active invalid instrument-P&L rows, and no invalid returns while a book
is active. Curve-level gross less positive fees reconciles to net within $2.90,
$2.58, and $2.59 respectively. Instrument-level reconciliation residuals are
$5.30, $5.04, and $5.46 because public rows are rounded independently.

Common-date daily-return correlations are 0.932 between 1.0 and 1.25, 0.843
between 1.0 and 2.0, and 0.879 between 1.25 and 2.0. The declining correlation
confirms that higher floors change portfolio membership rather than merely
scaling the same book.

## Full-history performance

Returns are fixed-cap daily contributions against the fixed $75,000 sizing
capital. Annualized mean is arithmetic, not a reinvested CAGR. Fixed-cap
adjusted Calmar is annualized arithmetic mean divided by absolute maximum
drawdown. Drawdown is saved net equity divided by its running peak.

| Metric | Floor 1.0 | Floor 1.25 | Floor 2.0 |
|---|---:|---:|---:|
| Ending net equity | $888,466.93 | $802,165.91 | $745,419.33 |
| Total net P&L | $813,467.07 | $727,166.01 | $670,419.26 |
| Annualized arithmetic mean | 19.90% | 17.80% | 16.50% |
| Annualized volatility | 18.52% | 18.18% | 17.34% |
| Sharpe | 1.075 | 0.979 | 0.951 |
| Fixed-cap adjusted Calmar | 2.007 | 1.295 | 1.271 |
| Maximum drawdown | -9.91% | -13.75% | -12.98% |
| Maximum drawdown dollars | -$17,306.16 | -$19,424.42 | -$16,978.44 |
| Drawdown peak to trough | 1974-02-26 to 1974-04-11 | 1974-02-26 to 1974-12-02 | 1973-08-14 to 1973-10-30 |

The loss from tightening the floor is much larger than the risk reduction.
From 1.0 to 1.25, annualized mean falls 2.10 percentage points while volatility
falls only 0.34 points. From 1.0 to 2.0, mean falls 3.40 points while volatility
falls 1.17 points. Neither stricter floor improves drawdown.

The annual comparison is not driven by one isolated year:

| Pair | First wins | Second wins | Mean annual first-minus-second | Median annual first-minus-second |
|---|---:|---:|---:|---:|
| 1.25 versus 1.0 | 24 | 29 | -2.11 pp | -0.25 pp |
| 2.0 versus 1.0 | 19 | 35 | -3.54 pp | -4.51 pp |
| 2.0 versus 1.25 | 24 | 30 | -1.43 pp | -1.47 pp |

One 1.0-versus-1.25 year is tied. From 2020 through the partial 2026 year, 1.0
beats 1.25 in all seven annual slices. Over that interval, fixed-cap return
contributions total 51.38% for 1.0, 18.42% for 1.25, and 37.30% for 2.0. The
2.0 path is better than 1.25 in five of those seven years, showing that results
are path dependent and not monotonic in the threshold.

## Performance by era

Each return is the sum of fixed-cap daily percentage contributions within the
stated era. Sharpe is calculated from that era's daily observations.

| Era | Floor 1.0 return / Sharpe | Floor 1.25 return / Sharpe | Floor 2.0 return / Sharpe |
|---|---:|---:|---:|
| 1973-1979 | 343.05% / 2.339 | 270.26% / 2.000 | 272.79% / 2.030 |
| 1980s | 202.59% / 1.111 | 192.05% / 1.072 | 180.53% / 1.147 |
| 1990s | 133.71% / 0.742 | 149.77% / 0.858 | 113.06% / 0.750 |
| 2000s | 283.70% / 1.310 | 267.33% / 1.216 | 262.51% / 1.184 |
| 2010s | 70.45% / 0.445 | 73.37% / 0.459 | 27.62% / 0.179 |
| 2020-2026 partial | 51.38% / 0.452 | 18.42% / 0.166 | 37.30% / 0.342 |

The 1.25 floor has isolated stronger regimes, particularly the 1990s and
2010s, but its full-history and recent results do not compensate for losses in
the 1970s, 2000s, and 2020s. Floor 2.0 is weakest across most long regimes.

## Selection, coverage, and risk allocation

| Metric | Floor 1.0 | Floor 1.25 | Floor 2.0 |
|---|---:|---:|---:|
| Annual reviews | 54 | 54 | 54 |
| Average candidate count | 75.20 | 75.22 | 75.17 |
| Average book size | 14.59 | 13.11 | 10.91 |
| Minimum / maximum book size | 7 / 22 | 6 / 22 | 5 / 16 |
| Coverage satisfied | 7 / 54 | 5 / 54 | 0 / 54 |
| Average effective instrument count | 11.29 | 10.52 | 9.08 |
| Average top-five raw-weight share | 54.63% | 57.27% | 63.20% |
| Average represented classes | 5.89 | 5.76 | 5.67 |
| Average represented subclasses | 9.04 | 8.57 | 7.31 |
| Average summed `limit_weight` | 0.837 | 0.819 | 0.765 |
| Average aggregate risk budget | $30,390 | $29,602 | $27,044 |
| 2026 book size | 19 | 16 | 10 |

Coverage shortfalls worsen monotonically. The minimum-book-count requirement
is missed in 28, 33, and 46 reviews respectively. Subclass coverage is missed
in 38, 45, and 54 reviews. These are reported as hard-feasibility shortfalls,
not a hidden book-count stop.

The interaction with the 8% per-instrument weight cap is important. Smaller
books allocate more raw weight to each member, but capped `limit_weight` cannot
absorb all of it. The summed effective allocation and aggregate risk budget
therefore decline as the floor rises. This is why a structural floor also
becomes a de-risking policy even though target volatility is unchanged.

## Current books and activity

| Metric | Floor 1.0 | Floor 1.25 | Floor 2.0 |
|---|---:|---:|---:|
| Current members | 19 | 16 | 10 |
| Portfolio margin at forecast maximum | $37,006.34 | $36,874.54 | $37,420.25 |
| Margin remaining | $493.66 | $625.46 | $79.75 |
| Summed `limit_weight` | 0.874 | 0.874 | 0.730 |
| Aggregate risk budget | $32,785.71 | $32,785.71 | $27,375.00 |
| Members never held in 2026 | 1 | 0 | 0 |
| Lowest member held-day rate | 0.0% | 29.0% | 56.2% |

### Floor 1.0 current book

The 19-member book has mean `avg_pos` **1.0582** and median `avg_pos`
**0.8982**. These contract counts are descriptive; they are not directly
comparable measures of risk across instruments with different contract sizes.

| Rank | Symbol | Weight | Limit weight | IDM | Weight x IDM | Avg pos | Max position | Max con |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | M2K | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 0.6483 | 1.2967 | 1 |
| 2 | RS | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 0.7123 | 1.4247 | 1 |
| 3 | MWN | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 0.7228 | 1.4456 | 1 |
| 4 | MHG | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.6440 | 1.2879 | 1 |
| 5 | BRE | 0.0476 | 0.0476 | 2.5000 | 0.1190 | 0.6628 | 1.3255 | 1 |
| 6 | MCL | 0.1429 | 0.0800 | 2.5000 | 0.2000 | 2.3392 | 4.6784 | 5 |
| 7 | MBT | 0.1429 | 0.0800 | 2.5000 | 0.2000 | 0.8982 | 1.7965 | 2 |
| 8 | TSEMOTHR | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 1.2081 | 2.4162 | 2 |
| 9 | LBR | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.9063 | 1.8125 | 2 |
| 10 | SONIA.N | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 0.6250 | 1.2500 | 1 |
| 11 | SCI | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 1.4798 | 2.9595 | 3 |
| 12 | J7 | 0.0476 | 0.0476 | 2.5000 | 0.1190 | 0.5144 | 1.0289 | 1 |
| 13 | GBS | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 0.9906 | 1.9813 | 2 |
| 14 | MZW | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 2.7281 | 5.4563 | 5 |
| 15 | TSEREIT | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 1.0108 | 2.0216 | 2 |
| 16 | SRFXON3 | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 1.7622 | 3.5244 | 4 |
| 17 | MYM | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 0.5644 | 1.1289 | 1 |
| 18 | KU | 0.0476 | 0.0476 | 2.5000 | 0.1190 | 1.1191 | 2.2383 | 2 |
| 19 | BTS | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 0.5703 | 1.1406 | 1 |

### Floor 1.25 current book

The 16-member book has mean `avg_pos` **1.2197** and median `avg_pos`
**1.0025**.

| Rank | Symbol | Weight | Limit weight | IDM | Weight x IDM | Avg pos | Max position | Max con |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | M2K | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 1.2967 | 2.5933 | 3 |
| 2 | RS | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 0.7123 | 1.4247 | 1 |
| 3 | MWN | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 0.7228 | 1.4456 | 1 |
| 4 | MHG | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.6440 | 1.2879 | 1 |
| 5 | BRE | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.9942 | 1.9883 | 2 |
| 6 | MCL | 0.1429 | 0.0800 | 2.5000 | 0.2000 | 2.3392 | 4.6784 | 5 |
| 7 | MBT | 0.1429 | 0.0800 | 2.5000 | 0.2000 | 0.8982 | 1.7965 | 2 |
| 8 | TSEMOTHR | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 1.2081 | 2.4162 | 2 |
| 9 | LBR | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.9063 | 1.8125 | 2 |
| 10 | SONIA.N | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 1.2500 | 2.4999 | 2 |
| 11 | SCI | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 1.4798 | 2.9595 | 3 |
| 12 | J7 | 0.0714 | 0.0714 | 2.5000 | 0.1786 | 0.7716 | 1.5433 | 2 |
| 13 | GBS | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 0.9906 | 1.9813 | 2 |
| 14 | MZW | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 2.7281 | 5.4563 | 5 |
| 15 | TSEREIT | 0.0357 | 0.0357 | 2.5000 | 0.0893 | 1.0108 | 2.0216 | 2 |
| 16 | MTN | 0.0238 | 0.0238 | 2.5000 | 0.0595 | 1.5625 | 3.1250 | 3 |

Floor 1.0's sole current zero-held member is BTS. Floor 1.25 removes BTS and
has no current zero-held members. Its least active members are MWN and RS,
held on about 29% of their active 2026 sessions. Floor 2.0 forces every member
to trade more frequently, but this is not evidence of better signal quality.
For example, TSEMOTHR's held-day rate rises from 36.6% at 1.25 to 58.0% at 2.0,
while its 2026 net P&L remains negative in both paths.

All three current books are close to the $37,500 portfolio margin limit.
Raising the floor therefore does not free meaningful margin: it assigns larger
positions to fewer members and consumes nearly the same limit.

## P&L and costs

Saved cost columns are negative cash flows; this table displays positive fee
magnitudes. Net P&L is gross P&L less trade and roll fees.

| Metric | Floor 1.0 | Floor 1.25 | Floor 2.0 |
|---|---:|---:|---:|
| Gross P&L | $883,469.96 | $795,487.86 | $735,983.23 |
| Trade fees | $44,743.31 | $43,354.82 | $41,978.56 |
| Roll fees | $25,256.68 | $24,964.45 | $23,582.82 |
| Total fees | $69,999.99 | $68,319.27 | $65,561.38 |
| Net P&L | $813,467.07 | $727,166.01 | $670,419.26 |
| Fees / gross P&L | 7.92% | 8.59% | 8.91% |

Higher floors reduce absolute fees, but gross P&L falls much faster. Moving
from 1.0 to 1.25 saves only $1.68k of fees while losing $87.98k of gross P&L.
Moving from 1.0 to 2.0 saves $4.44k of fees while losing $147.49k gross. Cost
reduction does not justify either tighter floor.

## Decision

Use **1.0** as the preferred structural floor among these three trials. It
provides the best performance and diversification while guaranteeing one full
continuous contract at the forecast maximum. It still does not make the
configured coverage targets broadly feasible, so it should not be interpreted
as a complete portfolio-eligibility solution.

Do not use 1.25 merely to eliminate BTS or another current zero-held market.
That is better addressed by causal forecast-activity probation using only
history available before each selection date. Floor 2.0 should not be used for
the $75,000 book.

Finally, `max_con` uses half-up rounding. A 1.25 continuous floor does not
guarantee `max_con >= 2`; that executable threshold begins at a continuous
position of 1.5. The present comparison answers the requested 1.0/1.25/2.0
grid and should not be read as a test of an exact two-executable-contract rule.

## Artifacts

The source reports are the `multi_speed_ewmac_selection_*.parquet` files under
the three scenario directories listed in the scope. This audit read those
artifacts without regenerating or overwriting any saved backtest.

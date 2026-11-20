# Carry staleness rerun audit, November 20, 2026

The rerun fixes the contract-mismatch freeze documented in the
[November 19 carry audit](carry-null-roll-audit-20261119.md#carry-only-forward-fill-run-november-19-at-230106).
MWN and UB now have a maximum forecast age of **91 calendar days**, down
from **3,889**. In the 2026 cash book, active rows using forecasts older
than one year fell from **669 to zero**. One unselected route, AIGCI,
still has year-old forecasts because its curve lacks valid price pairs.

## Runs and verification

The baseline is `selection_run_20261119_230106` and the completed v5 rerun
is `selection_run_20261120_103601`, under
`/home/dev/data/fin/misc/results/selection_v1/carry/`. Both use fixed
$75,000 sizing capital, 20% target volatility, annual top-down class
weighting, 15% instrument and 50% book margin caps, a one-contract minimum
estimated maximum position, one search path, and 256-session probation
with 0.50/0.25 thresholds and two weak reviews. The analysis reads the
saved forecast, cash, portfolio, review and allocation Parquets.

The rerun's 1,167,209 forecast rows cover 177 symbols. Every recorded
forecast origin matches the last valid same-session carry quote, with
zero future origins and zero age discrepancies. All four forward-filled
rule components match their originating values at saved precision.
Decimal returns are shown as percentages. All forecast ages are
elapsed calendar days.

## Forecast staleness

| Measure | Nov 19 | Nov 20 |
|---|---:|---:|
| Valid forecast rows | 1,159,110 | 1,163,215 |
| Valid rows using a prior observation | 148,218 | 140,468 |
| Prior observation more than 7 days old | 111,353 | 102,600 |
| Prior observation more than 365 days old | 12,143 | 494 |
| Median age among fills | 30 | 25 |
| 95th percentile age among fills | 722 | 83 |
| Maximum age | 3,889 | 1,085 |
| MWN and UB maximum age | 3,889 | 91 |
| 2026 IB forecast rows older than 7 days | 4,534 | 769 |

On the 1,159,110 jointly valid symbol-dates, the count older than seven
days fell from 111,353 to 100,169. The count older than a year fell from
12,143 to 494, a **95.9% decline**, even on these matched rows. Forecast
sign agreement was 99.76%, and the mean absolute forecast difference was
0.01008.

The rerun adds 1,436 symbol-dates. On common rows, 15,116 displayed
adjusted closes, 34 point changes and 36 raw carry values differ. The
saved portfolio comparison includes these input changes as well as the
forecast refresh change and resulting book changes.

All 494 remaining year-old rows belong to AIGCI (BBCOMM). Of these, 431
have an exact-zero spread and 63 have an invalid curve price. Its oldest
forecast on September 10, 2026 dates to September 21, 2023, 1,085 days
earlier. These rows supply no fresh carry input under either policy.
AIGCI contributes no active 2026 cash rows.

## What reached the 2026 cash book

Cash and public forecast values agree wherever both have a row. For
1,992 active IB cash rows outside the forecast panel, the audit
reconstructed forecasts from the run's v5 stitched artifacts and
eligible rule weights. All matched the saved cash forecasts at report
precision. This resolves forecast age for every active cash row.

| Measure | Nov 19 | Nov 20 |
|---|---:|---:|
| Active cash rows in 2026 | 4,221 | 4,235 |
| Used a prior carry observation | 730 | 154 |
| Used an observation older than 7 days | 670 | 52 |
| Used an observation older than 365 days | 669 | 0 |
| Held contracts on rows older than 365 days | 668 rows | 0 rows |

All three markets behind the 669 rows remained in the 2026 book. Their
forecasts now refresh and the resulting positions differ.

| Symbol | Oldest 2026 forecast used, Nov 20 | Nov 19 net P&L | Nov 20 net P&L |
|---|---:|---:|---:|
| MCL | 1 day | $6,974.64 | $3,446.76 |
| W | 29 days | $4,430.00 | $1,015.00 |
| SRFXON3 | 0 days | $1,232.77 | −$242.40 |
| **Total** | | **$12,637.41** | **$4,219.36** |

The oldest carry observation used by any active 2026 route was 58 days
old (RS). The three formerly frozen routes account for $8,418.05 of the
$11,488.29 decline in saved 2026 net P&L. That is cash attribution to the
observed runs. Across all years, active cash rows older than seven days
rose from 15,569 to 16,108 as books and exposure changed.

## P&L and book coverage

| $75,000 fixed-cap measure | Nov 19 | Nov 20 |
|---|---:|---:|
| Equity at Nov 11, last common date with all 19 active cash rows | $684,598.10 | $669,613.63 |
| Equity at saved endpoint, Nov 18 | $685,040.82 | $670,310.87 |
| Cumulative net P&L at saved endpoint | $610,040.82 | $595,310.87 |
| Cumulative net P&L through Mar 28, 2024 | $609,933.59 | $601,329.82 |
| IB-tagged net P&L | $245.93 | −$5,880.25 |
| Gross P&L | $676,070.30 | $660,951.21 |
| Trade fees | $34,336.36 | $34,185.65 |
| Roll fees | $31,692.41 | $31,453.99 |
| Annualized arithmetic return | 14.54% | 14.19% |
| Annualized volatility | 18.47% | 18.32% |
| Sharpe | 0.787 | 0.775 |
| Maximum drawdown | −50.53% | −50.53% |
| Maximum dollar drawdown | −$121,756.82 | −$121,756.82 |
| Fixed-cap adjusted Calmar | 0.288 | 0.281 |
| 2026 net P&L through Nov 18 | $17,688.65 | $6,200.36 |

Risk metrics use the saved zero-P&L convention for absent cash rows
and 256 sessions per year. Fixed-cap adjusted Calmar divides annualized
arithmetic return by absolute equity drawdown. Both runs reached their
drawdown trough on February 5, 1980 after peaking on July 6, 1976. The
rerun had all 19 active cash rows on November 17, its final full
book date. November 18 had 10 observed active rows in the rerun and six
in the baseline.

The rerun has no duplicate portfolio dates, cash symbol-date keys or
invalid economically active rows. Gross less fees exceeds recorded net
cash by $0.69, and recorded net cash exceeds summed daily portfolio
net by $0.14. The largest absolute daily cash residual is $0.0051.

Membership changes in 11 of 55 annual reviews and book size changes in
five. The mean book count rises from 13.49 to 13.56. The 2026 book keeps
19 names, replacing MZL, YC and CAD with SOFR3, RS and J7. All 746
accepted annual allocations meet the documented position and margin limits.

### Annual out-of-sample slices

These are calendar slices of the saved replay. Net return divides cash
P&L by the fixed $75,000. The drawdown baseline includes prior year-end
equity. Gross and fees are in USD. The 2026 slice includes November 18.

| Year | Book | Rerun net return | Drawdown | Sharpe | Adj. Calmar | Gross P&L | Fees |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1972 | 4 | +18.90% | -12.31% | 1.35 | 1.51 | 15,439 | 1,266 |
| 1973 | 5 | +82.76% | -5.68% | 4.80 | 14.30 | 62,781 | 712 |
| 1974 | 9 | +69.07% | -4.74% | 3.11 | 14.30 | 52,866 | 1,061 |
| 1975 | 8 | +21.25% | -3.21% | 1.21 | 6.50 | 17,332 | 1,398 |
| 1976 | 8 | +11.74% | -5.68% | 0.74 | 2.02 | 10,092 | 1,284 |
| 1977 | 10 | -2.33% | -6.94% | -0.12 | -0.33 | -171 | 1,578 |
| 1978 | 11 | -9.10% | -5.95% | -0.51 | -1.51 | -5,585 | 1,238 |
| 1979 | 11 | -117.17% | -40.11% | -4.74 | -2.87 | -86,776 | 1,099 |
| 1980 | 8 | +24.59% | -14.98% | 1.01 | 1.60 | 19,220 | 775 |
| 1981 | 8 | +50.98% | -5.94% | 2.26 | 8.41 | 39,343 | 1,111 |
| 1982 | 9 | +30.98% | -6.65% | 1.49 | 4.57 | 24,249 | 1,012 |
| 1983 | 10 | +17.19% | -5.29% | 1.02 | 3.20 | 13,478 | 588 |
| 1984 | 12 | +68.55% | -4.67% | 2.82 | 14.41 | 52,334 | 924 |
| 1985 | 12 | +1.09% | -7.82% | 0.06 | 0.14 | 2,126 | 1,305 |
| 1986 | 14 | +0.21% | -4.95% | 0.01 | 0.04 | 1,259 | 1,099 |
| 1987 | 13 | -22.98% | -9.97% | -0.94 | -2.26 | -16,155 | 1,082 |
| 1988 | 11 | +22.25% | -3.30% | 1.54 | 6.61 | 17,582 | 894 |
| 1989 | 15 | -12.55% | -6.80% | -0.72 | -1.82 | -7,768 | 1,646 |
| 1990 | 13 | +31.15% | -2.86% | 1.58 | 10.70 | 24,531 | 1,165 |
| 1991 | 10 | +27.22% | -2.76% | 1.74 | 9.67 | 21,546 | 1,127 |
| 1992 | 14 | +0.46% | -6.43% | 0.02 | 0.07 | 2,396 | 2,049 |
| 1993 | 15 | +19.15% | -4.31% | 0.91 | 4.36 | 15,746 | 1,382 |
| 1994 | 13 | +3.45% | -5.64% | 0.19 | 0.60 | 4,227 | 1,640 |
| 1995 | 14 | +10.29% | -2.24% | 0.72 | 4.53 | 8,983 | 1,263 |
| 1996 | 15 | +26.27% | -3.42% | 1.23 | 7.50 | 21,170 | 1,466 |
| 1997 | 15 | -8.59% | -4.29% | -0.52 | -1.96 | -5,128 | 1,316 |
| 1998 | 13 | +39.18% | -4.24% | 1.78 | 9.07 | 30,652 | 1,265 |
| 1999 | 12 | -5.44% | -3.17% | -0.33 | -1.69 | -3,043 | 1,036 |
| 2000 | 12 | +23.76% | -3.26% | 1.28 | 7.17 | 19,009 | 1,188 |
| 2001 | 11 | +33.24% | -2.79% | 1.80 | 11.68 | 25,713 | 786 |
| 2002 | 14 | +35.62% | -1.68% | 1.99 | 20.81 | 27,862 | 1,147 |
| 2003 | 15 | +38.20% | -4.86% | 1.55 | 7.72 | 30,053 | 1,401 |
| 2004 | 12 | +49.20% | -2.87% | 2.18 | 16.74 | 37,781 | 877 |
| 2005 | 11 | +31.90% | -1.05% | 1.98 | 29.83 | 24,940 | 1,012 |
| 2006 | 15 | +30.32% | -1.87% | 1.73 | 15.95 | 23,702 | 959 |
| 2007 | 18 | -1.14% | -2.31% | -0.07 | -0.49 | -5 | 850 |
| 2008 | 16 | +21.34% | -1.61% | 1.12 | 12.94 | 16,577 | 570 |
| 2009 | 15 | +10.49% | -1.28% | 0.60 | 8.03 | 8,583 | 712 |
| 2010 | 13 | +41.85% | -1.38% | 2.52 | 29.83 | 32,256 | 866 |
| 2011 | 14 | +46.10% | -2.00% | 2.26 | 22.70 | 35,516 | 944 |
| 2012 | 15 | +12.18% | -1.65% | 0.82 | 7.24 | 10,040 | 905 |
| 2013 | 18 | -8.19% | -2.73% | -0.64 | -2.94 | -4,948 | 1,194 |
| 2014 | 18 | +25.30% | -1.12% | 1.51 | 22.15 | 20,348 | 1,371 |
| 2015 | 18 | +7.36% | -2.19% | 0.30 | 3.29 | 6,829 | 1,312 |
| 2016 | 18 | +9.59% | -1.83% | 0.52 | 5.14 | 9,089 | 1,895 |
| 2017 | 17 | +31.86% | -1.36% | 2.49 | 23.10 | 25,724 | 1,829 |
| 2018 | 19 | -19.92% | -3.08% | -1.42 | -6.35 | -12,976 | 1,962 |
| 2019 | 18 | +18.14% | -0.89% | 1.34 | 20.10 | 15,510 | 1,907 |
| 2020 | 18 | -13.03% | -3.15% | -0.77 | -4.04 | -8,203 | 1,569 |
| 2021 | 18 | -20.36% | -4.26% | -1.09 | -4.69 | -13,631 | 1,638 |
| 2022 | 17 | -5.07% | -1.55% | -0.31 | -3.23 | -3,063 | 740 |
| 2023 | 17 | +16.49% | -3.02% | 0.73 | 5.38 | 13,471 | 1,100 |
| 2024 | 18 | -26.33% | -3.71% | -1.48 | -6.94 | -18,625 | 1,122 |
| 2025 | 20 | -2.04% | -1.42% | -0.15 | -1.41 | -254 | 1,275 |
| 2026 | 19 | +8.27% | -0.83% | 0.70 | 11.11 | 6,929 | 729 |

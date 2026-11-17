# EWMAC Beam 4/8 with probation and 15% instrument margin — 2026-11-17

## Decision

Beam 4 is the preferable beam sensitivity, but this audit does not support
making beam search the production default yet. Beam 8 has a marginal
full-history advantage over Beam 4, but it does not improve class or subclass
coverage, selects a slightly smaller and more concentrated average book,
creates more membership churn, and performs worse from 2015 onward. Most of
its lifetime advantage is a single 2003 path outcome.

Beam 4 improves the common-window Sharpe from 1.025 for greedy to 1.046 and
reduces cumulative book-count deficit from 95 to 80. Beam 8 reaches 1.048 and
reduces the deficit to 75, but its extra width does not produce a monotonic
breadth improvement: in 2024 Beam 4 selects 19 markets and Beam 8 selects 17;
in 2026 the counts are 20 and 18. Neither beam changes the class or subclass
deficit in any of the 54 annual reviews.

The saved 2026 MGC review row is also diagnosed. MGC is present because it was
in the 2025 book and the compact membership report must record its 2026 exit.
It was not rejected for a weak forecast: every MGC selector path was
hard-infeasible because `max_pos` was below the configured minimum of 1. The
generic saved note is a reporting defect, now fixed for future runs.

## Scope

The three saved runs use the same fixed $75,000 capital, yearly selection,
15% per-instrument initial-margin limit, 50% book-margin limit, minimum
`max_pos` of 1, and forecast probation settings 256/0.50/0.25/2. The inputs
are identical after excluding `report_ts_et`: candidates are 177 by 122,
forecast audit 177 by 62, and screen 215 by 498. The comparison therefore
isolates selector search width and its path-dependent prior membership.

| Label | Saved run | Beam width |
|:---|:---|---:|
| Greedy | `selection_run_20261116_202750` | 1 |
| Beam 4 | `selection_run_20261116_213439` | 4 |
| Beam 8 | `selection_run_20261116_214019` | 8 |

The common complete-book endpoint is 2026-11-10. On 2026-11-11 the active
instrument counts fall from the selected 20/20/18 books to 19/19/17, and on
2026-11-12 to 1/2/0. Those last two dates are partial-tail diagnostics and are
excluded from the common performance comparison. No saved run or source market
data was regenerated or modified.

## Common-window performance

The metrics below use the 14,010 return dates shared by all three runs from
1973-01-02 through 2026-11-10. Returns are fixed-capital returns. Adjusted
Calmar in the full-run diagnostic uses annualized fixed-capital return divided
by peak-to-trough equity drawdown, not a compounded-CAGR numerator.

| Run | Ann return | Ann vol | Sharpe | Cumulative fixed-cap return |
|:---|---:|---:|---:|---:|
| Greedy | 19.173% | 18.704% | 1.025 | 1,049.291% |
| Beam 4 | 19.686% | 18.828% | 1.046 | 1,077.330% |
| Beam 8 | 19.782% | 18.875% | 1.048 | 1,082.600% |

All three have the same full-run maximum drawdown, -9.913% or -$17,306.16,
on 1974-04-11. Their full saved-run adjusted Calmar values are 1.934, 1.982,
and 1.994 respectively. The small Beam 8 edge is not stable across more recent
windows:

| Start | Greedy Sharpe | Beam 4 Sharpe | Beam 8 Sharpe |
|---:|---:|---:|---:|
| 2000 | 0.730 | 0.726 | 0.762 |
| 2010 | 0.416 | 0.380 | 0.357 |
| 2015 | 0.268 | 0.239 | 0.161 |
| 2020 | 0.298 | 0.364 | 0.351 |
| 2024 | -0.269 | -0.338 | -0.449 |

Beam 8 beats Beam 4 by 27.13 percentage points in 2003, while losing 9.91
points in 2004, 8.21 in 2017, 5.23 in 2016, and 4.42 in 2024. Its full-history
edge is consequently path-dependent rather than a broadly repeated gain.

## Cost and P&L reconciliation through the common endpoint

| Run | Gross P&L | Trade cost | Roll cost | Net cash P&L | Gross-cost-net residual | Cash-curve residual |
|:---|---:|---:|---:|---:|---:|---:|
| Greedy | $854,971.66 | $44,684.96 | $23,685.23 | $786,596.89 | $4.58 | $0.20 |
| Beam 4 | $876,510.50 | $44,601.51 | $24,249.04 | $807,656.16 | $3.79 | -$0.07 |
| Beam 8 | $881,033.31 | $44,688.04 | $24,518.62 | $811,822.21 | $4.44 | $0.24 |

Beam 8's $4,166.05 net advantage over Beam 4 is almost entirely gross P&L:
gross is $4,522.81 higher while combined costs are $356.11 higher. Costs do
not explain the ranking. The small gross-cost-net residual is the accumulated
effect of persisted monetary rounding.

There are no duplicate `(symbol, date)` cash rows, duplicate portfolio dates,
active invalid cash-P&L rows, or invalid active-book return rows in any run.
Beam 4 has 17 active source handoff gaps and Beam 8 has 15, both with a maximum
gap of five days; greedy has 19 and a maximum gap of 82 days.

## Book construction, coverage, and stability

| Measure across 54 reviews | Greedy | Beam 4 | Beam 8 |
|:---|---:|---:|---:|
| Average selected markets | 15.111 | 15.426 | 15.352 |
| Min / max selected | 7 / 23 | 7 / 22 | 7 / 23 |
| Cumulative class deficit | 20 | 20 | 20 |
| Cumulative subclass deficit | 58 | 58 | 58 |
| Cumulative book-count deficit | 95 | 80 | 75 |
| Reviews with zero book-count deficit | 27 | 30 | 33 |
| Reviews satisfying all coverage targets | 7 | 7 | 7 |
| Average effective market count | 11.780 | 11.802 | 11.746 |
| Average top-five weight | 53.147% | 53.314% | 53.446% |
| Average / max margin at `max_pos` | $27,556.74 / $37,309.38 | $27,421.30 / $37,112.17 | $27,705.42 / $37,470.76 |
| Additions + removals after first review | 523 | 529 | 551 |
| Selected probation market-years | 82 | 78 | 79 |
| Zero-held selected market-years | 2 | 3 | 2 |

The class and subclass deficit sequences are exactly equal in all three runs.
Beam search repairs only some early book-count deficits. Since 2015, all runs
have zero final book-count deficit, so additional beam width offers no recent
breadth repair. Beam 8 also has the highest transition count and the lowest
average effective market count of the three.

Beam 4 and Beam 8 choose the same annual book in only 24 of 54 reviews. The
first difference is 1980. Beam 4 matches greedy in 18 reviews and Beam 8 in
11. Beam 4 retains an average of 4.76 terminal paths; Beam 8 retains 9.56, yet
their terminal book-count ranges differ in 38 reviews. More retained paths do
not ensure that the final chosen route has more markets after deficit and score
tie-breaking.

## Recent selected books

`Held` counts selected markets that hold a nonzero contract at least once in
the selection year. Margin is the rounded portfolio margin at `max_pos`.

| Year | B4 selected / held | B4 eff N | B4 top 5 | B4 margin | B8 selected / held | B8 eff N | B8 top 5 | B8 margin |
|---:|:---:|---:|---:|---:|:---:|---:|---:|---:|
| 2015 | 21 / 21 | 15.69 | 42.85% | $29,229.58 | 21 / 21 | 15.69 | 42.85% | $29,229.58 |
| 2016 | 21 / 21 | 15.48 | 42.85% | $35,550.81 | 20 / 20 | 14.89 | 42.85% | $37,470.76 |
| 2017 | 20 / 20 | 15.48 | 42.85% | $33,527.55 | 21 / 21 | 15.69 | 42.85% | $33,491.08 |
| 2018 | 22 / 22 | 15.69 | 42.85% | $30,901.40 | 23 / 23 | 16.57 | 42.85% | $34,413.16 |
| 2019 | 19 / 19 | 12.92 | 50.00% | $30,823.97 | 20 / 20 | 13.52 | 50.00% | $30,032.79 |
| 2020 | 22 / 22 | 16.34 | 42.85% | $30,830.86 | 22 / 22 | 16.34 | 42.85% | $34,167.91 |
| 2021 | 20 / 20 | 14.78 | 43.75% | $33,016.49 | 20 / 20 | 14.78 | 43.75% | $32,145.41 |
| 2022 | 15 / 15 | 12.19 | 50.00% | $31,244.45 | 15 / 15 | 12.19 | 50.00% | $31,321.35 |
| 2023 | 17 / 17 | 13.84 | 43.75% | $34,710.20 | 17 / 17 | 13.84 | 43.75% | $34,710.20 |
| 2024 | 19 / 19 | 14.64 | 43.75% | $37,014.69 | 17 / 17 | 13.84 | 43.75% | $37,184.39 |
| 2025 | 20 / 20 | 17.83 | 35.70% | $36,562.90 | 20 / 20 | 17.83 | 35.70% | $36,562.90 |
| 2026 | 20 / 18 | 13.52 | 50.00% | $37,112.17 | 18 / 17 | 12.51 | 50.00% | $35,377.78 |

In 2026 Beam 4 has only $387.83 margin headroom. Its zero-held selected markets
are BTS and SRFXON3; Beam 8's is SRFXON3. Across the entire history the other
zero-held selection is SOFR3 in 2014 for all three runs. Probation therefore
reduces but does not eliminate inactive selections; it also is not the cause
of MGC's 2026 exit.

The 2026 books differ materially. Beam 8 alone has DAX, MTN, and NZD. Beam 4
alone has BRE, BTS, KU, TSEMOTHR, and TSEREIT. Both still have class deficit 1,
subclass deficit 3, and book-count deficit 0, so Beam 8 gives up two markets
without improving the hierarchy objective.

## MGC 2026 exit reason

The saved Beam 4 and Beam 8 compact membership rows show MGC as `removed`,
inactive, with `max_pos=0.6373`, `avg_pos=0.3187`, and rolling average absolute
forecast 1.5492. The saved `sel_note=not_selected_in_target_book` is incomplete.

MGC has 80 Beam 4 trial rows and 141 Beam 8 trial rows in 2026. Every route is
infeasible. The instrument-specific rejection is:

`MGC:max_position_below_minimum max_position=0.6373 min_max_position=1.0000`

Some trial rows append the separate portfolio-margin rejection, but the MGC
gate is present in every MGC route. At the earliest iteration its `max_pos` is
0.7138 and at terminal routes it is 0.6373; both are below 1. The symbol does
not appear in final allocations because that report intentionally contains
only final selected members.

The report builder previously preserved an exact hard-ineligibility reason
only when an incumbent disappeared before candidate selection. MGC remained
in the candidate universe but was infeasible in every selector trial, so it
fell through to the generic removal note. The builder now retains the
instrument-specific hard reason from those trials and emits the incumbent as
`ineligible` with
`sel_note=hard_ineligible:MGC:max_position_below_minimum ...`. A causal
two-review regression test covers this case. Existing saved Parquet remains
unchanged; the corrected note appears on the next generated run.

## Calendar-year out-of-sample slices

Each row uses only that year's saved fixed-capital returns and cash P&L. The
drawdown starts from the preceding year's closing equity so an opening loss is
not reset away. Calmar uses annualized fixed-capital return over that drawdown.

| Year | B4 return | B4 DD | B4 Sharpe | B4 Calmar | B8 return | B8 DD | B8 Sharpe | B8 Calmar | B8 − B4 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1973 | 80.48% | -7.43% | 3.75 | 11.05 | 80.48% | -7.43% | 3.75 | 11.05 | +0.00 pp |
| 1974 | 61.33% | -9.91% | 2.60 | 6.26 | 61.33% | -9.91% | 2.60 | 6.26 | +0.00 pp |
| 1975 | -3.66% | -7.51% | -0.29 | -0.49 | -3.66% | -7.51% | -0.29 | -0.49 | +0.00 pp |
| 1976 | 8.65% | -9.05% | 0.60 | 0.97 | 8.65% | -9.05% | 0.60 | 0.97 | +0.00 pp |
| 1977 | 44.81% | -2.31% | 2.95 | 19.67 | 44.81% | -2.31% | 2.95 | 19.67 | +0.00 pp |
| 1978 | 59.87% | -6.81% | 2.07 | 8.96 | 59.87% | -6.81% | 2.07 | 8.96 | +0.00 pp |
| 1979 | 88.83% | -3.24% | 3.60 | 27.83 | 88.83% | -3.24% | 3.60 | 27.83 | +0.00 pp |
| 1980 | 16.03% | -3.85% | 0.85 | 4.23 | 12.32% | -4.37% | 0.67 | 2.87 | -3.71 pp |
| 1981 | 37.34% | -2.58% | 1.99 | 14.62 | 37.34% | -2.60% | 1.99 | 14.51 | +0.00 pp |
| 1982 | 27.40% | -3.54% | 1.18 | 7.84 | 22.40% | -3.43% | 0.95 | 6.61 | -5.01 pp |
| 1983 | -11.12% | -4.37% | -1.17 | -2.58 | -11.44% | -4.44% | -1.21 | -2.61 | -0.32 pp |
| 1984 | 41.40% | -2.21% | 1.92 | 18.96 | 41.40% | -2.25% | 1.92 | 18.65 | +0.00 pp |
| 1985 | 10.52% | -2.94% | 0.63 | 3.62 | 10.52% | -2.99% | 0.63 | 3.56 | +0.00 pp |
| 1986 | 29.99% | -2.40% | 1.58 | 12.44 | 29.99% | -2.44% | 1.58 | 12.25 | +0.00 pp |
| 1987 | 39.30% | -1.71% | 1.98 | 22.90 | 39.30% | -1.73% | 1.98 | 22.57 | +0.00 pp |
| 1988 | -1.88% | -1.55% | -0.20 | -1.23 | 5.87% | -2.13% | 0.52 | 2.79 | +7.75 pp |
| 1989 | 26.83% | -1.88% | 1.30 | 14.29 | 26.48% | -1.88% | 1.28 | 14.07 | -0.35 pp |
| 1990 | 18.33% | -1.97% | 1.02 | 9.25 | 11.69% | -2.14% | 0.67 | 5.45 | -6.63 pp |
| 1991 | 20.95% | -2.77% | 1.42 | 7.65 | 20.90% | -2.81% | 1.41 | 7.54 | -0.05 pp |
| 1992 | -2.08% | -2.87% | -0.13 | -0.73 | -4.24% | -3.15% | -0.27 | -1.35 | -2.16 pp |
| 1993 | 20.95% | -2.50% | 1.13 | 8.28 | 20.91% | -2.54% | 1.13 | 8.15 | -0.04 pp |
| 1994 | 16.10% | -1.34% | 0.94 | 11.90 | 12.13% | -1.58% | 0.72 | 7.59 | -3.98 pp |
| 1995 | 21.56% | -1.87% | 1.16 | 11.73 | 20.39% | -1.81% | 1.10 | 11.27 | -1.17 pp |
| 1996 | 17.90% | -1.97% | 0.87 | 9.02 | 18.11% | -2.03% | 0.88 | 8.80 | +0.21 pp |
| 1997 | 9.65% | -1.85% | 0.62 | 5.19 | 9.66% | -1.88% | 0.62 | 5.09 | +0.02 pp |
| 1998 | 40.60% | -1.40% | 2.03 | 28.90 | 40.60% | -1.43% | 2.03 | 28.36 | +0.00 pp |
| 1999 | -20.46% | -3.64% | -1.13 | -5.69 | -20.31% | -3.64% | -1.10 | -5.53 | +0.15 pp |
| 2000 | 30.82% | -1.01% | 1.62 | 30.35 | 35.33% | -1.01% | 1.87 | 34.62 | +4.51 pp |
| 2001 | 6.85% | -2.32% | 0.33 | 2.93 | 6.24% | -1.99% | 0.34 | 3.11 | -0.61 pp |
| 2002 | 14.75% | -1.10% | 0.93 | 13.21 | 13.70% | -1.12% | 0.86 | 12.09 | -1.05 pp |
| 2003 | 41.11% | -2.64% | 1.84 | 15.31 | 68.23% | -2.84% | 2.63 | 23.66 | +27.13 pp |
| 2004 | 45.72% | -1.34% | 1.84 | 33.49 | 35.80% | -1.53% | 1.52 | 22.94 | -9.91 pp |
| 2005 | 24.69% | -0.81% | 1.39 | 30.04 | 24.42% | -0.81% | 1.37 | 29.85 | -0.27 pp |
| 2006 | 55.51% | -2.37% | 2.38 | 23.05 | 53.89% | -2.53% | 2.27 | 21.16 | -1.61 pp |
| 2007 | 9.13% | -1.73% | 0.42 | 5.19 | 13.00% | -1.85% | 0.59 | 6.95 | +3.87 pp |
| 2008 | 52.79% | -2.02% | 1.69 | 25.65 | 58.72% | -2.04% | 1.76 | 28.26 | +5.93 pp |
| 2009 | -15.75% | -2.72% | -0.83 | -5.74 | -15.67% | -2.69% | -0.82 | -5.78 | +0.08 pp |
| 2010 | 22.20% | -1.22% | 1.10 | 17.94 | 21.84% | -1.24% | 1.14 | 17.37 | -0.36 pp |
| 2011 | 15.36% | -1.58% | 0.70 | 9.67 | 13.87% | -1.48% | 0.66 | 9.31 | -1.49 pp |
| 2012 | -8.39% | -1.23% | -0.65 | -6.67 | -1.58% | -1.11% | -0.13 | -1.40 | +6.82 pp |
| 2013 | 7.26% | -1.05% | 0.65 | 6.77 | 6.45% | -1.19% | 0.58 | 5.34 | -0.81 pp |
| 2014 | 25.23% | -0.82% | 1.81 | 30.06 | 29.97% | -0.73% | 2.12 | 40.13 | +4.74 pp |
| 2015 | -3.44% | -2.34% | -0.15 | -1.44 | -3.37% | -2.30% | -0.14 | -1.44 | +0.07 pp |
| 2016 | 0.55% | -2.15% | 0.04 | 0.25 | -4.68% | -2.19% | -0.33 | -2.11 | -5.23 pp |
| 2017 | 9.36% | -1.32% | 0.70 | 6.97 | 1.15% | -1.45% | 0.09 | 0.78 | -8.21 pp |
| 2018 | -1.60% | -2.04% | -0.12 | -0.77 | -4.59% | -2.44% | -0.32 | -1.85 | -2.99 pp |
| 2019 | -1.99% | -1.48% | -0.15 | -1.33 | -0.54% | -1.64% | -0.04 | -0.33 | +1.45 pp |
| 2020 | 37.43% | -0.91% | 2.05 | 40.28 | 36.70% | -0.88% | 2.18 | 40.89 | -0.72 pp |
| 2021 | 13.66% | -1.71% | 0.53 | 7.86 | 20.44% | -1.49% | 0.78 | 13.49 | +6.78 pp |
| 2022 | 30.99% | -1.71% | 1.36 | 16.55 | 29.86% | -1.72% | 1.31 | 15.78 | -1.13 pp |
| 2023 | -18.15% | -1.76% | -1.29 | -8.49 | -18.70% | -1.78% | -1.32 | -8.60 | -0.54 pp |
| 2024 | -0.01% | -0.91% | -0.00 | -0.01 | -4.43% | -1.29% | -0.25 | -2.85 | -4.42 pp |
| 2025 | -2.29% | -1.35% | -0.12 | -1.39 | -2.33% | -1.34% | -0.12 | -1.42 | -0.05 pp |
| 2026 | -13.29% | -1.63% | -1.01 | -8.32 | -14.65% | -1.63% | -1.21 | -10.32 | -1.36 pp |

## Recommendation

Keep width 1 as the default. If a beam sensitivity is retained, use Beam 4,
not Beam 8. Beam 4 captures nearly all of the lifetime Sharpe change with less
churn and better recent breadth. Before reconsidering the default, change or
audit beam pruning so a wider beam cannot lose a breadth-equivalent or
hierarchy-equivalent route merely because an intermediate score dominates it,
then rerun a genuinely forward holdout. The current fixed-capital backtest is
useful path evidence, not proof that the wider search generalizes.


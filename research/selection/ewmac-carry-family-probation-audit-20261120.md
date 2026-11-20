# EWMAC and carry family probation audit, November 20, 2026

The eight-calendar-day cutoff added **101,137 carry-family nulls** across the stitched forecast panel. Combined forecast nulls stayed at **1,830**, all before the first valid combined forecast for their instrument. Switching from combined to per-family probation with the cutoff held constant changed **zero** saved forecast values, family weights, or null counts on the 1,178,497 matched rows. It changed the selected books.

Per-family probation excluded AIGCI from 2023 onward, including the 2026 book. It improved common-window net P&L by **$169,877** relative to combined probation with the same cutoff, with higher Sharpe and a deeper percentage drawdown. In partial 2026 the per-family book lost **$4,837**, versus **$2,071** for the combined control.

## Saved runs

| Label | Run ID | Activity scope | Forecast expiry |
|---|---|---|---|
| Pre-cutoff | `selection_run_20261120_145246` | Combined | Disabled |
| Combined control | `selection_run_20261120_150537` | Combined | After 8 calendar days |
| Per-family | `selection_run_20261120_153928` | Per family | After 8 calendar days |

All three use the same November 20 snapshot (12:24:25 ET), rule report (12:34:19 ET), 176 screened candidates, fixed $75,000 sizing capital, 20% risk target, annual reviews, 15% instrument initial-margin limit, and minimum maximum position of 1.0. All use the activity filter with 256 sessions, a .50 probation threshold, a .25 hard threshold and two consecutive weak reviews. The saved EWMAC and carry runs available for this audit all had activity probation enabled. The pre-cutoff run is the nearest baseline before **per-family** probation. The combined control isolates the scope change.

The per-family policy measures each family's trailing mean absolute forecast before blend weighting. Missing values contribute zero to the family mean across the 256 instrument sessions preceding the review. First-review weakness below .50 blocks challengers and permits existing members to remain on probation. A reading below .25 or two consecutive reviews below .50 in the same family triggers hard exclusion. A daily expired component has zero blend weight and its forecast is null. The combiner reweights available components.

## Forecast coverage

Counts are instrument-session rows, including the full candidate forecast panel whether selected or excluded. The two with-cutoff runs match exactly across 56 saved forecast fields, including raw and scaled rules, scalars, FDMs, weights and staleness fields. Each run has 176 symbols, and `symbol,date` is unique. The checked forecast and raw fields have zero nonfinite non-null values.

| Saved forecast field | PST before | PST with cutoff | IB before | IB with cutoff |
|---|---:|---:|---:|---:|
| Panel rows | 1,064,338 | 1,064,338 | 114,159 | 114,159 |
| Combined forecast null | 1,830 | 1,830 | 0 | 0 |
| Trend family null | 7,201 | 7,201 | 0 | 0 |
| Carry family null | 3,984 | 93,492 | 0 | 11,629 |
| Raw carry null | 130,958 | 130,958 | 15,558 | 15,558 |
| Raw roll null | 114,805 | 114,805 | 12,917 | 12,917 |
| Combined forecast exactly zero | 28 | 475 | 8 | 7 |

The cutoff changed 97,443 combined forecast values and turned 89,508 PST and 11,629 IB carry-family values into nulls. The new carry-family nulls represent 8.41% of PST rows and 10.19% of IB rows. On 444 PST rows, carry expired and trend had zero weight, leaving both families unavailable. The combiner emitted a zero target on all 444. No combined null occurred after an instrument's first valid combined forecast. Active cash rows had zero combined forecast nulls in every run. The raw carry and roll fields match on all keys through both changes. A raw carry null can coexist with a carried-forward family forecast while it is still within the eight-day window.

### AIGCI

| Segment | Rows | Carry family null before | Carry family null with cutoff | Combined null in either |
|---|---:|---:|---:|---:|
| PST | 4,253 | 53 | 397 | 10 |
| IB | 666 | 0 | 660 | 0 |
| IB in 2026 through November 20 | 224 | 0 | 218 | 0 |

Of the 666 AIGCI IB rows, 662 have an exact zero raw roll, three have invalid prices and one has a valid nonzero raw carry input. The single fresh observation arrived July 2, 2026, with raw roll 0.2 points. It supplied a carry-family forecast of −0.0697 on six sessions through July 10, at age eight calendar days. Subsequent observations again expired and trend supplied 100% of blend weight on the 660 IB rows with null carry.

The 2026 review used December 24, 2024 through December 31, 2025, all 256 sessions from IB. All 256 carry-family values were null with the cutoff. The per-family review therefore measured carry **0.0000**, trend **0.4714** and combined **0.6081**. Carry failed the .25 hard minimum. With combined probation and the cutoff, AIGCI stayed eligible at 0.6081. Before the cutoff it stayed as a probationary incumbent at 0.3349. The July 2026 observation occurred after the 2026 selection date.

AIGCI was removed in 2019 after two weak trend reviews, reentered in 2020 and stayed through 2022. Carry then hard-failed each review from 2023 through 2026, with scores 0.1936, 0.0076, 0.0000 and 0.0000. In 2026 its net contribution was −$2,083 before the cutoff and +$635 in the combined control. The per-family run made no AIGCI allocation in 2026.

## Membership and coverage

Against the matched combined control, 52 of 54 shared annual reviews changed membership. There were 283 symbol-review slots lost and 207 gained across those dates. Of the 283 losses, 111 were direct carry hard failures and another 50 were repeated carry weakness. Repeated trend weakness accounted for 14. Another 28 were explicitly blocked as weak challengers, and 80 slots either had no family gate marked as the cause or had no matched instrument review row. This comparison follows the books on each path. An incumbent in the control can already be a challenger in the per-family run.

Relative to the pre-cutoff baseline, the per-family book lost AIGCI, M6B, MCL and SCI in 2026 and included BRE, MHG and QG. M6B's per-family carry mean was 0.0469, a direct hard failure. Holding the cutoff fixed gives the following five exclusions from the combined control's 2026 book.

| Symbol | Combined mean absolute forecast | Trend mean | Carry mean | Per-family decision | Control 2026 net |
|---|---:|---:|---:|---|---:|
| AIGCI | 0.6081 | 0.4714 | 0.0000 | Carry hard minimum | $635 |
| BTS | 0.6793 | 0.4727 | 1.0855 | Weak trend challenger | −$52 |
| GBS | 0.7896 | 0.4258 | 1.3934 | Weak trend challenger | $1,646 |
| MCL | 0.4019 | 0.4464 | 0.7643 | Second weak trend review | $1,515 |
| SCI | 0.6451 | 0.3382 | 1.0215 | Second weak trend review | −$1,131 |

BRE, MHG, QG and SGB are present in the per-family 2026 book and absent from the matched control. BRE and SGB carried over from the per-family 2025 book. MHG and QG entered at the 2026 review. MHG passed both family thresholds with trend 0.5159 and carry 0.7863 while its combined mean absolute forecast was 0.3053. The two activity definitions can therefore favor different instruments in either direction.

| Book measure | Pre-cutoff | Combined control | Per-family |
|---|---:|---:|---:|
| Completed annual reviews | 55 | 55 | 54 |
| Mean review book count | 14.25 | 14.49 | 13.28 |
| Min to max review book count | 4–23 | 4–23 | 7–19 |
| Reviews satisfying configured coverage | 5 | 5 | 4 |
| 2026 review candidate count | 126 | 127 | 95 |
| 2026 review hard exclusions | 23 | 18 | 46 |
| 2026 probation flags | 26 | 28 | 34 |
| 2026 selected book count | 17 | 17 | 16 |
| 2026 represented classes / subclasses | 9 / 12 | 9 / 12 | 8 / 11 |
| 2026 top-five weight | 55.55% | 55.55% | 56.25% |
| 2026 effective instrument count | 12.54 | 12.46 | 11.73 |

The 2026 review records unmet coverage due to hard feasibility in all three runs. On the common controls, combined books had 13, 16, 17, 17 and 17 instruments at the 2022 through 2026 reviews. Per-family books had 15, 14, 18, 17 and 16.

## Fixed-cap performance and reconciliation

Both combined-scope runs first have a complete book on January 3, 1972. The per-family run first has one on January 2, 1973. All reach November 20, 2026 with a complete book and have zero active dates with invalid returns or incomplete P&L coverage. There are no duplicate portfolio dates or cash instrument-date keys. The common performance window is January 2, 1973 to November 20, 2026, with 14,059 valid portfolio dates per run. The baseline and control earned $23,746 and $23,804 in 1972, respectively, which are excluded from common-window P&L.

Annualized arithmetic mean and volatility use saved daily fixed-cap returns and 256 sessions. The return denominator is $75,000 throughout. Drawdowns use the saved equity curve and its running peak in the common window, with each run's carry-in equity as the floor on the initial peak. Ending equity includes each run's carry-in. Fixed-cap adjusted Calmar divides annualized arithmetic mean by absolute maximum percentage drawdown.

| Fixed-cap measure | Pre-cutoff | Combined control | Per-family |
|---|---:|---:|---:|
| Ending net equity | $874,870 | $888,340 | $1,034,414 |
| Common-window net P&L | $776,124 | $789,536 | $959,413 |
| Annualized arithmetic mean | 18.84% | 19.17% | 23.29% |
| Annualized volatility | 18.14% | 19.05% | 18.73% |
| Sharpe | 1.04 | 1.01 | 1.24 |
| Maximum drawdown | −8.25% | −7.70% | −8.58% |
| Peak to percentage trough | 1976-07-06 to 1977-09-21 | 1974-02-25 to 1974-04-02 | 1974-02-25 to 1974-04-02 |
| Maximum dollar drawdown | −$37,040 | −$45,390 | −$31,002 |
| Fixed-cap adjusted Calmar | 2.29 | 2.49 | 2.72 |
| Gross P&L | $849,066 | $868,335 | $1,032,896 |
| Trade fees | $41,940 | $48,235 | $42,641 |
| Roll fees | $31,002 | $30,564 | $30,841 |
| Total fees / gross P&L | 8.59% | 9.07% | 7.11% |
| Total fees / net P&L | 9.40% | 9.98% | 7.66% |

Saved cash fees are signed debits. Across all rows, including exit trades on inactive rows, the cash gross-plus-debits-minus-net residual was $3.02, $2.37 and $1.54 respectively. Daily cash sums agree with saved portfolio amounts within $0.0051 for gross or net P&L, $0.02 for trade fees and $0.01 for roll fees. Over the full common window the corresponding cash-minus-portfolio net residuals are +$0.17, +$0.14 and +$0.17. The P&L gain versus the control was mainly gross P&L ($164,560), with another $5,317 of lower total fees.

Across the common window, the largest per-family net contributors were M6B ($63,402), MSF ($63,377) and CAD ($59,049). The largest losses were QO (−$8,988), IBEX (−$4,859) and TSEMOTHR (−$4,609). Agriculture contributed $97,247 more than in the matched control and FX contributed $67,997 more.

The per-family run beat the matched control in 36 of 54 calendar slices. It was ahead in 2023, 2024 and 2025, then behind by $2,767 through November 20, 2026. Membership and sizing changed across the books. The comparison uses the same historical sample on which the policy was evaluated.

### Calendar-year slices

Each row uses the year's saved walk-forward P&L and returns. Return is net dollars divided by $75,000. Within-year drawdown starts from prior year-end equity. Sharpe and fixed-cap adjusted Calmar use annualized statistics from that year's daily returns. Gross and total fees are in dollars. The 2026 row ends November 20.

| Year | Pre-cutoff return / DD / Sharpe / Calmar | Pre-cutoff gross / fees | Combined return / DD / Sharpe / Calmar | Combined gross / fees | Per-family return / DD / Sharpe / Calmar | Per-family gross / fees |
|---:|---:|---:|---:|---:|---:|---:|
| 1973 | 87.6% / -4.8% / 4.18 / 18.00 | $66,637 / $932 | 88.2% / -4.8% / 4.21 / 18.17 | $67,071 / $949 | 87.8% / -6.1% / 4.19 / 14.15 | $66,821 / $966 |
| 1974 | 77.4% / -7.8% / 3.18 / 9.79 | $59,052 / $973 | 76.4% / -7.7% / 3.12 / 9.73 | $58,316 / $1,025 | 76.5% / -8.6% / 3.14 / 8.75 | $58,346 / $994 |
| 1975 | 16.7% / -2.0% / 1.19 / 8.38 | $14,019 / $1,461 | 17.2% / -2.0% / 1.22 / 8.54 | $14,350 / $1,477 | 17.1% / -2.2% / 1.22 / 7.65 | $14,335 / $1,479 |
| 1976 | 14.0% / -6.7% / 0.91 / 2.05 | $11,587 / $1,091 | 13.5% / -6.8% / 0.87 / 1.95 | $11,241 / $1,134 | 13.8% / -7.2% / 0.87 / 1.87 | $11,579 / $1,217 |
| 1977 | 5.1% / -2.1% / 0.57 / 2.45 | $5,139 / $1,300 | 17.7% / -1.8% / 1.56 / 9.45 | $14,896 / $1,597 | 37.3% / -2.0% / 2.70 / 18.34 | $29,555 / $1,565 |
| 1978 | 27.9% / -5.4% / 1.39 / 5.11 | $22,068 / $1,173 | 32.5% / -5.6% / 1.48 / 5.70 | $25,830 / $1,426 | 38.7% / -5.7% / 1.69 / 6.64 | $30,247 / $1,250 |
| 1979 | 22.9% / -3.8% / 1.44 / 5.97 | $18,096 / $934 | 23.0% / -3.7% / 1.40 / 6.08 | $18,164 / $940 | 47.4% / -2.2% / 2.63 / 21.02 | $36,432 / $848 |
| 1980 | 6.1% / -6.2% / 0.33 / 0.96 | $5,665 / $1,101 | 8.3% / -4.8% / 0.44 / 1.68 | $6,943 / $748 | 14.2% / -4.8% / 0.84 / 2.91 | $11,129 / $455 |
| 1981 | 51.1% / -2.7% / 2.33 / 18.43 | $39,380 / $1,049 | 51.7% / -3.0% / 2.27 / 16.65 | $39,862 / $1,086 | 59.0% / -2.7% / 2.42 / 21.79 | $45,145 / $921 |
| 1982 | 30.3% / -3.1% / 1.36 / 9.47 | $23,709 / $984 | 30.1% / -3.2% / 1.31 / 9.34 | $23,541 / $1,003 | 30.8% / -3.2% / 1.34 / 9.49 | $23,963 / $860 |
| 1983 | 1.7% / -3.0% / 0.16 / 0.56 | $1,988 / $710 | 1.4% / -2.9% / 0.13 / 0.46 | $1,752 / $730 | 3.3% / -2.3% / 0.30 / 1.41 | $3,079 / $635 |
| 1984 | 46.6% / -2.7% / 1.93 / 17.16 | $36,024 / $1,085 | 43.1% / -2.8% / 1.77 / 14.99 | $33,591 / $1,256 | 74.4% / -2.6% / 2.67 / 28.55 | $56,742 / $955 |
| 1985 | -2.2% / -4.2% / -0.14 / -0.52 | −$576 / $1,082 | 12.2% / -3.1% / 0.69 / 3.85 | $10,685 / $1,516 | 24.3% / -2.6% / 1.13 / 9.13 | $18,961 / $756 |
| 1986 | 12.7% / -3.2% / 0.84 / 3.93 | $10,663 / $1,134 | 22.0% / -2.3% / 1.23 / 9.51 | $17,825 / $1,354 | 25.8% / -1.8% / 1.69 / 14.26 | $20,060 / $744 |
| 1987 | 7.8% / -3.2% / 0.62 / 2.38 | $6,711 / $839 | 12.8% / -3.0% / 0.83 / 4.23 | $10,643 / $1,055 | 13.9% / -1.6% / 1.08 / 8.59 | $11,128 / $695 |
| 1988 | 3.0% / -1.8% / 0.25 / 1.68 | $3,257 / $989 | 3.5% / -1.7% / 0.27 / 2.00 | $3,777 / $1,144 | 12.3% / -1.7% / 0.91 / 6.90 | $10,111 / $909 |
| 1989 | 11.1% / -1.6% / 0.64 / 6.63 | $9,843 / $1,522 | 16.3% / -1.9% / 0.84 / 8.36 | $14,031 / $1,789 | 13.3% / -1.8% / 0.74 / 7.07 | $11,277 / $1,328 |
| 1990 | 36.1% / -2.0% / 1.91 / 18.07 | $28,243 / $1,140 | 31.7% / -1.8% / 1.57 / 16.91 | $25,215 / $1,407 | 32.6% / -1.4% / 1.70 / 22.54 | $25,504 / $1,023 |
| 1991 | 39.1% / -2.5% / 2.08 / 15.38 | $31,006 / $1,659 | 40.7% / -2.5% / 2.17 / 15.97 | $32,222 / $1,702 | 21.7% / -1.9% / 1.21 / 11.51 | $17,907 / $1,625 |
| 1992 | 13.4% / -3.9% / 0.65 / 3.32 | $11,858 / $1,838 | -6.3% / -4.1% / -0.30 / -1.48 | −$2,603 / $2,085 | -1.7% / -3.2% / -0.09 / -0.52 | $607 / $1,889 |
| 1993 | 13.7% / -3.5% / 0.72 / 3.78 | $11,710 / $1,450 | 13.5% / -3.6% / 0.66 / 3.72 | $12,056 / $1,914 | 16.5% / -3.0% / 0.85 / 5.32 | $13,880 / $1,526 |
| 1994 | 4.3% / -3.2% / 0.30 / 1.32 | $5,106 / $1,850 | -1.1% / -2.9% / -0.08 / -0.38 | $1,027 / $1,861 | 19.5% / -1.6% / 1.53 / 12.34 | $16,044 / $1,425 |
| 1995 | 4.4% / -2.9% / 0.26 / 1.48 | $5,226 / $1,961 | 27.4% / -1.4% / 1.37 / 18.75 | $22,558 / $1,989 | 4.2% / -2.1% / 0.26 / 1.99 | $4,936 / $1,773 |
| 1996 | 26.5% / -2.0% / 1.25 / 12.77 | $21,765 / $1,857 | 32.6% / -2.0% / 1.41 / 16.22 | $26,518 / $2,031 | 24.9% / -1.9% / 1.07 / 13.13 | $20,098 / $1,424 |
| 1997 | 10.8% / -1.6% / 0.67 / 6.73 | $9,694 / $1,598 | 21.6% / -1.2% / 1.17 / 17.25 | $17,840 / $1,629 | 14.5% / -1.3% / 0.79 / 10.77 | $12,455 / $1,590 |
| 1998 | 48.6% / -2.0% / 2.33 / 23.58 | $38,281 / $1,819 | 40.8% / -1.8% / 1.90 / 22.78 | $32,605 / $1,980 | 50.1% / -1.8% / 2.18 / 27.45 | $39,749 / $2,195 |
| 1999 | -9.0% / -2.3% / -0.54 / -3.78 | −$5,373 / $1,366 | -7.9% / -2.2% / -0.46 / -3.54 | −$4,526 / $1,388 | -7.3% / -3.5% / -0.27 / -2.07 | −$3,706 / $1,802 |
| 2000 | 37.4% / -1.4% / 1.93 / 25.44 | $29,220 / $1,191 | 37.4% / -1.5% / 1.91 / 24.43 | $29,276 / $1,215 | 33.5% / -1.1% / 1.72 / 31.01 | $26,745 / $1,629 |
| 2001 | 24.1% / -1.7% / 1.38 / 13.55 | $19,244 / $1,151 | 18.6% / -2.0% / 0.93 / 9.17 | $15,057 / $1,137 | 26.6% / -1.8% / 1.27 / 14.43 | $21,064 / $1,129 |
| 2002 | 22.6% / -1.8% / 1.21 / 12.03 | $18,278 / $1,354 | 24.3% / -1.7% / 1.26 / 14.09 | $19,714 / $1,514 | 26.5% / -1.1% / 1.46 / 22.92 | $21,190 / $1,347 |
| 2003 | 47.2% / -2.7% / 2.14 / 17.39 | $36,784 / $1,377 | 55.0% / -2.7% / 2.29 / 20.21 | $42,651 / $1,369 | 45.6% / -2.4% / 1.95 / 18.94 | $35,554 / $1,348 |
| 2004 | 31.1% / -2.4% / 1.23 / 12.46 | $24,763 / $1,426 | 31.7% / -2.3% / 1.24 / 13.36 | $25,224 / $1,426 | 52.6% / -1.5% / 2.15 / 34.57 | $40,797 / $1,322 |
| 2005 | 34.5% / -0.7% / 1.92 / 47.23 | $27,329 / $1,452 | 35.2% / -0.7% / 1.91 / 50.86 | $27,912 / $1,539 | 30.9% / -0.7% / 1.84 / 43.45 | $24,420 / $1,246 |
| 2006 | 45.6% / -1.9% / 2.38 / 23.81 | $35,236 / $1,021 | 54.1% / -1.7% / 2.67 / 31.12 | $41,681 / $1,096 | 45.5% / -1.8% / 2.32 / 24.98 | $35,189 / $1,034 |
| 2007 | 3.0% / -1.2% / 0.16 / 2.48 | $3,168 / $949 | -0.6% / -1.6% / -0.03 / -0.35 | $601 / $1,023 | 9.7% / -1.3% / 0.52 / 7.41 | $8,244 / $999 |
| 2008 | 46.4% / -1.7% / 1.49 / 26.70 | $35,496 / $680 | 57.3% / -1.4% / 1.69 / 38.67 | $43,643 / $695 | 39.0% / -1.8% / 1.42 / 21.06 | $29,968 / $742 |
| 2009 | -12.8% / -2.8% / -0.65 / -4.41 | −$8,914 / $685 | -10.6% / -2.7% / -0.51 / -3.88 | −$7,248 / $729 | -7.6% / -1.9% / -0.44 / -3.95 | −$5,068 / $656 |
| 2010 | 25.3% / -1.3% / 1.28 / 19.70 | $19,832 / $833 | 24.8% / -1.2% / 1.25 / 20.48 | $19,442 / $853 | 33.1% / -1.0% / 1.85 / 32.50 | $25,637 / $816 |
| 2011 | 21.4% / -1.4% / 0.93 / 14.73 | $16,917 / $860 | 16.9% / -1.3% / 0.69 / 12.63 | $13,674 / $1,005 | 41.9% / -1.0% / 1.96 / 41.02 | $32,398 / $1,006 |
| 2012 | -0.1% / -1.2% / -0.01 / -0.10 | $1,042 / $1,129 | 0.8% / -1.1% / 0.06 / 0.71 | $1,756 / $1,161 | -3.0% / -1.5% / -0.18 / -1.93 | −$1,053 / $1,169 |
| 2013 | 4.3% / -1.3% / 0.35 / 3.33 | $4,415 / $1,175 | 4.5% / -1.1% / 0.36 / 3.93 | $4,610 / $1,206 | 0.4% / -1.7% / 0.03 / 0.21 | $1,689 / $1,407 |
| 2014 | 49.7% / -0.4% / 2.95 / 109.65 | $38,461 / $1,209 | 25.3% / -0.9% / 1.67 / 28.36 | $20,315 / $1,337 | 63.3% / -0.5% / 3.60 / 117.45 | $49,034 / $1,537 |
| 2015 | -0.5% / -1.8% / -0.02 / -0.29 | $1,485 / $1,885 | -5.4% / -1.9% / -0.22 / -2.72 | −$1,931 / $2,088 | -8.7% / -2.0% / -0.35 / -4.25 | −$4,668 / $1,868 |
| 2016 | 0.7% / -2.3% / 0.04 / 0.29 | $2,395 / $1,887 | -1.5% / -2.4% / -0.08 / -0.60 | $1,002 / $2,093 | 2.9% / -1.9% / 0.17 / 1.55 | $4,342 / $2,130 |
| 2017 | 33.5% / -1.1% / 2.20 / 30.68 | $27,774 / $2,618 | 33.9% / -1.1% / 2.20 / 31.41 | $28,327 / $2,923 | 33.1% / -0.8% / 2.28 / 43.03 | $27,834 / $2,975 |
| 2018 | -6.9% / -2.5% / -0.45 / -2.71 | −$2,961 / $2,212 | -13.1% / -2.9% / -0.82 / -4.48 | −$7,526 / $2,272 | -13.0% / -2.0% / -0.97 / -6.52 | −$7,230 / $2,523 |
| 2019 | 2.8% / -1.1% / 0.18 / 2.43 | $4,561 / $2,463 | -2.5% / -1.3% / -0.17 / -1.84 | $344 / $2,228 | 6.4% / -1.5% / 0.38 / 4.30 | $7,131 / $2,323 |
| 2020 | 27.7% / -1.4% / 1.51 / 19.97 | $22,950 / $2,149 | 26.4% / -1.4% / 1.38 / 18.71 | $21,994 / $2,171 | 35.2% / -1.0% / 1.78 / 34.45 | $28,655 / $2,270 |
| 2021 | -3.5% / -2.2% / -0.16 / -1.55 | −$1,155 / $1,445 | 3.0% / -1.8% / 0.13 / 1.65 | $3,896 / $1,638 | 20.2% / -1.2% / 0.87 / 16.95 | $17,070 / $1,884 |
| 2022 | 28.6% / -1.4% / 1.37 / 20.21 | $22,650 / $1,202 | 13.8% / -1.9% / 0.77 / 7.22 | $11,642 / $1,277 | 13.6% / -1.1% / 0.80 / 12.25 | $11,649 / $1,468 |
| 2023 | -2.0% / -1.4% / -0.14 / -1.42 | $240 / $1,738 | -0.4% / -1.3% / -0.03 / -0.31 | $1,610 / $1,908 | 12.4% / -0.7% / 0.81 / 17.55 | $11,046 / $1,778 |
| 2024 | -17.2% / -2.0% / -1.40 / -8.53 | −$11,535 / $1,354 | -22.4% / -2.3% / -1.82 / -9.41 | −$15,108 / $1,663 | -16.0% / -1.7% / -1.31 / -9.46 | −$10,649 / $1,370 |
| 2025 | -11.3% / -1.6% / -1.26 / -6.91 | −$7,313 / $1,179 | -14.1% / -1.8% / -1.22 / -7.58 | −$9,147 / $1,453 | -3.1% / -1.4% / -0.25 / -2.09 | −$1,057 / $1,238 |
| 2026 | -4.7% / -1.0% / -0.32 / -5.00 | −$2,074 / $1,421 | -2.8% / -1.4% / -0.16 / -2.23 | −$505 / $1,566 | -6.4% / -0.9% / -0.51 / -7.74 | −$3,421 / $1,416 |

## Source artifacts

The saved run directories under `/home/dev/data/fin/misc/results/selection_v1/ewmac_carry/` are `selection_run_20261120_145246`, `selection_run_20261120_150537` and `selection_run_20261120_153928`. In each scenario subdirectory, the audit reads the `ewmac_carry_selection_` Parquets for forecasts, cash P&L, portfolio curve, candidates, book, reviews and review instruments. Counts and comparisons come from these saved outputs. No backtests were regenerated.

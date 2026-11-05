# Selection membership-hysteresis audit: November 5, 2026

## Scope and comparability

This update adds the requested Score-1, Rank-25, and Score-2 eligibility sweep:

| Label | Run | Policy |
|---|---|---|
| No hysteresis | `20261103_185405` | Legacy raw annual selection |
| Rank-15 | `20261105_093208` | 15% rank band, `eh0` |
| Rank-25 | `20261105_100357` | 25% rank band, `eh0` |
| Score-1 | `20261105_101615` | 1% score hurdle, `eh0` |
| Score-2 | `20261105_093730` | 2% score hurdle, `eh0` |
| Score-2 + EH10 | `20261105_102258` | 2% score hurdle, 10% eligibility hysteresis |
| Score-2 + EH25 | `20261105_095347` | 2% score hurdle, 25% eligibility hysteresis |

The review artifacts resolve to the intended membership modes and parameter
values. The new canonical EH25 run is set-for-set and curve-for-curve identical
to the earlier `20261104_214756` `eb25` run, confirming that the CLI/report
rename did not change behavior.

All runs use target volatility 20%, instrument margin cap 20%, book margin cap
50%, weight-IDM cap 20%, score threshold 0.85, class count cap 20, subclass
count cap 15, class weight cap 50%, subclass weight cap 25%, annual reviews,
and a 31-day maximum history gap. The common complete-history endpoint is
2026-10-23. Later rows are excluded because history coverage differs after that
date and therefore changes the active book.

## Main result

Rank hysteresis is inactive at both tested widths. Rank-15 and Rank-25 select
the baseline set in all 54 reviews, and their portfolio curves, costs, and
performance are identical to the no-hysteresis run. Raising the band from 15%
to 25% does not create a marginal reduction in turnover; it produces no policy
action at all in this sample.

Score-1 is also not the hoped-for middle ground. It ends at $162.45M versus
$158.20M for Score-2 and $275.53M for baseline. Its full-history CAGR and
Sharpe are 15.34% and 0.904, only slightly above Score-2's 15.29% and 0.899.
Although Score-1 leaves 11 annual transitions unchanged, its accepted switches
are larger: total symbol additions plus removals rise to 523, above both
Score-2's 418 and baseline's 490.

Eligibility hysteresis is the dominant lever. Adding 10% to Score-2 recovers
most full-history performance, while 25% exceeds baseline on the full sample.
That result is path-dependent and not robust in the recent period: from 2024
through 2026-10-23, returns are -2.47% for Score-2 EH0, -3.58% for EH10, and
-4.60% for EH25, versus +0.34% for baseline. The higher full-history EH25
ending capital was accumulated before 2024.

## Parameter response summary

| Policy | Avg book n | Avg YoY Jaccard | Unchanged transitions | Symbol churn | Final/raw Jaccard | Final=raw | Avg final/base Jaccard |
|---|---:|---:|---:|---:|---:|---:|---:|
| No hysteresis | 34.78 | 78.25% | 0 / 53 | 490 | n/a | n/a | 100.00% |
| Rank-15 EH0 | 34.78 | 78.25% | 0 / 53 | 490 | 100.00% | 54 / 54 | 100.00% |
| Rank-25 EH0 | 34.78 | 78.25% | 0 / 53 | 490 | 100.00% | 54 / 54 | 100.00% |
| Score-1 EH0 | 34.52 | 79.42% | 11 / 53 | 523 | 95.22% | 43 / 54 | 88.18% |
| Score-2 EH0 | 35.35 | 82.76% | 14 / 53 | 418 | 92.17% | 40 / 54 | 87.07% |
| Score-2 EH10 | 33.35 | 82.16% | 18 / 53 | 416 | 92.31% | 36 / 54 | 87.31% |
| Score-2 EH25 | 33.98 | 86.72% | 23 / 53 | 281 | 90.85% | 31 / 54 | 86.74% |

An unchanged-transition count is not a turnover measure by itself. Score-1
freezes more complete reviews than baseline, yet the switches it accepts replace
enough symbols to generate more total membership churn. EH10 similarly has more
unchanged transitions than Score-2 EH0 but a slightly lower average YoY
Jaccard, because its changed years move farther.

Score-1 and Score-2 books are identical in 38 of 54 reviews, with 95.40% mean
cross-policy Jaccard. Score-2 EH0 and EH10 are identical in 29 reviews and have
91.20% mean Jaccard; EH0 and EH25 are identical in only 20 reviews and average
90.05%. Eligibility hysteresis changes the equity path, which changes later
position granularity and raw selection as well as incumbent survival.

## Performance by ten-year window

The history is divided into five non-overlapping ten-year windows through 2022,
followed by the 2023 through 2026-10-23 remainder. `Avg book n` is the mean
selected instrument count at reviews. `Avg YoY J` is mean Jaccard similarity
between adjacent books reviewed inside the window. `Avg ann return` is mean
valid daily net return times 256; CAGR is compounded over elapsed calendar
time. Sharpe is average annualized return divided by annualized volatility.

Rank-15 and Rank-25 are omitted as separate rows because every value equals the
baseline exactly.

| Window | Policy | Avg book n | Avg YoY J | Avg ann return | CAGR | Ann vol | Sharpe | Max drawdown | Ending net equity |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1973-1982 | Baseline/rank | 12.70 | 75.24% | 34.42% | 38.04% | 18.48% | 1.862 | -19.75% | $1.88M |
| 1973-1982 | Score-1 EH0 | 10.80 | 86.90% | 32.78% | 36.03% | 17.61% | 1.861 | -19.70% | $1.62M |
| 1973-1982 | Score-2 EH0/EH10/EH25 | 10.80 | 86.90% | 32.78% | 36.03% | 17.61% | 1.861 | -19.70% | $1.62M |
| 1983-1992 | Baseline/rank | 23.00 | 84.84% | 13.25% | 12.48% | 17.33% | 0.765 | -21.73% | $6.09M |
| 1983-1992 | Score-1 EH0 | 21.80 | 87.10% | 13.98% | 13.28% | 17.45% | 0.801 | -25.29% | $5.64M |
| 1983-1992 | Score-2 EH0 | 21.00 | 87.51% | 13.44% | 12.75% | 17.00% | 0.791 | -25.49% | $5.39M |
| 1983-1992 | Score-2 EH10 | 21.00 | 86.88% | 13.64% | 13.07% | 16.55% | 0.825 | -19.23% | $5.54M |
| 1983-1992 | Score-2 EH25 | 21.50 | 85.97% | 13.86% | 13.25% | 16.90% | 0.820 | -21.43% | $5.63M |
| 1993-2002 | Baseline/rank | 28.70 | 81.58% | 18.85% | 19.09% | 17.69% | 1.065 | -25.91% | $34.85M |
| 1993-2002 | Score-1 EH0 | 28.20 | 81.08% | 18.33% | 18.33% | 18.21% | 1.006 | -32.30% | $30.32M |
| 1993-2002 | Score-2 EH0 | 27.50 | 81.16% | 18.88% | 18.89% | 18.67% | 1.011 | -32.28% | $30.34M |
| 1993-2002 | Score-2 EH10 | 27.50 | 81.16% | 18.86% | 18.87% | 18.67% | 1.010 | -32.32% | $31.16M |
| 1993-2002 | Score-2 EH25 | 27.80 | 86.55% | 19.61% | 19.79% | 18.64% | 1.052 | -32.35% | $34.16M |
| 2003-2012 | Baseline/rank | 45.90 | 79.79% | 15.59% | 14.81% | 20.03% | 0.778 | -25.35% | $138.58M |
| 2003-2012 | Score-1 EH0 | 46.70 | 82.12% | 13.92% | 12.91% | 19.86% | 0.701 | -25.56% | $102.10M |
| 2003-2012 | Score-2 EH0 | 46.90 | 82.36% | 13.39% | 12.34% | 19.74% | 0.678 | -25.63% | $97.10M |
| 2003-2012 | Score-2 EH10 | 44.30 | 85.28% | 16.75% | 16.06% | 20.49% | 0.817 | -25.33% | $138.08M |
| 2003-2012 | Score-2 EH25 | 43.50 | 86.31% | 17.80% | 17.29% | 20.57% | 0.865 | -25.69% | $168.23M |
| 2013-2022 | Baseline/rank | 53.60 | 69.10% | 8.73% | 8.29% | 14.29% | 0.611 | -33.51% | $307.14M |
| 2013-2022 | Score-1 EH0 | 53.30 | 61.20% | 6.80% | 6.22% | 13.84% | 0.492 | -34.53% | $186.67M |
| 2013-2022 | Score-2 EH0 | 58.60 | 79.53% | 7.07% | 6.45% | 14.19% | 0.498 | -34.13% | $181.43M |
| 2013-2022 | Score-2 EH10 | 52.00 | 71.14% | 8.39% | 7.97% | 13.88% | 0.604 | -30.03% | $297.08M |
| 2013-2022 | Score-2 EH25 | 56.20 | 91.06% | 8.69% | 8.20% | 14.46% | 0.601 | -25.96% | $370.00M |
| 2023-2026-10-23 | Baseline/rank | 59.75 | 69.73% | -1.77% | -2.81% | 10.96% | -0.162 | -26.31% | $275.53M |
| 2023-2026-10-23 | Score-1 EH0 | 64.00 | 65.71% | -2.42% | -3.59% | 11.11% | -0.218 | -28.02% | $162.45M |
| 2023-2026-10-23 | Score-2 EH0 | 65.25 | 68.40% | -2.38% | -3.54% | 11.13% | -0.213 | -29.10% | $158.20M |
| 2023-2026-10-23 | Score-2 EH10 | 61.25 | 71.29% | -2.56% | -3.72% | 10.95% | -0.234 | -29.75% | $257.13M |
| 2023-2026-10-23 | Score-2 EH25 | 59.25 | 70.96% | -1.60% | -2.63% | 11.12% | -0.144 | -25.72% | $334.26M |
| Full history | Baseline/rank | 34.78 | 78.25% | 16.40% | 16.48% | 17.21% | 0.953 | -39.56% | $275.53M |
| Full history | Score-1 EH0 | 34.52 | 79.42% | 15.42% | 15.34% | 17.07% | 0.904 | -41.80% | $162.45M |
| Full history | Score-2 EH0 | 35.35 | 82.76% | 15.38% | 15.29% | 17.10% | 0.899 | -42.73% | $158.20M |
| Full history | Score-2 EH10 | 33.35 | 82.16% | 16.27% | 16.33% | 17.12% | 0.950 | -42.37% | $257.13M |
| Full history | Score-2 EH25 | 33.98 | 86.72% | 16.78% | 16.90% | 17.30% | 0.970 | -39.01% | $334.26M |

Score-1 and Score-2 EH0 both lose most of their relative ground during
2003-2012 and 2013-2022. EH10 largely repairs those two windows. EH25 is best
over 2003-2012 and produces the shallowest 2013-2022 drawdown, but this is an
in-sample path result rather than monotonic evidence that wider is better. All
policies have negative average return and Sharpe from 2023 onward.

## Recent calendar performance

| Year | Baseline/rank | Score-1 EH0 | Score-2 EH0 | Score-2 EH10 | Score-2 EH25 |
|---:|---:|---:|---:|---:|---:|
| 2023 | -10.59% | -10.46% | -10.60% | -10.24% | -5.30% |
| 2024 | -7.82% | -12.78% | -11.37% | -12.74% | -12.21% |
| 2025 | +0.98% | +2.26% | +1.09% | +1.33% | +1.30% |
| 2026 through Oct 23 | +7.80% | +8.96% | +8.86% | +9.05% | +7.28% |
| 2024 through Oct 23 compounded | +0.34% | -2.81% | -2.47% | -3.58% | -4.60% |

EH25's better 2023 explains its lead within the 2023-onward window. From the
start of 2024, every score variant trails baseline, and increasing eligibility
hysteresis does not improve the result. Score-1 has the worst 2024 loss; its
stronger 2025-2026 recovery is insufficient to offset it.

## Trading-cost effect

Cost percentages below are sums of daily percentage-point charges, not a
compounded performance attribution.

| Policy | Review contracts | Review trade cost | All-date trade cost | Roll cost | Total cost |
|---|---:|---:|---:|---:|---:|
| Baseline/rank | 81,863 | 1.647% | 45.755% | 23.845% | 69.600% |
| Score-1 EH0 | 73,730 | 1.445% | 46.796% | 24.169% | 70.965% |
| Score-2 EH0 | 71,257 | 1.450% | 47.713% | 25.071% | 72.784% |
| Score-2 EH10 | 58,839 | 1.270% | 45.484% | 23.511% | 68.995% |
| Score-2 EH25 | 45,333 | 1.150% | 45.750% | 23.903% | 69.653% |

Every score rule lowers review-date contracts and review-date cost. Score-1
reduces review contracts by 9.9% despite increasing symbol churn, demonstrating
that set churn and contract turnover are different measures. Score-2 EH25 cuts
review contracts by 44.6% and review cost by 30.1%, but its all-date total cost
is essentially baseline. The retained book still trades and rolls every day;
review savings are not a direct forecast of total cost or net performance.

## Diversification behavior

The concentrated early score book remains under every tested score and
eligibility setting from 1978 through 1983. Score-1 releases the incumbent in
1984 and expands to the baseline's 20-symbol book; Score-2 remains at 12 until
1985. A narrower hurdle shortens, but does not eliminate, the early stasis.
Eligibility hysteresis cannot repair it because the Score-2 eligibility paths
do not begin to differ until 1985.

Score stasis can also preserve diversification. In 2017 Score-2 EH0 retains a
large incumbent book instead of accepting a raw proposal that collapses to
seven instruments. Score-1's different equity path makes its incumbent fail
the hard portfolio-margin check, so it is forced into that seven-symbol raw
book. This single review accounts for much of Score-1's larger set churn and is
a concrete example of capital-path amplification. Instrument count alone still
does not explain performance: the rule alternates between blocking useful
expansion and blocking harmful collapse, with the result dominated by path.

## Conclusions and next diagnostic

1. Rank mode has no demonstrated value at 15% or 25%. Keep it off by default;
   testing 30% is only useful as a boundary/implementation diagnostic, not as
   evidence that 25% is nearly effective.
2. Score-1 is not a robust compromise. It slightly improves full-history
   performance over Score-2 EH0 but raises symbol churn above baseline and has
   the worst 2024 result.
3. Score-2 EH10 is the least extreme score configuration: full-history CAGR and
   Sharpe nearly match baseline and total cost is slightly lower. It still has
   a deeper maximum drawdown and worse 2024-onward return, so the run does not
   justify enabling it.
4. Score-2 EH25 has the best full-history result and strongest membership
   stability, but its advantage is pre-2024 and highly path-dependent. Treat it
   as a sensitivity result, not a selected parameter.

Because dynamic capital feeds back into feasibility and later raw books, the
next useful experiment is a frozen-equity decision replay. Apply each policy to
the same review equity path and compare decisions and costs; this will separate
the hysteresis rule from capital-path amplification. A diversification-aware
override is also preferable to lowering the score hurdle: do not retain an
incumbent when it violates a minimum class/subclass breadth or when the
challenger materially repairs concentration.

## Annual books and Jaccard similarity

Instrument order is the selector's reported order; Jaccard treats each book as
an unordered set. `S/base` and `R/base` compare each hysteresis book with the
no-hysteresis book in that year. `S/raw` and `R/raw` compare the final book with
that run's own unbuffered proposal, isolating whether membership hysteresis
changed the set. `S` is Score-2 EH0. `R` is Rank-15 EH0; Rank-25 is omitted
because it is identical to Rank-15 in every year. The parameter-response table
above summarizes Score-1, EH10, and EH25 without duplicating five long symbol
lists per row.

| Year | No-hysteresis instruments | Score-2 instruments | S/base | S/raw | Rank-15 instruments | R/base | R/raw |
|---:|---|---|---:|---:|---|---:|---:|
| 1973 | `SB,ZM,ZL,CT,YC,YW` | `SB,ZM,ZL,CT,YC,YW` | 100.0% | 100.0% | `SB,ZM,ZL,CT,YC,YW` | 100.0% | 100.0% |
| 1974 | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 100.0% | 100.0% | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 100.0% | 100.0% |
| 1975 | `CAD,ZC,CHF,LE,CT,ZL,SB,ZM,YW,CC` | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 72.7% | 72.7% | `CAD,ZC,CHF,LE,CT,ZL,SB,ZM,YW,CC` | 100.0% | 100.0% |
| 1976 | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC` | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC` | 100.0% | 100.0% | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC` | 100.0% | 100.0% |
| 1977 | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | 100.0% | 100.0% | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | 100.0% | 100.0% |
| 1978 | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 100.0% | 100.0% | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 100.0% | 100.0% |
| 1979 | `J7,LE,SB,CAD,ZW,M6B,ZM,HE,CT,ZC,MSF,GF,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 73.3% | 85.7% | `J7,LE,SB,CAD,ZW,M6B,ZM,HE,CT,ZC,MSF,GF,ZL,CC` | 100.0% | 100.0% |
| 1980 | `J7,SI,HE,ZB,ZC,CAD,SB,CHF,CT,ZM,GC,GF,CC,ZW,LE,ZL,M6B` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 70.6% | 70.6% | `J7,SI,HE,ZB,ZC,CAD,SB,CHF,CT,ZM,GC,GF,CC,ZW,LE,ZL,M6B` | 100.0% | 100.0% |
| 1981 | `J7,SI,HE,ZB,CT,CAD,MGC,ZC,CHF,SB,LE,M6B,ZO,ZW,GF,ZM,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 66.7% | 66.7% | `J7,SI,HE,ZB,CT,CAD,MGC,ZC,CHF,SB,LE,M6B,ZO,ZW,GF,ZM,ZL,CC` | 100.0% | 100.0% |
| 1982 | `J7,ZW,SI,ZB,ZC,CAD,HE,CT,GC,SB,LE,CC,GF,M6B,ZM,ZL,CHF` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 70.6% | 70.6% | `J7,ZW,SI,ZB,ZC,CAD,HE,CT,GC,SB,LE,CC,GF,M6B,ZM,ZL,CHF` | 100.0% | 100.0% |
| 1983 | `J7,ZB,HE,SI,CT,CAD,GC,SB,M6B,ZC,ZW,CC,ZM,ZL,YK,LE,GF,CHF` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 66.7% | 66.7% | `J7,ZB,HE,SI,CT,CAD,GC,SB,M6B,ZC,ZW,CC,ZM,ZL,YK,LE,GF,CHF` | 100.0% | 100.0% |
| 1984 | `M6B,ZN,ZM,SB,CHF,J7,HE,CAD,CT,ZW,ZL,GF,ZC,YK,CC,LE,GC,SI,ES,ZB` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 60.0% | 60.0% | `M6B,ZN,ZM,SB,CHF,J7,HE,CAD,CT,ZW,ZL,GF,ZC,YK,CC,LE,GC,SI,ES,ZB` | 100.0% | 100.0% |
| 1985 | `R,M6B,HE,ZC,ZN,MCD,CT,CHF,SB,LE,ZW,CC,ZL,ZM,GF,YK,ZB,GC,SI,ES,J7` | `R,M6B,HE,ZC,ZN,MCD,CT,CHF,SB,LE,ZW,CC,ZL,ZM,GF,YK,ZB,GC,SI,ES,J7` | 100.0% | 100.0% | `R,M6B,HE,ZC,ZN,MCD,CT,CHF,SB,LE,ZW,CC,ZL,ZM,GF,YK,ZB,GC,SI,ES,J7` | 100.0% | 100.0% |
| 1986 | `R,SI,ZL,Z,M6B,LE,ZN,ES,GC,ZW,HE,GF,ZC,CHF,MCD,SB,CC,CT,YK,ZM,SOFR3,ZB` | `R,SI,ZL,Z,M6B,LE,ZN,MCD,ES,GC,SB,CC,ZW,GF,HE,CT,ZC,YK,ZM,CHF,J7,SOFR3,ZB` | 95.7% | 100.0% | `R,SI,ZL,Z,M6B,LE,ZN,ES,GC,ZW,HE,GF,ZC,CHF,MCD,SB,CC,CT,YK,ZM,SOFR3,ZB` | 100.0% | 100.0% |
| 1987 | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 100.0% | 100.0% | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 100.0% | 100.0% |
| 1988 | `R,ZM,DX,ES,RB,CC,Z,ZN,LE,CT,CHF,J7,ZW,M6B,MCD,SB,ZC,ZL,ZS,GC,SI,GF,HE,SOFR3,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 92.0% | 92.0% | `R,ZM,DX,ES,RB,CC,Z,ZN,LE,CT,CHF,J7,ZW,M6B,MCD,SB,ZC,ZL,ZS,GC,SI,GF,HE,SOFR3,ZB` | 100.0% | 100.0% |
| 1989 | `DX,ZS,R,GC,M6A,ES,RB,LE,Z,SOFR3,CAD,SB,SI,CT,ZL,ZW,ZM,ZC,ZO,GF,HE,CC,J7,CHF,M6B,ZN,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 78.6% | 78.6% | `DX,ZS,R,GC,M6A,ES,RB,LE,Z,SOFR3,CAD,SB,SI,CT,ZL,ZW,ZM,ZC,ZO,GF,HE,CC,J7,CHF,M6B,ZN,ZB` | 100.0% | 100.0% |
| 1990 | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | 100.0% | 100.0% | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | 100.0% | 100.0% |
| 1991 | `W,DX,R,GC,MCL,ZM,M6A,ZF,SI,RB,ZW,CT,ES,J7,MCD,ZC,GF,HE,LE,ZS,ZL,M6B,CHF,ZN,ZB` | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | 81.5% | 81.5% | `W,DX,R,GC,MCL,ZM,M6A,ZF,SI,RB,ZW,CT,ES,J7,MCD,ZC,GF,HE,LE,ZS,ZL,M6B,CHF,ZN,ZB` | 100.0% | 100.0% |
| 1992 | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,GF,HE,ZS,ZW,ZM,ZC,ZL,LE,M6B,MCD,J7,CHF,ZN,ZB` | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,GF,HE,ZS,ZW,ZM,ZC,ZL,LE,M6B,MCD,J7,CHF,ZN,ZB` | 100.0% | 100.0% | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,GF,HE,ZS,ZW,ZM,ZC,ZL,LE,M6B,MCD,J7,CHF,ZN,ZB` | 100.0% | 100.0% |
| 1993 | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,HE,ZW,CT,CHF,MCD,J7,RS,ZM,GF,ZC,ZL,DX,Z,ZN,ZB,GC` | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,HE,ZW,CHF,MCD,CT,J7,RS,ZM,GF,ZC,ZL,DX,Z,ZN,ZB,GC,QG` | 96.4% | 100.0% | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,HE,ZW,CT,CHF,MCD,J7,RS,ZM,GF,ZC,ZL,DX,Z,ZN,ZB,GC` | 100.0% | 100.0% |
| 1994 | `DX,HSI,W,RB,R,SI,M6A,ZF,ZC,CL,ES,ZR,CT,ZW,ZL,RS,ZS,ZM,LE,HE,GF,M6B,MCD,J7,CHF,Z,ZN,ZB` | `DX,HSI,W,RB,R,SI,M6A,ZF,ZC,CL,QG,ES,ZR,CT,ZW,ZL,RS,ZS,ZM,M6B,MCD,GC,ZN,HE,LE,GF,ZB` | 83.3% | 100.0% | `DX,HSI,W,RB,R,SI,M6A,ZF,ZC,CL,ES,ZR,CT,ZW,ZL,RS,ZS,ZM,LE,HE,GF,M6B,MCD,J7,CHF,Z,ZN,ZB` | 100.0% | 100.0% |
| 1995 | `R,W,HSI,M6B,CL,GC,ZF,ZC,ES,SI,CHF,M6A,LE,HE,ZW,ZM,CT,ZS,ZL,RS,ZR,GF,DX,MCD,J7,ZN,ZB,Z` | `R,W,HSI,M6B,CL,GC,M6A,ZF,ZC,ES,SI,LE,HE,ZW,ZM,CT,DX,ZS,ZL,RS,ZR,GF,CHF,MCD,J7,ZN,ZB,Z` | 100.0% | 100.0% | `R,W,HSI,M6B,CL,GC,ZF,ZC,ES,SI,CHF,M6A,LE,HE,ZW,ZM,CT,ZS,ZL,RS,ZR,GF,DX,MCD,J7,ZN,ZB,Z` | 100.0% | 100.0% |
| 1996 | `R,W,DX,HSI,CL,GC,ZW,ES,ZF,CT,RB,CHF,M6A,ZS,HE,LE,ZC,ZM,RS,ZR,ZL,GF,M6B,J7,MCD,ZN,Z,ZB` | `R,W,DX,HSI,CL,GC,ZW,M6A,ES,ZF,HE,RB,QG,SI,CT,LE,ZS,ZC,ZM,RS,ZL,GF,M6B,ZN,ZB` | 76.7% | 100.0% | `R,W,DX,HSI,CL,GC,ZW,ES,ZF,CT,RB,CHF,M6A,ZS,HE,LE,ZC,ZM,RS,ZR,ZL,GF,M6B,J7,MCD,ZN,Z,ZB` | 100.0% | 100.0% |
| 1997 | `R,W,HSI,DX,HG,CL,CT,ZF,GC,M6A,ES,KE,M6B,HE,SI,GF,ZM,ZL,ZW,ZS,ZC,RS,LE,BRE,CHF,ZB,Z,ZN` | `R,W,HSI,DX,HG,CL,QG,CT,ZF,SI,M6A,ES,KE,M6B,GC,HE,GF,ZM,ZL,ZS,ZW,ZC,RS,LE,BRE,CHF,ZN,ZB` | 93.1% | 100.0% | `R,W,HSI,DX,HG,CL,CT,ZF,GC,M6A,ES,KE,M6B,HE,SI,GF,ZM,ZL,ZW,ZS,ZC,RS,LE,BRE,CHF,ZB,Z,ZN` | 100.0% | 100.0% |
| 1998 | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 100.0% | 100.0% | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 100.0% | 100.0% |
| 1999 | `Z,HSI,DX,BRE,W,LE,RS,ZW,HE,CT,DA,ZM,ZL,ZR,GF,ZC,KE,ZS,CHF,ES,PA,HG,GC,SI,ZF,R,RB,ZN,ZB` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 73.3% | 73.3% | `Z,HSI,DX,BRE,W,LE,RS,ZW,HE,CT,DA,ZM,ZL,ZR,GF,ZC,KE,ZS,CHF,ES,PA,HG,GC,SI,ZF,R,RB,ZN,ZB` | 100.0% | 100.0% |
| 2000 | `R,BRE,W,RB,HSI,GC,PA,ES,ZW,ZF,CHF,GF,SI,HE,ZM,RS,KE,LE,ZL,ZC,Z,ZS,CT,ZN,ZB,HG,DX` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 78.6% | 73.3% | `R,BRE,W,RB,HSI,GC,PA,ES,ZW,ZF,CHF,GF,SI,HE,ZM,RS,KE,LE,ZL,ZC,Z,ZS,CT,ZN,ZB,HG,DX` | 100.0% | 100.0% |
| 2001 | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZC,GC,ZL,ZS,CT,ZB,GF,SI,ZF,LE,I,DX,M6A` | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZC,ZS,GC,CT,ZL,ZB,GF,SI,ZF,LE,I,DX,M6A,M6B` | 96.8% | 100.0% | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZC,GC,ZL,ZS,CT,ZB,GF,SI,ZF,LE,I,DX,M6A` | 100.0% | 100.0% |
| 2002 | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,Z,ZW,ZL,KE,ZO,LE,CT,ES,DX,M6A,CHF,M6B,DAX,ZT,ZN,ZF,HG,I` | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,Z,ZW,ZL,KE,ZO,LE,M6A,ES,CT,DX,M6B,CHF,DAX,ZT,ZN,ZF,HG,I` | 100.0% | 100.0% | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,Z,ZW,ZL,KE,ZO,LE,CT,ES,DX,M6A,CHF,M6B,DAX,ZT,ZN,ZF,HG,I` | 100.0% | 100.0% |
| 2003 | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ES,CC,Z,DAX,ZT,DX,ZB,ZN,ZF,I,IBXXIBHY,EUR,RF,CHF` | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ES,CC,Z,DAX,EUR,ZB,ZT,ZN,RF,ZF,I,IBXXIBHY,DX,CHF` | 100.0% | 100.0% | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ES,CC,Z,DAX,ZT,DX,ZB,ZN,ZF,I,IBXXIBHY,EUR,RF,CHF` | 100.0% | 100.0% |
| 2004 | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZR,SI,ZM,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,EMD,ZB,ES,ZF,ZN,DAX,DX,EUR,I,RF` | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ES,CC,Z,DAX,EUR,ZB,ZT,ZN,RF,ZF,I,IBXXIBHY,DX,CHF` | 81.4% | 83.7% | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZR,SI,ZM,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,EMD,ZB,ES,ZF,ZN,DAX,DX,EUR,I,RF` | 100.0% | 100.0% |
| 2005 | `NZD,JGB,NQ,HE,CL,GF,BRE,HSI,R,RY,RS,PA,SI,IBEX35,CT,W,SSG,CC,LE,Z,ZS,ZW,ZR,ZM,KE,ZC,ZL,DA,YM,ES,DAX,EMD,CHF,ZT,EUR,DX,M6A,RF,ZB,NOK,ZF,SEK,M6B,ZN,GC,I` | `NZD,JGB,NQ,HE,CL,GF,BRE,HSI,R,RY,RS,PA,SI,RF,IBEX35,CT,W,SSG,CC,LE,Z,ZS,ZM,ZR,ZW,ZL,KE,ZC,NOK,DA,DAX,ZT,CHF,M6A,EUR,YM,M6B,DX,EMD,ZB,ES,ZN,SEK,MCD,ZF,GC` | 95.7% | 100.0% | `NZD,JGB,NQ,HE,CL,GF,BRE,HSI,R,RY,RS,PA,SI,IBEX35,CT,W,SSG,CC,LE,Z,ZS,ZW,ZR,ZM,KE,ZC,ZL,DA,YM,ES,DAX,EMD,CHF,ZT,EUR,DX,M6A,RF,ZB,NOK,ZF,SEK,M6B,ZN,GC,I` | 100.0% | 100.0% |
| 2006 | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,GC,CT,ZC,YM,ZT,HE,W,HSI,IBEX35,RS,LE,KE,ZM,ZL,ZW,CHF,ZB,ZR,EUR,DAX,DX,ZF,ZO,ZN,EMD,I,RF` | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,GC,CT,ZC,YM,ZT,W,HE,HSI,IBEX35,RS,LE,KE,ZM,ZL,ZW,CHF,ZB,DAX,EMD,EUR,ZO,DX,ZF,ZN,RF,M6A,I,ZR` | 97.6% | 100.0% | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,GC,CT,ZC,YM,ZT,HE,W,HSI,IBEX35,RS,LE,KE,ZM,ZL,ZW,CHF,ZB,ZR,EUR,DAX,DX,ZF,ZO,ZN,EMD,I,RF` | 100.0% | 100.0% |
| 2007 | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,CT,SSG,PA,YM,CHF,RY,R,ZM,ZW,HSI,RS,ZS,KE,LE,HE,SX7E,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,E7,DX,NOK,I,SEK` | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,RY,CT,SSG,PA,YM,R,ZM,RS,HSI,ZW,ZS,KE,SX7E,CHF,LE,HE,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,RF,I,SEK` | 91.5% | 100.0% | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,CT,SSG,PA,YM,CHF,RY,R,ZM,ZW,HSI,RS,ZS,KE,LE,HE,SX7E,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,E7,DX,NOK,I,SEK` | 100.0% | 100.0% |
| 2008 | `IBEX35,ZB,KE,RB,NZD,HG,GF,JGB,NQ,CHF,PA,HSI,CT,BRE,ZT,SI,RY,SX7E,SSG,LE,NOK,RF,SB,ZC,HE,SEK,ZW,ZM,RS,ZR,DA,ZS,EUR,M6A,R,ZF,YM,DX,Z,ZN,DAX,ZL,GBL,GC,DJ600,I,Y,EMD` | `IBEX35,ZB,KE,RB,NZD,HG,GF,JGB,NQ,CHF,PA,HSI,CT,BRE,ZT,SI,RY,SX7E,SSG,LE,NOK,RF,SB,ZC,HE,SEK,ZW,ZM,RS,ZR,M6A,EUR,MCD,M6B,YM,R,ZF,ZS,DX,DA,ES,EMD,DAX,ZN,Z,GBL,GC,ZL,I,Y,DJ600` | 94.1% | 100.0% | `IBEX35,ZB,KE,RB,NZD,HG,GF,JGB,NQ,CHF,PA,HSI,CT,BRE,ZT,SI,RY,SX7E,SSG,LE,NOK,RF,SB,ZC,HE,SEK,ZW,ZM,RS,ZR,DA,ZS,EUR,M6A,R,ZF,YM,DX,Z,ZN,DAX,ZL,GBL,GC,DJ600,I,Y,EMD` | 100.0% | 100.0% |
| 2009 | `IBEX35,ZM,JGB,HG,BRE,RB,CHF,NQ,SB,GC,GBL,HSI,KE,C,SSG,PA,SX7E,SI,ZN,ZW,RS,ZR,ZC,EMD,YM,ES,HE,GF,DA,LE,ZO,ZS,NZD,ZL,RY,Z,DAX,DJ600,CT,KC,RF,EUR,SEK,NOK,M6A,DX,R,ZT,ZB,ZF,GBS,I,IBXXIBHY` | `IBEX35,ZM,JGB,HG,BRE,RB,CHF,NQ,SB,GC,GBL,HSI,RF,KE,C,SSG,PA,SX7E,SI,ZN,ZW,RS,ZC,ZS,HE,ZR,GF,LE,ZL,NZD,DA,ZO,Z,DAX,CT,EMD,YM,DJ600,KC,ES,R,ZT,ZB,ZF,GBS,RY,EUR,SEK,NOK,M6A,M6B,MCD,DX,I,IBXXIBHY` | 96.4% | 100.0% | `IBEX35,ZM,JGB,HG,BRE,RB,CHF,NQ,SB,GC,GBL,HSI,KE,C,SSG,PA,SX7E,SI,ZN,ZW,RS,ZR,ZC,EMD,YM,ES,HE,GF,DA,LE,ZO,ZS,NZD,ZL,RY,Z,DAX,DJ600,CT,KC,RF,EUR,SEK,NOK,M6A,DX,R,ZT,ZB,ZF,GBS,I,IBXXIBHY` | 100.0% | 100.0% |
| 2010 | `IBEX35,JGB,ZM,CHF,RB,SB,GBM,NQ,BRE,HE,HSI,KE,ZT,SSG,CL,EMD,C,RY,ZW,ZC,RS,ZB,GBS,D,SX7E,YM,ZS,ZN,R,ZF,Z,ES,GBL,DAX,DJ600,ZL,NZD,ZR,DX,CT,GF,KC,LE,DA,M6E,NOK,SEK,I,SI,RF,M6A` | `IBEX35,JGB,ZM,CHF,RB,SB,GBM,NQ,BRE,HE,HSI,KE,ZT,SSG,CL,EMD,C,RY,ZW,ZC,RS,ZB,GBS,D,SX7E,YM,ZS,ZN,R,ZF,Z,ES,GBL,DAX,DJ600,ZL,NZD,ZR,EUR,ZO,DX,NOK,CT,RF,KC,SEK,M6A,LE,SI,GF,DA,I,IBXXIBHY` | 92.6% | 100.0% | `IBEX35,JGB,ZM,CHF,RB,SB,GBM,NQ,BRE,HE,HSI,KE,ZT,SSG,CL,EMD,C,RY,ZW,ZC,RS,ZB,GBS,D,SX7E,YM,ZS,ZN,R,ZF,Z,ES,GBL,DAX,DJ600,ZL,NZD,ZR,DX,CT,GF,KC,LE,DA,M6E,NOK,SEK,I,SI,RF,M6A` | 100.0% | 100.0% |
| 2011 | `IBEX35,SI,ZF,HE,JGB,ZW,NQ,SB,GBS,C,ZM,PA,EMD,SX7E,KE,D,GF,ZC,LE,YM,ES,HSI,RS,ZS,ZR,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,GBM,ZB,R,ZN,BRE,CHF,M6E,NZD,RY,DX,RB,RF,ZO,GC,SEK` | `IBEX35,SI,ZF,HE,JGB,C,NQ,GBS,SB,PA,KE,SX7E,ZM,EMD,D,GF,ZW,ZC,ZR,LE,YM,ES,HSI,RS,ZS,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,RY,BRE,E7,ZB,R,GBM,ZN,DX,NZD,CHF,I,RF,ZO,RB,GC,SEK,NOK,HG` | 90.6% | 100.0% | `IBEX35,SI,ZF,HE,JGB,ZW,NQ,SB,GBS,C,ZM,PA,EMD,SX7E,KE,D,GF,ZC,LE,YM,ES,HSI,RS,ZS,ZR,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,GBM,ZB,R,ZN,BRE,CHF,M6E,NZD,RY,DX,RB,RF,ZO,GC,SEK` | 100.0% | 100.0% |
| 2012 | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,ZM,HG,GF,MNTPX,BTP,ZW,KE,ZF,BRE,E7,W,GBS,RY,ZC,RS,SX7E,YM,C,D,ZS,KC,CT,ZL,LE,R,UB,ZN,GBL,ZB,ZR,GBM,SSG,DX,Z,HSI,DAX,DJ600,RB,GC,NQ,ZO` | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,GF,HG,MNTPX,BTP,ZM,ZW,KE,ZF,BRE,E7,W,GBS,RY,ZC,RS,SX7E,YM,C,D,ZS,KC,CT,ZO,ZL,UB,R,ZN,LE,GBL,ZB,GBM,SSG,ZR,DX,Z,HSI,DAX,RB,GC,NQ,EMD,DJ600` | 98.0% | 100.0% | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,ZM,HG,GF,MNTPX,BTP,ZW,KE,ZF,BRE,E7,W,GBS,RY,ZC,RS,SX7E,YM,C,D,ZS,KC,CT,ZL,LE,R,UB,ZN,GBL,ZB,ZR,GBM,SSG,DX,Z,HSI,DAX,DJ600,RB,GC,NQ,ZO` | 100.0% | 100.0% |
| 2013 | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,GC,BRE,MNTPX,CHF,MXEA,GF,BTS,GBL,N225M,ZW,SX7E,KE,RS,ZC,ZS,ZF,RY,NQ,YM,UB,KC,SB,D,E7,LE,ZL,CT,SSG,CC,HSI,DAX,DJ600,Z,R,GBS,ZN,GBM,ZB,ES,EMD,DX,RB,PA,ZO,DA` | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,RY,GC,MXEA,KC,YM,BRE,GF,CHF,ZW,SB,BTS,UB,KE,D,GBL,RS,LE,ZC,ZS,CT,DA,ZF,ZL,MNTPX,ZO,SX7E,E7,ZR,N225M,SSG,CC,Z,HSI,DJ600,DAX,R,GBS,ZN,GBM,ZB,DX,RB,NQ,PA,CSC` | 92.9% | 100.0% | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,GC,BRE,MNTPX,CHF,MXEA,GF,BTS,GBL,N225M,ZW,SX7E,KE,RS,ZC,ZS,ZF,RY,NQ,YM,UB,KC,SB,D,E7,LE,ZL,CT,SSG,CC,HSI,DAX,DJ600,Z,R,GBS,ZN,GBM,ZB,ES,EMD,DX,RB,PA,ZO,DA` | 100.0% | 100.0% |
| 2014 | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,KE,PA,Z,M6E,RY,RS,SX7E,ZC,DJ600,MNTPX,DAX,ES,YM,NQ,HSI,DJSD,N225M,SSG,SB,D,GF,HE,C,KC,ZS,CT,LE,ZL,EMD,DX,UB,ZT,GBL,ZF,BTP,R,GBM,ZN,GBS,ZB,BTS` | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,RY,KE,PA,Z,SX7E,RS,E7,ZC,DJ600,MNTPX,DAX,ES,YM,NQ,HSI,DJSD,N225M,SSG,Y,SB,D,KC,CT,ZS,ZL,EMD,HE,GF,C,DX,ZF,LE,UB,ZT,GBM,ZB,BTP,GBS,GBL,ZN,R` | 92.9% | 100.0% | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,KE,PA,Z,M6E,RY,RS,SX7E,ZC,DJ600,MNTPX,DAX,ES,YM,NQ,HSI,DJSD,N225M,SSG,SB,D,GF,HE,C,KC,ZS,CT,LE,ZL,EMD,DX,UB,ZT,GBL,ZF,BTP,R,GBM,ZN,GBS,ZB,BTS` | 100.0% | 100.0% |
| 2015 | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,SSG,DJSD,RS,ZC,BTS,GBL,ZS,EMD,M6E,ZL,R,ZF,BTP,GBM,ZN,ZB,GBS,DX,SB,D,CT,KC,CC` | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,N225M,MXEA,HE,BRE,CHF,KE,XINA50,ZT,NZD,UB,GF,ZW,Z,SX7E,BTS,MNTPX,DAX,HSI,ZC,RS,E7,ZS,DJ600,IXE,YM,ES,DJSD,SSG,RY,NQ,DX,GBL,ZL,EMD,IXU,IXV,SIXM,R,BTP,ZF,GBM,ZN,ZB,GBS,D,SB,ZR,CT,KC,CC,DA` | 86.7% | 100.0% | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,SSG,DJSD,RS,ZC,BTS,GBL,ZS,EMD,M6E,ZL,R,ZF,BTP,GBM,ZN,ZB,GBS,DX,SB,D,CT,KC,CC` | 100.0% | 100.0% |
| 2016 | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,NQ,XINA50,OAT,PA,ZM,DJUSRE,MNTPX,KE,RS,ES,SMI,DAX,HSI,GF,N225M,MXEA,Z,SX7E,ESTX50,DJ600,SSG,ZS,SX6P,ZC,LE,EMD,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,IXE,W,CT,BTS,SCI,CC,KC,D` | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,DJUSRE,XINA50,PA,OAT,ZM,NQ,MNTPX,KE,RS,ES,IXR,SIXM,IXU,DAX,SMI,HSI,GF,N225M,Z,MXEA,SX7E,DJ600,ESTX50,SSG,ZS,ZC,LE,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,W,CT,BTS,SCI,CC,KC,D` | 89.7% | 100.0% | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,NQ,XINA50,OAT,PA,ZM,DJUSRE,MNTPX,KE,RS,ES,SMI,DAX,HSI,GF,N225M,MXEA,Z,SX7E,ESTX50,DJ600,SSG,ZS,SX6P,ZC,LE,EMD,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,IXE,W,CT,BTS,SCI,CC,KC,D` | 100.0% | 100.0% |
| 2017 | `NZD,UC,CHF,DX,BRE,M6E,UB,ZT,BTS,JGB,GBX,BTP,ZB,GBS,ZF,R,GBL,OAT,ZN,GBM` | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,DJUSRE,XINA50,PA,OAT,ZM,NQ,MNTPX,KE,RS,ES,IXR,SIXM,IXU,DAX,SMI,HSI,GF,N225M,Z,MXEA,SX7E,DJ600,ESTX50,SSG,ZS,ZC,LE,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,W,CT,BTS,SCI,CC,KC,D` | 23.0% | 1.6% | `NZD,UC,CHF,DX,BRE,M6E,UB,ZT,BTS,JGB,GBX,BTP,ZB,GBS,ZF,R,GBL,OAT,ZN,GBM` | 100.0% | 100.0% |
| 2018 | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,M6E,GBX,BTP,MXEA,Z,SX6P,DAX,HSI,DJSD,DJ600,SSG,ESTX50,MNTPX,RS,IXE,DJUSRE,RTY,ES,UB,R,GBL,ZF,OAT,GF,ZB,ZL,ZN,W,CT,CC,D,KC` | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,M6E,KE,ZM,ZT,RY,DJUSRE,SI,DJ600,ZC,SX6P,SX7E,SMI,DJSD,Z,HSI,MXEA,DAX,ESTX50,SSG,RS,MNTPX,IXE,GBS,RTY,SIXM,BTP,R,UB,GBX,GBL,ZF,OAT,DX,GF,ZL,ZB,ZN,W,CT,CC,ZR,D,KC` | 93.8% | 100.0% | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,M6E,GBX,BTP,MXEA,Z,SX6P,DAX,HSI,DJSD,DJ600,SSG,ESTX50,MNTPX,RS,IXE,DJUSRE,RTY,ES,UB,R,GBL,ZF,OAT,GF,ZB,ZL,ZN,W,CT,CC,D,KC` | 100.0% | 100.0% |
| 2019 | `YM,SI,HE,BRE,GBX,MCL,NQ,BTS,CHF,HG,KE,DJUSRE,UB,CC,SCI,NZD,PA,ZC,D,RS,IBEX35,Z,IXV,IXR,SIXRE,IXU,M6E,CT,DAX,ZT,BTP,DX,JGB,W,LE,KC,GBS,TN,ZL,ZW,ZM,ZS,ZF,XINA50,SX7E,SX6P,DJ600,SMI,HSI,DJSD,ESTX50,SSG,MNTPX,GBM,R,ZN,GBL,OAT,ZB,GF,GC` | `YM,SI,HE,BRE,GBX,MCL,NQ,BTS,CHF,HG,KE,DJUSRE,UB,CC,SCI,E7,PA,W,RS,IBEX35,Z,IXU,SIXRE,D,IXR,ZT,BTP,DAX,CT,ZC,NZD,RY,JGB,SX6P,ZM,IXV,KC,ZL,ZW,ZO,GBS,TN,ZS,ZR,XINA50,ZF,SX7E,DX,DJ600,SMI,LE,HSI,ESTX50,SSG,Y,GBM,MNTPX,R,ZN,OAT,GBL,ZB,GF,GC` | 89.4% | 100.0% | `YM,SI,HE,BRE,GBX,MCL,NQ,BTS,CHF,HG,KE,DJUSRE,UB,CC,SCI,NZD,PA,ZC,D,RS,IBEX35,Z,IXV,IXR,SIXRE,IXU,M6E,CT,DAX,ZT,BTP,DX,JGB,W,LE,KC,GBS,TN,ZL,ZW,ZM,ZS,ZF,XINA50,SX7E,SX6P,DJ600,SMI,HSI,DJSD,ESTX50,SSG,MNTPX,GBM,R,ZN,GBL,OAT,ZB,GF,GC` | 100.0% | 100.0% |
| 2020 | `IBEX35,TN,DX,PA,ZS,ES,HE,SCI,NZD,ZF,XINA50,BRE,CC,NQ,CT,SI,ZC,GBX,YM,DJUSRE,ZW,CHF,DJ600,D,IXE,RS,Z,ZL,SMI,W,LE,HSI,ESTX50,SX7E,JGB,ZT,UB,OAT,R,ZN,BTP,MNTPX,KE,GBL,KC,ZM,N225M,TSEMOTHR,BTS,ZB,SX6P,SSG,MXEA,DAX,GBS,GF,GBM,GC,RB,HG` | `IBEX35,TN,DX,PA,ZS,ES,HE,SCI,NZD,ZF,XINA50,BRE,CC,NQ,DJUSRE,CHF,CT,GBX,ZW,SI,Z,ZL,D,ZC,IXE,YM,ESTX50,RS,GBL,W,ZT,R,UB,KE,ZM,BTP,ZN,HSI,LE,SX7E,SMI,BTS,TSEMOTHR,JGB,DJ600,N225M,KC,SX6P,MNTPX,SSG,Y,MXEA,ZB,GBS,OAT,GF,GBM,GC,RB,HG,M6E,DA,CSC,RY` | 90.8% | 100.0% | `IBEX35,TN,DX,PA,ZS,ES,HE,SCI,NZD,ZF,XINA50,BRE,CC,NQ,CT,SI,ZC,GBX,YM,DJUSRE,ZW,CHF,DJ600,D,IXE,RS,Z,ZL,SMI,W,LE,HSI,ESTX50,SX7E,JGB,ZT,UB,OAT,R,ZN,BTP,MNTPX,KE,GBL,KC,ZM,N225M,TSEMOTHR,BTS,ZB,SX6P,SSG,MXEA,DAX,GBS,GF,GBM,GC,RB,HG` | 100.0% | 100.0% |
| 2021 | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,DJUSRE,ZM,N225M,CHF,IXE,C,SMI,KE,HSI,SX7E,TSEMOTHR,RS,IBEX35,SX6P,Z,MXEA,ZC,CT,XINA50,DJ600,RTY,MNTPX,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,TN,R,GBX,GBM,ZT,ZN,BTS,ZB,SI` | `NQ,UB,PA,ZS,BRE,DX,HE,SCI,BTP,IBEX35,KC,N225M,ZW,IXE,XINA50,C,JGB,BTS,TN,NZD,DJUSRE,ZM,ZF,GF,DA,W,CHF,OAT,KE,SMI,TSEMOTHR,SX6P,RS,YM,SX7E,Z,DJSD,M6E,SSG,HSI,MNTPX,DJ600,Y,ESTX50,LE,GBX,ZN,ZC,GBL,R,ZT,GBS,ZB,GBM,ES,ZL,D,CT` | 78.1% | 100.0% | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,DJUSRE,ZM,N225M,CHF,IXE,C,SMI,KE,HSI,SX7E,TSEMOTHR,RS,IBEX35,SX6P,Z,MXEA,ZC,CT,XINA50,DJ600,RTY,MNTPX,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,TN,R,GBX,GBM,ZT,ZN,BTS,ZB,SI` | 100.0% | 100.0% |
| 2022 | `NQ,ZB,PA,KE,NZD,RB,BTS,ZM,CHF,SCI,HSI,LE,CC,BTP,JGB,SMI,KC,ZC,GF,XINA50,DX,BRE,ZW,HE,RS,GC,ZS,IBEX35,N225M,OAT,TN,Z3N,GBS,W,D,GBX,GBL,UB,ZT,GBM,R,ZF,MNTPX,SSG,TSEMOTHR,MXEA,SX6P,ESTX50,Z,SX7E,DJSD,TWN,ZL,ZO,CT,SI,HG,SONIA.N,ES,DJUSRE,IXE,RTY` | `NQ,ZB,PA,KE,BRE,HE,BTS,SCI,CHF,HSI,KC,SMI,NZD,TN,CC,ZM,GF,DJUSRE,OAT,ZW,XINA50,GC,Z3N,JGB,IBEX35,BTP,N225M,TSEMOTHR,RS,LE,ZC,ZL,MNTPX,SX6P,SSG,ZO,MXEA,UB,ZS,ESTX50,D,Z,R,DX,W,SX7E,GBS,DAX,Y,GBX,ZT,ZN,GBL,ZF,CT,ES,IXE,SIXM,SI,SONIA.N` | 84.8% | 100.0% | `NQ,ZB,PA,KE,NZD,RB,BTS,ZM,CHF,SCI,HSI,LE,CC,BTP,JGB,SMI,KC,ZC,GF,XINA50,DX,BRE,ZW,HE,RS,GC,ZS,IBEX35,N225M,OAT,TN,Z3N,GBS,W,D,GBX,GBL,UB,ZT,GBM,R,ZF,MNTPX,SSG,TSEMOTHR,MXEA,SX6P,ESTX50,Z,SX7E,DJSD,TWN,ZL,ZO,CT,SI,HG,SONIA.N,ES,DJUSRE,IXE,RTY` | 100.0% | 100.0% |
| 2023 | `NQ,ZL,JGB,PA,ETHUSDRR,MCL,KC,HSI,SONIA.N,SCI,LE,RS,BTP,HE,HG,ZM,GF,W,UB,N225M,ZW,ZC,SI,ZS,CT,D,BTS,GBX,KE,R,TN,CC,TSR20,GBS,OAT,ZB,ZT,GBL,GBM,ZF,ZN,GC,Z,TWN,XIN0I,MNTPX,SX7E,IBEX35,SSG,SMI,ESTX50,SX6P,MXEA,DAX,TSEMOTHR,RTY,DJUSRE,DX,IXE,ES` | `NQ,IXE,HSI,Z,IBEX35,XINA50,MXEA,N225M,XIN0I,SMI,SX7E,TWN,MNTPX,SSG,DAX,ESTX50,DJ600,RTY,DJUSRE,ES,PA,SCI,GC,HG,JGB,GBX,SONIA.N,UB,GBS,SI,BTS,ZM,HE,ZW,LE,RS,ZL,GF,ZC,KE,ZS,BTP,ZT,ZB,OAT,TN,R,GBM,GBL,ZF,Z3N,W,KC,CT,D,TSR20,RB,CC,DX,NZD,BRE,RY,I,ZR,ZO,CHF,E7` | 76.4% | 100.0% | `NQ,ZL,JGB,PA,ETHUSDRR,MCL,KC,HSI,SONIA.N,SCI,LE,RS,BTP,HE,HG,ZM,GF,W,UB,N225M,ZW,ZC,SI,ZS,CT,D,BTS,GBX,KE,R,TN,CC,TSR20,GBS,OAT,ZB,ZT,GBL,GBM,ZF,ZN,GC,Z,TWN,XIN0I,MNTPX,SX7E,IBEX35,SSG,SMI,ESTX50,SX6P,MXEA,DAX,TSEMOTHR,RTY,DJUSRE,DX,IXE,ES` | 100.0% | 100.0% |
| 2024 | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,Z,TWN,NZD,CHF,HE,IBEX35,RS,SONIA.N,MNTPX,ZM,ZW,ZL,ZC,N225M,HSI,SX7E,MCA,KE,CT,ESTX50,SMI,SSG,KC,DAX,MXEA,BTP,GBX,XIN0I,HHI.HK,GBS,ZB,BTS,GBM,TN,R,ZT,OAT,GBL,ZN,ZF,RB,BRE,GF,D,CC,LE,TSR20,GC,SI` | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,IBEX35,NZD,BRE,HE,MNTPX,RS,Z,ZW,ZM,MCA,SONIA.N,CT,ZL,N225M,KE,ZC,SX7E,KC,TWN,D,HSI,SX6P,ESTX50,XIN0I,TSEMOTHR,ZO,SMI,SSG,DAX,TSR20,CHF,GF,LE,BTP,GBX,C,GBS,ZB,R,TN,BTS,GBM,ZT,GBL,OAT,ZN,ZF,E7,RY,DJUSRE,EMD,RTY,QM,IXU,I,SI,GC` | 77.1% | 100.0% | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,Z,TWN,NZD,CHF,HE,IBEX35,RS,SONIA.N,MNTPX,ZM,ZW,ZL,ZC,N225M,HSI,SX7E,MCA,KE,CT,ESTX50,SMI,SSG,KC,DAX,MXEA,BTP,GBX,XIN0I,HHI.HK,GBS,ZB,BTS,GBM,TN,R,ZT,OAT,GBL,ZN,ZF,RB,BRE,GF,D,CC,LE,TSR20,GC,SI` | 100.0% | 100.0% |
| 2025 | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,GBX,BZ,ZC,JGB,HE,SB,RS,Z,MCA,ZM,YM,ZW,N225M,CC,XIN0I,LE,KE,D,GBS,ZS,CT,XINA50,SX7E,BTP,HSI,DA,TSR20,SMI,TWN,TSEMOTHR,ES,SSG,SX6P,DJUSRE,SIXM,IXU,OAT,BTS,R,ZT,GBL,TN,GBM,ZB,ZF,Z3N,SI` | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,BZ,GBX,ZC,JGB,I,HE,SB,RS,MCA,Z,ZM,ZW,YM,IXU,N225M,C,XIN0I,KE,IXR,TSEMOTHR,LE,D,BTP,ZS,HSI,SX7E,ES,CT,XINA50,TSR20,TWN,SSG,IXV,SMI,GBS,R,OAT,ZT,BTS,GBL,TN,GBM,ZB,ZF,Z3N,DA,SX6P,SI` | 89.2% | 100.0% | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,GBX,BZ,ZC,JGB,HE,SB,RS,Z,MCA,ZM,YM,ZW,N225M,CC,XIN0I,LE,KE,D,GBS,ZS,CT,XINA50,SX7E,BTP,HSI,DA,TSR20,SMI,TWN,TSEMOTHR,ES,SSG,SX6P,DJUSRE,SIXM,IXU,OAT,BTS,R,ZT,GBL,TN,GBM,ZB,ZF,Z3N,SI` | 100.0% | 100.0% |
| 2026 | `SI,NZD,NQ,ZS,JGB,RB,HHI.HK,ETHUSDRR,SONIA.N,KC,ZW,GBX,SCI,PA,HE,YM,GF,RS,SB,ZM,D,IXE,DJUSRE,SIXM,IXU,IXV,IXR,ZC,KE,GC,ES,CT,C,TSR20,EMD,RTY,ZL,SIXRE,TWN,HG,IXI,HSI,BTP,LE,GBS,UB,GBL,BTS,OAT,ZB,GBM,R,TN,ZT,ZN,N225M,Z3N,SSG,IBEX35,SX7E` | `SI,NZD,NQ,ZS,JGB,RB,SONIA.N,HHI.HK,D,SCI,DX,HE,PA,BRE,ZW,CC,GBX,EMD,GF,SB,RS,TWN,KU,KC,BTP,DJUSRE,SIXM,IXU,IXR,YM,GBS,RTY,ES,ZM,KE,ZC,CT,IXV,TSR20,ZL,HSI,GC,ZR,SIXRE,IXE,UB,SMI,GBL,OAT,ZT,GBM,ZB,BTS,TN,R,Z3N,ZF,SX7E,SSG,XIN0I,CHF,MCA,LE,M6E,RY,DA` | 72.6% | 100.0% | `SI,NZD,NQ,ZS,JGB,RB,HHI.HK,ETHUSDRR,SONIA.N,KC,ZW,GBX,SCI,PA,HE,YM,GF,RS,SB,ZM,D,IXE,DJUSRE,SIXM,IXU,IXV,IXR,ZC,KE,GC,ES,CT,C,TSR20,EMD,RTY,ZL,SIXRE,TWN,HG,IXI,HSI,BTP,LE,GBS,UB,GBL,BTS,OAT,ZB,GBM,R,TN,ZT,ZN,N225M,Z3N,SSG,IBEX35,SX7E` | 100.0% | 100.0% |

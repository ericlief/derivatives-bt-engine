# Selection membership-hysteresis audit: November 5, 2026

## Scope and comparability

This update compares eligibility-hysteresis-off annual-selection runs:

| Label | Run | Policy |
|---|---|---|
| No hysteresis | `20261103_185405` | Legacy raw annual selection |
| Score-2 | `20261105_093730` | `score`, 2% score hurdle, `eh0` |
| Rank-15 | `20261105_093208` | `rank`, 15% rank band, `eh0` |

The earlier `20261104_214756` score and `20261104_221157` rank runs are
retained only as 25% eligibility-hysteresis sensitivity cases. Their historical
scenario token is `eb25`; new runs use the canonical `eh0` or `eh25` token.

All primary runs use target volatility 20%, instrument margin cap 20%, book
margin cap 50%, weight-IDM cap 20%, score threshold 0.85, class count cap 20,
subclass count cap 15, class weight cap 50%, subclass weight cap 25%, annual
reviews, and a 31-day maximum history gap. The new review artifacts confirm
`sel_elig_hyst_pct=0.0`.

The legacy baseline predates the explicit hysteresis report columns. Rank-15
nevertheless selects exactly the same set as that baseline in all 54 reviews,
and their portfolio curves are identical on every common date. This provides a
strong in-run control that the inputs and non-hysteresis path remain comparable.

The common complete-history endpoint is 2026-10-23. Histories end on different
days after that date and the active book contracts, so later November rows are
not used for comparative performance.

## Main result

With eligibility hysteresis off, the 15% rank policy has no observable effect.
Its final set equals both its raw proposal and the no-hysteresis set at every
review. It has the same membership churn, weights, P&L, drawdown, and costs as
the baseline through the common endpoint. The prior rank run's differences
were caused by its implicit 25% eligibility hysteresis, not by the rank band.

Score-2 remains a meaningful stabilizer, but much less than the prior combined
score-plus-eligibility run. It leaves 14 of 53 annual transitions unchanged,
raises average year-over-year book Jaccard similarity from 78.25% to 82.76%,
and reduces additions plus removals from 490 to 418. The old `eh25` score run
left 23 transitions unchanged and reduced churn to 281.

The realized score-only performance is materially worse. Through 2026-10-23,
Score-2 ends at $158.20M, versus $275.53M for both the baseline and Rank-15.
Its CAGR is 15.29% versus 16.48%, Sharpe is 0.899 versus 0.953, and maximum
drawdown is 42.73% versus 39.56%. The earlier $334.26M score result therefore
cannot be attributed to score hysteresis alone: it came from the nonlinear path
created by combining score hysteresis with 25% eligibility hysteresis.

## Performance by ten-year window

The history is divided into five non-overlapping ten-year windows ending at the
start of 2023, followed by the 2023 through 2026-10-23 remainder. This avoids
comparing long middle regimes with a short recent segment. `Avg ann return` is
the arithmetic mean daily net return multiplied by 256. CAGR is the compounded
geometric return. Sharpe equals average annualized return divided by annualized
volatility. Drawdown is measured on net equity.

Ending net equity remains path-dependent and is included to show the inherited
capital state; use average return, CAGR, and Sharpe for within-window performance
comparisons.

| Window | Run | Avg ann return | CAGR | Ann vol | Sharpe | Max drawdown | Ending net equity |
|---|---|---:|---:|---:|---:|---:|---:|
| 1973-1982 | No hysteresis | 34.42% | 38.04% | 18.48% | 1.862 | -19.75% | $1.88M |
| 1973-1982 | Score-2 | 32.78% | 36.03% | 17.61% | 1.861 | -19.70% | $1.62M |
| 1973-1982 | Rank-15 | 34.42% | 38.04% | 18.48% | 1.862 | -19.75% | $1.88M |
| 1983-1992 | No hysteresis | 13.25% | 12.48% | 17.33% | 0.765 | -21.73% | $6.09M |
| 1983-1992 | Score-2 | 13.44% | 12.75% | 17.00% | 0.791 | -25.49% | $5.39M |
| 1983-1992 | Rank-15 | 13.25% | 12.48% | 17.33% | 0.765 | -21.73% | $6.09M |
| 1993-2002 | No hysteresis | 18.85% | 19.09% | 17.69% | 1.065 | -25.91% | $34.85M |
| 1993-2002 | Score-2 | 18.88% | 18.89% | 18.67% | 1.011 | -32.28% | $30.34M |
| 1993-2002 | Rank-15 | 18.85% | 19.09% | 17.69% | 1.065 | -25.91% | $34.85M |
| 2003-2012 | No hysteresis | 15.59% | 14.81% | 20.03% | 0.778 | -25.35% | $138.58M |
| 2003-2012 | Score-2 | 13.39% | 12.34% | 19.74% | 0.678 | -25.63% | $97.10M |
| 2003-2012 | Rank-15 | 15.59% | 14.81% | 20.03% | 0.778 | -25.35% | $138.58M |
| 2013-2022 | No hysteresis | 8.73% | 8.29% | 14.29% | 0.611 | -33.51% | $307.14M |
| 2013-2022 | Score-2 | 7.07% | 6.45% | 14.19% | 0.498 | -34.13% | $181.43M |
| 2013-2022 | Rank-15 | 8.73% | 8.29% | 14.29% | 0.611 | -33.51% | $307.14M |
| 2023-2026-10-23 | No hysteresis | -1.77% | -2.81% | 10.96% | -0.162 | -26.31% | $275.53M |
| 2023-2026-10-23 | Score-2 | -2.38% | -3.54% | 11.13% | -0.213 | -29.10% | $158.20M |
| 2023-2026-10-23 | Rank-15 | -1.77% | -2.81% | 10.96% | -0.162 | -26.31% | $275.53M |
| Full history | No hysteresis | 16.40% | 16.48% | 17.21% | 0.953 | -39.56% | $275.53M |
| Full history | Score-2 | 15.38% | 15.29% | 17.10% | 0.899 | -42.73% | $158.20M |
| Full history | Rank-15 | 16.40% | 16.48% | 17.21% | 0.953 | -39.56% | $275.53M |

Score-2 is slightly better on average return, CAGR, and Sharpe in 1983-1992,
but its drawdown is worse. It is approximately tied on average return in
1993-2002 while producing lower CAGR, lower Sharpe, and a materially deeper
drawdown. Its economically large weakness appears in 2003-2012 and persists
through 2013-2022. In the 2023 onward remainder, both average return and Sharpe
are negative for every run, with Score-2 again weaker.

The shorter recent calendar results are:

| Year | No hysteresis | Score-2 | Rank-15 |
|---:|---:|---:|---:|
| 2023 | -10.59% | -10.60% | -10.59% |
| 2024 | -7.82% | -11.37% | -7.82% |
| 2025 | +0.98% | +1.09% | +0.98% |
| 2026 through Oct 23 | +7.80% | +8.86% | +7.80% |

Score-2's recent deficit is mostly created in 2024. It performs slightly better
in 2025 and 2026, but not enough to recover that loss.

## Eligibility-hysteresis sensitivity

Removing the 25% eligibility hysteresis materially changes both modes:

| Mode | `eh0` ending equity | `eh25` ending equity | `eh0` CAGR | `eh25` CAGR | Mean book Jaccard | Different reviews |
|---|---:|---:|---:|---:|---:|---:|
| Score-2 | $158.20M | $334.26M | 15.29% | 16.90% | 90.05% | 34 / 54 |
| Rank-15 | $275.53M | $285.33M | 16.48% | 16.56% | 95.15% | 33 / 54 |

The score books are identical under `eh0` and `eh25` through 1984; eligibility
hysteresis does not explain the concentrated early score book. The first score
difference appears in 1985. Thereafter, eligibility hysteresis changes which
incumbents survive the soft cost and participation gates, which changes equity,
position granularity, later raw selection, and compounding. The $176.06M score
ending-equity difference is therefore a path interaction, not a direct estimate
of saved transaction costs.

The 25% eligibility setting also worsens recent percentage performance despite
its much higher inherited capital: from 2024 through October 23, Score-2
returns -4.60% with `eh25` versus -2.47% with `eh0`; Rank-15 returns -1.00%
with `eh25` versus +0.34% with `eh0`. Its historical advantage was accumulated
before 2024.

## Membership stability and diversification

| Statistic | No hysteresis | Score-2 | Rank-15 |
|---|---:|---:|---:|
| Average instruments per review | 34.78 | 35.35 | 34.78 |
| Average year-over-year Jaccard | 78.25% | 82.76% | 78.25% |
| Unchanged annual transitions | 0 / 53 | 14 / 53 | 0 / 53 |
| Unique instrument sets | 53 | 40 | 53 |
| Additions plus removals | 490 | 418 | 490 |
| Average final/raw Jaccard | n/a | 92.17% | 100.00% |
| Final set equals raw proposal | n/a | 40 / 54 | 54 / 54 |
| Average final/base Jaccard | 100.00% | 87.07% | 100.00% |

The early score concentration remains. Score-2 holds the same 12-instrument,
two-class book from 1978 through 1984. In 1984 it is approximately 50%
agriculture and 50% FX, with an effective instrument count of 8.8. The baseline
and Rank-15 books have 20 instruments across five classes and an effective
count of 10.6.

Score stasis is not always concentration-increasing. At the 2017 review its raw
proposal collapses to seven instruments, while the feasible prior book contains
55. The score hurdle retains the diversified incumbent because the raw
challenger's score is 0.393 SR worse before transition cost. More generally,
from 2004-2023 Score-2 averages 54.2 instruments and an effective count of 20.4,
above the baseline's 50.9 and 19.1. Its underperformance in that period is not
explained by a smaller instrument count.

## Trading-cost effect

| Measure | No hysteresis | Score-2 | Rank-15 |
|---|---:|---:|---:|
| Absolute contracts traded on review dates | 81,863 | 71,257 | 81,863 |
| Cumulative review-date trade cost | 1.647% | 1.450% | 1.647% |
| Cumulative all-date trade cost through Oct 23 | 45.755% | 47.713% | 45.755% |
| Cumulative all-date roll cost through Oct 23 | 23.845% | 25.071% | 23.845% |
| Cumulative total cost through Oct 23 | 69.600% | 72.784% | 69.600% |

Score-2 cuts review-date contract turnover by 13.0% and review-date percentage
cost by 12.0%. That saving does not carry through to total cost. Its different
book is more expensive over the daily simulation, increasing cumulative trade
plus roll cost by 3.18 percentage points. Rank-15 produces no cost saving
because it never changes the raw membership set.

## Interpretation and next tests

The corrected runs support three conclusions:

1. Rank-15 is too narrow or its greedy rank ordering is too discontinuous to
   retain any incumbent in this sample. Test 25% and 30% before retaining this
   feature as economically useful.
2. Score-2 is a real stabilizer but is too blunt as a sole rule. It can freeze a
   sparse early book, yet it can also prevent a later raw-book collapse. Test a
   1% hurdle and a diversification-aware override rather than treating every
   whole-book change identically.
3. Eligibility hysteresis is a major independent policy lever. Test 0%, 10%,
   and 25% separately for each membership mode; do not infer score or rank
   performance from a combined configuration.

Because compounded equity feeds back into selection feasibility, a second
policy diagnostic should replay all review decisions against the same frozen
equity path. The dynamic-capital backtest remains the economically relevant
simulation, but a frozen-equity replay is needed to separate membership logic
from capital-path amplification.

## Annual books and Jaccard similarity

Instrument order is the selector's reported order; Jaccard treats each book as
an unordered set. `S/base` and `R/base` compare each hysteresis book with the
no-hysteresis book in that year. `S/raw` and `R/raw` compare the final book with
that run's own unbuffered proposal, isolating whether membership hysteresis
changed the set.

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

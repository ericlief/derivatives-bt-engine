# Selection membership-hysteresis audit: November 4, 2026

## Scope and comparability

This audit compares the annual-selection runs below:

| Label | Run | Membership policy |
|---|---|---|
| No hysteresis | `20261103_185405` | Legacy annual selection with no membership hysteresis |
| Score | `20261104_214756` | `score`, 2% whole-book score buffer |
| Rank | `20261104_221157` | `rank`, 15% entry/exit rank band |

The common selection settings are target volatility 20%, instrument margin cap
20%, book margin cap 50%, weight-IDM cap 20%, score threshold 0.85, class
count cap 20, subclass count cap 15, class weight cap 50%, subclass weight cap
25%, annual reviews, and a 31-day maximum history gap.

This is not a perfectly controlled one-parameter ablation. Both new runs
resolve to a 25% incumbent soft-eligibility exit buffer (`eb25`); the legacy
baseline does not. The rank command did not pass the flag explicitly, but the
CLI default at the time of the run was 25%, the buffer is independent of
membership mode, and the saved rank review report confirms
`sel_elig_exit_buf_pct=25.0`. The default was subsequently changed to zero so
new runs do not enable this independent buffer implicitly. Dynamic capital
also feeds back into position granularity, margin, and later selection. Once
returns diverge, later raw books need not match even before membership
hysteresis is applied. A clean confirmation run should use the current code
with
`--selection-membership-hysteresis none` and
`--selection-eligibility-exit-buffer-pct 25`.

The common complete-history endpoint used for recent performance is
2026-10-23. After that date, histories end on different days and the active
instrument count contracts, so the November endpoint is not a comparable full
book.

## Main result

Score hysteresis is materially more prone to stasis. It leaves 23 of 53 annual
transitions completely unchanged, compared with zero for the legacy baseline
and two for rank hysteresis. Its average year-over-year book Jaccard similarity
is 86.7%, versus 78.2% for the baseline and 81.0% for rank. Total annual
membership churn (additions plus removals) falls from 490 symbols to 281, a
42.7% reduction.

That stability can preserve a poorly diversified old book. The score run keeps
the same 12-instrument, two-class book from 1978 through 1984 even as the
unbuffered selector expands. In 1984 the score book is 50% agriculture and 50%
FX. The baseline and rank books contain 20 instruments across five classes,
with approximately 20% in each class. The score book's normalized effective
instrument count is 8.8, versus 10.6 in the baseline and rank books.

The score run nevertheless has the best full-history result. Through
2026-10-23 it ends at $334.26M, versus $285.33M for rank and $275.53M for the
baseline. The advantage is earned primarily during 2004-2023, not after 2024.
Score enters 2024 with $350.58M, already 27.6% above the baseline. From
2024-01-01 through 2026-10-23 it loses 4.60%, while the baseline gains 0.34%
and rank loses 1.00%. Its higher ending capital is therefore inherited from
the earlier compounding path; the recent period reduces that lead.

## Performance by regime

Returns are compounded from daily net percentage returns. Volatility and
Sharpe use 256 business days. Drawdown is measured on net equity.

| Regime | Run | CAGR | Ann vol | Sharpe | Max drawdown | Ending net equity |
|---|---|---:|---:|---:|---:|---:|
| 1973-1984 | No hysteresis | 33.76% | 18.10% | 1.721 | -19.75% | $2.46M |
| 1973-1984 | Score | 32.21% | 16.93% | 1.758 | -19.70% | $2.14M |
| 1973-1984 | Rank | 35.48% | 18.13% | 1.790 | -21.06% | $2.86M |
| 1985-2003 | No hysteresis | 16.09% | 17.80% | 0.921 | -25.91% | $41.78M |
| 1985-2003 | Score | 17.06% | 18.38% | 0.943 | -32.35% | $42.59M |
| 1985-2003 | Rank | 16.08% | 18.04% | 0.911 | -28.03% | $48.63M |
| 2004-2023 | No hysteresis | 9.88% | 16.87% | 0.624 | -33.51% | $274.60M |
| 2004-2023 | Score | 11.12% | 17.30% | 0.676 | -26.17% | $350.37M |
| 2004-2023 | Rank | 9.31% | 16.83% | 0.595 | -33.24% | $288.19M |
| 2024-2026-10-23 | No hysteresis | +0.34% total | 11.44% | 0.066 | -21.11% | $275.53M |
| 2024-2026-10-23 | Score | -4.60% total | 11.00% | -0.073 | -21.89% | $334.26M |
| 2024-2026-10-23 | Rank | -1.00% total | 12.17% | 0.036 | -21.29% | $285.33M |
| Full history | No hysteresis | 16.48% | 17.21% | 0.953 | -39.56% | $275.53M |
| Full history | Score | 16.90% | 17.30% | 0.970 | -39.01% | $334.26M |
| Full history | Rank | 16.56% | 17.31% | 0.952 | -40.98% | $285.33M |

The score policy's early stasis is not unambiguously bad ex post. It produces
less capital through 1984 but slightly higher early Sharpe because volatility
also falls. Its strongest evidence is the 2004-2023 regime: CAGR is 1.24
percentage points above baseline, Sharpe is higher, and maximum drawdown is
7.3 percentage points shallower. This is one realized path, however, and does
not establish that retaining the incumbent book caused those improvements.

## Membership stability and similarity

| Statistic | No hysteresis | Score | Rank |
|---|---:|---:|---:|
| Average instruments per review | 34.78 | 33.98 | 34.74 |
| Average year-over-year Jaccard | 78.25% | 86.72% | 81.04% |
| Unchanged annual transitions | 0 / 53 | 23 / 53 | 2 / 53 |
| Unique instrument sets | 53 | 31 | 52 |
| Additions plus removals | 490 | 281 | 439 |
| Average final/raw Jaccard | n/a | 90.85% | 99.78% |
| Final set equals raw proposal | n/a | 31 / 54 | 53 / 54 |

The direct final-versus-raw comparison is the cleanest diagnostic inside each
new run. Score hysteresis overrides 23 raw proposals. Rank hysteresis changes
membership only in 2012; in every other year its final set equals its own raw
set. The 15% rank default is therefore almost inert in this simulation. It
does not support attributing the rank run's performance difference from the
older baseline to rank buffering.

Score rejects 23 reviews because net score gain is below its hurdle. Twelve
reviews switch because net score gain clears the hurdle; the remaining changes
are initial selection or forced by hard ineligibility. In the most visible
early streak, the 1984 challenger improves gross book score by 0.0243 SR, but
estimated switching cost consumes 0.0070 SR and the 2% score buffer requires
0.0199 SR. Net gain is 0.0173 SR, so the 12-instrument incumbent survives.

Average cross-run Jaccard similarity to the legacy baseline is:

| Period | Score/base | Rank/base | Score/rank |
|---|---:|---:|---:|
| 1973-1984 | 81.71% | 96.79% | 81.66% |
| 1985-2003 | 92.47% | 96.23% | 91.66% |
| 2004-2023 | 83.60% | 93.71% | 85.33% |
| 2024-2026 | 91.51% | 91.31% | 85.42% |
| All reviews | 86.74% | 95.15% | 86.75% |

All three score books from 2024 through 2026 equal their own unbuffered
proposals. Thus score hysteresis does not directly veto a scheduled recent
book. Its recent P&L difference is inherited from prior membership, capital,
and resulting raw-selection paths.

## Trading-cost effect

Score hysteresis reduces selection-day activity, but not total trading cost
materially:

| Measure | No hysteresis | Score | Rank |
|---|---:|---:|---:|
| Absolute contracts traded on review dates | 81,863 | 45,333 | 94,504 |
| Cumulative review-date trade cost | 1.647% | 1.150% | 1.478% |
| Cumulative all-date trade cost | 45.767% | 45.763% | 45.768% |

Score cuts review-date contract turnover by 44.6% and review-date percentage
cost by about 30%. Total percentage trading cost is virtually unchanged
because forecast changes, dynamic capital scaling, execution buffering, and
roll-related trading dominate the full daily simulation. Absolute dollar cost
is not comparable without scaling for the different capital paths.

## Interpretation and next test

The score policy is a strong stabilizer but its whole-book veto is too broad
for sparse early universes. A 2% hurdle can freeze the whole incumbent even
when the challenger adds several new asset classes. The current result argues
for making the score hurdle conditional on a diversification floor, or applying
it to substitutions while allowing additions that materially improve class or
subclass coverage. That should be tested, not assumed.

The rank policy is behaviorally safer in the sparse years, but the 15% default
does almost nothing to the selected sets here. Before judging its performance,
run 25% and 30% rank bands and verify final/raw Jaccard and membership churn.
A useful controlled grid is:

1. Current-code baseline: `none`, `eb25`.
2. Rank: 15%, 25%, and 30%, all with `eb25`.
3. Score: 0%, 1%, and 2%, all with `eb25`.
4. Rank plus score: rank 15%/25% crossed with score 0%/1%.
5. Report early-universe class count, effective instrument count, review-day
   cost, post-2024 return, and full-history drawdown in addition to ending cap.

The selection result depends on dynamically compounded equity. For a pure
membership-policy comparison, a second diagnostic should freeze review equity
or replay every policy against the same equity path. Otherwise better early
returns expand later sizing feasibility and alter the book being compared.

## Annual books and Jaccard similarity

Instrument order is the selector's reported order; Jaccard treats each book as
an unordered set. `S/base` and `R/base` compare each hysteresis book with
the legacy no-hysteresis book in that year. `S/raw` and `R/raw` compare the
final book with that run's own unbuffered proposal, isolating whether the
membership policy changed the set.

| Year | No-hysteresis instruments | Score-hysteresis instruments | S/base | S/raw | Rank-hysteresis instruments | R/base | R/raw |
|---:|---|---|---:|---:|---|---:|---:|
| 1973 | `SB,ZM,ZL,CT,YC,YW` | `SB,ZM,ZL,CT,YC,YW` | 100.0% | 100.0% | `SB,ZM,ZL,CT,YC,YW` | 100.0% | 100.0% |
| 1974 | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 100.0% | 100.0% | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 100.0% | 100.0% |
| 1975 | `CAD,ZC,CHF,LE,CT,ZL,SB,ZM,YW,CC` | `CAD,LE,CHF,ZM,SB,CT,ZL,YW,YC` | 72.7% | 72.7% | `CAD,ZC,CHF,LE,CT,ZL,SB,ZM,YW,CC` | 100.0% | 100.0% |
| 1976 | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC` | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC` | 100.0% | 100.0% | `CAD,HE,SB,CHF,ZW,ZL,LE,CT,ZM,ZC,CC` | 90.9% | 100.0% |
| 1977 | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | 100.0% | 100.0% | `CAD,ZW,GC,ZM,M6B,SI,SB,HE,CHF,CT,LE,ZC,CC,ZL` | 100.0% | 100.0% |
| 1978 | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 100.0% | 100.0% | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 100.0% | 100.0% |
| 1979 | `J7,LE,SB,CAD,ZW,M6B,ZM,HE,CT,ZC,MSF,GF,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 73.3% | 85.7% | `J7,LE,SB,CAD,ZW,M6B,ZM,HE,CT,ZC,CHF,GF,ZL,CC` | 86.7% | 100.0% |
| 1980 | `J7,SI,HE,ZB,ZC,CAD,SB,CHF,CT,ZM,GC,GF,CC,ZW,LE,ZL,M6B` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 70.6% | 70.6% | `J7,SI,HE,ZB,ZC,CAD,SB,CHF,CT,ZM,GC,GF,CC,ZW,LE,ZL,M6B` | 100.0% | 100.0% |
| 1981 | `J7,SI,HE,ZB,CT,CAD,MGC,ZC,CHF,SB,LE,M6B,ZO,ZW,GF,ZM,ZL,CC` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 66.7% | 66.7% | `J7,SI,HE,ZB,CT,CAD,GC,ZC,CHF,SB,LE,M6B,ZO,ZW,GF,ZM,ZL,CC` | 89.5% | 100.0% |
| 1982 | `J7,ZW,SI,ZB,ZC,CAD,HE,CT,GC,SB,LE,CC,GF,M6B,ZM,ZL,CHF` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 70.6% | 70.6% | `J7,ZW,SI,ZB,ZC,CAD,ZO,GC,HE,CT,SB,LE,CC,GF,M6B,ZM,ZL,CHF` | 94.4% | 100.0% |
| 1983 | `J7,ZB,HE,SI,CT,CAD,GC,SB,M6B,ZC,ZW,CC,ZM,ZL,YK,LE,GF,CHF` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 66.7% | 66.7% | `J7,ZB,HE,SI,CT,CAD,GC,SB,M6B,ZC,ZW,CC,ZM,ZL,YK,LE,GF,CHF` | 100.0% | 100.0% |
| 1984 | `M6B,ZN,ZM,SB,CHF,J7,HE,CAD,CT,ZW,ZL,GF,ZC,YK,CC,LE,GC,SI,ES,ZB` | `CAD,M6B,ZM,SB,HE,CHF,ZC,CT,LE,ZW,ZL,CC` | 60.0% | 60.0% | `J7,ZN,SB,CT,M6B,CAD,CHF,ZL,ZW,ZM,ZC,CC,YK,HE,GF,LE,GC,SI,ES,ZB` | 100.0% | 100.0% |
| 1985 | `R,M6B,HE,ZC,ZN,MCD,CT,CHF,SB,LE,ZW,CC,ZL,ZM,GF,YK,ZB,GC,SI,ES,J7` | `R,M6B,HE,ZC,CAD,ZN,CT,SB,CHF,LE,CC,ZW,ZL,ZM,GF,YK,ZB,GC,SI,ES,J7` | 90.9% | 100.0% | `R,M6B,HE,ZC,ZN,CHF,SB,CT,LE,CC,ZL,ZW,GF,ZM,YK,ZB,GC,SI,ES,CAD,J7` | 90.9% | 100.0% |
| 1986 | `R,SI,ZL,Z,M6B,LE,ZN,ES,GC,ZW,HE,GF,ZC,CHF,MCD,SB,CC,CT,YK,ZM,SOFR3,ZB` | `R,SI,ZL,Z,M6B,LE,ZN,MCD,ES,GC,J7,SB,CC,HE,ZW,GF,CT,ZC,YK,ZM,SOFR3,ZB,CHF` | 95.7% | 100.0% | `R,SI,ZL,Z,M6B,LE,ZN,ES,GC,ZW,HE,GF,ZC,CHF,MCD,J7,SB,CC,CT,YK,ZM,SOFR3,ZB` | 95.7% | 100.0% |
| 1987 | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,MCD,J7,SI,SB,CT,CC,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 95.8% | 100.0% | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,J7,MCD,SI,SB,CT,CC,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 95.8% | 100.0% |
| 1988 | `R,ZM,DX,ES,RB,CC,Z,ZN,LE,CT,CHF,J7,ZW,M6B,MCD,SB,ZC,ZL,ZS,GC,SI,GF,HE,SOFR3,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,MCD,J7,SI,SB,CT,CC,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 96.0% | 96.0% | `R,ZM,DX,ES,RB,CC,Z,ZN,LE,CT,CHF,J7,ZW,M6B,MCD,SB,ZC,ZL,ZS,GC,SI,GF,HE,SOFR3,ZB` | 100.0% | 100.0% |
| 1989 | `DX,ZS,R,GC,M6A,ES,RB,LE,Z,SOFR3,CAD,SB,SI,CT,ZL,ZW,ZM,ZC,ZO,GF,HE,CC,J7,CHF,M6B,ZN,ZB` | `R,GC,ZS,HE,ZN,RB,Z,ES,M6B,CHF,MCD,J7,SI,SB,CT,CC,ZW,LE,ZL,ZC,GF,ZM,SOFR3,ZB` | 82.1% | 82.1% | `DX,ZS,R,GC,M6A,ES,RB,LE,Z,SOFR3,MCD,SB,SI,CT,ZL,ZW,ZM,ZC,ZO,GF,HE,CC,J7,CHF,M6B,ZN,ZB` | 92.9% | 100.0% |
| 1990 | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCD,MCL,SB,ZS,ZM,ZL,ZC,J7,GF,HE,LE,M6B,CHF,ZB,SOFR3` | 96.0% | 100.0% | `DX,CT,R,RB,ES,M6A,ZW,GC,Z,ZN,MCL,SB,ZS,ZM,ZL,ZC,LE,HE,GF,ZB,M6B,MCD,J7,CHF` | 100.0% | 100.0% |
| 1991 | `W,DX,R,GC,MCL,ZM,M6A,ZF,SI,RB,ZW,CT,ES,J7,MCD,ZC,GF,HE,LE,ZS,ZL,M6B,CHF,ZN,ZB` | `W,DX,R,GC,MCL,ZM,M6A,ZF,SI,RB,ZW,J7,MCD,CT,ZC,GF,HE,LE,ZS,ZL,M6B,CHF,ES,ZN,ZB` | 100.0% | 100.0% | `W,DX,R,GC,MCL,ZM,M6A,ZF,SI,RB,ZW,CT,ES,J7,MCD,ZC,GF,HE,LE,ZS,ZL,M6B,CHF,ZN,ZB` | 100.0% | 100.0% |
| 1992 | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,GF,HE,ZS,ZW,ZM,ZC,ZL,LE,M6B,MCD,J7,CHF,ZN,ZB` | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,MCD,J7,GF,HE,ZS,ZC,ZM,ZW,ZL,LE,M6B,CHF,ZN,ZB` | 100.0% | 100.0% | `W,DX,R,ES,CL,CT,GC,M6A,Z,RB,ZF,GF,HE,ZS,ZW,ZM,ZC,ZL,LE,M6B,MCD,J7,CHF,ZN,ZB` | 100.0% | 100.0% |
| 1993 | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,HE,ZW,CT,CHF,MCD,J7,RS,ZM,GF,ZC,ZL,DX,Z,ZN,ZB,GC` | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,MCD,J7,CT,RS,HE,ZW,GF,ZM,ZC,CHF,ZL,DX,Z,ZN,ZB,GC,QG` | 96.4% | 100.0% | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,HE,ZW,CT,CHF,MCD,J7,RS,ZM,GF,ZC,ZL,DX,Z,ZN,ZB,GC` | 100.0% | 100.0% |
| 1994 | `DX,HSI,W,RB,R,SI,M6A,ZF,ZC,CL,ES,ZR,CT,ZW,ZL,RS,ZS,ZM,LE,HE,GF,M6B,MCD,J7,CHF,Z,ZN,ZB` | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,MCD,J7,CT,RS,HE,ZW,GF,ZM,ZC,CHF,ZL,DX,Z,ZN,ZB,GC,QG` | 86.7% | 90.0% | `DX,HSI,W,RB,R,SI,M6A,ZF,ZC,CL,ES,ZR,CT,ZW,ZL,RS,ZS,ZM,LE,HE,GF,M6B,MCD,J7,CHF,Z,ZN,ZB` | 100.0% | 100.0% |
| 1995 | `R,W,HSI,M6B,CL,GC,ZF,ZC,ES,SI,CHF,M6A,LE,HE,ZW,ZM,CT,ZS,ZL,RS,ZR,GF,DX,MCD,J7,ZN,ZB,Z` | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,MCD,J7,CT,RS,HE,ZW,GF,ZM,ZC,CHF,ZL,DX,Z,ZN,ZB,GC,QG` | 86.7% | 86.7% | `R,W,HSI,M6B,CL,GC,ZF,ZC,ES,SI,CHF,M6A,LE,HE,ZW,ZM,CT,ZS,ZL,RS,ZR,GF,DX,MCD,J7,ZN,ZB,Z` | 100.0% | 100.0% |
| 1996 | `R,W,DX,HSI,CL,GC,ZW,ES,ZF,CT,RB,CHF,M6A,ZS,HE,LE,ZC,ZM,RS,ZR,ZL,GF,M6B,J7,MCD,ZN,Z,ZB` | `W,R,M6B,HSI,RB,ZS,M6A,CL,ZF,ES,LE,MCD,J7,CT,RS,HE,ZW,GF,ZM,ZC,CHF,ZL,DX,Z,ZN,ZB,GC,QG` | 93.1% | 82.8% | `R,W,DX,HSI,CL,GC,ZW,M6A,ES,ZF,HE,RB,CT,LE,ZC,ZM,CHF,ZS,RS,ZR,ZL,GF,M6B,J7,MCD,ZN,Z,ZB` | 100.0% | 100.0% |
| 1997 | `R,W,HSI,DX,HG,CL,CT,ZF,GC,M6A,ES,KE,M6B,HE,SI,GF,ZM,ZL,ZW,ZS,ZC,RS,LE,BRE,CHF,ZB,Z,ZN` | `R,W,HSI,DX,HG,CL,QG,CT,ZF,SI,M6A,ES,KE,M6B,GC,HE,GF,ZM,ZL,ZS,ZW,ZC,RS,LE,BRE,CHF,ZN,ZB` | 93.1% | 100.0% | `R,W,HSI,DX,HG,CL,CT,ZF,GC,M6A,ES,KE,M6B,HE,SI,GF,ZM,ZR,ZW,ZL,ZS,ZC,RS,BRE,CHF,LE,ZB,Z,ZN` | 96.6% | 100.0% |
| 1998 | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 100.0% | 100.0% | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,ZR,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,ZB` | 88.0% | 100.0% |
| 1999 | `Z,HSI,DX,BRE,W,LE,RS,ZW,HE,CT,DA,ZM,ZL,ZR,GF,ZC,KE,ZS,CHF,ES,PA,HG,GC,SI,ZF,R,RB,ZN,ZB` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 73.3% | 73.3% | `Z,HSI,DX,BRE,W,LE,RS,ZW,HE,CT,DA,ZM,ZR,ZL,GF,ZC,KE,ZS,CHF,ES,PA,HG,GC,SI,ZF,R,RB,ZN,ZB` | 100.0% | 100.0% |
| 2000 | `R,BRE,W,RB,HSI,GC,PA,ES,ZW,ZF,CHF,GF,SI,HE,ZM,RS,KE,LE,ZL,ZC,Z,ZS,CT,ZN,ZB,HG,DX` | `Z,HSI,W,ZM,HE,CT,ZW,ES,LE,ZL,KE,ZS,RS,ZC,GF,HG,GC,SI,RB,CL,DX,ZF,BRE` | 78.6% | 78.6% | `R,BRE,W,RB,HSI,GC,PA,ES,ZW,ZF,CHF,GF,SI,HE,ZM,ZR,RS,KE,LE,ZL,ZC,ZS,DA,Z,CT,ZN,ZB,HG,DX` | 93.1% | 100.0% |
| 2001 | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZC,GC,ZL,ZS,CT,ZB,GF,SI,ZF,LE,I,DX,M6A` | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZC,ZS,GC,ZL,CT,ZB,GF,SI,ZF,LE,I,DX,M6A` | 100.0% | 100.0% | `EUR,PA,ZN,W,QG,HSI,ES,ZM,CL,BRE,HG,SGB,HE,R,ZW,RS,KE,ZS,ZC,GC,ZL,CT,ZB,GF,SI,ZF,LE,I,ZR` | 90.3% | 100.0% |
| 2002 | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,Z,ZW,ZL,KE,ZO,LE,CT,ES,DX,M6A,CHF,M6B,DAX,ZT,ZN,ZF,HG,I` | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,Z,ZW,ZL,KE,ZO,LE,M6A,ES,CT,DX,CHF,M6B,DAX,ZT,ZN,ZF,HG,I` | 100.0% | 100.0% | `EUR,NQ,PA,ZS,R,RB,BRE,W,SGB,HSI,GC,RY,ZM,IBEX35,HE,GF,SI,RF,ZB,RS,ZC,ZR,ZW,ZL,Z,KE,ZO,LE,CT,DX,DAX,CHF,M6A,ZT,ES,ZN,ZF,I,IBXXIBHY` | 90.2% | 100.0% |
| 2003 | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ES,CC,Z,DAX,ZT,DX,ZB,ZN,ZF,I,IBXXIBHY,EUR,RF,CHF` | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ZO,ES,CC,DX,ZT,ZB,RF,ZN,ZF,Z,I,DAX,EUR,CHF,M6A` | 92.5% | 100.0% | `IBEX35,RY,HE,SGB,RB,PA,SSG,RS,SI,BRE,R,NQ,W,MHI,LE,ZM,CT,GC,ZC,GF,KE,ZS,ZW,ZL,ZO,ES,CC,DX,ZT,ZB,Z,DAX,ZN,ZF,I,IBXXIBHY,EUR,CHF` | 94.9% | 100.0% |
| 2004 | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZR,SI,ZM,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,EMD,ZB,ES,ZF,ZN,DAX,DX,EUR,I,RF` | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZR,SI,ZM,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,ZB,EMD,ES,ZF,ZN,DAX,DX,EUR,I,RF` | 100.0% | 100.0% | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZM,ZR,SI,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,ZB,EMD,ES,ZF,ZN,DAX,DX,EUR,CHF,I` | 95.1% | 100.0% |
| 2005 | `NZD,JGB,NQ,HE,CL,GF,BRE,HSI,R,RY,RS,PA,SI,IBEX35,CT,W,SSG,CC,LE,Z,ZS,ZW,ZR,ZM,KE,ZC,ZL,DA,YM,ES,DAX,EMD,CHF,ZT,EUR,DX,M6A,RF,ZB,NOK,ZF,SEK,M6B,ZN,GC,I` | `IBEX35,RY,ZS,JGB,RB,PA,LE,NQ,HG,BRE,R,MHI,CT,SSG,W,RS,ZR,SI,ZM,HE,CC,ZL,ZW,GF,ZC,KE,Z,ZT,GC,YM,ZB,EMD,ES,ZF,ZN,DAX,DX,EUR,I,RF` | 75.5% | 73.5% | `NZD,JGB,NQ,HE,CL,GF,BRE,HSI,R,RY,RS,PA,SI,IBEX35,CT,W,SSG,CC,LE,Z,ZS,ZW,ZR,ZM,KE,ZC,ZL,DA,YM,ES,DAX,EMD,CHF,ZT,EUR,DX,ZB,RF,ZN,NOK,M6A,ZF,SEK,GC,I` | 97.8% | 100.0% |
| 2006 | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,GC,CT,ZC,YM,ZT,HE,W,HSI,IBEX35,RS,LE,KE,ZM,ZL,ZW,CHF,ZB,ZR,EUR,DAX,DX,ZF,ZO,ZN,EMD,I,RF` | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,CT,SI,RS,W,HE,IBEX35,ZT,KE,HSI,YM,ZM,LE,ZC,ZW,EUR,ZL,EMD,ZB,ZO,DX,ZF,DAX,ZN,ZR,I,ES,CHF` | 90.5% | 100.0% | `NZD,JGB,Z,ZS,QG,PA,NQ,GF,R,CL,RY,BRE,SSG,GC,CT,ZC,YM,ZT,HE,W,HSI,IBEX35,RS,LE,KE,ZM,ZL,ZW,CHF,ZB,ZR,EUR,DAX,DX,ZF,ZO,EMD,ZN,I,RF` | 100.0% | 100.0% |
| 2007 | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,CT,SSG,PA,YM,CHF,RY,R,ZM,ZW,HSI,RS,ZS,KE,LE,HE,SX7E,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,E7,DX,NOK,I,SEK` | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,RY,CT,SSG,PA,YM,R,ZM,RS,HSI,ZW,ZS,KE,SX7E,CHF,LE,HE,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,I,SEK,NOK` | 95.7% | 100.0% | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,CT,SSG,PA,YM,CHF,RY,R,ZM,ZW,HSI,RS,ZS,KE,LE,HE,SX7E,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,I` | 91.3% | 100.0% |
| 2008 | `IBEX35,ZB,KE,RB,NZD,HG,GF,JGB,NQ,CHF,PA,HSI,CT,BRE,ZT,SI,RY,SX7E,SSG,LE,NOK,RF,SB,ZC,HE,SEK,ZW,ZM,RS,ZR,DA,ZS,EUR,M6A,R,ZF,YM,DX,Z,ZN,DAX,ZL,GBL,GC,DJ600,I,Y,EMD` | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,RY,CT,SSG,PA,YM,R,ZM,RS,HSI,ZW,ZS,KE,SX7E,CHF,LE,HE,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,I,SEK,NOK` | 82.4% | 86.0% | `IBEX35,ZB,KE,RB,NZD,HG,GF,JGB,NQ,CHF,PA,HSI,CT,BRE,ZT,SI,RY,SX7E,SSG,LE,HE,SB,YM,ZC,ZW,ZM,RS,ZR,ZO,ZS,DA,ES,EUR,R,ZF,RF,DX,ZL,EMD,ZN,GBL,DAX,GC,Z,I,NOK,DJ600` | 90.0% | 100.0% |
| 2009 | `IBEX35,ZM,JGB,HG,BRE,RB,CHF,NQ,SB,GC,GBL,HSI,KE,C,SSG,PA,SX7E,SI,ZN,ZW,RS,ZR,ZC,EMD,YM,ES,HE,GF,DA,LE,ZO,ZS,NZD,ZL,RY,Z,DAX,DJ600,CT,KC,RF,EUR,SEK,NOK,M6A,DX,R,ZT,ZB,ZF,GBS,I,IBXXIBHY` | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,RY,CT,SSG,PA,YM,R,ZM,RS,HSI,ZW,ZS,KE,SX7E,CHF,LE,HE,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,I,SEK,NOK` | 81.5% | 83.0% | `IBEX35,ZM,JGB,HG,BRE,RB,CHF,NQ,SB,GC,GBL,HSI,KE,C,SSG,PA,SX7E,SI,ZN,ZW,RS,ZR,ZC,EMD,YM,ES,HE,GF,DA,LE,ZO,ZS,NZD,ZL,RY,Z,DAX,DJ600,CT,KC,RF,EUR,SEK,NOK,M6A,DX,R,ZT,ZB,ZF,GBS,I` | 98.1% | 100.0% |
| 2010 | `IBEX35,JGB,ZM,CHF,RB,SB,GBM,NQ,BRE,HE,HSI,KE,ZT,SSG,CL,EMD,C,RY,ZW,ZC,RS,ZB,GBS,D,SX7E,YM,ZS,ZN,R,ZF,Z,ES,GBL,DAX,DJ600,ZL,NZD,ZR,DX,CT,GF,KC,LE,DA,M6E,NOK,SEK,I,SI,RF,M6A` | `IBEX35,NZD,ZC,JGB,RB,HG,GF,SI,ZT,NQ,BRE,RY,CT,SSG,PA,YM,R,ZM,RS,HSI,ZW,ZS,KE,SX7E,CHF,LE,HE,ZR,ZO,ZL,DAX,Z,ZB,DJ600,EMD,W,ZF,ES,ZN,GC,EUR,DX,I,SEK,NOK` | 68.4% | 75.9% | `IBEX35,JGB,ZM,CHF,RB,SB,GBM,NQ,BRE,HE,HSI,KE,ZT,SSG,CL,EMD,C,RY,ZW,ZC,RS,ZB,GBS,D,SX7E,YM,ZS,ZN,R,Z,DAX,ZF,ES,GBL,DJ600,ZL,NZD,ZR,DX,EUR,GF,LE,CT,DA,SEK,KC,NOK,I,SI,RF,ZO,M6A` | 94.3% | 100.0% |
| 2011 | `IBEX35,SI,ZF,HE,JGB,ZW,NQ,SB,GBS,C,ZM,PA,EMD,SX7E,KE,D,GF,ZC,LE,YM,ES,HSI,RS,ZS,ZR,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,GBM,ZB,R,ZN,BRE,CHF,M6E,NZD,RY,DX,RB,RF,ZO,GC,SEK` | `IBEX35,SI,ZF,HE,JGB,ZW,NQ,SB,GBS,C,ZM,PA,EMD,SX7E,KE,D,GF,ZC,LE,YM,ES,HSI,RS,ZR,ZS,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,GBM,ZB,R,ZN,BRE,CHF,DX,M6E,NZD,RY,RB,RF,ZO,GC,SEK` | 100.0% | 100.0% | `IBEX35,SI,ZF,HE,JGB,ZW,NQ,SB,GBS,C,ZM,PA,EMD,SX7E,KE,D,GF,ZC,LE,YM,ES,HSI,RS,ZR,ZS,SSG,DAX,Z,ZL,DJ600,KC,CT,GBL,ZT,GBM,ZB,R,ZN,BRE,CHF,DX,E7,NZD,RY,RB,RF,ZO,GC,SEK` | 96.0% | 100.0% |
| 2012 | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,ZM,HG,GF,MNTPX,BTP,ZW,KE,ZF,BRE,E7,W,GBS,RY,ZC,RS,SX7E,YM,C,D,ZS,KC,CT,ZL,LE,R,UB,ZN,GBL,ZB,ZR,GBM,SSG,DX,Z,HSI,DAX,DJ600,RB,GC,NQ,ZO` | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,ZM,HG,GF,KE,BRE,RY,YM,NQ,SB,C,E7,EMD,ZC,ZW,D,RS,SX7E,MNTPX,KC,CT,ZS,ZR,SSG,ZL,ES,HSI,LE,DX,MCL,GC,DAX,Z,ZO,R,BTP,ZF,DJ600,UB,DJSD` | 77.8% | 100.0% | `IBEX35,SI,HE,JGB,NZD,ZT,MXEA,CHF,ZM,HG,GF,KE,BRE,RY,YM,NQ,SB,C,E7,EMD,ZC,ZW,D,RS,SX7E,MNTPX,KC,CT,ZS,ZR,SSG,ZL,ES,HSI,LE,DX,MCL,GC,DAX,Z,DJ600,ZF,ZO,R,GBS,ZN,GBL` | 81.1% | 88.0% |
| 2013 | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,GC,BRE,MNTPX,CHF,MXEA,GF,BTS,GBL,N225M,ZW,SX7E,KE,RS,ZC,ZS,ZF,RY,NQ,YM,UB,KC,SB,D,E7,LE,ZL,CT,SSG,CC,HSI,DAX,DJ600,Z,R,GBS,ZN,GBM,ZB,ES,EMD,DX,RB,PA,ZO,DA` | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,GC,BRE,MNTPX,CHF,MXEA,GF,BTS,GBL,N225M,ZW,SX7E,KE,RS,ZC,ZS,ZF,RY,NQ,YM,UB,KC,SB,D,E7,C,CT,LE,ZL,SSG,HSI,DAX,DJ600,Z,R,GBS,ZN,ES,EMD,GBM,ZB,DX,RB,ZR,PA,ZO` | 92.9% | 100.0% | `IBEX35,ZM,BTP,SI,NZD,JGB,XINA50,HE,ZT,GC,BRE,MNTPX,CHF,MXEA,GF,BTS,GBL,N225M,ZW,SX7E,KE,RS,ZC,ZS,ZF,RY,NQ,YM,UB,KC,SB,D,E7,C,CT,LE,ZL,SSG,HSI,DAX,DJ600,Z,R,GBS,ZN,ES,EMD,GBM,ZB,DX,RB,ZR,PA,ZO` | 92.9% | 100.0% |
| 2014 | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,KE,PA,Z,M6E,RY,RS,SX7E,ZC,DJ600,MNTPX,DAX,ES,YM,NQ,HSI,DJSD,N225M,SSG,SB,D,GF,HE,C,KC,ZS,CT,LE,ZL,EMD,DX,UB,ZT,GBL,ZF,BTP,R,GBM,ZN,GBS,ZB,BTS` | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,KE,PA,Z,SX7E,MNTPX,RS,ZC,BTS,ZT,UB,GBL,BTP,R,N225M,DAX,DJ600,HSI,NQ,YM,ES,DJSD,SSG,ZF,GBM,GBS,ZN,ZB,ZL,ZS,SB,D,GF,HE,C,KC,CT,LE,EMD,DX,E7,RY` | 96.4% | 100.0% | `IBEX35,ZM,OAT,GC,RB,NZD,XINA50,ZW,CHF,MXEA,SI,JGB,BRE,KE,PA,Z,SX7E,MNTPX,RS,ZC,BTS,ZT,UB,GBL,BTP,R,N225M,DAX,DJ600,HSI,NQ,YM,ES,DJSD,SSG,ZF,GBM,GBS,ZN,ZB,ZL,ZS,SB,D,GF,HE,C,KC,CT,LE,EMD,DX,E7,RY` | 96.4% | 100.0% |
| 2015 | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,SSG,DJSD,RS,ZC,BTS,GBL,ZS,EMD,M6E,ZL,R,ZF,BTP,GBM,ZN,ZB,GBS,DX,SB,D,CT,KC,CC` | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,DJSD,SSG,RS,ZC,BTS,GBL,ZS,EMD,ZL,R,BTP,ZF,GBM,ZN,GBS,ZB,DX,E7,SB,D,CT,KC,C` | 92.7% | 100.0% | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,DJSD,SSG,RS,ZC,BTS,GBL,ZS,EMD,E7,ZL,R,ZF,BTP,GBM,ZN,ZB,GBS,DX,RY,SB,D,CT,KC,C` | 91.1% | 100.0% |
| 2016 | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,NQ,XINA50,OAT,PA,ZM,DJUSRE,MNTPX,KE,RS,ES,SMI,DAX,HSI,GF,N225M,MXEA,Z,SX7E,ESTX50,DJ600,SSG,ZS,SX6P,ZC,LE,EMD,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,IXE,W,CT,BTS,SCI,CC,KC,D` | `IBEX35,OAT,LE,GC,RB,UC,ZM,JGB,YM,MXEA,HE,BRE,CHF,ZW,UB,ES,MNTPX,ZT,XINA50,NZD,KE,GF,SX7E,IXE,N225M,Z,HSI,DAX,NQ,DJ600,DJSD,SSG,RS,ZC,BTS,GBL,ZS,EMD,ZL,R,BTP,ZF,GBM,ZN,GBS,ZB,DX,E7,SB,D,CT,KC,C` | 71.4% | 71.4% | `IBEX35,RB,CHF,UB,ZW,GC,HE,YM,CL,SI,JGB,NQ,XINA50,OAT,PA,ZM,DJUSRE,MNTPX,KE,RS,ES,SMI,DAX,HSI,GF,N225M,MXEA,Z,SX7E,ESTX50,DJ600,SSG,ZS,SX6P,ZC,LE,EMD,ZT,GBS,BTP,ZB,ZL,GBM,ZF,GBL,R,ZN,IXE,W,CT,BTS,SCI,CC,KC,D` | 100.0% | 100.0% |
| 2017 | `NZD,UC,CHF,DX,BRE,M6E,UB,ZT,BTS,JGB,GBX,BTP,ZB,GBS,ZF,R,GBL,OAT,ZN,GBM` | `NZD,UC,CHF,DX,BRE,E7,UB,ZS,Z,PA,HE,NQ,SCI,ZT,XINA50,ZW,SI,ZM,W,CT,SMI,LE,KE,GC,D,SSG,CC,RS,ZC,MNTPX,HSI,KC,ZL,N225M,MXEA,SX7E,DAX,DJ600,IBEX35,SX6P,ESTX50,GF,RB,OAT,GBM,GBS,BTP,ZF,R,ZB,GBX,GBL,ZN,JGB,RY,BTS` | 33.3% | 100.0% | `NZD,UC,CHF,DX,BRE,UB,ZT,BTS,JGB,GBX,BTP,GBS,ZB,ZF,R,GBM,OAT,ZN,GBL` | 95.0% | 100.0% |
| 2018 | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,M6E,GBX,BTP,MXEA,Z,SX6P,DAX,HSI,DJSD,DJ600,SSG,ESTX50,MNTPX,RS,IXE,DJUSRE,RTY,ES,UB,R,GBL,ZF,OAT,GF,ZB,ZL,ZN,W,CT,CC,D,KC` | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,BTP,GBX,MXEA,DAX,HSI,SX6P,DJ600,Z,MNTPX,ESTX50,SSG,DJSD,RS,IXE,RTY,DJUSRE,N225M,UB,GBL,ZF,R,OAT,ZB,GF,ZN,ZL,W,CC,CT,D,KC` | 95.2% | 100.0% | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,BTP,GBX,MXEA,DAX,HSI,SX6P,DJ600,Z,MNTPX,DJSD,ESTX50,SSG,RS,IXE,DJUSRE,RTY,N225M,UB,GBL,ZF,R,OAT,ZB,GF,ZN,ZL,W,CC,CT,D,KC` | 95.2% | 100.0% |
| 2019 | `YM,SI,HE,BRE,GBX,MCL,NQ,BTS,CHF,HG,KE,DJUSRE,UB,CC,SCI,NZD,PA,ZC,D,RS,IBEX35,Z,IXV,IXR,SIXRE,IXU,M6E,CT,DAX,ZT,BTP,DX,JGB,W,LE,KC,GBS,TN,ZL,ZW,ZM,ZS,ZF,XINA50,SX7E,SX6P,DJ600,SMI,HSI,DJSD,ESTX50,SSG,MNTPX,GBM,R,ZN,GBL,OAT,ZB,GF,GC` | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,BTP,GBX,MXEA,DAX,HSI,SX6P,DJ600,Z,MNTPX,ESTX50,SSG,DJSD,RS,IXE,RTY,DJUSRE,N225M,UB,GBL,ZF,R,OAT,ZB,GF,ZN,ZL,W,CC,CT,D,KC` | 83.3% | 93.5% | `YM,SI,HE,BRE,GBX,MCL,NQ,BTS,CHF,HG,KE,DJUSRE,UB,CC,SCI,NZD,PA,ZC,D,RS,IBEX35,Z,DAX,CT,ZT,BTP,JGB,W,DX,E7,LE,KC,ZL,ZW,ZM,GBS,ZS,TN,ZF,SX7E,SX6P,XINA50,DJ600,SMI,DJSD,HSI,ESTX50,SSG,GBM,MNTPX,R,N225M,MXEA,ZN,GBL,OAT,ZB,GF,RTY,IXV,GC` | 87.7% | 100.0% |
| 2020 | `IBEX35,TN,DX,PA,ZS,ES,HE,SCI,NZD,ZF,XINA50,BRE,CC,NQ,CT,SI,ZC,GBX,YM,DJUSRE,ZW,CHF,DJ600,D,IXE,RS,Z,ZL,SMI,W,LE,HSI,ESTX50,SX7E,JGB,ZT,UB,OAT,R,ZN,BTP,MNTPX,KE,GBL,KC,ZM,N225M,TSEMOTHR,BTS,ZB,SX6P,SSG,MXEA,DAX,GBS,GF,GBM,GC,RB,HG` | `IBEX35,NZD,TN,ZS,PA,RB,HE,YM,SCI,GBM,CHF,ZW,JGB,XINA50,GC,BRE,LE,HG,NQ,BTS,KE,ZM,ZT,SX7E,ZC,SI,GBS,SMI,DX,BTP,GBX,MXEA,DAX,HSI,SX6P,DJ600,Z,MNTPX,ESTX50,SSG,DJSD,RS,IXE,RTY,DJUSRE,N225M,UB,GBL,ZF,R,OAT,ZB,GF,ZN,ZL,W,CC,CT,D,KC` | 93.5% | 93.5% | `IBEX35,TN,DX,PA,ZS,ES,HE,SCI,NZD,ZF,XINA50,BRE,CC,NQ,DJUSRE,CHF,CT,GBX,ZW,SI,Z,ZL,D,ZC,IXE,YM,ESTX50,RS,GBL,W,ZT,R,UB,KE,ZM,BTP,ZN,HSI,LE,SX7E,SMI,BTS,N225M,KC,MNTPX,SX6P,DJ600,SSG,TSEMOTHR,JGB,MXEA,DAX,ZB,GBS,GF,OAT,GBM,GC,RB,HG` | 100.0% | 100.0% |
| 2021 | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,DJUSRE,ZM,N225M,CHF,IXE,C,SMI,KE,HSI,SX7E,TSEMOTHR,RS,IBEX35,SX6P,Z,MXEA,ZC,CT,XINA50,DJ600,RTY,MNTPX,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,TN,R,GBX,GBM,ZT,ZN,BTS,ZB,SI` | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,ZM,IXE,DJUSRE,SMI,CHF,N225M,C,HSI,KE,SX7E,RS,ZC,IBEX35,CT,DJ600,SX6P,TSEMOTHR,XINA50,Z,RTY,MNTPX,MXEA,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,R,TN,GBX,GBM,ZT,ZN,BTS,ZB,SI` | 100.0% | 100.0% | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,BRE,ZW,SSG,SB,N225M,CT,ZM,IBEX35,KE,RS,CHF,KC,GF,NZD,IXE,DJUSRE,SMI,YM,TSEMOTHR,C,HSI,SX6P,SX7E,ZC,RTY,MXEA,CL,OAT,XINA50,DJ600,GBS,GC,LE,DAX,ZF,Z,MNTPX,TN,GBL,GBX,GBM,ZT,ZN,ZB,R` | 85.0% | 100.0% |
| 2022 | `NQ,ZB,PA,KE,NZD,RB,BTS,ZM,CHF,SCI,HSI,LE,CC,BTP,JGB,SMI,KC,ZC,GF,XINA50,DX,BRE,ZW,HE,RS,GC,ZS,IBEX35,N225M,OAT,TN,Z3N,GBS,W,D,GBX,GBL,UB,ZT,GBM,R,ZF,MNTPX,SSG,TSEMOTHR,MXEA,SX6P,ESTX50,Z,SX7E,DJSD,TWN,ZL,ZO,CT,SI,HG,SONIA.N,ES,DJUSRE,IXE,RTY` | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,ZM,IXE,DJUSRE,SMI,CHF,N225M,C,HSI,KE,SX7E,RS,ZC,IBEX35,CT,DJ600,SX6P,TSEMOTHR,XINA50,Z,RTY,MNTPX,MXEA,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,R,TN,GBX,GBM,ZT,ZN,BTS,ZB,SI` | 73.5% | 75.8% | `NQ,ZB,PA,KE,NZD,RB,BTS,ZM,CHF,SCI,HSI,LE,BRE,CC,SMI,JGB,BTP,KC,XINA50,ZW,HE,GF,ZC,GC,RS,ZS,DX,IBEX35,N225M,OAT,TN,GBS,Z3N,W,D,GBX,GBL,UB,ZT,GBM,R,ZF,MNTPX,TSEMOTHR,SSG,SX6P,MXEA,ESTX50,Z,SX7E,TWN,DAX,ZL,CT,SI,HG,ES,DJUSRE,IXE,RTY` | 93.7% | 100.0% |
| 2023 | `NQ,ZL,JGB,PA,ETHUSDRR,MCL,KC,HSI,SONIA.N,SCI,LE,RS,BTP,HE,HG,ZM,GF,W,UB,N225M,ZW,ZC,SI,ZS,CT,D,BTS,GBX,KE,R,TN,CC,TSR20,GBS,OAT,ZB,ZT,GBL,GBM,ZF,ZN,GC,Z,TWN,XIN0I,MNTPX,SX7E,IBEX35,SSG,SMI,ESTX50,SX6P,MXEA,DAX,TSEMOTHR,RTY,DJUSRE,DX,IXE,ES` | `NQ,UB,PA,ZS,DX,HE,SCI,ESTX50,BTP,UC,ZW,SSG,NZD,BRE,KC,W,YM,ZM,IXE,DJUSRE,SMI,CHF,N225M,C,HSI,KE,SX7E,RS,ZC,IBEX35,CT,DJ600,SX6P,TSEMOTHR,XINA50,Z,RTY,MNTPX,MXEA,CL,OAT,ES,GC,GBS,ZF,JGB,GBL,R,TN,GBX,GBM,ZT,ZN,BTS,ZB,SI` | 68.1% | 70.6% | `NQ,ZL,JGB,PA,ETHUSDRR,MCL,KC,HSI,SONIA.N,SCI,LE,RS,BTP,HE,HG,ZM,GF,W,UB,N225M,ZW,ZC,SI,ZS,CT,D,BTS,GBX,KE,R,TN,CC,TSR20,GBS,OAT,ZB,ZT,GBL,GBM,ZF,ZN,GC,Z,TWN,XIN0I,MNTPX,SX7E,IBEX35,SSG,SX6P,SMI,TSEMOTHR,XINA50,MXEA,ESTX50,DX,IXE,DJUSRE,ES,YM` | 93.5% | 100.0% |
| 2024 | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,Z,TWN,NZD,CHF,HE,IBEX35,RS,SONIA.N,MNTPX,ZM,ZW,ZL,ZC,N225M,HSI,SX7E,MCA,KE,CT,ESTX50,SMI,SSG,KC,DAX,MXEA,BTP,GBX,XIN0I,HHI.HK,GBS,ZB,BTS,GBM,TN,R,ZT,OAT,GBL,ZN,ZF,RB,BRE,GF,D,CC,LE,TSR20,GC,SI` | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,Z,TWN,NZD,CHF,HE,IBEX35,RS,SONIA.N,MNTPX,ZM,ZW,ZL,ZC,N225M,HSI,SX7E,MCA,KE,CT,ESTX50,SMI,SSG,KC,DAX,MXEA,BTP,GBX,XIN0I,HHI.HK,GBS,ZB,BTS,GBM,TN,R,ZT,OAT,GBL,ZN,ZF,D,MCL,BRE,GF,CC,LE,TSR20,SI,GC,YM,DJUSRE,IXE,RTY` | 90.5% | 100.0% | `NQ,UB,ZS,DX,PA,SB,SCI,JGB,UC,Z,TWN,NZD,CHF,HE,IBEX35,RS,SONIA.N,MNTPX,ZM,ZW,ZL,ZC,N225M,HSI,SX7E,MCA,KE,CT,ESTX50,SMI,SSG,KC,DAX,SX6P,MXEA,BTP,GBX,XINA50,GBS,ZB,GBM,BTS,ZT,TN,R,GBL,OAT,ZN,ZF,RB,BRE,GF,D,CC,TSR20,SI,GC,LE` | 93.3% | 100.0% |
| 2025 | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,GBX,BZ,ZC,JGB,HE,SB,RS,Z,MCA,ZM,YM,ZW,N225M,CC,XIN0I,LE,KE,D,GBS,ZS,CT,XINA50,SX7E,BTP,HSI,DA,TSR20,SMI,TWN,TSEMOTHR,ES,SSG,SX6P,DJUSRE,SIXM,IXU,OAT,BTS,R,ZT,GBL,TN,GBM,ZB,ZF,Z3N,SI` | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,GBX,BZ,ZC,JGB,HE,SB,RS,Z,MCA,ZM,YM,ZW,N225M,CC,XIN0I,KE,LE,D,GBS,ZS,CT,BTP,TSR20,SX7E,XINA50,HSI,SMI,TWN,ES,SSG,DJUSRE,SIXM,SX6P,ESTX50,TSEMOTHR,R,BTS,OAT,ZT,GBL,TN,GBM,ZB,ZF,Z3N,SI` | 95.2% | 100.0% | `NQ,UB,ZL,HG,ETHUSDRR,DX,PA,KC,IBEX35,SCI,SONIA.N,GF,HHI.HK,GC,GBX,BZ,ZC,JGB,HE,SB,RS,Z,MCA,ZM,YM,ZW,N225M,CC,XIN0I,LE,KE,D,GBS,ZS,BTP,CT,DA,TSR20,SX7E,HSI,XINA50,SMI,TWN,SSG,TSEMOTHR,ES,SX6P,DJUSRE,SIXM,IXU,OAT,BTS,R,ZT,GBL,TN,GBM,ZB,ZF,Z3N,SI` | 100.0% | 100.0% |
| 2026 | `SI,NZD,NQ,ZS,JGB,RB,HHI.HK,ETHUSDRR,SONIA.N,KC,ZW,GBX,SCI,PA,HE,YM,GF,RS,SB,ZM,D,IXE,DJUSRE,SIXM,IXU,IXV,IXR,ZC,KE,GC,ES,CT,C,TSR20,EMD,RTY,ZL,SIXRE,TWN,HG,IXI,HSI,BTP,LE,GBS,UB,GBL,BTS,OAT,ZB,GBM,R,TN,ZT,ZN,N225M,Z3N,SSG,IBEX35,SX7E` | `SI,NZD,NQ,ZS,JGB,RB,HHI.HK,ETHUSDRR,SONIA.N,KC,ZW,GBX,SCI,PA,HE,YM,GF,RS,SB,ZM,D,IXE,DJUSRE,SIXM,CC,ZC,KE,GC,ES,CT,IXU,ZL,TSR20,EMD,IXR,RTY,TWN,IXV,HG,HSI,IBEX35,BTP,UB,GBS,N225M,GBL,OAT,ZB,BTS,GBM,R,TN,ZT,ZN,Z3N,SMI,SX7E,SSG,XIN0I` | 88.9% | 100.0% | `SI,NZD,NQ,ZS,JGB,RB,SONIA.N,HHI.HK,D,SCI,DX,HE,PA,GBX,CC,ZW,GF,YM,TWN,HG,SB,RS,KC,CT,CHF,BTP,ZM,KE,ZC,ES,RTY,SIXM,IXU,IXV,ZL,NYFANG,IXR,IXI,EMD,TSR20,DJUSRE,IXB,GBS,IXE,SIXRE,IBEX35,HSI,MCA,BTS,UB,OAT,GBL,R,GBM,ZB,ZT,TN,Z3N,ZF,GC,LE` | 80.6% | 100.0% |



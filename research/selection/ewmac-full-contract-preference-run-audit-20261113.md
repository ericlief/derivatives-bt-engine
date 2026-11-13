# EWMAC full-contract preference run audit: November 13, 2026

## Scope and conclusion

This audit compares the two completed multi-speed EWMAC fixed-cap runs around
the full-contract preference change:

- pre-change run `20261113_140831`, generated after commit `c5d6d55` and before
  `99d3398`;
- latest run `20261113_153041`, generated immediately after `99d3398`.

Both runs use the identical scenario
`tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone`:
$75,000 fixed sizing capital, 20% target volatility, yearly selection,
full-contract preference, a 2.5-to-5.5 preferred contract band, and the same
class, subclass, margin, hysteresis, and roll settings. The comparison is
therefore compatible. It is a comparison of two saved backtests, not a rerun.

The ZC and ZW observations in the late 1970s are **not** a recurrence of the
old ZM suppression bug. Their micro siblings failed the causal 1% per-trade
cost-SR entry gate on the relevant review dates. A below-band full contract is
allowed when its micro sibling is hard-ineligible. The later transition from
ZW to MZW occurs as soon as MZW's historical cost falls below the gate.

The preference change is material but not catastrophic for the full saved
history. Latest ending net equity is $914,232.12 versus $928,010.79 before the
change, a $13,778.67 reduction. Annualized arithmetic mean falls 0.25
percentage points and Sharpe falls from 1.098 to 1.067, while maximum drawdown
improves slightly from 10.04% to 9.91%. Because yearly membership hysteresis
makes selection path-dependent, the route fix cascades beyond the first micro
substitution: every one of the 54 annual books differs from the pre-change
book. This is a policy-level comparison, not a pure ZM-versus-MZM attribution.

## ZC and ZW route audit

The selector first applies the historical per-trade cost gate. It then applies
the full-contract preference only among feasible family routes. The preferred
lower band is 2.5 continuous contracts; `max_con` is the executable half-up
rounded quantity. A full route below 2.5 is replaced by a feasible micro, but
not by a micro that already failed the 1% cost gate.

| Review | Selected route | Selected max position | Selected max con | Micro checked | Micro cost SR per trade | 1% entry gate | Result |
|---|---|---:|---:|---|---:|---|---|
| 1977-01-03 | ZC | 1.2124 | 1 | MZC | 1.2077% | Fail | ZC remains available |
| 1978-01-03 | ZC | 1.5183 | 2 | MZC | 1.4340% | Fail | ZC remains available |
| 1979-01-02 | ZC | 1.5228 | 2 | MZC | 1.6641% | Fail | ZC remains available |
| 1979-01-02 | ZW | 0.8795 | 1 | MZW | 1.0666% | Fail | ZW remains available |
| 1980-01-02 | ZC | 0.8295 | 1 | MZC | 1.0997% | Fail | ZC remains available |
| 1980-01-02 | MZW | 4.3025 | 4 | MZW | 0.6825% | Pass | MZW replaces ZW |

The cost inputs above are reproduced from the last saved annual dollar
volatility strictly before each review and the saved one-contract trade cost.
For example, MZC at the 1977 review uses $218.60 annual dollar volatility and
$2.64 per trade, giving `2.64 / 218.60 = 1.2077%`. MZW at the 1979 review uses
$287.82 and $3.07, giving 1.0666%; at the 1980 review its annual dollar
volatility rises to $449.80 and the same $3.07 cost falls to 0.6825%.

This per-trade eligibility field is distinct from `ann_cost_sr` in an accepted
allocation row, which annualizes trading and roll costs using the rule's
turnover. The late-1970s full routes should not be diagnosed from rounded
`max_con` alone.

The accepted route sequence also agrees with this explanation. The latest run
uses MZW in 1974-1975, replaces it with the alternate wheat route YW in 1976,
uses ZC in 1977-1980, adds ZW in 1979 while MZW is cost-ineligible, and changes
ZW to MZW in 1980. MZC is not selected until later periods when it passes the
causal gate and wins the portfolio competition.

## Full-history performance

Both curves cover 1970-02-03 through 2026-11-12, with the first valid return on
1973-01-02 and a complete active book through 2026-11-12. Fixed-cap return
fields are daily contributions against the fixed $75,000 sizing capital; the
annualized mean is arithmetic and is not a reinvested CAGR. Fixed-cap adjusted
Calmar is annualized arithmetic mean divided by absolute maximum drawdown.
Drawdown is calculated from saved net equity divided by its running peak.

| Metric | Pre-change `140831` | Latest `153041` | Latest minus pre-change |
|---|---:|---:|---:|
| Valid return observations | 14,053 | 13,993 | -60 |
| Ending net equity | $928,010.79 | $914,232.12 | -$13,778.67 |
| Total net P&L | $853,010.80 | $839,232.12 | -$13,778.67 |
| Annualized arithmetic mean | 20.72% | 20.47% | -0.25 pp |
| Annualized volatility | 18.88% | 19.18% | +0.31 pp |
| Sharpe | 1.098 | 1.067 | -0.030 |
| Fixed-cap adjusted Calmar | 2.063 | 2.065 | +0.002 |
| Maximum drawdown | -10.04% | -9.91% | +0.13 pp |
| Maximum drawdown dollars | -$17,855.71 | -$17,306.16 | +$549.55 |
| Drawdown peak to trough | 1974-02-26 to 1974-04-11 | 1974-02-26 to 1974-04-11 | unchanged dates |

There are 13,989 dates where both runs have valid returns. On that common-date
sample, pre-change mean/volatility/Sharpe are 20.83% / 18.92% / 1.101 and the
latest values are 20.48% / 19.19% / 1.067. Daily net-return correlation is
0.919 and sign agreement is 87.25%. The 64 pre-only and four latest-only valid
dates arise from the trading calendars represented by different selected
routes; neither run has an invalid return while an active book is present.

## P&L and cost reconciliation

Saved costs are negative cash-flow columns; this table displays their positive
fee magnitudes. Net P&L is gross P&L less trade and roll fees.

| Metric | Pre-change | Latest | Latest minus pre-change |
|---|---:|---:|---:|
| Gross P&L | $918,053.50 | $910,289.74 | -$7,763.76 |
| Trade fees | $42,548.23 | $46,113.25 | +$3,565.02 |
| Roll fees | $22,490.89 | $24,939.16 | +$2,448.27 |
| Total fees | $65,039.12 | $71,052.41 | +$6,013.29 |
| Net P&L | $853,010.80 | $839,232.12 | -$13,778.67 |
| Fees / gross P&L | 7.08% | 7.81% | +0.72 pp |
| Fees / net movement | 7.62% | 8.47% | +0.84 pp |

The instrument-level reconciliation residual `net - (gross - fees)` is
-$3.58 before the change and -$5.20 latest, consistent with summing public
rows rounded independently. Portfolio-curve gross/net sums differ from the
instrument sums by -$0.65/-$0.51 pre-change and -$0.02/+$0.08 latest. There
are no duplicate `(symbol, date)` cash keys, no duplicate portfolio dates, no
active invalid P&L rows, and no invalid-return dates with an active book.

The $6,013 increase in fees accounts for about 44% of the $13,779 reduction in
net P&L; the $7,764 gross-P&L reduction accounts for most of the balance. Costs
are material to the comparison but do not explain a loss-making strategy: the
latest run still earns $839,232 net on fixed sizing over the saved history.

Latest largest positive instrument contributors are SB ($80.37k), SI
($50.04k), M6B ($47.78k), QG ($41.28k), and I ($41.27k). The largest negative
contributors are Z (-$10.87k), MET (-$7.28k), SX7E (-$3.27k), V2TX
(-$3.15k), and CSC (-$3.04k). Relative to pre-change, the largest positive
instrument deltas include MSF (+$32.73k), MHG (+$30.72k), MGC (+$30.43k),
MCD (+$21.90k), and MCL (+$18.79k); the largest negative deltas include CAD
(-$45.54k), QO (-$33.92k), QM (-$31.78k), QC (-$24.33k), and J7 (-$19.58k).
At asset-class level, the largest reductions are metals (-$20.30k) and FX
(-$17.51k), partly offset by bonds (+$12.64k), equities (+$4.71k), and
agriculture (+$4.01k).

For the four grain routes discussed directly, the latest run shifts net P&L
from ZM to MZM ($0 versus $14.12k), reduces ZC from $8.73k to $3.09k while MZC
adds $1.19k, and reduces ZW from $2.85k to -$0.31k while MZW rises from
-$0.30k to $1.49k. These are route contributions, not isolated causal effects,
because weights and the rest of the book also change.

## Selection, eligibility, and concentration

The current snapshot has 177 candidates, 176 forecast-eligible markets, and
109 selection-eligible markets in both runs. M1MSA is the sole forecast
exclusion in both, for insufficient valid forecast observations. This confirms
that the current forecast universe did not change across the two saved runs.

Historical books do change broadly because the selector is greedy and yearly
membership hysteresis carries each accepted book into the next review. All 54
annual books differ. Average active book size rises from 17.57 to 18.17;
minimum/maximum book sizes move from 7/24 to 7/26. Average candidate count is
75.17 pre-change and 75.30 latest. Coverage is satisfied in 32 of 54 reviews
pre-change and 31 of 54 latest. The latest 2026 review contains 23 instruments
from 168 candidates, but remains explicitly uncovered with one class and one
subclass deficit (`coverage_unmet_hard_feasibility_gate`); the minimum book
count itself is met.

The early route changes are wider than ZM alone. In 1973 the only book
difference is MZM replacing ZM. By 1974, MZW and MSF replace ZC and CHF. By
1977, five symbols differ on each side; by 2007, 12 latest additions replace
nine pre-change members. This path dependence explains large positive and
negative calendar-year deltas and means the terminal difference should be
read as the performance of the corrected selection policy.

## Calendar-year OOS slices

Each cell is **return / within-year max drawdown / Sharpe / fixed-cap adjusted
Calmar / gross P&L / positive fees**. Return is the sum of that calendar year's
saved fixed-cap daily contributions against $75,000. Drawdown starts from the
prior year-end net equity baseline. These are OOS slices of the saved
walk-forward backtests, not separately refitted annual runs. 2026 is partial
through November 12.

| Year | Pre-change | Latest | Return delta |
|---:|---:|---:|---:|
| 1973 | 82.8% / -6.8% / 4.10 / 12.43 / $62.98k / $880 | 80.5% / -7.4% / 3.75 / 11.05 / $61.32k / $960 | -2.3 pp |
| 1974 | 63.3% / -10.0% / 2.66 / 6.38 / $48.57k / $1.06k | 61.3% / -9.9% / 2.60 / 6.26 / $47.08k / $1.09k | -2.0 pp |
| 1975 | -0.6% / -7.3% / -0.05 / -0.08 / $797 / $1.22k | 1.3% / -6.8% / 0.11 / 0.19 / $2.27k / $1.29k | +1.9 pp |
| 1976 | 10.3% / -3.8% / 0.84 / 2.71 / $8.67k / $975 | 8.0% / -4.5% / 0.66 / 1.78 / $7.14k / $1.16k | -2.3 pp |
| 1977 | 42.4% / -2.4% / 2.79 / 17.75 / $33.41k / $1.60k | 44.2% / -2.5% / 2.83 / 18.04 / $35.21k / $2.07k | +1.8 pp |
| 1978 | 67.2% / -5.3% / 2.23 / 12.85 / $51.33k / $961 | 59.5% / -7.1% / 2.02 / 8.53 / $46.14k / $1.50k | -7.6 pp |
| 1979 | 100.9% / -2.6% / 4.14 / 39.00 / $76.60k / $887 | 85.6% / -3.1% / 3.49 / 28.02 / $65.20k / $1.03k | -15.4 pp |
| 1980 | 38.7% / -6.0% / 1.61 / 6.51 / $29.47k / $468 | 35.3% / -5.9% / 1.49 / 6.10 / $27.16k / $652 | -3.3 pp |
| 1981 | 53.5% / -3.9% / 1.74 / 13.87 / $41.14k / $1.03k | 61.4% / -3.7% / 2.03 / 16.70 / $47.33k / $1.27k | +7.9 pp |
| 1982 | 35.3% / -3.1% / 1.41 / 11.39 / $27.31k / $863 | 35.9% / -3.1% / 1.45 / 11.75 / $28.04k / $1.08k | +0.7 pp |
| 1983 | -5.4% / -2.7% / -0.62 / -2.02 / -$3.55k / $508 | -4.9% / -2.9% / -0.54 / -1.72 / -$3.06k / $576 | +0.6 pp |
| 1984 | 41.6% / -1.8% / 1.95 / 23.65 / $32.41k / $1.18k | 38.7% / -2.0% / 1.75 / 19.77 / $30.22k / $1.21k | -3.0 pp |
| 1985 | 19.3% / -2.8% / 1.08 / 6.86 / $15.53k / $1.06k | 23.2% / -2.5% / 1.31 / 9.32 / $18.61k / $1.22k | +3.9 pp |
| 1986 | 32.8% / -2.7% / 1.38 / 12.13 / $25.77k / $1.13k | 30.8% / -2.8% / 1.31 / 10.85 / $24.32k / $1.24k | -2.1 pp |
| 1987 | 37.2% / -2.0% / 1.67 / 18.32 / $28.87k / $986 | 39.3% / -1.9% / 1.74 / 20.55 / $30.62k / $1.16k | +2.1 pp |
| 1988 | -13.9% / -2.1% / -1.22 / -6.56 / -$9.45k / $985 | -13.7% / -2.2% / -1.17 / -6.22 / -$9.06k / $1.18k | +0.3 pp |
| 1989 | 25.6% / -1.8% / 1.20 / 14.27 / $20.88k / $1.68k | 19.8% / -1.7% / 0.95 / 11.76 / $16.55k / $1.69k | -5.8 pp |
| 1990 | 15.8% / -1.5% / 0.95 / 10.54 / $13.08k / $1.22k | 10.9% / -1.6% / 0.66 / 6.68 / $9.51k / $1.32k | -4.9 pp |
| 1991 | 20.7% / -2.3% / 1.20 / 9.05 / $16.96k / $1.45k | 15.8% / -2.7% / 0.95 / 5.88 / $13.51k / $1.67k | -4.9 pp |
| 1992 | -10.1% / -3.1% / -0.63 / -3.21 / -$6.25k / $1.34k | -6.9% / -3.1% / -0.43 / -2.20 / -$3.58k / $1.63k | +3.2 pp |
| 1993 | 21.7% / -2.3% / 1.11 / 9.18 / $17.70k / $1.40k | 27.0% / -2.3% / 1.44 / 11.48 / $21.90k / $1.66k | +5.3 pp |
| 1994 | 5.4% / -1.7% / 0.36 / 3.21 / $5.44k / $1.37k | 6.2% / -1.6% / 0.43 / 3.83 / $6.27k / $1.60k | +0.8 pp |
| 1995 | 21.5% / -1.1% / 1.13 / 19.82 / $17.35k / $1.25k | 23.3% / -1.1% / 1.24 / 21.09 / $19.01k / $1.54k | +1.8 pp |
| 1996 | 10.7% / -2.3% / 0.49 / 4.62 / $9.68k / $1.62k | 10.1% / -2.3% / 0.44 / 4.32 / $9.59k / $2.04k | -0.7 pp |
| 1997 | 8.0% / -1.6% / 0.41 / 4.82 / $7.21k / $1.22k | 12.8% / -1.4% / 0.69 / 8.87 / $11.02k / $1.44k | +4.8 pp |
| 1998 | 41.3% / -1.5% / 2.03 / 28.52 / $32.72k / $1.77k | 48.0% / -1.4% / 2.44 / 34.33 / $38.18k / $2.21k | +6.7 pp |
| 1999 | -37.4% / -4.8% / -1.96 / -7.73 / -$26.56k / $1.45k | -26.3% / -3.8% / -1.33 / -6.79 / -$17.91k / $1.80k | +11.1 pp |
| 2000 | 29.1% / -1.0% / 1.68 / 29.36 / $23.12k / $1.28k | 34.7% / -1.2% / 1.96 / 27.70 / $27.50k / $1.51k | +5.5 pp |
| 2001 | -5.0% / -2.5% / -0.24 / -1.99 / -$2.28k / $1.46k | -3.3% / -2.4% / -0.16 / -1.38 / -$872 / $1.62k | +1.7 pp |
| 2002 | 21.1% / -1.5% / 1.25 / 14.34 / $16.98k / $1.18k | 21.6% / -1.8% / 1.12 / 11.52 / $17.72k / $1.55k | +0.5 pp |
| 2003 | 50.8% / -2.5% / 2.13 / 19.82 / $39.55k / $1.48k | 53.5% / -2.6% / 2.24 / 20.31 / $41.84k / $1.69k | +2.8 pp |
| 2004 | 27.8% / -1.9% / 1.14 / 14.47 / $22.05k / $1.21k | 36.3% / -1.2% / 1.45 / 28.56 / $28.69k / $1.44k | +8.6 pp |
| 2005 | 24.4% / -0.7% / 1.43 / 32.20 / $19.86k / $1.52k | 31.4% / -0.8% / 1.61 / 38.67 / $25.02k / $1.49k | +6.9 pp |
| 2006 | 59.9% / -1.9% / 2.52 / 30.56 / $45.98k / $1.06k | 49.4% / -2.2% / 2.13 / 21.85 / $38.12k / $1.04k | -10.5 pp |
| 2007 | 12.2% / -1.7% / 0.52 / 6.89 / $10.25k / $1.14k | -3.4% / -2.0% / -0.16 / -1.64 / -$1.46k / $1.07k | -15.5 pp |
| 2008 | 33.2% / -1.5% / 1.30 / 21.52 / $25.68k / $773 | 54.1% / -1.5% / 1.77 / 36.30 / $41.37k / $828 | +20.8 pp |
| 2009 | -17.0% / -2.6% / -0.82 / -6.39 / -$11.99k / $796 | -6.6% / -2.6% / -0.31 / -2.46 / -$4.19k / $762 | +10.4 pp |
| 2010 | 25.9% / -1.2% / 1.57 / 21.11 / $20.26k / $847 | 18.5% / -1.2% / 1.10 / 15.61 / $14.75k / $896 | -7.4 pp |
| 2011 | 37.9% / -1.1% / 1.87 / 33.24 / $29.25k / $811 | 18.6% / -1.4% / 0.86 / 13.26 / $14.78k / $858 | -19.3 pp |
| 2012 | 0.4% / -1.3% / 0.04 / 0.35 / $1.00k / $671 | -4.4% / -1.3% / -0.36 / -3.29 / -$2.60k / $674 | -4.8 pp |
| 2013 | 17.3% / -1.3% / 1.22 / 13.50 / $14.03k / $1.04k | 7.0% / -0.9% / 0.58 / 7.75 / $6.30k / $1.02k | -10.3 pp |
| 2014 | 28.1% / -0.5% / 1.90 / 51.15 / $22.37k / $1.32k | 18.3% / -0.8% / 1.38 / 22.14 / $14.91k / $1.18k | -9.8 pp |
| 2015 | 5.0% / -1.2% / 0.28 / 3.98 / $5.03k / $1.31k | 12.9% / -1.5% / 0.70 / 8.70 / $11.02k / $1.36k | +7.9 pp |
| 2016 | -5.5% / -1.6% / -0.45 / -3.31 / -$2.89k / $1.25k | -0.7% / -1.8% / -0.05 / -0.38 / $836 / $1.37k | +4.8 pp |
| 2017 | 30.9% / -0.8% / 2.06 / 39.09 / $25.05k / $1.86k | 12.4% / -1.0% / 0.83 / 12.69 / $11.22k / $1.92k | -18.5 pp |
| 2018 | -12.1% / -2.4% / -1.05 / -4.96 / -$7.34k / $1.74k | -2.0% / -2.2% / -0.14 / -0.90 / $235 / $1.77k | +10.1 pp |
| 2019 | -7.8% / -1.5% / -0.74 / -5.07 / -$3.97k / $1.91k | -8.4% / -1.3% / -0.77 / -6.26 / -$4.65k / $1.63k | -0.6 pp |
| 2020 | 29.2% / -0.9% / 1.77 / 30.94 / $23.65k / $1.71k | 24.4% / -1.0% / 1.44 / 22.85 / $19.94k / $1.62k | -4.8 pp |
| 2021 | 17.9% / -1.0% / 0.97 / 18.09 / $14.55k / $1.11k | 23.1% / -0.9% / 1.20 / 25.56 / $18.50k / $1.21k | +5.1 pp |
| 2022 | 24.2% / -1.2% / 1.42 / 17.78 / $19.26k / $1.10k | 23.4% / -1.6% / 1.27 / 13.74 / $18.62k / $1.05k | -0.8 pp |
| 2023 | -9.0% / -1.5% / -0.57 / -5.00 / -$4.95k / $1.81k | 6.0% / -1.0% / 0.35 / 4.84 / $5.99k / $1.50k | +15.0 pp |
| 2024 | -6.8% / -1.1% / -0.46 / -5.24 / -$3.98k / $1.14k | -5.8% / -1.1% / -0.39 / -4.52 / -$3.17k / $1.16k | +1.1 pp |
| 2025 | 8.0% / -0.7% / 0.52 / 9.26 / $6.97k / $999 | -16.0% / -1.5% / -1.49 / -10.78 / -$11.20k / $808 | -24.0 pp |
| 2026 | -11.2% / -1.2% / -1.01 / -10.74 / -$7.47k / $952 | -3.0% / -1.5% / -0.20 / -2.31 / -$1.51k / $718 | +8.3 pp |

The largest annual improvements are 2008 (+20.8 pp), 2023 (+15.0 pp), 1999
(+11.1 pp), 2009 (+10.4 pp), and 2018 (+10.1 pp). The largest reductions are
2025 (-24.0 pp), 2011 (-19.3 pp), 2017 (-18.5 pp), 2007 (-15.5 pp), and 1979
(-15.4 pp). This dispersion is consistent with the cascading book changes,
not with a uniform return haircut from using micros.

## Data and coverage checks

Both saved runs have zero active invalid P&L rows and zero invalid portfolio
returns while an active book exists. The latest run has 23 active source
handoffs where cross-source P&L is explicitly omitted, versus 22 pre-change.
The longest omitted gap is 82 calendar days in both runs; the affected route
is MSF pre-change and M6B latest. The next-largest latest gap is MCH.HK at 35
days. These omissions are permitted by the configured history-gap policy but
remain a limitation of the stitched performance history.

The latest trial-allocation schema correctly confines `trial_reason` to
`cand_row=true`; no portfolio-level rejection reason is repeated on incumbent
book rows. Review status fields also use `added`, `retained`, `replaced`, and
`ineligible` with descriptive notes. The ZC/ZW diagnosis above required
reproducing hard eligibility from the saved causal volatility and per-contract
cost because a never-admitted candidate is not persisted in the annual
`review_instruments` report. Persisting every hard-ineligible annual candidate
and its rejection reason would make future route audits direct rather than
reconstructed.

## Artifacts

The source reports are the `multi_speed_ewmac_selection_*.parquet` files under:

- `/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261113_140831/tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone/`
- `/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261113_153041/tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone/`

This audit used the saved `portfolio_curve`, `cash_pnl`, `review_instruments`,
`review_final_allocations`, `reviews`, `candidates`, and `forecast_audit`
Parquet reports. It did not overwrite or regenerate either run.

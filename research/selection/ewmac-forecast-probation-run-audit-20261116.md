# EWMAC forecast-probation, route-switch, and reentry audit — 2026-11-16

## Scope

This audit compares the latest completed fixed-cap EWMAC probation run with the
otherwise compatible minimum-position run that has no forecast-activity filter.
It reads the saved Parquet reports and does not rerun or overwrite either
backtest.

Probation run:

```text
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_145713/
tv0p2_im20_bm50_wi0p2_st0p85_fdm1_minmaxpos1_fal256_fap0p5_fah0p25_far2_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone
```

Control run:

```text
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_135922/
tv0p2_im20_bm50_wi0p2_st0p85_fdm1_minmaxpos1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone
```

Both runs use a USD 75,000 fixed sizing capital, annual selection, a minimum
forecast-cap position of one contract, full-contract preference, the same
selection constraints, and the same data endpoint. The only scenario-level
difference is the probation run's 256-session rolling forecast activity rule:

- first review below 0.50: probation but still eligible;
- second consecutive review below 0.50: hard filter;
- any review below 0.25: immediate hard filter;
- review at or above 0.50: reset and immediate eligibility.

The saved review history covers 54 annual reviews from 1973-01-02 through
2026-01-01. The portfolio curves end on 2026-11-12.

## Finding

The ES/MES replacements are real, but probation did not cause them. The same
SP500 route changes occur on the same dates in the control run. They are
produced by the current `--selection-prefer-full-contracts` policy, which
reconstructs the execution route at every annual review and uses the soft
2.5-contract preferred band as a route-switch boundary.

The probation filter reduced strictly zero-held selected symbol-years from
three to one relative to the `minmaxpos=1` control, including removing the last
zero-held market from the 2026 book. Most of the reduction from the older
23-market run had already come from the one-contract structural floor. The
probation overlay also reduced full-history fixed-cap results and created a
large 2026 book-path cascade: three direct hard exclusions from the control
book ultimately changed 15 symbols and reduced the final count from 19 to 10.

The current result therefore supports two separate changes:

1. Make an incumbent execution route sticky inside a continuously selected
   economic family. The 2.5-contract preferred band should choose a route when
   the family enters, rather than force annual full/micro substitutions.
2. Add recovery hysteresis and a final-book fallback to probation. A weak
   filter should not create a five-market minimum-book deficit through a
   downstream greedy-path cascade.

## ES/MES audit

The probation and control runs have the same SP500 route history around the
reported period:

| Review | Prior route | Selected route | Direction | Rolling mean absolute forecast | Selected route maximum | Equivalent ES maximum | 2.5 full-preference band |
|---|---|---|---|---:|---:|---:|---:|
| 1988-01-04 | ES | MES | full to micro | 1.2560 | 13.454 MES | 1.345 | below |
| 1989-01-03 | MES | ES | micro to full | 0.3198 | 3.669 ES | 3.669 | above |
| 1992-01-02 | ES | MES | full to micro | 0.7137 | 20.503 MES | 2.050 | below |
| 1993-01-04 | MES | ES | micro to full | 0.6381 | 3.263 ES | 3.263 | above |

The 1990 and 1991 reviews retain ES with maximum positions of 2.646 and 2.599,
respectively. The 1992 allocation would express about 2.050 ES contracts, so it
falls below `con_band_min=2.5` and the selector chooses MES. In 1993 ES rises to
3.263 and replaces MES again.

ES and MES use the same normalized signal history: their persisted rolling
forecast values match at every joint review. In 1989 both routes are marked
probationary with the same 0.3198 value, yet ES replaces MES. This rules out the
probation calculation as the route-selection cause.

The behavior follows the implementation:

- the full route suppresses its micro sibling only while its trial-book
  `max_position` reaches `con_band_min`;
- completed greedy books are normalized again, replacing a full route that
  fell below the band with a feasible micro sibling;
- the next annual review starts the route decision again, so a recovered full
  route can replace the micro.

This is internally consistent with the current documented policy, but it does
not match the desired interpretation of full-contract *preference*. A soft
preferred position band is acting as a recurring execution-route switch.

## Route churn and apparent reentries

The issue is broader than ES/MES.

| Metric over 54 reviews | Control | Probation |
|---|---:|---:|
| Symbol reentries | 195 | 199 |
| Economic-family reentries | 180 | 184 |
| Continuous-family route substitutions | 27 | 27 |
| Full to micro substitutions | 13 | 12 |
| Micro to full substitutions | 7 | 6 |
| Other same-family route substitutions | 7 | 9 |

Eight economic families switch routes in each run. In the probation run the
largest counts are WHEAT 6, SP500 5, WTI 5, SUGAR 4, and two each for GOLD,
SOYOIL, and CORN, plus one for CADUSD. SP500 switches five times: ES to MES in
1988, MES to ES in 1989, ES to MES in 1992, MES to ES in 1993, and ES to MES in
1996.

The 27 continuous-family route substitutions should not be counted as market
reentries. The economic exposure never left the book. Public reentry analysis
should therefore report family reentries as the primary count and route
substitutions separately.

## Probation population and duration

The raw `fcst_hard_filter_n` field covers the whole audited source universe,
including instruments that do not yet have 256 valid forecast observations.
It is not a count of removed book members.

| Forecast activity result | Symbol-review rows | Unique symbols | Active selected rows | Mean rolling absolute forecast |
|---|---:|---:|---:|---:|
| Insufficient 256-session history | 5,085 | 161 | 0 | — |
| First weak review: probation | 393 | 147 | 76 | 0.4261 |
| Consecutive weak review: hard filter | 85 | 60 | 0 | 0.4228 |
| Below 0.25: immediate hard filter | 9 | 6 | 0 | 0.1723 |

Only the latter three rows measure signal weakness. The 5,085 insufficient
history rows are causal pre-history diagnostics and never represent active
removals.

Across the saved book path, 36 instruments were selected while probationary in
76 symbol-reviews. That is an average of 1.41 probationary selected instruments
per annual review, or 1.81 in the 42 reviews where at least one was present.
Their mean rolling absolute forecast was 0.4248.

A continuous probation episode lasts exactly one annual review under the
configured two-review rule. If the signal remains weak, the next review is a
hard filter. If it rises to 0.50, the streak resets immediately.

## Book effect

Across all 54 reviews:

| Metric | Control | Probation |
|---|---:|---:|
| Average book count | 14.593 | 14.296 |
| Reviews with the same count | 46 | 46 |
| Identical books | 41 | 41 |
| Control-only symbol-review memberships | 35 | — |
| Probation-only symbol-review memberships | — | 19 |
| Control-only memberships directly hard-filtered | 19 | — |
| Reviews below the 15-market target | 28 | 31 |
| Sum of annual book-count deficits | 104 | 114 |

The average effect looks small because 41 of 54 books are identical. The
nonlinear effects are concentrated in a few reviews.

### 2026 path

The control selects 19 markets:

```text
BRE,BTS,GBS,J7,KU,LBR,M2K,MBT,MCL,MHG,MWN,MYM,MZW,RS,SCI,SONIA.N,
SRFXON3,TSEMOTHR,TSEREIT
```

The probation run selects 10:

```text
BRE,M2K,MBT,MHG,MWN,QG,RS,SX7E,TSEMOTHR,V2TX
```

The 2026 candidate universe contains 30 first-weak probation rows and 16 hard
filters. Fourteen of the hard filters are consecutive weak reviews and two are
immediate sub-0.25 filters. Of the 12 control members missing from the
probation book, only these three are direct hard exclusions:

| Symbol | Rolling mean absolute forecast | Weak-review count |
|---|---:|---:|
| SONIA.N | 0.3726 | 2 |
| SCI | 0.4076 | 2 |
| MCL | 0.4467 | 2 |

Removing those candidates changes the greedy construction path. It also drops
BTS, GBS, J7, KU, LBR, MYM, MZW, SRFXON3, and TSEREIT, while adding QG, SX7E,
and V2TX. BTS and GBS are only on their first weak review and remain eligible;
MZW is fully activity-eligible at 0.9662. Their removal is therefore a
portfolio-path consequence, not a direct probation decision.

V2TX enters the final book while on first-review probation at 0.3103. The
resulting book has a five-market deficit against `min_book_n=15`, whereas the
control has no book-count deficit. The control already has class and subclass
coverage deficits, but probation converts a 19-market book into a 10-market
book without satisfying the configured breadth target.

## Hard-filter recovery and reentry

There are 16 selected reentries across 13 symbols after at least one
intervening hard-filter review. The average excluded duration is 1.19 annual
reviews. The recoveries are:

| Reentry | Symbol | Prior hard-filter reviews | Reentry rolling forecast |
|---|---|---:|---:|
| 1981-01-02 | MZM | 1 | 1.1298 |
| 1986-01-02 | LE | 1 | 0.6035 |
| 1989-01-03 | MGC | 1 | 0.8329 |
| 1989-01-03 | SB | 1 | 0.5503 |
| 1993-01-04 | QO | 1 | 0.8500 |
| 1997-01-02 | MGC | 1 | 0.9099 |
| 1997-01-02 | PL | 3 | 0.7787 |
| 2001-01-02 | M6B | 1 | 0.9223 |
| 2004-01-02 | W | 1 | 0.6485 |
| 2007-01-02 | MNQ | 1 | 0.5869 |
| 2009-01-01 | MSF | 1 | 1.0239 |
| 2014-01-01 | M6B | 1 | 0.6962 |
| 2019-01-01 | MSF | 1 | 0.7554 |
| 2023-01-02 | SGB | 2 | 0.6114 |
| 2024-01-01 | GBS | 1 | 1.2107 |
| 2026-01-01 | TSEMOTHR | 1 | 0.5936 |

TSEMOTHR illustrates the present exit/reentry rule: hard-filtered in 2025, it
returns immediately in 2026 when the rolling forecast rises to 0.5936. A
separate recovery threshold or two healthy reviews would reduce this churn.

## Reduction in inactive selected markets

An inactive selected symbol-year is defined here as a market that is
`sel_active` but holds zero contracts on every active daily cash-P&L row. This
is stricter than being on forecast probation.

| Run | Position policy | Zero-held symbol-years, full history | Zero-held markets in 2026 | Selected markets in 2026 |
|---|---|---:|---:|---:|
| 2026-11-13 original | 0.5 minimum maximum position, no probation | 46 | 5 | 23 |
| 2026-11-16 control | 1.0 minimum maximum position, no probation | 3 | 1 | 19 |
| 2026-11-16 probation | 1.0 minimum maximum position plus probation | 1 | 0 | 10 |

The original 2026 zero-held markets were BTS, HE, MCH.HK, SRFXON3, and
TSEMOTHR. Raising the structural floor to one removed four of those and left
only BTS in the compatible control. The probation run removes BTS from the
final greedy path and has no selected market below a 5% held-day share in
2026.

Across the full compatible histories, the control's three zero-held
symbol-years are PL in 1995, SOFR3 in 2014, and BTS in 2026. The probation run
retains only SOFR3 in 2014. Thus probation removes two zero-held symbol-years,
while the structural floor accounts for 43 of the 45 reductions from the
original run.

This confirms that probation helps the narrow inactive-slot metric, but the
2026 improvement comes with nine fewer markets and a breadth deficit. It does
not establish that the current thresholds improve diversification or expected
returns.

## Saved performance and reconciliation

`port_net_pct` is already stored in percentage points. Annual means and
volatilities below multiply it by 256 and square-root 256, respectively,
without another percentage conversion. Fixed-cap adjusted Calmar is the
annualized arithmetic mean divided by absolute maximum drawdown; it is not a
standard compounding Calmar.

| Run | Valid return days | Ending net equity | Net P&L | Annual mean | Annual volatility | Sharpe | Max drawdown | Fixed-cap adjusted Calmar |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Control | 13,963 | $888,466.93 | $813,466.94 | 19.897% | 18.517% | 1.075 | -9.913% | 2.007 |
| Probation | 13,942 | $865,151.70 | $790,151.71 | 19.356% | 18.538% | 1.044 | -9.913% | 1.952 |

On the 13,937 dates where both returns are valid, the control has a 19.943%
annual mean, 18.534% volatility, and 1.076 Sharpe. Probation has a 19.362%
annual mean, 18.541% volatility, and 1.044 Sharpe. The common endpoint is
2026-11-12.

| Run | Gross P&L | Trade costs | Roll costs | Total costs | Net P&L | Reconciliation residual |
|---|---:|---:|---:|---:|---:|---:|
| Control | $883,469.80 | $44,740.95 | $25,256.61 | $69,997.56 | $813,466.94 | $5.30 |
| Probation | $859,536.06 | $44,401.81 | $24,977.55 | $69,379.36 | $790,151.71 | $4.99 |

Probation saves $618.20 in costs but loses $23,933.74 of gross P&L, leaving
net P&L $23,315.23 below the control. The residuals are the accumulated effect
of persisted report rounding. Both maximum drawdowns occur on 1974-04-11.

There are no duplicate cash `(symbol, date)` keys, duplicate portfolio dates,
active invalid-P&L rows, or invalid-return rows with an active book in either
run. Both have complete active books through 2026-11-12. Fixed-cap equity is
the accumulated cash result of positions still sized from the fixed USD
75,000 capital; it is not a reinvested CAGR series.

These are saved in-sample backtest paths, not independently refit annual
experiments. Their performance difference is evidence about the realized
policy path, not proof that weak forecasts should never be filtered.

## Recommended implementation direction

### Keep the execution route stable within a selected family

Persist the incumbent route by `econ_fam_id`. When the family remains in the
target book, retain the route if it remains causally available and clears hard
feasibility, including `min_max_position=1`. Use `con_band_min=2.5` to choose
the route when a family first enters or reenters. Do not use that soft band to
replace an incumbent route every year.

A full-to-micro switch should occur only when the full contract fails a hard
gate or an explicit route-switch hurdle. A micro-to-full promotion should
likewise require family reentry or a separately configured, persistent route
hurdle. Persist the incumbent route, proposed route, route sizing, and exact
switch decision in the review report.

### Track signal probation by family or shared signal identity

The weak-review counter is currently keyed by symbol even when full and micro
contracts share one signal. ES and MES happen to remain synchronized in this
run, so this did not cause the observed substitutions. Family-level state is
still the correct model and avoids double-counting probation and hard filters
for execution variants of the same market.

### Add recovery and breadth protection

Require a stronger or persistent recovery before a hard-filtered family
reenters, such as a 0.60 recovery threshold or two consecutive reviews above
0.50. After applying hard filters, enforce the 15-market breadth target with
an explicit reported fallback among first-review probation candidates. This
would preserve the distinction between probation and hard exclusion while
preventing the 2026 three-exclusion cascade from ending at 10 markets.

## Source artifacts

The primary inputs are each run's `selection_reviews`,
`selection_review_instruments`, `selection_review_final_allocations`,
`selection_screen`, `selection_book`, `selection_cash_pnl`, and
`selection_portfolio_curve`
Parquet files under the two scenario directories listed above. The older
2026-11-13 run is used only for the separately labeled structural-floor
comparison in the inactive-market table.

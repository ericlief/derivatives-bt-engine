# EWMAC 15% instrument-margin baseline audit — 2026-11-16

## Scope

This audit evaluates the latest completed fixed-cap EWMAC run with a 15% per-
instrument initial-margin ceiling as the greedy baseline for the bounded-beam
test. It reads the saved Parquet reports without rerunning or overwriting the
backtest.

Primary baseline candidate:

```text
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_202750/
tv0p2_im15_bm50_wi0p2_st0p85_fdm1_minmaxpos1_fal256_fap0p5_fah0p25_far2_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone
```

Comparison runs:

```text
# Same probation policy, 20% instrument-margin ceiling
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_145713/
tv0p2_im20_bm50_wi0p2_st0p85_fdm1_minmaxpos1_fal256_fap0p5_fah0p25_far2_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone

# 20% instrument-margin ceiling, no forecast-activity filter
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261116_135922/
tv0p2_im20_bm50_wi0p2_st0p85_fdm1_minmaxpos1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone
```

All three use USD 75,000 fixed sizing capital, annual selection, a 50% book-
margin ceiling, a one-contract maximum-position floor, full-contract
preference, and the same hierarchy limits. The two probation runs also have
identical 256-session forecast-activity settings. Their persisted candidate
snapshots are identical after excluding report timestamps, and no forecast
state or rolling forecast differs on common review rows. The controlled policy
difference is therefore the 15% versus 20% per-instrument margin ceiling.

The 15% run predates the public beam path fields, but it is the original greedy
selector path. Beam width one dispatches to that unchanged implementation, so
this is the correct saved numerical benchmark for a width-four run using the
same frozen inputs.

## Decision

Use the **15% instrument-margin plus probation run as the greedy breadth
baseline for the beam test**. It is a materially better baseline than the 20%
probation run for a USD 75,000 book:

- average book size rises from 14.30 to 15.11;
- summed annual book-count deficit falls from 114 to 95;
- reviews below the 15-market target fall from 31 to 27;
- every 2015--2026 book has at least 16 markets, compared with four recent
  20% books of only 10 or 11 markets;
- the 2026 book rises from 10 to 20 markets, effective instrument count rises
  from 8.68 to 13.51, and top-five weight falls from 62.51% to 50.01%;
- the common-history annual mean falls only 0.078 percentage points and Sharpe
  falls 0.016 relative to 20% plus probation.

This is a baseline choice, not evidence that 15% maximizes return. The annual
return delta ranges from -18.3 percentage points in 2023 to +21.3 points in
2025. The cap changes the greedy path, membership, and sizing; realized return
differences are consequently noisy and cannot identify an optimal threshold
from one saved path.

## Why the cap increases breadth

The lower ceiling acts as an anti-concentration feasibility gate. In the 2026
15% trials, V2TX is repeatedly rejected for `instrument_margin_limit`. Under
the 20% ceiling it is accepted at iteration seven; V2TX and MBT then consume
most of the available margin, and the greedy path terminates with ten markets
and only USD 50.55 of book-margin headroom.

The 15% path excludes that expensive route, continues to 20 markets, and ends
with USD 2,531.94 of headroom:

| 2026 metric | 15% + probation | 20% + probation | 20% no-probation control |
|---|---:|---:|---:|
| Book size | 20 | 10 | 19 |
| Portfolio margin at maximum positions | $34,968.06 | $37,449.45 | $37,006.34 |
| Remaining below $37,500 ceiling | $2,531.94 | $50.55 | $493.66 |
| Effective instrument count | 13.51 | 8.68 | 13.36 |
| Top-five weight | 50.01% | 62.51% | 50.01% |
| Final selection score | 1.2395 | 1.1009 | 1.3074 |
| Book-count deficit | 0 | 5 | 0 |

The 15% 2026 book is:

```text
BRE,GBS,I,J7,M2K,MBT,MCD,MET,MHG,MTN,MWN,MYM,MZW,QG,RS,SB,SOFR3,
SRFXON3,TSEMOTHR,TSEREIT
```

The 20% probation book is:

```text
BRE,M2K,MBT,MHG,MWN,QG,RS,SX7E,TSEMOTHR,V2TX
```

The 15% path can temporarily approach the book-margin ceiling and then regain
headroom as later additions redistribute weights. Its last five accepted
candidates are SRFXON3, MYM, MTN, SOFR3, and MCD; final margin declines from
USD 37,337.90 after SRFXON3 to USD 34,968.06 after MCD. This is expected under
full-book reallocation and is why terminal margin, not only the margin after an
early greedy winner, matters.

## Annual book effect

Daily P&L and membership are identical through 2014. The cap changes 11 of 54
annual books, all from 2015 onward; the 2024 probation books are identical.
Among the 11 changed books, the 15% selection score is higher in ten and lower
only in 2021.

| Year | 15% book | 20% probation book | 15% margin at max | 20% margin at max | 15% return | 20% probation return | Return delta |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 21 | 20 | $29,387.68 | $37,221.15 | 12.2% | 14.5% | -2.4 pp |
| 2016 | 21 | 20 | $33,872.49 | $36,648.53 | -3.1% | 1.5% | -4.6 pp |
| 2017 | 20 | 19 | $33,314.31 | $37,481.05 | 10.3% | 6.9% | +3.4 pp |
| 2018 | 23 | 23 | $33,389.04 | $37,252.15 | -1.3% | -7.0% | +5.6 pp |
| 2019 | 18 | 16 | $31,641.44 | $37,368.72 | -0.4% | -11.2% | +10.8 pp |
| 2020 | 22 | 18 | $31,609.59 | $37,483.50 | 35.6% | 42.4% | -6.9 pp |
| 2021 | 20 | 15 | $32,015.75 | $37,395.85 | 17.6% | 23.0% | -5.4 pp |
| 2022 | 16 | 10 | $32,545.08 | $37,434.37 | 19.5% | 24.4% | -4.9 pp |
| 2023 | 17 | 11 | $33,191.10 | $36,803.90 | -21.4% | -3.1% | -18.3 pp |
| 2024 | 21 | 21 | $37,309.38 | $37,309.38 | -3.3% | -3.2% | -0.2 pp |
| 2025 | 18 | 10 | $36,612.89 | $37,302.41 | 5.1% | -16.2% | +21.3 pp |
| 2026 partial | 20 | 10 | $34,968.06 | $37,449.45 | -14.2% | -11.4% | -2.8 pp |

The 15% cap fully repairs the numerical 15-market target over this recent
window, but it does **not** satisfy the complete hierarchy target. Every recent
review still has one class deficit and at least two subclass deficits; 2026
has one class and three subclass deficits. That remaining structure is the
appropriate problem for the beam, whose terminal objective prioritizes class
and subclass deficits before book-count deficit and score.

## Performance and risk

`port_net_pct` is persisted in percentage points. Annual mean and volatility
use 256 trading days. Fixed-cap adjusted Calmar is annualized arithmetic mean
divided by absolute maximum drawdown; it is not standard compounding Calmar.
Equity is accumulated cash P&L from positions still sized to the fixed USD
75,000 capital, not a reinvested CAGR series.

| Run | Valid days | Ending equity | Net P&L | Annual mean | Annual vol | Sharpe | Max drawdown | Fixed-cap adjusted Calmar |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 15% + probation | 14,020 | $861,960.52 | $786,960.33 | 19.171% | 18.698% | 1.025 | -9.913% | 1.934 |
| 20% + probation | 13,942 | $865,151.70 | $790,151.74 | 19.356% | 18.538% | 1.044 | -9.913% | 1.953 |
| 20% control | 13,963 | $888,466.93 | $813,467.07 | 19.897% | 18.517% | 1.075 | -9.913% | 2.007 |

All maximum drawdowns occur on 1974-04-11 and are unchanged because the 15%
and 20% probation paths do not diverge until 2015. All runs have complete
active books through 2026-11-12.

The 13,937-date common valid-return comparison removes the small difference in
valid-day coverage:

| Run | Annual mean | Annual vol | Sharpe |
|---|---:|---:|---:|
| 15% + probation | 19.284% | 18.752% | 1.028 |
| 20% + probation | 19.362% | 18.541% | 1.044 |
| 20% control | 19.943% | 18.534% | 1.076 |

Thus the 15% cap costs 0.078 percentage points of annual mean and 0.016 Sharpe
versus the matching 20% probation run on common dates. Against the no-
probation control, the combined 15% plus probation policy is lower by 0.658
percentage points of annual mean and 0.048 Sharpe. The latter is not a pure cap
effect because it also includes forecast probation.

Since 2020, the 15% path has a 5.280% annual mean and 0.308 Sharpe versus 7.600%
and 0.484 for 20% probation. Since 2024, it has performed better (-4.105%
annual mean and -0.272 Sharpe versus -9.964% and -0.821), almost entirely due
to avoiding the 20% path's 2025 loss. These short-window reversals reinforce
that the threshold should be chosen for feasible diversification, not recent
return fitting.

## P&L, costs, and data validity

Persisted cash costs are negative cash flows; the table reports positive fee
magnitudes. Net P&L is gross P&L less trade and roll fees.

| Run | Gross P&L | Trade fees | Roll fees | Total fees | Net P&L | Fees / gross | Reconciliation residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15% + probation | $855,335.80 | $44,685.46 | $23,685.23 | $68,370.69 | $786,960.53 | 7.99% | $4.58 |
| 20% + probation | $859,536.06 | $44,401.81 | $24,977.55 | $69,379.36 | $790,151.71 | 8.07% | $4.99 |
| 20% control | $883,469.80 | $44,740.95 | $25,256.61 | $69,997.56 | $813,466.94 | 7.92% | $5.30 |

Relative to 20% probation, 15% loses USD 4,200.26 of gross P&L and saves USD
1,008.67 of total fees, leaving net P&L lower by USD 3,191.19. Costs do not
cause the shortfall. Trade fees are USD 283.65 higher, consistent with a wider
book, but roll fees are USD 1,292.32 lower because the selected routes differ.

The largest positive 15% net contributors are SB (USD 55.53k), MGC (USD
50.11k), SI (USD 46.33k), M6B (USD 44.14k), and QG (USD 38.89k). The largest
negative contributors are MET (-USD 7.08k), M2K (-USD 4.41k), D (-USD 4.38k),
M6A (-USD 3.51k), and SX7E (-USD 3.37k). By class, the largest net contributors
are agriculture (USD 181.50k), metals (USD 161.13k), FX (USD 157.55k), bonds
(USD 125.51k), and energy (USD 112.94k); crypto loses USD 3.28k.

There are no duplicate cash `(symbol, date)` keys, duplicate portfolio dates,
active invalid-P&L rows, or invalid returns while a book is active. The cash-
to-portfolio net residual is USD 0.20; the larger USD 4.58 gross-less-fees
residual is accumulated public-row rounding.

## Forecast probation and inactive slots

The 15% and 20% probation runs have identical persisted forecast states. The
15% cap therefore restores breadth without weakening or bypassing forecast
probation. The 2026 15% book contains three first-weak probation markets: I at
0.4192 rolling mean absolute forecast, GBS at 0.4261, and MTN at 0.4490.

The wider book is not entirely executable activity. SRFXON3 is selected in
2026 but holds zero contracts on every active daily row. Together with SOFR3
in 2014, the 15% path has two zero-held symbol-years, versus one for 20%
probation. The one-contract `max_position` floor tests forecast-scaled maximum
capacity, not whether the buffered realized daily forecast ever crosses an
integer holding threshold. For baseline reporting, the 2026 book should
therefore be described as 20 selected markets, 19 markets with at least one
held contract, and effective instrument count 13.51.

This does not undo the breadth improvement, but it prevents interpreting raw
book count as executable diversification by itself. The beam comparison must
report selected count, ever-held count, effective count, and zero-held members.

## Beam acceptance test

Run width four with every other parameter fixed to the 15% probation baseline.
Do not compare a 15% width-four run only with the older 20% greedy run; that
would conflate the cap and search path.

The width-four result should be accepted over this baseline only if it:

1. reduces class or subclass deficits without reintroducing a book-count
   deficit;
2. does not materially reduce ever-held or effective instrument count;
3. keeps total margin within the same 50% ceiling and explains any movement in
   margin headroom;
4. reports route substitutions, annual membership turnover, zero-held slots,
   and probationary selections;
5. preserves cash-P&L reconciliation and complete-book coverage; and
6. has an acceptable return, risk, and cost change on common valid dates.

Because the 15% greedy path already clears the numerical 15-market target in
every recent year, the beam's primary value proposition is now hierarchy
coverage and path stability, not merely adding more symbols. Width four should
not be preferred simply because it builds the largest terminal book.

## Source artifacts

The audit reads each scenario's saved `selection_portfolio_curve`,
`selection_cash_pnl`, `selection_reviews`, `selection_review_instruments`,
`selection_review_final_allocations`, `selection_candidates`,
`selection_forecast_audit`, `selection_trials`, and
`selection_trial_allocations` Parquet reports. No source run or market-data
file was changed.

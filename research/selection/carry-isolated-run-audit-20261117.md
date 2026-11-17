# Carry isolated selection audit: November 17, 2026

This audit covers the latest complete fixed-capital carry run with forecast
activity filtering and the 15% per-instrument initial-margin ceiling. The
saved run is:

`/home/dev/data/fin/misc/results/selection_v1/carry/selection_run_20261117_132532/carry_tv0p2_im15_bm50_wi0p2_st0p9_minmaxpos1_fal256_fap0p5_fah0p25_far2_ic20_minc1_mins1_mb15_cw50_sw25_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh0_mhnone/`

The audit reads the 16 saved Parquet artifacts without regenerating the
backtest. The earlier `20261117_120506` carry run is not a valid control: it
does not share the margin, probation, roll-cost, and other selector settings.
The latest run should therefore be treated as the proposed isolated carry
baseline, not as a controlled improvement over that earlier run.

Post-audit implementation note: the probation admission defect described
below was fixed on November 17 without overwriting this saved run. A first-
weak challenger is now rejected before selector construction; only a current
book member may use the one-review probation state. The saved artifacts remain
the evidence for the pre-fix behavior.

## Findings

1. **DJUSRE exposed a probation-entry policy defect, now fixed in code.** Its 2026 rolling mean
   absolute forecast is 0.4446, below the 0.50 probation threshold. The code
   correctly labels it probationary, but only hard-filtered candidates are
   removed from the candidate pool. DJUSRE is consequently allowed to enter
   a new book and satisfy subclass coverage. This matches the current code,
   but conflicts with the intended policy that probation gives an incumbent
   breathing room and does not make a weak new candidate eligible.
2. **The micro-ag position values are not inverted.** Within the same review
   and trial iteration, the micro contract always has greater continuous
   capacity than the corresponding mini or full contract. Apparent
   contradictions come from comparing an early candidate row with a final
   allocation after weights and IDM have been recomputed for the expanded
   book.
3. **The frequent micro/mini/full ag changes are real policy behavior.** The
   selector has no family-route hysteresis, does not prefer full contracts,
   and has zero low/high contract-count penalties. Annual changes in
   volatility, cost, and book weights can therefore change the winning route.
4. **The saved portfolio return series has a material calendar/validity defect.** There
   are 1,495 active-book dates on which one or more selected symbols have no
   cash-P&L row. Those dates are marked `ret_valid=false`, but the available
   partial P&L still changes cumulative equity. Economic P&L reconciles, but
   strict-valid risk statistics omit a non-random $10,174.99 loss. A check of
   pinned pysystemtrade commit `b4a25e6e` shows that PST does not drop the
   entire portfolio day: it forward-fills an absent instrument price on its
   business-day grid, gives that instrument zero P&L, keeps other markets'
   P&L, and resamples account curves to `1B`. This carry baseline is not
   decision-ready for Sharpe or Calmar comparison until its portfolio calendar
   follows an equivalent explicit policy.

The probation-entry behavior and return-validity issue are separate. Fixing
the first changes selection. Fixing the second changes portfolio accounting
or validity classification and may change reported performance even with the
same books.

## Saved parameters

| Parameter | Saved value |
|---|---:|
| Capital mode / initial capital | Fixed / $75,000 |
| Portfolio target volatility | 20% |
| Signal | Carry |
| Carry smoothing spans | 5, 20, 60, 120 |
| Carry forecast scalar / cap | 3.0 / ±2.0 |
| Review frequency | Yearly |
| Weight policy | `topdown_classif` |
| Score threshold | 0.90 |
| Maximum weight × IDM | 0.20 |
| Maximum IDM | 2.5 |
| Minimum `max_pos` | 1.0 continuous contract |
| Forecast activity lookback | 256 valid sessions |
| Probation / immediate-hard thresholds | 0.50 / 0.25 |
| Consecutive weak reviews before hard filter | 2 |
| Minimum class / subclass / book counts | 1 / 1 / 15 |
| Maximum instruments per class / subclass | 20 / 3 |
| Maximum class / subclass weight | 50% / 25% |
| Instrument / book initial-margin ceiling | 15% / 50% of capital |
| Trade-cost SR entry limit / rule-cost SR limit | 0.01 / 0.15 |
| Roll cost included in score | Yes |
| Beam width | 1, greedy |
| Full-contract preference | Off |
| Contract-count band | 2.5–5.5, diagnostic only |
| Low/high contract-count penalty scales | 0 / 0 |
| Selection eligibility / family-route hysteresis | 0% / none |
| Position buffer / execution rule | 10% / trade to edge |
| Rebalance on roll | Yes |
| Maximum history gap | 31 days |

The scenario token `cl3` means the maximum of three instruments per economic
subclass; it is not the cost limit. The persisted candidate artifact records
the two cost-SR limits shown above.

## DJUSRE probation audit

The 2026 review row is unambiguous:

| Field | DJUSRE value |
|---|---:|
| Selection status | Added |
| Rolling mean absolute forecast | 0.4446 |
| Weak review count | 1 |
| Forecast activity status | Probation |
| Hard filtered | No |
| Final structural weight | 8.33% |
| Final `max_pos` / `max_con` | 1.36 / 1 |
| Final book score | 1.4016 |

DJUSRE was feasible in each of eight trials. At iteration 7 it was accepted in
the subclass-coverage phase with trial score 1.2512 and global trial rank 6.
It was the only coverage-eligible choice in that active step. The final 2026
book still had one class and three subclass coverage deficits, so admitting
the weak candidate did not make the hierarchy fully feasible.

The saved behavior follows `_forecast_activity_at_review`: a value below 0.50
receives a first-weak probation state, while a value below 0.25 or a second
consecutive weak review is hard filtered. At the time of this run, the
provider dropped only `forecast_activity_hard_filter` candidates. This was
inconsistent with the earlier probation design note, which specified that a
probationary candidate must not satisfy minimum-book or coverage requirements.

This is not unique to DJUSRE. The run has 24 selected probation market-years:
19 retained incumbents and five new entrants.

| Year | New probation entrant | Rolling avg. abs. forecast | Held? | Contracts traded | Gross P&L | Net P&L |
|---:|---|---:|---:|---:|---:|---:|
| 1990 | PL | 0.3487 | Yes | 2 | $2,925.00 | $2,864.18 |
| 1999 | PL | 0.3918 | Yes | 1 | $445.00 | $414.59 |
| 2004 | M6E | 0.4539 | Yes | 2 | −$110.00 | −$113.32 |
| 2019 | XINA50 | 0.3458 | Yes | 3 | $185.00 | $144.55 |
| 2026 | DJUSRE | 0.4446 | No | 0 | $0.00 | $0.00 |

DJUSRE's target remained below the buffered whole-contract entry threshold,
so it never held or traded a contract in 2026. It nevertheless occupied 8.33%
of structural book weight. DJUSRE and IBEX are the two selected 2026 markets
with no held contract; across the run, 12 of 666 selected market-years never
held a contract.

The static `forecast_audit` row for DJUSRE shows 0.718 and eligible because
that reference-only artifact ends on March 28, 2024. The dated 2026 selector
uses the stitched history available before the 2026 review and obtains
0.4446. The two figures describe different dated views; the 2026 trial value
is the one that controlled selection.

### Required policy correction

The implemented correction excludes a probationary non-incumbent before
selector construction, covering normal ranking, hierarchy coverage, and
minimum-book satisfaction uniformly. A first weak review may retain an
incumbent and reports the `forecast_probation` note; a second weak review or
an immediate-hard value removes it. No fallback admits weak challengers. No
saved-run counterfactual is inferred here: removing these entrants changes the
subsequent book and must be tested by a new sensitivity run.

## Micro agriculture audit

The audited route families are:

| Family | Micro | Mini | Full |
|---|---|---|---|
| Corn | MZC, multiplier 5 | YC, multiplier 10 | ZC, multiplier 50 |
| Wheat | MZW, multiplier 5 | YW, multiplier 10 | ZW, multiplier 50 |
| Soybeans | MZS, multiplier 5 | YK, multiplier 10 | ZS, multiplier 50 |
| Soymeal | MZM, multiplier 10 | — | ZM, multiplier 100 |
| Soy oil | MZL, multiplier 60 | — | ZL, multiplier 600 |

There is no `TW` route in these families; the wheat mini is `YW`. The current
live screen excludes MZC, MZM, MZL, and MZW on live volume/risk-volume checks,
while MZS is currently eligible. Those live execution gates are intentionally
not projected backward into historical selection, so the other micros can
appear in old books.

The selector calculates continuous capacity as:

`max_pos = capital × target_vol × limit_weight × IDM × forecast_cap_ratio / ann_dvol`

`avg_pos` is half of `max_pos`. `max_con` is whole-contract half-up rounding:
zero below 0.5, otherwise `floor(max_pos + 0.5)`. The values are therefore
capacity measures under the current trial allocation, not fixed instrument
properties.

At the first 1974 trial, like-for-like route comparisons have the expected
ordering:

| Route | `max_pos` | `max_con` | Annual cost SR | Size penalty SR | Net SR | Feasible |
|---|---:|---:|---:|---:|---:|---:|
| MZC | 18.5689 | 19 | 0.1347 | 0.0004 | 0.3649 | Yes |
| YC | 9.2844 | 9 | 0.0985 | 0.0015 | 0.4000 | Yes |
| ZC | 1.7513 | 2 | 0.0405 | 0.0408 | 0.4187 | Yes |
| MZW | 7.2469 | 7 | 0.0666 | 0.0024 | 0.4310 | Yes |
| YW | 3.6235 | 4 | 0.0522 | 0.0095 | 0.4383 | Yes |
| MZM | 7.6982 | 8 | 0.0888 | 0.0021 | 0.4091 | Yes |
| ZM | 0.7698 | 1 rounded | 0.0285 | infinite | — | No: `max_pos < 1` |
| MZL | 11.1621 | 11 | 0.0752 | 0.0010 | 0.4238 | Yes |
| ZL | 1.1162 | 1 | 0.0474 | 0.1003 | 0.3523 | Yes |

Micros have more continuous and rounded contract capacity in every direct
comparison. They do not always have the best score because per-risk trading
and roll costs can be greater. In the 1973 soymeal seed, MZM had `max_pos`
12.9049 but annual cost SR 0.1488 and net SR 0.3504; ZM had `max_pos` 1.2905,
annual cost SR 0.0478, and net SR 0.3772. The full contract therefore won
without any full-contract preference.

The apparent lower micro/mini values in final rows are caused by trial
reweighting. In 1974 MZM falls from 7.6982 in the one-name trial to 3.0456 in
the final nine-name book. In 1975 YC falls from 7.4528 to 3.4504, YW from
5.3435 to 2.4739, and MZL from 8.1553 to 3.7756. Trial allocations must be
compared at the same `sel_date` and `iteration`; a final-book row is not
comparable to an earlier challenger row.

The selected routes in the first 14 reviews were:

| Year | Corn | Wheat | Soybeans | Soymeal | Soy oil |
|---:|---|---|---|---|---|
| 1972 | — | — | — | ZM | ZL |
| 1973 | — | — | — | ZM | — |
| 1974 | YC | YW | — | MZM | — |
| 1975 | YC | YW | — | — | MZL |
| 1976 | ZC | YW | — | — | — |
| 1977 | ZC | YW | — | — | MZL |
| 1978 | ZC | ZW | — | — | — |
| 1979 | ZC | YW | — | — | MZL |
| 1980 | ZC | — | — | — | ZL |
| 1981 | ZC | — | — | — | MZL |
| 1982 | ZC | — | — | — | — |
| 1983 | ZC | ZW | — | — | — |
| 1984 | ZC | ZW | — | — | — |
| 1985 | ZC | — | MZS | — | MZL |

From 1972 through 1985, consecutive annual route states change 2 times for
corn, 6 for wheat, 1 for soybeans, 2 for soymeal, and 10 for soy oil. Over the
full 55 reviews, the counts are 17, 25, 16, 18, and 25 respectively. A change
includes entry from or exit to no selected route. With no family-route
hysteresis and no contract-band penalty, this turnover is expected rather
than evidence of a capacity inversion. If route stability is desired, it
needs an explicit family-route switching rule or cost, not a correction to
`max_pos`.

## Carry forecast and turnover checks

The forecast artifact has 1,003,716 rows for 167 symbols. It contains 892,437
valid combined forecasts and 111,279 null warm-up or unavailable forecasts;
there are no non-finite combined values. Across valid rows, mean absolute
forecast is 1.1587, signed mean is −0.0654, and 26.63% of forecasts are at the
±2 cap.

The standard rule set is four equal-weight smoothed forecasts at spans
5/20/60/120 with FDM 1.23; 157 of 167 symbols use it. Eight symbols have
20/60/120 with equal thirds and FDM 1.03, and two have 60/120 with equal
halves and FDM 1.02. Where all four standard components are jointly valid,
their signs agree 90.09% of the time.

The rule-cache turnover estimates and AFTS reference constants are both
present:

| Carry rule | Pooled PST turnover | AFTS turnover | PST / AFTS |
|---|---:|---:|---:|
| 5 | 19.0933 | 5.75 | 3.32× |
| 20 | 9.4135 | 3.12 | 3.02× |
| 60 | 6.0745 | 1.82 | 3.34× |
| 120 | 4.9044 | 1.22 | 4.02× |

The pooled source is
`pooled_pst_history_afts_span_segment_safe_v2`, based on 171 instruments. The
larger pooled estimates are used for component rule cost eligibility and are
not silently replaced by the AFTS constants. For final combined-signal cost,
the selector measures `comb_turnover` from the normalized combined reference
position history. It ranges from 2.4802 to 61.5772 with median 12.2285 across
the candidate set. Median audited trade, roll, and total annual cost SR are
0.0174, 0.0081, and 0.0259 respectively.

The current static forecast audit marks 125 symbols forecast-eligible and 42
excluded: 40 for insufficient carry coverage and two for insufficient valid
forecast observations. It also shows 22 current probation and nine current
hard-filter statuses. Forecast construction and cost wiring are therefore
present; the defect is specifically how probation status is admitted into
selection.

## P&L and cost audit

The portfolio curve runs from February 3, 1970 through November 16, 2026. The
first valid selected-book return is January 3, 1972. November 11, 2026 is the
last date on which all 16 selected instruments have a valid row.

| Metric | Result |
|---|---:|
| Gross P&L | $598,536.54 |
| Trading costs | $41,676.02 |
| Roll costs | $29,647.18 |
| Net cash P&L | $527,213.57 |
| Final saved net equity | $602,213.57 |
| Fixed-cap economic net return | 702.95% |
| Total costs / gross P&L | 11.92% |
| Strict-valid observations | 12,940 |
| Strict-valid arithmetic annual mean | 14.18% |
| Strict-valid annual volatility | 18.94% |
| Strict-valid Sharpe | 0.748 |
| Maximum drawdown | −28.10% |
| Strict-valid adjusted Calmar | 0.504 |

Cash P&L reconciles to the final curve within $0.40. The maximum drawdown runs
from the September 22, 1978 peak of $255,887.30 to the February 5, 1980 trough
of $183,975.78, with recovery on June 4, 1981.

Largest lifetime net contributors are SOFR3 ($68,694), MSF ($65,109), MCL
($59,360), M6B ($57,607), HE ($47,092), SCI ($44,709), CAD ($40,366), ZM
($40,199), CT ($39,208), and SB ($31,719). The largest losses are MHG
(−$27,756), LE (−$23,103), MGC (−$21,687), PL (−$14,996), DJUSRE
(−$13,426), RB (−$13,349), MBT (−$8,321), ZAR (−$8,059), IXR (−$7,408), and
QG (−$6,332).

| Asset class | Net P&L |
|---|---:|
| FX | $248,887 |
| Agriculture | $185,142 |
| Bonds | $104,842 |
| Energy | $61,482 |
| Other | $0 |
| Volatility | $0 |
| Crypto | −$8,321 |
| Metals | −$23,549 |
| Equity | −$41,269 |

Costs do not by themselves explain the negative equity and metals results:
equity gross P&L is −$35,924 before $5,346 of cost, while metals gross P&L is
−$11,262 before $12,287 of cost.

## Portfolio calendar and return validity

Of the selected-book dates, 1,495 have `ret_valid=false`. No cash row that is
present and active has `pnl_valid=false`; instead, some selected symbol-date
rows are absent. On these dates only 59.0% of selected instruments are present
on average. The partial rows still contribute −$10,174.99, or −13.57 fixed-cap
percentage points, to the saved equity curve.

The missing-date effect is not confined to the unfinished tail. It appears
from 1972 onward. The most affected recent years are 2024, with 249 dates and
−12.73 percentage points; 2025, with 83 dates and −5.79 points; and 2023,
with 76 dates and −2.60 points. In 2026, 25 of the 54 invalid dates are
Saturdays carrying only the SGX `IRON`/`SCI` and `FTSECHINAA`/`XINA50` rows.
The raw IB database contains those Saturday bars. PST's `1B` account-curve
resample would merge a Saturday amount into the preceding Friday rather than
count it as a separate annualized observation.

The relevant PST implementation is explicit:

- `calculate_pandl` concatenates positions and prices, forward-fills both,
  differences the filled price, and converts residual null returns to zero;
- portfolio P&L concatenates instrument curves and sums across columns, so a
  closed market does not erase another market's return;
- `accountCurve` calls `resample("1B").sum()`, normalizing the portfolio to a
  business-day series.

Therefore excluding every partial-book day is not a valid PST-parity repair.
It discards genuine returns from open markets and, in this run, removes a net
loss. The historical repair should normalize source dates to the intended
session/business date, add explicit zero contributions for verified closed
markets, and retain a separate invalid marker for genuine data gaps. An
unfinished final tail is different and should be excluded after the last
complete endpoint.

Strict-valid returns total 716.52%, while the complete economic cash series
totals 702.95%. Therefore:

- economic returns and P&L below include every saved cash contribution and
  reconcile to equity;
- Sharpe and Calmar below use only `ret_valid=true` dates and are diagnostic;
- dropping invalid dates is not a valid repair because it removes a net loss;
- a repair must create explicit zero rows only for verified market closures,
  normalize weekend/session labels before annualizing risk, and preserve
  invalid status for genuine missing data.

### Forecast gaps and the 2026 endpoint

Forecast handling is causal and conservative, but the calendar issue is not
irrelevant to forecast speed. Carry is computed only from same-session curve
observations, restarts its EWMA segment at a source change, invalid carry row,
or gap longer than seven calendar days, and persists a forecast for no more
than seven days while the executable contract is unchanged. Missing market
rows therefore do not leak future values or invent cross-gap P&L.

However, this implementation applies the 5/20/60/120 EWMAs over observed
rows. PST first places daily inputs on a business-day index. The extra SGX
Saturday rows consequently count as extra smoothing observations in the
stitched tail and should be session-normalized for exact span semantics.
Ordinary exchange holidays and short absences are otherwise safe for forecast
causality; their effect is a small timing difference rather than missing cash
P&L.

The saved 2026 tail is complete through November 11 for cash P&L: all 16 book
members have valid market P&L on that date. It is fully signal-populated only
through November 10; six members have a null carry forecast on November 11,
while their prior buffered contracts continue to earn valid P&L. On November
12 only `SCI` and `XINA50` are present, and November 13 and 16 contain none of
the 16 selected markets. Performance comparisons must therefore stop at
November 11, and a current live signal snapshot should not treat the saved
November 12–16 tail as complete.

## Selection breadth and constraints

Across 55 yearly reviews, the selector has 48.82 candidates on average
(range 4–105) and selects 12.11 instruments on average (range 4–18). Effective
book count averages 9.68 (range 3.13–15.16). The five largest structural
weights average 60.89% of the book. Average maximum book margin is $26,767.69
against the $37,500 ceiling; the maximum observed is $37,375.34.

Only two reviews satisfy every requested class, subclass, and minimum-book
target. The other 53 report `coverage_unmet_hard_feasibility_gate`. Summed
over reviews, final deficits are 20 class slots, 57 subclass slots, and 178
book-count slots. These are soft construction targets after hard feasibility,
not evidence that the margin gates were bypassed.

The 2026 book has 16 instruments, effective count 12.52, top-five structural
weight 49.99%, and maximum book margin $34,235.71. It is:

`ZAR, MHG, ZT, ZC, IBEX, MBT, CT, DJUSRE, GBM, BRE, SCI, XINA50, MZW, MJ, TSEREIT, J7`

## Calendar out-of-sample results

`Net return` includes all saved cash P&L and reconciles to the fixed-capital
equity curve. `Strict-valid return`, Sharpe, and Calmar exclude dates marked
invalid. Calmar is the annualized strict-valid arithmetic mean divided by the
within-year drawdown on the complete saved equity curve. The starred columns
are diagnostics until the portfolio calendar/validity treatment is fixed.

| Year | Net return | Strict-valid return | Invalid dates / return | Gross P&L | Costs | Sharpe* | Max DD | Calmar* |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1972 | 18.2% | 17.8% | 5 / 0.4% | $15,220 | $1,607 | 1.28 | −13.1% | 1.41 |
| 1973 | 81.9% | 81.9% | 7 / 0.0% | $62,204 | $765 | 4.92 | −5.7% | 15.01 |
| 1974 | 54.1% | 55.4% | 6 / −1.3% | $41,375 | $806 | 2.94 | −6.2% | 9.28 |
| 1975 | 24.9% | 26.3% | 5 / −1.4% | $20,011 | $1,347 | 1.76 | −2.8% | 9.60 |
| 1976 | 30.9% | 29.3% | 2 / 1.6% | $24,396 | $1,237 | 1.87 | −3.3% | 9.01 |
| 1977 | 5.1% | 5.3% | 5 / −0.2% | $5,521 | $1,696 | 0.33 | −3.8% | 1.45 |
| 1978 | 18.5% | 20.5% | 9 / −1.9% | $15,049 | $1,149 | 1.30 | −4.3% | 5.00 |
| 1979 | −72.2% | −69.7% | 6 / −2.5% | −$53,126 | $1,040 | −3.46 | −22.5% | −3.16 |
| 1980 | 27.8% | 22.7% | 6 / 5.0% | $21,574 | $759 | 0.96 | −10.5% | 2.22 |
| 1981 | 64.8% | 64.4% | 6 / 0.4% | $49,609 | $1,031 | 2.50 | −3.6% | 18.16 |
| 1982 | 22.2% | 20.7% | 6 / 1.5% | $17,532 | $882 | 1.05 | −4.8% | 4.42 |
| 1983 | 12.2% | 12.7% | 4 / −0.5% | $9,709 | $566 | 0.92 | −3.4% | 3.76 |
| 1984 | 72.3% | 69.1% | 7 / 3.2% | $55,011 | $810 | 2.81 | −3.2% | 21.91 |
| 1985 | 16.1% | 16.6% | 9 / −0.6% | $13,161 | $1,096 | 0.87 | −4.5% | 3.78 |
| 1986 | 11.2% | 10.8% | 5 / 0.3% | $9,284 | $906 | 0.73 | −2.1% | 5.20 |
| 1987 | −18.2% | −18.0% | 5 / −0.2% | −$12,756 | $881 | −1.11 | −5.9% | −3.10 |
| 1988 | 19.1% | 18.1% | 4 / 1.0% | $15,270 | $979 | 1.15 | −3.3% | 5.50 |
| 1989 | −10.3% | −10.4% | 6 / 0.1% | −$6,396 | $1,313 | −0.68 | −5.2% | −2.06 |
| 1990 | 41.8% | 40.3% | 8 / 1.4% | $32,309 | $984 | 2.12 | −2.2% | 18.45 |
| 1991 | 28.0% | 30.0% | 6 / −1.9% | $22,169 | $1,153 | 1.99 | −2.0% | 15.58 |
| 1992 | 10.0% | 10.5% | 9 / −0.5% | $9,553 | $2,039 | 0.44 | −5.6% | 1.91 |
| 1993 | 23.6% | 21.1% | 12 / 2.5% | $19,067 | $1,351 | 0.98 | −2.9% | 7.62 |
| 1994 | −21.2% | −21.4% | 10 / 0.3% | −$14,413 | $1,472 | −1.14 | −5.7% | −3.88 |
| 1995 | 9.6% | 9.8% | 10 / −0.2% | $8,893 | $1,691 | 0.69 | −2.8% | 3.58 |
| 1996 | 8.1% | 12.9% | 12 / −4.9% | $8,222 | $2,176 | 0.56 | −3.1% | 4.29 |
| 1997 | 1.3% | −1.2% | 10 / 2.4% | $2,657 | $1,706 | −0.07 | −3.0% | −0.39 |
| 1998 | 57.4% | 58.9% | 10 / −1.6% | $45,016 | $1,988 | 2.41 | −3.5% | 17.32 |
| 1999 | 5.4% | 4.8% | 12 / 0.6% | $5,388 | $1,358 | 0.28 | −3.1% | 1.62 |
| 2000 | −4.9% | −4.7% | 15 / −0.2% | −$2,200 | $1,499 | −0.26 | −4.2% | −1.17 |
| 2001 | 18.1% | 16.6% | 19 / 1.5% | $14,409 | $845 | 1.05 | −1.9% | 9.39 |
| 2002 | 16.2% | 13.8% | 13 / 2.4% | $13,346 | $1,222 | 0.83 | −4.4% | 3.28 |
| 2003 | 40.9% | 33.3% | 22 / 7.6% | $31,763 | $1,111 | 1.56 | −3.4% | 10.55 |
| 2004 | 25.2% | 23.6% | 38 / 1.7% | $20,002 | $1,084 | 1.39 | −2.3% | 11.81 |
| 2005 | 33.9% | 39.7% | 33 / −5.8% | $26,705 | $1,293 | 2.71 | −1.1% | 42.15 |
| 2006 | 30.8% | 36.1% | 31 / −5.3% | $24,178 | $1,065 | 2.17 | −1.4% | 29.13 |
| 2007 | −9.1% | −15.0% | 35 / 5.9% | −$5,713 | $1,112 | −1.01 | −2.6% | −6.45 |
| 2008 | 11.4% | 12.4% | 16 / −1.0% | $9,060 | $492 | 0.65 | −1.4% | 9.31 |
| 2009 | 2.6% | 6.3% | 17 / −3.7% | $2,424 | $452 | 0.41 | −1.5% | 4.47 |
| 2010 | 9.3% | 10.4% | 36 / −1.1% | $7,921 | $981 | 0.67 | −2.3% | 5.12 |
| 2011 | 42.9% | 44.6% | 35 / −1.6% | $33,027 | $822 | 2.71 | −1.5% | 34.01 |
| 2012 | 16.1% | 16.6% | 30 / −0.5% | $13,726 | $1,688 | 1.15 | −1.3% | 13.95 |
| 2013 | −10.4% | −11.9% | 44 / 1.6% | −$6,325 | $1,444 | −0.70 | −3.9% | −3.62 |
| 2014 | 31.6% | 29.7% | 54 / 1.9% | $26,088 | $2,389 | 1.91 | −1.0% | 35.89 |
| 2015 | −24.9% | −24.4% | 48 / −0.6% | −$16,961 | $1,748 | −1.13 | −4.4% | −6.61 |
| 2016 | −23.3% | −25.0% | 27 / 1.7% | −$15,295 | $2,167 | −1.41 | −4.8% | −5.75 |
| 2017 | 17.9% | 6.8% | 41 / 11.1% | $15,562 | $2,161 | 0.50 | −1.7% | 4.64 |
| 2018 | −18.4% | −13.6% | 55 / −4.8% | −$12,078 | $1,745 | −1.23 | −2.7% | −6.31 |
| 2019 | −13.9% | −5.8% | 59 / −8.1% | −$8,822 | $1,621 | −0.50 | −2.9% | −2.57 |
| 2020 | −21.9% | −18.9% | 44 / −3.0% | −$14,633 | $1,800 | −0.97 | −5.6% | −3.86 |
| 2021 | −10.5% | −13.3% | 56 / 2.8% | −$6,084 | $1,829 | −0.69 | −4.3% | −3.28 |
| 2022 | −2.7% | −6.1% | 63 / 3.4% | −$928 | $1,095 | −0.42 | −2.4% | −2.67 |
| 2023 | 23.0% | 25.6% | 76 / −2.6% | $18,750 | $1,483 | 1.47 | −2.0% | 13.67 |
| 2024 | −13.4% | −0.6% | 249 / −12.7% | −$9,095 | $932 | −0.16 | −3.4% | −0.80 |
| 2025 | 5.8% | 11.6% | 83 / −5.8% | $5,793 | $1,427 | 0.82 | −1.6% | 8.22 |
| 2026 | −11.5% | −10.3% | 54 / −1.2% | −$7,592 | $1,028 | −0.69 | −3.6% | −3.69 |

## Conclusion and primary artifacts

The carry forecast, span-specific pooled turnover estimates, measured combined
turnover, trade costs, and roll costs are wired into the saved run. The saved
run demonstrates the former weak-challenger admission bug; current code now
restricts probation to incumbents. The micro-ag sizing is correct, although
route churn remains an open policy choice because no route hysteresis or
switching penalty is active.

The proposed baseline should be rerun after probation admission and the
return-calendar issue are both resolved. Until then, the economically
reconciled P&L is useful for debugging, but the 0.748 Sharpe and 0.504 Calmar
should not be used to decide whether carry improves the fixed $75,000 book.

Primary evidence is in the run's `carry_selection_review_instruments`,
`review_trials`, `review_trial_allocations`, `review_final_allocations`,
`forecast_audit`, `forecasts`, `candidates`, `cash_pnl`, `portfolio_curve`,
`reviews`, and `book` Parquet artifacts. The probation design comparison is
in `research/selection/ewmac-inactive-contract-probation-audit-20261113.md`.

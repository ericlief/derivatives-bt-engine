# Carry integration for the fixed-$75k selection book

**Date:** 2026-11-12

**Status:** carry-only selector implemented and audited against the copied IB
history database

**Trigger:** the [November 11 fixed-cap signal audit](selection-capfixed-signal-audit-20261111.md)

**Prior design:** [carry and long-history plan](../futures-data/pysystemtrade-carry-and-long-history-plan.md)

## Implementation update

The first raw-carry implementation now lives in
`calculations/carry.py` and is wired end to end through the selection CLI. The
first pass follows the explicit AFTS strategy-ten specification while keeping
the current pysystemtrade calculation semantics:

- carry smoothing uses pandas-COM-equivalent EWMAs for Carry5, Carry20,
  Carry60, and Carry120;
- every smoothed rule uses a fixed scalar of 3 on this repository's
  divided-by-ten forecast scale, equivalent to Carver's scalar of 30 and
  +/-20 cap;
- the cost-eligible carry rules have equal pre-FDM weights and a +/-2 final
  cap;
- rule eligibility uses AFTS Table 40 annual turnover estimates of 5.75,
  3.12, 1.82, and 1.22 respectively, combined with each instrument's trade
  and roll SR costs; it does not reuse EWMAC turnover;
- the final-rule-count FDM table from AFTS Table 52 is the selected policy,
  giving 1.23 for four carry rules and 1.34 for five EWMAC plus four carry
  rules;
- the default mixed forecast allocates 60% to the existing EWMAC trend family
  and 40% equally across carry, then applies one nine-rule FDM. Family-only
  FDMs are not applied again inside the mixed forecast.

This FDM choice is intentionally distinct from AFTS Table 45, which gives a
carry-specific 1.04 multiplier for four extremely correlated carry smooths.
The requested implementation uses the later generic final-rule-count table.
Carver has also stated that applying a moving-average smooth does not change
the intended carry scalar except at the extremes, confirming that the same
30 (3 here) scalar applies to all four smoothed variants.

The module keeps same-date legs, raw roll, contract-year fraction, annualized
roll, annualized point volatility, and exclusion reasons in the calculation
frame. It restarts EWMA state at a provider change, invalid observation, or a
calendar gap longer than seven days. A forecast may persist for at most seven
calendar days only when the executable current contract is unchanged; the
source observation date and staleness remain visible for audit.

Static reference and stitched carry forecasts are content-addressed cache
artifacts. The mutable IB tail is built from the cached forecast, capped at
each signal's exchange-local market-snapshot endpoint, and then passed through
the existing executable cash-P&L and annual selection path.

### Carry-only full-selection audit

The first complete fixed-$75,000 run used the November 11 market snapshot and
the copied `/home/dev/fin/db/ib_futures_history.duckdb`. The live candidate
screen retained 113 executable instruments; the historical annual-rebalance
universe retained 173 reference-cost candidates. Of those, 132 passed the
carry forecast and reference-return coverage audit. Thirty-nine failed the
declared 90% carry-coverage floor and two lacked the minimum 256 valid forecast
observations.

The cost gate retained all four rules for 170 candidates, Carry20/60/120 for
one, and Carry60/120 for two. The resulting FDM values are therefore 1.23,
1.03, and 1.02 from the selected final-rule-count table. The final 2026 review
selected 11 instruments at the account's integer-contract and 50% book-margin
limits. The stitched cash panel has unique `(symbol, date)` identities, no
invalid P&L rows while an instrument is active, and reconciles to the public
portfolio curve within the shared report-rounding tolerance.

This is an implementation and integrity result, not evidence that carry will
put food on the table. The maximal-history fixed-cap run is useful as a smoke
test, but promotion still requires the pre-registered common-sample,
era-by-era, drawdown, and trend/carry comparison below.

## Recommendation

Use the completed carry-only full-selection run as an engineering smoke test,
then make the first decision-grade comparison:

1. the saved EWMAC fixed-cap baseline;
2. carry alone;
3. EWMAC/carry at the AFTS 60/40 default family weights;
4. EWMAC/carry at 75/25 and 50/50 sensitivity weights;
5. TSMOM 64/256/carry with 60/40 as the default and the same sensitivities.

Use the fixed carry scalar and final-rule-count FDM described above, cap at
+/-2, and keep target volatility, fixed capital, selected book, contract
accounting, and costs unchanged. This isolates whether carry adds information.
The selector plumbing and carry-aware cost eligibility are now available, but
the full-selection result should not be promoted unless those frozen-book and
common-sample comparisons pass.

Carry is a plausible diversifier, not a remedy for the account constraint.
The fixed-cap audit still shows that a $75,000 account supports a smaller book
than the compounding runs. A better forecast can improve return per unit risk,
but it cannot make an unaffordable contract divisible or guarantee income.

## Why carry is the next sensible family

The audit found that EWMAC and TSMOM 64/256 were the strongest fixed-cap
signals, with Sharpe ratios of 1.11 and 1.12. They are both trend families and
share much of the same directional information. Goulding changed the trend
construction but did not add a structurally different source of expected
return; its fixed-cap Sharpe was 0.77 in Sharpe mode and 0.64 in continuous
mode.

Carry measures the annualized slope between two dated futures contracts rather
than recent price direction. It can therefore agree with trend, offset it, or
remain strong when trend is weak. The useful question is not whether carry has
the best standalone historical Sharpe. It is whether a lagged, costed carry
family raises the fixed book's out-of-sample Sharpe and adjusted Calmar without
concentrating the improvement in one era or asset class.

The comparison must be risk-normalized. Simply averaging an additional
forecast into the current signal can reduce its magnitude and apparent return.
Conversely, applying an untested forecast-diversification multiplier can
manufacture more exposure. The fixed divided-by-ten carry scalar plus the
pre-registered final-rule-count FDM keeps variants on the repository's
mean-absolute-one, cap-two convention without fitting scale to this book.

## Current pysystemtrade implementation

The local upstream checkout is on commit
`b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8` dated 2026-07-18. Its carry path
is split between `systems/rawdata.py`, `sysobjects/carry_data.py`, and
`systems/provided/rules/carry.py`.

For one same-timestamp row it calculates:

```text
raw_roll = PRICE - CARRY

year_fraction = year_fraction(CARRY_CONTRACT)
              - year_fraction(PRICE_CONTRACT)

annualised_roll = raw_roll / year_fraction

raw_carry = daily_annualised_roll
          / (daily_point_volatility * sqrt(256))
```

Contract IDs are interpreted as year plus month/12. A zero contract gap is
floored in absolute value to one calendar day, and a zero price differential
is treated as missing. The implementation resamples intraday annualized roll
to business days with a mean before dividing by annualized point volatility.

The rule then applies pandas `raw_carry.ewm(smooth_days).mean()`. Because the
argument is positional, `smooth_days` is pandas' `com`, not its `span`; a
Polars port must use equivalent alpha semantics rather than silently passing
the number as `span`. The robust configuration defines 10, 30, 60, and
125-session variants, fixed forecast scalars of approximately 27.82, 28.38,
28.40, and 29.37, and a +/-20 forecast cap.

On the repository's divided-by-ten forecast convention those values are
2.782, 2.838, 2.840, and 2.937. The implementation uses the requested common
default of 3.0, close to every upstream value. The production-style
pysystemtrade config also contains instrument-specific weights across many
rule families; copying those weights would not answer the incremental carry
question for this book.

Two upstream details require explicit choices:

- The imported `daily.carry` table selects the last complete same-row snapshot
  for each session. Upstream calculates the business-day mean of intraday
  annualized roll. The parity report should quantify this difference using
  `raw.multiple_prices`; the source-neutral stitched forecast should retain the
  deterministic daily convention unless the difference is material.
- The robust upstream config uses a 35-day fast/20-year slow mixed volatility
  and permits early backfilling. The repository's existing causal estimator is
  32 sessions with a 10-year slow component weighted 30%, no future backfill,
  and 256-day annualization. Use the repository estimator for the scored test
  so carry and trend share one causal risk convention; use upstream settings
  only for parity diagnostics.

## Data readiness

The reference sidecar already contains the required four-field carry rows and
the source-neutral `FuturesHistory.carry` stream. No source market data needs
to be changed.

### Re-audit after the staged IB database copy

The copied `/home/dev/fin/db/ib_futures_history.duckdb` materially changes the
tail-readiness conclusion from the initial audit. It contains 579,483 daily
bars for 2,693 contracts and 163 reference instruments, spanning 2021-12-20
through 2026-11-12. All 4,803 recorded fetches have `success` status, and all
163 configured instruments build through `IBHistoryProvider` without an
exception.

The raw store has no duplicate instrument/contract/date keys, future-dated
rows, invalid closes, invalid volumes, or missing contract months. It does
contain 478 rows on or after the recorded contract expiration date. The
configured roll rules exclude all of those rows from the selected marks for
the 17 current-book histories, so this is a source-metadata diagnostic rather
than contamination of the audited stitch.

The selection-v1 history path deliberately uses `include_globex=False` and
`preserve_reference_overlap=True`. Replaying that exact policy, including its
three-prior-year IB request, stitches all 17 current-book histories directly
from pysystemtrade to IB and extends carry through 2026-11-12. From 2024-04-01
onward it has 10,282 valid carry observations for 11,238 signal dates: 91.5%
observation-weighted coverage and 94.0% median market coverage. Source gaps at
the handoff are one to five calendar days except US2/ZT at 26 days; all are
within the stitcher's 31-day limit and their unknown cross-gap return is
omitted rather than invented.

The remaining fixed-book quality issue is concentrated rather than systemic.
After their first valid IB carry row, CANOLA/RS has 84.9% coverage with 81
missing dates and SUGAR_WHITE/W has 85.4% with 80 missing dates. The other 15
markets are at least 99.2% complete after their first valid row. Carry must
therefore expose validity and staleness and reject an unexplained gap; it must
not silently turn missing observations into a zero forecast.

PST and IB have little usable same-contract overlap because most IB histories
begin as the reference history ends. SONIA3 is the one current-book market
with a substantial same-pair comparison: 203 dates, 0.994 correlation in
annualized roll, 100% sign agreement, and zero median absolute difference.
That is encouraging but not enough to replace the proposed multi-market parity
report.

### Audited selection universe

The November 11 EWMAC screen has 208 distinct reference histories after
deduplicating execution overlays. A history is called selection-eligible below
when any of its execution overlays is eligible. All 208 have mark history and
some carry data.

| Screen subset | Histories | Mean carry-day coverage | Median coverage | At least 90% | Latest reference carry |
|---|---:|---:|---:|---:|---:|
| Currently selection-eligible | 109 | 86.4% | 98.6% | 86 | 2024-03-29 |
| Currently ineligible | 99 | 91.6% | 99.9% | 81 | 2024-03-29 |

The current EWMAC fixed book contains 17 markets. Thirteen have at least 95.9%
reference carry coverage. Four need explicit sparse-data treatment:

| Executable | Reference history | Carry-day coverage |
|---|---|---:|
| SX7E | EU-BANKS | 18.2% |
| MYM | DOW | 23.6% |
| M2K | RUSSELL | 40.5% |
| GBS | SHATZ | 68.0% |

Raw carry legs must never be assembled across dates or filled through a
contract change. A daily forecast may persist through a short missing-data gap
only when the current/carry contract pair is unchanged, with a declared
staleness limit and an audit flag. Missing carry is not a zero forecast.

### Stitching readiness

The stitching machinery is partly ready:

- `PysystemtradeHistoryProvider` emits populated carry through 2024-03-29.
- `IBHistoryProvider` already constructs configured carry companions with
  `_build_configured_carry_frame`.
- `stitch_futures_histories` applies the same date ownership to carry as to the
  other streams and records the source segment.
- `GlobexHistoryProvider` currently emits an empty carry frame. When Globex
  owns the middle segment, the stitched carry stream therefore has a hole.

A read-only prototype parsed Globex contract months from standard futures
symbols, applied each reference instrument's held-contract cycle and carry
offset, and joined the companion contract on the same date. Of the 15
non-rejected crosswalk rows configured to use Globex, 13 have Globex futures
rows. Across those markets the prototype found 48,476 complete carry days out
of 53,562 selected days: 90.5% observation-weighted coverage and 98.9% median
market coverage, through 2026-06-18.

| Coverage band | Markets |
|---|---|
| 99% to 100% | ES, NQ, CL, GC, ZL, ZS |
| 90% to 99% | ZC, ZW, 6J |
| 80% to 90% | SI, ZN |
| Below 80% | ZT (63.3%), 6M (68.4%) |
| No Globex futures rows under mapped asset | TN, UB |

BRL/`6L` remains excluded from Globex stitching by the reviewed mapping. The
copied IB store now supplies the recent tail. The exact fixed-selection path
does not use Globex, so the empty Globex carry frame no longer blocks the
frozen-book experiment. The Globex bridge remains necessary for generic
three-source histories and for extending IB-limited instruments across the
middle segment, but it should be treated as a separate expansion gate rather
than a prerequisite for the first fixed-book carry result.

This bridge prototype is feasibility evidence, not an approved mapping change.
Globex contract month must be decoded from the contract symbol, not inferred
from expiration month: WTI December contracts, for example, expire in
November. Treasury scale and the known seasonal roll-policy differences remain
source-overlap diagnostics.

## Proposed implementation boundary

### 1. Pure carry calculations

Add `calculations/carry.py` with pure Polars functions for:

- contract-month parsing and signed year fractions;
- raw roll and annualized roll;
- same-date and same-pair validation;
- causal point-volatility normalization;
- pandas-`com`-equivalent EWMA smoothing;
- fixed scalar-3 scaling, final-rule-count FDM, and +/-2 capping;
- staleness and rejection reasons.

The public daily result should use `date` and include enough audit columns to
reproduce every value: current and carry prices/contracts, year fraction,
annualized roll, point volatility, raw carry, smoothing horizon, scalar,
forecast, source segment, available date, staleness, and validity reason.

### 2. Source construction

Move the generic configured carry-frame builder out of the IB loader so IB and
Globex share one implementation. Extend the Globex all-contract query with the
stored contract symbol, decode its contract month, build carry before reducing
the selected surface, and increment the history/cache schema versions. Keep
contract-month decoding covered by WTI, grains, FX, equity, metals, and rates
tests.

The stitch must change source only at the existing ownership handoffs. Around
each handoff, report contract pairs, raw/annualized carry, forecast difference,
and the first valid post-handoff date. Do not smooth across an unexplained
source gap as if it were observed data.

### 3. Forecast adapter

Add a selection-compatible carry builder that returns
`CombinedForecastResult`, using the same execution frame as return TSMOM and
Goulding. Build the four upstream horizons, scale each variant by 3, form one
equal-weight carry-family forecast, and apply the AFTS FDM for the final rule
count once before the final cap.

Add a family-level combination policy rather than treating four correlated
carry horizons as four independent votes against five EWMAC speeds. The first
weights are fixed experiment parameters, not estimated expected-return
weights. Save family forecasts, component weights, final-rule count and FDM,
cap hits, agreement with trend, and forecast turnover.

### 4. Selection integration

Evaluate carry along two complementary paths:

1. **Frozen-book signal test.** Replay the same dated EWMAC fixed-cap books
   with carry variants. This keeps market membership and the fixed-$75,000
   feasibility path out of the signal comparison.
2. **Full selection test.** Add carry-specific forecast eligibility and
   turnover/cost fields, then allow annual selection to change. Do not reuse
   EWMAC turnover or its cost gate as a proxy for carry.

The full-selection path has now supplied the requested first engineering pass.
The frozen-book path remains necessary for attribution because changing the
signal and selected book simultaneously cannot identify where an improvement
came from.

## Pre-registered experiment

Use the same fixed capital, 20% target volatility, yearly reviews, class and
subclass constraints, execution costs, and fixed-cap return convention as the
November 11 audit.

| Variant | Trend family weight | Carry family weight | Purpose |
|---|---:|---:|---|
| EWMAC baseline | 100% | 0% | Saved benchmark |
| Carry only | 0% | 100% | Standalone information and cost check |
| EWMAC/carry default | 60% | 40% | AFTS top-down family allocation |
| EWMAC/carry trend tilt | 75% | 25% | Higher-trend sensitivity |
| EWMAC/carry equal family | 50% | 50% | Equal-family sensitivity |
| TSMOM 64/256 baseline | 100% | 0% | Strongest audited mean return |
| TSMOM/carry default | 60% | 40% | Default incremental carry to slow trend |
| TSMOM/carry trend tilt | 75% | 25% | Higher-trend sensitivity |
| TSMOM/carry equal family | 50% | 50% | Equal-family sensitivity |

Report both maximal available history and a common valid sample. Break results
out by decade, asset class, and calendar year, with special attention to
2024-2026 rather than choosing weights to repair those years. Use strictly
prior observations for scaling and apply forecasts no earlier than the next
return interval.

For each fixed-book and full-selection run report:

- annualized arithmetic mean, volatility, Sharpe, fixed-cap adjusted Calmar,
  and percentage/dollar drawdown;
- gross P&L, trade and roll costs, turnover, and cost reconciliation;
- forecast correlation, sign agreement, cap hits, and trend/carry
  cancellation;
- valid coverage, stale observations, source handoffs, and exclusion reasons;
- selected market count and book changes in the full-selection pass;
- calendar-year results under the same OOS labeling convention as the audit.

Do not promote carry because full-history Sharpe rises by a small amount. The
case is credible only if the improvement survives the common sample, costs,
multiple eras, and the fixed-$75,000 integer-contract replay without a worse
drawdown profile. The 60/40 variant is the default challenger; 75/25 and 50/50
remain named robustness checks rather than weights selected after seeing this
book's results.

## Implementation order and acceptance gates

1. **Parity report:** reproduce upstream raw and smoothed carry for at least 10
   representative instruments and explain daily-snapshot differences.
2. **Fixed-book stitch audit:** verify the direct reference-to-IB handoffs and
   demonstrate that no leg or EWMA state crosses an invalid contract/source
   gap.
3. **Globex bridge:** populate and cache carry for mapped assets when expanding
   beyond the fixed-selection path; require same-date contract legs and
   publish coverage/rejection counts.
4. **Generic stitch audit:** verify three-source handoffs and demonstrate that
   no leg or EWMA state crosses an invalid contract/source gap.
5. **Pure-rule study:** score carry horizons and the carry family on reference
   history with lagged normalized returns.
6. **Frozen-book replay:** run the nine pre-registered fixed-cap variants.
7. **Full selector:** use the implemented carry-specific cost eligibility to
   rerun annual selection after the frozen-book result is broad and
   cost-effective; treat the current run as a plumbing audit.

The smallest next research task is therefore the parity report followed by the
frozen-book comparison. The carry calculation, explicit sparse-gap policy,
static forecast cache, direct PST-to-IB tail, cost eligibility, and full
selector are implemented. The Globex carry builder is still required before
relying on the generic three-source path.

## Evidence inspected

- Fixed and compounding artifacts under
  `/home/dev/data/fin/misc/results/selection_v1/{ewmac,return_tsmom,goulding}/`.
- Reference sidecar `/home/dev/fin/db/pysystemtrade_reference.duckdb`, schema
  v5, including `raw.multiple_prices`, `daily.marks`, and `daily.carry`.
- Globex contract surface `/home/dev/fin/db/globex_mdp_3.0.duckdb` through
  2026-06-18.
- IB dated-contract store `/home/dev/fin/db/ib_futures_history.duckdb`.
- Local upstream pysystemtrade checkout at the pinned source commit above.
- Repository history, overlap, IB carry, forecast-combination, volatility, and
  selection pipeline implementations.

This research performed read-only data checks and did not regenerate or
overwrite any saved backtest result.

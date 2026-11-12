# Carry integration for the fixed-$75k selection book

**Date:** 2026-11-12

**Status:** implementation research, experiment recommendation, and post-copy
data re-audit

**Trigger:** the [November 11 fixed-cap signal audit](selection-capfixed-signal-audit-20261111.md)

**Prior design:** [carry and long-history plan](../futures-data/pysystemtrade-carry-and-long-history-plan.md)

## Recommendation

Add absolute futures carry as a normalized forecast family beside trend, then
test it on the existing fixed-$75,000 book before allowing it to change market
selection. The first decision-grade comparison should be:

1. the saved EWMAC fixed-cap baseline;
2. carry alone;
3. EWMAC/carry at 75/25 family weights;
4. EWMAC/carry at 50/50 family weights;
5. TSMOM 64/256/carry at the same two weights.

Use one causal pooled scale after each family is formed, target mean absolute
forecast 1, cap at +/-2, and keep target volatility, fixed capital, selected
book, contract accounting, and costs unchanged. This isolates whether carry
adds information. Only after that comparison passes should the selector be
rerun with carry-aware eligibility and turnover costs.

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
manufacture more exposure. A causal pooled post-combination scale makes the
forecast variants comparable on the repository's existing mean-absolute-one,
cap-two convention.

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

Those fixed scalars are useful parity fixtures, not the recommended research
normalization. The selection engine already estimates causal pooled scales and
uses the divided-by-ten convention. The production-style pysystemtrade config
also contains instrument-specific weights across many rule families; copying
those weights would not answer the incremental carry question for this book.

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
- pooled causal scaling and +/-2 capping;
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
Goulding. Build the four upstream horizons, scale each variant causally, form
one equal-weight carry-family forecast, and then scale the family once to the
common unit convention.

Add a family-level combination policy rather than treating four correlated
carry horizons as four independent votes against five EWMAC speeds. The first
weights are fixed experiment parameters, not estimated expected-return
weights. Save family forecasts, weights, pooled scale history, cap hits,
agreement with trend, and forecast turnover.

### 4. Selection integration

Implement carry in two passes:

1. **Frozen-book signal test.** Replay the same dated EWMAC fixed-cap books
   with carry variants. This keeps market membership and the fixed-$75,000
   feasibility path out of the signal comparison.
2. **Full selection test.** Add carry-specific forecast eligibility and
   turnover/cost fields, then allow annual selection to change. Do not reuse
   EWMAC turnover or its cost gate as a proxy for carry.

The second pass is necessary for the final strategy result, but it should not
be the first result: changing the signal and selected book simultaneously
would make an improvement impossible to attribute.

## Pre-registered experiment

Use the same fixed capital, 20% target volatility, yearly reviews, class and
subclass constraints, execution costs, and fixed-cap return convention as the
November 11 audit.

| Variant | Trend family weight | Carry family weight | Purpose |
|---|---:|---:|---|
| EWMAC baseline | 100% | 0% | Saved benchmark |
| Carry only | 0% | 100% | Standalone information and cost check |
| EWMAC/carry conservative | 75% | 25% | Lower-risk first integration |
| EWMAC/carry equal family | 50% | 50% | Hierarchical `1/N` baseline |
| TSMOM 64/256 baseline | 100% | 0% | Strongest audited mean return |
| TSMOM/carry conservative | 75% | 25% | Incremental carry to slow trend |
| TSMOM/carry equal family | 50% | 50% | Family diversification bound |

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
drawdown profile. The 75/25 variant should be the default challenger if it
captures most of the diversification with materially less forecast turnover
or cancellation than 50/50.

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
6. **Frozen-book replay:** run the seven pre-registered fixed-cap variants.
7. **Full selector:** add carry-specific cost eligibility and rerun annual
   selection only if the frozen-book result is broad and cost-effective.

The smallest next coding task is therefore the pure Polars carry module plus
the parity report. The existing data model, reference carry stream, selection
forecast result type, direct reference-to-IB tail, and stitch ownership rules
can be reused. There is no remaining acquisition blocker for the 17-market
frozen-book experiment. The material missing pieces are the carry calculation,
explicit sparse-gap policy, and parity evidence; the Globex carry builder is
still required before relying on the generic three-source path.

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

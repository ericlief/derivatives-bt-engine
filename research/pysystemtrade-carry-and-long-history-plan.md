# Carry forecasts and long-history futures research

**Status:** research and implementation proposal

**Date:** 2026-09-18

**Related data audit:** [pysystemtrade-data.md](pysystemtrade-data.md)

## Executive recommendation

Use the imported `pysystemtrade` histories first as a **separate, return-space
research panel**, not as a replacement for the Globex execution data and not as
an input masquerading as ordinary OHLCV.

That panel is well suited to answering three questions over a much longer
sample:

1. Does an absolute carry forecast add diversifying returns to the repository's
   existing TSMOM and Goulding forecasts?
2. Which trend and carry horizons are genuinely distinct, rather than highly
   correlated copies of one another?
3. Do simple forecast combinations—especially equal or hierarchical `1/N`
   weights—survive causal out-of-sample tests as well as estimated weights?

The first implementation should produce normalized pure-rule returns for all
usable Carver instruments. It should not claim exact dollar P&L, realistic roll
execution, or an exact reconstruction of Carver's current production system.
Those require contract specifications, FX conversion, costs, and approved
Carver-to-Globex mappings. A second tier can use the existing contract ledger
for the approved subset later.

MongoDB is not required. It is a persistence choice inside `pysystemtrade`, not
part of the carry calculation. The immutable DuckDB sidecar already gives this
repository the useful relational boundary:

- source data remains untouched;
- source provenance and import validation remain inspectable;
- the existing Globex database and backtests cannot be silently contaminated;
- Polars can read the resulting tables into the research layer.

## What “carry” means here

This is a futures-curve forecast, not a separate trade funded by explicitly
borrowing cash. In Carver's framework, the forecast asks whether the contract
being priced is cheap or expensive relative to another dated contract, after
normalizing the price difference by the time between their expiries and by
recent risk.

For the `multiple_prices` fields, the parity target is:

```text
raw_futures_roll = PRICE - CARRY

year_fraction = contract_date(CARRY_CONTRACT)
              - contract_date(PRICE_CONTRACT)

annualised_roll = raw_futures_roll / year_fraction

raw_carry = annualised_roll
          / (daily_price_volatility * sqrt(256))
```

Here `PRICE` is the price of the contract the system is notionally holding and
`CARRY` is the comparison contract. The sign depends on their ordering. Early
printings of *Systematic Trading* contained a reversed carry definition in
Appendix B; Carver published the correction and noted that his code already had
the intended sign. Any implementation and unit tests should follow the code and
correction, not the erroneous printing ([Carver's correction](https://qoppac.blogspot.com/2015/11/typo-in-definition-of-carry-rule-for.html)).

The raw value is then smoothed. Current `pysystemtrade`'s robust example system
uses 10, 30, 60, and 125-day carry smooths; older explanations use a 90-day
smooth. Forecasts are conventionally scaled to average absolute 10 and capped
at ±20 before combination.

This produces a directional futures forecast:

- positive carry can support a long position;
- negative carry can support a short position;
- trend and carry can agree, offset one another, or cancel;
- realized profit still comes from holding a futures position, including the
  curve movement and roll path embedded in its returns.

The seasonal contract choice and the forecast are separate layers. For example,
Carver can deliberately hold December corn or winter crude as the preferred
tradable curve point, while the carry rule measures that chosen contract against
its comparison contract. “Choose a sensible contract to hold” is therefore not
the same rule as “always trade the steepest point on today's curve.” The source
files preserve Carver's chosen `PRICE_CONTRACT` and `CARRY_CONTRACT`, but not the
complete daily contract surface needed to retest every alternative curve-point
selection.

## Reading guide: where carry fits in Carver's framework

The relevant sections support three distinct operations: construct a forecast,
combine forecasts, and convert a combined forecast into a position.

### *Systematic Trading*

- Chapter 7, “Forecasts,” explains how trading rules such as trend and carry
  become comparable, risk-normalized forecasts.
- Chapter 8, “Combined forecasts,” separates forecast diversification and rule
  weights from the later portfolio allocation problem.
- Appendix B defines the trading rules, including carry, subject to the sign
  correction above.

The implication for this repository is that carry belongs beside trend as a
forecast family. It should not be inserted into price returns, treated as an
interest payment, or allowed to alter instrument allocation before its isolated
behavior is measured. See the publisher's [book description and contents](https://www.harriman-house.com/systematic-trading).

### *Leveraged Trading*

Chapter 8 covers adding trading rules, while Appendix C discusses back-adjusted
futures prices. Together they reinforce an important data separation:

- use an adjusted, continuous representation to infer trend;
- use actual same-date contract prices to infer carry;
- do not calculate percentage returns from a backward-adjusted level that can
  be zero or negative.

The official companion material explicitly includes a Chapter 8 carry-rule
calculation ([book page](https://www.harriman-house.com/leveragedtrading),
[resources](https://www.systematicmoney.org/leveraged-trading-resources)).

### *Advanced Futures Trading Strategies*

The most relevant sequence is Strategy 10 (basic carry), Strategy 11 (combined
carry and trend), Strategy 12 (adjusted trend), Strategy 13 (trend and carry in
different risk regimes), Strategy 15 (accurate carry), and Strategy 16 (trend
and carry allocation). Strategies 19 and 20 then distinguish cross-sectional
momentum and carry from their time-series counterparts. The official contents
are available on the [book information page](https://www.systematicmoney.org/advanced-futures-information).

These chapters motivate a staged research design: establish the absolute carry
forecast first, then combine it with trend, then test relative/cross-sectional
variants only after broad and consistent asset-class panels exist.

### Supporting empirical evidence

Carry is not merely a coding convention. Koijen, Moskowitz, Pedersen, and Vrugt
find that carry predicts returns across several asset classes, both in the cross
section and through time, while its poor periods share macroeconomic and global
risk characteristics. That makes it a plausible trend diversifier, but not a
free or permanently independent return source ([NBER working paper](https://www.nber.org/papers/w19325.pdf)).

Carver has also shown that the magnitude of a non-binary carry forecast contains
information about subsequent risk-adjusted returns, supporting a continuous
forecast rather than only a sign ([discussion](https://qoppac.blogspot.com/2020/07/)).

## What the imported data can support

The sidecar currently contains 252 instruments and 7,249,183
`raw.multiple_prices` rows spanning 1,251,845 normalized instrument-days. It is
not literally one observation per day: there are about 5.79 rows per
instrument-day on average, and every instrument has at least one date with
multiple intraday observations. The snapshots are nevertheless irregular and
do not form a full OHLCV contract surface. The deterministic research
convention should remain the latest observation satisfying each stream's
completeness constraint on a trade date. Importer schema v3 maps Sunday
timestamps to Monday `trade_date` while retaining the source timestamp for
audit. Carry's four fields must come from one complete source row; they must
never be assembled column by column from different snapshots.

### Breadth and history

| Asset class | Instruments | Earliest date | Median history | Mean carry-day coverage |
|---|---:|---:|---:|---:|
| Equity | 57 | 1982-09-14 | 11.1 years | 80.9% |
| FX | 43 | 1972-09-14 | 21.8 years | 96.8% |
| Agricultural | 36 | 1969-12-02 | 35.4 years | 93.4% |
| Sector | 34 | 2005-06-10 | 8.8 years | 86.0% |
| Bonds | 34 | 1978-05-31 | 16.2 years | 81.9% |
| Metals | 21 | 1970-06-15 | 10.2 years | 98.5% |
| Oil and gas | 20 | 1980-02-19 | 26.2 years | 97.7% |
| Volatility | 4 | 2005-11-22 | 14.7 years | 99.4% |
| Housing | 2 | 2021-11-12 | 2.3 years | 100.0% |
| Other | 1 | 2021-10-14 | 2.5 years | 100.0% |

Across all instruments, 30 begin before 1980, 55 before 1990, 80 before 2000,
and 133 before 2010. Mean, median, and observation-weighted carry coverage are
89.5%, 98.9%, and 90.0%; 205 instruments have at least 90% coverage, while 26
have less than 50%. Those masks must be visible in results rather than silently
turning missing carry into a zero forecast.

The longest series include orange juice, cotton, soybean oil, sugar, and cocoa
at roughly 54 years. Corn has about 51.4 years of history and 91.1% carry-day
coverage. This is the main advantage over the purchased Globex sample: slow
rules, decade slices, regime variation, and causal estimator stability can be
tested over materially more history and many more markets.

### The three source streams

The existing [futures history abstraction](../src/derivatives_bt_engine/domain/futures_history.py)
already represents the correct conceptual split:

| Stream | Source fields | Valid use |
|---|---|---|
| Signal | adjusted price changes divided by current raw price | Trend features and normalized returns |
| Marks | current raw price and `PRICE_CONTRACT` | Contract identity, roll flags, later accounting audits |
| Carry | same-row `PRICE`, `CARRY`, and both contract IDs | Carry forecast construction |

The signal stream is reconstructed as a positive index from
`Δ adjusted_price / current_price`. This avoids invalid `pct_change()` results
when backward Panama adjustment drives early levels through zero. A negative
adjusted level is not itself a bad trend observation; its level is an arbitrary
result of carrying later roll gaps back through history. The normalized change
is the economically meaningful input.

The carry stream must use all four values from the same source row. It must not
forward-fill one contract leg across a contract-ID change or combine fields from
different intraday snapshots.

### Hard limits

The source does **not** support, on its own:

- testing every candidate contract or dynamically selecting the steepest curve
  point;
- reconstructing volume/open-interest-based roll decisions;
- realistic intraday fills, bid/ask spreads, or exact roll slippage;
- ordinary OHLC-based breakout, stop, or execution simulations;
- assuming a Carver contract is the same economic path as a Globex continuous
  symbol merely because their labels resemble one another.

The existing overlap study found materially different roll timing in examples
such as corn. That is useful source-robustness evidence, not evidence that one
series may be spliced into the other as though their returns were identical.

## Why this should not enter the current TSMOM engine directly

The production-style path in
[tsmom_backtester.py](../src/derivatives_bt_engine/domain/tsmom_backtester.py)
loads a Globex continuous OHLCV frame and currently lets the same `close` data
serve three jobs: feature generation, daily marking, and contract/accounting
logic. The Carver data deliberately separates those jobs.

Passing its reconstructed signal index as a fake close would make the ledger
mark positions in index points, lose the actual contract level, and blur rolls.
Passing the backward-adjusted level as a close would make return calculations
invalid for the 40 adjusted series that reach nonpositive values. Neither is an
acceptable shortcut.

The first expansion should therefore be a small research engine beside the
contract backtester. Once it produces trustworthy forecasts and normalized
returns, selected forecasts can be adapted into the existing ledger for the
approved mapped subset.

## Proposed research architecture

```text
PysystemtradeHistoryProvider
  ├─ signal.normalized_return ──> trend and Goulding features
  ├─ same-row carry legs ───────> carry features
  └─ marks + contract IDs ──────> roll/audit flags
                                  │
                                  v
                         causal ForecastFrame
                                  │ lag 1 day
                                  v
                          pure-rule returns
                                  │
                 ┌────────────────┼────────────────┐
                 v                v                v
          correlations       weight tests     regime/coverage tests
                 └────────────────┼────────────────┘
                                  v
                       combined research return
```

### 1. `ResearchHistoryPanel`

Build a long-form Polars frame with one canonical row per instrument and trade
date:

```text
trade_date, instrument, asset_class,
normalized_return, signal_index,
mark, price_contract, is_roll,
carry_price, carry_contract,
signal_source_timestamp, mark_source_timestamp, carry_source_timestamp,
source_commit, quality_flags
```

This can be a method or adapter around `FuturesHistory`; it does not require a
new database. Cache only derived research artifacts, version their schema, and
retain the sidecar import identity in every manifest.

### 2. `ForecastFrame`

Every rule should emit one common schema:

```text
trade_date, instrument, family, variant,
raw_forecast, scaled_forecast,
available_at, quality_flags
```

`available_at` makes the causality contract explicit. A forecast calculated
with observations through date `t` may first earn return on `t+1`; the exact
execution lag should be configurable but never zero by accident.

### 3. Carry calculation module

Add a focused module such as `domain/carry.py` with pure Polars functions for:

- parsing `YYYYMM` contract IDs into year fractions;
- validating that price and carry contracts are distinct and recording their
  chronological order explicitly;
- annualizing the same-row price differential;
- scaling by causal daily volatility;
- EWMA smoothing;
- optional asset-class-relative carry;
- causal forecast scaling and capping.

For exact `pysystemtrade` parity, reproduce its minimum absolute contract gap of
`1 / 365.25` years. Keep missing or invalid legs null. Do not substitute zero,
and do not carry stale legs through a roll.

### 4. Pure-rule return engine

For each instrument/rule/day, calculate approximately:

```text
rule_return[t+1] = scaled_forecast[t] / forecast_target
                 * normalized_return[t+1]
```

Then normalize rule returns to a common risk convention before comparing or
combining them. This is a dimensionless research return, not cash P&L and not an
integer futures position. Its purpose is to isolate information in the signal
without letting contract multipliers, account size, rounding, or allocation
logic confound the result.

### 5. Separate command and outputs

A command such as `pysystemtrade-rule-study` should read the sidecar and write a
new results directory containing:

- run manifest and source/import identity;
- coverage and exclusion report;
- daily forecasts;
- pure-rule returns;
- forecast- and return-correlation matrices;
- weight histories;
- summary metrics by era and asset class.

Do not add an unqualified `--data-source pysystemtrade` switch to the current
integer-contract CLI until its mark, roll, multiplier, FX, and cost semantics
are defined.

## Finite experiment set

Pre-register a compact family set. The goal is to learn about structurally
different horizons, not to search hundreds of nearly identical parameters.

### Baseline trend

1. **Repository TSMOM:** current continuous 63/252 trading-day horizons, with
   the existing 0.4/0.6 blend and causal volatility normalization from
   [signal.py](../src/derivatives_bt_engine/domain/signal.py).
2. **Goulding:** existing genuine-calendar 2/12-month model, tested in both the
   binary and continuous forms already implemented. Preserve the existing
   expanding pooled `a_Co`/`a_Re` estimation and compare cluster versus global
   pooling.
3. **Carver EWMAC family:** 2/8, 4/16, 8/32, 16/64, 32/128, and 64/256 spans.
   The primary cost-aware analysis can emphasize the slower three while keeping
   the faster rules as a correlation and turnover diagnostic.

### Carry

1. Absolute carry with EWMA smooths of 10, 30, 60, and 125 days, matching the
   current robust example configuration.
2. A 90-day legacy/book variant as a named sensitivity, not another freely
   tuned grid dimension.
3. Relative carry only after absolute carry passes parity tests. Define it as
   an instrument's smoothed carry less the contemporaneous median for eligible
   peers in its asset class, with a minimum peer count and no future membership.

### Combinations

Report at least:

- each pure rule alone;
- repository TSMOM only;
- Goulding only;
- Carver EWMAC family only;
- carry family only;
- equal trend-family/carry-family blend;
- trend/Goulding/carry hierarchical blend;
- a “Carver-light” combination containing only the trend and carry rules in
  scope here.

Call the final case “Carver-light,” not a replication of the current robust
system. The upstream system also contains breakout, skew, relative rules, and
large fixed per-market configurations that this experiment will not reproduce.

## Forecast scaling

Raw rules cannot be fairly equal-weighted until their magnitudes have the same
meaning. For each variant:

1. estimate its scale from observations strictly before the forecast date;
2. pool instruments where appropriate to reduce estimation noise;
3. target an average absolute forecast of 10;
4. cap the scaled forecast at ±20;
5. record the scale estimate and its effective date.

The repository's continuous Goulding forecast currently targets average
absolute 0.5 and caps at ±1. Either map it into the common 10/20 convention in
the research adapter or convert every family into a neutral `[-1, 1]` exposure
unit after scaling. Do not combine the existing raw numbers directly; doing so
would give forecast units, rather than evidence, control of the weights.

Use a warm-up rule and an uninformed fallback specified before scoring. A fixed
scalar copied from Carver may be useful for a parity test, but causal pooled
scaling should be the main long-history experiment.

## Correlation and hyperparameter analysis

The longer panel is most valuable for asking whether parameter variants are
independent and stable, not for choosing the single best in-sample Sharpe.

For every pair of variants, calculate both:

- **forecast correlation**, which measures similarity of position intent; and
- **pure-rule return correlation**, which measures diversification in realized
  outcomes.

Report them:

- over the full causal history;
- by decade;
- by asset class;
- on a common balanced sample;
- on each rule's maximal available sample;
- separately on roll and non-roll dates;
- in the Globex overlap period as a source-robustness check.

Cluster rules by pure-rule return correlation and report an effective number of
signals, for example from the correlation eigenvalues. If six nearby trend
speeds form one tight cluster, they should not automatically receive six times
the family weight of one carry rule.

The existing
[window reporting](../src/derivatives_bt_engine/domain/tsmom_window_reporting.py)
and [grid search](../src/derivatives_bt_engine/strats/tsmom_grid_search.py) should
provide the causal scoring and report conventions. Extend their outputs rather
than inventing a second definition of expanding and rolling windows. The long
sample increases regime breadth; overlapping windows still do not create
independent observations.

## Weighting experiment

There are three different weights in this system and they must not be conflated:

1. **forecast weights** combine rule variants for one instrument;
2. **instrument weights** allocate risk across futures markets;
3. **portfolio scaling/IDM** maps the combined portfolio to a risk target.

The current backtester's `allocation_mode='ew'` is a directional futures
**instrument** benchmark: each configured symbol receives `1/N` of gross
notional. It is not equal forecast weighting. For this study, hold instrument
allocation and portfolio risk treatment fixed while varying only forecast
weights. Test instrument allocation separately afterward.

### Weight candidates

1. **Raw variant `1/N`:** every scaled rule receives the same weight. Keep it as
   a diagnostic, because adding redundant variants changes family weight.
2. **Hierarchical `1/N` — recommended baseline:** equal weight the named
   families first, then equal weight variants within each family. This prevents
   six trend speeds from defeating one carry family by simple rule count.
3. **Correlation-only handcrafting:** group highly correlated rules and divide
   weight across groups using Carver's qualitative/correlation-driven method,
   without fitting expected returns.
4. **Estimated challenger:** a shrinkage or bootstrap estimator using only
   prior data, updated slowly (for example annually), with turnover and weight
   instability reported.

Do not choose weights from full-sample standalone Sharpe ratios. In particular,
do not remove a market or rule because its isolated historical Sharpe happened
to be negative if it improves the combined portfolio; Carver makes this point
with the temptation to exclude corn from a diversified system
([discussion](https://qoppac.blogspot.com/p/pysystemtrade.html)).

### Why simple weights deserve the baseline slot

Carver's empirical comparison reported Sharpe ratios of 0.82 for naive
Markowitz, 0.96 for shrinkage, 0.97 for bootstrap, 1.01 for handcrafted weights,
and 1.02 for equal weights. The equal-weight versus handcrafted difference had
a pairwise t-statistic of only 0.19; most differences among the non-naive
methods were not statistically decisive. He also showed the rule-count trap:
on one S&P forecast example, handcrafted grouping allocated about 35% to carry,
while raw equal rule weights allocated only about 14% because there was one
carry rule but six EWMAC variants
([empirical tests](https://qoppac.blogspot.com/2019/02/portfolio-construction-through_9.html),
[method](https://qoppac.blogspot.com/2018/12/portfolio-construction-through_7.html),
[implementation](https://qoppac.blogspot.com/2018/12/portfolio-construction-through_14.html)).

DeMiguel, Garlappi, and Uppal similarly found that 14 optimized allocation
models did not consistently beat the `1/N` benchmark across seven empirical
datasets once estimation error and turnover were confronted
([*Review of Financial Studies* abstract](https://academic.oup.com/rfs/article-lookup/doi/10.1093/rfs/hhm075)).
That is evidence from asset allocation, not proof that equal forecast weights
must win in a CTA. It establishes the correct burden of proof: a more elaborate
estimator should beat a simple causal baseline out of sample after costs and
with tolerable weight instability.

### Evaluation

Update estimated weights only from prior observations, save every dated weight,
and apply a one-period lag. Compare:

- out-of-sample Sharpe and return volatility;
- drawdown, downside skew, and worst rolling period;
- turnover and a transparent cost sensitivity;
- weight concentration and year-to-year instability;
- breadth across decades and asset classes;
- incremental performance when carry is added to each trend baseline;
- results on both a fixed balanced universe and a causal expanding universe.

The preferred rule is the simplest one whose improvement is broad, stable, and
economically material. A small full-sample Sharpe edge that comes from one era,
asset class, or changing universe should not justify extra estimation.

## Backtest tiers

### Tier A: normalized research returns

Run across all 252 instruments subject to per-rule quality masks. This tier can
answer forecast, diversification, weighting, and regime questions now. It uses:

- the reconstructed positive signal index for trend/Goulding features;
- same-row contract legs for carry;
- lagged forecasts and normalized price changes for returns;
- no dollar multipliers, integer lots, synthetic fills, or financing claim.

Tier A is the primary deliverable.

### Tier B: contract-accounting validation

Run only for mappings that have been manually approved and for dates where
specifications, FX, marks, and costs are reliable. Feed the research forecast
into the existing ledger while retaining the actual mark and contract ID.

Tier B answers a narrower question: does a promising rule remain useful after
monthly sizing, integer rounding, realistic turnover, risk caps, and the
repository's portfolio machinery? Pre-Globex results should remain explicitly
labeled approximate; they must not be mixed into a “realistic execution” claim.

## Sample design

Use two complementary panels:

- **causal expanding universe:** an instrument becomes eligible only after its
  required warm-up and data-quality conditions are met;
- **balanced-universe control:** a fixed set with complete coverage over a
  declared interval, preventing changing composition from driving comparisons.

Publish decade and asset-class slices and reserve the Globex overlap period for
cross-source robustness. A possible chronological discipline is pre-2000 for
initial method development, 2000–2009 for validation, and 2010 onward as a
holdout, but the exact dates should be frozen only after inspecting availability
rather than performance. All hyperparameter comparisons must use the same
declared out-of-sample boundaries.

Run both common-sample and maximal-sample comparisons. The former answers which
rule is better on like-for-like observations; the latter shows how each rule
uses the full historical scope. Neither should silently replace the other.

## Implementation phases

### Phase A — carry parity and quality controls

- implement pure Polars contract-date and carry calculations;
- reproduce selected upstream `pysystemtrade` rows exactly;
- test sign, annualization, smoothing, scaling, capping, nulls, and rolls;
- emit coverage and rejection-reason counts by instrument;
- make no changes to the existing Globex execution path.

### Phase B — rule laboratory

- build `ResearchHistoryPanel` and `ForecastFrame`;
- adapt repository TSMOM and Goulding rules to the signal index;
- add the finite EWMAC and carry families;
- generate one-day-lagged pure-rule returns and immutable manifests.

### Phase C — correlation study

- produce forecast and return correlations by full sample, decade, and asset
  class;
- measure roll-date sensitivity and common-versus-maximal sample differences;
- cluster hyperparameters and quantify the effective number of rules.

### Phase D — weight study

- compare raw `1/N`, hierarchical `1/N`, correlation-only handcrafting, and one
  regularized estimated challenger;
- hold market allocation and portfolio risk handling fixed;
- update weights causally and report stability and turnover.

### Phase E — long-history causal comparison

- score repository TSMOM, Goulding, Carver EWMAC, carry, and their declared
  combinations through the existing window-report framework;
- run balanced and expanding universes;
- verify that conclusions are not isolated to one decade or asset class.

### Phase F — optional ledger integration

- approve mappings and contract economics market by market;
- expose a forecast-provider boundary to the current TSMOM backtester;
- keep signal, mark, and carry data sources explicit;
- compare normalized Tier A behavior with costed integer-contract Tier B
  behavior over the reliable overlap period.

## Acceptance criteria

Before any result is used to change production backtests:

- forecasts at `t` may earn returns only after `t`;
- scale and weight estimates must use strictly prior information;
- adjusted price levels must never be percentage-changed directly;
- carry legs must come from one complete source row;
- missing carry must remain missing, not become neutral zero;
- no carry value may be filled across a contract change;
- rule units must be normalized before weights are compared;
- raw and hierarchical `1/N` baselines must be reported beside estimators;
- common-sample and maximal-sample results must both be visible;
- source commit, sidecar schema, universe, exclusions, rules, lags, and cost
  assumptions must be recorded in the run manifest;
- existing Globex tests and saved backtest results must remain unchanged;
- no live trading path may consume the new forecasts during the research
  phases.

## Proposed first deliverable

The smallest useful implementation is a carry parity report, not a full
portfolio backtest. For 10 representative instruments—including corn, crude,
bonds, FX, metals, and one poor-coverage market—it should show:

1. source price/carry legs and contract IDs;
2. contract-year fraction and annualized roll;
3. causal volatility, raw carry, each smoothed forecast, scale, and cap;
4. null and rejection reasons;
5. roll-day versus non-roll-day behavior;
6. exact comparison with upstream calculations on selected dates.

Once that report passes, the same functions can be expanded across the 252
instruments and joined to the existing TSMOM/Goulding rules. This sequence uses
the long history immediately while keeping the execution-grade Globex engine
and its database isolated.

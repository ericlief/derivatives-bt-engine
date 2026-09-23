# Circularity in sizing a small futures account

## Decision summary

A large futures data universe is not the same thing as a large tradable
portfolio. This repository can now use the complete imported pysystemtrade
price-history panel to normalize Carver EWMAC forecasts, but a small account
cannot hold equal integer positions across that entire panel.

The recommended research implementation is therefore:

1. retain the complete history panel as the **source and normalization
   universe**;
2. on every scheduled historical review, reconstruct the **point-in-time
   possible universe** from every history then available;
3. apply only lagged data, contract-family, cost, liquidity, permission,
   margin, and absolute one-lot-risk constraints to form the dynamic eligible
   universe—never strategy returns, current forecast strength, or whether a
   target happens to round to a contract;
4. assign equal ex-ante risk weights within that eligible universe as the
   simple EWMAC shadow baseline;
5. multiply those weights by normalized continuous forecasts and inverse
   instrument volatility;
6. treat the continuous result as a shadow target and round it with explicit
   integer, margin, concentration, and risk constraints; and
7. leave unrepresentable or weak-forecast risk in cash instead of repeatedly
   redefining the active set and filling the released capacity.

This is a dynamic-universe TSMOM test, not a backtest of a fixed present-day
instrument list. The eligible set can expand as histories warm up and contract
when a market causally fails a structural constraint. The implemented integer
book may be much smaller than that set, but a zero holding must not retroactively
make an instrument ineligible or change `N`.

This is not assumption-free. No small-account futures construction can be:
contract indivisibility forces a choice between fewer markets, unused risk,
unequal positions, fractional substitutes, or more leverage. The objective is
to make that choice causal, stable, auditable, and independent of backtest
performance.

## Fact check: what the latest branch actually contains

The repository was refreshed against `origin` on 2026-09-23. At the start of
this note, `feat/pysystemtrade-phase4-hybrid` and its upstream both pointed to
`405d9a4`; there was no newer remote branch tip.

The phrase “all 256 Carver futures” is not the verified count for the imported
snapshot. The current facts are:

| Item | Verified count or value |
|---|---:|
| Imported multiple-price histories | 252 instruments |
| Imported adjusted-price histories | 252 instruments |
| Rows in Carver's instrument configuration catalog | 586 instruments |
| EWMAC global normalization universe | 252 instruments |
| Packaged Carver/Globex crosswalk rows | 14, all `candidate` / `signal_research` |
| Locally configured futures symbols in `instruments.py` | 35 symbols, including related full/mini/micro contracts |
| Imported history range | 1969-12-02 through 2024-03-29 |
| pysystemtrade source commit | `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8` |
| Sidecar schema | version 4, built 2026-09-18 |

The 252 histories are the rows with both multiple-price and adjusted-price
data and the required instrument and roll configuration. That is the set
loaded by `_pysystemtrade_source_coverage` and pooled by the full-universe
EWMAC scaler. The 586-row configuration table includes instruments without
the complete histories required by this path and must not be described as a
586-market backtest universe.

Likewise, 252 histories do not mean 252 instruments are ready for live
execution. The present integration deliberately separates these uses:

- all 252 eligible histories can contribute to a pooled EWMAC forecast scalar;
- the backtest still trades only its requested symbols;
- the source-neutral trading path currently requires a Carver/Globex mapping;
- the 14 packaged mappings remain research candidates, not approved live
  mappings; and
- the imported sample ends in March 2024, so it cannot establish current
  liquidity, margin, broker permission, or contract availability in September
  2026.

The full source audit and EWMAC cache details remain in
[`pysystemtrade-data.md`](pysystemtrade-data.md). The relevant implementation
is in
[`tsmom_backtester.py`](../src/derivatives_bt_engine/domain/tsmom_backtester.py)
and
[`tsmom_history.py`](../src/derivatives_bt_engine/domain/tsmom_history.py).

## Why small-account sizing becomes circular

### 1. Signal-active renormalization

Consider an allocation universe of 20 instruments. Ten forecasts become weak or
zero. If the ten weak instruments are removed and the remaining ten are
renormalized to sum to one, the surviving positions double before their own
forecast magnitudes are applied.

The allocation layer has partially reversed the information from the signal
layer:

```text
weaker aggregate forecasts
    -> fewer “active” instruments
    -> larger weights for survivors
    -> risk is redeployed instead of left unused
```

This is a slower version of the problem in Carver's fixed portfolio-risk post.
Forcing the completed book back toward its target can cancel the information
contained in aggregate continuous forecast strength. Monthly rather than daily
re-estimation reduces the frequency; it does not change the direction of the
effect.

### 2. Integer-active renormalization

Suppose an equal-risk target produces 0.20 contracts in most of the 20
instruments. Ordinary rounding produces a mostly flat book. If the zeroed
markets are then removed, `N` is recomputed from the handful of survivors, and
their allocations are increased until contracts fit, contract granularity has
silently become a position-selection signal.

The loop is:

```text
large N
    -> small target per instrument
    -> many targets below one contract
    -> remove zero positions
    -> smaller N
    -> larger target per survivor
    -> spend the released risk
```

The implemented book is no longer a 1/N portfolio. It is a concentrated
portfolio selected by the interaction of contract sizes, forecast magnitudes,
and the rounding rule.

### 3. The capital-feasibility fixed point

It is tempting to call an instrument feasible when its one-contract risk is
below its 1/N allocation. But its 1/N allocation depends on N, and N depends on
how many instruments pass that feasibility test. This definition has no unique
answer without an additional policy.

Use a two-stage rule instead:

1. apply absolute, account-level feasibility constraints that do not depend on
   N; then
2. select and freeze a portfolio from the survivors using a pre-registered
   static objective.

Portfolio representability can then be audited for the selected set without
feeding daily rounding results back into membership.

### 4. Full-universe IDM versus the implemented subset

An IDM estimated for a diversified 20-market shadow portfolio is not necessarily
appropriate when integer constraints leave only five positions. Recalculating
IDM on those five and scaling them up to spend all risk creates another feedback
loop. Retaining the 20-market IDM without measuring the integer book can instead
grant diversification leverage that the account does not possess.

The safe separation is:

- estimate base weights and any slow IDM for the structurally eligible
  allocation universe;
- calculate the risk of the actual signed integer book separately;
- use actual-book risk as a one-way maximum constraint; and
- do not use spare capacity as an instruction to manufacture more contracts.

### 5. ERC does not remove the circularity

ERC decides how to divide risk among the instruments supplied to it. It does
not decide which instruments should exist in that input set. Active-set ERC
therefore embeds the universe policy inside the risk estimator:

```text
forecast/activity gate -> ERC membership -> ERC weights -> IDM -> positions
```

Changing a forecast can change both the forecast multiplier and the portfolio
against which its weight was calculated. For a simple continuous EWMAC
baseline, forecast values should change positions but not allocation-universe
membership.
Active-set ERC should remain a separately labelled empirical challenger.

## Names for the sets

Avoid using `active` for every stage. The research and implementation should
report these distinct sets:

1. **Source universe** — every history available to the research database: 252
   in the current snapshot.
2. **Normalization universe** — histories allowed to estimate forecast scale:
   currently the same 252 for global EWMAC normalization.
3. **Quality universe** — histories passing rule-specific causal data checks on
   a given date.
4. **Operationally eligible universe** — contracts that can legally and
   operationally be traded, with current specifications and data.
5. **Capital-feasible universe** — operationally eligible contracts whose one-
   lot risk and margin fit absolute account limits.
6. **Allocation universe** — the point-in-time eligible instruments that
   receive base shadow-portfolio weights at the scheduled review. For the
   primary experiment this is dynamic, not a fixed present-day list.
7. **Forecast-bearing universe** — allocation-universe instruments with a
   valid forecast; a valid zero remains valid and does not alter base weights.
8. **Shadow portfolio** — continuous positions before integer conversion.
9. **Implemented book** — actual integer targets after constraints.
10. **Held book** — broker positions, including temporarily frozen or
    reduce-only contracts.

Only causal structural changes should normally alter the allocation universe.
Forecast changes, a sub-lot target, or the fact that the integer optimizer did
not select a contract must not. A separately tested static strategic subset may
still be useful for operational simplicity, but it is not the primary dynamic-
universe experiment.

## Recommended filters for the 252-history source universe

All historical filters must use information available at the review date. A
2026 list of liquid markets cannot be applied retrospectively to 1970 without
creating survivorship and look-ahead bias.

### A. History and data quality

Require, per instrument and per review date:

- enough prior observations to warm every chosen EWMAC rule and the volatility
  estimator;
- a valid current, forward, and carry contract where the rule needs them;
- a monotonic and usable roll calendar;
- a valid multiplier, currency, tick size, and contract identifier;
- no stale terminal price beyond a declared session tolerance;
- no unresolved nonpositive or discontinuous observation in the rule's active
  lookback; and
- an auditable return stream that excludes roll-level price gaps from economic
  P&L.

The imported sidecar has 40 instruments with at least one nonpositive adjusted
observation and 26 instruments with less than 50% carry-day coverage. Those are
not automatic universal exclusions: point-price EWMAC can remain meaningful in
some cases where return or carry rules cannot. Eligibility must be recorded per
rule rather than hiding every data issue behind a single symbol flag.

For the first simple EWMAC system, require the complete chosen rule family to be
warmed up before the instrument enters. Averaging whichever rules happen to be
available would make the rule definition change with instrument age.

### B. Contract-family deduplication

Treat full-size, mini, and micro contracts on the same underlying as one
economic family. Select one execution contract per family using this order:

1. passes liquidity and cost gates;
2. smallest usable one-contract dollar volatility;
3. lowest total expected trading cost in common risk units;
4. best roll and live-data reliability; and
5. deterministic symbol tie-break.

Do not count ES, MES, and another economically equivalent S&P contract as three
independent diversification opportunities. A larger sibling may remain in the
signal or normalization universe while a liquid micro is the execution vehicle,
but that mapping must be explicit.

### C. Liquidity

Use lagged, robust measures over a declared window rather than today's single
observation. At minimum record:

- median and lower-tail daily contracts traded;
- open interest where available;
- daily risk units traded: volume multiplied by one-contract dollar volatility;
- bid/ask spread and observed slippage; and
- the account's proposed order size as a fraction of normal volume.

Carver's current pysystemtrade documentation describes a bad market as one with
fewer than 100 contracts per day or less than about USD 1.5 million of risk units
traded per day. These are useful starting references, not values to copy before
matching definitions and data frequency. Use separate entry and exit thresholds
or a persistence rule so an instrument does not churn around a cutoff.

The imported price sidecar does not contain sufficient current volume, open
interest, margin, or live-spread evidence to apply this filter for 2026. That
requires current broker/exchange data.

### D. Costs

Measure costs in risk-adjusted units so contracts with different multipliers
and volatilities are comparable. Include:

- commission and exchange fees;
- half spread;
- expected slippage;
- roll turnover and roll cost;
- currency conversion where material; and
- expected rule turnover for each EWMAC speed.

Pysystemtrade's current instrument documentation uses a threshold of roughly
0.01 Sharpe-ratio units per trade when suggesting bad markets. Carver's older
posts sometimes express cost cutoffs using differently scaled language. The
repository must reproduce the exact cost-unit definition before adopting a
number. Fees belong in eligibility and net P&L, not in the correlation matrix
used to measure diversification.

#### Fee approximation available to this backtest

The public data does not provide a reliable daily history of realized spread,
broker commission, exchange fee, or market impact for every instrument. It
does provide a reproducible configuration snapshot. That supports a useful
historical approximation, but it must be labelled **static cost coefficients
applied to point-in-time market risk**, not historical observed transaction
costs.

The provenance is concrete:

- in the June 2021 pysystemtrade snapshot associated with Carver's small-fund
  article, `CORN` had a point value of USD 50, configured slippage of 0.125
  price points, and per-block commission of USD 2.90;
- its configured one-way cost was therefore `0.125 x 50 + 2.90 = USD 9.15`,
  which reproduces the article's example;
- in the local current pysystemtrade snapshot, `spreadcosts.csv` gives `CORN`
  a `SpreadCost` of 0.16 price points, while `instrumentconfig.csv` gives a
  point value of USD 50 and per-block commission of USD 2.97; and
- the analogous current configured one-way estimate is
  `0.16 x 50 + 2.97 = USD 10.97`.

The local spread-cost file was last changed by pysystemtrade commit `722fd7e`
on 2026-03-20 and the instrument configuration file by `073422a` on
2026-04-26. Those dates describe configuration provenance, not the dates on
which each individual value was economically observed. The imported sidecar's
source commit is `b4a25e6`; broad prices stop in March 2024. Consequently, the
current cost snapshot must not be presented as a measured 1970-2024 cost
history.

For each instrument and historical date, construct the proxy with lagged data
as follows:

```text
spread/slippage cash per contract
    = configured spread-cost points
    x contract point value
    x historical FX to account currency

commission cash per contract
    = configured commission
    x historical FX to account currency

configured one-way cash cost
    = spread/slippage cash + commission cash + other configured fees

one-contract annual dollar volatility
    = lagged annualized price-point volatility
    x contract point value
    x historical FX to account currency

SR cost per one-way trade
    = configured one-way cash cost
    / one-contract annual dollar volatility

expected annual SR cost
    = SR cost per one-way trade
    x pre-registered expected annual one-way turnover
```

When positive raw prices make a percentage diagnostic useful, also report:

```text
configured spread percentage
    = configured spread-cost points / raw tradable-contract price
```

This percentage is a diagnostic, not the preferred cross-market constraint.
The SR-cost series is more useful because its denominator places the contract's
cash cost in the same risk units used for sizing. Use the raw tradable-contract
mark for price and notional. Use roll-neutral, lagged returns or price-point
changes to estimate volatility; do not use the level of a back-adjusted/Panama
series as if it were a tradable contract price.

A fixed configured spread can still produce a time-varying historical cost
ratio because FX and estimated dollar volatility change through time. This is
the intended approximation. It does not reconstruct historically narrower or
wider order books. Run at least `0.5x`, `1.0x`, `1.5x`, and `2.0x` cost
sensitivities, and do not tune the multiplier on final-period performance.

Turnover assumptions must be rule-specific: a fast EWMAC rule can fail the
cost gate while a slow rule on the same instrument passes. Either pre-register
turnover from prior research or estimate it only from information available
before the review date. Do not use the realized future turnover of the test
period to decide whether the instrument was eligible in that period.

The cash P&L charge is separate from the eligibility ratio:

```text
cash cost charged on a rebalance
    = absolute change in contracts
    x configured one-way cash cost at that date
```

A reversal therefore pays for the full contract change. A physical roll
normally has two traded legs and must be charged accordingly. Do not subtract
the SR-cost ratio directly from returns, and do not insert commissions or
slippage into H or IDM. H/IDM measure diversification; costs determine
eligibility and net performance.

### E. Absolute one-lot risk

For each execution contract calculate:

```text
one-contract annual dollar volatility
    = contract price or point scale
    x contract multiplier
    x annualized return volatility
```

For point-priced futures, use the equivalent point-volatility calculation and
perform any currency conversion to the account base currency.

Then impose an absolute maximum independent of N:

```text
one-contract dollar volatility
    <= maximum instrument share
       x account portfolio-risk limit
```

Carver used a 10% maximum risk-capital share as an example in his small-fund
work. Treat that as a candidate concentration limit, not an empirical constant.
Pre-register the project value and test sensible neighbouring values without
selecting one from final-period performance.

Also report representability bands rather than a single pass/fail label:

- at least three contracts at a maximum normalized forecast;
- two contracts;
- exactly one contract; and
- one contract already exceeds the permitted instrument risk.

Carver's static-selection work preferred markets where a maximum forecast of 20
could hold at least three contracts because one-lot books suffer substantial
risk-targeting error. A very small account may need to accept the one- or two-
contract bands, but the resulting concentration and forecast discretization
must be visible.

### F. Margin and cash

One-lot risk feasibility does not guarantee funding feasibility. Require:

- one-contract initial and maintenance margin below declared account shares;
- stressed total margin below an account-wide ceiling;
- a cash buffer for variation margin, commissions, and adverse gaps;
- base-currency conversion using information available on the decision date;
  and
- explicit treatment of offsets without assuming the broker will grant them.

Margin should be a constraint, not a proxy for economic risk.

### G. Permissions and operational state

Record separately:

- exchange and product permission;
- market-data subscription and freshness;
- broker support and order-type support;
- trading hours and operational coverage;
- price-limit or exchange-halt state;
- contract roll state; and
- `normal`, `reduce_only`, `dont_trade`, and `frozen` overrides.

A persistent restriction can remove an instrument at the next scheduled
universe review. A temporary restriction should normally leave its allocation
unspent or permit only risk reduction; it should not immediately increase every
other position.

### H. Diversification after the absolute filters

Do not select the remaining markets by their own historical EWMAC Sharpe or by
their current forecasts. Prefer broad structural coverage:

- equities;
- government rates across regions and a limited number of maturities;
- currencies;
- energies;
- industrial and precious metals;
- grains and soft commodities;
- livestock where operationally viable; and
- other genuinely distinct groups supported by the data.

Within a highly redundant group, prefer the more granular, cheaper, and more
liquid contract. Correlation can help identify redundancy, but use a slow,
shrunk estimate and do not let small correlation differences create frequent
membership changes.

## How the point-in-time universe constraints should work

The primary backtest should not begin with a hand-picked 15- or 20-market list.
On every scheduled review date, it should begin with all imported histories
that existed by then. The 252 count is the union over the complete database,
not an assumption that all 252 markets traded simultaneously.

Use a monthly universe review, with every input lagged at least one trading day,
and apply this ordered pipeline:

1. **History existed:** include only instruments with observations dated on or
   before the review and a non-stale current tradable-contract mark.
2. **Rule was warmed:** require enough prior history for the complete selected
   EWMAC rule set and its volatility estimator. Do not average only the rules
   that happened to be available for a young market.
3. **Contract family was resolved:** choose one execution vehicle per economic
   family using causal cost, granularity, and liquidity criteria. Related
   contracts may remain in signal research but do not each receive an
   independent 1/N diversification budget.
4. **Cost was acceptable:** calculate the lagged SR-cost proxy above and apply
   the pre-registered threshold for each rule or rule blend.
5. **One lot was safe:** require one-contract dollar volatility to fit an
   absolute share of the account risk limit. This test must not contain `N`.
6. **Funding and operations were acceptable:** apply point-in-time margin,
   liquidity, permission, and restriction data where actually available.
7. **Membership was persistent:** require, for example, three consecutive
   monthly passes to enter and three failures to leave. Serious stale-price,
   permission, or risk failures can force immediate risk reduction.

The historical sidecar does not contain causal volume, margin, permissions, or
realized spread histories for the entire panel. The broad historical experiment
must therefore identify its primary set honestly as **data-, cost-, and
one-lot-risk eligible**, then run sensitivity scenarios for unavailable
operational gates. Do not fill old dates with 2026 volume or margin and call
the result point-in-time. The mapped recent hybrid period can separately test
the fuller operational policy with broker/exchange data.

At a review date, let `N` be the number of instruments that pass these
structural gates. A valid forecast of zero remains in `N`. A target below half
a contract remains in `N`. An instrument that receives zero contracts from the
integer allocator remains in `N`. Only a subsequent structural eligibility
decision changes membership.

Passing the filters may still leave more instruments than the small account
can physically hold. That is evidence about implementation, not a reason to
invent an arbitrary maximum count after seeing returns. Pre-register these
separate comparisons:

- **broad 1/N shadow plus independent rounding:** the clean diagnostic baseline;
  unrepresentable risk stays in cash;
- **broad 1/N shadow plus dynamic integer optimization:** select the contract
  book that best approximates the broad shadow exposure subject to cost,
  margin, concentration, and risk limits; and
- **structural subset control:** if operational simplicity requires a maximum
  count, choose it by a pre-registered cluster-coverage and implementation-cost
  rule, not performance, and label it as a different universe policy.

Do not include backtest return, Sharpe ratio, drawdown, current trend strength,
forecast sign, or rounded position in any eligibility or subset-selection
objective. Run several fixed account sizes because feasibility is intrinsically
capital-dependent, but freeze the constraint values before examining final
strategy performance.

## Baseline and challengers

### Baseline: feasible-universe EWMAC 1/N risk allocation

The recommended baseline is not the repository's current `allocation_mode=ew`.
That mode is intentionally a strict DeMiguel-style gross-notional benchmark: it
uses only signal direction and bypasses forecast magnitude, inverse-volatility
sizing, IDM, and correlation-aware integer processing.

The desired CTA baseline should instead use:

```text
equal eligible-universe risk weight (1 / N at the review date)
    x normalized continuous EWMAC forecast
    x inverse instrument volatility
    x common account risk scale
```

Use one of two explicitly labelled leverage policies:

- **no-IDM control:** no correlation-derived leverage; accept lower realized
  risk from diversification; or
- **slow/capped IDM control:** estimate IDM on the structurally eligible
  universe at scheduled reviews, update slowly, cap it, and never recompute it
  from forecast-active or nonzero-integer survivors to fill released risk.

These isolate the effect of optimized relative weights while preserving the
essential CTA mechanics of forecast magnitude and volatility normalization.

### Challenger 1: active-set ERC plus IDM

Retain the current monthly active-set ERC/IDM construction as a challenger. It
must be labelled as a different universe policy because it redistributes risk
from excluded instruments. Compare it against the baseline rather than treating
it as an implementation detail.

### Challenger 2: broad shadow portfolio plus dynamic integer optimization

Build continuous targets for a broad quality universe and minimize tracking
error plus costs subject to integer contracts and hard risk constraints. This
is the direction of Carver's later small-fund work. It may exploit signals from
instruments too large to trade by using correlated, more granular substitutes.

It is not 1/N after implementation, and it requires its own causal backtest.
Report the shadow and integer books separately. Do not compare only their final
returns.

### Approaches to avoid

- Recomputing N from nonzero rounded positions.
- Defining allocation-universe membership from `abs(forecast) > threshold`.
- Recalculating ERC/IDM on the integer survivors and filling all spare risk.
- Increasing the risk target merely to make contracts fit.
- Imposing a 15- or 20-market cap merely because independent rounding failed.
- Selecting markets by full-sample strategy Sharpe.
- Applying today's liquid-market list to the entire historical sample.
- Counting full, mini, and micro siblings as independent diversification.
- Treating missing/untradable as equivalent to a valid zero forecast.
- Calling the 252-history normalization panel a 252-market live universe.

## Current code implications

The latest branch already exposes the relevant fault lines:

- `_pysystemtrade_source_coverage` and `_load_pysystemtrade_ewmac_universe`
  correctly separate the 252-history normalization pool from requested traded
  symbols.
- Risk-targeted live allocation currently defines `active_symbols` from
  forecast validity and `min_conviction`, then estimates H, ERC/HRP, and IDM on
  that set. That is the active-set policy discussed above.
- `compute_symbol_notional_budget` splits the total active-set dollar-vol
  budget and optionally applies IDM. It is coherent conditional on that set,
  but it does not solve how membership should be defined.
- `allocate_lot_aware_targets` uses nearest-lot rounding above a fractional
  threshold and removes lots only when the book breaches its risk limit. It
  deliberately never spends spare risk. This is conservative and avoids one
  source of circularity, but a very large equal-weight universe will still
  produce many sub-0.5 targets and therefore a sparse or flat book.
- The current `ew` mode retains independent rounding only and is not the
  proposed equal-risk EWMAC baseline.

No production code should be changed until the eligibility schema and the
point-in-time data available for each criterion are specified. A minimal future
implementation sequence is:

1. add a dated universe-audit table with one row per source instrument and one
   column per eligibility reason;
2. add execution-family mappings for full/mini/micro substitutes;
3. calculate lagged one-lot dollar volatility, the configured-cost time series,
   margin, liquidity, and data-quality diagnostics, explicitly marking fields
   that are unavailable historically;
4. implement the monthly point-in-time eligibility pipeline over every source
   history then available, including rule warm-up, family deduplication,
   hysteresis, and reason-coded entry/exit decisions;
5. add an equal-risk EWMAC allocation mode that sets `N` from that eligible set
   and preserves continuous forecast
   magnitude and inverse-volatility sizing;
6. retain the continuous shadow target through integer allocation and report
   target-versus-implemented error; and
7. run active-set ERC and dynamic integer optimization only as separately
   named challengers.

## Required reports for the full-history experiment

The broad Carver panel has staggered starts from 1969 and ends in March 2024,
so this is no longer accurately described as one common 16-year backtest. Use
the maximum causal history available to each instrument, report the number of
eligible instruments through time, and separately report fixed-overlap periods
for like-for-like comparisons. Use the mapped hybrid data after March 2024 as a
shorter recent validation, not as if all 252 histories continued to the present.

For each account size, universe policy, and allocation method report:

- source-available, quality, operational, capital-feasible, allocation,
  forecast-valid,
  and nonzero-integer counts;
- entry and exit reasons for every universe change;
- shadow target contracts and final contracts;
- instruments below 0.25, 0.50, one, two, and three target contracts;
- one-contract dollar volatility and its share of the portfolio-risk limit;
- shadow and implemented portfolio volatility;
- unused risk capacity without automatically treating it as an error;
- tracking distance from the continuous target;
- instrument, cluster, and asset-class concentration;
- initial, maintenance, and stressed margin;
- turnover caused by forecasts, volatility changes, rolls, membership, and
  integer substitutions separately;
- gross and net performance; and
- performance of the same strategy at several fixed account sizes.

The primary small-account result is not merely which candidate has the highest
Sharpe. It is whether an apparently superior continuous portfolio survives
contract granularity, current tradability, turnover, and margin without
silently changing its universe and leverage rules.

## Sources

- Robert Carver, [Should I run my trading system at a fixed expected volatility target?](https://qoppac.blogspot.com/2020/10/should-i-run-my-trading-system-at-fixed.html), 2020.
- Robert Carver, [Optimising my way out of a small fund problem — part one](https://qoppac.blogspot.com/2021/06/optimising-my-way-out-of-small-fund.html), 2021.
- Robert Carver, [Optimising portfolios for small funds](https://qoppac.blogspot.com/2021/06/optimising-portfolios-for-small.html), 2021.
- Robert Carver, [Static optimisation of the best set of instruments to hold in a futures trading system](https://qoppac.blogspot.com/2021/06/static-optimisation-of-best-set-of.html), 2021.
- Robert Carver, [Talking to the dead / simple heuristic position selection](https://qoppac.blogspot.com/2021/07/talking-to-dead-simple-heuristic.html), 2021.
- Robert Carver, [Mr Greedy and the Tale of the Minimum Tracking Error Variance](https://qoppac.blogspot.com/2021/10/mr-greedy-and-tale-of-minimum-tracking.html), 2021.
- Pysystemtrade, [instrument inclusion, bad markets, restrictions, and dynamic optimization](https://github.com/pst-group/pysystemtrade/blob/develop/docs/instruments.md).
- Victor DeMiguel, Lorenzo Garlappi, and Raman Uppal, [Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?](https://doi.org/10.1093/rfs/hhm075), *Review of Financial Studies* 22(5), 2009.

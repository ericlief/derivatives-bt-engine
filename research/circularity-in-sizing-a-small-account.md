# Circularity in sizing a small futures account

## Decision summary

A large futures data universe is not the same thing as a large tradable
portfolio. This repository can now use the complete imported pysystemtrade
price-history panel to normalize Carver EWMAC forecasts, but a small account
cannot hold equal integer positions across that entire panel.

The recommended first implementation is therefore:

1. retain the complete history panel as the **source and normalization
   universe**;
2. form a much smaller **capital-feasible strategic universe** using only
   lagged operational criteria, never strategy returns or current forecast
   strength;
3. assign equal risk weights within that strategic universe as the simple
   EWMAC baseline;
4. multiply those weights by normalized continuous forecasts and inverse
   instrument volatility;
5. treat the continuous result as a shadow target and round it with explicit
   integer, margin, concentration, and risk constraints; and
6. leave unrepresentable or weak-forecast risk in cash instead of repeatedly
   redefining the active set and filling the released capacity.

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

Consider a strategic universe of 20 instruments. Ten forecasts become weak or
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

- estimate strategic weights and any slow IDM for the strategic universe;
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
baseline, forecast values should change positions but not strategic membership.
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
6. **Strategic universe** — the slowly selected instruments that receive base
   portfolio weights.
7. **Forecast-bearing universe** — strategic instruments with a valid forecast;
   a valid zero remains valid and does not alter strategic weights.
8. **Shadow portfolio** — continuous positions before integer conversion.
9. **Implemented book** — actual integer targets after constraints.
10. **Held book** — broker positions, including temporarily frozen or
    reduce-only contracts.

Only structural changes should normally alter the strategic universe. Forecast
changes, a sub-lot target, or a transient broker failure should not.

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
strategic review. A temporary restriction should normally leave its allocation
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

## How to select the strategic universe without fitting returns

After the absolute filters, select a fixed or slowly reviewed set using a
pre-registered lexicographic objective:

1. satisfy hard data, permission, liquidity, cost, margin, and one-lot-risk
   constraints;
2. maximize coverage of distinct asset-class and regional clusters;
3. minimize duplicate economic exposures;
4. minimize aggregate contract-granularity error at normalized forecast 10;
5. minimize expected implementation cost;
6. maximize liquidity as a tie-break; and
7. use a deterministic symbol tie-break.

Do not include backtest return, Sharpe ratio, drawdown, current trend strength,
or the sign of the forecast in the selection objective.

There is no universal correct number of instruments. Let capital and the above
constraints determine candidate portfolio sizes, then compare a small grid such
as 8, 12, 16, and 20 using only training-period implementation metrics. Freeze
the chosen policy before evaluating strategy performance. A capital grid is
also necessary: an implementable 20-market portfolio at USD 500,000 may be an
unimplementable four-contract portfolio at USD 100,000.

For the first baseline, review strategic membership annually. Apply immediate
risk-off overrides when required, but do not promote a replacement and rescale
the entire book in response to every temporary outage.

## Baseline and challengers

### Baseline: feasible-universe EWMAC 1/N risk allocation

The recommended baseline is not the repository's current `allocation_mode=ew`.
That mode is intentionally a strict DeMiguel-style gross-notional benchmark: it
uses only signal direction and bypasses forecast magnitude, inverse-volatility
sizing, IDM, and correlation-aware integer processing.

The desired CTA baseline should instead use:

```text
equal strategic risk weight
    x normalized continuous EWMAC forecast
    x inverse instrument volatility
    x common account risk scale
```

Use one of two explicitly labelled leverage policies:

- **no-IDM control:** no correlation-derived leverage; accept lower realized
  risk from diversification; or
- **slow/capped IDM control:** estimate IDM on the frozen strategic universe,
  update slowly, cap it, and never use it to fill risk released by weak
  forecasts or rounding.

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
- Defining strategic membership from `abs(forecast) > threshold`.
- Recalculating ERC/IDM on the integer survivors and filling all spare risk.
- Increasing the risk target merely to make contracts fit.
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
3. calculate lagged one-lot dollar volatility, margin, liquidity, cost, and
   data-quality diagnostics;
4. implement a reproducible slow strategic-universe selector that excludes
   forecast and performance inputs;
5. add an equal-risk EWMAC allocation mode that preserves continuous forecast
   magnitude and inverse-volatility sizing;
6. retain the continuous shadow target through integer allocation and report
   target-versus-implemented error; and
7. run active-set ERC and dynamic integer optimization only as separately
   named challengers.

## Required reports for the 16-year experiment

For each account size, universe policy, and allocation method report:

- source, quality, operational, capital-feasible, strategic, forecast-valid,
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
- Robert Carver, [Static optimisation of the best set of instruments to hold in a futures trading system](https://qoppac.blogspot.com/2021/06/static-optimisation-of-best-set-of.html), 2021.
- Robert Carver, [Talking to the dead / simple heuristic position selection](https://qoppac.blogspot.com/2021/07/talking-to-dead-simple-heuristic.html), 2021.
- Robert Carver, [Mr Greedy and the Tale of the Minimum Tracking Error Variance](https://qoppac.blogspot.com/2021/10/mr-greedy-and-tale-of-minimum-tracking.html), 2021.
- Pysystemtrade, [instrument inclusion, bad markets, restrictions, and dynamic optimization](https://github.com/pst-group/pysystemtrade/blob/develop/docs/instruments.md).
- Victor DeMiguel, Lorenzo Garlappi, and Raman Uppal, [Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?](https://doi.org/10.1093/rfs/hhm075), *Review of Financial Studies* 22(5), 2009.

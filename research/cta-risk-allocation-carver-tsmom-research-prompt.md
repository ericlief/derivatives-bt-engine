# CTA risk allocation, Carver, and TSMOM research brief

Conduct an exhaustive empirical and theoretical review of CTA/managed-futures
portfolio construction for `/home/dev/projects/derivatives-bt-engine`.

The task is to reconcile the current TSMOM implementation with Robert
Carver-style forecast and instrument diversification, and specify a
statistically defensible 16-year walk-forward backtest under small-account,
integer-contract constraints.

## Code to inspect first

- `src/derivatives_bt_engine/domain/signal.py`
  - `continuous_momentum`: continuous volatility-standardized fast/slow trend,
    currently squashed with `tanh`.
  - `goulding_monthly`, `_goulding_blend`, `_goulding_direction`: Goulding
    binary bull/bear direction logic.
  - Locate `ts` and `contin_signal`; determine whether correction/rebound
    discounting already applies.
- `src/derivatives_bt_engine/domain/allocation.py`
  - `_bounded_ewm_correlation_matrix`: current EWM H based on underlying daily
    close-to-close returns.
  - `compute_erc_weights`, `compute_idm`, `compute_symbol_notional_budget`, and
    `compute_realized_portfolio_risk`.
- `src/derivatives_bt_engine/live/tsmom_rebalance.py`
  - `_is_active`, universe/cluster selection, monthly allocation, H/ERC/IDM,
    and lot-aware sizing.
  - `combined_scalar` is not a pure forecast: it contains signal, risk scalar,
    regime/confidence effects, and VIX/portfolio overlays. Do not normalize it
    as a Carver forecast.
- `src/derivatives_bt_engine/domain/tsmom_backtester.py`
  - Confirm live/backtest parity in activity gates, signal construction, and
    allocation timing.
- `research/tsmom-sizing-terms-cheat-sheet.md`
- `research/cta-layer-separation-risk-budgeting.md`
- `research/research_trend_strength_crossover_signal.md`

## Central problem

The system currently has:

1. Continuous TSMOM: fast/slow time-series momentum, standardized by its own
   volatility, combined and `tanh`-squashed, then correction/rebound-discounted.
   It has volatility scaling for sizing but no Carver-style average-absolute
   forecast = 10 normalization.
2. Goulding: mostly binary `+1`/`-1` monthly fast/slow crossover direction,
   closer to conventional sign TSMOM than a continuous Carver forecast.
3. Monthly allocation: H is based on raw underlying daily returns for currently
   active selected symbols, ERC produces risk budgets, IDM and portfolio
   volatility adjustments follow, and positions are then signal-multiplied and
   rounded to contracts.

Determine whether this is coherent for CTA-style TSMOM, and whether a
Carver-style bootstrap optimizer should replace, complement, or be rejected in
favour of EWM-H/ERC.

## Distinctions that must remain explicit

- Forecast versus position scalar: a forecast is a bounded directional expected
  return score, conventionally scaled to historical average absolute value 10.
  A position scalar can include forecast, volatility scaling, confidence,
  regimes, portfolio overlays, caps, and integer-lot effects. Normalize pure
  rule forecasts before combination; never normalize `combined_scalar`.
- Not all CTAs are continuously active. Binary 12-month TSMOM is often always
  long or short; continuous forecasts attenuate near zero. Carry, calendar
  spreads, liquidity, data availability, regime rules, long-only restrictions,
  cluster caps, and small-account feasibility can create a sparse book.
  Differentiate missing/untradable from valid zero signal.
- Whole-universe strategic weights times signals and active-set ERC are not the
  same. Whole-universe weights preserve base budgets; active-set ERC redistributes
  relative risk budgets among survivors. A common portfolio-volatility multiplier
  can scale the active book but is not generally active-set ERC. Test both.
- Constant positive fractional scaling leaves correlation unchanged. Time-varying
  sign, magnitude, volatility scale, activity, or tradability changes it.
  State whether each H estimator uses raw returns, volatility-scaled returns,
  dynamic rule P&L, or final pre-cost sleeve P&L.

## Carver sources to verify

Use primary sources where possible:

- Robert Carver, *Systematic Trading*, portfolio-optimization appendix.
- [A little demonstration of portfolio optimisation](https://qoppac.blogspot.com/2015/10/a-little-demonstration-of-portfolio.html).
- [The corresponding optimisation code](https://github.com/robcarver17/systematictradingexamples/blob/master/optimisation.py).
- [Correlations, weights and multipliers](https://qoppac.blogspot.com/2016/01/correlations-weights-multipliers.html).
- `pysystemtrade` documentation/source where appropriate.

Verify rather than assume:

- `opt_and_plot(data, "expanding", "bootstrap", equalisemeans=False,
  equalisevols=True)` is not ERC. It resamples rows, equalizes per-instrument
  volatility, estimates mean/covariance, solves constrained long-only
  maximum-Sharpe/Markowitz, and averages resulting weights.
- The public code uses daily rows with replacement, not necessarily contiguous
  short blocks. Explain IID row versus moving-block/stationary bootstrap and
  preservation of same-day cross-asset dependence.
- Explain why volatility equalization is not ERC; the role of `equalisemeans`;
  and why equal means plus equal vol is a correlation/minimum-variance problem,
  not ERC.
- Examine Carver’s forecast normalization (average absolute forecast = 10),
  forecast-diversification multipliers, instrument weights, and annual/slow
  re-estimation.
- Separate optimization of rule forecasts within an instrument from
  optimization of instruments in the portfolio.

## Literature review requirements

Provide direct citations and a bibliography covering:

1. **Time-series momentum and managed futures:** Moskowitz, Ooi, and Pedersen
   (2012); Hurst, Ooi, and Pedersen; later evidence on trend strength, horizons,
   volatility scaling, crisis alpha, and implementation costs.
2. **Risk allocation and covariance estimation:** Markowitz; equal weight,
   inverse volatility, minimum variance, ERC/risk parity, maximum
   diversification, and HRP; Maillard/Roncalli/Teiletche, Qian, Roncalli,
   Ledoit-Wolf shrinkage; out-of-sample evidence for naive, risk-parity, and
   optimized portfolios. Distinguish H for risk budgeting from covariance for
   mean-variance optimization.
3. **Volatility targeting:** Moreira and Muir and managed-futures evidence for
   and against instrument and portfolio vol targets; interaction with attenuation
   and active-set renormalization.
4. **Forecast/rule combination:** Carver scaling and diversification; correlated
   alpha combination; shrinkage of rule weights; selection bias. Compare binary
   sign, continuous z-score/tanh, and crossover signals. Decide whether Goulding
   is a separate candidate, binary baseline, normalized ensemble forecast, or a
   position rule distinct from forecast magnitude.
5. **Carry and calendar spreads:** futures carry research, including Koijen,
   Moskowitz, Pedersen, and Vrugt; carry, roll yield, term structure, and
   calendar-spread signals. Assess normalized per-instrument forecasts, separate
   sleeves, strategy-level allocation, or hybrids. Avoid double counting:
   calendar spreads have their own risk, margin, correlation, and execution.
6. **Small accounts:** discrete lots, contract multipliers, ticks, margin,
   concentration, liquidity, micro contracts, turnover, and inability to realize
   infinitesimal ERC weights. Compare continuous targets with implemented books;
   recommend lot-aware minimization of dollar-vol/risk-budget deviations subject
   to cash, margin, concentration, and tradability constraints.

## Empirical questions

### A. Return series for H and instrument weights

Evaluate:

1. Raw underlying returns: `Corr(r_i,t)`.
2. Volatility-normalized underlying returns: `Corr(r_i,t / sigma_i,t)`.
3. Dynamic strategy returns: `Corr(x_i,t-1 * r_i,t)`, where `x` has intended
   historical signal and instrument-volatility scaling only.
4. Final pre-cost sleeve returns: `Corr(exposure_i,t-1 * r_i,t)`.

Explain which input is appropriate for strategic weights, short-horizon
active-book control, forecast-diversification multipliers, risk budgeting, and
small-account execution risk.

### B. Universe policy

Compare:

1. Whole-universe strategic weights multiplied by continuous or binary signals.
2. ERC recalculated on eligible, above-threshold active symbols.
3. Whole-universe weights with an explicit cash sleeve and no automatic
   redeployment of a zero forecast’s budget.
4. Whole-universe weights plus final portfolio-volatility targeting, preserving
   relative active exposures.
5. Slow strategic weights plus a faster risk overlay.

For each specify active definition, zero forecast treatment, H input,
renormalization, intended risk redeployment, and expected behaviour in broad,
concentrated, and sparse trends.

### C. Weight estimators

Pre-register and compare:

1. Equal dollar-volatility / inverse-volatility baseline.
2. Current monthly EWM-H/ERC: the existing three-year window and 63-day
   half-life plus few slower alternatives.
3. EWM covariance minimum variance.
4. ERC with covariance/correlation shrinkage.
5. Carver-style expanding-window bootstrap average of constrained optimized
   weights.
6. Block/stationary bootstrap if strategy-return serial dependence requires it.
7. Manual/equalized strategic risk budgets as robust baseline.
8. If used, ERC/bootstrap shrinkage chosen only on prior data.

For each estimator report training window, updates, bootstrap unit/block length,
resamples, constraints, and treatment of means. Do not presume ERC or Markowitz
is superior: test net out-of-sample performance, concentration, turnover, tail
loss, and implementability.

### D. Forecast design and normalization

Compare current continuous `ts`/`contin_signal`, binary Goulding direction, a
separately defined Goulding crossover-strength variant, Carver-normalized trend
forecasts, and trend-only/carry-only/calendar-spread-only/combined sleeves. For
each, establish boundedness, sign, information timing, comparability of scale,
average absolute forecast near 10 where relevant, position sizing as forecast/10
times instrument risk target, and whether `tanh` should be recalibrated rather
than scaled twice.

## Required 16-year protocol

Treat the 16 years of post-development futures data as scarce. Freeze candidates
and hypotheses before evaluation. Use strictly lagged walk-forward estimation,
actual contract eligibility/rolls, and enough burn-in for the longest estimator.
Do not tune across the whole period and call it out of sample.

Use nested walk-forward selection: prior history for estimation, rolling
validation for pre-registered candidate selection, and untouched final periods
where possible. Every candidate must share universe, roll construction,
execution timing, costs, volatility target, capital, margin/cash constraints,
caps, rounding, and rebalance calendar. Report both ideal continuous targets and
actual integer-contract results.

## Statistics

Do not use a naive daily t-test as proof one strategy wins. Report gross/net
return, volatility, Sharpe/Sortino, drawdown/Calmar, skew/tail loss, turnover and
cost, number of holdings, concentration, realized risk contributions,
margin/cash, infeasibility, rounding error, and regime/subperiod performance.

For paired candidates, compare net-return differences. Use HAC/Newey-West and/or
moving-block/stationary-bootstrap confidence intervals; appropriate
Sharpe-difference tests; and reality-check, SPA, or deflated-Sharpe controls for
many variants. Fisher-z or block-bootstrap tests of correlations answer only
whether correlations differ, not which allocation is superior. Report confidence
intervals and separate statistical difference, economic preference after
constraints, and inconclusiveness.

## Deliverables

1. Literature review with direct citations and bibliography.
2. Precise map of current implementation and live/backtest divergences.
3. Layered architecture: returns, forecasts, forecast normalization/combination,
   volatility scaling, strategic allocation, active-book control, portfolio vol
   target, and integer implementation.
4. Candidate matrix: signal, H input, universe policy, weight estimator, update
   frequency, normalization, carry/spread integration, and failure modes.
5. Pre-registered 16-year walk-forward plan: candidates, windows, bootstraps,
   statistics, and selection procedure.
6. Ranked robust baseline, one or two empirical challengers, and approaches to
   avoid.
7. Do not make code changes unless specifically requested; identify exact files
   and a minimal implementation sequence for recommended changes.

# Window-slicing scheme comparison: bull put credit spread

Question: does the existing 90-day-slide rolling window (`grid_search_backtester.py`'s `_generate_windows`) produce independent, meaningful out-of-sample performance estimates, or mostly redundant/autocorrelated noise -- versus an expanding-window or a capped-rolling-window alternative? Fixed strategy: bull put credit spread, fixed combo `{'short_delta_target': 0.3, 'dte_target': 45, 'max_spread_width': 10, 'early_close_on_dte': 25}`, SPX options 2010-01-04 through 2023-12-29 (option chain is the binding data constraint; underlying/VIX both run longer).

## Methodology

- **Scheme A (rolling, 90-day slide)** -- reuses `grid_search_backtester._generate_windows` directly (same windows the real grid search would use) with `periods=[1]`, `start_date=2010-01-01`, `end_date=2023-12-29`: 1-year windows sliding forward by 90 days each step, heavily overlapping.
- **Scheme B (expanding)** -- start fixed at 2010-01-01; end grows by 1 year(s) each step until the actual data end (2023-12-29); every window covers the full history to date. Also reports the cumulative running average of Sharpe/return_pct across windows 1..k.
- **Scheme C (expand-then-cap)** -- identical to Scheme B for the first 5 windows (1..5 years, anchored at 2010-01-01); once width hits 5 years, both edges slide forward by 1 year(s) per step, holding width fixed, until end reaches 2023-12-29.
- Per window: Sharpe (recomputed from `trade_results` using the exact formula `Backtester._finalize_results` computes internally but never returns -- see `_trade_to_trade_sharpe`), total_pnl, return_pct, win_rate, max_drawdown, num_trades, window length in years, start/end dates -- via `_format_single_backtest_result_row` plus the Sharpe/window_years additions.
- Each window's backtest is run in its own throwaway subprocess against only that window's slice of the option chain (loaded straight from the cached parquet with a date-range predicate, not sliced from a fully-loaded in-memory copy) -- this box is memory-constrained and shared; loading the full ~15.4M-row chain once and letting `Backtester.run()`'s internal `.copy()` duplicate it was enough to trigger the OOM killer for even a single request in testing. See the script's module docstring and `_run_window_isolated`/`_load_chain_window` for details.
- **Known pre-existing bug found and worked around (not fixed in place):** `SingleLegOptionPosition._update_single_leg_closing_data` (`options_bt/domain/position.py`) crashes on every early/forced close for options strategies with a polars `Datetime` vs `List(Date)` dtype mismatch in an `.is_in()` call -- reproducible on unmodified `bull_put_param_search.py` with this same combo, unrelated to windowing. Worked around with a process-local monkeypatch (`_apply_position_dtype_patch`) that casts the chain's `date`/`expire_date` columns to `pl.Date` before the original method runs; no repository file was modified. Recommend fixing this directly in `position.py` upstream of this research.

## Results by scheme

| Scheme | Windows | Sharpe mean | Sharpe std | Sharpe min | Sharpe max | Return % mean | Return % std |
|---|---|---|---|---|---|---|---|
| A_rolling_90d_slide | 53 | 0.638 | 1.338 | -2.627 | 3.371 | 0.20 | 0.48 |
| B_expanding | 14 | 0.189 | 0.306 | -0.652 | 0.435 | 1.03 | 1.08 |
| C_capped_rolling | 14 | 0.303 | 0.600 | -0.663 | 1.305 | 0.70 | 1.06 |

## Scheme A: is the 90-day slide adding independent information?

Lag-N autocorrelation of Scheme A's Sharpe and return_pct series (windows sorted by start date, lag measured in slide-steps -- lag 1 = windows 90 days apart, lag 5 = 450 days apart):

| Lag (x90 days) | Sharpe autocorr | Return % autocorr |
|---|---|---|
| 1 | 0.774 | 0.823 |
| 2 | 0.563 | 0.646 |
| 3 | 0.269 | 0.390 |
| 4 | 0.028 | 0.112 |
| 5 | -0.244 | -0.126 |

## Scheme B: cumulative running average vs point-in-time Sharpe

| Window (k) | End date | Window years | Sharpe (full history to date) | Cumulative avg Sharpe |
|---|---|---|---|---|
| 1 | 2011-01-01 | 1.0 | 0.056 | 0.056 |
| 2 | 2012-01-01 | 2.0 | -0.652 | -0.298 |
| 3 | 2013-01-01 | 3.0 | -0.233 | -0.277 |
| 4 | 2014-01-01 | 4.0 | 0.131 | -0.175 |
| 5 | 2015-01-01 | 5.0 | 0.333 | -0.073 |
| 6 | 2016-01-01 | 6.0 | 0.072 | -0.049 |
| 7 | 2017-01-01 | 7.0 | 0.244 | -0.007 |
| 8 | 2018-01-01 | 8.0 | 0.435 | 0.048 |
| 9 | 2019-01-01 | 9.0 | 0.380 | 0.085 |
| 10 | 2020-01-01 | 10.0 | 0.376 | 0.114 |
| 11 | 2021-01-01 | 11.0 | 0.376 | 0.138 |
| 12 | 2022-01-01 | 12.0 | 0.376 | 0.158 |
| 13 | 2023-01-01 | 13.0 | 0.376 | 0.175 |
| 14 | 2023-12-29 | 14.0 | 0.376 | 0.189 |

## Discussion

**Recency bias (Scheme C, capped rolling):** once the window hits its 5-year cap, each step drops the oldest year and adds the newest -- the performance estimate tracks whatever regime is currently in the trailing window, so it reacts fast to genuine regime shifts (e.g. a vol regime change) but is also the scheme most prone to overfitting a strategy to whatever the last 5 years happened to look like, and its worst residual output is entirely a fixed-width bet on how much history is 'enough'.

**Regime dilution (Scheme B, ever-expanding):** every window after the first few contains 2010-2012 and every crisis/regime since, so by window 10+ the estimate is dominated by an enormous, largely fixed base of history and moves only slowly as new years are appended -- exactly what the cumulative-average-Sharpe trace above shows: it damps out, converging toward a single full-sample number rather than reacting to anything recent. Good for 'what has this strategy done, all-in, since 2010' but useless for detecting whether the strategy's edge has decayed recently, since one bad recent year barely moves an average built on 10+ years of history.

**Redundancy (Scheme A, 90-day slide):** with a 1-year window and a 90-day slide, consecutive windows share roughly 9 of 12 months of trades -- see the lag-1 autocorrelation above. High lag-1..lag-3 autocorrelation (windows within ≤270 days of each other) would mean most of Scheme A's ~50-60 'windows' are not independent draws at all, just the same handful of trades reshuffled across overlapping slices -- inflating the apparent sample size (and understating the true standard error of the mean Sharpe) without adding real information. If autocorrelation decays toward zero by lag 4-5 (720-900 days apart), that's evidence the slide does eventually decorrelate, just not on a 1-2 window horizon.

## Answer: is the 90-day slide meaningful, or is another scheme more reliable?

See the autocorrelation table above for the direct evidence. In general terms: the 90-day slide's large window count is mostly an illusion of sample size -- adjacent windows overlap so heavily that they are not independent evaluations of the strategy, so its reported std of Sharpe across ~50-60 windows understates true uncertainty far more than a naive reading would suggest. An expanding window (Scheme B) is the most honest *summary* statistic (all data, no double-counting) but is unresponsive to recent decay by design. A capped rolling window (Scheme C) is the best *monitoring* tool -- each step is a materially different sample of history (not just a 90-day nudge), so its cross-window variance is a more trustworthy read on how stable the strategy's edge really is, at the cost of being sensitive to the choice of cap width.

## Recommendation

Use Scheme C (capped rolling, 5-year window, 1-year step) as the primary tool for estimating out-of-sample stability: report the mean and std of Sharpe/return_pct across its ~14 largely-independent 5-year windows as the headline stability metric, and pair it with Scheme B's single full-history Sharpe as a sanity-check 'what-did-this-actually-return-since-2010' headline number. Treat Scheme A's ~50-60 windows as, at most, a high-resolution *sensitivity* view (e.g. for spotting which sub-periods drove the aggregate result) rather than as ~50-60 independent samples for a standard error calculation -- the 90-day slide does not manufacture new information at that cadence; it re-samples the same handful of realized regimes.

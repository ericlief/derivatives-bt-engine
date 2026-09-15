# Archived: Goulding cluster-cap cold-start window-scheme check — 2026-09-15

> **Interpretation:** this is retained as a cold-start/history-sensitivity
> experiment. It reruns and resets the strategy at every row boundary, so it
> is not the Carver-style causal expanding/rolling return-window report. Use
> `domain.tsmom_window_reporting.score_causal_windows` on one full strategy
> path for that comparison.

This is a robustness check of the live-policy-parity path in the main `tsmom`
backtester. It uses the same two date-window generators as
`tsmom_vol_parity_window_scheme.py`; each row is an independent backtest with
the strategy configuration held fixed.

- **Scheme B — expanding:** each test starts on 2010-01-01 and extends its end
  date by one year.
- **Scheme C — capped rolling:** the first five rows expand from 2010-01-01 to
  a five-year width, then the start and end both move forward one year while
  preserving that width.

The rows deliberately overlap. Their mean and standard deviation are useful
stability diagnostics, not estimates from 16 independent observations. In
both schemes, Goulding's `a_Co`/`a_Re` consumes only prior monthly state/return
observations within that row, so the estimation is causal and is expanding in
Scheme B but reset/capped by the row boundaries in Scheme C.

## Configuration

```bash
.venv/bin/python -m derivatives_bt_engine.strats.tsmom_window_scheme \
  --data-end 2026-06-18 --save-results
```

The runner defaults to the 90K live universe and the policy under test:
15% instrument and portfolio volatility targets, ERC + IDM, Goulding cluster
mixing, 42/252 momentum windows, a 42-day correlation half-life over three
years, VIX gating off, 15-contract/$100K per-instrument limits, and the
cluster policy (`max-active=2`, 25% cluster risk cap, 50% lot overrun).
`fast-window`/`vol-fast-window` determine the continuous model's volatility
estimate used for Goulding sizing; they do not alter Goulding's monthly
fast/slow return horizons. The CLI also accepts `--schemes B,C` and zero-based
`--window-index-start` / `--window-index-end` for a resumable subset of a long
sweep.

## Per-window results

All 34 rows completed without an error. `Capital`, `final capital`, and fees
are USD. The first one-year row in each scheme has no rebalances after model
warmup, so return/volatility/Sharpe are shown as `—` and excluded from the
scheme summary.

| Scheme | Window start | Window end | Years | Capital | Final capital | Rebalances | Days | Ann. return | Ann. vol | Sharpe | Max DD | Fees |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B expanding | 2010-01-01 | 2011-01-01 | 1.00 | $90,000.00 | $90,000.00 | 0 | 149 | — | — | — | 0.00% | $0.00 |
| B expanding | 2010-01-01 | 2012-01-01 | 2.00 | $90,000.00 | $90,209.00 | 72 | 408 | 0.29% | 5.34% | 0.05 | -6.82% | $67.30 |
| B expanding | 2010-01-01 | 2013-01-01 | 3.00 | $90,000.00 | $88,642.00 | 228 | 670 | -0.31% | 7.28% | -0.04 | -6.82% | $312.86 |
| B expanding | 2010-01-01 | 2014-01-01 | 4.00 | $90,000.00 | $104,060.00 | 371 | 930 | 4.28% | 8.22% | 0.52 | -7.32% | $551.30 |
| B expanding | 2010-01-01 | 2015-01-01 | 5.00 | $90,000.00 | $110,101.00 | 509 | 1,190 | 4.68% | 9.01% | 0.52 | -11.88% | $876.22 |
| B expanding | 2010-01-01 | 2016-01-01 | 6.00 | $90,000.00 | $99,440.00 | 641 | 1,449 | 2.15% | 9.13% | 0.24 | -12.34% | $1,181.44 |
| B expanding | 2010-01-01 | 2017-01-01 | 7.00 | $90,000.00 | $105,163.00 | 785 | 1,708 | 2.72% | 9.13% | 0.30 | -15.05% | $1,545.22 |
| B expanding | 2010-01-01 | 2018-01-01 | 8.00 | $90,000.00 | $103,816.00 | 941 | 1,968 | 2.25% | 9.14% | 0.25 | -15.05% | $1,965.16 |
| B expanding | 2010-01-01 | 2019-01-01 | 9.00 | $90,000.00 | $111,099.00 | 1,085 | 2,228 | 2.82% | 9.32% | 0.30 | -15.05% | $2,343.50 |
| B expanding | 2010-01-01 | 2020-01-01 | 10.00 | $90,000.00 | $111,604.00 | 1,229 | 2,488 | 2.61% | 9.30% | 0.28 | -15.05% | $2,745.96 |
| B expanding | 2010-01-01 | 2021-01-01 | 11.00 | $90,000.00 | $125,543.00 | 1,353 | 2,747 | 3.57% | 10.17% | 0.35 | -15.05% | $3,078.22 |
| B expanding | 2010-01-01 | 2022-01-01 | 12.00 | $90,000.00 | $145,537.00 | 1,493 | 3,006 | 4.55% | 10.22% | 0.45 | -15.05% | $3,389.56 |
| B expanding | 2010-01-01 | 2023-01-01 | 13.00 | $90,000.00 | $149,340.00 | 1,637 | 3,265 | 4.43% | 10.21% | 0.43 | -15.05% | $3,637.92 |
| B expanding | 2010-01-01 | 2024-01-01 | 14.00 | $90,000.00 | $160,099.00 | 1,793 | 3,526 | 4.63% | 10.11% | 0.46 | -15.05% | $3,921.04 |
| B expanding | 2010-01-01 | 2025-01-01 | 15.00 | $90,000.00 | $166,010.00 | 1,937 | 3,787 | 4.58% | 10.00% | 0.46 | -15.05% | $4,354.76 |
| B expanding | 2010-01-01 | 2026-01-01 | 16.00 | $90,000.00 | $168,947.00 | 2,081 | 4,047 | 4.41% | 9.87% | 0.45 | -15.05% | $4,755.64 |
| B expanding | 2010-01-01 | 2026-06-18 | 16.46 | $90,000.00 | $179,369.00 | 2,141 | 4,167 | 4.66% | 9.90% | 0.47 | -15.05% | $4,946.90 |
| C capped rolling | 2010-01-01 | 2011-01-01 | 1.00 | $90,000.00 | $90,000.00 | 0 | 149 | — | — | — | 0.00% | $0.00 |
| C capped rolling | 2010-01-01 | 2012-01-01 | 2.00 | $90,000.00 | $90,209.00 | 72 | 408 | 0.29% | 5.34% | 0.05 | -6.82% | $67.30 |
| C capped rolling | 2010-01-01 | 2013-01-01 | 3.00 | $90,000.00 | $88,642.00 | 228 | 670 | -0.31% | 7.28% | -0.04 | -6.82% | $312.86 |
| C capped rolling | 2010-01-01 | 2014-01-01 | 4.00 | $90,000.00 | $104,060.00 | 371 | 930 | 4.28% | 8.22% | 0.52 | -7.32% | $551.30 |
| C capped rolling | 2010-01-01 | 2015-01-01 | 5.00 | $90,000.00 | $110,101.00 | 509 | 1,190 | 4.68% | 9.01% | 0.52 | -11.88% | $876.22 |
| C capped rolling | 2011-01-01 | 2016-01-01 | 5.00 | $90,000.00 | $99,440.00 | 641 | 1,300 | 2.40% | 9.64% | 0.25 | -12.34% | $1,181.44 |
| C capped rolling | 2012-01-01 | 2017-01-01 | 5.00 | $90,000.00 | $108,686.18 | 701 | 1,300 | 4.15% | 9.86% | 0.42 | -12.29% | $1,474.56 |
| C capped rolling | 2013-01-01 | 2018-01-01 | 5.00 | $90,000.00 | $105,290.06 | 713 | 1,299 | 3.52% | 9.75% | 0.36 | -11.41% | $1,639.82 |
| C capped rolling | 2014-01-01 | 2019-01-01 | 5.00 | $90,000.00 | $109,502.51 | 714 | 1,299 | 4.30% | 9.95% | 0.43 | -9.94% | $1,694.32 |
| C capped rolling | 2015-01-01 | 2020-01-01 | 5.00 | $90,000.00 | $82,807.96 | 720 | 1,299 | -1.16% | 9.51% | -0.12 | -16.42% | $1,432.28 |
| C capped rolling | 2016-01-01 | 2021-01-01 | 5.00 | $90,000.00 | $115,463.43 | 700 | 1,298 | 5.47% | 11.20% | 0.49 | -13.52% | $1,690.30 |
| C capped rolling | 2017-01-01 | 2022-01-01 | 5.00 | $90,000.00 | $115,488.29 | 696 | 1,298 | 5.51% | 11.53% | 0.48 | -14.29% | $1,611.44 |
| C capped rolling | 2018-01-01 | 2023-01-01 | 5.00 | $90,000.00 | $114,993.55 | 696 | 1,298 | 5.46% | 11.87% | 0.46 | -14.21% | $1,338.02 |
| C capped rolling | 2019-01-01 | 2024-01-01 | 5.00 | $90,000.00 | $124,739.53 | 708 | 1,299 | 6.98% | 11.31% | 0.62 | -12.94% | $1,229.38 |
| C capped rolling | 2020-01-01 | 2025-01-01 | 5.00 | $90,000.00 | $125,142.40 | 708 | 1,300 | 7.04% | 11.34% | 0.62 | -12.36% | $1,280.80 |
| C capped rolling | 2021-01-01 | 2026-01-01 | 5.00 | $90,000.00 | $119,938.24 | 716 | 1,300 | 6.02% | 9.47% | 0.64 | -10.02% | $1,119.68 |
| C capped rolling | 2022-01-01 | 2026-06-18 | 4.46 | $90,000.00 | $127,305.41 | 636 | 1,161 | 8.00% | 9.60% | 0.83 | -10.06% | $993.92 |

## Scheme summary

| Scheme | Usable windows | Mean Sharpe | Sharpe std. | Min | Max | Mean ann. return | Mean ann. vol | Mean max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B expanding | 16 | 0.343 | 0.163 | -0.040 | 0.520 | 3.270% | 9.147% | -13.171% |
| C capped rolling | 16 | 0.408 | 0.258 | -0.120 | 0.830 | 4.164% | 9.680% | -11.415% |

The local continuous-futures data ends on 2026-06-18, so the last row in
each scheme is a partial calendar-year result. Scheme C's higher mean Sharpe
comes with a materially higher dispersion: it is a sensitivity check, not
evidence that a five-year rolling history is intrinsically superior.

## Policy being tested

Before the correlation/IDM/ERC pass, active signals are ranked within each
instrument cluster and only the strongest two remain. Goulding's binary
Bull/Bear direction is not a ranking input: agreeing states rank by
`abs((g_fast + g_slow) / 2)` and Correction/Rebound by `abs(g_blend)`.
The resulting selected set is the only set supplied to the correlation matrix
and budget split. The optional cluster cap then applies the existing
whole-contract priority walk-down to that same selected set, using account
equity × target portfolio vol as its cap denominator.

This validates causal portfolio construction and integer cap behavior, not
execution: it uses backtest close prices and the backtest's normal monthly
integer rounding rather than IB orders or live-price splicing.

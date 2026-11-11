# Return TSMOM reference baseline

The `return_tsmom` selection model runs the original continuous return signal
through the same portfolio construction and executable cash replay used by the
multi speed EWMAC selection variant. It is the baseline for comparing signal
models without changing the universe, weights, roll schedule, or costs.

The baseline uses:

- the reference history's positive return index for the signal;
- 64 and 256 trading day return and volatility horizons by default;
- a 0.5 forecast multiplier when the fast and slow signals disagree;
- the reference history's Panama point changes and 256 day mixed point
  volatility for contract sizing and P&L;
- yearly, lagged instrument selection when `--selection-rebalance yearly` is
  supplied;
- top-down asset class and economic subclass weights;
- the same contract costs, roll costs, position buffer, rounding, and roll
  dates as the EWMAC selection backtest.

The rule cache remains the reusable source of contract-level costs and
execution eligibility. The return model recalculates forecast turnover from
its own positions. Realized cash P&L always charges ordinary trade and roll
costs. `--include-roll-cost-in-score` additionally includes estimated roll
costs when ranking candidate books.

Run the full reference-history baseline with:

```bash
.venv/bin/futures-build-selection \
  --input results/multi_speed_ewmac_rule_cache_<timestamp>.parquet \
  --output-dir results/return_tsmom_baseline \
  --cache .cache/futures \
  --signal-model return_tsmom \
  --fast-window 64 \
  --slow-window 256 \
  --selection-rebalance yearly \
  --weight-policy topdown_classif \
  --include-roll-cost-in-score \
  --backtest-start-date 2000-01-01 \
  --initial-capital-usd 100000 \
  --target-vol 0.20 \
  --selection-report-mode compact
```

Omit `--fast-window` and `--slow-window` to use the same 64/256 defaults. For
a controlled EWMAC comparison, reuse every other argument and change only
`--signal-model multi_ewmac`.

The Goulding variants use the same pipeline and update their signal once per
calendar month. Binary Goulding emits full signed forecasts:

```bash
.venv/bin/futures-build-selection \
  --input results/multi_speed_ewmac_rule_cache_<timestamp>.parquet \
  --output-dir results/goulding_baseline \
  --cache .cache/futures \
  --signal-model goulding \
  --goulding-signal-mode binary \
  --selection-rebalance yearly \
  --weight-policy topdown_classif \
  --include-roll-cost-in-score \
  --initial-capital-usd 100000 \
  --target-vol 0.20 \
  --selection-report-mode compact
```

Change `--goulding-signal-mode binary` to `continuous` to retain the monthly
signal magnitude and apply the causal pooled forecast scalar. The Goulding
mixing parameters are estimated within economic subclass using only earlier
monthly outcomes. `--selection-rebalance` remains the book review cadence;
the Goulding position forecast itself updates monthly in either mode.

Use `--goulding-signal-mode sharpe` for the volatility-normalized continuous
challenger. This is deliberately not labeled as the paper's exact signal. Raw
fast and slow arithmetic returns still classify Bull, Correction, Bear, and
Rebound and still drive the state-outcome history used to estimate `a_co` and
`a_re`. Only the magnitude legs supplied to the continuous blend change:

$$
\sigma^F_{i,t} =
\sqrt{\frac{1}{2}\sum_{k=1}^{2} v_{i,t-k}},
\qquad
\sigma^S_{i,t} =
\sqrt{\frac{1}{12}\sum_{k=1}^{12} v_{i,t-k}},
$$

$$
F^*_{i,t}=\frac{F_{i,t}}{\sigma^F_{i,t}},
\qquad
S^*_{i,t}=\frac{S_{i,t}}{\sigma^S_{i,t}},
$$

where $v_{i,t-k}$ is the realized variance (sum of squared daily simple
returns) in completed month $t-k$. The square root of the mean monthly
variance is the RMS monthly volatility; it is not the standard error of the
fast or slow average. Both numerator and denominator exclude the forecast
month. Bull/Bear use the mean of $F^*$ and $S^*$; Correction/Rebound apply the
same `a_co`/`a_re` equation-7 blend to $F^*$ and $S^*$. The result then uses
the same causal pooled scalar and cap as `continuous`.

The selection grid accepts the same `--signal-model` and
`--goulding-signal-mode` flags and forwards them to every scenario, so book
constraint sweeps can be run separately for binary, continuous, and Sharpe-like
Goulding.

Supplying `--ib-history-db` extends executable cash replay through the stored
IB tail. Without it, both signal and P&L use the frozen reference history.

The main comparison artifacts are the timestamped
`return_tsmom_selection_book`, `return_tsmom_selection_reviews`,
`return_tsmom_selection_cash_pnl`, and
`return_tsmom_selection_portfolio_curve` Parquet reports. All expose `date` as
the time key; return-model provenance uses `ref_instr_code`.

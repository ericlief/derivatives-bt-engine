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

Supplying `--ib-history-db` extends executable cash replay through the stored
IB tail. Without it, both signal and P&L use the frozen reference history.

The main comparison artifacts are the timestamped
`return_tsmom_selection_book`, `return_tsmom_selection_reviews`,
`return_tsmom_selection_cash_pnl`, and
`return_tsmom_selection_portfolio_curve` Parquet reports. All expose `date` as
the time key; return-model provenance uses `ref_instr_code`.

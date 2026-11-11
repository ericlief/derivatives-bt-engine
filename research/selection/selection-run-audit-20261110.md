# Selection run audit: November 10, 2026

This audit covers the latest completed Goulding continuous selection run,
generated at `20261110_151448`. The scenario is
`goulding_continuous_tv0p2_im20_bm50_wi0p2_st0p85_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_selyearly_hg31_eh25_mhnone`.
Artifacts are under
`/home/dev/data/fin/misc/results/selection_v1/goulding/selection_run_20261110_151448/`.

## Roll and P&L audit

The source-handoff and monotonic-contract changes are reflected in the saved
run. There are no economically active rows with `pnl_valid=false`, and no
portfolio dates with an active book and `ret_valid=false`. The cash P&L has no
duplicate `(symbol, date)` keys; the portfolio curve has no duplicate dates.
Instrument gross and net P&L and costs reconcile to the portfolio totals
within the one-cent public-report rounding.

The previously investigated handoff dates now behave as ordinary marked
positions:

| Market | Date | Held contracts | Roll event | Gross P&L | Roll cost | P&L valid |
|---|---|---:|---|---:|---:|---|
| I | 2024-04-02 | -13 | No | $2,276.40 | $0.00 | Yes |
| I | 2024-04-15 | -13 | No | $2,976.83 | $0.00 | Yes |
| ETHUSDRR | 2024-06-03 | 45 | No | $9,000.00 | $0.00 | Yes |
| HSI | 2024-08-01 | -13 | No | $3,156.14 | $0.00 | Yes |
| TWN | 2024-09-02 | 4 | No | $240.00 | $0.00 | Yes |

I next rolls on 2024-07-15 to the 202609 contract. ZS rolls on 2025-09-15;
2025-10-28 is a valid non-roll P&L day. ZW's 2026-10-15 roll is valid and
includes $641.24 of roll cost. ZW on 2025-11-04 is inactive with zero held
contracts, so its false `pnl_valid` is expected and does not invalidate the
portfolio return.

The run records 32 active source handoffs where the cross-source return was
explicitly omitted. Most are 1–5 calendar days; the longest are JGB (263
days), IBXXIBHY (69), ZT (26), and IXR/IXU (18). Returns after each splice are
valid, but the omitted cross-gap changes are not represented in performance.
The configured 353-day maximum permits these gaps, so they remain a material
history-coverage limitation despite the clean active-row validity checks.

## Performance and costs

The run spans 1973-01-02 through 2026-11-10, starting at $75,000. Final net
equity is $52,168,264.89, comprising $61,922,662.10 gross P&L less
$5,202,020.71 of trade costs and $4,627,377.60 of roll costs. The resulting
net P&L is $52,093,264.89. CAGR is 12.92%, annualized daily volatility is
17.03%, simple annualized daily Sharpe is 0.78, and maximum drawdown is
37.98%.

Goulding forecast settings are consistent across clusters: continuous mode,
fast/slow windows 2/12 months, forecast cap 2, and target absolute forecast
1. Among active positions, realized mean absolute combined forecast is
0.828 and the maximum is 2. The realized mean below 1 is not evidence of a
normalization defect; the configured target is an expectation over the
forecast calibration, while the cap truncates tails.

These are research backtest statistics, not a live-performance estimate.
The book grows from $75,000 to over $56.8 million in the selection metadata,
so later contract sizes, margin feasibility, and market participation reflect
a much larger account than the initial capital.

## Final book coverage

The 2026-01-01 book contains 21 instruments across eight represented asset
classes and 11 economic subclasses. It sums to 99.99% weight. Coverage is
explicitly unsatisfied: one asset class, three subclasses, and zero minimum
book-count requirements remain unmet. The missing groups are `other /
commodity_index`, `bond / stir`, and `metals / industrial`.

The candidate report has eligible markets in all three missing subclasses.
The saved allocation trials show the limiting conditions: AIGCI is rejected
for the market-participation limit; attempts to add SCI or SONIA.N are rejected
once the portfolio-margin limit binds. Final portfolio margin is $28.302M
against a $28.404M limit, leaving about $102.5k of modeled headroom. Therefore
the configured minimum coverage rules do not imply that the saved book meets
coverage; inspect `coverage_satisfied` on every final book before treating it
as a covered universe.

## Audit artifacts

Primary files are the `goulding_continuous_selection_*.parquet` reports in
the scenario directory: `cash_pnl`, `portfolio_curve`, `book`,
`forecast_audit`, `candidates`, `trials`, and `review_final_allocations`.
This note audits the saved run and does not regenerate it.

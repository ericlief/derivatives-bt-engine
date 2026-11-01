# Selection run audit: 2026-10-31

This audit covers the yearly-rebalanced selection run generated at
`20261031_221905`, scenario
`tv0p2_im20_bm50_wi0p2_st0p85_fdm1_cl5_roll1_topdown_classif_selyearly`.
The run artifacts are under
`/home/dev/data/fin/misc/results/selection_v1/selection_run_20261031_221905/`.
The portfolio curve, annual review books, instrument-level review details,
cash P&L, and candidate eligibility are retained as typed Parquet files in
that scenario directory.

## Equity and accounting

The reported result is **$432.86 million ending equity**, not $432 billion.
Starting from $75,000, cumulative net P&L is approximately $432.79 million.
The equity peak is $623.07 million on 2022-06-14, leaving a 30.53% decline
from peak to the final date. Full-period CAGR is 16.49%; maximum drawdown is
37.14%.

The earlier same-parameter run ended at $134.29 million with 14.07% CAGR.
Changing rule eligibility raises the new run's CAGR by about 2.42 percentage
points and its ending equity to 3.22 times the earlier result. Daily returns
of the two runs correlate 0.803 over their common period, so the eligibility
change materially altered the portfolio path. The new run gained about 214%
in 1973, taking equity from roughly $75,000 to $235,000; this early increase
then compounds through the remaining history.

Basic P&L consistency checks pass: daily instrument P&L sums to portfolio P&L
within a cent, net equity equals initial capital plus cumulative net P&L,
daily returns use prior-day net equity, and sizing capital equals prior-day
net equity. These checks do not establish that the historical eligibility or
execution history is causal and complete.

## Rule eligibility and historical interpretation

Against the previous run, 50 symbols changed eligible-rule counts and 17
changed final eligibility. The daily return correlation and large final
equity difference show that this is a material change to the historical
simulation, rather than a small reporting adjustment.

The rule eligibility set is frozen from the current reference calibration and
applied across the full backtest. It is not recomputed using only the
information available at each historical date. Forecast and correlation
inputs are cut before each yearly review, but the frozen rule-eligibility
membership introduces lookahead for a strict out-of-sample interpretation.
Treat the full-history CAGR and compounded ending equity as research
diagnostics, not as a causal live-performance estimate.

## Instrument count and concentration

The reported 30–40 selected symbols overstates effective diversification.
The effective instrument count, calculated as the inverse sum of squared
normalized weights, is approximately 16–19 in recent full books. Examples:

| Review year | Selected instruments | Effective instruments | Largest weight | Top-five weight |
|---|---:|---:|---:|---:|
| 2020 | 36 | 17.0 | 16.67% | about 42% |
| 2023 | 36 | 17.6 | 16.67% | about 42% |
| 2024 | 29 | 16.0 | 16.67% | 43.1% |
| 2025 | 37 | 18.7 | 16.67% | 37.5% |
| 2026 | 16 | 8.0 | 14.29% | 71.45% |

Top-down classification weighting gives equal risk to each represented asset
class, then divides each class allocation among its subclasses and
instruments. A singleton class can therefore receive the same class-level
weight as a class containing many contracts. In 2026, singleton-class
contracts GC, Z, ETHUSDRR, JGB, UC, and RB each received 14.29%; ten
agricultural instruments shared only 14.29%.

The 20% `weight × IDM` sizing constraint is reflected in `limit_weight`, not
in the uncapped structural `weight`. For the six 2026 singleton-class
contracts, `limit_weight` is 8% and IDM is 2.5, giving a 20% sizing weight.
At a forecast-cap ratio of 2, that permits a maximum position representing
40% of target portfolio risk. This is the configured sizing convention and
still permits substantial concentration. The 2026 selection reached 99.47%
of its estimated 50%-of-capital book-margin limit; the margin screen stopped
further additions.

Full-size contracts such as NQ are present in earlier review books, including
2021–2025. Their presence is not itself evidence of a P&L accounting error.

## Forward-history coverage defect

Annual review counts do not match the instruments that actually generate
cash P&L after the reference-history endpoint:

| Year | Selected at review | Median instruments with daily P&L |
|---|---:|---:|
| 2023 | 36 | 36 |
| 2024 | 29 | 4 |
| 2025 | 37 | 6 |
| 2026 | 16 | 2 |

The 2026 review selected 16 symbols, but only GC and ZS have continuing
executable cash-history rows that year. Most of the other selected symbols
fall back to reference histories ending around 2024-03-28/29. The yearly
selection code filters each history to dates strictly before the review, but
does not require the latest observation to be recent relative to that review.
As a result, stale candidates can appear in the review book without producing
forward P&L. `--max-source-gap-days` applies to IB stitching; it does not
currently enforce freshness in the yearly candidate selector.

The 2024–2026 return record therefore reflects a shrinking executable subset,
not the 29–37 instruments shown in the selection review reports. The 2026
portfolio is effectively a two-instrument replay. Recent performance and
diversification statistics should not be interpreted as those of the reported
selected books until this coverage mismatch is addressed and the run is
recalculated.

The selector was subsequently changed to require the latest valid candidate
return to be within 31 calendar days of every scheduled review. The limit is
configured by `--selection-max-history-gap-days` and uses the stitched PST/IB
frame when available. A scheduled run with an IB history database also adds a
single handoff review after the reference endpoint exceeds this freshness
window, so the 2024 segment does not retain the stale January book after the
late-March reference endpoint. It remains separate from
`--max-source-gap-days`, which only controls the one-time reference-to-IB
splice. A rerun is required because this saved audit run predates the
freshness gate.

## Audit artifacts

Primary audit files:

- `multi_speed_ewmac_selection_portfolio_curve_20261031_221905.parquet`
- `multi_speed_ewmac_selection_reviews_20261031_221905.parquet`
- `multi_speed_ewmac_selection_review_instruments_20261031_221905.parquet`
- `multi_speed_ewmac_selection_cash_pnl_20261031_221905.parquet`
- `multi_speed_ewmac_selection_candidates_20261031_221905.parquet`

All are in the scenario directory named above. This note records an audit of
the saved run; it does not modify or regenerate its results.

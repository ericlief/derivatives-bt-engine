# EWMAC bounded-beam selection test — 2026-11-16

## Result

A small bounded beam does reduce the greedy selector's book-count instability,
but it does not make every 15-market target attainable. Width 4 is the useful
first sensitivity: across the four saved 2020–2023 reviews it raises average
book size from 13.50 to 15.75 and reduces the summed breadth deficit from 9 to
3. Widths 8 and 16 add little breadth beyond width 4 while costing materially
more selector time.

The clearest case is 2023. The exact greedy path stops at 11 markets with four
missing breadth slots. Widths 4 and 8 find feasible 15-market books, and width
16 finds 16 markets. In 2022, however, every tested beam stops at 12 or fewer;
the remaining three-market deficit is not repaired by retaining more of these
paths under the current granularity and 50% rounded-margin constraints.

This is a research result, not a recommendation to change the default yet.
The default remains width 1.

## Scope and reproduction

The test reconstructs the scheduled selector directly from the current saved
probation run's candidate and forecast-audit inputs:

`selection_run_20261116_145713/tv0p2_im20_bm50_wi0p2_st0p85_fdm1_`
`minmaxpos1_fal256_fap0p5_fah0p25_far2_ic20_minc1_mins1_mb15_`
`cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_`
`hg31_eh25_mhnone`

It reads the saved causal reference-return curves from the project cache and
replays the 1 January reviews for 2020 through 2023. These dates precede the
PST-to-IB handoff, so the experiment does not need to regenerate or overwrite
stitched IB history. Each width receives the identical dated candidates,
correlation matrix, capital, prior book, costs, forecast-activity state, and
hard constraints. No source market data or saved result was modified.

The raw comparison artifact is temporary:

`/tmp/saved-review-beam-comparison.parquet`

## Width-one equivalence

Width 1 dispatches to the existing greedy implementation rather than running
a one-state approximation of the beam. It reproduces the saved probation run's
annual book counts exactly for the tested years: 18, 15, 10, and 11. The
selector unit suite also passes with the unchanged width-one path.

## Dated comparison

The score is the final construction score. Margin is rounded maximum-position
margin against the same $37,500 book ceiling. Terminal count range describes
the distinct terminal paths observed by that width; it is not a relaxation of
the chosen-book constraints.

| Review | Width | Candidates | Selected | Breadth deficit | Score | Margin | Terminal count range | Selector seconds |
|---:|---:|---:|---:|---:|---:|---:|:---:|---:|
| 2020 | 1 | 139 | 18 | 0 | 1.1316 | $37,483 | 18 | 0.46 |
| 2020 | 4 | 139 | 19 | 0 | 1.1423 | $37,252 | 18–19 | 1.15 |
| 2020 | 8 | 139 | 22 | 0 | 1.2065 | $37,317 | 22–23 | 3.22 |
| 2020 | 16 | 139 | 22 | 0 | 1.2101 | $37,302 | 21–24 | 7.54 |
| 2021 | 1 | 138 | 15 | 0 | 1.2287 | $37,396 | 15 | 0.44 |
| 2021 | 4 | 138 | 17 | 0 | 1.1111 | $37,464 | 17 | 1.11 |
| 2021 | 8 | 138 | 17 | 0 | 1.1080 | $37,351 | 17–18 | 2.74 |
| 2021 | 16 | 138 | 17 | 0 | 1.1111 | $37,464 | 17–19 | 5.65 |
| 2022 | 1 | 143 | 10 | 5 | 1.0408 | $37,434 | 10 | 0.21 |
| 2022 | 4 | 143 | 12 | 3 | 0.9817 | $35,792 | 11–12 | 0.59 |
| 2022 | 8 | 143 | 12 | 3 | 0.9953 | $37,313 | 11–12 | 1.35 |
| 2022 | 16 | 143 | 12 | 3 | 0.9957 | $37,283 | 10–12 | 3.13 |
| 2023 | 1 | 159 | 11 | 4 | 1.0827 | $36,804 | 11 | 0.32 |
| 2023 | 4 | 159 | 15 | 0 | 0.9454 | $37,414 | 12–15 | 1.08 |
| 2023 | 8 | 159 | 15 | 0 | 0.9454 | $37,414 | 12–15 | 2.08 |
| 2023 | 16 | 159 | 16 | 0 | 1.0621 | $37,375 | 8–16 | 4.78 |

Across these reviews, mean selector-only runtime is 0.36 seconds at width 1,
0.98 at width 4, 2.35 at width 8, and 5.27 at width 16. Relative to greedy,
those are approximately 2.8x, 6.6x, and 14.8x. Forecast construction, history
loading, and cash replay are outside these timings.

## Interpretation

The result confirms the path-dependence diagnosis. All final books continue to
use almost the entire margin budget, but wider beams can retain a lower-margin
partial route long enough to add more markets later. More candidates alone do
not solve the problem: 2022 has 143 candidates and still cannot reach 15 in
any tested retained path.

The beam's terminal objective is lexicographic: minimize class deficit, then
subclass deficit, then total-book deficit, then maximize score. This explains
why a wider book can have a lower score, especially in 2021 and 2023. Before
making width 4 the production default, the review report should expose all
three coverage deficits for every terminal choice so that a lower score cannot
be mistaken for an unexplained regression.

The terminal range also remains wide at width 16. That range is useful audit
evidence, but selecting solely for maximum count would be wrong: it would turn
the breadth target into an unconstrained incentive to add markets. The present
objective instead treats 15 as a deficit floor and retains score as the final
tie-break after hierarchy coverage.

## Implementation tested

`--selection-beam-width N` now controls construction. Width 1 is the exact
greedy baseline. Wider beams:

- expand every retained parent through its hard-feasible family routes;
- keep the existing coverage phase and per-parent score floor;
- deduplicate identical route books;
- alternate score-leading and rounded-margin-efficient states when pruning;
- retain full granularity, liquidity, hierarchy, forecast, family, and margin
  gates; and
- persist path, parent, beam rank, prune decision, selected-path marker, and
  terminal count diagnostics.

Compact report mode omits member-level allocations for every beam trial because
their volume scales with width. Summary trials and final allocations remain
available. Full mode retains the member rows for detailed path audit.

## Recommendation

Use width 4 for the next full stitched-IB sensitivity, alongside width 1. Do
not make width 8 or 16 the default from this evidence: their incremental breadth
is small, runtime rises sharply, and 2022 still misses the target. The next run
should cover the audited 2024–2026 collapse and compare class/subclass deficits,
membership turnover, route switches, P&L, and costs—not only selected count.

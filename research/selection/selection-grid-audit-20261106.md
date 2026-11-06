# Selection hysteresis grid audit: November 6, 2026

## Scope

This audit covers
`selection_grid_20261105_222348`, an 80-scenario annual-selection sweep over:

- eligibility hysteresis of 0% and 25%;
- membership modes `none`, `rank`, `score`, and `rank_score`;
- rank buffers of 10%, 15%, and 25%;
- rank-count EWMA half-lives of 3, 5, and 10 reviews; and
- score buffers of 0%, 1%, and 2%.

The common construction settings are target volatility 20%, instrument margin
cap 20%, book margin cap 50%, weight-IDM cap 20%, score threshold 0.85, class
count cap 20, subclass count cap 15, class weight cap 50%, subclass weight cap
25%, annual reviews, and a 31-day maximum selection-history gap.

All 80 scenario directories contain a final book, cash-P&L curve, portfolio
curve, and command log. Seventy-four scenarios have the newer explicit
completion marker. Six scenarios predate the marker and satisfy the legacy
artifact-based completion check. The former end on November 6, 2026; the
legacy six end on November 5.

## Audit verdict

**The grid is not valid for performance or policy comparison.** Completion is
not the problem. The scenarios did not use one invariant stitched-history
universe after the April 2024 IB handoff.

The parallel workers retained `--ib-history-live missing`, shared one mutable
IB-history DuckDB, and used the same IB client ID. When a cached IB segment was
absent, each worker could independently retry the broker and write the shared
database. Competing processes then encountered DuckDB locks or disconnected IB
sessions. The complete-selection pipeline intentionally treats those broker
failures as nonfatal and falls back to reference history. That behavior is
appropriate for one diagnostic run, but it invalidates a parameter grid: one
scenario may receive an IB tail while another scenario using the same selected
book may not.

Consequently, terminal equity, recent return, costs, feasibility, later books,
and even the apparent effect of hysteresis combine two changes:

1. the requested selection policy; and
2. scenario-specific availability of the executable history tail.

No scenario should be selected from this grid, including the apparent
$920.47M full-history winner.

## Direct reproduction: TWD at the splice

The clearest control comparison is between these two marked scenarios:

| Policy | Scenario suffix |
|---|---|
| EH25, membership off | `eh25_mhnone` |
| EH25, rank 10%, half-life 5 | `eh25_mhrank_rb10_rhl5` |

Both select the same 15-instrument 2024 book:

`6M,AJY,BRE,CHF,CLP,DX,EURAUD,EURCAD,NOK,RF,RP,RY,TWD,UC,ZAR`

The rank scenario's own final and unbuffered sets are equal at every review, so
rank hysteresis does not explain a cash-curve difference at the April handoff.
Nevertheless, the curves diverge on April 1, 2024. The complete difference on
that date is TWD:

| Scenario | TWD cash row | Held contracts | Trade cost | Roll cost | Net P&L |
|---|---:|---:|---:|---:|---:|
| EH25, membership off | present | -595 | -$170.40 | -$2,677.50 | +$220,277.10 |
| EH25, rank 10%, half-life 5 | absent | — | — | — | — |

The total portfolio net P&L is $384,706.79 versus $164,429.69, a difference of
exactly $220,277.10. The same-book control is therefore not a policy comparison.

The logs explain the missing row. The membership-off run loads a stitched TWD
history with a PST endpoint of March 25 and an IB start of April 1. The rank
run reports:

```text
stitched_ib_history ib_unavailable ... TWD ...
IO Error: Could not set lock on file .../ib_futures_history.duckdb
... Conflicting lock is held ...
selection_stitched_history unavailable hist_instr_code=TWD
fallback=reference reason=no_ib_segment sources=['pysystemtrade']
```

The rank scenario then completes normally. Its completion marker proves that
the subprocess produced all required reports; it does not prove that the
subprocess used the same market-data identity as another scenario.

## Grid-wide evidence

The failure is not isolated to TWD:

| Diagnostic | Scenario logs affected |
|---|---:|
| DuckDB `Conflicting lock is held` | 65 / 80 |
| IB `Not connected` | 41 / 80 |
| At least one stitched-history fallback warning | 80 / 80 |

Some fallback warnings represent genuinely unavailable broker histories and
would be harmless if every scenario made the same decision. The DuckDB-lock
and connection errors are different: they are concurrency-dependent. TWD is a
directly verified case in which they change the cash curve for an identical
selected book.

The six unmarked legacy scenarios are complete but also not comparable. For
example, the baseline and one rank control share their selected set through
2004 but report IDM 2.0295 versus 2.0294 at that review. Their P&L begins to
diverge in November 2004, and dynamic equity later changes feasible raw books.
Another otherwise control-equivalent pair differs only in the last three
November dates. These are signs of input/cache timing, not robust rank-policy
effects.

## Why the apparent results differ from the prior audit

The prior audit established a common endpoint and compared matched daily
inputs before interpreting policy differences. In that audit, rank-15 and
rank-25 with zero eligibility hysteresis were identical to baseline in every
review and every cash return.

This grid superficially reverses that result. Rank-only terminal equity ranges
from approximately $318.08M to $412.39M, and the apparent best rank-only run
returns 17.95% from 2024 while baseline loses 0.30%. But the direct rank action
counts remain only zero to two reviews, and some paths diverge before a rank
set substitution occurs. The mismatch is explained by data-tail and cache
differences amplified by dynamic equity, not by stable evidence that the EWMA
rank anchor improves selection.

The same problem affects the apparent score result. EH0 `rank_score` scenarios
reach as much as $920.47M full-history equity, yet the best full-history case
loses 6.17% from 2024 and has a 44.33% maximum drawdown. These figures are
useful only as a diagnostic of path sensitivity. They cannot rank score
buffers or half-lives because the underlying executable histories differ.

## Implementation cause

The grid coordinator does run one pending selection serially before starting
the worker pool. That reduces first-writer races but does not freeze the data
layer:

1. every worker command still includes `--ib-history-live missing`;
2. every worker receives the same IB client ID;
3. every worker can retry a missing IB segment and write the same DuckDB;
4. the loader catches a broker/database failure and continues with the frozen
   reference curve; and
5. the completion marker checks report presence, not a common history
   fingerprint or coverage manifest.

The warm-up therefore cannot guarantee comparability. Any source that remains
missing, was not recognized from cache, or becomes locked can produce a
worker-specific fallback.

## Required rerun design

Do not resume this directory as the final research grid. A valid rerun should
use a new directory and two distinct phases:

1. **Materialize histories serially.** Run one acquisition-only pass with
   `--ib-history-live missing` or `update`, finish all database writes, close
   IB and DuckDB, and persist a per-instrument stitched-history manifest.
2. **Run scenarios offline.** Remove `--ib-history-live` from every scenario
   worker. Open the frozen IB database read-only and consume only the pinned
   stitched artifacts.

The acquisition pass must not double as the first measured scenario. After it
finishes, all 80 scenarios, including baseline, must be rebuilt against the
same frozen state.

Before accepting the rerun, require all of the following:

- one data-universe fingerprint in every completion marker;
- one common IB database identity and stitched-history manifest;
- identical history source, start, end, row count, and content key per symbol;
- no DuckDB write-lock, `Not connected`, or live-retry messages in worker logs;
- one common curve endpoint;
- a hard failure when a selected symbol lacks the pinned cash history; and
- exact curve equality for control-equivalent configurations when a rank
  buffer makes no set change.

Until the coordinator enforces those conditions, the safe manual workaround
is to prefetch serially, stop all database writers, and run the grid without
`--ib-history-live`. `--max-workers 1` with live acquisition is safer than the
current parallel run but still does not guarantee that an instrument acquired
late in the grid was available to earlier scenarios.

## Conclusion

The main difference from the previous audit is not a newly superior
hysteresis parameter. It is a comparability regression introduced by running
live missing-history acquisition inside parallel scenario workers. The
current grid demonstrates that dynamic equity strongly amplifies even one
missing splice row, but it does not establish the performance of rank, score,
eligibility, or combined hysteresis. Freeze the stitched inputs first, rerun
all scenarios offline, and only then repeat the book-similarity, regime,
recent-performance, and cost analysis.

# Selection hysteresis grid audit: November 6, 2026

## Resolution

The coordinator now separates history preparation from scored scenarios. A
single serial subprocess performs any requested live IB acquisition, after
which the finalized IB DuckDB is copied into the grid run and identified by a
SHA-256 manifest. Every measured command is strictly offline and reads that
frozen database; the stored-history reader no longer initializes DuckDB or
takes a writer lock. Scenario completion markers also carry the common input
fingerprint, so the invalid artifacts described below cannot be resumed into a
new comparable grid. The `selection_grid_20261105_222348` outputs remain
preserved for diagnosis, but must not be used for policy ranking.

## Corrected rerun

The valid rerun is `selection_grid_20261106_162523`. It contains 80 complete
scenarios through November 6, 2026. All 80 completion markers contain the same
input fingerprint, that fingerprint matches the run-level frozen-input
manifest, every measured command points to the same run-local IB DuckDB, and
no measured command contains live IB connection arguments. No command log
contains a DuckDB lock conflict or an IB connection failure.

The sweep holds the following settings fixed: 20% target volatility, 20%
instrument margin cap, 50% book margin cap, 20% weight-times-IDM cap, 0.85
score threshold, class/subclass count caps of 20/15, class/subclass weight caps
of 50%/25%, annual reviews, and a 31-day history-freshness requirement. It
varies eligibility hysteresis, rank buffering, rank-count smoothing, and score
hurdles.

## Corrected verdict

Eligibility hysteresis is the only effect that is both useful and reasonably
stable in this grid. With membership hysteresis off, raising the eligibility
exit buffer from 0% to 25% increases ending equity from $338.13M to $359.94M,
raises 2024-present return from -1.25% to +2.50%, and reduces the current
drawdown from the historical peak from 14.04% to 10.78%. It makes relatively
small book changes before the IB handoff and prevents marginal incumbents from
being needlessly dropped.

Rank hysteresis has little effect over most of the tested surface. Five of the
nine rank-only settings at each eligibility level reproduce their corresponding
no-membership path exactly. The outlying 25% rank buffer with a three-review
half-life improves 2024-present return to 6.99%, but ends below the simpler
EH25 control, incurs more cost, and differs from that control in only five
review years. It is useful sensitivity evidence, not enough evidence to choose
25%/3 as a production optimum.

Score hysteresis is too prone to stasis. It can produce much larger terminal
wealth because particular retained books perform extremely well in earlier
regimes, especially 2003-2012, but every score-only configuration is worse
after 2024 than the corresponding membership-off control. A zero score buffer
does not disable score hysteresis: the switch-cost hurdle remains. Score mode
rejects 16-28 annual changes, retains much larger recent books, costs more, and
keeps stale membership through the 2023-2024 universe contraction.

### What changed after freezing history

The two membership-off controls were comparatively stable across the repair.
EH25's final book is identical, its ending equity moves from $361.82M to
$359.94M, and its recent return moves from 3.03% to 2.50%. EH0 moves from
$340.65M to $338.13M and from -0.51% to -1.25%.

The unstable rank results disappear. The invalid EH0 rank-15/half-life-10 run
reported +17.70% after 2024; the corrected run reports -1.25% and is exactly
the EH0 membership-off path. Across all matched scenarios, the final-book
Jaccard between invalid and corrected artifacts averages only 89.7%, with a
minimum of 28.4%. This confirms that the large rank dispersion in the old grid
was caused by scenario-specific history rather than the rank rule.

### Representative paths

Returns are compounded net returns. Annual mean, volatility, and Sharpe use
256 business days. Post-2024 cost is the sum of daily cost percentages, so it
is reported as percentage points rather than as a compounded return.

| Policy | Ending equity | Full Sharpe | Full max DD | 2024-present return | Recent Sharpe | Recent max DD | Current DD from peak | Recent cost | Avg recent book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EH0, membership off | $338.13M | 0.997 | -27.3% | -1.25% | 0.021 | -18.1% | -14.0% | 1.723 pp | 38.7 |
| EH25, membership off | $359.94M | 1.004 | -27.1% | +2.50% | 0.122 | -18.3% | -10.8% | 1.693 pp | 39.4 |
| EH25, rank 25%, half-life 3 | $350.65M | 1.006 | -27.1% | +6.99% | 0.237 | -15.9% | -6.9% | 1.852 pp | 38.8 |
| EH0, score buffer 1% | $480.31M | 1.010 | -44.9% | -13.53% | -0.298 | -27.0% | -34.9% | 2.392 pp | 54.2 |
| EH0, rank 10% / half-life 5 / score 1% | $913.39M | 1.057 | -44.5% | -11.71% | -0.248 | -26.5% | -33.6% | 2.203 pp | 53.5 |

The $913.39M combined path is now a valid calculation, unlike the apparent
$920.47M winner in the contaminated grid. It still should not be selected: its
terminal value is dominated by a 933.9% gain in 2003-2012, while it loses
11.7% after 2024 and remains one-third below its June 2022 peak.

### Ten-year regimes

Each cell reports compounded return followed by arithmetic Sharpe. The final
window is the partial period from January 2024 through November 6, 2026.

| Policy | 1973-82 | 1983-92 | 1993-02 | 2003-12 | 2013-23 | 2024-26 |
|---|---:|---:|---:|---:|---:|---:|
| EH0 off | 2,367% / 1.74 | 254% / 0.78 | 495% / 1.13 | 218% / 0.74 | 176% / 0.77 | -1.2% / 0.02 |
| EH25 off | 2,798% / 1.82 | 249% / 0.77 | 524% / 1.16 | 215% / 0.73 | 136% / 0.67 | +2.5% / 0.12 |
| EH25 rank 25/3 | 2,798% / 1.82 | 249% / 0.77 | 524% / 1.16 | 194% / 0.71 | 135% / 0.67 | +7.0% / 0.24 |
| EH0 score 1% | 2,051% / 1.85 | 255% / 0.80 | 535% / 1.11 | 455% / 0.92 | 175% / 0.72 | -13.5% / -0.30 |
| EH0 rank 10/5 + score 1% | 2,051% / 1.85 | 255% / 0.80 | 535% / 1.11 | 934% / 1.14 | 176% / 0.72 | -11.7% / -0.25 |

The regime table is the main reason not to optimize terminal equity. The score
paths win primarily through one historical decade, do not improve 2013-2023,
and fail in the executable tail.

Average daily book count and average annual Jaccard similarity to the EH0
membership-off book show the same behavior:

| Policy | 1973-82 | 1983-92 | 1993-02 | 2003-12 | 2013-23 | 2024-26 |
|---|---:|---:|---:|---:|---:|---:|
| EH0 off | 13.1 / 100% | 23.7 / 100% | 31.8 / 100% | 39.2 / 100% | 55.7 / 100% | 38.7 / 100% |
| EH25 off | 13.3 / 95% | 23.8 / 100% | 32.2 / 99% | 39.6 / 99% | 55.5 / 97% | 39.4 / 90% |
| EH25 rank 25/3 | 13.3 / 95% | 23.8 / 100% | 32.2 / 99% | 39.6 / 98% | 55.6 / 97% | 38.8 / 84% |
| EH0 score 1% | 10.9 / 83% | 21.9 / 90% | 33.0 / 89% | 44.8 / 78% | 59.9 / 82% | 54.2 / 57% |
| EH0 rank 10/5 + score 1% | 10.9 / 83% | 21.9 / 90% | 33.0 / 89% | 47.7 / 63% | 59.9 / 82% | 53.5 / 52% |

### The 2023-2026 transition

The 2023 and 2024 membership-off books each contain the same 15 FX
instruments:

`6M, AJY, BRE, CHF, CLP, DX, EURAUD, EURCAD, NOK, RF, RP, RY, TWD, UC, ZAR`.

This is not a 100%-risk FX allocation. Nominal hierarchical weights sum to
100%, but the 25% subclass cap reduces `limit_weight` to 25% and leaves 75%
unallocated. Cash position sizing uses the limited weight. The cap therefore
de-risks the portfolio when no other class is accepted; it does not manufacture
underrepresented instruments.

| Policy | 2024 return | 2025 return | 2026 YTD | 2025 book | 2026 book |
|---|---:|---:|---:|---:|---:|
| EH0 off | -1.68% | -2.92% | +3.46% | 65 | 62 |
| EH25 off | -1.68% | -2.83% | +7.28% | 65 | 64 |
| EH25 rank 25/3 | -1.68% | +0.97% | +7.78% | 63 | 65 |
| EH0 score 1% | -16.77% | -0.03% | +3.92% | 50 | 54 |
| EH0 rank 10/5 + score 1% | -16.78% | +0.76% | +5.29% | 49 | 52 |

Score mode's 2024 loss is the clearest stasis failure. It rejects the large
reset into the limited 15-FX book and carries roughly 59 older instruments.
That choice saves a membership transition but loses about 16.8% during 2024,
versus 1.7% for the deliberately de-risked reset.

The compact changed-book audit is:

| Review | EH25 versus EH0 | Rank 25/3 versus EH25 |
|---|---|---|
| 2023 | Identical, Jaccard 100% | Identical, Jaccard 100% |
| 2024 | Identical, Jaccard 100% | Identical, Jaccard 100% |
| 2025 | Jaccard 97.0%; `DJ600` replaces `TSEMOTHR` | Jaccard 77.8%; adds `HSI, IXI, IXR, IXU, IXV, NYFANG, SIXM`; removes `CHF, DJ600, ESTX50, HG, IXE, MHI, SMI, TWN, XINA50` |
| 2026 | Jaccard 72.6%; adds `BTS, CC, ESTX50, HG, IBEX35, N225M, SONIA.N, SSG, SX7E, UC, XIN0I`; removes `C, ETHUSDRR, IXB, IXI, IXR, IXU, IXV, SMI, ZN` | Jaccard 92.5%; adds `IXR, SMI, TSR20`; removes `IXE, XIN0I` |

Before 2020, EH25 differs from EH0 in only scattered years and generally by
one or two instruments. Rank 25/3 differs from EH25 only in 2010, 2011, 2014,
2025, and 2026. Its apparent recent advantage therefore depends heavily on
the 2025 substitution and should be tested on later untouched reviews.

### Turnover and costs

EH25 modestly improves membership continuity: total annual additions plus
removals fall from 862 to 846 and mean consecutive-book Jaccard rises from
68.1% to 69.5%. Rank 25/3 only reduces churn to 839. Score 1% cuts churn much
further to 498 and raises consecutive-book Jaccard to 81.8%, but this is not a
free improvement: it rejects 23 reviews and preserves economically stale
books. The combined rank/score path changes book only 27 times in 53
transitions, versus 51 for EH0, and exhibits the same recent failure.

Post-2024 realized costs are:

| Policy | Trade cost | Roll cost | Total | Cash total |
|---|---:|---:|---:|---:|
| EH0 off | 1.088 pp | 0.634 pp | 1.723 pp | $5.66M |
| EH25 off | 1.085 pp | 0.609 pp | 1.693 pp | $5.74M |
| EH25 rank 25/3 | 1.148 pp | 0.704 pp | 1.852 pp | $5.99M |
| EH0 score 1% | 1.526 pp | 0.866 pp | 2.392 pp | $11.46M |
| EH0 rank 10/5 + score 1% | 1.440 pp | 0.764 pp | 2.203 pp | $19.79M |

The score paths trade membership less often but still cost more as a fraction
of capital because they retain larger books with more ongoing forecast trades
and rolls. Selection turnover is only one component of realized cost.

For EH0, `SCI` is the largest recent cost contributor at $954.9K, including
$556.9K of roll cost. It alone accounts for 16.9% of post-2024 cash costs.
`ETHUSDRR` contributes $371.2K and `BRE` $331.9K. `MCL` and `CL` together
contribute $161.6K, only 2.9% of total costs. Replacing micro exposure with a
larger contract can reduce tickets per unit risk when the larger contract has
better cost-per-risk and adequate granularity, but it cannot explain or repair
the portfolio-wide cost burden. This grid does not test a forced micro-to-full
substitution policy, so that change requires a separate paired-contract test.

### Recommendation

Use EH25 with membership hysteresis off as the current research default. It is
the simplest setting with a consistent recent improvement and slightly lower
cost. Keep rank hysteresis available, but treat 10-15% as a conservative live
candidate and collect forward reviews before adopting the apparent 25%/3
winner. Do not enable score or combined score/rank hysteresis by default.

The final 2026 books in this grid are sized from roughly $327M-$336M of path
equity. They are not launch books for a new $75K account. A crude rescaling of
the EH0 2026 allocation leaves only five instruments with at least 0.5 current
maximum contracts, and recomputing selection at $75K would change both
membership and weights. Current-account projections therefore require a fresh
live selection at $75K, not division of the historical P&L curve.

## Invalid-run appendix: scope

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

### Invalid-run verdict

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

### Direct reproduction: TWD at the splice

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

### Grid-wide evidence

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

### Why the apparent results differ from the prior audit

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

### Implementation cause

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

### Required rerun design

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

### Invalid-run conclusion

The main difference from the previous audit is not a newly superior
hysteresis parameter. It is a comparability regression introduced by running
live missing-history acquisition inside parallel scenario workers. The
current grid demonstrates that dynamic equity strongly amplifies even one
missing splice row, but it does not establish the performance of rank, score,
eligibility, or combined hysteresis. Freeze the stitched inputs first, rerun
all scenarios offline, and only then repeat the book-similarity, regime,
recent-performance, and cost analysis.

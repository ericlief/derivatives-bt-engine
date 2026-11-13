# Pooled carry-turnover estimation

**Date:** 2026-11-12

**Status:** re-audited and corrected in code; rule-cache rebuild required

## Decision

Carry-rule affordability now uses turnover estimated from the reviewed
pysystemtrade reference-history pool. The AFTS Table 40 values remain in the
cache as comparison inputs but do not decide eligibility.

The smoothing audit found that the first implementation incorrectly passed
the AFTS labels 5/20/60/120 as pandas center-of-mass values. AFTS defines
those numbers as EWMA spans. The implementation now uses explicit spans, and
carry forecast cache version 2 prevents old COM-based artifacts from being
accepted as current.

This matches the existing trend-rule ownership boundary: the expensive,
history-derived turnover estimate belongs to `futures-rule-cache`, while
`futures-build-selection` combines that frozen estimate with each market's
trade SR, roll SR, and configured annual roll count.

## Estimation sample

The calibration uses `/home/dev/fin/db/pysystemtrade_reference.duckdb` at
source commit `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8` and the reviewed default
pooling map. The map retains 228 representatives after its duplicate and
excluded-history decisions. Their available reference histories span
1969-12-02 through 2024-03-29.

Carry eligibility requires at least 256 valid forecast observations and 90%
post-warm-up coverage. Of the 228 reviewed representatives, 171 meet that
policy and contribute to the pooled turnover. The other 57 remain visible in
the calibration audit but receive no weight.

The current estimate deliberately stops at the PST reference boundary. A
future all-history estimate can add the stitched IB segment, but its cache
identity must include a fixed as-of date and source content so a newer local
IB database cannot revise an older selection run.

## Formula

For each eligible instrument `i` and carry smoothing rule `s`, the individual
turnover is

```text
turnover(i, s) = 256 * mean(abs(forecast(t, s) - forecast(t-1, s)))
```

The denominator is one because the repository stores forecasts on its
divided-by-ten Carver scale. This is the same convention as the EWMAC
calculation, whose unscaled forecast uses a target average absolute value of
ten.

The cross-market pool follows the trend cache's observation weighting:

```text
pooled_turnover(s) = sum(history_obs(i) * turnover(i, s))
                     / sum(history_obs(i))
```

`history_obs(i)` is the complete daily execution-history row count, rather
than only the number of non-null forecast rows. Carry5/20/60/120 are built in
one pass per instrument using scalar 3, component cap 2, bounded seven-day
staleness, and the declared gap/contract validity rules.

The daily difference is included only when the current and previous rows are
valid and belong to the same carry smoothing segment. This prevents an
invalid row, a provider handoff, or a long data gap from being counted as a
forecast trade. The first implementation filtered invalid rows before taking
the difference, which bridged those boundaries and inflated turnover.

## Result versus AFTS

| Rule | Corrected pooled PST audit | AFTS Table 40 | Difference | PST / AFTS |
|---|---:|---:|---:|---:|
| Carry5 | 19.0933 | 5.75 | +13.3433 | 3.3206 |
| Carry20 | 9.4135 | 3.12 | +6.2935 | 3.0172 |
| Carry60 | 6.0745 | 1.82 | +4.2545 | 3.3376 |
| Carry120 | 4.9044 | 1.22 | +3.6844 | 4.0200 |

These figures reproduce the existing 171-instrument eligible pool with the
correct span semantics and segment-safe differences. They are an audit result,
not a replacement for the persisted rule cache: rebuild the cache before the
next selection run. The local estimates remain above the AFTS benchmark at
every speed, with the largest proportional difference at Carry120.

For comparison, applying the correct spans but retaining the old filtered-row
difference produced 20.1514, 10.6875, 7.4733, and 6.3388 annual trades. The
gap to the corrected figures isolates the segment-bridging defect.

## Rule-cache and selection fields

The public rule cache persists, for each speed:

- `pool_carry_<speed>_ann_trades`: production pooled turnover;
- `ref_carry_<speed>_ann_trades`: AFTS Table 40 comparison;
- `pool_carry_<speed>_minus_ref_ann_trades` and
  `pool_carry_<speed>_to_ref_turn_ratio`: direct comparison;
- pool instrument counts, reviewed counts, history dates, and source labels.

`futures-build-selection --signal-model carry` now requires all four pooled
turnover fields and the source marker
`pooled_pst_history_afts_span_segment_safe_v2`. An older rule cache fails with
an instruction to rebuild it; there is no fallback to the AFTS constants. The
selection forecast audit also records the pooled values and the AFTS
references used for that run.

## Runtime and isolated selection check

With all 228 PST histories already in the local history cache, rebuilding the
four carry forecasts and pooling turnover takes about six minutes on the local
VM. Static per-instrument forecast artifacts used by complete selection are
now versioned separately under carry forecast cache version 2; this pooled
rule-cost calibration is the slower offline cache boundary.

The earlier fixed-$75,000 candidate counts were produced from the invalid
COM-based rule cache and are superseded. They must not be used to assess the
corrected carry universe; record new counts only after rebuilding the rule
cache and rerunning selection from that frozen input.

## Implementation and verification result

The correction was committed on `feat/carry-signal` as `fd91651` and pushed
to the configured remote on 2026-11-13. The production path now has four
explicit safeguards:

- `Carry5/20/60/120` are passed to Polars as EWMA `span` values;
- turnover differences require two consecutive valid rows in the same
  `carry_segment`;
- static and stitched carry forecasts use cache version 2 and identify their
  smoothing inputs as `carry_smooth_spans`;
- candidate and complete selection reject rule caches without the v2
  segment-safe source marker.

Focused verification completed with 37 passing tests across carry
calculation, turnover estimation, selection-cache identity, rule-cache
schema, and carry eligibility. A carry-filtered repository run completed with
16 passing tests and 711 deselected tests. Both `futures-rule-cache --help`
and `futures-build-selection --help` loaded successfully from the project
virtual environment.

The repository-wide suite remains blocked during collection by the unrelated
missing `_resolve_globex_asset` import in `test_ewmac_notebook.py`. No saved
market screen, rule cache, selection result, or backtest result was
regenerated or overwritten as part of this correction.

## Reproduction

Rebuild the rule cache after acquiring or copying a current market snapshot:

```bash
.venv/bin/futures-rule-cache \
  --snapshot-input /path/to/futures_market_snapshot_<timestamp>.parquet \
  --history-db /home/dev/fin/db/pysystemtrade_reference.duckdb \
  --rule-cost-limit-sr 0.15 \
  --output /home/dev/data/fin/misc/results/multi_speed_ewmac_rule_cache.parquet
```

Then pass the resulting timestamped Parquet to
`futures-build-selection --signal-model carry`. For a new isolated
eligibility pass:

```bash
.venv/bin/futures-build-selection \
  --input /home/dev/data/fin/misc/results/multi_speed_ewmac_rule_cache_<timestamp>.parquet \
  --output-dir /home/dev/data/fin/misc/results/carry_pooled_turnover \
  --initial-capital-usd 75000 \
  --target-vol 0.20 \
  --signal-model carry \
  --candidates-only
```

Replace `--candidates-only` with `--complete` and add the normal history, IB
history, and selection-cache arguments when building and replaying the full
book.

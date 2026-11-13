# Pooled carry-turnover estimation

**Date:** 2026-11-12

**Status:** implemented for the futures rule cache

## Decision

Carry-rule affordability now uses turnover estimated from the reviewed
pysystemtrade reference-history pool. The AFTS Table 40 values remain in the
cache as comparison inputs but do not decide eligibility.

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

## Result versus AFTS

| Rule | Pooled PST | AFTS Table 40 | Difference | PST / AFTS |
|---|---:|---:|---:|---:|
| Carry5 | 13.7745 | 5.75 | +8.0245 | 2.3956 |
| Carry20 | 8.3282 | 3.12 | +5.2082 | 2.6693 |
| Carry60 | 6.3281 | 1.82 | +4.5081 | 3.4770 |
| Carry120 | 5.6151 | 1.22 | +4.3951 | 4.6025 |

The local pooled estimates are substantially higher at every speed. The
largest proportional difference is Carry120, whose estimate is about 4.6
times the AFTS benchmark. Carry selection will therefore apply a stricter and
locally reproducible cost gate than the first fixed-AFTS implementation.

## Rule-cache and selection fields

The public rule cache persists, for each speed:

- `pool_carry_<speed>_ann_trades`: production pooled turnover;
- `ref_carry_<speed>_ann_trades`: AFTS Table 40 comparison;
- `pool_carry_<speed>_minus_ref_ann_trades` and
  `pool_carry_<speed>_to_ref_turn_ratio`: direct comparison;
- pool instrument counts, reviewed counts, history dates, and source labels.

`futures-build-selection --signal-model carry` now requires all four pooled
turnover fields. An older rule cache fails with an instruction to rebuild it;
there is no fallback to the AFTS constants. The selection forecast audit also
records the pooled values and the AFTS references used for that run.

## Runtime and isolated selection check

With all 228 PST histories already in the local history cache, rebuilding the
four carry forecasts and pooling turnover took about six minutes on the local
VM. The subsequent cached `futures-build-selection --candidates-only` pass
took under one second and made no IB request. Static per-instrument forecast
artifacts used by complete selection remain versioned under the selection
cache; this pooled rule-cost calibration is the slower offline cache boundary.

At the fixed $75,000 capital and 20% target-volatility screen, 187 of 215
execution instruments retained at least one live carry rule under the pooled
turnovers, compared with 190 under the original fixed AFTS turnovers. The
remaining liquidity, size, data, cost, and execution gates accepted 113 of
215 instruments in the isolated candidate pass. The pooled rule distribution
was 181 instruments with all four rules, four without Carry5, two with only
Carry60/120, and 28 with no live carry rule.

## Reproduction

Rebuild the rule cache after acquiring or copying a current market snapshot:

```bash
.venv/bin/futures-rule-cache \
  --snapshot-input /path/to/futures_market_snapshot_<timestamp>.parquet \
  --history-db /home/dev/fin/db/pysystemtrade_reference.duckdb \
  --rule-cost-limit-sr 0.15 \
  --output results/multi_speed_ewmac_rule_cache.parquet
```

Then pass the resulting timestamped Parquet to
`futures-build-selection --signal-model carry`. For the isolated eligibility
pass used above:

```bash
.venv/bin/futures-build-selection \
  --input results/multi_speed_ewmac_rule_cache_<timestamp>.parquet \
  --output-dir results/carry_pooled_turnover \
  --initial-capital-usd 75000 \
  --target-vol 0.20 \
  --signal-model carry \
  --candidates-only
```

Replace `--candidates-only` with `--complete` and add the normal history, IB
history, and selection-cache arguments when building and replaying the full
book.

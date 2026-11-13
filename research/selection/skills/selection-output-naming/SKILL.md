---
name: selection-output-naming
description: Define and review public naming and ordering for futures market snapshots and rule-cache reports.
---

# Selection output naming

Use this skill when changing snapshot, rule-cache, or market-screen output
schemas. Keep field names explicit about the rule, speed, data mode, and
variable.

## Rule fields

Name per-rule values as `RULE_SPEED_MODE_VARIABLE`. Use underscore-separated EWMAC speeds
such as `ewmac_4_16` and carry smoothing spans such as `carry_5`. Use `ref` for
an instrument's pysystemtrade reference-history measurement, `pool` for a
universe estimate, `afts` for a published AFTS comparison, and `live` for
point-in-time executable cost calculations.

Examples:

- `ewmac_4_16_ref_turn`: that instrument's reference-history 4/16 forecast
  turnover estimate.
- `ewmac_4_16_pool_turn`: pooled 4/16 turnover across reviewed reference
  histories, used to estimate rule costs.
- `ewmac_4_16_pool_instrs`: number of reference histories contributing to
  that speed's pooled estimates.
- `ewmac_4_16_ref_tot_ann_cost_sr`: that instrument's annualized reference
  trading plus roll cost in SR units for that speed.
- `ewmac_4_16_live_tot_ann_cost_sr`: the equivalent point-in-time executable
  cost result.
- `carry_5_ref_turn`: that instrument's reference-history Carry5 turnover.
- `carry_5_pool_turn`: pooled Carry5 turnover across reviewed reference
  histories.
- `carry_5_afts_turn`: published AFTS turnover benchmark.
- `carry_5_pool_pre_cost_sr`: pooled pre-cost Sharpe when that statistic is
  calculated and persisted.

Turnover fields use `turn`; annual turnover values may use `ann_turn` when the
annual unit needs to be explicit. Do not label a published AFTS constant as
`ref`, since `ref` denotes the instrument's reference-history value.

## Shared values and ordering

Persist shared instrument-level reference inputs once, outside the speed
groups. Emit `ref_ann_rolls`, `ref_trade_sr`, and `ref_roll_sr` once before
rule-specific fields. Roll frequency and reference cost rates are common to
all speeds and must not be repeated under every rule. The primary selected or
live `trade_sr` and `roll_sr` inputs also appear once in the main cost section.

Group rule fields together in increasing numeric speed. Within each speed,
place instrument reference measurements, pooled estimates, published
benchmarks, and executable-cost results in that order. Keep snapshot quote
alternatives in the `snap_` section, after rule fields. Reserve `ref_` for
reference inputs and per-instrument reference-history measurements.

## Snapshot artifacts

Keep snapshot names and rule-cache names distinct. A snapshot records acquired
point-in-time market and quote inputs; a rule cache combines that snapshot
with reference-history rule estimates. Preserve timestamped filenames for
reproducible runs and include the artifact kind in the filename, such as
`futures_market_snapshot_<timestamp>.parquet` and
`multi_speed_ewmac_rule_cache_<timestamp>.parquet`.

When changing a public schema, update the centralized rename map, report
ordering, readers, documentation, and schema checks together. Keep internal
calculation names descriptive and apply compact tokens at the public report
boundary.

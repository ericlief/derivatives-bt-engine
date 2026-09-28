# pysystemtrade pooling-universe audit

This audit leaves the source DuckDB unchanged. It first identifies related
histories from symbols, normalized descriptions, broker identity, and the
reviewed economic-family map. It then compares the same canonical
roll-neutral returns and selected contracts used by the backtester, together
with configured roll cycles, offsets, and carry relationships.

The reviewed mapping is authoritative; pairwise `assessment` is supporting
evidence and never deletes a history automatically.

Representative selection uses canonical daily coverage, not raw mixed-frequency
row counts. Exact daily-coverage ties prefer the established full-sized contract
source unless a reviewed source-quality reason says otherwise.

## Summary

- Source histories: 252
- Default normalization histories: 228
- Excluded duplicate/mixed histories: 24
- Candidate pairs inspected: 70
- Mapping fingerprint: `fe05a4f078ad7428`

| pooling_role | instruments |
| --- | --- |
| distinct_contract | 32 |
| distinct_roll_policy | 19 |
| excluded_mixed_history | 2 |
| execution_duplicate | 22 |
| primary | 20 |
| singleton | 157 |

## Excluded from the default normalization pool

| instrument_code | economic_family_id | roll_policy_id | pooling_role | representative_instrument | decision_basis |
| --- | --- | --- | --- | --- | --- |
| AEX | AEX | AEX_ALL_MONTHS | execution_duplicate | AEX_mini | same_contract_policy_history |
| AUD | AUDUSD | AUDUSD_QUARTERLY | execution_duplicate | AUD_micro | same_contract_policy_history |
| CAD_micro | CADUSD | CADUSD_QUARTERLY | execution_duplicate | CAD | same_contract_policy_history |
| CHF_micro | CHFUSD | CHFUSD_QUARTERLY | execution_duplicate | CHF | same_contract_policy_history |
| CRUDE_W_micro | WTI | WTI_NYMEX_SMALL_MIXED | excluded_mixed_history | CRUDE_W | reviewed_mixed_regime |
| CRUDE_W_mini | WTI | WTI_NYMEX_SMALL_MIXED | excluded_mixed_history | CRUDE_W | reviewed_mixed_regime |
| DOW | DOW30 | DOW30_QUARTERLY | execution_duplicate | DOW_mini | same_contract_policy_history |
| ETHER-micro | ETHER | ETHER_CME_MONTHLY | execution_duplicate | ETHEREUM | same_economic_exposure_source_divergence |
| EUR | EURUSD | EURUSD_QUARTERLY | execution_duplicate | EUR_mini | same_contract_policy_history |
| EUR_micro | EURUSD | EURUSD_QUARTERLY | execution_duplicate | EUR_mini | same_contract_policy_history |
| GAS_US | HENRY_HUB_GAS | GAS_NYMEX_MONTHLY | execution_duplicate | GAS_US_mini | same_config_execution_variant |
| GBP | GBPUSD | GBPUSD_QUARTERLY | execution_duplicate | GBP_micro | same_contract_policy_history |
| GOLD | GOLD | GOLD_COMEX | execution_duplicate | GOLD-mini | realized_history_near_identical |
| GOLD_micro | GOLD | GOLD_COMEX | execution_duplicate | GOLD-mini | realized_history_near_identical |
| HANG_mini | HANG_SENG | HANG_SENG_ALL_MONTHS | execution_duplicate | HANG | same_contract_policy_history |
| IBEX_mini | IBEX35 | IBEX35_ALL_MONTHS | execution_duplicate | IBEX | same_contract_policy_history |
| JGB | JGB10 | JGB_OSE_QUARTERLY | execution_duplicate | JGB-mini | same_contract_policy_history |
| JPY | JPYUSD | JPYUSD_QUARTERLY | execution_duplicate | JPY_mini | same_contract_policy_history |
| NASDAQ_micro | NASDAQ100 | NASDAQ100_QUARTERLY | execution_duplicate | NASDAQ | same_contract_policy_history |
| RUSSELL | RUSSELL2000 | RUSSELL2000_QUARTERLY | execution_duplicate | RUSSELL_mini | same_contract_policy_history |
| SGD | USDSGD | USDSGD_ALL_MONTHS | execution_duplicate | SGD_mini | same_config_execution_variant |
| SP500_micro | SP500 | SP500_QUARTERLY | execution_duplicate | SP500 | same_contract_policy_history |
| TWD-mini | TWDUSD | TWDUSD_ALL_MONTHS | execution_duplicate | TWD | same_config_execution_variant |
| VIX | VIX | VIX_MONTHLY | execution_duplicate | VIX_mini | same_config_execution_variant |

All execution variants remain in `instrument_classification.csv` and in the
futures cost/risk report. `candidate_pairs.csv` contains return correlation,
contract-month agreement, exact contract agreement, two-sided five-day roll
matching, both roll configurations, descriptions, broker symbols, exchanges,
history starts, and the reviewed decision for every first-pass candidate.

## Interpretation

- `execution_duplicate` is omitted only from pooled parameter estimation; it
  remains available as a tradable contract and cost diagnostic.
- `distinct_roll_policy` and `distinct_contract` remain in the research pool
  because their history differences are intentional rather than contract-size
  duplication.
- `excluded_mixed_history` is a reviewed data-quality/policy decision. The WTI
  mini/micro histories change regime inside one series, so the default pool
  instead retains the consistent NYMEX December and ICE monthly histories.
- Full and micro Ether have the same economic exposure and roll configuration,
  but Carver's stored EOD marks diverge materially. The full contract is the
  sole normalization representative; the micro remains available for execution
  and source-quality analysis.
- A long backfilled mini/micro history is research coverage, not evidence that
  the small contract itself traded throughout that period.

# Proposal: using pysystemtrade futures data with the Globex database

Research date: 2026-09-17. This is the design proposal and Phase 1
implementation record. The source data examined is the local checkout at
`/home/dev/projects/pysystemtrade/data/futures`; the existing market-data
source is `/home/dev/fin/db/globex_mdp_3.0.duckdb`.

The companion [carry and long-history research plan](pysystemtrade-carry-and-long-history-plan.md)
defines the proposed forecast rules, causal backtests, hyperparameter-correlation
study, and forecast-weight comparison that build on this data work.

## Recommendation

Import Carver's shipped data into a **separate, reproducible DuckDB sidecar**:

```text
/home/dev/fin/db/pysystemtrade_reference.duckdb
```

Do not add the data to `globex_mdp_3.0.duckdb`. The Globex database should
remain the read-only source of record for paid/raw contract OHLCV. The sidecar
should preserve Carver's transformed adjusted, multiple-price, carry, roll,
FX, and configuration data with its own provenance and quality flags. Research
jobs can open both databases read-only or attach the sidecar transiently.

MongoDB is unnecessary for this use case. pysystemtrade uses MongoDB/Arctic as
a mutable production store for live prices and trading state, but its CSV data
classes are first-class inputs. Our workload is immutable historical research,
columnar scans, joins, and deterministic rebuilds, which fit DuckDB and Polars
better than an additional database service.

The highest-value first use is not immediate expansion to all 252 markets. It
is a controlled extension of the existing CME TSMOM history, followed by a
carry prototype:

1. Validate and map the 12-14 current TSMOM underlyings during the long
   2010-2024 overlap.
2. Use Carver's data to extend those signal histories backward, in several
   cases to the 1970s or 1980s.
3. Keep Globex contract bars authoritative from 2010 onward for trade prices,
   contract selection, volume, and roll execution.
4. Add carry only from same-timestamp `PRICE`/`CARRY` pairs and apply explicit
   instrument/date quality masks.
5. Expand to non-CME markets only in a research-only portfolio after contract
   specifications, currency conversion, costs, and investability are verified.

## Phase 1 build status

The deterministic raw-data importer is implemented in
`derivatives_bt_engine.data.pysystemtrade_import` and exposed as the
`pysystemtrade-import` CLI. It writes the independently rebuildable sidecar at
the recommended path; it does not attach to or write the Globex database.

The last fully regenerated overlap artifact used schema v3 and source commit
`b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8` and produced:

| Build result | Value |
|---|---:|
| Sidecar size | 286,273,536 bytes |
| Manifest files | 803 |
| Hashed source bytes | 717,390,223 |
| Multiple-price rows | 7,249,183 |
| Adjusted-price rows | 7,248,713 |
| Instruments | 252 |
| Source range | 1969-12-02 23:00 to 2024-03-29 05:00 |

The materialized QA results reproduce the session-normalized statistics in
this document: 89.5% mean, 98.9% median, and 90.0% weighted carry-day coverage;
205 instruments at or above 90% and 26 below 50%; 40 adjusted series with
non-positive history; 152,992 null adjusted rows; and five non-positive raw
price rows. A second clean build was compared with `EXCEPT ALL` in both
directions across the manifest, every raw table, and every QA table. It had no
logical differences. Import time is intentionally the sole run-specific
metadata value.

Schema v3 preserved every original `source_timestamp` and added a normalized
`trade_date` to adjusted and multiple-price rows. Sunday timestamps are assigned
to Monday's futures trading session. The current build shifted 34,299 adjusted
rows and 34,301 multiple-price rows; `qa.session_date_normalization` records
those counts. It also materializes stream-specific daily tables after session
normalization. Each stream selects its last complete observation independently:
normally the 23:00 row, otherwise the final complete timestamp for that session.
This prevents an incomplete late carry row from displacing an earlier valid
same-timestamp price/carry pair.

| Canonical daily stream | Selected days | 23:00 selections | Last-complete fallbacks |
|---|---:|---:|---:|
| Adjusted price | 1,249,673 | 1,064,871 | 184,802 |
| Mark | 1,249,673 | 1,064,884 | 184,789 |
| Carry pair | 1,124,999 | 963,601 | 161,398 |

Two source quirks are retained and made auditable instead of silently cleaned:

- `CANOLA`, `COAL-GEORDIE`, and `GAS-PEN` roll calendars contain an unnamed
  trailing boolean field. It is preserved as nullable `legacy_flag`; only
  null/false values are accepted.
- `EDOLLAR#1` and `INR-micro` each contain two different roll transitions at
  the same timestamp. Both rows are preserved with their physical source-row
  number and reported by `qa.roll_calendar_duplicate_timestamps`.

Run a new build explicitly with:

```bash
pysystemtrade-import \
  --source /home/dev/projects/pysystemtrade/data/futures \
  --output /home/dev/fin/db/pysystemtrade_reference.duckdb
```

The command refuses an existing output unless `--replace` is supplied. A
replacement is assembled and validated in a temporary database and only then
published atomically. The importer also refuses to use the configured Globex
path as its output. Generated DuckDB files and temporary builds are ignored by
Git.

## Phase 2 and 3 implementation status

Phase 2 originally provided signal, mark, and carry frames using adjusted
differences as a return proxy. Phase 4 corrects that provisional adapter.
`FuturesHistory` now also carries a generated Panama frame;
`PysystemtradeHistoryProvider` reconstructs both the additive series and the
contract-consistent return index from raw multiple prices. The supplied
adjusted level is retained only for validation. Marks retain the raw selected-
contract price and identity, while carry retains same-timestamp pairs.
`GlobexHistoryProvider` uses the same prior-contract reference convention.

Both providers open DuckDB read-only. Cache paths are isolated by source and
history schema version; pysystemtrade caches additionally include the source
Git commit, while Globex research caches include a database fingerprint. The
legacy `FuturesDataLoader` default remains Globex-only, but its cache moved to
the source/version-qualified `globex/v1` namespace.

Phase 3 now has a reproducible `futures-overlap-report` CLI. The generated
[14-market report](pysystemtrade-overlap/report.md),
[machine-readable summary](pysystemtrade-overlap/summary.csv), and
[roll matches](pysystemtrade-overlap/rolls.csv), plus the
[source-specific missing dates](pysystemtrade-overlap/missing_dates.csv),
compare 2010-06-07 through
2024-03-29 without changing either database. All mappings remain candidates;
the automated recommendation is triage, not approval.

Important findings are:

- JPY/6J is the only mapping currently clearing the automatic return/trend
  thresholds for manual approval review: 0.906 daily-return correlation,
  0.982 trend correlation, and 96.9% contract-month agreement.
- US10/ZN is similarly strong (0.899 return and 0.988 trend correlation) but
  remains blocked on explicit Treasury fractional-price scale validation.
- Silver was the only instrument with a material session-date issue. Its
  legacy observations through 2021-06-30 are now mapped to the preceding
  actual SI trading session; later observations retain their normalized date.
- `CRUDE_W` is an annual December winter contract, not a monthly WTI front
  contract. `CORN`, `SOYBEAN`, and `WHEAT` likewise hold one annual delivery
  month in this data. They may still be useful underlying trend histories, but
  they are not direct validations of the Globex liquid-front roll sequence.
- Trend correlations are much stronger than exact daily-return correlations
  for most mappings. This supports the proposed independent-signal use case,
  but not source substitution for execution marks or roll replay.
- BRE/6L remains contaminated by the known local Globex sticky-anchor issue.

Phase 3 therefore produced the evidence and exception list, but its mapping
approval exit criterion is deliberately not marked complete. JPY and then
US10 are the sensible first manual reviews; WTI, grains, silver, and BRE need
the documented reconciliation work before approval.

### Post-report session-date audit and resolution (2026-09-18)

The original missing-date output overstated Globex gaps because the two
providers did not apply the same session-date convention. The Globex `daily`
table already incorporates the repository's UTC correction and merges
Sunday UTC session fragments forward into the next trading day. The Carver
provider previously grouped its mixed-frequency source rows with
`CAST(source_timestamp AS DATE)`, which turns Sunday-evening observations
into separate daily rows.

Of the 3,194 dates reported as present only in Carver, 1,863 (58.3%) are
Sundays. Excluding the already-known BRE/6L sticky-anchor problem, 1,732 of
1,835 apparent Carver-only dates (94.4%) are Sunday artifacts. Removing them
leaves 103 non-Sunday discrepancies outside BRE; these include genuine
coverage gaps, exchange-holiday differences, and other source-specific
omissions. Silver's separate one-calendar-day lead/lag result also remains
after Sunday normalization.

The Sunday artifact is resolved in importer schema v3. `raw.adjusted_prices` and
`raw.multiple_prices` retain the original timestamp and store Sunday rows with
the following Monday `trade_date`. Materialized `daily.adjusted_prices`,
`daily.marks`, and `daily.carry` then select the last complete observation per
normalized session, and the history provider recomputes returns and rolls from
those tables. `HISTORY_SCHEMA_VERSION=3` invalidates the old Carver and
source-neutral Globex history caches; the Globex database itself was not
changed.

The EOD-normalized, Silver-aligned report contains 1,325 Carver-only dates and
747 Globex-only dates across the 14 mappings. The six-row reduction relative to
the Sunday-only report is the expected collapse of legacy Silver dates that map
to the same actual SI session (seven rows collapse over Silver's complete
history). No generated Silver trade date is a weekend.

### Silver session alignment audit

Silver is the only mapping with a configured cross-provider date adjustment.
The source regime changes after the last Sunday timestamp on 2021-06-27: Carver
observations through 2021-06-30 are assigned to the preceding **actual Globex SI
session**, not blindly shifted one calendar day. This keeps the resulting dates
Monday through Friday and handles exchange holidays without creating Sundays.
From 2021-07-01 onward, the Carver and Globex dates are already aligned and are
left unchanged.

Before this adjustment, Silver's same-date non-roll return correlation was
0.358 and its best naive calendar shift was +1 day at 0.654. After mapping to
actual SI sessions and recomputing returns, the -1/0/+1 return correlations are
0.160 / 0.816 / 0.028. The corresponding trailing 20-session annualized-
volatility correlations are 0.906 / 0.915 / 0.900. The best return and
volatility shifts are therefore both zero. All other 13 mappings also have
their best return correlation at zero without a configured adjustment; BRL is
the sole case whose highly autocorrelated volatility measure peaks at +1 day,
which is not evidence for a price-date shift.

Regenerate the evidence with:

```bash
futures-overlap-report \
  --carver-db /home/dev/fin/db/pysystemtrade_reference.duckdb \
  --globex-db /home/dev/fin/db/globex_mdp_3.0.duckdb \
  --output-dir research/pysystemtrade-overlap
```

## Why a separate database

| Choice | Advantages | Problems | Decision |
|---|---|---|---|
| New tables in `globex_mdp_3.0.duckdb` | Easy local joins | Mutates the paid source-of-record, couples backups/licensing/schema, and makes clean rebuilds harder | Reject |
| Separate DuckDB | Isolation, provenance, replaceable build, independent schema/versioning, read-only attachment | One extra path/connection | **Use this** |
| Parquet files only | Simple and portable | Weak catalog/provenance, awkward multi-table QA and mappings | Use only for versioned caches/exports |
| MongoDB | Mirrors Carver's production setup | Service overhead with no benefit for immutable backtests | Reject |

The importer should refuse to use the configured `GLOBEX_DB_PATH` as its
output path. It should build a temporary sidecar, validate it, then atomically
publish the new file. Both backtest data connections should use
`read_only=True`. No persistent view or table in the Globex database should
reference or copy the Carver data.

## Existing data landscape

### Globex/DATABENTO source

The current DuckDB is approximately 1.5 GiB and contains:

- 10,882,152 rows in `daily` across 101 parsed assets and instrument types;
- 477,969 rows for 17 outright futures roots;
- daily contract OHLCV from 2010-06-07 through 2026-06-18;
- actual `instrument_id`, parsed symbol, expiration, volume, and contract
  metadata; and
- the raw ingredients needed to select and mark real contracts.

Its current outright roots are `6J`, `6L`, `6M`, `CL`, `ES`, `GC`, `MGC`,
`NIY`, `NQ`, `SI`, `SOX`, `ZC`, `ZL`, `ZN`, `ZS`, `ZT`, and `ZW` (17 unique
roots; `MGC` is separately present). This source has far more execution detail
than Carver's files, but a much shorter history and a small universe.

### pysystemtrade source

| Directory | Files | Approx. size | Contents |
|---|---:|---:|---|
| `multiple_prices_csv` | 252 | 445 MiB | Current, forward, and carry prices plus contract IDs |
| `adjusted_prices_csv` | 252 | 237 MiB | Additively back-adjusted continuous series |
| `roll_calendars_csv` | 276 CSVs | 2.0 MiB | Current/next/carry contract roll schedule |
| `fx_prices_csv` | 12 | 3.1 MiB | USD conversion series |
| `crypto_spread_roll_calendars_csv` | 8 | 36 KiB | BTC/ETH spread calendars |
| `csvconfig` | 3 | 72 KiB | Instrument, roll, and spread-cost configuration |

The 252 multiple-price files contain 7,249,183 rows. The global observed
range is 1969-12-02 through 2024-03-29. The adjusted files contain 7,248,713
rows. All 252 multiple-price instruments have corresponding adjusted-price,
instrument-config, roll-config, and spread-cost entries.

The instrument mix is:

| Asset class | Instruments |
|---|---:|
| Equity | 57 |
| FX | 43 |
| Agriculture | 36 |
| Bonds/rates | 34 |
| Sectors | 34 |
| Metals | 21 |
| Oil/gas | 20 |
| Volatility | 4 |
| Housing | 2 |
| Other | 1 |

This data is fully tracked in the upstream Git repository rather than stored
as Git LFS pointers. The last commit that changed both price directories was
2024-05-01, and almost all series stop on 2024-03-28 or 2024-03-29. It is a
historical seed, not a current feed.

## What the files represent

Each multiple-price CSV contains:

```text
DATETIME,CARRY,CARRY_CONTRACT,PRICE,PRICE_CONTRACT,FORWARD,FORWARD_CONTRACT
```

- `PRICE` is the selected contract currently held.
- `FORWARD` is the contract intended to be held after the next roll.
- `CARRY` is the comparison contract designated for the carry rule.
- Contract IDs such as `20240600` encode a delivery year/month; `00` is not
  an actual expiry day.

An adjusted price is a synthetic continuous level with mechanical roll gaps
removed. If the expiring contract is 100 and the forward contract is 105 at a
roll, switching raw contracts would look like a false +5 market move. Carver's
Panama stitch adds that +5 differential to the older adjusted history, making
the old series end at 105 before appending the new contract. Subsequent
adjusted differences represent market movement rather than the price-level
change caused solely by changing contracts. The adjusted level is therefore a
point-signal device, not a traded price and not the authoritative P&L ledger.

This is not a complete per-contract market database. It does not preserve
every listed contract, OHLC, volume, or open interest. It is sufficient for
continuous-price trend research, approximate futures P&L, historical rolls,
and carry. Globex remains necessary for execution-aware work and rebuilding a
contract book from actual daily liquidity.

## Data-quality audit

### Price and adjusted series

- All 252 multiple-price files parsed successfully.
- No duplicate or null timestamps were found in the multiple-price files.
- The raw `PRICE` field is non-null on a mean 98.4% of rows; asynchronous
  intraday leg updates account for much of the remaining sparsity.
- Only five non-positive raw current-price observations were found: three
  zeroes in `HANGENT_mini` and two in `MSCITAIWAN`.
- 197 adjusted files have exactly the same timestamp sequence as their
  multiple-price file. Across the other 55, adjusted files contain 470 fewer
  rows in total. All starts agree and 246 ends agree.
- 128 adjusted files contain at least one null price, totaling 152,992 null
  intraday rows.
- Forty adjusted series become non-positive historically. This is an expected
  consequence of additive/Panama back-adjustment, not evidence that the
  underlying futures traded at those negative levels.

The last point is architecturally important. The current repository's signal
pipeline computes `close.pct_change()` and horizon ratios such as
`close / close.shift(n) - 1`. A Panama-adjusted level must **not** be inserted
directly as `close`: negative or near-zero adjusted levels make percentage and
log returns meaningless.

For return-defined models, construct a positive index from the raw selected
contract and its contract-consistent reference:

```text
reference[t-1] = PRICE[t-1]                         # no roll
reference[t-1] = FORWARD[t-1]                       # matched roll
ret_1d[t] = PRICE[t] / reference[t-1] - 1

signal_index[t] = signal_index[t-1] * (1 + ret_1d[t])
```

Existing return TSMOM and Goulding code can consume this index. Carver EWMAC
instead consumes the separately generated additive Panama series. Position
notional and P&L continue to use raw contracts and roll-neutral point changes.

### Carry coverage

Coverage below means a day with a valid same-timestamp `PRICE`, `CARRY`,
`PRICE_CONTRACT`, and `CARRY_CONTRACT`, divided by days with a valid current
price. This is stricter and more meaningful than merely forward-filling each
leg independently.

Across the full history:

- median instrument carry coverage is 98.9%;
- mean instrument coverage is 89.5%;
- weighted coverage across all instrument-days is 90.0%;
- 205 of 252 instruments have at least 90% coverage; and
- 26 instruments have less than 50% coverage.

The weakest complete-history series include:

| Instrument | Carry coverage |
|---|---:|
| FANG | 13.1% |
| US30 | 15.4% |
| SP400 | 17.8% |
| EU-BANKS | 18.2% |
| CAD_micro | 18.5% |
| EURO600 | 18.9% |
| US-INDUSTRY | 18.9% |
| EU-TRAVEL | 19.2% |
| DAX | 22.6% |
| DOW | 23.6% |

Much of this is old backfill sparsity: carry appears only around quarterly
roll windows, sometimes with 80-100 day gaps. Coverage improves materially in
the final years:

- weighted coverage is 96.7% in 2023 and 97.4% in 2024;
- over 2023 through March 2024, 222 instruments exceed 90% and 201 exceed 99%;
- in 2024 alone, 235 exceed 90% and 210 exceed 99%; and
- the four 2024 series below 50% are `BB3M` (4.9%), `JGB-SGX-mini` (31.2%),
  `OATIES` (34.4%), and `RICE` (47.5%).

`FORWARD` coverage is slightly worse over the full history: median 98.8%,
mean 86.7%, and weighted 83.3%.

The import must preserve nulls. Carry should be calculated only after aligning
valid current/carry observations at the same source timestamp. Forward-filling
the two price legs separately before subtraction would combine fresh current
prices with stale carry prices and manufacture a false roll yield.

### Roll and metadata observations

- Roll calendars are auxiliary once multiple prices have been built, but they
  are valuable for QA and comparison with Globex roll selection.
- Four multiple-price series have no matching roll-calendar CSV: `COTTON`,
  `CRUDE_W_micro`, `EUR_micro`, and `RUSSELL_mini`.
- Twenty-eight roll-calendar names have no current multiple-price series,
  mostly retired aliases or numbered crypto/rate variants.
- Carver warns that some mini histories were derived from full-size contracts
  and that historical carry offsets can differ from the current
  `rollconfig.csv`. Neither mini/micro price duplication nor roll configuration
  should be accepted silently.
- Timestamps are timezone-naive and mix old daily 23:00 observations with
  recent intraday observations. Schema v3 retains `source_timestamp` exactly
  and derives a separate, documented `trade_date` rather than assuming the
  strings are UTC.

## Initial symbol crosswalk

The following are high-confidence candidates for the first overlap study. A
micro traded contract should normally share the full-size signal history, as
the repository already does through `signal_symbol`; importing duplicate
micro histories into the covariance universe would double-count a market.

| Repo/Globex signal root | Carver series | Carver range | Carry coverage | Notes |
|---|---|---|---:|---|
| ES / MES | SP500 | 1982-09-14 to 2024-03-28 | 63.4% | Use main series for both signal roots |
| NQ / MNQ | NASDAQ | 1999-12-14 to 2024-03-28 | 96.5% | Direct price-scale match expected |
| CL / MCL | CRUDE_W | 1990-10-16 to 2024-03-28 | 99.1% | Underlying match only: Carver holds the annual December winter contract, not the monthly liquid front |
| GC / MGC | GOLD | 1975-04-01 to 2024-03-28 | 99.5% | `GOLD_micro` exists but should not be a second risk factor |
| SI / SIL | SILVER | 1970-06-15 to 2024-03-28 | 70.6% | Point size is 1,000; mapping/settlement date needs extra validation |
| ZN / MTN | US10 | 1982-08-30 to 2024-03-28 | 95.0% | Treasury fractional pricing needs exact scale checks |
| ZT | US2 | 2000-03-02 to 2024-03-28 | 99.3% | Direct candidate |
| ZC / MZC | CORN | 1972-10-18 to 2024-03-28 | 91.1% | Underlying match only: Carver holds annual December rather than the liquid front |
| ZL / MZL | SOYOIL | 1970-02-03 to 2024-03-28 | 92.6% | Underlying match; roll cycles differ materially |
| ZS / MZS | SOYBEAN | 1985-09-20 to 2024-03-28 | 99.5% | Underlying match only: Carver holds annual November rather than the liquid front |
| ZW / MZW | WHEAT | 1973-12-19 to 2024-03-28 | 96.5% | Underlying match only: Carver holds annual December rather than the liquid front |
| 6J / JPY / J7 | JPY | 1977-06-14 to 2024-03-28 | 98.8% | Same underlying, traded multipliers remain separate |
| 6M | MXP | 1995-09-15 to 2024-03-28 | 92.1% | Naming crosswalk required |
| 6L / BRE | BRE | 1995-12-01 to 2024-03-28 | 100.0% | Useful independent reference for the known Globex 6L issue |

`NIY` to Carver `NIKKEI` is only a candidate, not an approved mapping:
Carver describes a Nikkei 225 Mini with a JPY 100 point size, while the current
backtest-only `NIY` spec uses JPY 500. `SOX` has no obvious equivalent. Both
should remain unmapped initially.

An exploratory non-roll-day overlap check found strong but non-identical daily
change correlations for most candidate pairs. Differences in settlement time,
vendor, selected contract, and timezone are material enough that symbol-name
similarity alone is not an acceptance test. Silver is especially in need of a
date/contract-level reconciliation.

## Proposed sidecar schema

The first import should preserve source data rather than prematurely forcing it
into the current Globex schema.

### `meta` schema

`meta.dataset_manifest`

- source repository path and Git commit;
- source file relative path, size, SHA-256, and modification time;
- import timestamp and importer/schema version; and
- row count, minimum timestamp, and maximum timestamp.

`meta.schema_version`

- integer database schema version; and
- importer version/commit.

### `raw` schema

`raw.multiple_prices`

```text
instrument_code VARCHAR
source_timestamp TIMESTAMP
trade_date DATE
price DOUBLE
price_contract VARCHAR
carry DOUBLE
carry_contract VARCHAR
forward DOUBLE
forward_contract VARCHAR
source_file VARCHAR
```

Contract IDs should be strings, not numeric values, so leading structure and
the synthetic `00` day cannot be altered by inference.

`raw.adjusted_prices`

```text
instrument_code VARCHAR
source_timestamp TIMESTAMP
trade_date DATE
adjusted_price DOUBLE
source_file VARCHAR
```

Additional raw tables should preserve roll calendars, FX prices,
`instrumentconfig.csv`, `rollconfig.csv`, and `spreadcosts.csv` without
overwriting repository live-trading specifications.

`raw.roll_calendars` also retains `calendar_type`, one-based physical
`source_row_number` (including the CSV header offset), and the optional
`legacy_flag`. Roll timestamps are not declared unique because the source has
two known same-time sequential transitions; their source-row keys are unique
and their timestamp conflicts are materialized for QA.

### `ref` schema

`ref.instrument_map`

```text
canonical_market_id VARCHAR
carver_instrument VARCHAR
globex_asset VARCHAR
repo_trade_symbol VARCHAR
mapping_status VARCHAR       -- approved, candidate, rejected
usage_status VARCHAR         -- signal, carry, research_only, excluded
effective_start DATE
effective_end DATE
notes VARCHAR
```

Mappings must be data, not scattered Python aliases. A canonical market ID
lets ES and MES share one S&P risk factor while preserving different traded
contract specifications.

`ref.quality_policy`

- per-instrument usable date ranges for price and carry;
- minimum carry coverage/gap rules;
- known zero-price exclusions;
- mapping approval and reviewer notes; and
- currency/point-value verification status.

### `curated` schema

Prefer views where practical so derived logic stays auditable:

- `daily.roll_inputs`: EOD current/forward prices and contract IDs for audit;
- generated Panama stream: locally stitched point price, roll differential,
  supplied-adjusted validation error, and quality flags;
- generated return stream: contract-consistent reference, roll-neutral point
  change, normalized return, positive index, and return-validity flags;
- `curated.carry`: matched current/carry observations, contract-month year
  fraction, raw spread, annualized roll, and validity flags;
- `curated.rolls`: inferred contract changes reconciled against shipped roll
  calendars; and
- `curated.coverage`: rows, dates, null percentages, maximum gaps, zero-price
  counts, and carry/forward coverage.

For carry, retain the Carver sign convention explicitly:

```text
raw_roll = price - carry
year_fraction = carry_contract_year_month - price_contract_year_month
annualized_roll = raw_roll / year_fraction
```

The implementation should mirror pysystemtrade's exact month-fraction and
zero-differential flooring after tests reproduce known sample outputs.

## Engine data contract

The current `FuturesDataLoader.daily` returns one `close` that downstream code
uses for both signal returns and mark-to-market P&L. That is already a fragile
conflation around contract rolls; it cannot safely represent Carver data.

Introduce a source-neutral history object with four explicit streams:

1. **Return-signal stream**: `date`, positive `signal_index`, contract-consistent
   arithmetic return, reference price, source, and data-quality flags.
2. **Execution/mark stream**: date, raw selected-contract price, contract ID or
   expiration, multiplier/currency, and volume when available.
3. **Carry stream**: matched current/carry prices and contract IDs plus an
   annualized carry observation.
4. **Additive signal stream**: a locally generated Panama price, roll-neutral
   point change, roll differential, and reconstruction-quality flags.

The imported Carver adjusted CSV remains in `raw.adjusted_prices` and
`daily.adjusted_prices`, but only as a provenance-preserving regression oracle.
Operational signal series are generated from `raw.multiple_prices`; importing
the supplied adjusted level as the working series would prevent the same code
from being used for Globex and IB and would risk double-adjusting future rolls.

Keep `FuturesDataLoader` as the Globex implementation and add a separate
Carver provider. A later hybrid provider composes them. The default
`data_source=globex` path must remain behaviorally unchanged until the hybrid
path passes comparison tests.

Cache paths must include source and schema version, for example:

```text
.cache/futures/globex/v1/ES_daily.parquet
.cache/futures/globex/v7/<database-fingerprint>/ES_signal.parquet
.cache/futures/pysystemtrade/v7/<source-commit>/SP500_signal.parquet
```

The first path is the unchanged legacy `FuturesDataLoader` cache. The v7 paths
are the source-neutral history-provider caches. Asset-only cache names are
unsafe once two sources can provide the same market.

## TSMOM use cases

### 1. Long-history signal robustness

Run the existing 12-market TSMOM universe over multiple historical starts:

- Carver-only signal research for the full available history;
- identical-universe 2010-2024 Carver versus Globex overlap;
- hybrid long history through the current Globex endpoint; and
- the unchanged Globex-only control.

The purpose is robustness across inflation, rate, commodity, and equity
regimes absent from a 2010+ sample. Primary comparisons should include signal
direction agreement, forecast correlation, realized volatility, turnover,
roll dates, drawdowns, and portfolio results—not only Sharpe.

### 2. Separate signals from executable P&L

For dates before Globex coverage:

- compute return signals from contract-consistent raw references;
- estimate research P&L as `contracts * multiplier * roll_neutral_point_change`;
- size notional from the unadjusted current-contract `PRICE`;
- infer rolls from `PRICE_CONTRACT` changes; and
- charge documented commission/slippage assumptions at those rolls.

This is a defensible research approximation, not an execution replay.

From 2010 onward:

- Globex remains authoritative for raw marks, volume, contract identity,
  expiration, fills, and executable rolls;
- a locally constructed roll-adjusted Globex signal index should be the
  preferred modern signal input; and
- Carver acts as an independent signal/roll/carry comparison source.

Do not concatenate Carver's adjusted level directly onto a raw Globex
front-contract close. Splicing should operate on contract-consistent returns
or roll-neutral point changes, with a documented handoff date and overlap
report.

### 3. Carry and trend-plus-carry

Add carry as a separate forecast family only after the price-only adapter is
stable:

1. Reproduce Carver annualized roll on a small set such as `GOLD`, `CRUDE_W`,
   `US10`, `CORN`, and `JPY`.
2. Normalize carry by causal ex-ante volatility and smooth it with registered,
   fixed parameters.
3. Apply per-instrument/date masks; do not treat missing carry as zero.
4. Test carry alone, trend alone, and a pre-specified trend/carry blend.
5. Report how eligibility changes portfolio breadth, cluster risk, IDM, and
   realized post-rounding risk.

For 2024 onward, a current carry stream will eventually need to be built from
Globex's contract curve. That requires an instrument-specific current/forward/
carry contract-book builder using validated active months and liquidity—not
the term-structure diagnostic's simple nearest-expiry ranking. The 2010-2024
overlap provides the validation window for this builder.

### 4. Broader cross-asset research

The 252-market universe can test Carver-style diversification, strategic
instrument weights, sector caps, and long-sample covariance stability. It
should initially be research-only because:

- many contracts are outside the current CME/IB execution universe;
- point values, currency conversion, costs, and exchange calendars require
  validation;
- duplicated mini/micro series can exaggerate effective breadth;
- a 252-way daily inner join would discard large amounts of data under the
  current `build_returns_wide` implementation; and
- carry quality varies sharply by instrument and period.

That 252-history source panel is not itself a live small-account universe.
[`circularity-in-sizing-a-small-account.md`](circularity-in-sizing-a-small-account.md)
separates normalization, operational eligibility, capital feasibility,
dynamic point-in-time allocation membership, continuous shadow targets, and
the final integer book. It also defines how the fixed configuration snapshot
can produce a lagged historical SR-cost proxy, and the pre-registered filters
required before using the large panel for portfolio allocation.

Start with weekly synchronized returns and an approved liquid universe. A
broad-universe implementation will likely need coverage-aware pairwise or
cluster-level covariance handling rather than the current complete-case inner
join across every supplied symbol.

## Phased implementation plan

### Phase 0: freeze the source contract

- Record the pysystemtrade Git commit and file hashes.
- Document that both original data trees and the Globex database are
  immutable inputs.
- Add the proposed database and generated exports to `.gitignore` before the
  importer can run.
- Define the canonical market IDs and approve the initial crosswalk.

Exit criterion: every imported row can be traced to one source file and
commit, and no source database/file is modified.

### Phase 1: deterministic sidecar importer

Status: **implemented and validated on 2026-09-17**.

- Add a Polars-based importer CLI with explicit source and output paths.
- Import raw price/config tables in one transaction into a temporary DB.
- Enforce types, unique price/adjusted `(instrument_code, source_timestamp)`
  keys, source-row keys for roll calendars, row counts, and checksums.
- Materialize the audit/coverage tables and compare them to the statistics in
  this document.
- Publish the sidecar only after validation succeeds.

Exit criterion: rebuilding from the same commit produces the same manifest,
row counts, coverage results, and logical table contents.

### Phase 2: loader and signal-return adapter

Status: **implemented and validated on 2026-09-17**.

- Add the source-neutral signal/mark/carry contract.
- Implement the Carver daily return index without direct percentage changes
  on adjusted levels.
- Preserve the current Globex loader as the default.
- Version cache locations by source.
- Add narrow tests for SP500, GOLD, CRUDE_W, US10, and JPY.

Exit criterion: negative adjusted levels never enter percentage-return math,
roll-adjusted returns are causal, and existing Globex tests/results remain
unchanged.

### Phase 3: overlap validation

Status: **reporting implemented; mapping approvals pending manual review**.

- Generate 2010-2024 per-market reports comparing contract selection, daily
  changes, roll dates, volatility, missing dates, and trend forecasts.
- Investigate settlement-date or timezone shifts rather than automatically
  forcing alignment.
- Approve/reject mappings individually, with special attention to silver,
  BRE/6L, Treasury price scaling, and Nikkei.
- Compare Carver point sizes to repository multipliers without automatically
  overwriting live specs.

Exit criterion: the initial mapped universe has explicit approved ranges and
no unexplained large signal/P&L discrepancies.

### Phase 4: hybrid long-history TSMOM

Status: **source-neutral backtester and CLI implemented on 2026-09-18;
mapping approval and full-universe validation remain pending**.

#### Source-of-truth and generated representations

The source of truth is always contract-aware raw data.  The v4 Carver sidecar
adds `daily.roll_inputs` for inspection while retaining the complete
mixed-frequency `raw.multiple_prices` stream.  Reconstruction deliberately
runs on the full chronological stream *before* selecting the final normalized
session observation; otherwise an intraday roll row and its forward quote can
be lost.

For an unchanged selected contract:

```text
reference[t-1] = PRICE[t-1]
pt_change_1d[t] = PRICE[t] - reference[t-1]
ret_1d[t] = pt_change_1d[t] / reference[t-1]
```

At a Carver roll, require
`FORWARD_CONTRACT[t-1] == PRICE_CONTRACT[t]`, then use:

```text
reference[t-1] = FORWARD[t-1]
pt_change_1d[t] = PRICE[t] - FORWARD[t-1]
ret_1d[t] = pt_change_1d[t] / reference[t-1]
roll_differential[t] = FORWARD[t-1] - PRICE[t-1]
```

The two named daily fields are paired measurements of the same matched-
contract move: `pt_change_1d` is in price points and `ret_1d` is a fractional
simple return. Neither field is volatility-normalized. The history cache
schema is v7 to keep these names separate from older Parquet caches and to
invalidate indices built before invalid daily sessions were removed from the
compounded return path.

The local Panama implementation is algebraically identical to Carver's
forward mutation: every new roll differential is added to all earlier
observations, leaving the newest segment at its raw price.  It is implemented
as a reverse cumulative adjustment to avoid quadratic repeated mutation.
Carver's own documentation says adjusted prices are generated from multiple
prices and that Panama is the default stitch; his worked EWMAC example uses
the stitched price, point differences for volatility, and an EMA-price
difference for the numerator ([pysystemtrade data documentation](https://github.com/pst-group/pysystemtrade/blob/develop/docs/data.md),
[EWMAC example](https://github.com/robcarver17/systematictradingexamples/blob/master/ewmac.py),
[rolling discussion](https://qoppac.blogspot.com/2015/05/systems-building-futures-rolling.html)).

The three supported signal classes therefore have intentionally different
inputs:

| Signal class | Required input | Reason |
|---|---|---|
| repository return TSMOM | positive contract-return index | horizons and volatility are defined from arithmetic returns |
| Goulding monthly | positive contract-return index | monthly price relatives must be multiplicative, not additive-Panama ratios |
| Carver EWMAC | generated Panama point price | EMA differences and daily volatility must share point units |

`domain.futures_signal_harness` enforces this routing.  `carver_ewmac` exposes
the raw point-vol-normalized forecast; its scalar is explicit and defaults to
one because calibrated forecast scalars differ by speed pair and must not be
invented.  Raw contract marks—not either derived signal level—remain the
authoritative execution, sizing, cost, and roll record. P&L uses the
roll-neutral point changes generated from those contract-aware raw inputs.

The repository's return TSMOM no longer computes EMA-price or MACD columns on
the positive index. Those columns were charting diagnostics with no path into
its forecast and were easily confused with Carver EWMAC. Return TSMOM now
contains only its actual horizon-return/return-volatility construction;
Carver EWMAC is computed independently from Panama prices and the completed
forecasts may be joined by date for comparison.

Forecast units also remain explicit. The current backtester supports one
EWMAC speed pair, computes
`clip(((fast_ewma - slow_ewma) / point_vol) * forecast_scalar, -20, 20)`, and
then divides by 20 for its internal `[-1, 1]` exposure. This preserves
comparability with the repository's bounded tanh signal without applying a
second nonlinear squash to Carver's already capped forecast.

The scalar is now estimated causally by default. For each date and speed pair,
the configured pool takes the cross-sectional median absolute raw forecast;
the scalar is `10 / expanding_mean(prior daily medians)`, shifted so date `t`
uses dates strictly before `t`, with no future backfill. The default
`ewmac_scalar_universe=pysystemtrade` makes `global` pool all 252 eligible
Carver instruments while trading only the requested symbols. The explicit
`backtest` universe limits normalization to configured symbols and is required
for `cluster` (`instruments.py` clusters) or `instrument` (each individual
history) pooling. `fixed` retains the explicit
`ewmac_forecast_scalar` parity hook. The default requires 500 prior daily
pool observations. Zero raw forecasts are omitted from scale estimation and
each instrument is forward-filled only after its first usable observation,
matching the important mechanics of Carver's pooled estimator. Instruments
may contribute to normalization even when a later cost filter would give the
rule zero trading weight: scale eligibility and trading eligibility are
separate concerns.

The full-universe path has two cache layers. Existing versioned history
caches retain each instrument's signal, marks, carry, and generated Panama
series. A derived EWMAC cache is keyed by history schema, source commit,
fast/slow/vol spans, and a range fingerprint over every eligible instrument's
raw/adjusted start timestamp, end timestamp, and row count. Cache hits validate
both that fingerprint and exact instrument membership before reading the
252-instrument raw-forecast panel and coverage table. A second derived file,
additionally keyed by target magnitude and minimum observations, stores the
causal scalar history. Consequently the first run builds missing histories and
forecasts; matching later runs read the panel and scalar directly, while any
source-range or membership change selects a new cache. A backtest's requested
years do not truncate the normalization input: all pre-start history remains
available, and the daily scalar report can be sliced after its causal
calculation.

The 2026-09-22 integration build found all 252 eligible instruments and
materialized 1,249,673 raw-forecast rows over instrument histories spanning
1969-12-02 through 2024-03-29. The pooled cache contains 14,192 daily scalar
rows; for EWMAC 16/64 with 35-day point volatility, target 10, and a 500-day
warm-up, its final scalar is approximately 4.7441. An immediate repeat load
hit both the forecast-panel and scalar caches. The current range key is
`range19691202_20240329_n252_c580f0fc08c52f8d`.

Each run returns `ewmac_scalar_history` and the CLI prints its last rows. Saved
runs write a separate `*_tsmom_ewmac_scalars_*.csv` (and `ewmac_scalars`
spreadsheet tab) containing pool, date, available-instrument count, daily
cross-sectional median, prior observation count, historical mean, scalar,
validity, explicit normalization-universe label and configured count, target,
cap, and speed parameters. The CLI prints the separate CSV's
path after saving it. As with the mixing-parameter CSV, this calibration report
is an independent artifact rather than being repeated across rebalance rows.
The companion `*_tsmom_ewmac_universe_*.csv` (and `ewmac_universe` spreadsheet
tab) has one row per normalization instrument, including the traded symbol,
source instrument code, pool, source segments, `ts_start`, `ts_end`, first and
last usable-forecast timestamps, and both raw-history and usable-forecast row
counts. This makes staggered history availability auditable without repeating
instrument ranges on every daily scalar row. The daily report also records
the configured and normalization-universe instrument counts, source commit,
and whether the forecast-panel and scalar caches were hits.
Each traded EWMAC row also records `scalar_as_of_date` and
`scalar_carried_forward`. In a hybrid validation after the Carver universe
ended, the May 2024 rebalance explicitly showed a scalar as-of 2024-03-29 with
the carry flag set; changing data source therefore recalculates the traded
hybrid rule, while reuse or extrapolation of the separately calibrated scalar
is visible and range-checked.
When an EWMAC family is added,
scale and cap each speed rule in 10/20 units first, combine those forecasts
with weights summing to one, apply a causal forecast-diversification
multiplier if used, and cap the combined forecast again. A component's
forecast weight does not change its own average-absolute-10 calibration
target.

The TSMOM backtester now consumes the same separation through
`domain.tsmom_history`. Its source-neutral rows expose `close` as the current
raw contract mark, `pnl_close` as the roll-neutral additive level,
`signal_index` as the positive contract-return index, and `panama_price` as
the point-price signal level. The portfolio ledger marks positions with
changes in `pnl_close`, but executes, sizes, and records trades at `close`.
This prevents a roll gap from becoming P&L without hiding the actually traded
contract price.

The three rule classes are available through the existing backtester:

```text
--signal-weighting continuous    # repository return TSMOM
--signal-weighting goulding      # averaged monthly return horizons
--signal-weighting carver_ewmac  # Panama-price EMA difference / point vol
```

`carver_ewmac` requires a source-neutral data path and has explicit fast,
slow, volatility-span, forecast-scalar, and cap arguments. The legacy loader
remains the CLI default and rejects this rule because it does not preserve the
required distinction between raw marks and generated series.

#### Hybrid-source harness

`HybridHistoryProvider` is opt-in and accepts a historical provider, a primary
provider, an explicit instrument crosswalk, and an optional handoff date.  The
primary source owns the handoff session and every later session.  It rebuilds:

- the hybrid return index from daily contract returns, never by joining level
  values;
- the hybrid additive price from roll-neutral point changes, so a different
  vendor level base cannot create a false trend jump;
- marks and carry with the same date boundary; and
- per-row `source_segment` plus manifest metadata identifying both providers,
  instruments, and the handoff.

The backtester exposes these providers as `--data-source globex`,
`pysystemtrade`, or `hybrid`; `legacy_globex` remains the unchanged default.
All current crosswalk rows are still candidates, so Carver-backed research
requires the conspicuous `--allow-candidate-mappings` opt-in. Every run
manifest records database paths, mapping policy, resolved symbols, source
ranges, handoff, invalid-return counts, quality flags, and provider metadata.
Silver's configured pre-2022 previous-Globex-session alignment is applied
inside the historical provider before the hybrid boundary and is also stored
in that metadata.

A narrow, non-persisting comparison can now be run as follows (substitute
`goulding` or `carver_ewmac` to isolate the other rules):

```bash
.venv/bin/tsmom --symbols ES --years 2009-2011 \
  --data-source hybrid --allow-candidate-mappings \
  --signal-weighting continuous --disable-vix-gating --no-save
```

For the planned Carver/Globex deployment, Carver supplies the long history and
Globex is primary from an approved handoff within the overlap.  For live
extension, IB contributes new immutable raw contract observations to the same
builder.  A roll is accepted only when both old and new contract prices are
available at the decision point; the new differential is applied exactly once.
If late corrections alter historical raw observations, rebuild the complete
derived stream rather than mutating a previously adjusted series.

#### Zero and negative prices

Zero and negative futures prices are legitimate raw observations, not bad
ticks by definition.  CME explicitly required systems to support zero and
negative CL prices and stated that its trading, clearing, settlement, and
message formats support them ([CME Clearing Advisory 20-160](https://www.cmegroup.com/notices/clearing/2020/04/Chadv20-160.html)).
Consequently:

- raw marks, contract identity, point changes, Panama prices, EWMAC, and
  contract-dollar P&L retain nonpositive values;
- a percentage return is emitted only when both the reference and current
  price are strictly positive and the roll contract match is valid;
- invalid ratio rows receive `return_valid=false` and specific flags such as
  `nonpositive_reference`, `nonpositive_current`, or `unmatched_roll`;
- the positive index holds its last valid value only as an auditable state
  variable; the signal harness excludes the flagged observation from
  return-defined models, so the held level must not be interpreted as a zero
  economic return; and
- reports must disclose excluded dates.  Sensitivity runs may exclude the
  affected instrument/window or use EWMAC, but must not add an arbitrary
  offset, take an absolute value, clip the raw price, or manufacture a percent
  return.  Those operations change the economic meaning and can change the
  signal.

This is deliberately conservative.  A price relative crossing zero is not a
well-defined capital return, whereas futures P&L remains a point change times
the multiplier.  Carver's point-price EWMAC naturally survives this case; it
does not justify forcing the Goulding or repository return models through it.

#### Remaining validation gates

The first schema-v4 real-data reconstruction covered all 252 instruments.
Most generated EOD Panama values match the supplied adjusted CSV to floating-
point precision. Five isolated maxima need source-row review rather than a
blanket tolerance: RUSSELL `+0.70`, MILKWET `-0.23`, GICS `-0.20`, CHFJPY
`-0.105`, and CRUDE_W `-0.01`. Four daily return sessions were conservatively
masked because an invalid intraday transition occurred even though the final
EOD mark was positive: HANGENT_mini on 2023-07-17, MILKWET on 2018-07-23, and
MSCITAIWAN on 2023-06-21 and 2023-06-23. None is in the initial 14-market
overlap universe. The regenerated schema-v5 history report now shows materially
higher same-date return correlations for the intended mappings (for example,
Silver 0.813 after its configured session alignment), which confirms that EOD
returns must be derived from the fully chained intraday index rather than from
the final intraday row alone.

- Approve one handoff per market from the overlap rather than choosing it from
  backtest performance.
- Run single-symbol/year tests including CL around April 2020, then matched
  regime windows, before a full-history portfolio run.
- Review the five isolated Panama discrepancies and four conservatively
  masked return sessions listed above.
- Compare the default legacy Globex-only control to the opt-in source-neutral
  Globex path over matched windows before making the latter the default.

Exit criterion: the hybrid backtest is causal and reproducible, and the
Globex-only control is unchanged.

### Phase 5: carry prototype

- Reproduce pysystemtrade carry calculations on a handful of clean markets.
- Add carry quality masks and missing-observation semantics.
- Validate locally derived Globex carry against Carver during the overlap.
- Pre-register trend/carry blend weights and evaluation windows before broad
  testing.

Exit criterion: carry forecasts never use stale unmatched legs and every
forecast observation identifies its two contracts and annualization interval.

### Phase 6: wider universe

- Create an approved research universe distinct from the live universe.
- Validate currency conversion, point size, costs, calendars, and duplicated
  exposures.
- Add strategic instrument weighting/slow IDM experiments only after data
  coverage and covariance missingness are handled.

Exit criterion: no unverified Carver instrument can enter live routing or be
reported as execution-grade.

### Executable-contract cost and granularity diagnostic

`futures-cost-risk` is the executable-universe companion to the broad Carver
research panel. It resolves the dated IBKR contract that would actually be
traded and reports its current notional, Carver mixed volatility, daily and
annual dollar volatility per contract, configured
commission, live bid/ask width when available, and one-way/round-trip cost as
a fraction of annual dollar volatility.

The default volatility source is the full roll-neutral pysystemtrade history.
The shared core helper calculates the *Advanced Futures Trading* specification:
an `adjust=True` EWM standard deviation of daily point changes with span 32,
an `adjust=True` EWM mean of that fast volatility with span `10 × 256`, and
`mixed_vol = 0.7 × fast_vol + 0.3 × slow_vol`. The output exposes both
components, their blend, observation count, and effective history years. In
the current 252-instrument panel every history produces a mixed estimate, but
93 have fewer than ten years of fast-volatility observations and are labelled
accordingly rather than treated as fully warmed up.

Percentage volatility divides the mixed point volatility by the raw current-
contract price at the same history endpoint (`vol_reference_price`). It does
not divide stale historical point volatility by today's IB execution price;
the latter remains a separate notional input.

IB dated (`--vol-source dated`) and continuous (`--vol-source continuous`)
history remain explicit comparison modes. Continuous is never the default
because its adjustment and roll behavior disagrees with the repository's
Globex construction.

The bundled Carver-to-IB registry contains all 584 configured futures mappings
and covers all 252 usable histories. It retains Carver instrument code,
`IBSymbol`, exchange, broker currency, broker multiplier, price magnifier, and
weekly-expiry flag. The packaged file is pinned to upstream pysystemtrade
commit `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8`. Candidate mapping does not imply that the connected US
IBKR account has trading permission or market data: live qualification writes
that result separately. The effective IB point value
`IBMultiplier / priceMagnifier` matches imported `point_size` for all 584
mappings.

Eleven usable histories carry Carver's `IgnoreWeekly` flag. The generic live
resolver does not guess among their weekly/daily expiries; it retains their
offline cost/risk rows but marks live qualification unavailable until a
product-specific expiry filter is supplied.

The spread input is a point-in-time live quote on the actual micro/mini, not
Carver's full-size static spread coefficient. Missing bid/ask data and zero
Carver micro coefficients are treated as **unknown**, never as free execution;
spread-dependent total costs remain null. A live snapshot is useful for
current granularity screening but is not a historical slippage estimate.
Repeated snapshots or realized fills are still required to calibrate robust
micro slippage.

```bash
.venv/bin/futures-cost-risk \
  --instruments all-pysystemtrade \
  --offline \
  --vol-source pysystemtrade \
  --fast-vol-span 32 \
  --slow-vol-years 10 \
  --slow-vol-weight 0.30
```

`--offline` writes the complete 252-row comparison without connecting to IB.
Prices and volatility then end at the imported Carver history boundary, live
spreads remain null, and non-USD conversions use the latest FX observation in
that database (with `fx_asof` exposed). Omit `--offline` to qualify current IB
contracts and request current quotes; this is the step that tests real account
availability.

For an IB-connected audit, the command defaults to `--market-data-type auto`:
it tries real-time (`1`), then delayed (`3`), then delayed-frozen (`4`), and
stops at the first valid bid/ask. The CSV records both the successful mode and
the attempted sequence. Explicit `live`, `delayed`, and `delayed-frozen`
settings disable fallback. IB errors 354/10168 indicate quote entitlement,
not a failed futures contract
mapping; error 300 can be follow-on cancellation noise after a rejected quote
request.

Saved CSV reports round monetary notional, commission, cash-cost, and dollar-
volatility fields to two decimal places. Other floating-point diagnostics are
rounded to four decimal places; identifiers and observation counts are left
unchanged.

## Required tests and safeguards

- Importer refuses the Globex path as output and never opens source databases
  writable.
- Source file hashes, row counts, schemas, and date ranges are tested.
- Contract IDs round-trip as strings.
- Duplicate timestamps and zero/raw-price exceptions are surfaced.
- Return-defined signals use contract-consistent raw references; neither
  `pct_change(adjusted_price)` nor adjusted differences divided by current raw
  price is permitted.
- Generated Panama prices reproduce supplied Carver adjusted prices up to a
  constant/tolerance before the supplied series may serve as a validation
  oracle.
- Zero/negative raw prices survive import; percentage returns at invalid
  references are flagged and excluded, never coerced.
- Daily aggregation is deterministic and retains the original timestamp.
- Carry requires matched legs and valid, nonzero contract-month separation.
- Source/date quality masks are applied before signals, never after results
  are observed.
- Splicing uses only information available on or before each date.
- Micro/full-size histories map to one risk factor unless a test explicitly
  studies contract differences.
- Existing Globex-only backtests remain identical under the default source.
- Result manifests record database manifest/schema version, mapping version,
  source choice, and handoff date.

## Licensing and repository policy

The pysystemtrade repository is GPLv3, but its documentation separately notes
licensing restrictions around redistributing source-vendor market data. The
shipped multiple and adjusted files are transformed data, yet that distinction
should not be treated as blanket permission for redistribution or commercial
publication. Keep the source checkout, sidecar database, and derived extracts
private and out of Git. Publish code, schemas, aggregate QA statistics, and
reproducible methodology—not the data itself—unless the applicable rights are
confirmed.

The paid Globex data remains isolated under its own license and storage. No
Carver import should weaken or blur that boundary.

## Initial deliverable status

Phases 0-3 produced:

1. a rebuildable `pysystemtrade_reference.duckdb`;
2. manifest, raw tables, coverage tables, and the approved symbol map;
3. a read-only Carver loader that produces a positive signal index; and
4. an overlap report for ES, NQ, CL, GC, SI, ZN, ZT, ZC, ZL, ZS, ZW, 6J,
   and 6M.

Phase 4 now adds the generated-series and hybrid harness plus source-neutral
portfolio/CLI integration for repository return TSMOM, Goulding monthly, and
Carver EWMAC. Candidate-map approval and broader portfolio validation remain
behind the listed gates; no live routing is enabled by this work.

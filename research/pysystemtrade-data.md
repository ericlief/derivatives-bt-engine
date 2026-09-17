# Proposal: using pysystemtrade futures data with the Globex database

Research date: 2026-09-17. This is the design proposal and Phase 1
implementation record. The source data examined is the local checkout at
`/home/dev/projects/pysystemtrade/data/futures`; the existing market-data
source is `/home/dev/fin/db/globex_mdp_3.0.duckdb`.

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

The first validated build used source commit
`b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8` and produced:

| Build result | Value |
|---|---:|
| Sidecar size | 222,572,544 bytes |
| Manifest files | 803 |
| Hashed source bytes | 717,390,223 |
| Multiple-price rows | 7,249,183 |
| Adjusted-price rows | 7,248,713 |
| Instruments | 252 |
| Source range | 1969-12-02 23:00 to 2024-03-29 05:00 |

The materialized QA results reproduce the statistics in this document:
89.4% mean, 98.7% median, and 89.7% weighted carry-day coverage; 203
instruments at or above 90% and 26 below 50%; 40 adjusted series with
non-positive history; 152,992 null adjusted rows; and five non-positive raw
price rows. A second clean build was compared with `EXCEPT ALL` in both
directions across the manifest, every raw table, and every QA table. It had no
logical differences. Import time is intentionally the sole run-specific
metadata value.

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

Phase 2 now provides a source-neutral `FuturesHistory` object with independent
signal, mark, and carry frames. `PysystemtradeHistoryProvider` constructs the
signal stream as `delta(adjusted_price) / same-day current price` and compounds
that return into a strictly positive index. The negative Panama-adjusted level
is retained for audit but never enters percentage-return math. Marks retain the
raw selected-contract price and contract ID, while carry retains only
same-timestamp current/carry pairs. `GlobexHistoryProvider` builds its signal
return from each selected contract's own prior close, including across a
contract change, rather than treating a roll gap as market movement.

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
  thresholds for manual approval review: 0.908 daily-return correlation,
  0.973 trend correlation, and 96.9% contract-month agreement.
- US10/ZN is similarly strong (0.903 return and 0.983 trend correlation) but
  remains blocked on explicit Treasury fractional-price scale validation.
- Silver has a material session-date issue: its best correlation occurs with
  Globex shifted one calendar day, so no shift is applied automatically.
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
signal/P&L accounting device, not a price at which a contract traded.

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

For Carver data, construct a positive signal index from the two price objects:

```text
normalized_return[t] = (adjusted_price[t] - adjusted_price[t-1])
                       / current_contract_price[t]

signal_index[t] = signal_index[t-1] * (1 + normalized_return[t])
```

This follows pysystemtrade's own separation between the adjusted-price
difference numerator and its same-day, forward-filled current-contract
denominator. Existing TSMOM signal code can consume the positive
`signal_index`; position notional and pre-Globex approximate P&L continue to
use the raw current contract and the adjusted point change respectively.

### Carry coverage

Coverage below means a day with a valid same-timestamp `PRICE`, `CARRY`,
`PRICE_CONTRACT`, and `CARRY_CONTRACT`, divided by days with a valid current
price. This is stricter and more meaningful than merely forward-filling each
leg independently.

Across the full history:

- median instrument carry coverage is 98.7%;
- mean instrument coverage is 89.4%;
- weighted coverage across all instrument-days is 89.7%;
- 203 of 252 instruments have at least 90% coverage; and
- 26 instruments have less than 50% coverage.

The weakest complete-history series include:

| Instrument | Carry coverage |
|---|---:|
| FANG | 13.1% |
| US30 | 13.6% |
| SP400 | 16.1% |
| EU-BANKS | 18.2% |
| CAD_micro | 18.3% |
| EURO600 | 18.9% |
| US-INDUSTRY | 18.9% |
| EU-TRAVEL | 19.2% |
| DOW | 22.5% |
| DAX | 22.6% |

Much of this is old backfill sparsity: carry appears only around quarterly
roll windows, sometimes with 80-100 day gaps. Coverage improves materially in
the final years:

- weighted coverage is 96.5% in 2023 and 96.9% in 2024;
- over 2023 through March 2024, 222 instruments exceed 90% and 193 exceed 99%;
- in 2024 alone, 232 exceed 90% and 201 exceed 99%; and
- the four 2024 series below 50% are `BB3M` (4.9%), `JGB-SGX-mini` (28.1%),
  `OATIES` (34.4%), and `RICE` (47.5%).

`FORWARD` coverage is slightly worse over the full history: median 98.6%,
mean 86.6%, and weighted 83.0%.

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
  recent intraday observations. The importer should retain `source_timestamp`
  exactly and derive a separate, documented `trade_date` rather than assuming
  the strings are UTC.

## Initial symbol crosswalk

The following are high-confidence candidates for the first overlap study. A
micro traded contract should normally share the full-size signal history, as
the repository already does through `signal_symbol`; importing duplicate
micro histories into the covariance universe would double-count a market.

| Repo/Globex signal root | Carver series | Carver range | Carry coverage | Notes |
|---|---|---|---:|---|
| ES / MES | SP500 | 1982-09-14 to 2024-03-28 | 63.8% | Use main series for both signal roots |
| NQ / MNQ | NASDAQ | 1999-12-14 to 2024-03-28 | 96.5% | Direct price-scale match expected |
| CL / MCL | CRUDE_W | 1990-10-16 to 2024-03-28 | 98.3% | Underlying match only: Carver holds the annual December winter contract, not the monthly liquid front |
| GC / MGC | GOLD | 1975-04-01 to 2024-03-28 | 99.4% | `GOLD_micro` exists but should not be a second risk factor |
| SI / SIL | SILVER | 1970-06-15 to 2024-03-28 | 69.0% | Point size is 1,000; mapping/settlement date needs extra validation |
| ZN / MTN | US10 | 1982-08-30 to 2024-03-28 | 94.5% | Treasury fractional pricing needs exact scale checks |
| ZT | US2 | 2000-03-02 to 2024-03-28 | 99.4% | Direct candidate |
| ZC / MZC | CORN | 1972-10-18 to 2024-03-28 | 90.5% | Underlying match only: Carver holds annual December rather than the liquid front |
| ZL / MZL | SOYOIL | 1970-02-03 to 2024-03-28 | 92.5% | Underlying match; roll cycles differ materially |
| ZS / MZS | SOYBEAN | 1985-09-20 to 2024-03-28 | 99.5% | Underlying match only: Carver holds annual November rather than the liquid front |
| ZW / MZW | WHEAT | 1973-12-19 to 2024-03-28 | 96.2% | Underlying match only: Carver holds annual December rather than the liquid front |
| 6J / JPY / J7 | JPY | 1977-06-14 to 2024-03-28 | 98.5% | Same underlying, traded multipliers remain separate |
| 6M | MXP | 1995-09-15 to 2024-03-28 | 91.3% | Naming crosswalk required |
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

- `curated.daily_price`: daily last valid adjusted/current price, contract ID,
  source timestamps, and quality flags;
- `curated.signal_returns`: adjusted point difference, current-price
  denominator, normalized return, and positive signal index;
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

Introduce a source-neutral history object with three explicit streams:

1. **Signal stream**: `date`, positive `signal_index`, normalized return, source,
   and data-quality flags.
2. **Execution/mark stream**: date, raw selected-contract price, contract ID or
   expiration, multiplier/currency, and volume when available.
3. **Carry stream**: matched current/carry prices and contract IDs plus an
   annualized carry observation.

Keep `FuturesDataLoader` as the Globex implementation and add a separate
Carver provider. A later hybrid provider composes them. The default
`data_source=globex` path must remain behaviorally unchanged until the hybrid
path passes comparison tests.

Cache paths must include source and schema version, for example:

```text
.cache/futures/globex/v1/ES_daily.parquet
.cache/futures/pysystemtrade/v1/SP500_daily.parquet
.cache/futures/hybrid/v1/SP500_daily.parquet
```

The current asset-only cache names are unsafe once two sources can provide the
same market.

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

- compute signal returns from the adjusted/current-price pair;
- estimate P&L as `contracts * multiplier * delta(adjusted_price)`;
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
front-contract close. Splicing should operate on normalized returns or signal
indices, with a documented handoff date and overlap report.

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

- Introduce an opt-in `hybrid` data source.
- Use normalized returns/signal indices across the source boundary.
- Keep pre-Globex approximate P&L visibly labeled in reports.
- Run small single-symbol/year tests, then matched-universe regime windows,
  before a full-history portfolio run.
- Store source and quality metadata in every result manifest.

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

## Required tests and safeguards

- Importer refuses the Globex path as output and never opens source databases
  writable.
- Source file hashes, row counts, schemas, and date ranges are tested.
- Contract IDs round-trip as strings.
- Duplicate timestamps and zero/raw-price exceptions are surfaced.
- Adjusted-price returns use adjusted differences divided by current raw
  price; direct `pct_change(adjusted_price)` is prohibited.
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

## Proposed first deliverable

Implement only Phases 0-3 initially:

1. a rebuildable `pysystemtrade_reference.duckdb`;
2. manifest, raw tables, coverage tables, and the approved symbol map;
3. a read-only Carver loader that produces a positive signal index; and
4. an overlap report for ES, NQ, CL, GC, SI, ZN, ZT, ZC, ZL, ZS, ZW, 6J,
   and 6M.

That creates immediate research value and settles the hard alignment questions
before changing portfolio logic, adding carry forecasts, or expanding the live
instrument registry.

# Derivatives Backtesting Engine

A Polars-based research and backtesting package for futures trend systems and
options strategies.

## Features

- Support for both PUT and CALL options
- Long and short position management
- Early exit capabilities
- Delta and DTE-based trade selection
- Comprehensive trade analysis including:
  - Win rate
  - P&L tracking
  - Drawdown analysis
  - Sharpe ratio calculation
  - Return on margin metrics

## Runtime package structure

```
src/derivatives_bt_engine/
├── data/          # load, validate, cache, and persist market data
├── calculations/  # storage-independent financial calculations
├── backtest/      # shared historical position and result records
│   ├── options/   # legacy option event loop, contract selection, and pricing
│   └── futures/   # futures adapters and portfolio TSMOM simulation
├── strats/        # strategy definitions and command entry points
├── pipelines/     # Phase 1, Phase 2, and other multi-stage workflows
├── reports/       # public schemas, formatting, and report builders
├── integrations/  # optional external-service adapters such as Google Sheets
├── live/          # live portfolio and rebalance workflows
└── logging_config.py  # shared package file-logger configuration
```

The package intentionally has no generic `domain`, `core`, `engine`, or
`system` container, and no catch-all `utils` or `enums` module. Code is placed
by its concrete responsibility; strategy entry points may orchestrate the
reusable data, calculation, and backtest layers but should not duplicate their
financial logic.

## Futures instrument identities

Snapshot, screen, and selection reports use `symbol` as the unique executable
IB-facing key (for example, `MGC`). `pst_instr_code` is the separate
pysystemtrade history/configuration identity (for example, `GOLD_micro`), and
`signal_symbol` identifies the market series used to generate the signal (for
example, `GC`). Selection joins and notebook screen lookups key only on
`symbol`; the other fields preserve provenance and signal lineage.

The bundled IB mapping supplies the raw broker root by PST code. Where a raw
IB root is reused across products or venues, the explicit
`data/pysystemtrade_executable_symbols.csv` table assigns a unique execution
key and validates the expected broker root. Unambiguous roots continue to be
used directly. Thus execution identity is stable without overloading the PST
history identifier or assuming signal and execution symbols are the same.

## Futures selection: contract-size bands

Phase 2 estimates each selected instrument's **continuous maximum position**
in contracts: the position implied at the configured forecast cap, before
rounding to whole contracts. `SelectionConfig` defaults this position band to
`[2.5, 5.5)`, which corresponds to about three through five contracts when
rounded half-up. A position below 2.5 is “low”; 2.5 through less than 5.5 is
“band”; 5.5 or more is “high.” These are capacity/granularity categories, not
the number of contracts the portfolio holds every day.

For each trial book, the configured weight policy (by default,
`topdown_classif`) assigns a normalized structural risk weight `w_i` to each
instrument; the weights sum to one. That weight has three related uses in
`calculations/instrument_selection.py`:

1. It weights each instrument's net Sharpe in the book-score numerator and
   combines the trial book's correlations in the score denominator.
2. It contributes to the maximum-position estimate, together with capital,
   target volatility, the trial's IDM, the forecast-cap ratio, and the
   instrument's annual dollar volatility. Position sizing applies the
   `max_weight_idm` cap to `w_i * IDM` for this estimate:

   ```text
   max_position_i = capital * target_vol * min(w_i, max_weight_idm / IDM)
                    * IDM * forecast_cap_ratio / ann_dvol_i
   ```

3. The **uncapped structural weights** of instruments in each position band
   are summed into `low_con_weight`, `band_con_weight`, and
   `high_con_weight`. These fields therefore mean shares of total portfolio
   risk weight—not contract counts, margin shares, or P&L.

The optional near-tie rule uses a stable candidate reference position rather
than the changing weights of a partial greedy book. `--tie-ref-book-count`
sets the estimated final book size, with a default of 15. The reference gives
every candidate weight `1 / count`, applies the configured maximum IDM and
`max_weight_idm` cap, and calculates:

```text
ref_max_position_i = capital * target_vol
                     * min(max_idm / count, max_weight_idm)
                     * forecast_cap_ratio / ann_dvol_i
```

When `--score-tie-tolerance-pct` is greater than zero, Phase 2 first admits
candidates within that percentage of the iteration's best score. It then
chooses the candidate whose `ref_max_position` is inside or closest to the
configured contract band, breaking any remaining tie by score and symbol.
The reference position does not change as the selected book grows. The
default tolerance is zero, so this tie-break is off.

This candidate tie-break is separate from `--low-con-penalty-scale` and
`--high-con-penalty-scale`. Those optional settings subtract per-instrument
SR penalties when an instrument's continuous maximum position lies outside
the band; both also default to zero. The boundaries themselves can be changed
with `--con-band-min` and `--con-band-max` (defaults 2.5 and 5.5). The main
calculation is in `GreedyInstrumentSelector.score()`; `_choose_near_tie()` applies
the candidate reference-position comparison only after score eligibility is
established. Actual trial-book positions and low, band, and high risk-weight
masses remain in the reports as final-allocation diagnostics.

## Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install polars numpy
```

## Usage

### Basic Example

```python
from derivatives_bt_engine.bt import run_backtest, OptionsType, PositionSide

# Run a backtest for short puts
results = run_backtest(
    spx_file_path="path/to/spx_data.csv",
    options_chain_file_path="path/to/options_chain.csv",
    vix_file_path="path/to/vix_data.csv",
    option_type=OptionsType.PUT,
    position_side=PositionSide.SHORT,
    start_date="2020-01-01",
    end_date="2020-12-31",
    delta_range=(0.30, 0.35),
    dte_range=(28, 32),
    initial_capital=100000
)
```

### Parameters

- `option_type`: `OptionsType.PUT` or `OptionsType.CALL`
- `position_side`: `PositionSide.LONG` or `PositionSide.SHORT`
- `delta_target`: Single delta value for option selection
- `delta_range`: Tuple of (min_delta, max_delta)
- `dte_target`: Single DTE value for option selection
- `dte_range`: Tuple of (min_dte, max_dte)
- `early_close_days`: Number of days to hold before closing (None for expiration)
- `initial_capital`: Starting capital amount
- `use_preprocessed`: Whether to use preprocessed data files
- `save_preprocessed`: Whether to save preprocessed data for future use

## Data Requirements

The package expects the following data files, loaded as polars DataFrames with a plain `date` column (polars has no index concept):

1. SPX Price Data (CSV):
   - Columns: date, open, high, low, close

2. Options Chain Data (CSV):
   - Required columns: date, strike, expire_date, p_bid, p_ask, c_bid, c_ask, p_delta, c_delta, underlying_last
   - Optional columns: p_iv, c_iv, p_size, c_size

3. VIX Data (CSV):
   - Required columns: date, close

## Output

The backtest generates:

1. Trade Results (CSV):
   - Detailed trade information including entry/exit prices, P&L, and metrics
   - Saved in the `results/` directory

2. Mark-to-Market Data (CSV):
   - Daily portfolio value, drawdown, and ROI
   - Saved in the `results/` directory

3. Log Files:
   - Detailed execution logs
   - Saved in the `logs/` directory

## Performance Optimization

The package includes several memory optimization features:

- Pivoted options chain for faster lookups
- Reduced precision for numeric columns (float16)
- Caching of preprocessed data
- Efficient date handling and normalization

## License

[Your License Here]

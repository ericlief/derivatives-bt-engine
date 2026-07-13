# Options Backtesting Package

A Python package for backtesting options trading strategies, specifically designed for SPX options trading.

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

## Project Structure

```
options-bt/
├── options_bt/           # Source code directory
│   ├── __init__.py      # Package initialization
│   └── bt.py            # Main backtesting logic
├── logs/                 # Log files directory
├── results/             # Backtest results directory
└── README.md            # This file
```

## Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install polars numpy
```

## Usage

### Basic Example

```python
from options_bt.bt import run_backtest, OptionsType, PositionSide

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

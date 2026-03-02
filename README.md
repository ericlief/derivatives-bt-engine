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
.
├── options_bt/           # Source code directory
│   ├── __init__.py      # Package initialization
│   └── bt.py            # Main backtesting logic
├── __init__.py           # Root package initialization
├── requirements.txt      # Project dependencies
└── README.md            # This file
```

## Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Example

```python
from options_bt.bt import run_and_analyze_backtest, OptionType, PositionSide

# Run a backtest for short puts
results = run_and_analyze_backtest(
    data_dir="path/to/data_directory",
    option_type=OptionType.PUT,
    position_side=PositionSide.SHORT,
    start_date="2020-01-01",
    end_date="2020-12-31",
    delta_range=(0.30, 0.35),
    dte_range=(28, 32),
    initial_capital=100000
)
```

### Parameters

- `data_dir`: Path to directory containing the data files
- `option_type`: `OptionType.PUT` or `OptionType.CALL`
- `position_side`: `PositionSide.LONG` or `PositionSide.SHORT`
- `start_date`: Optional start date for filtering (e.g., "2020-01-01")
- `end_date`: Optional end date for filtering (e.g., "2020-12-31")
- `delta_target`: Single delta value for option selection
- `delta_range`: Tuple of (min_delta, max_delta)
- `dte_target`: Single DTE value for option selection
- `dte_range`: Tuple of (min_dte, max_dte)
- `initial_capital`: Starting capital amount (default: 100000)
- `early_close_days`: Number of days to hold before closing (None for expiration)
- `use_preprocessed`: Whether to use preprocessed data files (default: False)
- `save_preprocessed`: Whether to save preprocessed data for future use (default: False)
- `save_trades`: Whether to save trade results to CSV (default: True)

## Data Requirements

The package expects a data directory containing the following CSV files:

1. `spx.csv` (SPX Price Data):
   - Columns: date, open, high, low, close
   - Index: date

2. `options.csv` (Options Chain Data):
   - Required columns: strike, expire_date, p_bid, p_ask, c_bid, c_ask, p_delta, c_delta, underlying_last
   - Optional columns: p_iv, c_iv, p_size, c_size
   - Index: date

3. `vix.csv` (VIX Data):
   - Required column: close
   - Index: date

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
- Reduced precision for numeric columns
- Caching of preprocessed data
- Efficient date handling and normalization
- Dask-based data processing for memory efficiency

## License

[Your License Here]

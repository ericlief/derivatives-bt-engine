# Project conventions

## Data format: polars, not pandas

This project uses polars throughout (`pl.DataFrame`, `pl.col`, etc.) — `options_bt/domain/`, `options_bt/live/`, and `options_bt/domain/tsmom_backtester.py` are all polars-based. Don't introduce pandas as a general dependency or let it leak into data-handling code beyond a single, scoped conversion point.

When a third-party library requires pandas specifically (this comes up with portfolio-optimization libraries — e.g. `PyPortfolioOpt`'s `HRPOpt` expects a pandas DataFrame, dates as index, assets as columns):

- Convert with `.to_pandas()` (a native polars method) **only at that library's call site**, not earlier in the pipeline.
- Do the rest of the data wrangling, fetching, and any numpy-based math (e.g. `scipy.optimize`, which needs plain numpy arrays and has no pandas/polars dependency at all — convert with `.to_numpy()` instead) in polars/numpy as normal.
- Don't let a pandas conversion done for one library's sake become the ambient format for the rest of a script.

When writing implementation prompts or new scripts in this project, check what data format each proposed library actually expects before assuming polars works everywhere — verify, don't assume.

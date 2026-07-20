# Project conventions

## Data format: polars, not pandas

This project uses polars throughout (`pl.DataFrame`, `pl.col`, etc.) — `derivatives_bt_engine/domain/`, `derivatives_bt_engine/live/`, and `derivatives_bt_engine/domain/tsmom_backtester.py` are all polars-based. Don't introduce pandas as a general dependency or let it leak into data-handling code beyond a single, scoped conversion point.

When a third-party library requires pandas specifically (this comes up with portfolio-optimization libraries — e.g. `PyPortfolioOpt`'s `HRPOpt` expects a pandas DataFrame, dates as index, assets as columns):

- Convert with `.to_pandas()` (a native polars method) **only at that library's call site**, not earlier in the pipeline.
- Do the rest of the data wrangling, fetching, and any numpy-based math (e.g. `scipy.optimize`, which needs plain numpy arrays and has no pandas/polars dependency at all — convert with `.to_numpy()` instead) in polars/numpy as normal.
- Don't let a pandas conversion done for one library's sake become the ambient format for the rest of a script.

When writing implementation prompts or new scripts in this project, check what data format each proposed library actually expects before assuming polars works everywhere — verify, don't assume.

WHen running python commands always use `.venv/bin/python`

Always git commit. Always git push after committing.

## Constants and defaults

Tunable defaults and infrastructure constants (SQL strings, file paths, numeric thresholds) belong at the **top of the file** in a clearly labelled constants block — not inline in function signatures or buried near the function that happens to use them first. Function signatures reference the constant by name; the constant definition is the single place to change the value.

```python
# ── Tunable defaults ───────────────────────────────────────────────
DEFAULT_HALFLIFE        = 60.0
VOLUME_THRESHOLD_FACTOR = 0.1

# ── Infrastructure ─────────────────────────────────────────────────
_DEFAULT_DB_PATH = '/path/to/db'
_SOME_SQL = """..."""

def my_func(halflife: float = DEFAULT_HALFLIFE): ...
```

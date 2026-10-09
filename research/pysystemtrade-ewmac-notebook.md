# Pysystemtrade EWMAC notebook workflow

The reusable EWMAC research API loads one reviewed instrument without running
the futures-cost or Phase 2 selection commands. It uses the same versioned
history, forecast-panel, and pooled-scalar caches as the production backtester,
so the notebook is an inspection surface rather than an independent strategy
implementation.

## Load one instrument

```python
from derivatives_bt_engine.pipelines.pysystemtrade_ewmac import load_ewmac_research

corn = load_ewmac_research("CORN_mini")
corn.cache_status()
```

`corn.history` is the source-neutral `FuturesHistory` object. Its streams stay
separate because they have different financial meanings:

```python
corn.history.signal   # return index used by return-based signals
corn.history.panama   # additive point-price path used by EWMAC
corn.history.marks    # executable contract prices and roll identity
corn.history.carry    # same-timestamp current/carry contract observations
```

`history.metadata["cache_hit"]` reports whether the four history streams came
from their Parquet cache. `cache_status()` separately reports the full-universe
forecast-panel and causal pooled-scalar cache hits for every EWMAC speed.

## Display forecasts and their inputs

The default display table contains the Panama price, mixed point volatility,
and all five scaled and capped component forecasts:

```python
corn.forecast_table(start="2020-01-01").tail(30)
```

The underlying detailed frame also includes raw price momentum, the raw
volatility-normalized forecast, and the causal pooled scalar for every rule:

```python
corn.forecasts.select(
    "date",
    "close",
    "point_vol",
    "raw_ewmac_16_64",
    "raw_fcst_16_64",
    "scalar_16_64",
    "fcst_16_64",
).tail(30)
```

The names correspond to this calculation chain:

```text
Panama price
  -> fast EWMA - slow EWMA                  raw_ewmac_16_64
  -> divide by mixed point volatility      raw_fcst_16_64
  -> multiply by prior-data pooled scalar  scalar_16_64
  -> cap to [-1, +1]                       fcst_16_64
```

Inspect the global normalization itself in long form:

```python
corn.scalar_table("16/64", start="2020-01-01").tail(30)
```

## Plot

```python
fig, axes = corn.plot(
    start="2018-01-01",
    rules=["4/16", "16/64", "64/256"],
)
```

The upper panel is the additive Panama price and the lower panel contains the
selected component forecasts in the project's normalized `-1` to `+1` units.
The method returns the Matplotlib figure and axes so the notebook can customize
labels, limits, or output files.

## Combined Phase 2 forecasts

The notebook loader intentionally does not manufacture an FDM from one market.
Phase 2 estimates component-forecast correlations over its eligible history
universe and then combines only the rules that passed each executable
contract's cost gate. The exact combined forecasts, normalized positions, and
subsystem returns remain in
`pysystemtrade_phase2_forecasts_<timestamp>.parquet`; use the research object
to understand the component construction and that Parquet to audit the exact
portfolio-context combination.

# Pysystemtrade EWMAC notebook workflow

The reusable EWMAC research API loads one reviewed instrument without running
the market-screen or portfolio-selection commands. It uses the same versioned
history, forecast-panel, and pooled-scalar caches as the production backtester,
so the notebook is an inspection surface rather than an independent strategy
implementation.

## Load one instrument

```python
from derivatives_bt_engine.strats.multi_speed_ewmac import load_ewmac_research

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
  -> cap to [-2, +2]                       fcst_16_64
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
selected component forecasts in mean-absolute-one, `-2` to `+2` units.
The method returns the Matplotlib figure and axes so the notebook can customize
labels, limits, or output files.

## Build forecasts from a notebook dataframe

For a provider-neutral Polars dataframe such as an IB stock history, import
the reusable calculations directly. The input must contain one row per
``date`` and a ``close`` column; ``point_change`` is optional and is calculated
from the sorted close when absent. Equity closes should already be adjusted
for splits.

```python
from derivatives_bt_engine.calculations.ewmac_components import (
    build_ewmac_rule_component,
    build_ewmac_components,
)
from derivatives_bt_engine.calculations.forecast_combination import (
    combine_ewmac_forecasts,
)

result = combine_ewmac_forecasts(
    xar,                       # date, adjusted close, optional point_change
    instrument_code="XAR",
    fdm=1.0,                   # no single-instrument diversification uplift
)

forecasts = result.frame
forecasts
```

``result.frame`` retains the input columns, mixed-volatility diagnostics, all
five rule-component calculations, the combined forecast, and turnover audit
columns. ``result.weights``, ``result.fdm``, ``result.turnover``, and
``result.audit`` expose the corresponding summary values. Use
``build_ewmac_components`` when only the wide component frame is needed, or
``build_ewmac_rule_component`` to inspect a single speed.

## Plot the combined forecast, positions, and P&L

Wrap the combined result in the notebook view instead of rebuilding P&L and
plotting each column in separate cells. The normalized subsystem curve is
always produced. Executable contracts and cash P&L are produced only when the
actual contract multiplier and explicit account settings are supplied:

```python
from derivatives_bt_engine.pipelines.ewmac_notebook import (
    ExecutableCashSettings,
    build_ewmac_notebook_view,
)

view = build_ewmac_notebook_view(
    result,
    instrument_code="ES",
    multiplier=50.0,
    fx_to_usd=1.0,
    ann_cost_sr=0.0,
    cash_settings=ExecutableCashSettings(
        capital=75_000,
        target_vol=0.20,
        weight=1.0,
        idm=1.0,
        trade_cost_usd_per_con=1.00,
        roll_cost_usd_per_con=0.0,
    ),
)

view.plot(start="2021-01-01")
```

The linked hvPlot dashboard contains adjusted signal price, combined forecast,
raw and smoothed position proxies, normalized position, cumulative subsystem
P&L, buffered whole contracts, and cumulative executable cash P&L. The
underlying Polars frames remain available for detailed inspection:

```python
view.forecasts
view.forecast_positions(start="2021-01-01")
view.pnl
view.subsystem_pnl_curves()
view.cash
view.cash_pnl_curves()
```

`view.pnl` is intentionally a relative-risk path used for subsystem
correlations; it is not an account equity curve. `view.cash` uses the supplied
capital, risk target, weight, IDM, multiplier, buffering, and contract costs,
so it is the executable account-scale diagnostic. Passing `multiplier=1` for
ES is therefore appropriate only for a unit test, not an ES cash backtest.

## Combined portfolio-selection forecasts

The notebook loader intentionally does not manufacture an FDM from one market.
Portfolio selection estimates component-forecast correlations over eligible history
universe and then combines only the rules that passed each executable
contract's cost gate. The exact combined forecasts, normalized positions, and
subsystem returns remain in
`multi_speed_ewmac_selection_forecasts_<timestamp>.parquet`; use the research object
to understand the component construction and that Parquet to audit the exact
portfolio-context combination.

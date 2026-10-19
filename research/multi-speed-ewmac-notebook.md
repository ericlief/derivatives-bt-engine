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
five rule-component calculations, and the combined forecast. ``result.weights``,
``result.fdm``, and ``result.audit`` expose the corresponding summary values.
Reference sizing and turnover are added after multiplier and FX are known. Use
``build_ewmac_components`` when only the wide component frame is needed, or
``build_ewmac_rule_component`` to inspect a single speed.

For a direct inspection of the equal-weight subsystem-reference return curve,
use the same explicit reference assumptions as the selection pipeline:

```python
from derivatives_bt_engine.calculations.pnl import (
    append_reference_position_turnover,
    build_subsystem_reference_return_curve,
)

reference = build_subsystem_reference_return_curve(
    result.frame.with_columns(pl.lit(1.0).alias("fx_to_usd")),
    capital=75_000,
    target_vol=0.20,
    reference_book_count=15,
    reference_idm=2.5,
    max_weight_idm=0.20,
    multiplier=50.0,
    ann_cost_sr=0.0,
)
reference, reference_turnover = append_reference_position_turnover(reference)

reference.head(600)
```

Set ``multiplier`` and ``fx_to_usd`` to the instrument's actual contract and
currency values. The returned ``reference_net_return`` is a continuous,
allocation-capital-normalized subsystem series for cross-instrument comparison,
and ``reference_turnover`` is measured from its target and smoothed average
positions. ``reference_return_capital_usd`` records the denominator implied by
the risk budget. Use ``build_executable_cash_pnl_curve`` after final portfolio
weights are known.

## Plot the combined forecast, positions, and P&L

Wrap the combined result in the notebook view instead of rebuilding sizing and
returns in separate cells. The equal-weight subsystem-reference curve is
always produced from explicit reference settings. Executable contracts and
cash P&L are produced only when final portfolio settings are also supplied:

```python
from derivatives_bt_engine.pipelines.ewmac_notebook import (
    ExecutableCashSettings,
    SubsystemReferenceSettings,
    build_ewmac_notebook_view,
)

view = build_ewmac_notebook_view(
    result,
    instrument_code="ES",
    multiplier=50.0,
    fx_to_usd=1.0,
    ann_cost_sr=0.0,
    reference_settings=SubsystemReferenceSettings(
        capital=75_000,
        target_vol=0.20,
        reference_book_count=15,
        reference_idm=2.5,
        max_weight_idm=0.20,
    ),
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
sized and smoothed reference positions, normalized reference position,
cumulative subsystem-reference return, buffered whole contracts, and
cumulative executable cash P&L. The underlying Polars frames and annualized
reference turnover remain available for detailed inspection:

```python
view.forecasts
view.forecast_positions(start="2021-01-01")
view.reference
view.reference_turnover
view.subsystem_reference_accounting()
view.subsystem_reference_return_curves()
view.cash
view.cash_pnl_curves()
view.cash_return_contribution_curves()
```

`view.reference` assigns the instrument `1 / reference_book_count`, caps the
weight-times-IDM product, sizes continuous positions from daily dollar
volatility, and divides the resulting P&L by the allocation capital implied by
the reference risk budget. It is the normalized candidate subsystem return
used for correlations. `view.subsystem_reference_accounting()` groups that
allocation capital and its annual and daily risk budgets with gross, cost, and
net reference P&L and the matching returns. `view.cash` uses final portfolio
weight and IDM, buffering, whole contracts, and per-contract costs, so it is
the executable account-scale diagnostic.
`view.cash_return_contribution_curves()` divides its gross, realized-net, and
expected-net cash P&L by the common account capital; the daily contributions
add across selected instruments to the portfolio return. Passing
`multiplier=1` for ES is appropriate only for a unit test.

## Combined portfolio-selection forecasts

The notebook loader intentionally does not manufacture an FDM from one market.
Portfolio selection estimates component-forecast correlations over eligible history
universe and then combines only the rules that passed each executable
contract's cost gate. The exact combined forecasts, normalized positions, and
subsystem returns remain in
`multi_speed_ewmac_selection_forecasts_<timestamp>.parquet`; use the research object
to understand the component construction and that Parquet to audit the exact
portfolio-context combination.

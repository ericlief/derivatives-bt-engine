"""Notebook diagnostics for EWMAC reference percentages and portfolio cash P&L.

This module is the interactive inspection boundary after forecast combination.
It accepts the same :class:`CombinedForecastResult` used by Phase 2, adds the
equal-weight subsystem-reference percentage and optional executable cash P&L
paths, and exposes small Polars tables plus an hvPlot dashboard. A helper can
join a saved Phase 1 selection screen for contract and cost inputs. It does not
reimplement forecast, sizing, buffering, or cost calculations; those remain
owned by the calculation modules used by production selection. The stitched
history entry point also orchestrates dated-contract loading, history
ownership, pooled forecast construction, and this same view boundary. The
selected-book cash-P&L plotting helper plots Polars slices directly and lays
out comparable execution diagnostics without merging their scales. The
scheduled-selection plotting helper reads the compact review-instrument report
and exposes each dated book as instrument-weight bars plus class and subclass
composition bars. Portfolio-level helpers plot compounded equity and drawdown,
realized contract-risk concentration, and annual instrument P&L attribution
from the persisted public reports without recreating the backtest accounting
path. The selection-grid dashboard applies those same plotters to one scenario
at a time and adds scenario and coverage navigation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl
from ib_tools.ibpysync import IBPySync

from derivatives_bt_engine.calculations.ewmac import (
    CANONICAL_EWMAC_RULES,
    EWMAC_FORECAST_TARGET_ABS,
    EwmacRule,
)
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastEngine,
    CombinedForecastResult,
    FixedForecastDiversification,
    ForecastCombinationConfig,
    combine_ewmac_forecasts,
)
from derivatives_bt_engine.calculations.costs import calculate_annual_sr_cost
from derivatives_bt_engine.calculations.pnl import (
    PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
    append_reference_position_turnover,
    build_executable_cash_pnl_curve,
    build_subsystem_reference_return_curve,
    summarize_return_significance,
)
from derivatives_bt_engine.data.fx_history import align_fx_to_history
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH as DEFAULT_REFERENCE_DB_PATH,
    FuturesHistory,
)
from derivatives_bt_engine.data.ib_futures_dataloader import (
    DEFAULT_BROKER_MAPPING_PATH,
    DEFAULT_IB_BAR_SIZE,
    DEFAULT_IB_DURATION,
    DEFAULT_IB_DATED_CONTRACT_DURATION,
    DEFAULT_MAX_SOURCE_GAP_DAYS,
    IBStitchedHistoryProvider,
)
from derivatives_bt_engine.data.ib_futures_history_store import (
    DEFAULT_IB_FUTURES_HISTORY_DB_PATH,
)
from derivatives_bt_engine.data.pysystemtrade_ib import (
    load_pysystemtrade_ib_instruments,
)
from derivatives_bt_engine.data.pysystemtrade_pooling import (
    DEFAULT_POOLING_MAPPING_PATH,
)
from derivatives_bt_engine.reports.formatting import rename_public_report_columns
from derivatives_bt_engine.strats.multi_speed_ewmac import (
    build_ewmac_component_frame,
    load_pooled_ewmac_rules,
)


def _coerce_date(value: date | datetime | str | None) -> date | None:
    """Normalize an optional notebook boundary to a calendar date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_slice(
    frame: pl.DataFrame,
    *,
    start: date | datetime | str | None,
    end: date | datetime | str | None,
) -> pl.DataFrame:
    """Return an inclusive date slice without mutating the full audit frame."""
    start_date = _coerce_date(start)
    end_date = _coerce_date(end)
    result = frame
    if start_date is not None:
        result = result.filter(pl.col("date") >= start_date)
    if end_date is not None:
        result = result.filter(pl.col("date") <= end_date)
    return result


def _read_notebook_report(
    report: pl.DataFrame | Path | str,
    *,
    label: str,
) -> pl.DataFrame:
    """Load one public notebook report without changing its persisted schema."""
    if isinstance(report, (str, Path)):
        frame = pl.read_parquet(Path(report).expanduser())
    else:
        frame = report
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(f"{label} must be a Polars DataFrame or Parquet path")
    return frame


def _require_report_columns(
    frame: pl.DataFrame,
    required: set[str],
    *,
    label: str,
) -> None:
    """Raise a readable schema error before constructing an interactive plot."""
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def plot_selection_cash_pnl(
    cash_pnl: pl.DataFrame | Path | str,
    *,
    symbols: Iterable[str] | None = None,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    width: int = 1050,
    height: int = 220,
):
    """Plot selected-book cash P&L with a widget for choosing the symbol.

    ``cash_pnl`` may be the public Polars frame or a saved selection cash-P&L
    Parquet path. By default, every symbol appears in the selector. Choosing a
    symbol updates its vertically stacked forecast, contract-position, daily
    P&L, separate daily-cost, cumulative P&L, cumulative-return, and a shared
    mark/Panama-price panel.
    The wider panels
    have independent scales and support hover and zoom.

    Parameters
    ----------
    cash_pnl
        Selected-book cash-P&L frame or its Parquet path. The public report
        schema must include ``symbol``, ``date``, ``comb_fcst``, ``tgt_pos``,
        ``gross_pnl``, and ``net_pnl``.
    symbols
        Optional symbols to include, in the requested order. Unknown symbols
        raise ``KeyError`` rather than silently producing an empty dashboard.
    start, end
        Optional inclusive date bounds shared by every symbol panel.
    width, height
        Width and height of each subplot in pixels.

    Returns
    -------
    ipywidgets.VBox
        A symbol dropdown and output area. Selecting a symbol redraws five
        independently scaled plots for that symbol.
    """
    import holoviews as hv
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API
    import ipywidgets as widgets
    from IPython.display import display

    cash_path: Path | None = None
    if isinstance(cash_pnl, (str, Path)):
        cash_path = Path(cash_pnl).expanduser()
        frame = pl.read_parquet(cash_path)
    else:
        frame = cash_pnl
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("cash_pnl must be a Polars DataFrame or Parquet path")

    mark_column = next(
        (
            column
            for column in ("con_px", "contract_close", "mark_price")
            if column in frame.columns
        ),
        None,
    )
    if mark_column is None:
        source = str(cash_path) if cash_path is not None else "the supplied frame"
        raise ValueError(
            f"{source} has no contract mark price ('con_px'). Regenerate the "
            "selection cash report so its mark comes from the same stitched "
            "history as its cash P&L."
        )
    required = {"symbol", "date", "comb_fcst", "tgt_pos", "gross_pnl", "net_pnl"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            "selection cash-P&L frame is missing required columns: "
            f"{missing}"
        )
    available_symbols = set(frame.get_column("symbol").drop_nulls().unique().to_list())
    selected_symbols = (
        list(symbols)
        if symbols is not None
        else sorted(available_symbols)
    )
    if not selected_symbols:
        raise ValueError("selection cash-P&L frame contains no symbols")
    unknown = [symbol for symbol in selected_symbols if symbol not in available_symbols]
    if unknown:
        raise KeyError(f"symbols are not present in cash-P&L frame: {unknown}")

    symbol_frames = {}
    for symbol in selected_symbols:
        symbol_frame = _date_slice(
            frame.filter(pl.col("symbol") == symbol),
            start=start,
            end=end,
        ).sort("date")
        if symbol_frame.is_empty():
            raise ValueError(
                f"no cash-P&L observations for {symbol} in the requested date range"
            )
        symbol_frames[symbol] = symbol_frame

    def line_panel(
        symbol_frame: pl.DataFrame,
        *,
        columns: list[str],
        title: str,
        ylabel: str,
        labels: dict[str, str] | None = None,
        colors: dict[str, str] | None = None,
    ):
        """Build one independently scaled overlay from available fields."""
        available = [name for name in columns if name in symbol_frame.columns]
        tools = ["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"]
        curves = [
            symbol_frame.hvplot.line(
                x="date",
                y=column,
                label=(labels or {}).get(column, column),
                title=title,
                ylabel=ylabel,
                width=width,
                height=height,
                tools=tools,
                hover_tooltips=[
                    ("Date", "@date{%F}"),
                    ((labels or {}).get(column, column), f"@{column}{{0.0000}}"),
                ],
                **(
                    {"color": colors[column]}
                    if colors is not None and column in colors
                    else {}
                ),
            )
            for column in available
        ]
        overlay = hv.Overlay(curves)
        return overlay.opts(legend_position="top_left")

    def plot_symbol(symbol: str):
        """Build all independently scaled panels for one selected symbol."""
        symbol_frame = symbol_frames[symbol]
        panels = [
            line_panel(
                symbol_frame,
                columns=["comb_fcst"],
                title=f"{symbol} combined forecast",
                ylabel="Forecast",
            ) * hv.HLine(0.0).opts(color="black", line_width=1),
            line_panel(
                symbol_frame,
                columns=["vol_scalar", "tgt_pos", "held_con"],
                title=f"{symbol} vol scalar, target, and held contracts",
                ylabel="Contracts",
            ),
            line_panel(
                symbol_frame,
                columns=["gross_pnl", "net_pnl"],
                title=f"{symbol} daily P&L",
                ylabel="USD",
            ) * hv.HLine(0.0).opts(color="black", line_width=1),
            line_panel(
                symbol_frame,
                columns=["trade_cost", "roll_cost"],
                title=f"{symbol} daily costs",
                ylabel="USD",
            ) * hv.HLine(0.0).opts(color="black", line_width=1),
            line_panel(
                symbol_frame,
                columns=["cum_gross_pnl", "cum_net_pnl"],
                title=f"{symbol} cumulative P&L",
                ylabel="USD",
            ),
            line_panel(
                symbol_frame,
                columns=["cum_gross_pct", "cum_net_pct"],
                title=f"{symbol} cumulative return contribution",
                ylabel="Return contribution",
            ),
        ]
        price_panel = (
            line_panel(
                symbol_frame,
                columns=[mark_column, "close"],
                title=f"{symbol} contract mark and Panama prices",
                ylabel="Price points",
                labels={
                    mark_column: "Mark price",
                    "close": "Panama price",
                },
                colors={
                    mark_column: "#1f77b4",
                    "close": "#ff7f0e",
                },
            )
            * hv.HLine(0.0).opts(color="black", line_width=1)
        )
        panels.append(price_panel)
        dashboard = panels[0]
        for panel in panels[1:]:
            dashboard = dashboard + panel
        return dashboard.cols(1).opts(shared_axes=False)

    selector = widgets.Dropdown(
        description="Symbol",
        options=selected_symbols,
        value=selected_symbols[0],
    )
    plot_output = widgets.Output(
        layout=widgets.Layout(
            width="100%",
            height="auto",
            max_height="none",
            overflow="visible",
        )
    )

    def update_plot(change=None):
        """Redraw the selected symbol inside the ipywidgets output area."""
        symbol = change["new"] if change is not None else selector.value
        with plot_output:
            plot_output.clear_output(wait=True)
            display(plot_symbol(symbol))

    selector.observe(update_plot, names="value")
    update_plot()
    return widgets.VBox(
        [selector, plot_output],
        layout=widgets.Layout(width="100%", overflow="visible"),
    )


def plot_selection_portfolio_overview(
    portfolio_curve: pl.DataFrame | Path | str,
    *,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    rolling_days: int = 256,
    width: int = 1050,
    height: int = 240,
):
    """Plot compounded selection equity, drawdown, return, and realized vol.

    The equity series are read directly from the persisted portfolio curve.
    Rolling return is the net-equity change over ``rolling_days`` report rows;
    rolling percentage volatility is the annualized standard deviation of the
    report's prior-net-equity daily return, and rolling dollar volatility is
    the annualized standard deviation of daily net P&L. Invalid warm-up rows
    remain visible in the equity panel but do not enter the rolling statistics.

    Parameters
    ----------
    portfolio_curve
        Public selection portfolio-curve frame or its Parquet path. It must
        contain ``date``, gross and net equity, daily net return, and
        ``ret_valid``.
    start, end
        Optional inclusive date bounds applied after rolling fields are
        calculated, preserving their prior-history warm-up.
    rolling_days
        Trading observations used for trailing net return and realized
        volatility. The default 256 matches the selection annualization.
    width, height
        Width and height of each vertically stacked panel in pixels.

    Returns
    -------
    holoviews.core.layout.Layout
        Five independently scaled panels for equity, drawdown, trailing return,
        annualized percentage-return volatility, and annualized dollar volatility.
    """
    import holoviews as hv
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API

    if rolling_days < 2:
        raise ValueError("rolling_days must be at least 2")
    frame = _read_notebook_report(portfolio_curve, label="portfolio_curve")
    _require_report_columns(
        frame,
        {
            "date",
            "port_gross_equity",
            "port_net_equity",
            "port_net",
            "port_net_pct",
            "ret_valid",
        },
        label="selection portfolio curve",
    )
    frame = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    if frame.is_empty():
        raise ValueError("selection portfolio curve is empty")

    # Calculate trailing statistics before slicing so a recent view retains
    # the observations immediately before its displayed start date.
    frame = frame.with_columns(
        pl.col("port_net_equity").cum_max().alias("running_net_peak"),
        pl.when(pl.col("ret_valid").fill_null(False))
        .then(pl.col("port_net_pct"))
        .otherwise(None)
        .alias("valid_net_pct"),
        pl.when(pl.col("ret_valid").fill_null(False))
        .then(pl.col("port_net"))
        .otherwise(None)
        .alias("valid_net_pnl"),
    ).with_columns(
        (
            (pl.col("port_net_equity") / pl.col("running_net_peak") - 1.0)
            * 100.0
        ).alias("net_drawdown_pct"),
        (
            (
                pl.col("port_net_equity")
                / pl.col("port_net_equity").shift(rolling_days)
                - 1.0
            )
            * 100.0
        ).alias("rolling_return_pct"),
        (
            pl.col("valid_net_pct").rolling_std(window_size=rolling_days)
            * math.sqrt(256.0)
        ).alias("rolling_realized_vol_pct"),
        (
            pl.col("valid_net_pnl").rolling_std(window_size=rolling_days)
            * math.sqrt(256.0)
        ).alias("rolling_realized_dvol"),
    )
    frame = _date_slice(frame, start=start, end=end)
    if frame.is_empty():
        raise ValueError("no portfolio observations in the requested date range")

    tools = ["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"]
    positive_equity = bool(
        frame.select(
            (pl.min_horizontal("port_gross_equity", "port_net_equity") > 0)
            .all()
            .alias("positive")
        ).item()
    )
    equity = frame.hvplot.line(
        x="date",
        y=["port_gross_equity", "port_net_equity"],
        title="Selection portfolio gross and net equity",
        ylabel="Equity (USD)",
        logy=positive_equity,
        width=width,
        height=height,
        tools=tools,
        hover_cols=["port_gross_equity", "port_net_equity"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Gross equity", "@port_gross_equity{$0,0.00}"),
            ("Net equity", "@port_net_equity{$0,0.00}"),
        ],
    ).opts(legend_position="top_left")
    drawdown_area = frame.hvplot.area(
        x="date",
        y="net_drawdown_pct",
        title="Net-equity drawdown from running peak",
        ylabel="Drawdown (%)",
        color="#d62728",
        alpha=0.45,
        width=width,
        height=height,
        hover=False,
        tools=["pan", "wheel_zoom", "box_zoom", "reset", "save"],
    )
    # An area is rendered as one polygon, which cannot expose the nearest
    # dated drawdown value to Bokeh's hover tool. The line supplies one point
    # per date while the area retains the visual drawdown fill.
    drawdown_line = frame.hvplot.line(
        x="date",
        y="net_drawdown_pct",
        color="#d62728",
        line_width=1.5,
        width=width,
        height=height,
        tools=tools,
        hover_cols=["net_drawdown_pct"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Drawdown", "@net_drawdown_pct{0.00}%"),
        ],
    )
    drawdown = (
        drawdown_area
        * drawdown_line
        * hv.HLine(0.0).opts(color="black", line_width=1)
    )
    trailing_return = frame.hvplot.line(
        x="date",
        y="rolling_return_pct",
        title=f"Trailing {rolling_days}-observation net return",
        ylabel="Return (%)",
        width=width,
        height=height,
        tools=tools,
        hover_cols=["rolling_return_pct"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Trailing return", "@rolling_return_pct{0.00}%"),
        ],
    )
    trailing_volatility = frame.hvplot.line(
        x="date",
        y="rolling_realized_vol_pct",
        title=(
            f"Trailing {rolling_days}-observation annualized return volatility"
        ),
        ylabel="Annualized return volatility (%)",
        width=width,
        height=height,
        tools=tools,
        hover_cols=["rolling_realized_vol_pct"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Annualized return vol", "@rolling_realized_vol_pct{0.00}%"),
        ],
    )
    trailing_dollar_volatility = frame.hvplot.line(
        x="date",
        y="rolling_realized_dvol",
        title=f"Trailing {rolling_days}-observation annualized dollar volatility",
        ylabel="Annualized dollar volatility (USD)",
        width=width,
        height=height,
        tools=tools,
        hover_cols=["rolling_realized_dvol"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Annualized dollar vol", "@rolling_realized_dvol{$0,0.00}"),
        ],
    )
    return (
        equity
        + drawdown
        + trailing_return
        + trailing_volatility
        + trailing_dollar_volatility
    ).cols(1).opts(shared_axes=False)


def plot_selection_portfolio_concentration(
    cash_pnl: pl.DataFrame | Path | str,
    *,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    width: int = 1050,
    height: int = 220,
):
    """Plot realized whole-contract risk concentration through time.

    Absolute annualized risk for an instrument is ``abs(held_con) × ann_dvol``.
    The first panel divides its cross-instrument sum by ``sizing_cap``, the
    prior-day net equity actually used for position sizing. The second reports
    top-one and top-three shares of that absolute contract risk, while the
    third counts instruments with nonzero holdings. These are realized book
    diagnostics after forecasting, integer rounding, buffering, and capital
    scaling; they are not the fixed selection weights.

    Parameters
    ----------
    cash_pnl
        Public selection cash-P&L frame or its Parquet path. It must contain
        ``date``, ``symbol``, ``held_con``, ``ann_dvol``, and ``sizing_cap``.
    start, end
        Optional inclusive date bounds.
    width, height
        Width and height of each vertically stacked panel in pixels.

    Returns
    -------
    holoviews.core.layout.Layout
        Three independently scaled panels for gross risk, concentration
        shares, and the nonzero held-instrument count.
    """
    import holoviews as hv
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API

    frame = _read_notebook_report(cash_pnl, label="cash_pnl")
    _require_report_columns(
        frame,
        {"date", "symbol", "held_con", "ann_dvol", "sizing_cap"},
        label="selection cash-P&L frame",
    )
    frame = frame.with_columns(
        pl.col("date").cast(pl.Date),
        (
            pl.col("held_con").fill_null(0).abs()
            * pl.col("ann_dvol").fill_null(0.0)
        ).alias("abs_ann_risk"),
    )
    frame = _date_slice(frame, start=start, end=end)
    if frame.is_empty():
        raise ValueError("no cash-P&L observations in the requested date range")

    daily = (
        frame.group_by("date")
        .agg(
            pl.col("abs_ann_risk").sum().alias("gross_ann_risk"),
            pl.col("abs_ann_risk").max().alias("top1_ann_risk"),
            pl.col("abs_ann_risk")
            .sort(descending=True)
            .head(3)
            .sum()
            .alias("top3_ann_risk"),
            (pl.col("abs_ann_risk") > 0).sum().alias("held_n_instr"),
            pl.col("sizing_cap").drop_nulls().max().alias("sizing_cap"),
        )
        .sort("date")
        .with_columns(
            pl.when(pl.col("sizing_cap") > 0)
            .then(pl.col("gross_ann_risk") / pl.col("sizing_cap") * 100.0)
            .otherwise(None)
            .alias("gross_ann_risk_pct"),
            pl.when(pl.col("gross_ann_risk") > 0)
            .then(pl.col("top1_ann_risk") / pl.col("gross_ann_risk") * 100.0)
            .otherwise(None)
            .alias("top1_risk_share_pct"),
            pl.when(pl.col("gross_ann_risk") > 0)
            .then(pl.col("top3_ann_risk") / pl.col("gross_ann_risk") * 100.0)
            .otherwise(None)
            .alias("top3_risk_share_pct"),
        )
    )
    tools = ["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"]
    gross_risk = daily.hvplot.line(
        x="date",
        y="gross_ann_risk_pct",
        title="Gross annualized whole-contract risk relative to sizing capital",
        ylabel="Gross annual risk / prior net equity (%)",
        color="#1f77b4",
        width=width,
        height=height,
        tools=tools,
        hover_cols=["gross_ann_risk", "sizing_cap"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Gross risk / capital", "@gross_ann_risk_pct{0.00}%"),
            ("Gross annual risk", "@gross_ann_risk{$0,0.00}"),
            ("Sizing capital", "@sizing_cap{$0,0.00}"),
        ],
    )
    shares = daily.hvplot.line(
        x="date",
        y=["top1_risk_share_pct", "top3_risk_share_pct"],
        title="Largest realized instrument-risk shares",
        ylabel="Share of gross annual risk (%)",
        width=width,
        height=height,
        tools=tools,
        hover_cols=["top1_risk_share_pct", "top3_risk_share_pct"],
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Top one", "@top1_risk_share_pct{0.00}%"),
            ("Top three", "@top3_risk_share_pct{0.00}%"),
        ],
    ).opts(legend_position="top_left")
    held_count = daily.hvplot.step(
        x="date",
        y="held_n_instr",
        where="post",
        title="Instruments with nonzero held contracts",
        ylabel="Held instruments",
        color="#9467bd",
        width=width,
        height=height,
        tools=tools,
        hover_tooltips=[
            ("Date", "@date{%F}"),
            ("Held instruments", "@held_n_instr{0}"),
        ],
    )
    return (gross_risk + shares + held_count).cols(1).opts(shared_axes=False)


def plot_selection_pnl_attribution(
    cash_pnl: pl.DataFrame | Path | str,
    *,
    width: int = 1050,
    height: int = 440,
):
    """Plot annual instrument net P&L with separate gross and cost hover data.

    Parameters
    ----------
    cash_pnl
        Public selection cash-P&L frame or its Parquet path. The report must
        include instrument-level gross and net P&L plus trade and roll costs.
    width, height
        Width and height of the vertical bar chart in pixels.

    Returns
    -------
    ipywidgets.VBox
        A year selector, defaulting to the latest year, and instrument net-P&L
        bars for the selected year. Hover retains gross P&L and both realized
        cost streams.
    """
    import holoviews as hv
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API
    import ipywidgets as widgets
    from IPython.display import display

    frame = _read_notebook_report(cash_pnl, label="cash_pnl")
    _require_report_columns(
        frame,
        {
            "date",
            "symbol",
            "gross_pnl",
            "net_pnl",
            "trade_cost",
            "roll_cost",
        },
        label="selection cash-P&L frame",
    )
    frame = frame.with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("date").cast(pl.Date).dt.year().alias("year"),
    )
    nonzero = frame.filter(
        (pl.col("gross_pnl").fill_null(0.0) != 0)
        | (pl.col("trade_cost").fill_null(0.0) != 0)
        | (pl.col("roll_cost").fill_null(0.0) != 0)
        | (pl.col("net_pnl").fill_null(0.0) != 0)
    )
    years = nonzero.get_column("year").drop_nulls().unique().sort().to_list()
    if not years:
        raise ValueError("selection cash-P&L frame contains no nonzero P&L")

    selector = widgets.Dropdown(description="Year", options=years, value=years[-1])
    plot_output = widgets.Output(
        layout=widgets.Layout(
            width="100%",
            height="auto",
            max_height="none",
            overflow="visible",
        )
    )

    def plot_year(year: int):
        """Aggregate and draw one calendar year's instrument attribution."""
        annual = (
            frame.filter(pl.col("year") == year)
            .group_by("symbol")
            .agg(
                pl.col("gross_pnl").fill_null(0.0).sum().alias("gross_pnl"),
                pl.col("trade_cost").fill_null(0.0).sum().alias("trade_cost"),
                pl.col("roll_cost").fill_null(0.0).sum().alias("roll_cost"),
                pl.col("net_pnl").fill_null(0.0).sum().alias("net_pnl"),
            )
            .filter(
                (pl.col("gross_pnl") != 0)
                | (pl.col("trade_cost") != 0)
                | (pl.col("roll_cost") != 0)
                | (pl.col("net_pnl") != 0)
            )
            .sort("net_pnl", descending=True)
        )
        if annual.is_empty():
            raise ValueError(f"year {year} contains no nonzero instrument P&L")
        total_net = annual.get_column("net_pnl").sum()
        pnl_min = min(0.0, float(annual.get_column("net_pnl").min()))
        pnl_max = max(0.0, float(annual.get_column("net_pnl").max()))
        pnl_span = pnl_max - pnl_min
        scale_padding = (
            pnl_span * 0.06
            if pnl_span > 0
            else max(abs(pnl_max), abs(pnl_min), 1.0) * 0.06
        )
        bars = annual.hvplot.bar(
            x="symbol",
            y="net_pnl",
            title=f"{year} instrument net P&L (portfolio total ${total_net:,.0f})",
            xlabel="Instrument",
            ylabel="Net P&L (USD)",
            rot=70,
            color="#1f77b4",
            width=width,
            height=height,
            tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"],
            hover_cols=["gross_pnl", "trade_cost", "roll_cost"],
            hover_tooltips=[
                ("Instrument", "@symbol"),
                ("Net P&L", "@net_pnl{$0,0.00}"),
                ("Gross P&L", "@gross_pnl{$0,0.00}"),
                ("Trade cost", "@trade_cost{$0,0.00}"),
                ("Roll cost", "@roll_cost{$0,0.00}"),
            ],
        ).opts(ylim=(pnl_min - scale_padding, pnl_max + scale_padding))
        return bars * hv.HLine(0.0).opts(color="black", line_width=1)

    def update_plot(change=None) -> None:
        """Redraw the annual attribution selected by the year widget."""
        year = change["new"] if change is not None else selector.value
        with plot_output:
            plot_output.clear_output(wait=True)
            display(plot_year(year))

    selector.observe(update_plot, names="value")
    update_plot()
    return widgets.VBox(
        [selector, plot_output],
        layout=widgets.Layout(width="100%", overflow="visible"),
    )


def plot_selection_rebalance_weights(
    review_instruments: pl.DataFrame | Path | str,
    *,
    width: int = 1050,
    height: int = 420,
):
    """Plot instrument and classification weights at each selection review.

    ``review_instruments`` may be the public Polars frame or a saved
    ``selection_review_instruments`` Parquet path. The year selector defaults
    to the latest review year. When a monthly run has more than one review in
    that year, a second selector chooses the exact review date. The first bars
    show the final ``limit_weight`` used for instrument position sizing. Two
    companion bars aggregate those final weights into asset-class and
    economic-subclass shares of the deployed book. Group bars normalize by the
    sum of active ``limit_weight`` values so their shares total 100%; hover
    retains each group's absolute limit-weight total so max-weight
    under-allocation remains visible.

    Parameters
    ----------
    review_instruments
        Scheduled selection review-instrument frame or its Parquet path. The
        public schema must include ``sel_date``, ``symbol``, ``sel_active``,
        ``weight``, and ``limit_weight``.
    width, height
        Width and height of the vertical bar chart in pixels.

    Returns
    -------
    ipywidgets.VBox
        A year selector, an exact-date selector for multi-review years, and an
        output area containing the selected review's instrument, class, and
        subclass weight bars.
    """
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API
    import ipywidgets as widgets
    from IPython.display import display

    if isinstance(review_instruments, (str, Path)):
        frame = pl.read_parquet(Path(review_instruments).expanduser())
    else:
        frame = review_instruments
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(
            "review_instruments must be a Polars DataFrame or Parquet path"
        )

    required = {"sel_date", "symbol", "sel_active", "weight", "limit_weight"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            "selection review-instrument frame is missing required columns: "
            f"{missing}"
        )

    # Normalize the review key once so widget values compare cleanly even when
    # a caller supplies a datetime rather than the persisted public Date type.
    frame = frame.with_columns(pl.col("sel_date").cast(pl.Date))
    review_dates = (
        frame.get_column("sel_date").drop_nulls().unique().sort().to_list()
    )
    if not review_dates:
        raise ValueError("selection review-instrument frame has no review dates")

    years = sorted({review_date.year for review_date in review_dates})
    dates_by_year = {
        year: [
            date_value
            for date_value in review_dates
            if date_value.year == year
        ]
        for year in years
    }

    year_selector = widgets.Dropdown(
        description="Year",
        options=years,
        value=years[-1],
    )
    review_selector = widgets.Dropdown(description="Review")
    plot_outputs = [
        widgets.Output(
            layout=widgets.Layout(
                width="100%",
                height="auto",
                max_height="none",
                overflow="visible",
            )
        )
        for _ in range(3)
    ]

    def configure_review_selector(year: int) -> None:
        """Expose exact review dates only when the chosen year needs them."""
        year_dates = dates_by_year[year]
        review_selector.options = [
            (review_date.isoformat(), review_date) for review_date in year_dates
        ]
        review_selector.value = year_dates[-1]
        review_selector.layout.display = "" if len(year_dates) > 1 else "none"

    def plot_review(review_date: date):
        """Build instrument and group-weight bars for one dated book."""
        dated = frame.filter(pl.col("sel_date") == review_date)
        active = (
            dated.filter(
                pl.col("sel_active").fill_null(False)
                & pl.col("limit_weight").is_not_null()
            )
            .sort("limit_weight", "symbol", descending=[True, False])
            .with_columns(
                (pl.col("limit_weight") * 100.0).alias("limit_weight_pct"),
                (pl.col("weight") * 100.0).alias("weight_pct"),
            )
        )
        if active.is_empty():
            raise ValueError(
                f"review {review_date.isoformat()} has no active weights"
            )

        optional_defaults = {
            "sel_status": None,
            "rank": None,
            "asset_cls": None,
            "econ_subcls": None,
            "sel_equity": None,
            "cls_weight": None,
            "cls_lim_weight": None,
            "subcls_weight": None,
            "subcls_lim_weight": None,
            "group_lim_weight": None,
            "unalloc_weight": None,
        }
        active = (
            active.with_columns(
                *[
                    pl.lit(value).alias(column)
                    for column, value in optional_defaults.items()
                    if column not in active.columns
                ]
            ).with_columns(
                *[
                    (
                        pl.col(column).cast(pl.Float64, strict=False) * 100.0
                    ).alias(f"{column}_pct")
                    for column in (
                        "cls_weight",
                        "cls_lim_weight",
                        "subcls_weight",
                        "subcls_lim_weight",
                        "group_lim_weight",
                        "unalloc_weight",
                    )
                ],
            )
        )
        added = (
            dated.filter(pl.col("sel_status") == "added").height
            if "sel_status" in dated.columns
            else 0
        )
        removed = (
            dated.filter(pl.col("sel_status") == "removed").height
            if "sel_status" in dated.columns
            else 0
        )
        title = (
            f"Selection limit weights at {review_date.isoformat()} "
            f"({active.height} instruments, +{added} / -{removed})"
        )
        instrument_bars = active.hvplot.bar(
            x="symbol",
            y="limit_weight_pct",
            title=title,
            xlabel="Instrument",
            ylabel="Limit weight (%)",
            width=width,
            height=height,
            rot=70,
            color="#1f77b4",
            tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"],
            hover_cols=[
                "weight_pct",
                "asset_cls",
                "econ_subcls",
                "limit_weight_pct",
                "sel_status",
                "rank",
                "sel_equity",
                "cls_weight_pct",
                "cls_lim_weight_pct",
                "subcls_weight_pct",
                "subcls_lim_weight_pct",
                "group_lim_weight_pct",
                "unalloc_weight_pct",
            ],
            hover_tooltips=[
                ("Instrument", "@symbol"),
                ("Limit weight", "@limit_weight_pct{0.00}%"),
                ("Structural weight", "@weight_pct{0.00}%"),
                ("Class", "@asset_cls"),
                ("Subclass", "@econ_subcls"),
                ("Status", "@sel_status"),
                ("Rank", "@rank"),
                ("Class weight", "@cls_weight_pct{0.00}%"),
                ("Capped class weight", "@cls_lim_weight_pct{0.00}%"),
                ("Subclass weight", "@subcls_weight_pct{0.00}%"),
                (
                    "Capped subclass weight",
                    "@subcls_lim_weight_pct{0.00}%",
                ),
                ("Group-limited weight", "@group_lim_weight_pct{0.00}%"),
                ("Unallocated book weight", "@unalloc_weight_pct{0.00}%"),
                ("Review equity", "@sel_equity{$0,0.00}"),
            ],
        )

        deployed_limit_weight = float(active.get_column("limit_weight").sum())
        if (
            not math.isfinite(deployed_limit_weight)
            or deployed_limit_weight <= 0
        ):
            raise ValueError(
                f"review {review_date.isoformat()} has no positive limit weight"
            )

        # Group shares describe concentration within the final deployed book.
        # Preserve the absolute total because instrument caps can leave final
        # limit weights summing below one even when group weights sum to one.
        classified = active.with_columns(
            pl.col("asset_cls").fill_null("unclassified"),
            pl.col("econ_subcls").fill_null("unclassified"),
        )
        class_weights = (
            classified.group_by("asset_cls")
            .agg(
                pl.col("limit_weight").sum().alias("group_limit_weight"),
                pl.len().alias("instr_n"),
                pl.col("symbol").sort().str.join(", ").alias("instruments"),
            )
            .with_columns(
                (
                    pl.col("group_limit_weight")
                    / pl.lit(deployed_limit_weight)
                    * 100.0
                ).alias("book_share_pct"),
                (pl.col("group_limit_weight") * 100.0).alias(
                    "group_limit_weight_pct"
                ),
            )
            .sort("book_share_pct", "asset_cls", descending=[True, False])
        )
        subclass_weights = (
            classified.group_by("asset_cls", "econ_subcls")
            .agg(
                pl.col("limit_weight").sum().alias("group_limit_weight"),
                pl.len().alias("instr_n"),
                pl.col("symbol").sort().str.join(", ").alias("instruments"),
            )
            .with_columns(
                pl.concat_str(
                    "asset_cls", "econ_subcls", separator=" / "
                ).alias("subclass_label"),
                (
                    pl.col("group_limit_weight")
                    / pl.lit(deployed_limit_weight)
                    * 100.0
                ).alias("book_share_pct"),
                (pl.col("group_limit_weight") * 100.0).alias(
                    "group_limit_weight_pct"
                ),
            )
            .sort(
                "book_share_pct", "subclass_label", descending=[True, False]
            )
        )

        allocation_pct = deployed_limit_weight * 100.0
        class_height = max(260, height // 2)
        class_bars = class_weights.hvplot.bar(
            x="asset_cls",
            y="book_share_pct",
            title=(
                "Asset-class share of final limit-weight book "
                f"(limit weights total {allocation_pct:.2f}%)"
            ),
            xlabel="Asset class",
            ylabel="Book share (%)",
            width=width,
            height=class_height,
            rot=45,
            color="#2ca02c",
            tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"],
            hover_cols=[
                "book_share_pct",
                "group_limit_weight_pct",
                "instr_n",
                "instruments",
            ],
            hover_tooltips=[
                ("Class", "@asset_cls"),
                ("Book share", "@book_share_pct{0.00}%"),
                ("Summed limit weight", "@group_limit_weight_pct{0.00}%"),
                ("Instruments", "@instr_n"),
                ("Members", "@instruments"),
            ],
        )
        subclass_height = max(320, height)
        subclass_bars = subclass_weights.hvplot.bar(
            x="subclass_label",
            y="book_share_pct",
            title="Economic-subclass share of final limit-weight book",
            xlabel="Asset class / economic subclass",
            ylabel="Book share (%)",
            width=width,
            height=subclass_height,
            rot=70,
            color="#ff7f0e",
            tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"],
            hover_cols=[
                "asset_cls",
                "econ_subcls",
                "book_share_pct",
                "group_limit_weight_pct",
                "instr_n",
                "instruments",
            ],
            hover_tooltips=[
                ("Class", "@asset_cls"),
                ("Subclass", "@econ_subcls"),
                ("Book share", "@book_share_pct{0.00}%"),
                ("Summed limit weight", "@group_limit_weight_pct{0.00}%"),
                ("Instruments", "@instr_n"),
                ("Members", "@instruments"),
            ],
        )
        return instrument_bars, class_bars, subclass_bars

    def update_plot(change=None) -> None:
        """Redraw the exact review selected by the widget controls."""
        review_date = review_selector.value
        if review_date is None:
            return
        plots = plot_review(review_date)
        for output, plot in zip(plot_outputs, plots, strict=True):
            with output:
                output.clear_output(wait=True)
                display(plot)

    def update_year(change) -> None:
        """Refresh the available review dates after a year change."""
        configure_review_selector(change["new"])
        update_plot()

    configure_review_selector(year_selector.value)
    year_selector.observe(update_year, names="value")
    review_selector.observe(update_plot, names="value")
    update_plot()
    controls = widgets.HBox([year_selector, review_selector])
    return widgets.VBox(
        [controls, *plot_outputs],
        layout=widgets.Layout(
            width="100%",
            height="auto",
            overflow="visible",
            align_items="stretch",
        ),
    )


def load_selection_grid_review_instruments(
    grid_directory: Path | str,
    scenario_id: str,
) -> pl.DataFrame:
    """Load the complete dated-book panel for one selection-grid scenario.

    Parameters
    ----------
    grid_directory
        Selection-grid run containing ``selection_grid_summary.parquet`` and
        one subdirectory per scenario.
    scenario_id
        Exact scenario identifier from the grid summary.

    Returns
    -------
    polars.DataFrame
        The scenario's compact ``selection_review_instruments`` report across
        every annual or monthly review. This is intentionally distinct from
        ``selection_grid_membership.parquet``, which contains only each
        scenario's latest selected book.
    """
    root = Path(grid_directory).expanduser()
    summary_path = root / "selection_grid_summary.parquet"
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"selection grid summary does not exist: {summary_path}"
        )
    summary = pl.read_parquet(summary_path, columns=["scenario_id"])
    if scenario_id not in set(summary.get_column("scenario_id").to_list()):
        raise KeyError(f"selection grid has no scenario {scenario_id!r}")
    scenario_directory = root / scenario_id
    reports = sorted(
        scenario_directory.glob(
            "multi_speed_ewmac_selection_review_instruments_*.parquet"
        )
    )
    if not reports:
        raise FileNotFoundError(
            "no selection review-instrument report exists for scenario "
            f"{scenario_id!r} under {scenario_directory}"
        )
    return pl.read_parquet(reports[-1]).with_columns(
        pl.lit(scenario_id).alias("scenario_id")
    )


def plot_selection_grid_rebalance_weights(
    grid_directory: Path | str,
    *,
    scenario_id: str | None = None,
    width: int = 1050,
    height: int = 420,
):
    """Navigate every scenario and dated book in one selection grid.

    Coverage dropdowns filter the outer scenario selector by minimum class,
    subclass, and book-size settings. The nested widget is the standard
    selection-review view, with year and optional exact-review selectors plus
    instrument, asset-class, and economic-subclass limit-weight bars. Only the
    chosen scenario's compact review report is loaded, which keeps an 80-run
    grid notebook responsive and avoids loading daily cash-P&L panels.

    Parameters
    ----------
    grid_directory
        Completed selection-grid run directory.
    scenario_id
        Optional initial scenario. Defaults to the summary's baseline scenario
        when present, otherwise the first scenario identifier.
    width, height
        Dimensions forwarded to :func:`plot_selection_rebalance_weights`.

    Returns
    -------
    ipywidgets.VBox
        Scenario navigation, compact scenario metadata, and the nested dated
        selection-weight dashboard.
    """
    import ipywidgets as widgets
    from IPython.display import display

    root = Path(grid_directory).expanduser()
    summary_path = root / "selection_grid_summary.parquet"
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"selection grid summary does not exist: {summary_path}"
        )
    summary = pl.read_parquet(summary_path)
    if "scenario_id" not in summary.columns:
        raise ValueError("selection grid summary is missing scenario_id")
    if summary.is_empty():
        raise ValueError("selection grid summary has no scenarios")

    def scenario_label(row: dict[str, object]) -> str:
        """Return a compact unique label for one scenario dropdown option."""
        eligibility = row.get("sel_elig_hyst_pct")
        mode = str(row.get("sel_membership_hyst") or "none")
        if isinstance(eligibility, (int, float)):
            parts = [f"EH{eligibility:g}", mode]
        else:
            parts = [mode]
        rank_buffer = row.get("sel_rank_buf_pct")
        rank_halflife = row.get("sel_rank_n_ewma_hl")
        score_buffer = row.get("sel_book_score_buf_pct")
        if isinstance(rank_buffer, (int, float)):
            parts.append(f"RB{rank_buffer:g}")
        if isinstance(rank_halflife, (int, float)):
            parts.append(f"HL{rank_halflife:g}")
        if isinstance(score_buffer, (int, float)):
            parts.append(f"SB{score_buffer:g}")
        for column, label in resolved_coverage_columns:
            value = row.get(column)
            if value is not None:
                short_label = {
                    "Min / class": "MC",
                    "Min / subclass": "MS",
                    "Min book size": "MB",
                }[label]
                parts.append(f"{short_label}{value:g}")
            elif label != "Min book size":
                parts.append(f"{ {'Min / class': 'MC', 'Min / subclass': 'MS'}[label] }off")
        if bool(row.get("baseline_scenario")):
            parts.append("baseline")
        return " · ".join(parts)

    rows = summary.sort(
        "sel_elig_hyst_pct",
        "sel_membership_hyst",
        "sel_rank_buf_pct",
        "sel_rank_n_ewma_hl",
        "sel_book_score_buf_pct",
        nulls_last=True,
    ).to_dicts()
    coverage_columns = (
        ("minimum_instruments_per_class", "min_instr_per_cls", "Min / class"),
        (
            "minimum_instruments_per_subclass",
            "min_instr_per_subcls",
            "Min / subclass",
        ),
        ("minimum_book_count", "min_book_n", "Min book size"),
    )
    resolved_coverage_columns = [
        (next((name for name in aliases if name in summary.columns), None), label)
        for *aliases, label in coverage_columns
    ]
    resolved_coverage_columns = [
        (column, label)
        for column, label in resolved_coverage_columns
        if column is not None
    ]

    def coverage_options(column: str) -> list[tuple[str, object]]:
        """Build stable filter options, including disabled and all values."""
        values = summary.get_column(column).unique().to_list()
        values = sorted(
            values,
            key=lambda value: (value is None, value if value is not None else 0),
        )
        options: list[tuple[str, object]] = [("All", "__all__")]
        options.extend(
            ("Disabled" if value is None else str(value), value)
            for value in values
        )
        return options

    coverage_selectors = {
        column: widgets.Dropdown(
            description=label,
            options=coverage_options(column),
            value="__all__",
            layout=widgets.Layout(width="32%"),
        )
        for column, label in resolved_coverage_columns
    }

    def matching_rows() -> list[dict[str, object]]:
        """Return scenarios matching the selected coverage filters."""
        matching = rows
        for column, control in coverage_selectors.items():
            selected = control.value
            if selected != "__all__":
                matching = [
                    row for row in matching if row.get(column) == selected
                ]
        return matching

    options = [(scenario_label(row), str(row["scenario_id"])) for row in rows]
    scenario_ids = {value for _, value in options}
    if scenario_id is None:
        baseline = (
            summary.filter(pl.col("baseline_scenario").fill_null(False))
            if "baseline_scenario" in summary.columns
            else pl.DataFrame()
        )
        scenario_id = (
            str(baseline.get_column("scenario_id")[0])
            if baseline.height
            else options[0][1]
        )
    if scenario_id not in scenario_ids:
        raise KeyError(f"selection grid has no scenario {scenario_id!r}")

    selector = widgets.Dropdown(
        description="Scenario",
        options=options,
        value=scenario_id,
        layout=widgets.Layout(width="95%"),
    )
    metadata_output = widgets.Output()
    plot_output = widgets.Output(
        layout=widgets.Layout(
            width="100%",
            height="auto",
            max_height="none",
            overflow="visible",
        )
    )
    metadata_columns = [
        column
        for column in (
            "scenario_id",
            "sel_elig_hyst_pct",
            "sel_membership_hyst",
            "sel_rank_buf_pct",
            "sel_rank_n_ewma_hl",
            "sel_book_score_buf_pct",
            *[column for column, _ in resolved_coverage_columns],
            "n_instr",
            "construction_score",
            "jaccard_to_baseline",
        )
        if column in summary.columns
    ]

    def update_scenario(change=None) -> None:
        """Load and redraw only the newly selected scenario."""
        selected = change["new"] if change is not None else selector.value
        if selected == "__none__":
            return
        metadata = summary.filter(pl.col("scenario_id") == selected).select(
            metadata_columns
        )
        metadata = metadata.rename({
            column: f"latest_{column}"
            for column in (
                "n_instr",
                "construction_score",
                "jaccard_to_baseline",
            )
            if column in metadata.columns
        })
        reviews = load_selection_grid_review_instruments(root, selected)
        with metadata_output:
            metadata_output.clear_output(wait=True)
            display(metadata)
        with plot_output:
            plot_output.clear_output(wait=True)
            display(
                plot_selection_rebalance_weights(
                    reviews,
                    width=width,
                    height=height,
                )
            )

    def update_coverage(change=None) -> None:
        """Restrict scenario choices to the chosen coverage parameters."""
        candidates = matching_rows()
        candidate_options = [
            (scenario_label(row), str(row["scenario_id"]))
            for row in candidates
        ]
        if not candidate_options:
            selector.unobserve(update_scenario, names="value")
            selector.options = [("No matching scenarios", "__none__")]
            selector.value = "__none__"
            selector.observe(update_scenario, names="value")
            with metadata_output:
                metadata_output.clear_output(wait=True)
                display(
                    widgets.Label(
                        "No scenarios match these coverage settings."
                    )
                )
            with plot_output:
                plot_output.clear_output(wait=True)
            return
        current = selector.value
        selected = next(
            (
                str(row["scenario_id"])
                for row in candidates
                if str(row["scenario_id"]) == current
            ),
            None,
        )
        if selected is None:
            selected = next(
                (
                    str(row["scenario_id"])
                    for row in candidates
                    if bool(row.get("baseline_scenario"))
                ),
                candidate_options[0][1],
            )
        selector.unobserve(update_scenario, names="value")
        selector.options = candidate_options
        selector.value = selected
        selector.observe(update_scenario, names="value")
        update_scenario()

    selector.observe(update_scenario, names="value")
    for control in coverage_selectors.values():
        control.observe(update_coverage, names="value")
    update_scenario()
    return widgets.VBox(
        [
            selector,
            widgets.HBox(list(coverage_selectors.values())),
            metadata_output,
            plot_output,
        ],
        layout=widgets.Layout(width="100%", overflow="visible"),
    )


def plot_selection_grid_dashboard(
    grid_directory: Path | str,
    *,
    scenario_id: str | None = None,
    view: str = "cash_pnl",
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    rolling_days: int = 256,
    width: int = 1050,
    height: int = 420,
):
    """Navigate the standard selection plots across grid scenarios.

    The dashboard offers cash P&L and combined-forecast panels, portfolio
    overview, realized risk concentration, annual P&L attribution, and dated
    rebalance weights. Coverage dropdowns filter the scenario choices. Only
    the chosen scenario's report is loaded when a view is selected.

    Parameters
    ----------
    grid_directory
        Completed selection-grid run directory.
    scenario_id
        Optional initial scenario identifier. Defaults to the baseline scenario
        when present, otherwise the first summary row.
    view
        Initial view key: ``cash_pnl``, ``portfolio_overview``,
        ``concentration``, ``pnl_attribution``, or ``rebalance_weights``.
    start, end
        Optional inclusive date bounds for time-series views.
    rolling_days
        Trailing observation count forwarded to the portfolio overview.
    width, height
        Plot dimensions forwarded to the standard notebook plotters.

    Returns
    -------
    ipywidgets.VBox
        Coverage, scenario, and view selectors with the selected plot.
    """
    import ipywidgets as widgets
    from IPython.display import display

    view_labels = {
        "cash_pnl": "Cash P&L + combined forecast",
        "portfolio_overview": "Portfolio overview",
        "concentration": "Realized risk concentration",
        "pnl_attribution": "Annual P&L attribution",
        "rebalance_weights": "Rebalance weights",
    }
    if view not in view_labels:
        raise ValueError(
            f"unknown grid view {view!r}; choose one of {list(view_labels)}"
        )

    root = Path(grid_directory).expanduser()
    summary_path = root / "selection_grid_summary.parquet"
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"selection grid summary does not exist: {summary_path}"
        )
    summary = pl.read_parquet(summary_path)
    if "scenario_id" not in summary.columns:
        raise ValueError("selection grid summary is missing scenario_id")
    if summary.is_empty():
        raise ValueError("selection grid summary has no scenarios")

    rows = summary.to_dicts()
    coverage_schema = (
        ("minimum_instruments_per_class", "min_instr_per_cls", "Min / class"),
        (
            "minimum_instruments_per_subclass",
            "min_instr_per_subcls",
            "Min / subclass",
        ),
        ("minimum_book_count", "min_book_n", "Min book size"),
    )
    coverage_columns = []
    for schema_row in coverage_schema:
        aliases, label = schema_row[:-1], schema_row[-1]
        column = next(
            (name for name in aliases if name in summary.columns), None
        )
        if column is not None:
            coverage_columns.append((column, label))

    coverage_selectors = {}
    for column, label in coverage_columns:
        values = summary.get_column(column).unique().to_list()
        values = sorted(
            values,
            key=lambda value: (value is None, value if value is not None else 0),
        )
        options = [("All", "__all__")]
        options.extend(
            ("Disabled" if value is None else str(value), value)
            for value in values
        )
        coverage_selectors[column] = widgets.Dropdown(
            description=label,
            options=options,
            value="__all__",
            layout=widgets.Layout(width="32%"),
        )

    def matching_rows() -> list[dict[str, object]]:
        """Apply the selected minimum-coverage filters to scenario rows."""
        matching = rows
        for column, control in coverage_selectors.items():
            if control.value != "__all__":
                matching = [
                    row
                    for row in matching
                    if row.get(column) == control.value
                ]
        return matching

    def label_for(row: dict[str, object]) -> str:
        """Label scenarios with their ID and enabled coverage minimums."""
        parts = [str(row["scenario_id"])]
        for column, label in coverage_columns:
            value = row.get(column)
            short = {
                "Min / class": "MC",
                "Min / subclass": "MS",
                "Min book size": "MB",
            }[label]
            if value is not None:
                parts.append(f"{short}{value:g}")
        return " · ".join(parts)

    scenario_options = [
        (label_for(row), str(row["scenario_id"])) for row in rows
    ]
    available_scenario_ids = {value for _, value in scenario_options}
    if scenario_id is None:
        baseline_rows = [
            row for row in rows if bool(row.get("baseline_scenario"))
        ]
        scenario_id = str(
            baseline_rows[0]["scenario_id"]
            if baseline_rows
            else rows[0]["scenario_id"]
        )
    if scenario_id not in available_scenario_ids:
        raise KeyError(f"selection grid has no scenario {scenario_id!r}")

    scenario_selector = widgets.Dropdown(
        description="Scenario",
        options=scenario_options,
        value=scenario_id,
        layout=widgets.Layout(width="95%"),
    )
    view_selector = widgets.Dropdown(
        description="View",
        options=[(label, key) for key, label in view_labels.items()],
        value=view,
        layout=widgets.Layout(width="95%"),
    )
    metadata_output = widgets.Output()
    plot_output = widgets.Output(
        layout=widgets.Layout(
            width="100%",
            height="auto",
            max_height="none",
            overflow="visible",
        )
    )

    def scenario_cash_path(selected: str) -> tuple[Path, Path]:
        """Resolve the latest matching cash and portfolio reports."""
        directory = root / selected
        cash_reports = sorted(
            directory.glob("multi_speed_ewmac_selection_cash_pnl_*.parquet")
        )
        if not cash_reports:
            raise FileNotFoundError(
                f"no selection cash-P&L report for scenario {selected!r}"
            )
        cash_path = cash_reports[-1]
        stamp = cash_path.name.removeprefix(
            "multi_speed_ewmac_selection_cash_pnl_"
        ).removesuffix(".parquet")
        curve_path = directory / (
            f"multi_speed_ewmac_selection_portfolio_curve_{stamp}.parquet"
        )
        if not curve_path.is_file():
            raise FileNotFoundError(
                f"portfolio curve matching {cash_path.name} is missing"
            )
        return cash_path, curve_path

    def plot_selected(selected: str, selected_view: str):
        """Load the report required by one selected scenario view."""
        if selected_view == "rebalance_weights":
            reviews = load_selection_grid_review_instruments(root, selected)
            return plot_selection_rebalance_weights(
                reviews, width=width, height=height
            )
        cash_path, curve_path = scenario_cash_path(selected)
        if selected_view == "cash_pnl":
            return plot_selection_cash_pnl(
                cash_path,
                start=start,
                end=end,
                width=width,
                height=min(height, 240),
            )
        if selected_view == "portfolio_overview":
            return plot_selection_portfolio_overview(
                curve_path,
                start=start,
                end=end,
                rolling_days=rolling_days,
                width=width,
                height=min(height, 260),
            )
        if selected_view == "concentration":
            return plot_selection_portfolio_concentration(
                cash_path,
                start=start,
                end=end,
                width=width,
                height=min(height, 240),
            )
        return plot_selection_pnl_attribution(
            cash_path,
            width=width,
            height=height,
        )

    def update_plot(change=None) -> None:
        """Redraw the chosen scenario and standard plot view."""
        selected = scenario_selector.value
        if selected == "__none__":
            return
        selected_view = view_selector.value
        selected_row = next(
            row for row in rows if str(row["scenario_id"]) == selected
        )
        metadata_columns = [
            column
            for column in (
                "scenario_id",
                "target_vol",
                "min_instr_per_cls",
                "min_instr_per_subcls",
                "min_book_n",
                "sel_elig_hyst_pct",
                "sel_membership_hyst",
                "sel_rank_buf_pct",
                "sel_rank_n_ewma_hl",
                "sel_book_score_buf_pct",
                "n_instr",
                "construction_score",
                "jaccard_to_baseline",
            )
            if column in summary.columns
        ]
        selected_metadata = pl.DataFrame([selected_row]).select(metadata_columns)
        with metadata_output:
            metadata_output.clear_output(wait=True)
            display(selected_metadata)
        with plot_output:
            plot_output.clear_output(wait=True)
            display(plot_selected(selected, selected_view))

    def update_coverage(change=None) -> None:
        """Restrict scenario choices to the selected coverage settings."""
        candidates = matching_rows()
        options = [
            (label_for(row), str(row["scenario_id"])) for row in candidates
        ]
        if not options:
            scenario_selector.unobserve(update_plot, names="value")
            scenario_selector.options = [("No matching scenarios", "__none__")]
            scenario_selector.value = "__none__"
            scenario_selector.observe(update_plot, names="value")
            with metadata_output:
                metadata_output.clear_output(wait=True)
                display(
                    widgets.Label(
                        "No scenarios match these coverage settings."
                    )
                )
            with plot_output:
                plot_output.clear_output(wait=True)
            return
        current = scenario_selector.value
        selected = next(
            (value for _, value in options if value == current), None
        )
        if selected is None:
            selected = next(
                (
                    str(row["scenario_id"])
                    for row in candidates
                    if bool(row.get("baseline_scenario"))
                ),
                options[0][1],
            )
        scenario_selector.unobserve(update_plot, names="value")
        scenario_selector.options = options
        scenario_selector.value = selected
        scenario_selector.observe(update_plot, names="value")
        update_plot()

    scenario_selector.observe(update_plot, names="value")
    view_selector.observe(update_plot, names="value")
    for control in coverage_selectors.values():
        control.observe(update_coverage, names="value")
    update_plot()
    return widgets.VBox(
        [
            view_selector,
            scenario_selector,
            widgets.HBox(list(coverage_selectors.values())),
            metadata_output,
            plot_output,
        ],
        layout=widgets.Layout(width="100%", overflow="visible"),
    )


def _attach_fx(frame: pl.DataFrame, fx_to_usd: float) -> pl.DataFrame:
    """Attach one constant native-currency-to-USD rate for notebook research."""
    if not math.isfinite(fx_to_usd) or fx_to_usd <= 0:
        raise ValueError("fx_to_usd must be finite and positive")
    return frame.with_columns(pl.lit(float(fx_to_usd)).alias("fx_to_usd"))


def _forecast_with_fx(
    result: CombinedForecastResult,
    fx_to_usd: float | None,
) -> pl.DataFrame:
    """Use causal forecast FX when present, otherwise attach a constant rate."""
    if fx_to_usd is not None:
        return _attach_fx(result.frame, fx_to_usd)
    if "fx_to_usd" in result.frame.columns:
        return result.frame
    return _attach_fx(result.frame, 1.0)


def _read_screen_frame(
    screen: pl.DataFrame | Path | str,
) -> pl.DataFrame:
    """Load a selection-screen report from a Polars frame or local file."""
    if isinstance(screen, pl.DataFrame):
        return screen
    path = Path(screen).expanduser()
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pl.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pl.read_csv(path, try_parse_dates=True)
    raise ValueError(
        "screen must be a Polars dataframe or a .parquet/.csv report path"
    )


def _screen_row(
    screen: pl.DataFrame,
    symbol: str,
) -> dict[str, object]:
    """Resolve one screen row by its executable ``symbol`` key."""
    if "symbol" not in screen.columns:
        raise ValueError("screen has no executable 'symbol' column")
    matches = screen.filter(pl.col("symbol").cast(pl.String) == symbol)
    if matches.height == 0:
        raise ValueError(
            f"screen has no executable symbol row for {symbol!r}"
        )
    if matches.height != 1:
        raise ValueError(
            f"executable symbol {symbol!r} matches {matches.height} "
            "screen rows; symbol must uniquely identify execution"
        )
    return matches.row(0, named=True)


def _screen_number(
    row: dict[str, object],
    names: tuple[str, ...],
    *,
    label: str,
) -> float:
    """Extract a finite numeric screen value with supported schema aliases."""
    value = _screen_optional_number(row, names)
    if value is not None:
        return value
    raise ValueError(
        f"screen row is missing a finite {label}; expected one of {names}"
    )


def _screen_optional_number(
    row: dict[str, object],
    names: tuple[str, ...],
) -> float | None:
    """Return the first finite numeric value, skipping null screen fields."""
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


@dataclass(frozen=True)
class SubsystemReferenceSettings:
    """Configure the equal-weight candidate return used for correlations.

    ``capital`` and ``target_vol`` turn daily dollar volatility into an
    auditable continuous position. ``ref_book_count`` assigns every
    candidate the same structural weight, while ``ref_idm`` and
    ``max_weight_idm`` apply the same fixed sizing convention as the selection
    tie rule. The resulting USD series is divided by ``ref_cap_alloc`` and
    exposed as a normalized subsystem-reference percentage series.
    """

    capital: float
    target_vol: float
    reference_book_count: int = 15
    reference_idm: float = 2.5
    max_weight_idm: float = 0.20


@dataclass(frozen=True)
class ExecutableCashSettings:
    """Configure the optional account-scaled executable cash curve.

    Parameters are passed unchanged to
    :func:`build_executable_cash_pnl_curve`. Monetary inputs are USD; costs are
    one-way dollars per contract. ``weight`` is the instrument risk weight and
    ``idm`` is the instrument diversification multiplier.
    """

    capital: float
    target_vol: float
    weight: float = 1.0
    idm: float = 1.0
    trade_cost_usd_per_con: float = 0.0
    roll_cost_usd_per_con: float = 0.0
    buffer_size: float = PYSYSTEMTRADE_FORECAST_BUFFER_SIZE
    buffer_trade_to_edge: bool = True
    rebalance_on_roll: bool = True


@dataclass(frozen=True)
class EwmacNotebookView:
    """Hold forecast, subsystem-reference, and optional cash-P&L frames.

    ``forecasts`` returns the combined-forecast result at the compact public
    schema boundary. ``reference`` is the equal-weight continuous position
    and return path used for turnover and correlations.
    ``reference_turnover`` is its annualized average-position turnover. ``cash``
    is present only when executable account settings were supplied and
    contains continuous targets, buffered whole contracts, and cash costs.
    ``history`` retains the source-owned futures streams when the stitched
    history helper constructed the view. ``contract_history`` retains the
    overlapping dated-contract download used to build its newest segment.
    """

    symbol: str
    result: CombinedForecastResult
    reference: pl.DataFrame
    reference_turnover: float | None
    cash: pl.DataFrame | None = None
    history: FuturesHistory | None = None
    contract_history: pl.DataFrame | None = None
    close_label: str = "Panama price"

    @property
    def forecasts(self) -> pl.DataFrame:
        """Return the compact public forecast frame before reference sizing."""
        return rename_public_report_columns(self.result.frame)

    def forecast_positions(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return the forecast and sized reference-position diagnostics."""
        frame = _date_slice(self.reference, start=start, end=end)
        columns = [
            "date",
            "close",
            "comb_fcst",
            "vol_scalar",
            "ref_avg_pos",
            "ref_tgt_pos",
            "ref_norm_pos",
        ]
        available_columns = [
            column for column in columns if column in frame.columns
        ]
        return frame.select(available_columns)

    def subsystem_reference_pct_curves(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return cumulative gross, cost, and net reference percentages."""
        frame = _date_slice(self.reference, start=start, end=end)
        return frame.select(
            "date",
            pl.col("ref_gross_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_gross_pct"),
            pl.col("ref_cost_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_cost_pct"),
            pl.col("ref_net_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_net_pct"),
        )

    def subsystem_reference_accounting(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return grouped reference budget, USD, and percentage diagnostics."""
        frame = _date_slice(self.reference, start=start, end=end)
        return frame.select(
            "date",
            "port_ann_risk_target",
            "ref_cap_alloc",
            "ref_ann_risk",
            "ref_daily_risk",
            "ref_gross",
            "ref_cost",
            "ref_net",
            "ref_gross_pct",
            "ref_ann_cost_pct",
            "ref_cost_pct",
            "ref_net_pct",
        )

    def cash_pnl_curves(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return cumulative executable cash P&L or fail if cash was omitted."""
        if self.cash is None:
            raise ValueError("cash P&L was not requested for this notebook view")
        frame = _date_slice(self.cash, start=start, end=end)
        return frame.select(
            "date",
            "cum_gross_pnl",
            "cum_net_pnl",
        )

    def cash_return_curves(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return daily and cumulative executable cash percentage returns.

        Gross return divides this instrument's executable cash flow by prior
        portfolio gross equity. Net and cost returns divide by prior portfolio
        net equity. Contributions from all selected instruments therefore add
        to the corresponding daily portfolio return. Cost columns are negative
        percentage points and are nonzero only on trade or roll dates.
        """
        if self.cash is None:
            raise ValueError("cash P&L was not requested for this notebook view")
        frame = _date_slice(self.cash, start=start, end=end)
        return frame.select(
            "date",
            "gross_pct",
            "trade_cost_pct",
            "roll_cost_pct",
            "total_cost_pct",
            "net_pct",
            "cum_gross_pct",
            "cum_trade_cost_pct",
            "cum_roll_cost_pct",
            "cum_total_cost_pct",
            "cum_net_pct",
        )

    def cash_return_significance(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        annualization_days: int = 256,
    ) -> pl.DataFrame:
        """Summarize executable net-return magnitude and IID significance.

        Daily ``net_pct`` values are contributions relative to prior portfolio
        net equity. The annual mean is cumulative net contribution divided by
        elapsed calendar years; annual volatility scales the daily sample
        standard deviation by ``sqrt(annualization_days)``. The reported
        t-statistic is the corresponding Sharpe ratio times ``sqrt(years)``.
        It is a simple IID diagnostic and does not adjust for serial dependence,
        non-normal tails, or repeated strategy comparisons.

        Parameters
        ----------
        start, end
            Optional inclusive boundaries for the cash-return sample.
        annualization_days
            Trading-day count used to annualize daily volatility.

        Returns
        -------
        polars.DataFrame
            One row containing sample dates, observation count, elapsed years,
            cumulative and annualized net return percentages, annualized
            volatility, Sharpe ratio, and approximate IID t-statistic.
        """
        if self.cash is None:
            raise ValueError("cash P&L was not requested for this notebook view")
        sample = (
            _date_slice(self.cash, start=start, end=end)
            .select("date", "net_pct")
        )
        return summarize_return_significance(
            sample,
            return_col="net_pct",
            annualization_days=annualization_days,
        ).rename(
            {"cum_return_pct": "cum_net_pct"}
        )

    def annual_turnover_audit(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Compare annual reference turnover with executable trading.

        Executable turnover is the absolute ordinary contract trade divided
        by the smoothed ``ref_avg_pos`` on the same date, not the instantaneous
        ``vol_scalar``. This is directly comparable with reference turnover
        when the reference and executable curves use the same instrument risk
        budget. Contract rolls
        are excluded from both turnover measures and reported separately;
        ``con_load`` counts an ordinary trade as one contract operation and a
        roll as the closing and opening of one contract.

        Parameters
        ----------
        start, end
            Optional inclusive date limits applied before calendar-year
            aggregation. A limit inside a year therefore produces a partial
            first or last year.

        Returns
        -------
        polars.DataFrame
            One row per calendar year with executable and reference turnover,
            ordinary and rolled contract quantities, total contract load,
            and the proportions of reference turnover retained and
            suppressed by whole-contract buffering.

        Raises
        ------
        ValueError
            If the view has no executable cash curve or the reference and
            executable annual risk budgets differ.
        """
        if self.cash is None:
            raise ValueError("cash P&L was not requested for this notebook view")

        reference = _date_slice(self.reference, start=start, end=end).select(
            "date",
            "ref_ann_risk",
            "ref_avg_pos",
            "ref_abs_pos_change",
        )
        cash = _date_slice(self.cash, start=start, end=end).select(
            "date",
            "risk_budget",
            "trade_con",
            "roll_con",
        )
        joined = reference.join(cash, on="date", how="inner").sort("date")
        if joined.is_empty():
            return pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "exec_turn": pl.Float64,
                    "ref_turn": pl.Float64,
                    "trade_con": pl.Int64,
                    "roll_con": pl.Int64,
                    "con_load": pl.Int64,
                    "turn_retained": pl.Float64,
                    "turn_suppression": pl.Float64,
                }
            )

        budget_mismatch = joined.filter(
            pl.col("ref_ann_risk").is_not_null()
            & pl.col("risk_budget").is_not_null()
            & (
                (pl.col("ref_ann_risk") - pl.col("risk_budget")).abs()
                > (
                    pl.max_horizontal(
                        pl.col("ref_ann_risk").abs(),
                        pl.col("risk_budget").abs(),
                        pl.lit(1.0),
                    )
                    * 1e-9
                )
            )
        )
        if not budget_mismatch.is_empty():
            raise ValueError(
                "annual turnover audit requires matching reference and "
                "executable instrument risk budgets"
            )

        annual = (
            joined.with_columns(
                pl.when(
                    pl.col("ref_avg_pos").is_not_null()
                    & pl.col("ref_avg_pos").is_finite()
                    & (pl.col("ref_avg_pos") > 0)
                )
                .then(
                    pl.col("trade_con").abs()
                    / pl.col("ref_avg_pos")
                )
                .otherwise(None)
                .alias("exec_turn_row")
            )
            .group_by_dynamic(
                "date",
                every="1y",
                closed="left",
                label="left",
            )
            .agg(
                pl.col("exec_turn_row").sum().alias("exec_turn"),
                pl.col("ref_abs_pos_change").sum().alias("ref_turn"),
                pl.col("trade_con").abs().sum().alias("trade_con"),
                pl.col("roll_con").sum().alias("roll_con"),
            )
            .with_columns(
                pl.col("trade_con").cast(pl.Int64),
                pl.col("roll_con").cast(pl.Int64),
            )
            .with_columns(
                (
                    pl.col("trade_con") + 2 * pl.col("roll_con")
                ).alias("con_load"),
                pl.when(
                    pl.col("ref_turn").is_not_null()
                    & pl.col("ref_turn").is_finite()
                    & (pl.col("ref_turn") > 0)
                )
                .then(pl.col("exec_turn") / pl.col("ref_turn"))
                .otherwise(None)
                .alias("turn_retained"),
            )
            .with_columns(
                (1.0 - pl.col("turn_retained")).alias("turn_suppression")
            )
        )
        return annual

    def plot(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        width: int = 950,
        height: int = 240,
    ):
        """Return a vertically stacked hvPlot dashboard for notebook display.

        Panels show the executable contract mark and Panama-adjusted signal
        price on one shared axis, followed by combined forecast, sized
        reference positions, normalized position, cumulative reference
        percentage, and—when configured—whole-contract positions and
        cumulative cash P&L. Each panel has an independent y-axis so the
        account-scale cash curve cannot flatten contract- or signal-scale
        diagnostics. Every panel uses the same requested date range.
        hvPlot is imported lazily so non-notebook pipelines do not initialize a
        plotting backend.
        """
        import holoviews as hv
        import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API

        reference = _date_slice(self.reference, start=start, end=end)
        if reference.is_empty():
            raise ValueError("plot date range contains no forecast observations")

        def line_panel(
            frame: pl.DataFrame,
            columns: list[str],
            *,
            title: str,
            ylabel: str,
            labels: dict[str, str] | None = None,
            colors: dict[str, str] | None = None,
        ):
            """Overlay lines with fixed-point numeric hover values."""
            curves = []
            for column in columns:
                label = (labels or {}).get(column, column)
                curves.append(
                    frame.hvplot.line(
                        x="date",
                        y=column,
                        label=label,
                        title=title,
                        ylabel=ylabel,
                        width=width,
                        height=height,
                        tools=["hover"],
                        hover_tooltips=[
                            ("Date", "@date{%F}"),
                            (label, f"@{{{column}}}{{0,0.0000}}"),
                        ],
                        **(
                            {"color": colors[column]}
                            if colors is not None and column in colors
                            else {}
                        ),
                    )
                )
            return hv.Overlay(curves)

        # ``close`` is Panama-adjusted for stitched futures and the downloaded
        # close for direct IB stocks; ``con_px`` is the raw notional mark.
        price_curves = []
        if "con_px" in reference.columns:
            price_curves.append("con_px")
        price_curves.append("close")
        price_plot = line_panel(
            reference,
            price_curves,
            title=f"{self.symbol} contract mark and {self.close_label}",
            ylabel="Price points",
            labels={"con_px": "Mark price", "close": self.close_label},
            colors={"con_px": "#1f77b4", "close": "#ff7f0e"},
        ).opts(legend_position="top_left") * hv.HLine(0.0).opts(
            color="black", line_width=1
        )
        forecast_plot = line_panel(
            reference,
            ["comb_fcst"],
            title="Combined forecast (mean abs 1, cap 2)",
            ylabel="Average-position multiple",
        ) * hv.HLine(0.0).opts(color="black", line_width=1)
        proxy_plot = line_panel(
            reference,
            ["vol_scalar", "ref_avg_pos", "ref_tgt_pos"],
            title="Equal-weight reference positions",
            ylabel="Continuous contracts",
        ) * hv.HLine(0.0).opts(color="black", line_width=1)
        normalized_plot = line_panel(
            reference,
            ["comb_fcst", "ref_norm_pos"],
            title="Forecast versus reference-normalized position",
            ylabel="Smoothed average-position units",
        ) * hv.HLine(0.0).opts(color="black", line_width=1)

        reference_curves = self.subsystem_reference_pct_curves(
            start=start, end=end
        )
        reference_plot = line_panel(
            reference_curves,
            ["cum_ref_gross_pct", "cum_ref_net_pct"],
            title="Cumulative equal-weight subsystem-reference percentage",
            ylabel="Percent",
        )
        panels = (
            price_plot
            + forecast_plot
            + proxy_plot
            + normalized_plot
            + reference_plot
        )

        if self.cash is not None:
            cash = _date_slice(self.cash, start=start, end=end)
            contract_plot = line_panel(
                cash,
                ["tgt_pos", "exec_con", "held_con"],
                title="Executable contract positions",
                ylabel="Contracts",
            )
            cash_curves = self.cash_pnl_curves(start=start, end=end)
            cash_plot = line_panel(
                cash_curves,
                ["cum_gross_pnl", "cum_net_pnl"],
                title="Cumulative executable cash P&L",
                ylabel="USD",
            )
            panels = panels + contract_plot + cash_plot

        return panels.cols(1).opts(shared_axes=False)


def build_ewmac_notebook_view(
    result: CombinedForecastResult,
    *,
    symbol: str,
    multiplier: float,
    reference_settings: SubsystemReferenceSettings,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    fx_to_usd: float | None = None,
    ann_cost_sr: float = 0.0,
    cash_settings: ExecutableCashSettings | None = None,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
) -> EwmacNotebookView:
    """Build aligned notebook diagnostics from one combined forecast result.

    Parameters
    ----------
    result
        Output from :func:`combine_ewmac_forecasts` or
        :meth:`CombinedForecastEngine.combine`.
    symbol
        Executable instrument identity used in plot titles and view metadata.
    start_date
        Optional inclusive calendar-date start for the forecast and all
        derived notebook curves.
    end_date
        Optional inclusive calendar-date end for the forecast and all derived
        notebook curves.
    multiplier
        Native-currency value of one price point for the actual instrument.
    fx_to_usd
        Optional constant native-currency-to-USD conversion. If omitted, use
        the forecast frame's causal ``fx_to_usd`` series when present, or the
        identity rate for USD frames without FX data.
    ann_cost_sr
        Annual subsystem cost in SR units for the reference return path.
    reference_settings
        Equal-weight book assumption used to size the continuous candidate
        reference return. This is independent of final portfolio membership.
    cash_settings
        Optional account sizing and per-contract execution costs. When absent,
        no claim is made about executable contracts or cash P&L.
    target_abs_forecast
        Forecast value corresponding to one average position. The project
        default is 1.0; explicit alternative forecast scales remain supported.

    Returns
    -------
    EwmacNotebookView
        The original forecast result plus aligned subsystem-reference return
        and optional portfolio cash P&L frames and a reusable hvPlot dashboard.
    """
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be finite and positive")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")

    if start_date is not None or end_date is not None:
        # Trim the source before calculating positions so every derived path
        # has the same inclusive date boundaries.
        result = replace(
            result,
            frame=_date_slice(result.frame, start=start_date, end=end_date),
        )

    forecast = _forecast_with_fx(result, fx_to_usd)
    if "contract_close" not in forecast.columns and "close" in forecast.columns:
        # Direct, unstitched IB histories carry the executable mark as close.
        # Stitched histories retain their separate raw mark column, because
        # their close may be an additive-Panama signal price.
        forecast = forecast.with_columns(
            pl.col("close").alias("contract_close")
        )
    reference = build_subsystem_reference_return_curve(
        forecast,
        capital=reference_settings.capital,
        target_vol=reference_settings.target_vol,
        reference_book_count=reference_settings.reference_book_count,
        reference_idm=reference_settings.reference_idm,
        max_weight_idm=reference_settings.max_weight_idm,
        multiplier=multiplier,
        ann_cost_sr=ann_cost_sr,
        target_abs_forecast=target_abs_forecast,
    )
    reference, reference_turnover = append_reference_position_turnover(
        reference
    )
    cash = None
    if cash_settings is not None:
        cash = build_executable_cash_pnl_curve(
            forecast,
            capital=cash_settings.capital,
            target_vol=cash_settings.target_vol,
            weight=cash_settings.weight,
            idm=cash_settings.idm,
            multiplier=multiplier,
            trade_cost_usd_per_con=cash_settings.trade_cost_usd_per_con,
            roll_cost_usd_per_con=cash_settings.roll_cost_usd_per_con,
            target_abs_forecast=target_abs_forecast,
            buffer_size=cash_settings.buffer_size,
            buffer_trade_to_edge=cash_settings.buffer_trade_to_edge,
            rebalance_on_roll=cash_settings.rebalance_on_roll,
        )
    # This facade is consumed directly in notebooks, so expose its aligned
    # audit frames with the same compact, centralized vocabulary as reports.
    # Keep the underlying calculation frames descriptive and full precision.
    reference = rename_public_report_columns(reference)
    if cash is not None:
        cash = rename_public_report_columns(cash)
    return EwmacNotebookView(
        symbol=symbol,
        result=result,
        reference=reference,
        reference_turnover=reference_turnover,
        cash=cash,
    )


def build_ewmac_notebook_view_from_screen(
    result: CombinedForecastResult,
    *,
    screen: pl.DataFrame | Path | str,
    symbol: str,
    reference_settings: SubsystemReferenceSettings,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    cash_settings: ExecutableCashSettings | None = None,
    fx_to_usd: float | None = None,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
) -> EwmacNotebookView:
    """Build reference and optional cash curves using a Phase 1 screen row.

    The screen supplies executable contract multiplier, one-contract trading
    and roll costs, SR-normalized costs, and annual rolls. The combined
    forecast remains the supplied time series, so an ES signal can be sized
    with a MES screen row. Account capital, target volatility, weight, IDM, and
    buffer policy stay explicit in the settings objects.

    ``screen`` may be a typed Parquet report (preferred), a legacy CSV report,
    or an already-loaded Polars dataframe. A market snapshot alone is not
    sufficient for costed curves because it does not contain the selection
    screen's annual SR cost rates and roll-policy cost assumptions.

    Parameters
    ----------
    result
        Combined forecast history to use as the strategy signal.
    start_date
        Optional inclusive calendar-date start for the forecast and all
        derived notebook curves.
    end_date
        Optional inclusive calendar-date end for the forecast and all derived
        notebook curves.
    screen
        Phase 1 selection-screen frame or path to its Parquet/CSV report.
    symbol
        Exact executable symbol used to select the screen row and label the view.
    reference_settings
        Equal-weight subsystem-reference sizing assumptions.
    cash_settings
        Optional account-level settings. When provided, per-contract trade
        and roll costs are taken from the matched screen row rather than the
        settings object's cost defaults. If account-specific costs are null,
        the helper uses the screen's explicitly labelled snapshot/configured
        trade estimate and estimates a calendar-spread roll as spread plus two
        reference commissions; the output frames identify those sources.
    fx_to_usd
        Optional constant FX override. Omit to preserve a causal FX series
        already attached to ``result.frame``.
    target_abs_forecast
        Forecast value corresponding to one average position.

    Returns
    -------
    EwmacNotebookView
        Compact forecast, costed reference, optional executable cash frames,
        and plotting methods.
    """
    if start_date is not None or end_date is not None:
        # Screen-derived annual cost uses the same sample as the displayed
        # curves, so estimate turnover only over the requested date range.
        result = replace(
            result,
            frame=_date_slice(result.frame, start=start_date, end=end_date),
        )

    screen_frame = _read_screen_frame(screen)
    screen_row = _screen_row(screen_frame, symbol)
    multiplier = _screen_number(
        screen_row,
        ("mult", "multiplier", "ib_mult"),
        label="contract multiplier",
    )
    annual_rolls = _screen_number(
        screen_row,
        ("ref_ann_rolls", "ann_rolls"),
        label="annual roll count",
    )
    ann_dvol = _screen_optional_number(
        screen_row, ("ann_dvol", "annual_dollar_vol_per_contract")
    )
    trade_cost = _screen_optional_number(
        screen_row,
        ("cost_usd", "trade_cost_per_con", "trade_cost_usd_per_con"),
    )
    trade_cost_src = "screen_cost"
    if trade_cost is None:
        # The account-specific IB WhatIf commission may be unavailable (for
        # example, when the test order fails its margin check). Preserve the
        # screen's explicitly labelled snapshot/configured estimate as a
        # research fallback instead of silently treating it as a WhatIf cost.
        trade_cost = _screen_number(
            screen_row,
            ("snap_ref_config_cost_usd",),
            label="one-way snapshot/configured trade cost per contract in USD",
        )
        trade_cost_src = "snap_ref_config"

    roll_cost = _screen_optional_number(
        screen_row,
        ("roll_cost_usd", "roll_cost_per_con", "roll_cost_usd_per_con"),
    )
    roll_cost_src = "screen_roll_cost"
    if roll_cost is None:
        spread_cash = _screen_optional_number(
            screen_row, ("snap_spread_cash_usd",)
        )
        reference_commission = _screen_optional_number(
            screen_row, ("ref_config_comm_usd",)
        )
        if spread_cash is None or reference_commission is None:
            raise ValueError(
                "screen row has no finite roll cost; expected a roll-cost "
                "field or snapshot spread cash plus reference commission"
            )
        # Calendar-spread roll estimate: spread crossing plus two sides of
        # the explicitly labelled reference commission.
        roll_cost = spread_cash + 2.0 * reference_commission
        roll_cost_src = "snap_ref_config_calendar_spread"

    trade_sr = _screen_optional_number(screen_row, ("trade_sr",))
    if trade_sr is None:
        if ann_dvol is None or ann_dvol <= 0:
            raise ValueError(
                "screen row has no finite trade_sr or positive ann_dvol "
                "to derive it from the selected trade cost"
            )
        trade_sr = trade_cost / ann_dvol
    roll_sr = _screen_optional_number(screen_row, ("roll_sr",))
    if roll_sr is None:
        if ann_dvol is None or ann_dvol <= 0:
            raise ValueError(
                "screen row has no finite roll_sr or positive ann_dvol "
                "to derive it from the selected roll cost"
            )
        roll_sr = roll_cost / ann_dvol

    forecast = _forecast_with_fx(result, fx_to_usd)
    reference = build_subsystem_reference_return_curve(
        forecast,
        capital=reference_settings.capital,
        target_vol=reference_settings.target_vol,
        reference_book_count=reference_settings.reference_book_count,
        reference_idm=reference_settings.reference_idm,
        max_weight_idm=reference_settings.max_weight_idm,
        multiplier=multiplier,
        ann_cost_sr=0.0,
        target_abs_forecast=target_abs_forecast,
    )
    _, turnover = append_reference_position_turnover(reference)
    if turnover is None or not math.isfinite(turnover):
        raise ValueError(
            f"cannot estimate reference turnover for {symbol!r}"
        )
    annual_cost = calculate_annual_sr_cost(
        trade_sr=trade_sr,
        roll_sr=roll_sr,
        subsystem_turnover=turnover,
        ann_rolls=annual_rolls,
    )

    if cash_settings is not None:
        cash_settings = replace(
            cash_settings,
            trade_cost_usd_per_con=trade_cost,
            roll_cost_usd_per_con=roll_cost,
        )

    view = build_ewmac_notebook_view(
        result,
        symbol=symbol,
        multiplier=multiplier,
        reference_settings=reference_settings,
        start_date=start_date,
        end_date=end_date,
        fx_to_usd=fx_to_usd,
        ann_cost_sr=annual_cost.ann_cost_sr,
        cash_settings=cash_settings,
        target_abs_forecast=target_abs_forecast,
    )
    # Retain cost provenance next to the per-contract costs in notebook frames;
    # snapshot estimates must remain distinguishable from account WhatIf data.
    reference = view.reference.with_columns(
        pl.lit(trade_cost_src).alias("trade_cost_src"),
        pl.lit(roll_cost_src).alias("roll_cost_src"),
    )
    cash = (
        view.cash.with_columns(
            pl.lit(trade_cost_src).alias("trade_cost_src"),
            pl.lit(roll_cost_src).alias("roll_cost_src"),
        )
        if view.cash is not None
        else None
    )
    return replace(view, reference=reference, cash=cash)


def build_ib_ewmac_notebook_view(
    ib: IBPySync,
    *,
    symbol: str,
    reference_settings: SubsystemReferenceSettings,
    exchange: str = "SMART",
    currency: str = "USD",
    duration: str = DEFAULT_IB_DURATION,
    bar_size: str = DEFAULT_IB_BAR_SIZE,
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
    fdm: float = 1.0,
    combination_config: ForecastCombinationConfig | None = None,
    multiplier: float = 1.0,
    fx_to_usd: float | None = None,
    ann_cost_sr: float = 0.0,
    cash_settings: ExecutableCashSettings | None = None,
    screen: pl.DataFrame | Path | str | None = None,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
) -> EwmacNotebookView:
    """Download one IB stock history and build its unstitched EWMAC view.

    The downloaded daily close is both the signal price and the executable
    notional mark. No reference or Globex data is loaded or stitched. Forecasts
    are calculated on the full downloaded sample before optional display
    boundaries are applied, preserving indicator warmup before ``start_date``.

    Parameters
    ----------
    ib
        Connected synchronous IB client.
    symbol
        Stock ticker used for the IB contract and notebook label.
    reference_settings
        Equal-weight subsystem reference sizing assumptions.
    exchange, currency
        IB stock contract routing and quote currency.
    duration, bar_size, what_to_show, use_rth
        Historical bar request settings forwarded to IB.
    rules, fdm, combination_config
        EWMAC components and single-instrument combination conventions.
    multiplier
        Native currency value of one price point per share; normally 1.0.
        A matched Phase 1 ``screen`` row may supply its own multiplier.
    fx_to_usd
        Optional constant currency conversion. Omit for USD stocks, where the
        view uses identity FX.
    ann_cost_sr, cash_settings, screen
        Reference cost and optional executable cash assumptions. When
        ``screen`` is given, its multiplier and trade/roll costs are used.
    start_date, end_date
        Optional inclusive output boundaries. Forecast indicators use all
        downloaded history so rows before ``start_date`` still warm them up.

    Returns
    -------
    EwmacNotebookView
        Combined forecasts, reference sizing, optional cash P&L, and plotting
        methods. ``view.contract_history`` contains the downloaded IB bars.
    """
    contract = IBPySync.stock(
        symbol,
        exchange=exchange,
        currency=currency,
    )
    qualified = ib.qualify_contracts(contract)
    if qualified:
        contract = qualified[0]
    bars = ib.get_historical_bars(
        contract,
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=use_rth,
    )
    if bars is None or bars.is_empty():
        raise ValueError(f"IB returned no historical bars for {symbol}")
    date_column = "date" if "date" in bars.columns else "ts_event"
    if date_column not in bars.columns or "close" not in bars.columns:
        raise ValueError(
            "IB stock history must contain a date/ts_event column and close"
        )
    history = (
        bars.select(
            pl.col(date_column).cast(pl.Date).alias("date"),
            pl.col("close").cast(pl.Float64),
        )
        .filter(pl.col("close").is_finite() & (pl.col("close") > 0))
        .unique(subset="date", keep="last", maintain_order=True)
        .sort("date")
        .with_columns(pl.col("close").alias("contract_close"))
    )
    if end_date is not None:
        history = _date_slice(history, start=None, end=end_date)
    if history.is_empty():
        raise ValueError(f"IB history has no valid closes for {symbol}")

    config = combination_config or ForecastCombinationConfig()
    result = combine_ewmac_forecasts(
        history,
        rules=rules,
        instrument_code=symbol,
        fdm=fdm,
        config=config,
    )
    if screen is not None:
        view = build_ewmac_notebook_view_from_screen(
            result,
            screen=screen,
            symbol=symbol,
            reference_settings=reference_settings,
            start_date=start_date,
            end_date=end_date,
            cash_settings=cash_settings,
            fx_to_usd=fx_to_usd,
            target_abs_forecast=config.target_abs_forecast,
        )
    else:
        view = build_ewmac_notebook_view(
            result,
            symbol=symbol,
            multiplier=multiplier,
            reference_settings=reference_settings,
            start_date=start_date,
            end_date=end_date,
            fx_to_usd=fx_to_usd,
            ann_cost_sr=ann_cost_sr,
            cash_settings=cash_settings,
            target_abs_forecast=config.target_abs_forecast,
        )
    return replace(view, contract_history=bars, close_label="Close")


def _history_currency(history: FuturesHistory) -> str:
    """Return the reference segment's currency from stitched metadata."""
    metadata = history.metadata.get("source_metadata", [])
    if isinstance(metadata, list):
        for source_metadata in metadata:
            if not isinstance(source_metadata, dict):
                continue
            currency = str(source_metadata.get("currency") or "").strip()
            if currency:
                return currency
    raise ValueError("stitched history has no reference currency metadata")


def _resolve_stitched_symbol_mappings(
    instrument_rows: list[dict],
    *,
    symbol: str,
    signal_symbol: str | None,
) -> tuple[dict, dict]:
    """Resolve executable and signal identities without conflating their roles.

    Public executable ``symbol`` selects contract sizing and the view label.
    ``signal_symbol`` selects a broker-mapped history instrument; its PST code
    owns the historical price series and roll policy.
    """
    executable_rows = [row for row in instrument_rows if row["symbol"] == symbol]
    if len(executable_rows) != 1:
        raise KeyError(f"No unique executable-symbol mapping for {symbol!r}")
    executable = executable_rows[0]
    resolved_signal_symbol = signal_symbol or executable["signal_symbol"]
    signal_rows = [
        row for row in instrument_rows if row["symbol"] == resolved_signal_symbol
    ]
    if not signal_rows:
        signal_rows = [
            row for row in instrument_rows if row["ib_symbol"] == resolved_signal_symbol
        ]
    if len(signal_rows) != 1:
        raise KeyError(
            f"signal_symbol {resolved_signal_symbol!r} does not resolve to one "
            "PST/IB history mapping; pass an unambiguous IB signal symbol"
        )
    return executable, signal_rows[0]


def build_stitched_ewmac_notebook_view(
    ib: IBPySync,
    *,
    symbol: str,
    reference_settings: SubsystemReferenceSettings,
    cache_root: Path | str,
    signal_symbol: str | None = None,
    as_of: date | None = None,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    prior_years: int = 1,
    max_source_gap_days: int = DEFAULT_MAX_SOURCE_GAP_DAYS,
    duration: str = DEFAULT_IB_DATED_CONTRACT_DURATION,
    bar_size: str = DEFAULT_IB_BAR_SIZE,
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    rules: Iterable[EwmacRule] = CANONICAL_EWMAC_RULES,
    fdm: float = 1.0,
    combination_config: ForecastCombinationConfig | None = None,
    multiplier: float | None = None,
    ann_cost_sr: float = 0.0,
    cash_settings: ExecutableCashSettings | None = None,
    screen: pl.DataFrame | Path | str | None = None,
    reference_db_path: Path | str = DEFAULT_REFERENCE_DB_PATH,
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH,
    broker_mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH,
    ib_history_db_path: Path | str = DEFAULT_IB_FUTURES_HISTORY_DB_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    use_cache: bool = True,
    save_cache: bool = True,
    update_ib_history: bool = True,
) -> EwmacNotebookView:
    """Build a complete stitched-history EWMAC notebook view in one call.

    The helper downloads overlapping dated contracts from IB, constructs the
    configured held-contract path, and stitches it over the long reference
    history, with a Globex middle segment when the reviewed crosswalk provides
    one. It then loads the causal pooled EWMAC scalars, builds every requested
    component, aligns historical FX, combines the forecasts, and delegates
    sizing and plotting to the standard notebook view.

    Parameters
    ----------
    ib
        Connected synchronous IB client.
    symbol
        Executable instrument key, such as ``MGC``. It owns the screen lookup,
        multiplier, cash sizing, and view label.
    reference_settings
        Equal-weight subsystem reference sizing assumptions.
    cache_root
        Explicit root for cached PySystemTrade, Globex, and stitched histories.
        No project-local cache path is selected implicitly.
    signal_symbol
        IB ticker used to download the newest signal history. Defaults to the
        mapped signal ticker for ``symbol``. For example, ``MGC`` can use
        ``GC`` signal history while retaining the micro contract's sizing.
        The corresponding PST history code is resolved from the IB mapping;
        callers do not pass a PST code as an executable/display identity.
    as_of, prior_years, duration, bar_size, what_to_show, use_rth
        Dated-contract download settings forwarded to the IB history loader.
        When no Globex segment is available, the provider requests older
        contract years while they extend IB history toward the end of the long
        reference series.
    start, end
        Optional inclusive date limits for the returned forecast, reference,
        and cash curves. History acquisition and forecast warm-up still use
        the full stitched history.
    max_source_gap_days
        Largest calendar gap permitted for a direct reference-to-IB stitch.
        The unknown return over an allowed gap is masked and the interval is
        recorded in ``view.history.metadata["source_gaps"]``. Zero requires
        source overlap.
    rules
        EWMAC rules to normalize, calculate, and combine.
    fdm, combination_config
        Fixed single-market forecast diversification and numeric conventions.
    multiplier
        Optional native-currency point-value override. When absent, infer it
        from the executable ``symbol`` mapping. The signal contract multiplier
        is never substituted for the traded contract's point value.
    ann_cost_sr, cash_settings
        Inputs for the standard reference and optional executable cash paths.
    screen
        Optional Phase 1 screen input, matched by executable ``symbol``. The
        screen costs and multiplier replace ``ann_cost_sr`` and ``multiplier``.
    reference_db_path, globex_db_path, broker_mapping_path
        Read-only history, roll configuration, and broker mapping inputs.
    ib_history_db_path
        Dedicated writable DuckDB for raw IB dated-contract bars.
    pooling_mapping_path
        Reviewed pooling decisions used by causal scalar normalization.
    use_cache, save_cache
        Read and write controls for the stitched Parquet view and underlying
        reference/Globex caches. Set ``use_cache=False`` and
        ``save_cache=True`` to refresh the stitched view.
    update_ib_history
        Fetch current or missing raw bars into ``ib_history_db_path`` on a
        stitched-cache miss. False rebuilds only from stored raw bars.

    Returns
    -------
    EwmacNotebookView
        Combined forecasts, reference sizing, optional cash P&L, plotting
        methods, the stitched history in ``view.history``, and the overlapping
        IB rows in ``view.contract_history``.
    """
    rule_tuple = tuple(rules)
    if not rule_tuple:
        raise ValueError("at least one EWMAC rule is required")
    config = combination_config or ForecastCombinationConfig()

    instrument_rows = load_pysystemtrade_ib_instruments(
        db_path=reference_db_path,
        mapping_path=broker_mapping_path,
    )
    executable, signal_mapping = _resolve_stitched_symbol_mappings(
        instrument_rows,
        symbol=symbol,
        signal_symbol=signal_symbol,
    )
    pst_instr_code = signal_mapping["pst_instr_code"]
    history_provider = IBStitchedHistoryProvider(
        ib=ib,
        as_of=as_of,
        prior_years=prior_years,
        max_source_gap_days=max_source_gap_days,
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=use_rth,
        reference_db_path=reference_db_path,
        globex_db_path=globex_db_path,
        mapping_path=broker_mapping_path,
        history_db_path=ib_history_db_path,
        cache_root=cache_root,
        use_cache=use_cache,
        save_cache=save_cache,
        update_history=update_ib_history,
    )
    history = history_provider.load(
        symbol,
        signal_symbol=signal_mapping["symbol"],
    )
    contracts = history_provider.contract_history
    if contracts is None:
        raise RuntimeError("stitched history provider did not retain IB audit rows")
    pooled = load_pooled_ewmac_rules(
        db_path=reference_db_path,
        cache_root=cache_root,
        pooling_mapping_path=pooling_mapping_path,
        rules=rule_tuple,
        target_abs_forecast=config.target_abs_forecast,
        forecast_cap=config.forecast_cap,
    )
    components = build_ewmac_component_frame(
        history,
        pooled,
        detailed=True,
    )
    components = align_fx_to_history(
        components,
        _history_currency(history),
        db_path=reference_db_path,
    )
    rule_keys = [rule.key for rule in rule_tuple]
    result = CombinedForecastEngine(
        diversification_policy=FixedForecastDiversification(fdm),
        config=config,
    ).combine(
        components,
        rule_keys,
        np.eye(len(rule_keys)),
        rule_keys,
    )

    if screen is not None:
        view = build_ewmac_notebook_view_from_screen(
            result,
            screen=screen,
            symbol=symbol,
            reference_settings=reference_settings,
            cash_settings=cash_settings,
            target_abs_forecast=config.target_abs_forecast,
            start_date=start,
            end_date=end,
        )
    else:
        view = build_ewmac_notebook_view(
            result,
            symbol=symbol,
            multiplier=(
                float(executable["multiplier"]) if multiplier is None else multiplier
            ),
            ann_cost_sr=ann_cost_sr,
            reference_settings=reference_settings,
            cash_settings=cash_settings,
            target_abs_forecast=config.target_abs_forecast,
            start_date=start,
            end_date=end,
        )
    return replace(
        view,
        history=history,
        contract_history=contracts,
    )

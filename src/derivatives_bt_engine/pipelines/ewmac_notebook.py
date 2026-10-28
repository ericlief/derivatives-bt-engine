"""Notebook diagnostics for EWMAC reference percentages and portfolio cash P&L.

This module is the interactive inspection boundary after forecast combination.
It accepts the same :class:`CombinedForecastResult` used by Phase 2, adds the
equal-weight subsystem-reference percentage and optional executable cash P&L
paths, and exposes small Polars tables plus a lazy hvPlot dashboard. A helper
can join a saved Phase 1 selection screen for contract and cost inputs. It does
not reimplement forecast, sizing, buffering, or cost calculations; those
remain owned by the calculation modules used by production selection. The
stitched-history entry point also orchestrates dated-contract loading, history
ownership, pooled forecast construction, and this same view boundary. The
selected-book cash-P&L plotting helper reads saved multi-symbol Parquet reports
and lays out comparable execution diagnostics without merging their scales.
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
from derivatives_bt_engine.data.futures_overlap import resolve_reviewed_globex_asset
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH as DEFAULT_REFERENCE_DB_PATH,
    FuturesHistory,
)
from derivatives_bt_engine.data.ib_futures_dataloader import (
    DEFAULT_BROKER_MAPPING_PATH,
    DEFAULT_IB_BAR_SIZE,
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
    P&L/cost, cumulative P&L, and cumulative-return panels. The wider panels
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
    holoviews.DynamicMap
        A widget-selectable dashboard with five independently scaled panels
        for the chosen symbol.
    """
    import holoviews as hv
    import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API
    hv.extension("bokeh")

    if isinstance(cash_pnl, (str, Path)):
        frame = pl.read_parquet(Path(cash_pnl).expanduser())
    else:
        frame = cash_pnl
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("cash_pnl must be a Polars DataFrame or Parquet path")
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
    ):
        """Build one independently scaled line panel from available fields."""
        available = [name for name in columns if name in symbol_frame.columns]
        return symbol_frame.hvplot.line(
            x="date",
            y=available,
            title=title,
            ylabel=ylabel,
            width=width,
            height=height,
            tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset", "save"],
        )

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
                columns=[
                    "tgt_pos", "buffer_top_con", "buffer_bot_con",
                    "exec_con", "held_con",
                ],
                title=f"{symbol} target, buffer, and held contracts",
                ylabel="Contracts",
            ),
            line_panel(
                symbol_frame,
                columns=["gross_pnl", "net_pnl", "trade_cost", "roll_cost"],
                title=f"{symbol} daily P&L and costs",
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
        dashboard = panels[0]
        for panel in panels[1:]:
            dashboard = dashboard + panel
        return dashboard.cols(1).opts(shared_axes=False)

    symbol_dimension = hv.Dimension("symbol", values=selected_symbols)
    return hv.DynamicMap(plot_symbol, kdims=[symbol_dimension])


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
    tie rule. The resulting USD series is divided by ``ref_budget`` and
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
            "ref_budget",
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

        Each percentage-point return divides this instrument's
        executable cash flow by total account capital and multiplies by 100.
        Contributions from all selected instruments therefore add to the
        corresponding portfolio percentage return. Cost columns are negative
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

        Daily ``net_pct`` values are percentage-point contributions relative
        to fixed account capital. The annual mean is cumulative net percentage
        divided by elapsed calendar years; annual volatility scales the daily
        sample standard deviation by ``sqrt(annualization_days)``. The reported
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

        Panels show the adjusted signal price, combined forecast, sized
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

        price_plot = reference.hvplot.line(
            x="date",
            y="close",
            title=f"{self.symbol} adjusted signal price",
            ylabel="Price points",
            width=width,
            height=height,
        )
        forecast_plot = reference.hvplot.line(
            x="date",
            y="comb_fcst",
            title="Combined forecast (mean abs 1, cap 2)",
            ylabel="Average-position multiple",
            width=width,
            height=height,
        ) * hv.HLine(0.0).opts(color="black", line_width=1)
        proxy_plot = reference.hvplot.line(
            x="date",
            y=[
                "vol_scalar",
                "ref_avg_pos",
                "ref_tgt_pos",
            ],
            title="Equal-weight reference positions",
            ylabel="Continuous contracts",
            width=width,
            height=height,
        )
        normalized_plot = reference.hvplot.line(
            x="date",
            y=["comb_fcst", "ref_norm_pos"],
            title="Forecast versus reference-normalized position",
            ylabel="Smoothed average-position units",
            width=width,
            height=height,
        ) * hv.HLine(0.0).opts(color="black", line_width=1)

        reference_curves = self.subsystem_reference_pct_curves(
            start=start, end=end
        )
        reference_plot = reference_curves.hvplot.line(
            x="date",
            y=["cum_ref_gross_pct", "cum_ref_net_pct"],
            title="Cumulative equal-weight subsystem-reference percentage",
            ylabel="Percent",
            width=width,
            height=height,
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
            contract_plot = cash.hvplot.line(
                x="date",
                y=["tgt_pos", "exec_con", "held_con"],
                title="Executable contract positions",
                ylabel="Contracts",
                width=width,
                height=height,
            )
            cash_curves = self.cash_pnl_curves(start=start, end=end)
            cash_plot = cash_curves.hvplot.line(
                x="date",
                y=[
                    "cum_gross_pnl",
                    "cum_net_pnl",
                ],
                title="Cumulative executable cash P&L",
                ylabel="USD",
                width=width,
                height=height,
            )
            panels = panels + contract_plot + cash_plot

        return panels.cols(1).opts(shared_axes=False)


def build_ewmac_notebook_view(
    result: CombinedForecastResult,
    *,
    symbol: str,
    multiplier: float,
    reference_settings: SubsystemReferenceSettings,
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

    forecast = _forecast_with_fx(result, fx_to_usd)
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


def _resolve_globex_asset(pst_instr_code: str) -> str | None:
    """Resolve an optional Globex root from the reviewed crosswalk.

    A missing row means that the middle Globex segment is unavailable. The
    stitched provider can still join long reference history directly to the
    recent IB segment. Multiple usable rows remain an error because source
    ownership would be ambiguous.
    """
    return resolve_reviewed_globex_asset(pst_instr_code)


def build_stitched_ewmac_notebook_view(
    ib: IBPySync,
    *,
    symbol: str,
    reference_settings: SubsystemReferenceSettings,
    signal_symbol: str | None = None,
    as_of: date | None = None,
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
    globex_asset = _resolve_globex_asset(pst_instr_code)
    history_provider = IBStitchedHistoryProvider(
        ib=ib,
        pst_instr_code=pst_instr_code,
        globex_asset=globex_asset,
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
        use_cache=use_cache,
        save_cache=save_cache,
        update_history=update_ib_history,
    )
    history = history_provider.load(symbol)
    contracts = history_provider.contract_history
    if contracts is None:
        raise RuntimeError("stitched history provider did not retain IB audit rows")
    pooled = load_pooled_ewmac_rules(
        db_path=reference_db_path,
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
        )
    return replace(
        view,
        history=history,
        contract_history=contracts,
    )

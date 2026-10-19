"""Notebook diagnostics for combined EWMAC forecasts, positions, and P&L.

This module is the interactive inspection boundary after forecast combination.
It accepts the same :class:`CombinedForecastResult` used by Phase 2, adds the
normalized subsystem P&L and optional executable cash P&L paths, and exposes
small Polars tables plus a lazy hvPlot dashboard.  It does not reimplement
forecast, sizing, buffering, or cost calculations; those remain owned by the
calculation modules used by production selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

import polars as pl

from derivatives_bt_engine.calculations.ewmac import EWMAC_FORECAST_TARGET_ABS
from derivatives_bt_engine.calculations.forecast_combination import (
    CombinedForecastResult,
)
from derivatives_bt_engine.calculations.pnl import (
    PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
    build_executable_cash_pnl_curve,
    build_subsystem_pnl_curve,
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


def _attach_fx(frame: pl.DataFrame, fx_to_usd: float) -> pl.DataFrame:
    """Attach one constant native-currency-to-USD rate for notebook research."""
    if not math.isfinite(fx_to_usd) or fx_to_usd <= 0:
        raise ValueError("fx_to_usd must be finite and positive")
    return frame.with_columns(pl.lit(float(fx_to_usd)).alias("fx_to_usd"))


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


@dataclass(frozen=True)
class EwmacNotebookView:
    """Hold aligned forecast, subsystem-P&L, and optional cash-P&L frames.

    ``forecasts`` contains the detailed combined-forecast result. ``pnl`` is
    the scale-free subsystem path used for correlations. ``cash`` is present
    only when executable account settings were supplied and contains actual
    continuous targets, buffered whole contracts, and cash costs.
    """

    instrument_code: str
    result: CombinedForecastResult
    pnl: pl.DataFrame
    cash: pl.DataFrame | None = None

    @property
    def forecasts(self) -> pl.DataFrame:
        """Return the full combined-forecast and position audit frame."""
        return self.result.frame

    def forecast_positions(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return the principal forecast and relative-position diagnostics."""
        frame = _date_slice(self.forecasts, start=start, end=end)
        columns = [
            "date",
            "close",
            "combined_forecast",
            "avg_position",
            "smooth_avg_position",
            "subsystem_position",
            "normalized_position",
        ]
        available_columns = [
            column for column in columns if column in frame.columns
        ]
        return frame.select(available_columns)

    def subsystem_pnl_curves(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return cumulative gross, cost, and net subsystem P&L in USD units."""
        frame = _date_slice(self.pnl, start=start, end=end)
        return frame.select(
            "date",
            pl.col("gross_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_gross_pnl_usd"),
            pl.col("cost_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cost_pnl_usd"),
            pl.col("net_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_net_pnl_usd"),
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
            pl.col("cash_gross_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_gross_pnl_usd"),
            pl.col("cash_expected_net_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_expected_net_pnl_usd"),
            pl.col("cash_realized_net_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_realized_net_pnl_usd"),
        )

    def plot(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        width: int = 950,
        height: int = 240,
    ):
        """Return a vertically linked hvPlot dashboard for notebook display.

        Panels show the adjusted signal price, combined forecast, position
        proxies, volatility-normalized position, cumulative subsystem P&L,
        and—when configured—whole-contract positions and cumulative cash P&L.
        hvPlot is imported lazily so non-notebook pipelines do not initialize a
        plotting backend.
        """
        import holoviews as hv
        import hvplot.polars  # noqa: F401 - registers Polars' ``hvplot`` API

        forecast = _date_slice(self.forecasts, start=start, end=end)
        if forecast.is_empty():
            raise ValueError("plot date range contains no forecast observations")

        price_plot = forecast.hvplot.line(
            x="date",
            y="close",
            title=f"{self.instrument_code} adjusted signal price",
            ylabel="Price points",
            width=width,
            height=height,
        )
        forecast_plot = forecast.hvplot.line(
            x="date",
            y="combined_forecast",
            title="Combined forecast (mean abs 1, cap 2)",
            ylabel="Average-position multiple",
            width=width,
            height=height,
        ) * hv.HLine(0.0).opts(color="black", line_width=1)
        proxy_plot = forecast.hvplot.line(
            x="date",
            y=["avg_position", "smooth_avg_position", "subsystem_position"],
            title="Relative subsystem position proxies",
            ylabel="Inverse-point-vol units",
            width=width,
            height=height,
        )
        normalized_plot = forecast.hvplot.line(
            x="date",
            y=["combined_forecast", "normalized_position"],
            title="Forecast versus volatility-normalized position",
            ylabel="Smoothed average-position units",
            width=width,
            height=height,
        ) * hv.HLine(0.0).opts(color="black", line_width=1)

        subsystem_curves = self.subsystem_pnl_curves(start=start, end=end)
        pnl_plot = subsystem_curves.hvplot.line(
            x="date",
            y=["cum_gross_pnl_usd", "cum_net_pnl_usd"],
            title="Cumulative subsystem P&L (relative-risk scale)",
            ylabel="USD-scaled units",
            width=width,
            height=height,
        )
        panels = price_plot + forecast_plot + proxy_plot + normalized_plot + pnl_plot

        if self.cash is not None:
            cash = _date_slice(self.cash, start=start, end=end)
            contract_plot = cash.hvplot.line(
                x="date",
                y=["target_position", "exec_cons", "held_cons"],
                title="Executable contract positions",
                ylabel="Contracts",
                width=width,
                height=height,
            )
            cash_curves = self.cash_pnl_curves(start=start, end=end)
            cash_plot = cash_curves.hvplot.line(
                x="date",
                y=[
                    "cum_cash_gross_pnl_usd",
                    "cum_cash_expected_net_pnl_usd",
                    "cum_cash_realized_net_pnl_usd",
                ],
                title="Cumulative executable cash P&L",
                ylabel="USD",
                width=width,
                height=height,
            )
            panels = panels + contract_plot + cash_plot

        return panels.cols(1)


def build_ewmac_notebook_view(
    result: CombinedForecastResult,
    *,
    instrument_code: str,
    multiplier: float,
    fx_to_usd: float = 1.0,
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
    instrument_code
        Display identity used in plot titles.
    multiplier
        Native-currency value of one price point for the actual instrument.
    fx_to_usd
        Constant native-currency-to-USD conversion. Use 1.0 for USD assets.
        Historical portfolio research should continue to use the Phase 2 FX
        loader rather than treating this notebook convenience as dated FX.
    ann_cost_sr
        Annual subsystem cost in SR units for the relative-risk P&L path.
    cash_settings
        Optional account sizing and per-contract execution costs. When absent,
        no claim is made about executable contracts or cash P&L.
    target_abs_forecast
        Forecast value corresponding to one average position. The project
        default is 1.0; explicit alternative forecast scales remain supported.

    Returns
    -------
    EwmacNotebookView
        The original forecast result plus aligned subsystem and optional cash
        P&L frames and a reusable hvPlot dashboard.
    """
    if not instrument_code.strip():
        raise ValueError("instrument_code must not be empty")
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be finite and positive")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")

    forecast = _attach_fx(result.frame, fx_to_usd)
    pnl = build_subsystem_pnl_curve(
        forecast,
        multiplier=multiplier,
        ann_cost_sr=ann_cost_sr,
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
        )
    return EwmacNotebookView(
        instrument_code=instrument_code,
        result=result,
        pnl=pnl,
        cash=cash,
    )

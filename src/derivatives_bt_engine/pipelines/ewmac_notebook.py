"""Notebook diagnostics for EWMAC reference percentages and portfolio cash P&L.

This module is the interactive inspection boundary after forecast combination.
It accepts the same :class:`CombinedForecastResult` used by Phase 2, adds the
equal-weight subsystem-reference percentage and optional executable cash P&L
paths, and exposes small Polars tables plus a lazy hvPlot dashboard. It does
not reimplement forecast, sizing, buffering, or cost calculations; those
remain owned by the calculation modules used by production selection.
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
    append_reference_position_turnover,
    build_executable_cash_pnl_curve,
    build_subsystem_reference_return_curve,
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
class SubsystemReferenceSettings:
    """Configure the equal-weight candidate return used for correlations.

    ``capital`` and ``target_vol`` turn daily dollar volatility into an
    auditable continuous position. ``ref_book_count`` assigns every
    candidate the same structural weight, while ``ref_idm`` and
    ``max_weight_idm`` apply the same fixed sizing convention as the selection
    tie rule. The resulting USD series is divided by ``ref_budget_usd`` and
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


@dataclass(frozen=True)
class EwmacNotebookView:
    """Hold forecast, subsystem-reference, and optional cash-P&L frames.

    ``forecasts`` contains the detailed combined-forecast result. ``reference``
    is the equal-weight continuous position and return path used for turnover
    and correlations. ``reference_turnover`` is its annualized Carver-style
    turnover. ``cash`` is present only when executable account settings were
    supplied and contains the selected portfolio's continuous targets,
    buffered whole contracts, and cash costs.
    """

    instrument_code: str
    result: CombinedForecastResult
    reference: pl.DataFrame
    reference_turnover: float | None
    cash: pl.DataFrame | None = None

    @property
    def forecasts(self) -> pl.DataFrame:
        """Return the combined-forecast frame before reference sizing."""
        return self.result.frame

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
            "combined_forecast",
            "ref_average_position",
            "smoothed_ref_average_position",
            "ref_target_position",
            "ref_normalized_position",
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
            "ref_budget_usd",
            "ref_ann_risk_usd",
            "ref_daily_risk_usd",
            "ref_gross_usd",
            "ref_cost_usd",
            "ref_net_usd",
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
            "cum_cash_gross_pnl_usd",
            "cum_cash_expected_net_pnl_usd",
            "cum_cash_realized_net_pnl_usd",
        )

    def cash_return_contribution_curves(
        self,
        *,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> pl.DataFrame:
        """Return daily and cumulative executable portfolio contributions.

        Each percentage-point contribution divides this instrument's
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
            "cash_gross_pct_contrib",
            "cash_trade_cost_pct_contrib",
            "cash_realized_roll_cost_pct_contrib",
            "cash_expected_roll_cost_pct_contrib",
            "cash_realized_cost_pct_contrib",
            "cash_expected_cost_pct_contrib",
            "cash_realized_net_pct_contrib",
            "cash_expected_net_pct_contrib",
            "cum_cash_gross_pct_contrib",
            "cum_cash_trade_cost_pct_contrib",
            "cum_cash_realized_roll_cost_pct_contrib",
            "cum_cash_expected_roll_cost_pct_contrib",
            "cum_cash_realized_cost_pct_contrib",
            "cum_cash_expected_cost_pct_contrib",
            "cum_cash_realized_net_pct_contrib",
            "cum_cash_expected_net_pct_contrib",
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

        Panels show the adjusted signal price, combined forecast, sized
        reference positions, normalized position, cumulative reference
        percentage, and—when configured—whole-contract positions and
        cumulative cash P&L.
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
            title=f"{self.instrument_code} adjusted signal price",
            ylabel="Price points",
            width=width,
            height=height,
        )
        forecast_plot = reference.hvplot.line(
            x="date",
            y="combined_forecast",
            title="Combined forecast (mean abs 1, cap 2)",
            ylabel="Average-position multiple",
            width=width,
            height=height,
        ) * hv.HLine(0.0).opts(color="black", line_width=1)
        proxy_plot = reference.hvplot.line(
            x="date",
            y=[
                "ref_average_position",
                "smoothed_ref_average_position",
                "ref_target_position",
            ],
            title="Equal-weight reference positions",
            ylabel="Continuous contracts",
            width=width,
            height=height,
        )
        normalized_plot = reference.hvplot.line(
            x="date",
            y=["combined_forecast", "ref_normalized_position"],
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
    reference_settings: SubsystemReferenceSettings,
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
    if not instrument_code.strip():
        raise ValueError("instrument_code must not be empty")
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be finite and positive")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")

    forecast = _attach_fx(result.frame, fx_to_usd)
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
        )
    return EwmacNotebookView(
        instrument_code=instrument_code,
        result=result,
        reference=reference,
        reference_turnover=reference_turnover,
        cash=cash,
    )

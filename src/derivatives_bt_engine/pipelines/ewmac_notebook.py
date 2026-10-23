"""Notebook diagnostics for EWMAC reference percentages and portfolio cash P&L.

This module is the interactive inspection boundary after forecast combination.
It accepts the same :class:`CombinedForecastResult` used by Phase 2, adds the
equal-weight subsystem-reference percentage and optional executable cash P&L
paths, and exposes small Polars tables plus a lazy hvPlot dashboard. A helper
can join a saved Phase 1 selection screen for contract and cost inputs. It does
not reimplement forecast, sizing, buffering, or cost calculations; those
remain owned by the calculation modules used by production selection. The
stitched-history entry point also orchestrates dated-contract loading, history
ownership, pooled forecast construction, and this same view boundary.
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
    DEFAULT_IB_DATED_CONTRACT_DURATION,
    build_stitched_futures_history,
    load_broker_mapping,
    load_roll_cycle_ib_contract_history,
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
    screen_symbol: str,
) -> dict[str, object]:
    """Resolve exactly one screen row by its executable or signal symbol."""
    for column in ("signal_symbol", "symbol", "ib_symbol", "hist_instr_code"):
        if column not in screen.columns:
            continue
        matches = screen.filter(pl.col(column).cast(pl.String) == screen_symbol)
        if matches.height == 0:
            continue
        if matches.height != 1:
            raise ValueError(
                f"screen symbol {screen_symbol!r} matches "
                f"{matches.height} rows in {column!r}; use a unique symbol"
            )
        return matches.row(0, named=True)
    raise ValueError(
        f"screen has no row for {screen_symbol!r}; checked "
        "signal_symbol, symbol, ib_symbol, and hist_instr_code"
    )


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

    instrument_code: str
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
            "ref_avg_pos",
            "ref_smooth_avg_pos",
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
            title=f"{self.instrument_code} adjusted signal price",
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
                "ref_avg_pos",
                "ref_smooth_avg_pos",
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
    instrument_code: str,
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
    instrument_code
        Display identity used in plot titles.
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
    if not instrument_code.strip():
        raise ValueError("instrument_code must not be empty")
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
        instrument_code=instrument_code,
        result=result,
        reference=reference,
        reference_turnover=reference_turnover,
        cash=cash,
    )


def build_ewmac_notebook_view_from_screen(
    result: CombinedForecastResult,
    *,
    screen: pl.DataFrame | Path | str,
    screen_symbol: str,
    reference_settings: SubsystemReferenceSettings,
    cash_settings: ExecutableCashSettings | None = None,
    instrument_code: str | None = None,
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
    screen_symbol
        Exact symbol in the screen's ``signal_symbol`` or ``symbol`` field.
    reference_settings
        Equal-weight subsystem-reference sizing assumptions.
    cash_settings
        Optional account-level settings. When provided, per-contract trade
        and roll costs are taken from the matched screen row rather than the
        settings object's cost defaults. If account-specific costs are null,
        the helper uses the screen's explicitly labelled snapshot/configured
        trade estimate and estimates a calendar-spread roll as spread plus two
        reference commissions; the output frames identify those sources.
    instrument_code
        Plot label. Defaults to ``screen_symbol``; can differ from the signal
        instrument when sizing (for example, ES forecast with MES terms).
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
    screen_row = _screen_row(screen_frame, screen_symbol)
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
            f"cannot estimate reference turnover for {screen_symbol!r}"
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
        instrument_code=instrument_code or screen_symbol,
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


def _contract_point_value(contract_history: pl.DataFrame) -> float:
    """Return the unique native-currency point value in downloaded contracts."""
    required = {"ib_multiplier", "price_magnifier"}
    missing = required - set(contract_history.columns)
    if missing:
        raise ValueError(
            f"contract history missing point-value columns: {sorted(missing)}"
        )
    values = (
        contract_history.select(
            (
                pl.col("ib_multiplier").cast(pl.Float64)
                / pl.col("price_magnifier").cast(pl.Float64)
            ).alias("point_value")
        )
        .drop_nulls()
        .unique()
    )
    if values.height != 1:
        raise ValueError("contract history must contain one point value")
    point_value = float(values.item())
    if not math.isfinite(point_value) or point_value <= 0:
        raise ValueError("contract point value must be finite and positive")
    return point_value


def _mapped_contract_point_value(
    instrument_code: str,
    mapping_path: Path | str,
) -> float:
    """Return an execution instrument's mapped native point value."""
    mapping = load_broker_mapping(mapping_path).filter(
        pl.col("instrument_code") == instrument_code
    )
    if mapping.height != 1:
        raise KeyError(f"No unique broker mapping for {instrument_code}")
    row = mapping.row(0, named=True)
    point_value = float(row["ib_multiplier"]) / float(
        row["price_magnifier"]
    )
    if not math.isfinite(point_value) or point_value <= 0:
        raise ValueError(
            f"{instrument_code} mapped point value must be finite and positive"
        )
    return point_value


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


def build_stitched_ewmac_notebook_view(
    ib: IBPySync,
    *,
    instrument_code: str,
    globex_asset: str,
    reference_settings: SubsystemReferenceSettings,
    signal_instrument_code: str | None = None,
    reference_instrument_code: str | None = None,
    display_instrument_code: str | None = None,
    as_of: date | None = None,
    prior_years: int = 1,
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
    screen_symbol: str | None = None,
    reference_db_path: Path | str = DEFAULT_REFERENCE_DB_PATH,
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH,
    broker_mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH,
    pooling_mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
    use_cache: bool = True,
    save_cache: bool = True,
) -> EwmacNotebookView:
    """Build a complete stitched-history EWMAC notebook view in one call.

    The helper downloads overlapping dated contracts from IB, constructs the
    configured held-contract path, and stitches it over Globex and the long
    reference history. It then loads the causal pooled EWMAC scalars, builds
    every requested component, aligns historical FX, combines the forecasts,
    and delegates sizing and plotting to the standard notebook view.

    Parameters
    ----------
    ib
        Connected synchronous IB client.
    instrument_code
        Executable instrument identifier used for contract specifications,
        screen lookup, and the default display label.
    globex_asset
        Raw Globex asset identifier used by the signal history.
    reference_settings
        Equal-weight subsystem reference sizing assumptions.
    signal_instrument_code
        Instrument identifier used to download the newest signal history and
        select its roll configuration. Defaults to ``instrument_code``. This
        permits a micro contract to use its full-size sibling's deeper price
        history while retaining the micro's point value for sizing and P&L.
    reference_instrument_code
        Oldest-history identifier. Defaults to ``signal_instrument_code``.
    display_instrument_code
        Optional plot label. Defaults to ``instrument_code``.
    as_of, prior_years, duration, bar_size, what_to_show, use_rth
        Dated-contract download settings forwarded to the IB history loader.
    rules
        EWMAC rules to normalize, calculate, and combine.
    fdm, combination_config
        Fixed single-market forecast diversification and numeric conventions.
    multiplier
        Optional native-currency point-value override. When absent, infer it
        from the downloaded broker multiplier and price magnifier.
    ann_cost_sr, cash_settings
        Inputs for the standard reference and optional executable cash paths.
    screen, screen_symbol
        Optional Phase 1 screen inputs. When supplied, screen costs and the
        screen multiplier replace ``ann_cost_sr`` and ``multiplier``.
    reference_db_path, globex_db_path, broker_mapping_path
        Read-only history, roll configuration, and broker mapping inputs.
    pooling_mapping_path
        Reviewed pooling decisions used by causal scalar normalization.
    use_cache, save_cache
        Cache controls for the database-backed history providers.

    Returns
    -------
    EwmacNotebookView
        Combined forecasts, reference sizing, optional cash P&L, plotting
        methods, the stitched history in ``view.history``, and the overlapping
        IB rows in ``view.contract_history``.
    """
    if screen is None and screen_symbol is not None:
        raise ValueError("screen_symbol requires screen")
    rule_tuple = tuple(rules)
    if not rule_tuple:
        raise ValueError("at least one EWMAC rule is required")
    config = combination_config or ForecastCombinationConfig()

    signal_code = signal_instrument_code or instrument_code
    contracts = load_roll_cycle_ib_contract_history(
        ib,
        signal_code,
        as_of=as_of,
        prior_years=prior_years,
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=use_rth,
        db_path=reference_db_path,
        mapping_path=broker_mapping_path,
    )
    history = build_stitched_futures_history(
        contracts,
        globex_asset=globex_asset,
        reference_instrument_code=(
            reference_instrument_code or signal_code
        ),
        globex_db_path=globex_db_path,
        reference_db_path=reference_db_path,
        use_cache=use_cache,
        save_cache=save_cache,
    )
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

    display_code = display_instrument_code or instrument_code
    if screen is not None:
        view = build_ewmac_notebook_view_from_screen(
            result,
            screen=screen,
            screen_symbol=screen_symbol or instrument_code,
            reference_settings=reference_settings,
            cash_settings=cash_settings,
            instrument_code=display_code,
            target_abs_forecast=config.target_abs_forecast,
        )
    else:
        view = build_ewmac_notebook_view(
            result,
            instrument_code=display_code,
            multiplier=(
                (
                    _contract_point_value(contracts)
                    if signal_code == instrument_code
                    else _mapped_contract_point_value(
                        instrument_code,
                        broker_mapping_path,
                    )
                )
                if multiplier is None
                else multiplier
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

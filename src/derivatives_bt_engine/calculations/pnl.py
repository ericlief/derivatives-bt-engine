"""Build reference percentages, executable cash P&L, and portfolio audit curves.

Forecast construction owns the combined forecast. This module owns the next
calculation boundary: size and lag positions, measure turnover from those
positions, mark them through daily point changes, convert through the contract
multiplier and historical FX, and accrue annual SR cost through elapsed
calendar time. The subsystem-reference path uses a fixed equal-weight book
assumption to produce comparable instrument returns for correlation estimation.
The separate selected-book cash path applies final weights and IDM, buffers and
rounds to executable contracts, and charges ordinary trades and contract rolls
separately. Its long instrument output can then be aggregated into a fixed
initial-capital portfolio return and equity curve without changing the
positions that produced the cash P&L.
"""

from __future__ import annotations

import math

import polars as pl

from derivatives_bt_engine.reports.formatting import rename_public_report_columns

from derivatives_bt_engine.calculations.ewmac import EWMAC_FORECAST_TARGET_ABS
from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
)


PYSYSTEMTRADE_FORECAST_BUFFER_SIZE = 0.10

_REFERENCE_ACCOUNTING_COLUMNS = (
    "ref_book_count",
    "ref_weight",
    "ref_idm",
    "max_weight_idm",
    "ref_weight_idm",
    "ref_budget",
    "ref_ann_risk",
    "ref_daily_risk",
    "daily_dvol",
    "ref_avg_pos",
    "ref_tgt_pos",
    "ref_held_pos",
    "ref_gross",
    "ref_cost",
    "ref_net",
    "ref_gross_pct",
    "ref_ann_cost_pct",
    "ref_cost_pct",
    "ref_net_pct",
    "cum_ref_gross",
    "cum_ref_net",
    "cum_ref_gross_pct",
    "cum_ref_net_pct",
    "ref_return_valid",
)


def _group_reference_accounting_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """Place reference sizing, USD values, and percentages in one block."""
    accounting = [
        column
        for column in _REFERENCE_ACCOUNTING_COLUMNS
        if column in frame.columns
    ]
    other = [column for column in frame.columns if column not in accounting]
    return frame.select(*other, *accounting)


def apply_forecast_position_buffer(
    frame: pl.DataFrame,
    *,
    optimal_position_col: str = "target_position",
    average_position_col: str = "average_position",
    force_rebalance_col: str | None = None,
    buffer_size: float = PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
    trade_to_edge: bool = True,
) -> pl.DataFrame:
    """Append pysystemtrade-style buffered whole-contract positions.

    Parameters
    ----------
    frame
        One chronologically sorted instrument frame containing the continuous
        optimal and average positions.
    optimal_position_col
        Desired continuous contract position after forecast scaling.
    average_position_col
        Contracts held at the target absolute forecast. This is the position
        scale used to form the buffer and may vary with volatility and FX.
    force_rebalance_col
        Optional boolean column whose true rows reset the executable position
        to the rounded optimum, bypassing the no-trade band for that row.
    buffer_size
        Nonnegative fraction of absolute average position used on each side of
        the target. Pysystemtrade's default forecast buffer is ``0.10``.
    trade_to_edge
        If true, a breach moves to the nearest band edge. If false, it moves
        to the rounded optimal position.

    Returns
    -------
    polars.DataFrame
        The input plus continuous ``buffer_width_position``, effective rounded
        ``buffer_top_cons`` and ``buffer_bot_cons``, and stateful ``exec_cons``.

    Notes
    -----
    This follows pysystemtrade's ``apply_buffer`` ordering: forward-fill the
    optimal position and band, round each to whole contracts using Polars'
    ties-to-even convention (the same convention as pandas), initialize at the
    first rounded optimum (or zero), then retain the previous executable
    position while it remains inside the band. Only the final state transition
    is recursive because today's output depends on yesterday's held position.
    A configured force-rebalance row overrides that state transition.
    """
    required = {optimal_position_col, average_position_col}
    if force_rebalance_col is not None:
        required.add(force_rebalance_col)
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"position-buffer input missing columns: {missing}")
    if not math.isfinite(buffer_size) or buffer_size < 0:
        raise ValueError("buffer_size must be finite and nonnegative")

    buffered_input = frame.with_columns(
        (
            pl.col(average_position_col).abs() * buffer_size
        ).alias("buffer_width_position")
    ).with_columns(
        pl.col(optimal_position_col).forward_fill().alias("_buffer_optimal"),
        (
            pl.col(optimal_position_col) + pl.col("buffer_width_position")
        ).forward_fill().alias("_buffer_top"),
        (
            pl.col(optimal_position_col) - pl.col("buffer_width_position")
        ).forward_fill().alias("_buffer_bot"),
    ).with_columns(
        pl.col("_buffer_optimal")
        .round(0)
        .cast(pl.Int64, strict=False)
        .alias("_rounded_optimal"),
        pl.col("_buffer_top")
        .round(0)
        .cast(pl.Int64, strict=False)
        .alias("buffer_top_cons"),
        pl.col("_buffer_bot")
        .round(0)
        .cast(pl.Int64, strict=False)
        .alias("buffer_bot_cons"),
    )

    rounded_optimal = buffered_input.get_column("_rounded_optimal").to_list()
    rounded_top = buffered_input.get_column("buffer_top_cons").to_list()
    rounded_bot = buffered_input.get_column("buffer_bot_cons").to_list()
    force_rebalance = (
        buffered_input.get_column(force_rebalance_col)
        .fill_null(False)
        .cast(pl.Boolean)
        .to_list()
        if force_rebalance_col is not None
        else [False] * buffered_input.height
    )

    if not rounded_optimal:
        return buffered_input.with_columns(
            pl.Series("exec_cons", [], dtype=pl.Int64),
        ).drop(
            "_buffer_optimal",
            "_buffer_top",
            "_buffer_bot",
            "_rounded_optimal",
        )

    # Pysystemtrade starts at zero when the first optimum is unavailable.
    current_position = rounded_optimal[0]
    if current_position is None:
        current_position = 0
    executable_positions = [current_position]
    for optimal_position, top_position, bot_position, force_position in zip(
        rounded_optimal[1:],
        rounded_top[1:],
        rounded_bot[1:],
        force_rebalance[1:],
        strict=True,
    ):
        if (
            optimal_position is None
            or top_position is None
            or bot_position is None
        ):
            executable_positions.append(current_position)
            continue
        if force_position:
            current_position = optimal_position
        elif current_position > top_position:
            current_position = (
                top_position if trade_to_edge else optimal_position
            )
        elif current_position < bot_position:
            current_position = (
                bot_position if trade_to_edge else optimal_position
            )
        executable_positions.append(current_position)

    return buffered_input.with_columns(
        pl.Series("exec_cons", executable_positions, dtype=pl.Int64),
    ).drop(
        "_buffer_optimal",
        "_buffer_top",
        "_buffer_bot",
        "_rounded_optimal",
    )


def append_reference_position_turnover(
    frame: pl.DataFrame,
    *,
    average_position_ewm_com: int = 250,
    turnover_rolling_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
) -> tuple[pl.DataFrame, float | None]:
    """Append Carver-style turnover from sized reference positions.

    Parameters
    ----------
    frame
        Chronological reference frame containing ``ref_avg_pos`` and
        ``ref_tgt_pos`` in continuous contracts.
    average_position_ewm_com
        EWM ``com`` used to smooth the changing average-position denominator.
        The target position and forecast are not smoothed.
    turnover_rolling_days
        Complete trailing window used for the row-level annualized turnover
        diagnostic.
    annualization_days
        Trading-day count used to annualize mean absolute daily changes.

    Returns
    -------
    tuple[polars.DataFrame, float | None]
        The input plus ``ref_smooth_avg_pos``, ``ref_norm_pos``, absolute
        position changes, rolling diagnostics, and the
        full-history annual turnover. The scalar is ``None`` when no finite
        position change exists.

    Notes
    -----
    This implements the turnover ratio as target position divided by smoothed
    average position. Because both inputs are already sized with historical FX
    and the contract multiplier, the audit does not rely on an inverse-volatility
    proxy or assume that FX is constant through time.
    """
    required = {"ref_avg_pos", "ref_tgt_pos"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"reference turnover input missing columns: {missing}")
    if average_position_ewm_com <= 0:
        raise ValueError("average_position_ewm_com must be positive")
    if turnover_rolling_days <= 0:
        raise ValueError("turnover_rolling_days must be positive")
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")

    result = (
        frame.sort("date")
        .with_columns(
            pl.col("ref_avg_pos")
            .ewm_mean(
                com=average_position_ewm_com,
                adjust=True,
                min_samples=2,
            )
            .alias("ref_smooth_avg_pos")
        )
        .with_columns(
            pl.when(pl.col("ref_smooth_avg_pos") > 0)
            .then(
                pl.col("ref_tgt_pos")
                / pl.col("ref_smooth_avg_pos")
            )
            .otherwise(None)
            .alias("ref_norm_pos")
        )
        .with_columns(
            pl.col("ref_norm_pos")
            .diff()
            .abs()
            .alias("ref_abs_pos_change")
        )
        .with_columns(
            pl.col("ref_abs_pos_change")
            .rolling_mean(
                window_size=turnover_rolling_days,
                min_samples=turnover_rolling_days,
            )
            .alias("ref_roll_mean_abs_pos_change")
        )
        .with_columns(
            (
                pl.col("ref_roll_mean_abs_pos_change")
                * annualization_days
            ).alias("ref_roll_ann_turnover")
        )
    )
    changes = result.get_column("ref_abs_pos_change")
    changes = changes.filter(changes.is_not_null() & changes.is_finite())
    turnover = (
        float(changes.mean() * annualization_days) if changes.len() else None
    )
    return result, turnover


def apply_subsystem_reference_cost(
    frame: pl.DataFrame,
    *,
    ann_cost_sr: float,
) -> pl.DataFrame:
    """Apply an annual SR cost to an already sized reference percentage frame.

    The helper is separate from sizing so the pipeline can first measure
    turnover from the reference positions, convert that turnover to annual SR
    cost, and then apply the resulting cost without rebuilding the positions.
    Cost percentage is normalized by ``ref_budget``, the equal-weight
    reference budget implied by the annual risk budget and target volatility.
    """
    required = {
        "ref_held_pos",
        "period_year_fraction",
        "ref_ann_risk",
        "ref_budget",
        "ref_gross",
        "ref_gross_pct",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"reference cost input missing columns: {missing}")
    if not math.isfinite(ann_cost_sr) or ann_cost_sr < 0:
        raise ValueError("ann_cost_sr must be finite and nonnegative")

    result = (
        frame.with_columns(
            (
                -ann_cost_sr
                * pl.col("ref_ann_risk")
                / pl.col("ref_budget")
                * 100.0
            ).alias("ref_ann_cost_pct")
        )
        .with_columns(
            # Costs begin only once a position was actually held. Calendar
            # elapsed time spreads the annual charge over irregular rows.
            pl.when(
                pl.col("ref_held_pos").is_not_null()
                & pl.col("period_year_fraction").is_not_null()
            )
            .then(
                pl.col("ref_ann_cost_pct")
                * pl.col("period_year_fraction")
            )
            .otherwise(None)
            .alias("ref_cost_pct")
        )
        .with_columns(
            (
                pl.col("ref_cost_pct")
                / 100.0
                * pl.col("ref_budget")
            ).alias("ref_cost"),
        )
        .with_columns(
            (
                pl.col("ref_gross")
                + pl.col("ref_cost")
            ).alias("ref_net"),
        )
        .with_columns(
            (
                pl.col("ref_net")
                / pl.col("ref_budget")
                * 100.0
            ).alias("ref_net_pct"),
        )
        .with_columns(
            (
                pl.col("ref_net_pct").is_not_null()
                & pl.col("ref_net_pct").is_finite()
            ).alias("ref_return_valid")
        )
        .with_columns(
            # Keep the cumulative path at calculation precision. At a public
            # report boundary, summing individually rounded daily USD rows
            # can differ by cents from this rounded cumulative value.
            pl.col("ref_gross")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_gross"),
            pl.col("ref_net")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_net"),
            pl.col("ref_gross_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_gross_pct"),
            pl.col("ref_net_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_ref_net_pct"),
        )
    )
    return _group_reference_accounting_columns(result)


def build_subsystem_reference_return_curve(
    frame: pl.DataFrame,
    *,
    capital: float,
    target_vol: float,
    reference_book_count: int,
    reference_idm: float,
    max_weight_idm: float,
    multiplier: float,
    ann_cost_sr: float,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    point_vol_col: str | None = None,
    fx_col: str = "fx_to_usd",
) -> pl.DataFrame:
    """Append equal-weight subsystem-reference sizing and return columns.

    Parameters
    ----------
    frame
        One instrument's daily combined-forecast frame. It must contain
        ``date``, ``point_change``, ``combined_forecast``, point volatility,
        and a historical USD FX conversion. ``mixed_point_vol`` is preferred,
        with legacy ``point_vol`` accepted for compact Phase 2 frames.
    capital
        Reference account capital in USD. This supplies a reproducible
        position scale. Percentages are normalized by ``ref_budget``,
        which is implied by the instrument risk budget and target volatility.
    target_vol
        Annual portfolio volatility target as a decimal fraction.
    reference_book_count
        Assumed final instrument count. Each candidate receives the fixed
        structural weight ``1 / reference_book_count``.
    reference_idm
        Fixed IDM assumption shared by every candidate reference curve.
    max_weight_idm
        Maximum product of reference weight and IDM. This matches the
        small-account sizing cap used by the selection tie rule.
    multiplier
        Native-currency cash value of one price point for one contract.
    ann_cost_sr
        Annual total cost in SR units.  It normally combines actual subsystem
        turnover with the Phase 1 trade cost and roll-policy frequency with
        the Phase 1 roll cost.
    target_abs_forecast
        Forecast value corresponding to one average position.
    annualization_days
        Trading days used to convert the annual reference risk budget to a
        daily risk budget and daily dollar volatility to annual units.
    point_vol_col
        Explicit point-volatility column, or ``None`` to discover the standard
        descriptive/legacy name.
    fx_col
        Column containing the causal native-currency-to-USD conversion.

    Returns
    -------
    polars.DataFrame
        The input plus reference weights and risk budgets, daily dollar
        volatility per contract, reference budget, continuous average, target,
        and held positions, gross/cost/net hypothetical reference values in
        USD, matching subsystem percentages, and a row-level validity flag.

    Notes
    -----
    This path is continuous and deliberately does not round to whole
    contracts. It represents every candidate at the same assumed equal-weight
    allocation, independent of the partial greedy book. Only
    :func:`build_executable_cash_pnl_curve` represents the selected portfolio's
    final weights, IDM, buffering, discrete contracts, and cash P&L.
    """
    if point_vol_col is None:
        if "mixed_point_vol" in frame.columns:
            point_vol_col = "mixed_point_vol"
        elif "point_vol" in frame.columns:
            point_vol_col = "point_vol"
        else:
            raise ValueError(
                "subsystem reference input missing point volatility; expected "
                "'mixed_point_vol' or legacy 'point_vol'"
            )

    required = {
        "date",
        "point_change",
        point_vol_col,
        "combined_forecast",
        fx_col,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            f"subsystem reference input missing columns: {missing}"
        )
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be finite and positive")
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise ValueError("target_vol must be finite and positive")
    if reference_book_count <= 0:
        raise ValueError("reference_book_count must be positive")
    if not math.isfinite(reference_idm) or reference_idm < 1:
        raise ValueError("reference_idm must be finite and at least one")
    if not math.isfinite(max_weight_idm) or not 0 < max_weight_idm <= 1:
        raise ValueError("max_weight_idm must be finite and in (0, 1]")
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be finite and positive")
    if not math.isfinite(ann_cost_sr) or ann_cost_sr < 0:
        raise ValueError("ann_cost_sr must be finite and nonnegative")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")

    annualization_sqrt = math.sqrt(annualization_days)
    ref_weight = 1.0 / reference_book_count
    ref_weight_idm = min(
        ref_weight * reference_idm,
        max_weight_idm,
    )
    annual_risk_budget_usd = (
        capital * target_vol * ref_weight_idm
    )
    ref_budget_usd = annual_risk_budget_usd / target_vol
    daily_risk_budget_usd = annual_risk_budget_usd / annualization_sqrt
    reference = (
        frame.sort("date")
        .with_columns(
            (
                pl.col(fx_col).is_not_null()
                & pl.col(fx_col).is_finite()
                & (pl.col(fx_col) > 0)
            ).alias("fx_valid"),
            (
                pl.col("date").diff().dt.total_days().cast(pl.Float64)
                / 365.25
            ).alias("period_year_fraction"),
        )
        .with_columns(
            pl.lit(reference_book_count).alias("ref_book_count"),
            pl.lit(ref_weight).alias("ref_weight"),
            pl.lit(reference_idm).alias("ref_idm"),
            pl.lit(max_weight_idm).alias("max_weight_idm"),
            pl.lit(ref_weight_idm).alias("ref_weight_idm"),
            pl.lit(annual_risk_budget_usd).alias("ref_ann_risk"),
            pl.lit(ref_budget_usd).alias(
                "ref_budget"
            ),
            pl.lit(daily_risk_budget_usd).alias("ref_daily_risk"),
            pl.when(pl.col("fx_valid"))
            .then(pl.col(point_vol_col) * multiplier * pl.col(fx_col))
            .otherwise(None)
            .alias("daily_dvol"),
        )
        .with_columns(
            pl.when(
                pl.col("daily_dvol").is_finite()
                & (pl.col("daily_dvol") > 0)
            )
            .then(
                daily_risk_budget_usd
                / pl.col("daily_dvol")
            )
            .otherwise(None)
            .alias("ref_avg_pos"),
        )
        .with_columns(
            (
                pl.col("ref_avg_pos")
                * pl.col("combined_forecast")
                / target_abs_forecast
            ).alias("ref_tgt_pos"),
        )
        .with_columns(
            # End-of-day positions earn the following observation's move.
            pl.col("ref_tgt_pos")
            .shift(1)
            .alias("ref_held_pos"),
        )
        .with_columns(
            (
                pl.col("ref_held_pos")
                * pl.col("point_change")
                * multiplier
                * pl.col(fx_col)
            ).alias("ref_gross"),
        )
        .with_columns(
            (
                pl.col("ref_gross")
                / pl.col("ref_budget")
                * 100.0
            ).alias("ref_gross_pct"),
        )
    )
    return apply_subsystem_reference_cost(
        reference,
        ann_cost_sr=ann_cost_sr,
    )


def build_executable_cash_pnl_curve(
    frame: pl.DataFrame,
    *,
    capital: float,
    target_vol: float,
    weight: float,
    idm: float,
    multiplier: float,
    trade_cost_usd_per_con: float,
    roll_cost_usd_per_con: float,
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    point_vol_col: str | None = None,
    fx_col: str = "fx_to_usd",
    roll_col: str = "is_roll",
    buffer_size: float = PYSYSTEMTRADE_FORECAST_BUFFER_SIZE,
    buffer_trade_to_edge: bool = True,
    rebalance_on_roll: bool = True,
) -> pl.DataFrame:
    """Append account-scaled contracts and executable cash P&L diagnostics.

    Parameters
    ----------
    frame
        One combined-subsystem frame containing ``date``, ``point_change``,
        ``combined_forecast``, point volatility, historical USD FX, and an
        optional boolean roll marker. ``close`` may be the locally generated
        additive-Panama signal price. When present, ``contract_close`` is the
        date-aligned raw mark of the selected contract and is used for annual
        volatility as a percentage of notional. Without it, ``ann_pct_vol``
        is null rather than inferred from an adjusted price. The position must
        already represent the final net forecast rather than separate rule
        sleeves.
    capital
        Account capital in USD.
    target_vol
        Annual portfolio volatility target as a decimal fraction.
    weight
        Instrument risk weight used for small-account position limits. Phase 2
        passes its capped ``limit_weight`` so this curve reproduces the
        contracts shown in selection reports.
    idm
        Instrument diversification multiplier applied to the risk weight.
    multiplier
        Native-currency cash value of one price point for one contract.
    trade_cost_usd_per_con
        Current one-way ordinary-trade cost for one contract in USD.
    roll_cost_usd_per_con
        Current cost of one complete old-to-new contract roll in USD. The
        Phase 1 convention is one calendar-spread width plus two commissions.
    target_abs_forecast
        Mean absolute forecast represented by a 1x average position. The
        default is 1.0, so ``combined_forecast`` is directly the signed
        position factor. Nondefault scales are divided by this value inline.
    annualization_days
        Trading days used to annualize daily point volatility.
    point_vol_col
        Explicit point-volatility column, or ``None`` to discover the standard
        descriptive/legacy name.
    fx_col
        Historical native-currency-to-USD conversion column.
    roll_col
        Boolean history-policy roll marker. When absent, the output retains a
        false marker and zero realized/average-roll quantities.
    buffer_size
        Forecast-method no-trade band as a fraction of absolute average
        contracts. The default ``0.10`` matches pysystemtrade.
    buffer_trade_to_edge
        Move to the nearest band edge on a breach, matching pysystemtrade's
        default. If false, move to the rounded optimal position instead.
    rebalance_on_roll
        If true, reset executable contracts to the rounded target on each roll
        date. If false, retain the normal stateful no-trade buffer policy.

    Returns
    -------
    polars.DataFrame
        The input (including ``close`` when supplied) plus daily and annual
        dollar volatility per contract and annualized volatility as a percent
        of raw contract notional when a contract close is available,
        continuous average and target positions, buffer width and boundaries,
        buffered executable and lagged contracts, ordinary trade and roll
        quantities, and gross, cost, and net cash P&L in USD, plus daily and
        cumulative percentage-point returns and costs. Cash percentages
        divide each instrument's USD amount by total account capital and
        multiply by 100, so they add across instruments to the portfolio
        percentage return. Annualized tracking error is aligned to the date a
        target becomes held: each row compares the prior row's continuous
        target and dollar volatility with the current row's held contracts.
        The first row has no prior target and is null. It is exposed as
        ``track_err_ann`` and ``track_err_ann_pct_cap``. Returned dataframe
        columns use the centralized compact report vocabulary (for example,
        ``avg_pos``, ``ref_norm_pos``, and ``exec_con``).

    Notes
    -----
    With roll rebalancing enabled, ``roll_cons`` is the same-direction
    overlap between the prior holding and the newly rounded target. Any size
    change is retained in signed ``trade_con`` as an ordinary trade. Its
    absolute size is used internally for transaction costs. Positive signed
    trades increase the net position and negative trades decrease it. With
    roll rebalancing disabled, the
    executable position normally remains unchanged
    and the complete prior holding is rolled. Ordinary position changes
    are charged independently, including the initial entry from zero. Current
    point-in-time cost rates are intentionally applied to the full history;
    this is an execution-cost sensitivity, not a claim that historical
    commissions and spreads were constant.
    """
    if point_vol_col is None:
        if "mixed_point_vol" in frame.columns:
            point_vol_col = "mixed_point_vol"
        elif "point_vol" in frame.columns:
            point_vol_col = "point_vol"
        else:
            raise ValueError(
                "cash P&L input missing point volatility; expected "
                "'mixed_point_vol' or legacy 'point_vol'"
            )

    required = {
        "date",
        "point_change",
        "combined_forecast",
        point_vol_col,
        fx_col,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"cash P&L input missing columns: {missing}")
    positive_values = {
        "capital": capital,
        "target_vol": target_vol,
        "weight": weight,
        "idm": idm,
        "multiplier": multiplier,
    }
    for name, value in positive_values.items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    cost_values = {
        "trade_cost_usd_per_con": trade_cost_usd_per_con,
        "roll_cost_usd_per_con": roll_cost_usd_per_con,
    }
    for name, value in cost_values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")
    if not math.isfinite(buffer_size) or buffer_size < 0:
        raise ValueError("buffer_size must be finite and nonnegative")

    risk_budget_usd = capital * target_vol * weight * idm
    roll_expr = (
        pl.col(roll_col).fill_null(False).cast(pl.Boolean)
        if roll_col in frame.columns
        else pl.lit(False)
    )
    target_frame = (
        frame.sort("date")
        .with_columns(
            roll_expr.alias("roll_event"),
            (
                pl.col(fx_col).is_not_null()
                & pl.col(fx_col).is_finite()
                & (pl.col(fx_col) > 0)
            ).alias("cash_fx_valid"),
        )
        .with_columns(
            (
                pl.col(point_vol_col)
                * multiplier
                * pl.col(fx_col)
            ).alias("daily_dvol"),
            pl.lit(risk_budget_usd).alias("risk_budget_usd"),
        )
        .with_columns(
            (
                pl.col("daily_dvol")
                * math.sqrt(annualization_days)
            ).alias("annual_dollar_vol_per_contract"),
        )
        .with_columns(
            (
                pl.when(
                    pl.col("cash_fx_valid")
                    & pl.col("contract_close").is_not_null()
                    & pl.col("contract_close").is_finite()
                    & (pl.col("contract_close") > 0)
                    & pl.col("annual_dollar_vol_per_contract").is_finite()
                )
                .then(
                    pl.col("annual_dollar_vol_per_contract")
                    / (
                        pl.col("contract_close")
                        * multiplier
                        * pl.col(fx_col)
                    )
                    * 100.0
                )
                .otherwise(None)
                if "contract_close" in frame.columns
                else pl.lit(None, dtype=pl.Float64)
            ).alias("annual_percentage_vol"),
        )
        .with_columns(
            pl.when(
                pl.col("cash_fx_valid")
                & pl.col("annual_dollar_vol_per_contract").is_finite()
                & (pl.col("annual_dollar_vol_per_contract") > 0)
            )
            .then(
                risk_budget_usd / pl.col("annual_dollar_vol_per_contract")
            )
            .otherwise(None)
            .alias("average_position")
        )
        .with_columns(
            (
                pl.col("average_position")
                * pl.col("combined_forecast")
                / target_abs_forecast
            ).alias("target_position")
        )
    )
    # At this point average_position already contains instrument weight and IDM,
    # exactly as pysystemtrade's portfolio forecast buffer multiplies its
    # volatility scalar by those quantities.
    result = (
        apply_forecast_position_buffer(
            target_frame,
            force_rebalance_col=("roll_event" if rebalance_on_roll else None),
            buffer_size=buffer_size,
            trade_to_edge=buffer_trade_to_edge,
        )
        .with_columns(
            pl.lit(buffer_size).alias("buffer_size"),
            pl.lit(buffer_trade_to_edge).alias("buffer_trade_to_edge"),
            pl.lit(rebalance_on_roll).alias("rebalance_on_roll"),
            pl.lit(trade_cost_usd_per_con).alias("trade_cost_usd_per_con"),
            pl.lit(roll_cost_usd_per_con).alias("roll_cost_usd_per_con"),
        )
        .with_columns(
            pl.col("exec_cons").shift(1).alias("held_cons"),
        )
        .with_columns(
            pl.when(
                pl.col("target_position").shift(1).is_not_null()
                & pl.col("target_position").shift(1).is_finite()
                & pl.col("held_cons").is_not_null()
                & pl.col("annual_dollar_vol_per_contract")
                .shift(1)
                .is_not_null()
                & pl.col("annual_dollar_vol_per_contract")
                .shift(1)
                .is_finite()
                & (
                    pl.col("annual_dollar_vol_per_contract").shift(1)
                    > 0
                )
            )
            .then(
                (
                    pl.col("target_position").shift(1)
                    - pl.col("held_cons")
                ).abs()
                * pl.col("annual_dollar_vol_per_contract").shift(1)
            )
            .otherwise(None)
            .alias("annual_tracking_error_usd"),
        )
        .with_columns(
            (
                pl.col("annual_tracking_error_usd")
                / capital
                * 100.0
            ).alias("annual_tracking_error_pct_capital"),
        )
        .with_columns(
            pl.when(pl.col("exec_cons").is_not_null())
            .then(
                pl.col("exec_cons") - pl.col("held_cons").fill_null(0)
            )
            .otherwise(None)
            .cast(pl.Float64)
            .alias("signed_trade_cons"),
            pl.when(pl.col("roll_event"))
            .then(
                pl.when(
                    pl.col("held_cons").is_not_null()
                    & pl.col("exec_cons").is_not_null()
                    & ((pl.col("held_cons") * pl.col("exec_cons")) > 0)
                )
                .then(
                    pl.min_horizontal(
                        pl.col("held_cons").abs(),
                        pl.col("exec_cons").abs(),
                    )
                )
                .otherwise(0)
            )
            .otherwise(0)
            .cast(pl.Float64)
            .alias("roll_cons"),
            (
                pl.lit(rebalance_on_roll)
                & pl.col("roll_event")
                & pl.col("target_position").is_not_null()
                & pl.col("target_position").is_finite()
            ).alias("roll_rebalanced"),
        )
        .with_columns(
            (
                pl.col("held_cons")
                * pl.col("point_change")
                * multiplier
                * pl.col(fx_col)
            ).alias("cash_gross_pnl_usd"),
            (-pl.col("signed_trade_cons").abs() * trade_cost_usd_per_con).alias(
                "cash_trade_cost_usd"
            ),
            (-pl.col("roll_cons") * roll_cost_usd_per_con).alias(
                "cash_roll_cost_usd"
            ),
        )
        .with_columns(
            (
                pl.col("cash_gross_pnl_usd").fill_null(0.0)
                + pl.col("cash_trade_cost_usd")
                + pl.col("cash_roll_cost_usd")
            ).alias("cash_net_pnl_usd"),
        )
        .with_columns(
            # Dollar P&L is retained alongside percentage returns so an
            # instrument-level audit can reconcile to the portfolio curve.
            pl.col("cash_gross_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_gross_pnl_usd"),
            pl.col("cash_net_pnl_usd")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_net_pnl_usd"),
        )
        .with_columns(
            (
                pl.col("cash_net_pnl_usd").is_not_null()
                & pl.col("cash_net_pnl_usd").is_finite()
            ).alias("cash_pnl_valid"),
            # Cash and subsystem return columns both use percentage points:
            # USD contribution divided by the relevant capital base, then ×100.
            pl.when(
                pl.col("cash_gross_pnl_usd").is_not_null()
                & pl.col("cash_gross_pnl_usd").is_finite()
            )
            .then(pl.col("cash_gross_pnl_usd") / capital * 100.0)
            .otherwise(0.0)
            .alias("cash_gross_pct"),
            (pl.col("cash_trade_cost_usd") / capital * 100.0).alias(
                "cash_trade_cost_pct"
            ),
            (pl.col("cash_roll_cost_usd") / capital * 100.0).alias(
                "cash_roll_cost_pct"
            ),
            (
                (pl.col("cash_trade_cost_usd")
                 + pl.col("cash_roll_cost_usd"))
                / capital
                * 100.0
            ).alias("cash_total_cost_pct"),
            pl.when(
                pl.col("cash_net_pnl_usd").is_not_null()
                & pl.col("cash_net_pnl_usd").is_finite()
            )
            .then(pl.col("cash_net_pnl_usd") / capital * 100.0)
            .otherwise(0.0)
            .alias("cash_net_pct"),
        )
        .with_columns(
            pl.col("cash_gross_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_gross_pct"),
            pl.col("cash_trade_cost_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_trade_cost_pct"),
            pl.col("cash_roll_cost_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_roll_cost_pct"),
            pl.col("cash_total_cost_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_total_cost_pct"),
            pl.col("cash_net_pct")
            .fill_null(0.0)
            .cum_sum()
            .alias("cum_cash_net_pct"),
        )
    )
    # This cash-audit builder is consumed directly by notebooks as well as
    # persisted by selection, so expose its completed frame with the shared
    # compact schema. All calculations above use descriptive working names.
    public = rename_public_report_columns(result)
    # Keep per-contract risk metrics beside the point-volatility inputs they
    # derive from, rather than buried among position and P&L audit columns.
    point_vol_columns = [
        column
        for column in (
            "fast_point_vol",
            "slow_point_vol",
            "point_vol",
            "mixed_point_vol",
        )
        if column in public.columns
    ]
    risk_columns = [
        column
        for column in ("daily_dvol", "ann_dvol", "ann_pct_vol")
        if column in public.columns
    ]
    if point_vol_columns and risk_columns:
        remaining = [
            column for column in public.columns
            if column not in risk_columns
        ]
        insert_after = max(remaining.index(column) for column in point_vol_columns)
        ordered = (
            remaining[:insert_after + 1]
            + risk_columns
            + remaining[insert_after + 1:]
        )
        public = public.select(ordered)
    # Keep the executable, held, traded, and roll contract quantities together
    # so the position transition can be audited without scanning the frame.
    contract_state_columns = [
        column
        for column in (
            "held_con",
            "trade_con",
            "roll_con",
        )
        if column in public.columns
    ]
    if "exec_con" in public.columns and contract_state_columns:
        remaining = [
            column for column in public.columns
            if column not in contract_state_columns
        ]
        insert_after = remaining.index("exec_con") + 1
        ordered = (
            remaining[:insert_after]
            + contract_state_columns
            + remaining[insert_after:]
        )
        public = public.select(ordered)
    return public


def build_fixed_capital_portfolio_curve(
    cash_pnl_long: pl.DataFrame,
    *,
    initial_capital_usd: float,
) -> pl.DataFrame:
    """Aggregate selected-instrument cash P&L into one portfolio curve.

    Parameters
    ----------
    cash_pnl_long
        Long selected-book frame with one row per instrument and date. It must
        contain ``symbol``, ``date``, ``pnl_valid``, and compact gross, cost,
        and net amount columns produced by
        :func:`build_executable_cash_pnl_curve`.
    initial_capital_usd
        Positive USD account capital used as the common denominator for every
        instrument and date.

    Returns
    -------
    polars.DataFrame
        One row per date with summed daily USD cash flows, fixed-capital daily
        and cumulative percentage-point returns/costs, gross and net equity
        paths, and instrument coverage counts.

    Notes
    -----
    This is a fixed-capital audit curve. The executable positions were sized
    from ``initial_capital_usd`` before their cash P&L was calculated, so the
    curve does not resize positions as equity changes. Null or non-finite cash
    values contribute zero to the daily sum; ``portfolio_valid_pnl_instruments``
    records how many instrument rows supplied a valid P&L observation.
    """
    if not math.isfinite(initial_capital_usd) or initial_capital_usd <= 0:
        raise ValueError("initial_capital_usd must be finite and positive")

    cash_columns = (
        ("gross_pnl", "portfolio_cash_gross_pnl_usd"),
        ("trade_cost", "portfolio_cash_trade_cost_usd"),
        ("roll_cost", "portfolio_cash_roll_cost_usd"),
        ("net_pnl", "portfolio_cash_net_pnl_usd"),
    )
    required = {"date", "symbol", "pnl_valid"} | {
        source for source, _ in cash_columns
    }
    missing = sorted(required.difference(cash_pnl_long.columns))
    if missing:
        raise ValueError(f"portfolio curve input missing columns: {missing}")

    def finite_or_zero(column: str) -> pl.Expr:
        """Return a finite cash value or zero for portfolio aggregation."""
        return (
            pl.when(pl.col(column).is_not_null() & pl.col(column).is_finite())
            .then(pl.col(column))
            .otherwise(0.0)
        )

    daily = (
        cash_pnl_long.group_by("date")
        .agg(
            pl.col("symbol")
            .n_unique()
            .cast(pl.Int64)
            .alias("portfolio_instrument_count"),
            pl.col("pnl_valid")
            .fill_null(False)
            .cast(pl.Int64)
            .sum()
            .alias("portfolio_valid_pnl_instruments"),
            *[
                finite_or_zero(source)
                .sum()
                .alias(aggregate)
                for source, aggregate in cash_columns
            ],
        )
        .sort("date")
        .with_columns(
            pl.lit(initial_capital_usd).alias("initial_capital_usd"),
            (pl.col("portfolio_valid_pnl_instruments") > 0).alias(
                "portfolio_return_valid"
            ),
        )
        .with_columns(
            (pl.col("portfolio_cash_gross_pnl_usd") / initial_capital_usd * 100.0)
            .alias("portfolio_gross_pct"),
            (pl.col("portfolio_cash_net_pnl_usd") / initial_capital_usd * 100.0)
            .alias("portfolio_net_pct"),
            (pl.col("portfolio_cash_trade_cost_usd") / initial_capital_usd * 100.0)
            .alias("portfolio_trade_cost_pct"),
            (pl.col("portfolio_cash_roll_cost_usd") / initial_capital_usd * 100.0)
            .alias("portfolio_roll_cost_pct"),
            (
                (
                    pl.col("portfolio_cash_trade_cost_usd")
                    + pl.col("portfolio_cash_roll_cost_usd")
                )
                / initial_capital_usd
                * 100.0
            ).alias("portfolio_total_cost_pct"),
        )
        .with_columns(
            pl.col("portfolio_cash_gross_pnl_usd")
            .cum_sum()
            .alias("cumulative_portfolio_cash_gross_pnl_usd"),
            pl.col("portfolio_cash_net_pnl_usd")
            .cum_sum()
            .alias("cumulative_portfolio_cash_net_pnl_usd"),
        )
        .with_columns(
            (
                pl.col("cumulative_portfolio_cash_gross_pnl_usd")
                / initial_capital_usd
                * 100.0
            ).alias("cumulative_portfolio_gross_pct"),
            (
                pl.col("cumulative_portfolio_cash_net_pnl_usd")
                / initial_capital_usd
                * 100.0
            ).alias("cumulative_portfolio_net_pct"),
            (
                pl.col("portfolio_cash_trade_cost_usd").cum_sum()
                / initial_capital_usd
                * 100.0
            ).alias("cumulative_portfolio_trade_cost_pct"),
            (
                pl.col("portfolio_cash_roll_cost_usd").cum_sum()
                / initial_capital_usd
                * 100.0
            ).alias("cumulative_portfolio_roll_cost_pct"),
            (
                (
                    pl.col("portfolio_cash_trade_cost_usd").cum_sum()
                    + pl.col("portfolio_cash_roll_cost_usd").cum_sum()
                )
                / initial_capital_usd
                * 100.0
            ).alias("cumulative_portfolio_total_cost_pct"),
            (
                initial_capital_usd
                + pl.col("cumulative_portfolio_cash_gross_pnl_usd")
            ).alias("portfolio_gross_equity_usd"),
            (
                initial_capital_usd
                + pl.col("cumulative_portfolio_cash_net_pnl_usd")
            ).alias("portfolio_net_equity_usd"),
        )
    )
    return daily

"""Build reference percentages, executable cash P&L, and portfolio audit curves.

Forecast construction owns the combined forecast. This module owns the next
calculation boundary: size and lag positions, measure turnover from those
positions, mark them through daily point changes, convert through the contract
multiplier and historical FX, and accrue annual SR cost through elapsed
calendar time. The subsystem-reference path uses a fixed equal-weight book
assumption to produce comparable instrument returns for correlation estimation.
The separate selected-book cash path fixes final weights and IDM, scales every
instrument from common lagged net equity, buffers and rounds to executable
contracts, and charges ordinary trades and contract rolls separately. Its long
instrument output then aggregates into gross and net portfolio equity paths.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import polars as pl

from derivatives_bt_engine.reports.formatting import (
    PUBLIC_REPORT_RENAMES,
)
from derivatives_bt_engine.reports.formatting import rename_public_report_columns

from derivatives_bt_engine.calculations.ewmac import EWMAC_FORECAST_TARGET_ABS
from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
)


PYSYSTEMTRADE_FORECAST_BUFFER_SIZE = 0.10


def _with_source_gap_audit(frame: pl.DataFrame) -> pl.DataFrame:
    """Return ``frame`` with normalized source-gap audit columns.

    A permitted history-source gap has no rows of its own. The first retained
    row afterward carries the event, missing calendar-day count, and explicit
    statement that no cross-gap market P&L was inferred. These fields do not
    change return validity or any sizing, cost, or P&L expression.
    """
    expressions: list[pl.Expr] = []
    if "source_gap_before" in frame.columns:
        expressions.append(
            pl.col("source_gap_before")
            .fill_null(False)
            .cast(pl.Boolean)
        )
    else:
        expressions.append(pl.lit(False).alias("source_gap_before"))
    if "source_gap_days" in frame.columns:
        expressions.append(pl.col("source_gap_days").cast(pl.Int64))
    else:
        expressions.append(
            pl.lit(None, dtype=pl.Int64).alias("source_gap_days")
        )
    if "gap_pnl_omitted" in frame.columns:
        expressions.append(
            pl.col("gap_pnl_omitted")
            .fill_null(False)
            .cast(pl.Boolean)
        )
    else:
        expressions.append(pl.lit(False).alias("gap_pnl_omitted"))
    return frame.with_columns(*expressions)


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
    "vol_scalar",
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


def summarize_return_significance(
    frame: pl.DataFrame,
    *,
    return_col: str,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
) -> pl.DataFrame:
    """Summarize one percentage-point return series and its IID t-statistic.

    Parameters
    ----------
    frame
        Daily observations containing ``date`` and ``return_col``. Return
        values are percentage points measured against the series' applicable
        prior-equity or fixed-budget denominator.
    return_col
        Column holding the daily percentage-point return series.
    annualization_days
        Trading-day count used to annualize its sample standard deviation.

    Returns
    -------
    polars.DataFrame
        One row with the sample dates and size, elapsed calendar years,
        cumulative and annualized return percentages, annualized volatility,
        Sharpe ratio, and approximate IID t-statistic.

    Notes
    -----
    The t-statistic is the annualized Sharpe ratio times the square root of
    elapsed years. It does not adjust for serial dependence, non-normal tails,
    or repeated strategy comparisons.
    """
    required = {"date", return_col}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            f"return significance input missing columns: {missing}"
        )
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")
    sample = (
        frame.select("date", return_col)
        .filter(
            pl.col(return_col).is_not_null()
            & pl.col(return_col).is_finite()
        )
        .sort("date")
    )
    if sample.height < 2:
        raise ValueError(
            "return significance requires at least two observations"
        )
    start_date = sample.get_column("date").min()
    end_date = sample.get_column("date").max()
    if start_date is None or end_date is None or end_date <= start_date:
        raise ValueError("return significance requires a positive date span")
    hist_years = (end_date - start_date).days / 365.25
    returns = sample.get_column(return_col)
    cumulative_return_pct = float(returns.sum())
    daily_vol_pct = returns.std(ddof=1)
    if daily_vol_pct is None or not math.isfinite(daily_vol_pct):
        raise ValueError("return volatility is not finite")
    annual_mean_pct = cumulative_return_pct / hist_years
    annual_vol_pct = daily_vol_pct * math.sqrt(annualization_days)
    sharpe = (
        annual_mean_pct / annual_vol_pct
        if annual_vol_pct > 0
        else None
    )
    iid_t_stat = (
        sharpe * math.sqrt(hist_years)
        if sharpe is not None
        else None
    )
    return pl.DataFrame({
        "start": [start_date],
        "end": [end_date],
        "n_obs": [sample.height],
        "hist_years": [hist_years],
        "cum_return_pct": [cumulative_return_pct],
        "ann_mean_pct": [annual_mean_pct],
        "ann_vol_pct": [annual_vol_pct],
        "sharpe": [sharpe],
        "iid_t_stat": [iid_t_stat],
    })


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
        Chronological reference frame containing ``vol_scalar`` and
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
        The input plus smoothed ``ref_avg_pos``, ``ref_norm_pos``, absolute
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
    required = {"vol_scalar", "ref_tgt_pos"}
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
            pl.col("vol_scalar")
            .ewm_mean(
                com=average_position_ewm_com,
                adjust=True,
                min_samples=2,
            )
            .alias("ref_avg_pos")
        )
        .with_columns(
            pl.when(pl.col("ref_avg_pos") > 0)
            .then(
                pl.col("ref_tgt_pos")
                / pl.col("ref_avg_pos")
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
        The forecast context uses the shared report tokens ``comb_fcst`` and
        ``fcst_valid`` so reference frames are directly usable in audits.

    Notes
    -----
    This path is continuous and deliberately does not round to whole
    contracts. It represents every candidate at the same assumed equal-weight
    allocation, independent of the partial greedy book. Only
    :func:`build_executable_cash_pnl_curve` represents the selected portfolio's
    final weights, IDM, buffering, discrete contracts, and cash P&L.
    """
    frame = _with_source_gap_audit(frame)
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
            .alias("vol_scalar"),
        )
        .with_columns(
            (
                pl.col("vol_scalar")
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
    result = apply_subsystem_reference_cost(
        reference,
        ann_cost_sr=ann_cost_sr,
    )
    # Reference curves are also the notebook/audit boundary. Apply the
    # centralized tokens here so callers do not have to rename signal context
    # after every cost or turnover transformation.
    signal_renames = {
        source: PUBLIC_REPORT_RENAMES[source]
        for source in ("combined_forecast", "forecast_valid")
        if source in result.columns
    }
    return result.rename(signal_renames) if signal_renames else result


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
        ``combined_forecast`` (or its public alias ``comb_fcst``), point
        volatility, historical USD FX, and an optional boolean roll marker.
        ``close`` may be the locally generated
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
    # Reference-curve outputs use public compact names; selection passes that
    # frame directly into this calculation. Normalize the alias here so all
    # sizing expressions below retain the descriptive internal column name.
    if "combined_forecast" not in frame.columns and "comb_fcst" in frame.columns:
        frame = frame.rename({"comb_fcst": "combined_forecast"})
    frame = _with_source_gap_audit(frame)

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
            .alias("vol_scalar")
        )
        .with_columns(
            (
                pl.col("vol_scalar")
                * pl.col("combined_forecast")
                / target_abs_forecast
            ).alias("target_position")
        )
    )
    # The instantaneous position scale is a volatility scalar, not a smoothed
    # historical average position. It already includes weight and IDM, as in
    # pysystemtrade's portfolio forecast sizing.
    result = (
        apply_forecast_position_buffer(
            target_frame,
            average_position_col="vol_scalar",
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
            "pt_vol",
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


def build_equity_scaled_cash_pnl_curve(
    cash_pnl_long: pl.DataFrame,
    *,
    initial_capital_usd: float,
    target_vol: float,
    multiplier_by_symbol: dict[str, float],
    target_abs_forecast: float = EWMAC_FORECAST_TARGET_ABS,
    allocation_provider: Callable[
        [object, float], dict[str, dict[str, float | int]] | None
    ] | None = None,
) -> pl.DataFrame:
    """Build selected-book cash P&L from lagged portfolio net equity.

    The selected instruments, structural weights, capped sizing weights, and
    IDM remain fixed. On each portfolio date, every instrument uses the same
    prior net equity to calculate its risk budget, average-contract volatility
    scalar, forecast target, and buffer width. Gross P&L comes from the
    previously held contracts; trades and costs then update the equity used on
    the next date.

    Parameters
    ----------
    cash_pnl_long
        Long selected-book cash frame in compact public-report vocabulary. It
        must contain the forecast, contract volatility, weights, buffer policy,
        costs, FX, and point changes needed to replay executable positions.
    initial_capital_usd
        Positive initial net and gross portfolio equity in USD.
    target_vol
        Annual portfolio volatility target as a decimal fraction.
    multiplier_by_symbol
        Native-currency point-value multiplier for every selected symbol.
    target_abs_forecast
        Mean absolute forecast represented by one average position.
    allocation_provider
        Optional walk-forward book callback. On each portfolio date it receives
        the date and prior net equity. Returning ``None`` retains the current
        book; returning a symbol-to-allocation mapping replaces it. Each active
        allocation must provide positive ``weight``, ``limit_weight``, and
        ``idm`` values and may provide ``rank``. Symbols omitted from a new
        mapping are closed on their next executable row. When omitted, the
        input's fixed allocation is used exactly as before.

    Returns
    -------
    polars.DataFrame
        The input with position, P&L, cost, and percentage fields replaced by
        the equity-scaled causal replay. ``vol_scalar`` is the executable daily
        average-contract scalar; ``init_vol_scalar`` preserves its
        initial-capital baseline and ``cap_scalar`` reports the common equity
        scaling factor.

    Notes
    -----
    The state transition is intentionally explicit because each date's net
    cash result controls every instrument's next sizing capital. The shared
    capital state cannot be reproduced by independently vectorizing each
    instrument. Percentage columns are daily portfolio-return contributions:
    gross contributions use prior gross equity, while net and cost
    contributions use prior net equity.
    """
    required = {
        "date",
        "symbol",
        "weight",
        "comb_fcst",
        "pt_change",
        "fx_to_usd",
        "ann_dvol",
        "limit_weight",
        "idm",
        "buf_size",
        "buf_trade_to_edge",
        "rebalance_on_roll",
        "roll_event",
        "trade_cost_per_con",
        "roll_cost_per_con",
    }
    missing = sorted(required.difference(cash_pnl_long.columns))
    if missing:
        raise ValueError(f"equity-scaled cash input missing columns: {missing}")
    if cash_pnl_long.is_empty():
        raise ValueError("equity-scaled cash input must contain at least one row")
    if (
        cash_pnl_long.select("date", "symbol").unique().height
        != cash_pnl_long.height
    ):
        raise ValueError(
            "equity-scaled cash input must have one row per date and symbol"
        )
    if not math.isfinite(initial_capital_usd) or initial_capital_usd <= 0:
        raise ValueError("initial_capital_usd must be finite and positive")
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise ValueError("target_vol must be finite and positive")
    if not math.isfinite(target_abs_forecast) or target_abs_forecast <= 0:
        raise ValueError("target_abs_forecast must be finite and positive")

    static_sizing = cash_pnl_long.select(
        "symbol",
        "weight",
        "limit_weight",
        "idm",
    ).unique()
    changing_sizing = (
        static_sizing.group_by("symbol")
        .len()
        .filter(pl.col("len") != 1)
        .get_column("symbol")
        .to_list()
    )
    if changing_sizing and allocation_provider is None:
        raise ValueError(
            "selected-book weights and IDM must remain fixed for: "
            + ", ".join(sorted(str(symbol) for symbol in changing_sizing))
        )
    for sizing_row in static_sizing.iter_rows(named=True):
        if allocation_provider is not None:
            continue
        for column in ("weight", "limit_weight", "idm"):
            value = sizing_row[column]
            if (
                value is None
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError(
                    f"{column} must be finite and positive for "
                    f"{sizing_row['symbol']}"
                )

    symbols = sorted(
        str(symbol)
        for symbol in cash_pnl_long.get_column("symbol").unique().to_list()
    )
    missing_multipliers = sorted(set(symbols).difference(multiplier_by_symbol))
    if missing_multipliers:
        raise ValueError(
            "missing point-value multipliers for: "
            + ", ".join(missing_multipliers)
        )
    invalid_multipliers = sorted(
        symbol
        for symbol in symbols
        if not math.isfinite(multiplier_by_symbol[symbol])
        or multiplier_by_symbol[symbol] <= 0
    )
    if invalid_multipliers:
        raise ValueError(
            "point-value multipliers must be finite and positive for: "
            + ", ".join(invalid_multipliers)
        )

    def finite_float(value: object) -> float | None:
        """Return a finite float or ``None`` for an unusable row value."""
        if value is None:
            return None
        converted = float(value)
        return converted if math.isfinite(converted) else None

    def rounded_contracts(value: float | None) -> int | None:
        """Round a continuous position with the existing ties-to-even rule."""
        return None if value is None else int(round(value))

    states: dict[str, dict[str, float | int | None]] = {
        symbol: {
            "exec_contracts": None,
            "cumulative_gross_pnl": 0.0,
            "cumulative_net_pnl": 0.0,
            "cumulative_gross_pct": 0.0,
            "cumulative_trade_cost_pct": 0.0,
            "cumulative_roll_cost_pct": 0.0,
            "cumulative_total_cost_pct": 0.0,
            "cumulative_net_pct": 0.0,
            "selection_active": False,
        }
        for symbol in symbols
    }
    gross_equity = initial_capital_usd
    net_equity = initial_capital_usd
    output_rows: list[dict[str, object]] = []
    if allocation_provider is None:
        current_allocations: dict[str, dict[str, float | int]] = {
            str(row["symbol"]): {
                "weight": float(row["weight"]),
                "limit_weight": float(row["limit_weight"]),
                "idm": float(row["idm"]),
            }
            for row in static_sizing.iter_rows(named=True)
        }
    else:
        current_allocations = {}
    selection_effective_date: object | None = None

    ordered = cash_pnl_long.sort("date", "symbol")
    for date_frame in ordered.partition_by("date", maintain_order=True):
        date_value = date_frame.get_column("date")[0]
        prior_gross_equity = gross_equity
        prior_net_equity = net_equity
        if not math.isfinite(prior_net_equity) or prior_net_equity <= 0:
            raise ValueError(
                "portfolio net equity must remain positive before sizing; "
                f"date={date_value} equity={prior_net_equity}"
            )
        capital_scalar = prior_net_equity / initial_capital_usd
        date_gross_pnl = 0.0
        date_net_pnl = 0.0
        selection_rebalanced = False
        if allocation_provider is not None:
            replacement = allocation_provider(date_value, prior_net_equity)
            if replacement is not None:
                unknown = sorted(set(replacement).difference(symbols))
                if unknown:
                    raise ValueError(
                        "walk-forward allocation contains unknown symbols: "
                        + ", ".join(unknown)
                    )
                for allocation_symbol, allocation in replacement.items():
                    for column in ("weight", "limit_weight", "idm"):
                        value = allocation.get(column)
                        if (
                            value is None
                            or not math.isfinite(float(value))
                            or float(value) <= 0
                        ):
                            raise ValueError(
                                f"walk-forward {column} must be finite and "
                                f"positive for {allocation_symbol} on {date_value}"
                            )
                current_allocations = replacement
                selection_effective_date = date_value
                selection_rebalanced = True

        for source_row in date_frame.iter_rows(named=True):
            row = dict(source_row)
            symbol = str(row["symbol"])
            state = states[symbol]
            allocation = current_allocations.get(symbol)
            selection_active = allocation is not None
            annual_dollar_vol = finite_float(row["ann_dvol"])
            weight = (
                finite_float(allocation.get("weight"))
                if allocation is not None
                else None
            )
            limit_weight = (
                finite_float(allocation.get("limit_weight"))
                if allocation is not None
                else None
            )
            idm = (
                finite_float(allocation.get("idm"))
                if allocation is not None
                else None
            )
            combined_forecast = finite_float(row["comb_fcst"])
            buffer_size = finite_float(row["buf_size"])
            if selection_active and (limit_weight is None or limit_weight <= 0):
                raise ValueError(f"invalid limit_weight for {symbol} on {date_value}")
            if selection_active and (idm is None or idm <= 0):
                raise ValueError(f"invalid idm for {symbol} on {date_value}")
            if buffer_size is None or buffer_size < 0:
                raise ValueError(f"invalid buf_size for {symbol} on {date_value}")

            initial_risk_budget = (
                initial_capital_usd * target_vol * limit_weight * idm
                if selection_active
                and limit_weight is not None
                and idm is not None
                else 0.0
            )
            risk_budget = (
                prior_net_equity * target_vol * limit_weight * idm
                if selection_active
                and limit_weight is not None
                and idm is not None
                else 0.0
            )
            if (
                selection_active
                and annual_dollar_vol is not None
                and annual_dollar_vol > 0
            ):
                initial_volatility_scalar = (
                    initial_risk_budget / annual_dollar_vol
                )
                volatility_scalar = (
                    initial_volatility_scalar * capital_scalar
                )
            else:
                initial_volatility_scalar = None
                volatility_scalar = None
            target_position = (
                volatility_scalar * combined_forecast / target_abs_forecast
                if volatility_scalar is not None
                and combined_forecast is not None
                else None
            )
            buffer_width = (
                abs(volatility_scalar) * buffer_size
                if volatility_scalar is not None
                else None
            )
            rounded_optimal = rounded_contracts(target_position)
            buffer_top = rounded_contracts(
                target_position + buffer_width
                if target_position is not None and buffer_width is not None
                else None
            )
            buffer_bottom = rounded_contracts(
                target_position - buffer_width
                if target_position is not None and buffer_width is not None
                else None
            )

            previous_exec = state["exec_contracts"]
            held_contracts = (
                int(previous_exec) if previous_exec is not None else None
            )
            if not selection_active:
                exec_contracts = 0
            elif previous_exec is None:
                exec_contracts = (
                    rounded_optimal if rounded_optimal is not None else 0
                )
            elif (
                rounded_optimal is None
                or buffer_top is None
                or buffer_bottom is None
            ):
                exec_contracts = int(previous_exec)
            elif bool(row["rebalance_on_roll"]) and bool(row["roll_event"]):
                exec_contracts = rounded_optimal
            elif int(previous_exec) > buffer_top:
                exec_contracts = (
                    buffer_top
                    if bool(row["buf_trade_to_edge"])
                    else rounded_optimal
                )
            elif int(previous_exec) < buffer_bottom:
                exec_contracts = (
                    buffer_bottom
                    if bool(row["buf_trade_to_edge"])
                    else rounded_optimal
                )
            else:
                exec_contracts = int(previous_exec)

            signed_trade_contracts = float(
                exec_contracts
                - (held_contracts if held_contracts is not None else 0)
            )
            roll_event = bool(row["roll_event"])
            roll_contracts = (
                float(min(abs(held_contracts), abs(exec_contracts)))
                if roll_event
                and held_contracts is not None
                and held_contracts * exec_contracts > 0
                else 0.0
            )
            point_change = finite_float(row["pt_change"])
            fx_to_usd = finite_float(row["fx_to_usd"])
            gross_pnl = (
                held_contracts
                * point_change
                * multiplier_by_symbol[symbol]
                * fx_to_usd
                if held_contracts is not None
                and point_change is not None
                and fx_to_usd is not None
                else None
            )
            trade_cost_per_contract = finite_float(row["trade_cost_per_con"])
            roll_cost_per_contract = finite_float(row["roll_cost_per_con"])
            if allocation is not None:
                trade_cost_per_contract = finite_float(
                    allocation.get(
                        "trade_cost_per_con",
                        trade_cost_per_contract,
                    )
                )
                roll_cost_per_contract = finite_float(
                    allocation.get(
                        "roll_cost_per_con",
                        roll_cost_per_contract,
                    )
                )
            if trade_cost_per_contract is None or trade_cost_per_contract < 0:
                raise ValueError(
                    f"invalid trade cost for {symbol} on {date_value}"
                )
            if roll_cost_per_contract is None or roll_cost_per_contract < 0:
                raise ValueError(
                    f"invalid roll cost for {symbol} on {date_value}"
                )
            trade_cost = -abs(signed_trade_contracts) * trade_cost_per_contract
            roll_cost = -roll_contracts * roll_cost_per_contract
            gross_pnl_or_zero = gross_pnl if gross_pnl is not None else 0.0
            net_pnl = gross_pnl_or_zero + trade_cost + roll_cost

            gross_pct = (
                gross_pnl_or_zero / prior_gross_equity * 100.0
                if prior_gross_equity > 0
                else None
            )
            trade_cost_pct = trade_cost / prior_net_equity * 100.0
            roll_cost_pct = roll_cost / prior_net_equity * 100.0
            total_cost_pct = trade_cost_pct + roll_cost_pct
            net_pct = net_pnl / prior_net_equity * 100.0

            state["exec_contracts"] = exec_contracts
            state["selection_active"] = selection_active
            state["cumulative_gross_pnl"] = (
                float(state["cumulative_gross_pnl"]) + gross_pnl_or_zero
            )
            state["cumulative_net_pnl"] = (
                float(state["cumulative_net_pnl"]) + net_pnl
            )
            if gross_pct is not None:
                state["cumulative_gross_pct"] = (
                    float(state["cumulative_gross_pct"]) + gross_pct
                )
            state["cumulative_trade_cost_pct"] = (
                float(state["cumulative_trade_cost_pct"]) + trade_cost_pct
            )
            state["cumulative_roll_cost_pct"] = (
                float(state["cumulative_roll_cost_pct"]) + roll_cost_pct
            )
            state["cumulative_total_cost_pct"] = (
                float(state["cumulative_total_cost_pct"]) + total_cost_pct
            )
            state["cumulative_net_pct"] = (
                float(state["cumulative_net_pct"]) + net_pct
            )

            if allocation_provider is not None:
                row.update({
                    "selection_active": selection_active,
                    "selection_rebalanced": selection_rebalanced,
                    "selection_effective_date": selection_effective_date,
                    "selection_rank": (
                        int(allocation["rank"])
                        if allocation is not None and "rank" in allocation
                        else None
                    ),
                })
            row.update({
                "weight": weight,
                "limit_weight": limit_weight,
                "idm": idm,
                "trade_cost_per_con": trade_cost_per_contract,
                "roll_cost_per_con": roll_cost_per_contract,
                "sizing_capital_usd": prior_net_equity,
                "capital_scalar": capital_scalar,
                "initial_capital_volatility_scalar": (
                    initial_volatility_scalar
                ),
                "risk_budget": risk_budget,
                "vol_scalar": volatility_scalar,
                "tgt_pos": target_position,
                "buf_width_pos": buffer_width,
                "buffer_top_con": buffer_top,
                "buffer_bot_con": buffer_bottom,
                "exec_con": exec_contracts,
                "held_con": held_contracts,
                "trade_con": signed_trade_contracts,
                "roll_con": roll_contracts,
                "roll_rebalanced": (
                    bool(row["rebalance_on_roll"])
                    and roll_event
                    and target_position is not None
                ),
                "gross_pnl": gross_pnl,
                "trade_cost": trade_cost,
                "roll_cost": roll_cost,
                "net_pnl": net_pnl,
                "gross_pct": gross_pct if gross_pct is not None else 0.0,
                "trade_cost_pct": trade_cost_pct,
                "roll_cost_pct": roll_cost_pct,
                "total_cost_pct": total_cost_pct,
                "net_pct": net_pct,
                "cum_gross_pnl": state["cumulative_gross_pnl"],
                "cum_net_pnl": state["cumulative_net_pnl"],
                "cum_gross_pct": state["cumulative_gross_pct"],
                "cum_trade_cost_pct": state["cumulative_trade_cost_pct"],
                "cum_roll_cost_pct": state["cumulative_roll_cost_pct"],
                "cum_total_cost_pct": state["cumulative_total_cost_pct"],
                "cum_net_pct": state["cumulative_net_pct"],
                "pnl_valid": (
                    math.isfinite(net_pnl)
                    and (
                        allocation_provider is None
                        or selection_active
                        or (held_contracts not in (None, 0))
                        or signed_trade_contracts != 0
                    )
                ),
            })
            output_rows.append(row)
            date_gross_pnl += gross_pnl_or_zero
            date_net_pnl += net_pnl

        gross_equity += date_gross_pnl
        net_equity += date_net_pnl

    result = pl.DataFrame(output_rows, infer_schema_length=None)
    return rename_public_report_columns(result)


def build_portfolio_equity_curve(
    cash_pnl_long: pl.DataFrame,
    *,
    initial_capital_usd: float,
) -> pl.DataFrame:
    """Aggregate selected-book cash P&L into gross and net equity curves.

    Parameters
    ----------
    cash_pnl_long
        Long selected-book frame with one row per instrument and date. It must
        contain ``symbol``, ``date``, ``pnl_valid``, and compact gross, cost,
        and net amount columns produced by a selected-book cash builder.
    initial_capital_usd
        Positive initial USD account capital used to seed the equity paths and
        calculate cumulative returns.

    Returns
    -------
    polars.DataFrame
        One row per date with summed daily USD cash flows, daily returns based
        on the applicable prior equity, cumulative returns relative to initial
        capital, cost contributions, equity paths, and coverage counts.

    Notes
    -----
    Position-sizing semantics belong to the supplied cash frame. The current
    selected-book pipeline supplies positions sized from lagged net equity.
    Daily gross return divides gross cash P&L by prior gross equity, while
    daily net return and cost rates divide by prior net equity.
    Null or non-finite cash values contribute zero to the daily sum;
    ``portfolio_valid_pnl_instruments`` records how many instrument rows
    supplied a valid P&L observation.
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

    active_column = (
        "sel_active" if "sel_active" in cash_pnl_long.columns else None
    )
    instrument_count = (
        pl.col("symbol")
        .filter(pl.col(active_column).fill_null(False))
        .n_unique()
        if active_column is not None
        else pl.col("symbol").n_unique()
    )
    instrument_list = (
        pl.col("symbol")
        .filter(pl.col(active_column).fill_null(False))
        if active_column is not None
        else pl.col("symbol")
    )
    daily = (
        cash_pnl_long.group_by("date")
        .agg(
            instrument_count
            .cast(pl.Int64)
            .alias("portfolio_instrument_count"),
            instrument_list
            .drop_nulls()
            .unique()
            .sort()
            .str.join(",")
            .alias("portfolio_instruments"),
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
            pl.col("portfolio_cash_gross_pnl_usd")
            .cum_sum()
            .alias("cumulative_portfolio_cash_gross_pnl_usd"),
            pl.col("portfolio_cash_net_pnl_usd")
            .cum_sum()
            .alias("cumulative_portfolio_cash_net_pnl_usd"),
        )
        .with_columns(
            (
                initial_capital_usd
                + pl.col("cumulative_portfolio_cash_gross_pnl_usd")
            ).alias("portfolio_gross_equity_usd"),
            (
                initial_capital_usd
                + pl.col("cumulative_portfolio_cash_net_pnl_usd")
            ).alias("portfolio_net_equity_usd"),
        )
        .with_columns(
            pl.col("portfolio_gross_equity_usd")
            .shift(1)
            .fill_null(initial_capital_usd)
            .alias("prior_portfolio_gross_equity_usd"),
            pl.col("portfolio_net_equity_usd")
            .shift(1)
            .fill_null(initial_capital_usd)
            .alias("prior_portfolio_net_equity_usd"),
        )
        .with_columns(
            pl.when(
                pl.col("prior_portfolio_gross_equity_usd").is_finite()
                & (pl.col("prior_portfolio_gross_equity_usd") > 0)
            )
            .then(
                pl.col("portfolio_cash_gross_pnl_usd")
                / pl.col("prior_portfolio_gross_equity_usd")
                * 100.0
            )
            .otherwise(None)
            .alias("portfolio_gross_pct"),
            pl.when(
                pl.col("prior_portfolio_net_equity_usd").is_finite()
                & (pl.col("prior_portfolio_net_equity_usd") > 0)
            )
            .then(
                pl.col("portfolio_cash_net_pnl_usd")
                / pl.col("prior_portfolio_net_equity_usd")
                * 100.0
            )
            .otherwise(None)
            .alias("portfolio_net_pct"),
            *[
                pl.when(
                    pl.col("prior_portfolio_net_equity_usd").is_finite()
                    & (pl.col("prior_portfolio_net_equity_usd") > 0)
                )
                .then(
                    pl.col(source)
                    / pl.col("prior_portfolio_net_equity_usd")
                    * 100.0
                )
                .otherwise(None)
                .alias(output)
                for source, output in (
                    ("portfolio_cash_trade_cost_usd", "portfolio_trade_cost_pct"),
                    ("portfolio_cash_roll_cost_usd", "portfolio_roll_cost_pct"),
                )
            ],
            pl.when(
                pl.col("prior_portfolio_net_equity_usd").is_finite()
                & (pl.col("prior_portfolio_net_equity_usd") > 0)
            )
            .then(
                (
                    pl.col("portfolio_cash_trade_cost_usd")
                    + pl.col("portfolio_cash_roll_cost_usd")
                )
                / pl.col("prior_portfolio_net_equity_usd")
                * 100.0
            )
            .otherwise(None)
            .alias("portfolio_total_cost_pct"),
            (
                (pl.col("portfolio_valid_pnl_instruments") > 0)
                & pl.col("prior_portfolio_net_equity_usd").is_finite()
                & (pl.col("prior_portfolio_net_equity_usd") > 0)
            ).alias("portfolio_return_valid"),
        )
        .with_columns(
            (
                pl.col("portfolio_gross_equity_usd")
                / initial_capital_usd
                - 1.0
            )
            .mul(100.0)
            .alias("cumulative_portfolio_gross_pct"),
        )
        .with_columns(
            (
                (
                    pl.col("portfolio_net_equity_usd")
                    / initial_capital_usd
                    - 1.0
                )
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
        )
        .drop(
            "prior_portfolio_gross_equity_usd",
            "prior_portfolio_net_equity_usd",
        )
    )
    return daily


def build_fixed_capital_portfolio_curve(
    cash_pnl_long: pl.DataFrame,
    *,
    initial_capital_usd: float,
) -> pl.DataFrame:
    """Return the portfolio equity curve through the legacy function name.

    The name is retained for notebook and API compatibility. Position sizing
    semantics belong to the supplied cash frame; the current selected-book
    pipeline passes equity-scaled positions.
    """
    return build_portfolio_equity_curve(
        cash_pnl_long,
        initial_capital_usd=initial_capital_usd,
    )

"""Build auditable gross, cost, and net subsystem P&L curves.

Forecast construction owns desired positions and turnover.  This module owns
the next calculation boundary: lag the position, mark it through the daily
point change, convert through the contract multiplier and historical FX, and
accrue an annual SR cost through elapsed calendar time.  It also provides an
account-scaled cash path that rounds the combined subsystem to executable
contracts and charges ordinary trades and contract rolls separately.  The
normalized net USD series remains suitable for subsystem correlation
estimation; the cash series is an execution and affordability audit.
"""

from __future__ import annotations

import math

import polars as pl

from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
)


def _round_contracts_half_up(expression: pl.Expr) -> pl.Expr:
    """Round signed continuous positions to whole contracts symmetrically."""
    return (
        pl.when(expression >= 0)
        .then((expression + 0.5).floor())
        .otherwise((expression - 0.5).ceil())
        .cast(pl.Int64)
    )


def build_subsystem_pnl_curve(
    frame: pl.DataFrame,
    *,
    multiplier: float,
    ann_cost_sr: float,
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    point_vol_col: str | None = None,
    fx_col: str = "fx_to_usd",
) -> pl.DataFrame:
    """Append lagged-position gross, cost, and net P&L columns.

    Parameters
    ----------
    frame
        One instrument's daily subsystem frame.  It must contain ``date``,
        ``point_change``, ``avg_position``, ``subsystem_position``, and a
        historical USD FX conversion.  ``mixed_point_vol`` is preferred, with
        legacy ``point_vol`` accepted for compact Phase 2 frames.
    multiplier
        Native-currency cash value of one price point for one contract.
    ann_cost_sr
        Annual total cost in SR units.  It normally combines actual subsystem
        turnover with the Phase 1 trade cost and roll-policy frequency with
        the Phase 1 roll cost.
    annualization_days
        Trading days used to turn daily point volatility into annual point
        volatility.
    point_vol_col
        Explicit point-volatility column, or ``None`` to discover the standard
        descriptive/legacy name.
    fx_col
        Column containing the causal native-currency-to-USD conversion.

    Returns
    -------
    polars.DataFrame
        The input columns plus held position, gross P&L, annual cost budget,
        elapsed-year accrual, net USD P&L, and a row-level validity flag.

    Notes
    -----
    ``avg_position`` and ``subsystem_position`` are proportional point-risk
    proxies from forecast combination.  Dividing them by multiplier and the
    historical FX rate produces positions with constant relative USD risk.
    The omitted capital/risk-target constant would multiply every observation
    and therefore cancels from correlations.
    """
    if point_vol_col is None:
        if "mixed_point_vol" in frame.columns:
            point_vol_col = "mixed_point_vol"
        elif "point_vol" in frame.columns:
            point_vol_col = "point_vol"
        else:
            raise ValueError(
                "subsystem P&L input missing point volatility; expected "
                "'mixed_point_vol' or legacy 'point_vol'"
            )

    required = {
        "date",
        "point_change",
        point_vol_col,
        "avg_position",
        "subsystem_position",
        fx_col,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"subsystem P&L input missing columns: {missing}")
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("multiplier must be finite and positive")
    if not math.isfinite(ann_cost_sr) or ann_cost_sr < 0:
        raise ValueError("ann_cost_sr must be finite and nonnegative")
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")

    annualization_sqrt = math.sqrt(annualization_days)
    return (
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
            # Forecast combination intentionally omits cash-value constants
            # because turnover is measured in average-position units. Restore
            # multiplier and dated FX here so this position has stable USD
            # point risk before calculating a cash P&L curve.
            pl.when(pl.col("fx_valid"))
            .then(pl.col("avg_position") / (multiplier * pl.col(fx_col)))
            .otherwise(None)
            .alias("pnl_avg_position"),
            pl.when(pl.col("fx_valid"))
            .then(pl.col("subsystem_position") / (multiplier * pl.col(fx_col)))
            .otherwise(None)
            .alias("pnl_position"),
        )
        .with_columns(
            # End-of-day positions earn the following observation's move.
            pl.col("pnl_position").shift(1).alias("held_pnl_position"),
        )
        .with_columns(
            (
                pl.col("held_pnl_position") * pl.col("point_change")
            ).alias("gross_pnl_pts"),
            (
                pl.col("pnl_avg_position")
                * pl.col(point_vol_col)
                * annualization_sqrt
            ).alias("ann_risk_pts"),
        )
        .with_columns(
            (pl.col("gross_pnl_pts") * multiplier).alias("gross_pnl_native"),
            (-ann_cost_sr * pl.col("ann_risk_pts")).alias("ann_cost_pts"),
        )
        .with_columns(
            (
                pl.col("gross_pnl_native") * pl.col(fx_col)
            ).alias("gross_pnl_usd"),
            # Costs begin only once a position was actually held.  Calendar
            # elapsed time spreads an annual SR charge over irregular daily
            # rows without pretending every gap is one business day.
            pl.when(
                pl.col("held_pnl_position").is_not_null()
                & pl.col("period_year_fraction").is_not_null()
            )
            .then(pl.col("ann_cost_pts") * pl.col("period_year_fraction"))
            .otherwise(None)
            .alias("cost_pnl_pts"),
        )
        .with_columns(
            (pl.col("cost_pnl_pts") * multiplier).alias("cost_pnl_native")
        )
        .with_columns(
            (
                pl.col("cost_pnl_native") * pl.col(fx_col)
            ).alias("cost_pnl_usd")
        )
        .with_columns(
            (
                pl.col("gross_pnl_usd") + pl.col("cost_pnl_usd")
            ).alias("net_pnl_usd")
        )
        .with_columns(
            (
                pl.col("net_pnl_usd").is_not_null()
                & pl.col("net_pnl_usd").is_finite()
            ).alias("pnl_valid")
        )
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
    annualization_days: int = MIXED_VOL_ANNUALIZATION_DAYS,
    point_vol_col: str | None = None,
    fx_col: str = "fx_to_usd",
    roll_col: str = "is_roll",
) -> pl.DataFrame:
    """Append account-scaled contracts and executable cash P&L diagnostics.

    Parameters
    ----------
    frame
        One combined-subsystem frame containing ``date``, ``point_change``,
        ``forecast_multiplier``, point volatility, historical USD FX, and an
        optional boolean roll marker. The position must already represent the
        final net forecast rather than separate rule sleeves.
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
    annualization_days
        Trading days used to annualize daily point volatility.
    point_vol_col
        Explicit point-volatility column, or ``None`` to discover the standard
        descriptive/legacy name.
    fx_col
        Historical native-currency-to-USD conversion column.
    roll_col
        Boolean history-policy roll marker. When absent, the output retains a
        false marker and zero realized/expected roll quantities.

    Returns
    -------
    polars.DataFrame
        The input plus daily annual dollar volatility, continuous average and
        target contracts, rounded executable and lagged contracts, ordinary
        trade quantities, realized and interval-average roll quantities, and
        gross/expected/realized cash P&L in USD.

    Notes
    -----
    ``realized_roll_cons`` uses the rounded position held immediately before a
    history-policy roll. ``expected_roll_cons`` uses mean absolute rounded
    exposure since the previous roll, matching pysystemtrade's stable
    interval-average idea while retaining the actual policy dates. Ordinary
    position changes are charged independently, including the initial entry
    from zero. Current point-in-time cost rates are intentionally applied to
    the full history; this is an execution-cost sensitivity, not a claim that
    historical commissions and spreads were constant.
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
        "forecast_multiplier",
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

    risk_budget_usd = capital * target_vol * weight * idm
    roll_expr = (
        pl.col(roll_col).fill_null(False).cast(pl.Boolean)
        if roll_col in frame.columns
        else pl.lit(False)
    )
    # Polars' default rounding is bankers rounding. Futures positions use
    # symmetric half-up rounding so +/-0.5 becomes +/-1 contract.
    rounded_target = _round_contracts_half_up(pl.col("target_cons"))

    result = (
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
                * math.sqrt(annualization_days)
                * multiplier
                * pl.col(fx_col)
            ).alias("ann_dvol"),
            pl.lit(risk_budget_usd).alias("risk_budget_usd"),
        )
        .with_columns(
            pl.when(
                pl.col("cash_fx_valid")
                & pl.col("ann_dvol").is_finite()
                & (pl.col("ann_dvol") > 0)
            )
            .then(risk_budget_usd / pl.col("ann_dvol"))
            .otherwise(None)
            .alias("avg_cons")
        )
        .with_columns(
            (pl.col("avg_cons") * pl.col("forecast_multiplier")).alias(
                "target_cons"
            )
        )
        .with_columns(rounded_target.alias("exec_cons"))
        .with_columns(
            pl.col("exec_cons").shift(1).alias("held_cons"),
            # Keep the roll row in the interval that is ending on that date.
            # The following observation starts the next exposure interval.
            pl.col("roll_event")
            .cast(pl.Int64)
            .cum_sum()
            .shift(1)
            .fill_null(0)
            .alias("roll_interval"),
        )
        .with_columns(
            pl.when(pl.col("exec_cons").is_not_null())
            .then(
                (
                    pl.col("exec_cons")
                    - pl.col("held_cons").fill_null(0)
                ).abs()
            )
            .otherwise(None)
            .cast(pl.Float64)
            .alias("trade_cons"),
            pl.when(pl.col("roll_event"))
            .then(pl.col("held_cons").abs().fill_null(0))
            .otherwise(0)
            .cast(pl.Float64)
            .alias("realized_roll_cons"),
            # Expected roll volume averages the contracts actually held over
            # the completed interval, rather than the new end-of-day target on
            # the roll row. Null initial exposure is excluded by mean().
            pl.col("held_cons")
            .abs()
            .cast(pl.Float64)
            .mean()
            .over("roll_interval")
            .alias("interval_avg_abs_cons"),
        )
        .with_columns(
            # An expected roll still has to be executable. Keep the fractional
            # interval mean beside it for audit, but charge the nearest whole
            # contract using the same half-up convention as target positions.
            _round_contracts_half_up(
                pl.when(pl.col("roll_event"))
                .then(pl.col("interval_avg_abs_cons"))
                .otherwise(0.0)
            ).alias("expected_roll_cons"),
            (
                pl.col("held_cons")
                * pl.col("point_change")
                * multiplier
                * pl.col(fx_col)
            ).alias("cash_gross_pnl_usd"),
            (-pl.col("trade_cons") * trade_cost_usd_per_con).alias(
                "cash_trade_cost_usd"
            ),
            (-pl.col("realized_roll_cons") * roll_cost_usd_per_con).alias(
                "cash_realized_roll_cost_usd"
            ),
        )
        .with_columns(
            (-pl.col("expected_roll_cons") * roll_cost_usd_per_con).alias(
                "cash_expected_roll_cost_usd"
            ),
        )
        .with_columns(
            (
                pl.col("cash_gross_pnl_usd")
                + pl.col("cash_trade_cost_usd")
                + pl.col("cash_realized_roll_cost_usd")
            ).alias("cash_realized_net_pnl_usd"),
            (
                pl.col("cash_gross_pnl_usd")
                + pl.col("cash_trade_cost_usd")
                + pl.col("cash_expected_roll_cost_usd")
            ).alias("cash_expected_net_pnl_usd"),
        )
        .with_columns(
            (
                pl.col("cash_realized_net_pnl_usd").is_not_null()
                & pl.col("cash_realized_net_pnl_usd").is_finite()
                & pl.col("cash_expected_net_pnl_usd").is_not_null()
                & pl.col("cash_expected_net_pnl_usd").is_finite()
            ).alias("cash_pnl_valid")
        )
    )
    return result

"""Build auditable gross, cost, and net subsystem P&L curves.

Forecast construction owns desired positions and turnover.  This module owns
the next calculation boundary: lag the position, mark it through the daily
point change, convert through the contract multiplier and historical FX, and
accrue an annual SR cost through elapsed calendar time.  The resulting net USD
series is suitable for subsystem correlation estimation.
"""

from __future__ import annotations

import math

import polars as pl

from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_ANNUALIZATION_DAYS,
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

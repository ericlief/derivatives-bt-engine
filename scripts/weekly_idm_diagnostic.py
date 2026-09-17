"""Compare the current daily-return IDM estimator with weekly-return variants.

This is a read-only research diagnostic.  It uses the repository's existing
continuous futures cache/database and production correlation, ERC, and IDM
functions; it does not write prices, backtest results, or configuration.

The weekly inputs in this script are PRE-COST underlying returns.  Commissions
and slippage belong in strategy selection and final net portfolio P&L, not in
``H``: synchronized fee debits can manufacture correlation without representing
an economic diversification relationship.

The ``weekly_span25`` case matches the observation frequency and EWMA decay in
current pysystemtrade's instrument-correlation defaults: weekly observations
and pandas ``ewm(span=25)``.  It remains a proxy rather than a full Carver
replication because this script compounds underlying returns, whereas Carver's
instrument IDM uses volatility-scaled subsystem P&L.

Run from the repository root::

    .venv/bin/python -m scripts.weekly_idm_diagnostic
    .venv/bin/python -m scripts.weekly_idm_diagnostic --show-roll-audit
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

from derivatives_bt_engine.domain.allocation import (
    _bounded_ewm_correlation_matrix,
    build_returns_wide,
    compute_erc_weights,
    compute_idm,
)
from derivatives_bt_engine.domain.futures_dataloader import FuturesDataLoader
from derivatives_bt_engine.domain.instruments import resolve_price_symbol
from derivatives_bt_engine.strats.tsmom_binary_vol_parity_backtest import DEFAULT_SYMBOLS
from derivatives_bt_engine.utils.logger import setup_logger


logger = setup_logger()

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "futures"
DEFAULT_WINDOW_YEARS = 3.0
DEFAULT_DAILY_HALFLIFE = 63.0
DEFAULT_WEEKLY_SPAN = 25.0
DEFAULT_MIN_DAILY_ROWS = 63
DEFAULT_MIN_WEEKLY_ROWS = 20
TRADING_DAYS_PER_WEEK = 5.0


@dataclass(frozen=True)
class EstimatorSpec:
    returns_key: str
    halflife: float
    min_rows: int


def span_to_halflife(span: float) -> float:
    """Convert pandas EWM ``span`` to this repository's EWM half-life."""
    if span <= 1.0:
        raise ValueError("span must be greater than 1")
    decay = (span - 1.0) / (span + 1.0)
    return float(np.log(0.5) / np.log(decay))


def load_price_data(symbols: list[str], cache_dir: Path) -> dict[str, pl.DataFrame]:
    """Load continuous prices without creating or overwriting cache files."""
    prices = {}
    for symbol in symbols:
        price_symbol = resolve_price_symbol(symbol)
        prices[symbol] = FuturesDataLoader(
            asset=price_symbol,
            data_dir=str(cache_dir),
            use_preprocessed=True,
            save_preprocessed=False,
        ).daily
    return prices


def compound_completed_calendar_weeks(daily_returns: pl.DataFrame) -> pl.DataFrame:
    """Compound synchronized daily returns into completed Monday-Sunday weeks.

    Each observation is labelled on Sunday.  Since the production correlation
    estimator admits only rows strictly before ``as_of``, an in-progress week
    cannot leak later observations into a mid-week or month-end estimate.
    """
    symbols = [column for column in daily_returns.columns if column != "ts_event"]
    return (
        daily_returns
        .with_columns(week_start=pl.col("ts_event").dt.truncate("1w"))
        .group_by("week_start")
        .agg([((pl.col(symbol) + 1.0).product() - 1.0).alias(symbol) for symbol in symbols])
        .with_columns(ts_event=pl.col("week_start") + pl.duration(days=6))
        .drop("week_start")
        .select("ts_event", *symbols)
        .sort("ts_event")
    )


def month_end_dates(daily_returns: pl.DataFrame, window_years: float) -> list[date]:
    """Common-date month ends after a full outer-window burn-in."""
    month_ends = (
        daily_returns
        .select("ts_event")
        .with_columns(ym=pl.col("ts_event").dt.strftime("%Y-%m"))
        .group_by("ym")
        .agg(pl.col("ts_event").max())
        .sort("ts_event")
        .get_column("ts_event")
        .to_list()
    )
    first = daily_returns.get_column("ts_event").min()
    if first is None:
        return []
    burn_in_end = first + timedelta(days=int(window_years * 365.25))
    return [value for value in month_ends if value >= burn_in_end]


def matrix_statistics(H: np.ndarray) -> tuple[float, float, float]:
    pairs = H[np.triu_indices_from(H, k=1)]
    return (
        float(np.mean(pairs)),
        float(np.median(pairs)),
        float(np.mean(np.clip(pairs, 0.0, 1.0))),
    )


def estimator_specs(daily_halflife: float, weekly_span: float) -> dict[str, EstimatorSpec]:
    """Pre-registered daily, Carver-like weekly, and unit-sensitivity cases."""
    return {
        f"daily_hl{daily_halflife:g}": EstimatorSpec(
            "daily", daily_halflife, DEFAULT_MIN_DAILY_ROWS
        ),
        f"weekly_span{weekly_span:g}": EstimatorSpec(
            "weekly", span_to_halflife(weekly_span), DEFAULT_MIN_WEEKLY_ROWS
        ),
        "weekly_calendar_matched": EstimatorSpec(
            "weekly", daily_halflife / TRADING_DAYS_PER_WEEK, 52
        ),
        "weekly_hl26": EstimatorSpec("weekly", 26.0, 52),
        # Deliberate unit-error sensitivity: this means 63 WEEKS, not the
        # current estimator's 63 trading days.
        "weekly_hl63": EstimatorSpec("weekly", 63.0, 52),
    }


def calculate_monthly_estimates(
    returns_by_frequency: dict[str, pl.DataFrame],
    symbols: list[str],
    rebalance_dates: list[date],
    specs: dict[str, EstimatorSpec],
    window_years: float,
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {name: [] for name in specs}
    for as_of in rebalance_dates:
        for name, spec in specs.items():
            H, covered = _bounded_ewm_correlation_matrix(
                returns_by_frequency[spec.returns_key],
                symbols,
                as_of,
                window_years,
                spec.halflife,
                min_rows=spec.min_rows,
            )
            if not covered.all():
                logger.debug(
                    "weekly_idm candidate_rejected estimator=%s as_of=%s reason=coverage covered=%s total=%s",
                    name, as_of, int(covered.sum()), len(symbols),
                )
                continue

            flat = {symbol: 1.0 / len(symbols) for symbol in symbols}
            erc = compute_erc_weights(symbols, H)
            corr_mean, corr_median, corr_positive_mean = matrix_statistics(H)
            results[name].append({
                "date": as_of,
                "H": H,
                "corr_mean": corr_mean,
                "corr_median": corr_median,
                "corr_positive_mean": corr_positive_mean,
                "idm_flat": compute_idm(symbols, H, flat),
                "idm_erc": compute_idm(symbols, H, erc),
                "erc": np.array([erc[symbol] for symbol in symbols]),
            })
    return results


def weighted_risk_factor(H: np.ndarray, weights: np.ndarray, floor: bool) -> float:
    correlation = np.clip(H, 0.0, 1.0) if floor else H
    return float(np.sqrt(max(0.0, weights @ correlation @ weights)))


def forward_risk_diagnostic(
    rows: list[dict], daily_returns: pl.DataFrame, symbols: list[str], weighting: str
) -> dict[str, float]:
    """Compare predicted risk with the next 63 daily observations.

    Monthly evaluation windows overlap, so this is calibration evidence rather
    than an independent hypothesis test.  Realized correlation remains signed;
    only the predicted IDM input receives the production zero floor.
    """
    ratios = []
    for row in rows:
        future = daily_returns.filter(pl.col("ts_event") > row["date"]).head(63)
        if future.height < 40:
            continue
        realized = np.corrcoef(future.select(symbols).to_numpy(), rowvar=False)
        if not np.isfinite(realized).all():
            continue
        if weighting == "flat":
            weights = np.full(len(symbols), 1.0 / len(symbols))
        else:
            weights = row["erc"]
        predicted_factor = weighted_risk_factor(row["H"], weights, floor=True)
        realized_factor = weighted_risk_factor(realized, weights, floor=False)
        if predicted_factor > 0:
            ratios.append(realized_factor / predicted_factor)
    values = np.asarray(ratios)
    return {
        "n": float(len(values)),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.quantile(values, 0.95)),
        "mean_abs_error": float(np.mean(np.abs(values - 1.0))),
    }


def circular_block_mean_ci(
    values: np.ndarray, block_length: int = 12, draws: int = 10_000, seed: int = 20260917
) -> tuple[float, float]:
    """Circular-block bootstrap interval for a serially dependent monthly mean."""
    rng = np.random.default_rng(seed)
    n = len(values)
    bootstrap_means = np.empty(draws)
    offsets = np.arange(block_length)
    for draw in range(draws):
        starts = rng.integers(0, n, size=int(np.ceil(n / block_length)))
        indices = ((starts[:, None] + offsets) % n).ravel()[:n]
        bootstrap_means[draw] = values[indices].mean()
    return (
        float(np.quantile(bootstrap_means, 0.025)),
        float(np.quantile(bootstrap_means, 0.975)),
    )


def effective_sample_size(rows: int, halflife: float) -> float:
    age = (rows - 1) - np.arange(rows)
    weights = (0.5 ** (1.0 / halflife)) ** age
    weights /= weights.sum()
    return float(1.0 / np.sum(weights * weights))


def roll_return_audit(price_data: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Summarize raw front-contract returns on expiration-switch dates."""
    rows = []
    for symbol, frame in price_data.items():
        enriched = (
            frame.sort("ts_event")
            .with_columns(
                absolute_return=pl.col("close").pct_change().abs(),
                roll=pl.col("expiration") != pl.col("expiration").shift(1),
            )
            .drop_nulls("absolute_return")
        )
        roll = enriched.filter(pl.col("roll")).get_column("absolute_return")
        ordinary = enriched.filter(~pl.col("roll")).get_column("absolute_return")
        rows.append({
            "symbol": symbol,
            "rolls": len(roll),
            "roll_median": roll.median(),
            "roll_p95": roll.quantile(0.95),
            "nonroll_p99": ordinary.quantile(0.99),
            "worst_roll": roll.max(),
        })
    return pl.DataFrame(rows)


def summarize(
    results: dict[str, list[dict]],
    daily_returns: pl.DataFrame,
    symbols: list[str],
    specs: dict[str, EstimatorSpec],
) -> str:
    lines = []
    for name, rows in results.items():
        for weighting in ("flat", "erc"):
            values = np.asarray([row[f"idm_{weighting}"] for row in rows])
            lines.append(
                f"{name:24s} {weighting:4s} IDM mean={values.mean():.4f} "
                f"sd={values.std():.4f} p05={np.quantile(values, .05):.4f} "
                f"p50={np.quantile(values, .50):.4f} p95={np.quantile(values, .95):.4f} "
                f"mean_abs_monthly_change={np.mean(np.abs(np.diff(values))):.4f}"
            )
        lines.append(
            f"{name:24s} corr_mean={np.mean([row['corr_mean'] for row in rows]):.4f} "
            f"positive_floor_mean={np.mean([row['corr_positive_mean'] for row in rows]):.4f}"
        )

    daily_name = next(name for name, spec in specs.items() if spec.returns_key == "daily")
    weekly_span_name = next(name for name in specs if name.startswith("weekly_span"))
    daily_by_date = {row["date"]: row for row in results[daily_name]}
    for name in specs:
        if name == daily_name:
            continue
        pairs = [(daily_by_date[row["date"]], row) for row in results[name]
                 if row["date"] in daily_by_date]
        differences = np.asarray([weekly["idm_flat"] - daily["idm_flat"]
                                  for daily, weekly in pairs])
        matrix_rmse = np.mean([
            np.sqrt(np.mean((daily["H"] - weekly["H"]) ** 2))
            for daily, weekly in pairs
        ])
        erc_weight_l1 = np.mean([
            np.abs(weekly["erc"] - daily["erc"]).sum()
            for daily, weekly in pairs
        ])
        low, high = circular_block_mean_ci(differences)
        lines.append(
            f"vs_daily {name:18s} matrix_RMSE={matrix_rmse:.4f} "
            f"flat_IDM_diff={differences.mean():+.4f} mean_abs={np.abs(differences).mean():.4f} "
            f"block95=[{low:+.4f},{high:+.4f}] ERC_weight_L1={erc_weight_l1:.4f}"
        )

    for weighting in ("flat", "erc"):
        for name, rows in results.items():
            metric = forward_risk_diagnostic(rows, daily_returns, symbols, weighting)
            lines.append(
                f"forward {weighting:4s} {name:24s} n={int(metric['n']):3d} "
                f"realized_over_target_mean={metric['mean']:.3f} "
                f"median={metric['median']:.3f} p95={metric['p95']:.3f} "
                f"mean_abs_error={metric['mean_abs_error']:.3f}"
            )

    for name, spec in specs.items():
        assumed_rows = 756 if spec.returns_key == "daily" else 156
        lines.append(
            f"effective_sample {name:24s} halflife_rows={spec.halflife:.2f} "
            f"ESS={effective_sample_size(assumed_rows, spec.halflife):.1f}"
        )

    daily_latest = results[daily_name][-1]
    weekly_latest = results[weekly_span_name][-1]
    lines.append(
        f"latest as_of={daily_latest['date']} daily_flat_IDM={daily_latest['idm_flat']:.4f} "
        f"{weekly_span_name}_flat_IDM={weekly_latest['idm_flat']:.4f}"
    )
    return "\n".join(lines)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--window-years", type=float, default=DEFAULT_WINDOW_YEARS)
    parser.add_argument("--daily-halflife", type=float, default=DEFAULT_DAILY_HALFLIFE)
    parser.add_argument("--weekly-span", type=float, default=DEFAULT_WEEKLY_SPAN)
    parser.add_argument("--show-roll-audit", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> dict[str, list[dict]]:
    args = parse_args(argv)
    symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]
    if len(symbols) < 2:
        raise ValueError("At least two symbols are required")
    if args.window_years <= 0 or args.daily_halflife <= 0:
        raise ValueError("window-years and daily-halflife must be positive")

    logger.info(
        "weekly_idm phase=start symbols=%s window_years=%.2f daily_halflife_days=%.2f weekly_span=%.2f",
        symbols, args.window_years, args.daily_halflife, args.weekly_span,
    )
    price_data = load_price_data(symbols, args.cache_dir)
    daily_returns = build_returns_wide(price_data)
    weekly_returns = compound_completed_calendar_weeks(daily_returns)
    rebalance_dates = month_end_dates(daily_returns, args.window_years)
    specs = estimator_specs(args.daily_halflife, args.weekly_span)
    results = calculate_monthly_estimates(
        {"daily": daily_returns, "weekly": weekly_returns},
        symbols,
        rebalance_dates,
        specs,
        args.window_years,
    )
    if any(not rows for rows in results.values()):
        empty = [name for name, rows in results.items() if not rows]
        raise ValueError(f"No fully covered estimates for: {empty}")

    header = (
        f"daily rows={daily_returns.height} {daily_returns['ts_event'].min()}..{daily_returns['ts_event'].max()}\n"
        f"weekly rows={weekly_returns.height} {weekly_returns['ts_event'].min()}..{weekly_returns['ts_event'].max()}\n"
        f"monthly evaluations={len(rebalance_dates)} {rebalance_dates[0]}..{rebalance_dates[-1]}"
    )
    report = f"{header}\n{summarize(results, daily_returns, symbols, specs)}"
    print(report)
    logger.info("weekly_idm phase=complete\n%s", report)

    if args.show_roll_audit:
        audit = roll_return_audit(price_data)
        print("\nRaw front-contract roll-return audit")
        print(audit)
        logger.info("weekly_idm roll_audit=%s", audit.to_dicts())
    return results


if __name__ == "__main__":
    main()

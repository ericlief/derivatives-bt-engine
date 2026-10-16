"""Build the reusable futures EWMAC rule/cost cache from a live snapshot.

This command is the narrow offline boundary between IB-dependent market-data
acquisition and parameterized instrument selection. It delegates pooled EWMAC
calibration to :mod:`market_screen`, but deliberately exposes no
capital, target-volatility, liquidity, margin, or portfolio-weight options.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from derivatives_bt_engine.calculations.ewmac import EWMAC_SCALAR_MIN_PERIODS
from derivatives_bt_engine.calculations.volatility import (
    MIXED_VOL_FAST_SPAN,
    MIXED_VOL_MIN_SAMPLES,
    MIXED_VOL_SLOW_WEIGHT,
    MIXED_VOL_SLOW_YEARS,
)
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)
from derivatives_bt_engine.data.pysystemtrade_pooling import (
    DEFAULT_POOLING_MAPPING_PATH,
)
from derivatives_bt_engine.pipelines.market_screen import (
    DEFAULT_COST_EWMAC_FAST_SPANS,
    DEFAULT_RULE_COST_LIMIT_SR,
    run as run_cost_pipeline,
)


def _parse_fast_spans(value: str) -> tuple[int, ...]:
    """Parse unique positive EWMAC fast spans from a comma-separated value."""
    try:
        spans = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fast spans must be integers") from exc
    if not spans or any(span <= 0 for span in spans):
        raise argparse.ArgumentTypeError("fast spans must be positive")
    return spans


def parse_args(argv=None) -> argparse.Namespace:
    """Parse only inputs that can change the historical rule/cost cache."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-input",
        type=Path,
        required=True,
        help=(
            "Exact market-snapshot Parquet, or a directory whose newest "
            "timestamped market snapshot should be used."
        ),
    )
    parser.add_argument(
        "--history-db",
        "--pysystemtrade-db",
        dest="history_db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
        help="Historical signal database used to calibrate EWMAC rules.",
    )
    parser.add_argument(
        "--pooling-map",
        "--pysystemtrade-pooling-mapping",
        dest="pysystemtrade_pooling_mapping",
        type=Path,
        default=DEFAULT_POOLING_MAPPING_PATH,
        help="Reviewed forecast normalization-pool and economic classification map.",
    )
    parser.add_argument(
        "--cost-ewmac-fast-spans",
        type=_parse_fast_spans,
        default=DEFAULT_COST_EWMAC_FAST_SPANS,
        help="Comma-separated EWMAC fast spans; slow spans are four times fast.",
    )
    parser.add_argument(
        "--cost-ewmac-vol-span",
        type=int,
        default=MIXED_VOL_FAST_SPAN,
        help="Fast point-volatility span used during EWMAC calibration.",
    )
    parser.add_argument(
        "--cost-ewmac-vol-slow-years",
        type=int,
        default=MIXED_VOL_SLOW_YEARS,
        help="Slow point-volatility horizon in years.",
    )
    parser.add_argument(
        "--cost-ewmac-vol-slow-weight",
        type=float,
        default=MIXED_VOL_SLOW_WEIGHT,
        help="Weight of slow point volatility in the mixed estimator.",
    )
    parser.add_argument(
        "--cost-ewmac-vol-min-samples",
        type=int,
        default=MIXED_VOL_MIN_SAMPLES,
        help="Minimum point changes before fast volatility becomes valid.",
    )
    parser.add_argument(
        "--cost-ewmac-scalar-min-periods",
        type=int,
        default=EWMAC_SCALAR_MIN_PERIODS,
        help="Minimum pooled observations before a forecast scalar is valid.",
    )
    parser.add_argument(
        "--rule-cost-limit-sr",
        type=float,
        default=DEFAULT_RULE_COST_LIMIT_SR,
        help="Maximum annual SR cost retained for an individual EWMAC rule.",
    )
    parser.add_argument(
        "--skip-ewmac-cost-baseline",
        action="store_true",
        help="Skip historical pooled EWMAC calibration for diagnostics only.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Parquet output base; defaults to "
            "results/multi_speed_ewmac_rule_cache.parquet."
        ),
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Build and print the cache without writing Parquet.",
    )
    return parser.parse_args(argv)


def run(argv=None) -> pl.DataFrame:
    """Build the cache and return its public Polars dataframe.

    The delegated argument list is explicit so future selection-only options
    added to the legacy cost command cannot leak into this command's API.
    """
    args = parse_args(argv)
    forwarded = [
        "--snapshot-input",
        str(args.snapshot_input),
        "--pysystemtrade-db",
        str(args.history_db),
        "--pysystemtrade-pooling-mapping",
        str(args.pysystemtrade_pooling_mapping),
        "--cost-ewmac-fast-spans",
        ",".join(str(span) for span in args.cost_ewmac_fast_spans),
        "--cost-ewmac-vol-span",
        str(args.cost_ewmac_vol_span),
        "--cost-ewmac-vol-slow-years",
        str(args.cost_ewmac_vol_slow_years),
        "--cost-ewmac-vol-slow-weight",
        str(args.cost_ewmac_vol_slow_weight),
        "--cost-ewmac-vol-min-samples",
        str(args.cost_ewmac_vol_min_samples),
        "--cost-ewmac-scalar-min-periods",
        str(args.cost_ewmac_scalar_min_periods),
        "--rule-cost-limit-sr",
        str(args.rule_cost_limit_sr),
    ]
    if args.output is not None:
        forwarded.extend(("--output", str(args.output)))
    if args.skip_ewmac_cost_baseline:
        forwarded.append("--skip-ewmac-cost-baseline")
    if args.no_save:
        forwarded.append("--no-save")
    return run_cost_pipeline(forwarded, rule_cache_only=True)


def main(argv=None) -> None:
    """Console entry point for ``futures-rule-cache``."""
    run(argv)


if __name__ == "__main__":
    main()

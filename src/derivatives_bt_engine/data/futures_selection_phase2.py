"""Start Phase 2 selection from the persisted Phase 1 cost audit."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from derivatives_bt_engine.data.futures_cost_rankings import (
    load_latest_phase1_cost_report,
    phase2_step1_candidates,
)
from derivatives_bt_engine.utils.logger import setup_logger


logger = logging.getLogger(__name__)
REPORT_TIMEZONE = ZoneInfo("America/Chicago")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Load a Phase 1 futures cost CSV and save the instruments that "
            "already passed its cost, dollar-vol, liquidity, data, rule, "
            "execution, and IB-availability gates."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit Phase 1 CSV; otherwise use the latest report.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("."),
        help="Directory (and its results/ child) searched for the latest CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Exact Step 1 output path; defaults beside the input report.",
    )
    return parser.parse_args(argv)


def _default_output_path(source: Path, generated_at: datetime) -> Path:
    stamp = generated_at.astimezone(REPORT_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return source.parent / f"pysystemtrade_phase2_step1_{stamp}.csv"


def run(argv=None) -> tuple[Path, pl.DataFrame]:
    """Load Phase 1, save its selected candidate rows, and return both."""
    args = parse_args(argv)
    setup_logger()

    if args.input is None:
        source, report = load_latest_phase1_cost_report(args.report_dir)
    else:
        source = args.input.expanduser().resolve()
        report = pl.read_csv(source, infer_schema_length=None)

    logger.info(
        "phase2_step1 start source=%s instruments=%d",
        source,
        report.height,
    )
    selected = phase2_step1_candidates(report)
    selected_symbols = set(selected.get_column("symbol").to_list())
    for row in report.select(
        name
        for name in (
            "symbol",
            "ann_dvol",
            "avg_daily_volume",
            "pct_mkt_volume",
            "phase2_excl",
        )
        if name in report.columns
    ).iter_rows(named=True):
        symbol = row.get("symbol")
        if symbol in selected_symbols:
            logger.debug(
                "phase2_step1 accepted symbol=%s ann_dvol=%s "
                "avg_daily_volume_cons=%s pct_mkt_volume=%s",
                symbol,
                row.get("ann_dvol"),
                row.get("avg_daily_volume"),
                row.get("pct_mkt_volume"),
            )
        else:
            logger.debug(
                "phase2_step1 rejected symbol=%s ann_dvol=%s "
                "avg_daily_volume_cons=%s pct_mkt_volume=%s reason=%s",
                symbol,
                row.get("ann_dvol"),
                row.get("avg_daily_volume"),
                row.get("pct_mkt_volume"),
                row.get("phase2_excl"),
            )

    generated_at = datetime.now(REPORT_TIMEZONE)
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else _default_output_path(source, generated_at)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.write_csv(output)
    logger.info(
        "phase2_step1 complete source=%s output=%s selected=%d total=%d",
        source,
        output,
        selected.height,
        report.height,
    )
    print(
        f"Phase 2 Step 1 selected {selected.height}/{report.height} instruments"
    )
    print(f"Saved {output}")
    return output, selected


def main(argv=None) -> None:
    run(argv)


if __name__ == "__main__":
    main()

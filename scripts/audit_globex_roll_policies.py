"""Audit code-pinned Globex roll policies against Carver and raw contracts.

Run from the repository root::

    .venv/bin/python scripts/audit_globex_roll_policies.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import polars as pl

from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)
from derivatives_bt_engine.domain.instruments import (
    CME_MONTH_NUM_TO_LETTER,
    GLOBEX_ROLL_POLICY_DEFAULTS,
    GLOBEX_ROLL_POLICY_OVERRIDES,
    resolve_active_months,
    resolve_price_symbol,
)
from derivatives_bt_engine.utils.logger import setup_logger


def audit_roll_policies(
    *,
    pysystemtrade_db: Path,
    globex_db: Path,
) -> pl.DataFrame:
    carver = duckdb.connect(str(pysystemtrade_db), read_only=True)
    globex = duckdb.connect(str(globex_db), read_only=True)
    try:
        carver_rows = carver.execute(
            """
            SELECT instrument_code, hold_roll_cycle, roll_offset_days
            FROM raw.roll_config
            """
        ).fetchall()
        raw_month_rows = globex.execute(
            """
            SELECT asset, extract(month FROM expiration)::INTEGER AS month
            FROM daily
            WHERE instrument_class = 'F'
              AND security_type = 'FUT'
              AND expiration IS NOT NULL
            GROUP BY asset, month
            """
        ).fetchall()
    finally:
        carver.close()
        globex.close()

    carver_by_instrument = {
        instrument: (cycle, int(offset))
        for instrument, cycle, offset in carver_rows
    }
    raw_months_by_asset: dict[str, set[int]] = {}
    for asset, month in raw_month_rows:
        raw_months_by_asset.setdefault(asset, set()).add(int(month))

    rows: list[dict[str, object]] = []
    policy_groups = (
        ("raw_root_default", GLOBEX_ROLL_POLICY_DEFAULTS),
        ("requested_symbol_override", GLOBEX_ROLL_POLICY_OVERRIDES),
    )
    for scope, policies in policy_groups:
        for symbol, policy in policies.items():
            raw_symbol = (
                symbol
                if scope == "raw_root_default"
                else resolve_price_symbol(symbol)
            )
            configured = (
                str(policy["hold_roll_cycle"]),
                int(policy["roll_offset_days"]),
            )
            carver_values = carver_by_instrument.get(
                str(policy["carver_instrument"])
            )
            raw_months = raw_months_by_asset.get(raw_symbol)
            required_months = {
                month
                for month, letter in CME_MONTH_NUM_TO_LETTER.items()
                if letter in configured[0]
            }
            missing_months = (
                sorted(required_months - raw_months)
                if raw_months is not None
                else []
            )
            active_months = "".join(resolve_active_months(symbol) or [])
            rows.append({
                "scope": scope,
                "symbol": symbol,
                "raw_symbol": raw_symbol,
                "carver_instrument": policy["carver_instrument"],
                "hold_roll_cycle": configured[0],
                "roll_offset_days": configured[1],
                "carver_hold_roll_cycle": (
                    carver_values[0] if carver_values else None
                ),
                "carver_roll_offset_days": (
                    carver_values[1] if carver_values else None
                ),
                "carver_match": configured == carver_values,
                "active_months": active_months or None,
                "active_months_match_hold_cycle": (
                    active_months == configured[0] if active_months else None
                ),
                "globex_asset_available": raw_months is not None,
                "missing_hold_months": ",".join(
                    CME_MONTH_NUM_TO_LETTER[month]
                    for month in missing_months
                ),
            })
    return pl.DataFrame(rows, infer_schema_length=None).sort("scope", "symbol")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pysystemtrade-db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
    )
    parser.add_argument(
        "--globex-db",
        type=Path,
        default=DEFAULT_GLOBEX_DB_PATH,
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    log = setup_logger()
    audit = audit_roll_policies(
        pysystemtrade_db=args.pysystemtrade_db.expanduser(),
        globex_db=args.globex_db.expanduser(),
    )
    mismatches = audit.filter(~pl.col("carver_match"))
    missing_months = audit.filter(
        pl.col("globex_asset_available")
        & (pl.col("missing_hold_months") != "")
    )
    log.info(
        "globex_roll_policy_audit policies=%d carver_mismatches=%d "
        "missing_hold_month_sets=%d globex_assets_available=%d",
        audit.height,
        mismatches.height,
        missing_months.height,
        audit.get_column("globex_asset_available").sum(),
    )
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=240):
        print(audit)
    if mismatches.height or missing_months.height:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

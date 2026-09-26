"""Carver instrument-code to Interactive Brokers futures metadata.

The bundled mapping is copied from pysystemtrade's
``sysbrokers/IB/config/ib_config_futures.csv``.  It is broker identity
metadata, not an assertion that a particular IBKR account has market-data or
trading permission for the contract.  Live qualification remains the final
availability test.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from derivatives_bt_engine.domain.futures_history import DEFAULT_PYSYSTEMTRADE_DB_PATH


DEFAULT_IB_MAPPING_PATH = Path(__file__).with_name("pysystemtrade_ib_mappings.csv")
# Provenance for the packaged copy of sysbrokers/IB/config/ib_config_futures.csv.
PYSYSTEMTRADE_IB_MAPPING_COMMIT = "b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8"


def load_pysystemtrade_ib_mapping(
    path: Path | str = DEFAULT_IB_MAPPING_PATH,
) -> pl.DataFrame:
    mapping = pl.read_csv(path, infer_schema_length=10_000).rename(
        {
            "Instrument": "instrument_code",
            "IBSymbol": "ib_symbol",
            "IBExchange": "ib_exchange",
            "IBCurrency": "ib_currency",
            "IBMultiplier": "ib_multiplier",
            "priceMagnifier": "price_magnifier",
            "IgnoreWeekly": "ignore_weekly",
        }
    )
    return mapping.with_columns(
        pl.when(pl.col("ib_currency") == "NA")
        .then(pl.lit(""))
        .otherwise(pl.col("ib_currency"))
        .alias("ib_currency"),
        (pl.col("ib_multiplier") / pl.col("price_magnifier")).alias(
            "ib_effective_point_value"
        ),
    )


def load_pysystemtrade_ib_instruments(
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    *,
    mapping_path: Path | str = DEFAULT_IB_MAPPING_PATH,
    history_only: bool = True,
) -> list[dict]:
    """Return Carver instruments enriched with IB identity and cash specs."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        config = con.execute(
            """
            SELECT
                i.instrument_code,
                i.description,
                i.point_size,
                i.currency,
                i.asset_class,
                i.per_block_cost,
                i.percentage_cost,
                i.per_trade_cost,
                i.region,
                s.spread_cost,
                c.multiple_rows,
                c.multiple_start,
                c.multiple_end,
                c.adjusted_rows,
                c.adjusted_start,
                c.adjusted_end
            FROM raw.instrument_config i
            LEFT JOIN raw.spread_costs s USING (instrument_code)
            LEFT JOIN qa.series_coverage c USING (instrument_code)
            """
        ).pl()
    finally:
        con.close()

    if history_only:
        config = config.filter(
            (pl.col("multiple_rows").fill_null(0) > 0)
            & (pl.col("adjusted_rows").fill_null(0) > 0)
        )
    mapping = load_pysystemtrade_ib_mapping(mapping_path)
    joined = config.join(mapping, on="instrument_code", how="left", validate="1:1")
    missing = joined.filter(pl.col("ib_symbol").is_null())["instrument_code"].to_list()
    if missing:
        raise ValueError(f"Missing IB mappings for pysystemtrade instruments: {missing}")

    mismatch = joined.filter(
        (pl.col("point_size") - pl.col("ib_effective_point_value")).abs() > 1e-9
    )
    if mismatch.height:
        raise ValueError(
            "IB multiplier/price magnifier does not match point_size for: "
            f"{mismatch['instrument_code'].to_list()}"
        )

    return [
        {
            "symbol": row["instrument_code"],
            "instrument_code": row["instrument_code"],
            "description": row["description"],
            "ib_symbol": row["ib_symbol"],
            "exchange": row["ib_exchange"],
            "ib_currency": row["ib_currency"],
            "ib_multiplier": row["ib_multiplier"],
            "price_magnifier": row["price_magnifier"],
            "ignore_weekly": row["ignore_weekly"],
            "currency": row["currency"],
            "multiplier": row["point_size"],
            "commission": row["per_block_cost"],
            "percentage_cost": row["percentage_cost"],
            "per_trade_cost": row["per_trade_cost"],
            "carver_spread_points": row["spread_cost"],
            "asset_class": row["asset_class"],
            "region": row["region"],
            "expiry": "auto",
            "mapping_status": "carver_ib_candidate",
            "history_start": row["multiple_start"],
            "history_end": row["multiple_end"],
        }
        for row in joined.sort("instrument_code").iter_rows(named=True)
    ]

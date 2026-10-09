"""Load and causally align historical native-currency-to-USD prices.

The pysystemtrade reference database stores daily FX observations separately
from futures histories.  This module reads those immutable observations and
performs backward as-of joins, so a subsystem P&L row can only use an FX rate
known on or before that row's date.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
)


_REFERENCE_CURRENCY_ALIASES = {
    # The imported reference data retains the historical ISO-style MXP label.
    "MXN": "MXP",
}


def load_fx_to_usd_history(
    currency: str,
    *,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
) -> pl.DataFrame:
    """Return one currency's dated direct USD conversions.

    The returned frame contains unique ``fx_date`` rows ordered ascending,
    with ``fx_to_usd``, ``fx_pair``, and the original source timestamp.  USD
    itself is an identity conversion and is created during alignment because
    it has no stored market observations.
    """
    normalized = str(currency).strip().upper()
    if normalized == "USD":
        return pl.DataFrame(
            schema={
                "fx_date": pl.Date,
                "fx_to_usd": pl.Float64,
                "fx_pair": pl.String,
                "fx_source_ts": pl.Datetime("us"),
                "fx_src": pl.String,
            }
        )
    reference_currency = _REFERENCE_CURRENCY_ALIASES.get(normalized, normalized)
    pair = f"{reference_currency}USD"
    con = duckdb.connect(str(Path(db_path).expanduser()), read_only=True)
    try:
        history = con.execute(
            """
            SELECT
                CAST(source_timestamp AS DATE) AS fx_date,
                price AS fx_to_usd,
                currency_pair AS fx_pair,
                source_timestamp AS fx_source_ts,
                source_file AS fx_src
            FROM raw.fx_prices
            WHERE currency_pair = ? AND price IS NOT NULL AND price > 0
            QUALIFY row_number() OVER (
                PARTITION BY CAST(source_timestamp AS DATE)
                ORDER BY source_timestamp DESC
            ) = 1
            ORDER BY fx_date
            """,
            [pair],
        ).pl()
    finally:
        con.close()
    if history.is_empty():
        raise ValueError(f"no historical {pair} conversion is available")
    return history


def align_fx_to_history(
    frame: pl.DataFrame,
    currency: str,
    *,
    fx_history: pl.DataFrame | None = None,
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
) -> pl.DataFrame:
    """Append a causal ``fx_to_usd`` rate to a dated instrument frame.

    ``fx_history`` may be supplied by a caller cache to avoid repeated
    database reads for instruments sharing a currency.  Non-USD rates use a
    backward as-of join and therefore remain null before the first available
    observation rather than backfilling future information.
    """
    if "date" not in frame.columns:
        raise ValueError("FX alignment input is missing date")
    normalized = str(currency).strip().upper()
    ordered = frame.sort("date")
    if normalized == "USD":
        return ordered.with_columns(
            pl.lit(1.0).alias("fx_to_usd"),
            pl.lit("USDUSD").alias("fx_pair"),
            pl.col("date").alias("fx_date"),
            pl.lit(None, dtype=pl.Datetime("us")).alias("fx_source_ts"),
            pl.lit("identity").alias("fx_src"),
        )

    rates = fx_history
    if rates is None:
        rates = load_fx_to_usd_history(normalized, db_path=db_path)
    required = {"fx_date", "fx_to_usd", "fx_pair", "fx_source_ts", "fx_src"}
    missing = sorted(required.difference(rates.columns))
    if missing:
        raise ValueError(f"FX history is missing columns: {missing}")
    return ordered.join_asof(
        rates.sort("fx_date"),
        left_on="date",
        right_on="fx_date",
        strategy="backward",
    )

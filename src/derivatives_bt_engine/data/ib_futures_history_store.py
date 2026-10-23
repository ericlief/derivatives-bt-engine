"""Persist raw Interactive Brokers dated-futures bars in an isolated DuckDB.

The database is a durable broker-source layer. It stores qualified contract
identity, unadjusted bars, and fetch coverage only; continuous selection,
Panama adjustment, signals, and stitched vendor views remain derived data.
Those derived histories are cached separately as versioned Parquet bundles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import polars as pl


DEFAULT_IB_FUTURES_HISTORY_DB_PATH = Path(
    "/home/dev/fin/db/ib_futures_history.duckdb"
)
IB_FUTURES_HISTORY_SCHEMA_VERSION = 1


@dataclass
class IBFuturesHistoryStore:
    """Read and update the isolated raw IB futures-history database.

    Parameters
    ----------
    db_path
        Dedicated DuckDB path. The store never attaches or modifies the
        reference, Globex, or general IB market-data databases.
    """

    db_path: Path | str = DEFAULT_IB_FUTURES_HISTORY_DB_PATH

    def initialize(self) -> None:
        """Create the versioned raw-contract schema when it does not exist."""
        path = Path(self.db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS meta")
            con.execute("CREATE SCHEMA IF NOT EXISTS raw")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS meta.schema_version (
                    version INTEGER PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL,
                    description VARCHAR NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT OR IGNORE INTO meta.schema_version
                VALUES (?, current_timestamp, ?)
                """,
                [
                    IB_FUTURES_HISTORY_SCHEMA_VERSION,
                    "Raw Interactive Brokers dated-futures history",
                ],
            )
            versions = con.execute(
                "SELECT version FROM meta.schema_version ORDER BY version"
            ).fetchall()
            if versions != [(IB_FUTURES_HISTORY_SCHEMA_VERSION,)]:
                raise ValueError(
                    "Unsupported IB futures-history schema versions: "
                    f"{[row[0] for row in versions]}"
                )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS raw.contracts (
                    con_id BIGINT PRIMARY KEY,
                    pst_instr_code VARCHAR NOT NULL,
                    ib_symbol VARCHAR NOT NULL,
                    exchange VARCHAR NOT NULL,
                    currency VARCHAR NOT NULL,
                    multiplier DOUBLE NOT NULL,
                    price_magnifier DOUBLE NOT NULL,
                    contract_month VARCHAR NOT NULL,
                    expiration DATE NOT NULL,
                    local_symbol VARCHAR NOT NULL,
                    trading_class VARCHAR NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (pst_instr_code, contract_month, con_id)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS raw.daily_bars (
                    con_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    bar_size VARCHAR NOT NULL,
                    what_to_show VARCHAR NOT NULL,
                    use_rth BOOLEAN NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE NOT NULL,
                    volume DOUBLE,
                    average DOUBLE,
                    bar_count BIGINT,
                    fetched_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (
                        con_id, date, bar_size, what_to_show, use_rth
                    )
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS raw.fetch_log (
                    fetch_id BIGINT PRIMARY KEY,
                    con_id BIGINT,
                    pst_instr_code VARCHAR NOT NULL,
                    contract_month VARCHAR NOT NULL,
                    requested_at TIMESTAMPTZ NOT NULL,
                    requested_end VARCHAR NOT NULL,
                    duration VARCHAR NOT NULL,
                    bar_size VARCHAR NOT NULL,
                    what_to_show VARCHAR NOT NULL,
                    use_rth BOOLEAN NOT NULL,
                    status VARCHAR NOT NULL,
                    row_count BIGINT NOT NULL,
                    complete_contract BOOLEAN NOT NULL,
                    error VARCHAR
                )
                """
            )
            con.execute(
                """
                CREATE SEQUENCE IF NOT EXISTS raw.fetch_log_id_seq START 1
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS raw_contract_lookup
                ON raw.contracts (pst_instr_code, contract_month)
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS raw_bar_lookup
                ON raw.daily_bars (con_id, date)
                """
            )
        finally:
            con.close()

    def complete_contract_months(
        self,
        *,
        pst_instr_code: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> set[str]:
        """Return expired contract months already fetched to completion."""
        self.initialize()
        con = duckdb.connect(str(Path(self.db_path).expanduser()), read_only=True)
        try:
            rows = con.execute(
                """
                SELECT DISTINCT contract_month
                FROM raw.fetch_log
                WHERE pst_instr_code = ?
                  AND bar_size = ?
                  AND what_to_show = ?
                  AND use_rth = ?
                  AND status = 'success'
                  AND complete_contract
                """,
                [pst_instr_code, bar_size, what_to_show, use_rth],
            ).fetchall()
        finally:
            con.close()
        return {str(row[0]) for row in rows}

    def upsert_contract_and_bars(
        self,
        *,
        contract: dict[str, object],
        bars: pl.DataFrame,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        requested_end: str,
        complete_contract: bool,
    ) -> None:
        """Upsert one qualified contract and its raw daily bar observations."""
        self.initialize()
        required = {"date", "close"}
        missing = required - set(bars.columns)
        if missing:
            raise ValueError(f"IB raw bars missing columns: {sorted(missing)}")
        now = datetime.now(timezone.utc)
        path = Path(self.db_path).expanduser()
        con = duckdb.connect(str(path))
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT INTO raw.contracts VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (con_id) DO UPDATE SET
                    pst_instr_code = excluded.pst_instr_code,
                    ib_symbol = excluded.ib_symbol,
                    exchange = excluded.exchange,
                    currency = excluded.currency,
                    multiplier = excluded.multiplier,
                    price_magnifier = excluded.price_magnifier,
                    contract_month = excluded.contract_month,
                    expiration = excluded.expiration,
                    local_symbol = excluded.local_symbol,
                    trading_class = excluded.trading_class,
                    last_seen_at = excluded.last_seen_at
                """,
                [
                    contract["con_id"],
                    contract["pst_instr_code"],
                    contract["ib_symbol"],
                    contract["exchange"],
                    contract["currency"],
                    contract["multiplier"],
                    contract["price_magnifier"],
                    contract["contract_month"],
                    contract["expiration"],
                    contract["local_symbol"],
                    contract["trading_class"],
                    now,
                    now,
                ],
            )
            optional_columns = {
                name: (
                    pl.col(name).cast(dtype)
                    if name in bars.columns
                    else pl.lit(None, dtype=dtype)
                ).alias(name)
                for name, dtype in {
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "volume": pl.Float64,
                    "average": pl.Float64,
                    "bar_count": pl.Int64,
                }.items()
            }
            stored = bars.select(
                pl.lit(int(contract["con_id"])).alias("con_id"),
                pl.col("date").cast(pl.Date),
                pl.lit(bar_size).alias("bar_size"),
                pl.lit(what_to_show).alias("what_to_show"),
                pl.lit(use_rth).alias("use_rth"),
                optional_columns["open"],
                optional_columns["high"],
                optional_columns["low"],
                pl.col("close").cast(pl.Float64),
                optional_columns["volume"],
                optional_columns["average"],
                optional_columns["bar_count"],
                pl.lit(now).alias("fetched_at"),
            )
            con.register("incoming_ib_bars", stored)
            con.execute(
                """
                INSERT OR REPLACE INTO raw.daily_bars
                SELECT * FROM incoming_ib_bars
                """
            )
            con.execute(
                """
                INSERT INTO raw.fetch_log VALUES (
                    nextval('raw.fetch_log_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'success', ?, ?, NULL
                )
                """,
                [
                    contract["con_id"],
                    contract["pst_instr_code"],
                    contract["contract_month"],
                    now,
                    requested_end,
                    duration,
                    bar_size,
                    what_to_show,
                    use_rth,
                    stored.height,
                    complete_contract,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def log_fetch_failure(
        self,
        *,
        pst_instr_code: str,
        contract_month: str,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        requested_end: str,
        error: str,
        con_id: int | None = None,
    ) -> None:
        """Record a failed broker history request without storing partial data."""
        self.initialize()
        con = duckdb.connect(str(Path(self.db_path).expanduser()))
        try:
            con.execute(
                """
                INSERT INTO raw.fetch_log VALUES (
                    nextval('raw.fetch_log_id_seq'), ?, ?, ?, current_timestamp,
                    ?, ?, ?, ?, ?, 'failed', 0, false, ?
                )
                """,
                [
                    con_id,
                    pst_instr_code,
                    contract_month,
                    requested_end,
                    duration,
                    bar_size,
                    what_to_show,
                    use_rth,
                    error,
                ],
            )
        finally:
            con.close()

    def load_bars(
        self,
        *,
        pst_instr_code: str,
        contract_months: list[str],
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> pl.DataFrame:
        """Load cached raw bars with contract identity for requested months."""
        self.initialize()
        if not contract_months:
            return pl.DataFrame()
        placeholders = ", ".join("?" for _ in contract_months)
        con = duckdb.connect(str(Path(self.db_path).expanduser()), read_only=True)
        try:
            return con.execute(
                f"""
                SELECT
                    b.date,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.average,
                    b.bar_count,
                    'ib' AS source,
                    c.pst_instr_code,
                    c.ib_symbol,
                    c.exchange AS ib_exchange,
                    c.currency AS ib_currency,
                    c.multiplier AS ib_multiplier,
                    c.price_magnifier,
                    c.contract_month,
                    c.expiration,
                    c.con_id AS ib_contract_id,
                    c.local_symbol AS ib_local_symbol,
                    c.trading_class AS ib_trading_class
                FROM raw.daily_bars b
                JOIN raw.contracts c USING (con_id)
                WHERE c.pst_instr_code = ?
                  AND c.contract_month IN ({placeholders})
                  AND b.bar_size = ?
                  AND b.what_to_show = ?
                  AND b.use_rth = ?
                ORDER BY b.date, c.contract_month
                """,
                [
                    pst_instr_code,
                    *contract_months,
                    bar_size,
                    what_to_show,
                    use_rth,
                ],
            ).pl()
        finally:
            con.close()


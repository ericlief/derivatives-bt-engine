"""Source-neutral futures history streams for research and backtesting.

The existing :class:`FuturesDataLoader` returns one continuous ``close``
column that older callers use for both signals and marking.  That shape is
not safe for additively adjusted data.  This module keeps three concerns
explicit:

``signal``
    A positive index and its normalized returns.  Signal code may safely use
    percentage changes on this index.
``marks``
    Actual selected-contract prices and contract identity.
``carry``
    Same-timestamp current/carry observations.  Missing carry is absent, not
    zero-filled.

Both providers open their DuckDB inputs read-only.  No provider writes to the
Globex database or the pysystemtrade sidecar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Protocol

import duckdb
import polars as pl

from derivatives_bt_engine.domain.instruments import get_spec
from derivatives_bt_engine.utils.logger import setup_logger


logger = setup_logger()

HISTORY_SCHEMA_VERSION = 3
DEFAULT_PYSYSTEMTRADE_DB_PATH = Path(
    "/home/dev/fin/db/pysystemtrade_reference.duckdb"
)
DEFAULT_GLOBEX_DB_PATH = Path("/home/dev/fin/db/globex_mdp_3.0.duckdb")
DEFAULT_FUTURES_CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "futures"

_SIGNAL_COLUMNS = {
    "trade_date",
    "source_timestamp",
    "normalized_return",
    "signal_index",
    "quality_flag",
}
_MARK_COLUMNS = {
    "trade_date",
    "source_timestamp",
    "mark_price",
    "contract_id",
    "is_roll",
    "quality_flag",
}
_CARRY_COLUMNS = {
    "trade_date",
    "source_timestamp",
    "current_price",
    "current_contract",
    "carry_price",
    "carry_contract",
    "quality_flag",
}


class FuturesHistoryProvider(Protocol):
    """Interface implemented by source-specific history providers."""

    def load(self, instrument_code: str) -> "FuturesHistory":
        """Load one source instrument into distinct research streams."""


@dataclass(frozen=True)
class FuturesHistory:
    """One instrument's source-neutral signal, mark, and carry streams."""

    source: str
    instrument_code: str
    schema_version: int
    signal: pl.DataFrame
    marks: pl.DataFrame
    carry: pl.DataFrame
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_stream("signal", self.signal, _SIGNAL_COLUMNS)
        _validate_stream("marks", self.marks, _MARK_COLUMNS)
        _validate_stream("carry", self.carry, _CARRY_COLUMNS, allow_empty=True)

        if self.signal.filter(
            pl.col("signal_index").is_null() | (pl.col("signal_index") <= 0)
        ).height:
            raise ValueError("signal_index must be non-null and strictly positive")

    def signal_bars(self) -> pl.DataFrame:
        """Adapt the positive signal index to existing signal-code columns.

        This is deliberately signal-only.  It must not be used as an
        executable price or a contract mark.
        """
        return (
            self.signal.select(
                pl.col("trade_date").alias("ts_event"),
                pl.col("signal_index").alias("close"),
                "normalized_return",
                "quality_flag",
            )
            .sort("ts_event")
        )


def _validate_stream(
    name: str,
    frame: pl.DataFrame,
    required_columns: set[str],
    *,
    allow_empty: bool = False,
) -> None:
    missing = required_columns - set(frame.columns)
    if missing:
        raise ValueError(f"{name} stream is missing columns: {sorted(missing)}")
    if frame.is_empty() and allow_empty:
        return
    if frame.is_empty():
        raise ValueError(f"{name} stream must not be empty")
    if frame.get_column("trade_date").null_count():
        raise ValueError(f"{name} stream contains null trade dates")
    duplicate_dates = frame.group_by("trade_date").len().filter(pl.col("len") > 1)
    if duplicate_dates.height:
        raise ValueError(f"{name} stream contains duplicate trade dates")


def _empty_carry_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "trade_date": pl.Date,
            "source_timestamp": pl.Datetime("us"),
            "current_price": pl.Float64,
            "current_contract": pl.String,
            "carry_price": pl.Float64,
            "carry_contract": pl.String,
            "quality_flag": pl.String,
        }
    )


def _build_positive_signal_index(
    frame: pl.DataFrame,
    *,
    return_numerator: str,
    denominator: str,
) -> pl.DataFrame:
    frame = frame.sort("trade_date").with_columns(
        pl.col(return_numerator).diff().alias("point_change")
    )
    frame = frame.with_columns(
        pl.when(
            pl.col("point_change").is_not_null()
            & pl.col(denominator).is_not_null()
            & (pl.col(denominator) > 0)
        )
        .then(pl.col("point_change") / pl.col(denominator))
        .otherwise(None)
        .alias("normalized_return")
    )
    invalid_growth = frame.filter(pl.col("normalized_return") <= -1)
    if invalid_growth.height:
        first_date = invalid_growth.get_column("trade_date")[0]
        raise ValueError(
            f"normalized return is <= -100% on {first_date}; cannot build a positive index"
        )
    return frame.with_columns(
        (
            (pl.lit(1.0) + pl.col("normalized_return").fill_null(0.0))
            .cum_prod()
            * 100.0
        ).alias("signal_index")
    )


@dataclass
class PysystemtradeHistoryProvider:
    """Read Carver's imported data and construct a safe daily signal index."""

    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH
    cache_root: Path | str = DEFAULT_FUTURES_CACHE_ROOT
    use_cache: bool = True
    save_cache: bool = True

    def _database_metadata(self) -> tuple[int, str]:
        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            row = con.execute(
                "SELECT version, source_git_commit FROM meta.schema_version"
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError("pysystemtrade sidecar has no schema metadata")
        return int(row[0]), str(row[1])

    def _cache_directory(self, source_commit: str) -> Path:
        return (
            Path(self.cache_root)
            / "pysystemtrade"
            / f"v{HISTORY_SCHEMA_VERSION}"
            / source_commit[:12]
        )

    def _cache_paths(self, instrument_code: str, source_commit: str) -> dict[str, Path]:
        directory = self._cache_directory(source_commit)
        return {
            stream: directory / f"{instrument_code}_{stream}.parquet"
            for stream in ("signal", "marks", "carry")
        }

    def load(self, instrument_code: str) -> FuturesHistory:
        sidecar_version, source_commit = self._database_metadata()
        if sidecar_version != 3:
            raise ValueError(
                f"Unsupported pysystemtrade sidecar schema version: {sidecar_version}"
            )
        cache_paths = self._cache_paths(instrument_code, source_commit)

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            config = con.execute(
                """
                SELECT
                    i.description,
                    i.point_size,
                    i.currency,
                    i.asset_class,
                    i.region,
                    r.hold_roll_cycle,
                    r.roll_offset_days,
                    r.carry_offset,
                    r.priced_roll_cycle,
                    r.expiry_offset
                FROM raw.instrument_config i
                JOIN raw.roll_config r USING (instrument_code)
                WHERE i.instrument_code = ?
                """,
                [instrument_code],
            ).fetchone()
            if config is None:
                raise KeyError(
                    f"Unknown pysystemtrade instrument code: {instrument_code}"
                )

            if self.use_cache and all(path.exists() for path in cache_paths.values()):
                logger.info(
                    "pysystemtrade_history cache_hit instrument=%s source_commit=%s",
                    instrument_code,
                    source_commit,
                )
                signal = pl.read_parquet(cache_paths["signal"])
                marks = pl.read_parquet(cache_paths["marks"])
                carry = pl.read_parquet(cache_paths["carry"])
            else:
                logger.info(
                    "pysystemtrade_history load instrument=%s source_commit=%s",
                    instrument_code,
                    source_commit,
                )
                signal, marks, carry = self._load_uncached(con, instrument_code)
                if self.save_cache:
                    cache_paths["signal"].parent.mkdir(parents=True, exist_ok=True)
                    signal.write_parquet(cache_paths["signal"])
                    marks.write_parquet(cache_paths["marks"])
                    carry.write_parquet(cache_paths["carry"])
        finally:
            con.close()

        metadata = {
            "description": config[0],
            "point_size": config[1],
            "currency": config[2],
            "asset_class": config[3],
            "region": config[4],
            "hold_roll_cycle": config[5],
            "roll_offset_days": config[6],
            "carry_offset": config[7],
            "priced_roll_cycle": config[8],
            "expiry_offset": config[9],
            "source_git_commit": source_commit,
            "sidecar_schema_version": sidecar_version,
        }
        return FuturesHistory(
            source="pysystemtrade",
            instrument_code=instrument_code,
            schema_version=HISTORY_SCHEMA_VERSION,
            signal=signal,
            marks=marks,
            carry=carry,
            metadata=metadata,
        )

    @staticmethod
    def _load_uncached(
        con: duckdb.DuckDBPyConnection, instrument_code: str
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        adjusted = con.execute(
            """
            SELECT
                trade_date,
                source_timestamp,
                adjusted_price
            FROM daily.adjusted_prices
            WHERE instrument_code = ?
            ORDER BY trade_date
            """,
            [instrument_code],
        ).pl()
        marks = con.execute(
            """
            SELECT
                trade_date,
                source_timestamp,
                mark_price,
                contract_id
            FROM daily.marks
            WHERE instrument_code = ?
            ORDER BY trade_date
            """,
            [instrument_code],
        ).pl()
        if adjusted.is_empty() or marks.is_empty():
            raise ValueError(f"No price history for {instrument_code}")

        marks = (
            marks.sort("trade_date")
            .with_columns(
                (
                    pl.col("contract_id")
                    != pl.col("contract_id").shift(1)
                )
                .fill_null(False)
                .alias("is_roll"),
                pl.when(pl.col("mark_price") <= 0)
                .then(pl.lit("nonpositive_mark"))
                .otherwise(pl.lit(""))
                .alias("quality_flag"),
            )
        )

        joined = (
            adjusted.join(
                marks.select(
                    "trade_date",
                    pl.col("source_timestamp").alias("mark_source_timestamp"),
                    "mark_price",
                    "contract_id",
                    "is_roll",
                ),
                on="trade_date",
                how="inner",
            )
            .filter(pl.col("mark_price") > 0)
            .sort("trade_date")
        )
        signal = _build_positive_signal_index(
            joined,
            return_numerator="adjusted_price",
            denominator="mark_price",
        ).rename(
            {
                "point_change": "adjusted_point_change",
                "mark_price": "current_price",
            }
        )
        signal = signal.with_columns(
            pl.when(pl.col("normalized_return").is_null())
            .then(pl.lit("initial_observation"))
            .otherwise(pl.lit(""))
            .alias("quality_flag")
        ).select(
            "trade_date",
            "source_timestamp",
            "mark_source_timestamp",
            "adjusted_price",
            "adjusted_point_change",
            "current_price",
            "normalized_return",
            "signal_index",
            "contract_id",
            "is_roll",
            "quality_flag",
        )

        carry = con.execute(
            """
            SELECT
                trade_date,
                source_timestamp,
                current_price,
                current_contract,
                carry_price,
                carry_contract,
                '' AS quality_flag
            FROM daily.carry
            WHERE instrument_code = ?
            ORDER BY trade_date
            """,
            [instrument_code],
        ).pl()
        if carry.is_empty():
            carry = _empty_carry_frame()
        return signal, marks, carry


_GLOBEX_HISTORY_SQL = """
WITH bars AS (
    SELECT
        instrument_id,
        ts_event,
        open,
        high,
        low,
        close,
        volume,
        expiration,
        lag(close) OVER (
            PARTITION BY instrument_id, expiration
            ORDER BY ts_event
        ) AS previous_contract_close
    FROM daily
    WHERE asset = ?
      AND instrument_class = 'F'
      AND security_type = 'FUT'
      AND expiration IS NOT NULL
      AND ts_event < expiration
),
naive_ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY ts_event
            ORDER BY volume DESC, expiration ASC
        ) AS rn
    FROM bars
),
naive_front AS (
    SELECT
        ts_event,
        expiration,
        max(expiration) OVER (
            ORDER BY ts_event ROWS UNBOUNDED PRECEDING
        ) AS sticky_expiration
    FROM naive_ranked
    WHERE rn = 1
)
SELECT
    b.ts_event AS source_timestamp,
    CAST(b.ts_event AS DATE) AS trade_date,
    b.open,
    b.high,
    b.low,
    b.close AS mark_price,
    b.volume,
    b.instrument_id,
    b.expiration,
    b.previous_contract_close
FROM bars b
JOIN naive_front f
  ON b.ts_event = f.ts_event
 AND b.expiration = f.sticky_expiration
ORDER BY b.ts_event
"""


@dataclass
class GlobexHistoryProvider:
    """Build a roll-adjusted signal stream from paid/raw Globex marks."""

    db_path: Path | str = field(
        default_factory=lambda: Path(
            os.getenv("GLOBEX_DB_PATH", str(DEFAULT_GLOBEX_DB_PATH))
        )
    )
    cache_root: Path | str = DEFAULT_FUTURES_CACHE_ROOT
    use_cache: bool = True
    save_cache: bool = True

    def _dataset_fingerprint(self) -> str:
        stat = Path(self.db_path).stat()
        return f"{stat.st_size}-{stat.st_mtime_ns}"

    def _cache_paths(self, asset: str) -> dict[str, Path]:
        directory = (
            Path(self.cache_root)
            / "globex"
            / f"v{HISTORY_SCHEMA_VERSION}"
            / self._dataset_fingerprint()
        )
        return {
            stream: directory / f"{asset}_{stream}.parquet"
            for stream in ("signal", "marks", "carry")
        }

    def load(self, instrument_code: str) -> FuturesHistory:
        cache_paths = self._cache_paths(instrument_code)
        if self.use_cache and all(path.exists() for path in cache_paths.values()):
            logger.info("globex_history cache_hit asset=%s", instrument_code)
            signal = pl.read_parquet(cache_paths["signal"])
            marks = pl.read_parquet(cache_paths["marks"])
            carry = pl.read_parquet(cache_paths["carry"])
        else:
            logger.info("globex_history load asset=%s", instrument_code)
            con = duckdb.connect(str(self.db_path), read_only=True)
            try:
                raw = con.execute(_GLOBEX_HISTORY_SQL, [instrument_code]).pl()
            finally:
                con.close()
            if raw.is_empty():
                raise KeyError(f"Unknown or empty Globex futures asset: {instrument_code}")
            signal, marks, carry = self._from_raw(raw)
            if self.save_cache:
                cache_paths["signal"].parent.mkdir(parents=True, exist_ok=True)
                signal.write_parquet(cache_paths["signal"])
                marks.write_parquet(cache_paths["marks"])
                carry.write_parquet(cache_paths["carry"])

        try:
            spec = get_spec(instrument_code)
        except KeyError:
            spec = {}
        return FuturesHistory(
            source="globex",
            instrument_code=instrument_code,
            schema_version=HISTORY_SCHEMA_VERSION,
            signal=signal,
            marks=marks,
            carry=carry,
            metadata={
                "multiplier": spec.get("multiplier"),
                "exchange": spec.get("exchange"),
                "active_months": spec.get("active_months"),
                "dataset_fingerprint": self._dataset_fingerprint(),
            },
        )

    @staticmethod
    def _from_raw(
        raw: pl.DataFrame,
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        raw = raw.sort("trade_date").with_columns(
            pl.col("expiration").dt.strftime("%Y%m00").alias("contract_id")
        )
        marks = raw.select(
            "trade_date",
            "source_timestamp",
            "mark_price",
            "contract_id",
            "instrument_id",
            "expiration",
            "volume",
        ).with_columns(
            (
                pl.col("contract_id") != pl.col("contract_id").shift(1)
            )
            .fill_null(False)
            .alias("is_roll"),
            pl.when(pl.col("mark_price") <= 0)
            .then(pl.lit("nonpositive_mark"))
            .otherwise(pl.lit(""))
            .alias("quality_flag"),
        )

        eligible = raw.filter(pl.col("mark_price") > 0).with_columns(
            (
                pl.col("mark_price") - pl.col("previous_contract_close")
            ).alias("contract_point_change"),
        )
        signal = eligible.with_columns(
            pl.when(pl.col("previous_contract_close").is_not_null())
            .then(pl.col("contract_point_change") / pl.col("mark_price"))
            .otherwise(None)
            .alias("normalized_return")
        )
        invalid_growth = signal.filter(pl.col("normalized_return") <= -1)
        if invalid_growth.height:
            first_date = invalid_growth.get_column("trade_date")[0]
            raise ValueError(
                f"Globex normalized return is <= -100% on {first_date}"
            )
        signal = signal.with_columns(
            (
                (pl.lit(1.0) + pl.col("normalized_return").fill_null(0.0))
                .cum_prod()
                * 100.0
            ).alias("signal_index"),
            (
                pl.col("contract_id") != pl.col("contract_id").shift(1)
            )
            .fill_null(False)
            .alias("is_roll"),
            pl.when(pl.col("normalized_return").is_null())
            .then(pl.lit("initial_contract_observation"))
            .otherwise(pl.lit(""))
            .alias("quality_flag"),
        ).select(
            "trade_date",
            "source_timestamp",
            "contract_point_change",
            pl.col("mark_price").alias("current_price"),
            "normalized_return",
            "signal_index",
            "contract_id",
            "instrument_id",
            "expiration",
            "is_roll",
            "quality_flag",
        )
        return signal, marks, _empty_carry_frame()

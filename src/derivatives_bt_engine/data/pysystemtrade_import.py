"""Build a read-only research sidecar from pysystemtrade's shipped CSV data.

The importer deliberately does not write to the paid Globex database or to the
pysystemtrade checkout. It builds a new DuckDB file beside the requested output,
validates the complete logical import in one transaction, and atomically
publishes the sidecar only after validation succeeds.

Run after installing the project::

    pysystemtrade-import \
      --source /home/dev/projects/pysystemtrade/data/futures \
      --output /home/dev/fin/db/pysystemtrade_reference.duckdb
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import duckdb
import polars as pl

from derivatives_bt_engine.utils.logger import setup_logger


logger = setup_logger()

SCHEMA_VERSION = 4
DEFAULT_GLOBEX_DB_PATH = Path("/home/dev/fin/db/globex_mdp_3.0.duckdb")

_MULTIPLE_SCHEMA = {
    "DATETIME": pl.String,
    "CARRY": pl.Float64,
    "CARRY_CONTRACT": pl.String,
    "PRICE": pl.Float64,
    "PRICE_CONTRACT": pl.String,
    "FORWARD": pl.Float64,
    "FORWARD_CONTRACT": pl.String,
}
_ADJUSTED_SCHEMA = {"DATETIME": pl.String, "price": pl.Float64}
_ROLL_SCHEMA = {
    "DATE_TIME": pl.String,
    "current_contract": pl.String,
    "next_contract": pl.String,
    "carry_contract": pl.String,
}
_FX_SCHEMA = {"DATETIME": pl.String, "PRICE": pl.Float64}
_CsvReader = Callable[[Path, str], pl.DataFrame]


class ImportValidationError(RuntimeError):
    """Raised when a source tree or completed import violates an invariant."""


@dataclass(frozen=True)
class ImportResult:
    output_path: Path
    source_git_commit: str
    manifest_files: int
    multiple_rows: int
    adjusted_rows: int


@dataclass(frozen=True)
class _ManifestEntry:
    relative_path: str
    dataset: str
    instrument_code: Optional[str]
    sha256: str
    size_bytes: int
    modified_time_ns: int
    row_count: int
    min_source_timestamp: Optional[datetime]
    max_source_timestamp: Optional[datetime]


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_paths(source: Path, output: Path, globex_db: Path, replace: bool) -> None:
    source = _resolved(source)
    output = _resolved(output)
    globex_db = _resolved(globex_db)

    if not source.is_dir():
        raise ImportValidationError(f"pysystemtrade source directory does not exist: {source}")
    if output == globex_db:
        raise ImportValidationError(
            f"Refusing to overwrite the Globex source-of-record: {globex_db}"
        )
    if output == source or source in output.parents:
        raise ImportValidationError(
            f"Output must not be inside the immutable pysystemtrade source tree: {source}"
        )
    if output.exists() and not replace:
        raise FileExistsError(f"Output already exists; pass --replace to rebuild it: {output}")

    required_dirs = [
        "multiple_prices_csv",
        "adjusted_prices_csv",
        "roll_calendars_csv",
        "fx_prices_csv",
        "csvconfig",
    ]
    missing_dirs = [name for name in required_dirs if not (source / name).is_dir()]
    if missing_dirs:
        raise ImportValidationError(f"Missing required source directories: {missing_dirs}")

    required_config = ["instrumentconfig.csv", "rollconfig.csv", "spreadcosts.csv"]
    missing_config = [
        name for name in required_config if not (source / "csvconfig" / name).is_file()
    ]
    if missing_config:
        raise ImportValidationError(f"Missing required config files: {missing_config}")

    if not list((source / "multiple_prices_csv").glob("*.csv")):
        raise ImportValidationError("No multiple-price CSV files found")
    if not list((source / "adjusted_prices_csv").glob("*.csv")):
        raise ImportValidationError("No adjusted-price CSV files found")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_git_commit(source: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ImportValidationError(
            f"Cannot record a Git commit for pysystemtrade source: {source}"
        ) from error
    commit = result.stdout.strip()
    if not commit:
        raise ImportValidationError(
            f"Git returned an empty commit for pysystemtrade source: {source}"
        )
    return commit


def _timestamp_expr(column: str) -> pl.Expr:
    return pl.col(column).str.to_datetime(strict=True, time_unit="us")


def _following_session_date_expr(column: str = "source_timestamp") -> pl.Expr:
    """Map Sunday observations to Monday while preserving source timestamps.

    Carver's mixed-frequency CSV timestamps are naive, but Sunday evening rows
    belong to the following futures trading session.  The paid Globex daily
    table already applies that convention, so persist it explicitly here rather
    than making every downstream consumer reinterpret the raw timestamp.
    """
    timestamp = pl.col(column)
    return (
        pl.when(timestamp.dt.weekday() == 7)
        .then(timestamp.dt.offset_by("1d").dt.date())
        .otherwise(timestamp.dt.date())
        .alias("trade_date")
    )


def _read_multiple(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, schema=_MULTIPLE_SCHEMA, null_values=[""])
        .with_columns(
            _timestamp_expr("DATETIME").alias("source_timestamp"),
            pl.lit(path.stem).alias("instrument_code"),
            pl.lit(relative_path).alias("source_file"),
        )
        .with_columns(_following_session_date_expr())
        .select(
            "instrument_code",
            "source_timestamp",
            "trade_date",
            pl.col("PRICE").alias("price"),
            pl.col("PRICE_CONTRACT").alias("price_contract"),
            pl.col("CARRY").alias("carry"),
            pl.col("CARRY_CONTRACT").alias("carry_contract"),
            pl.col("FORWARD").alias("forward"),
            pl.col("FORWARD_CONTRACT").alias("forward_contract"),
            "source_file",
        )
    )


def _read_adjusted(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, schema=_ADJUSTED_SCHEMA, null_values=[""])
        .with_columns(
            _timestamp_expr("DATETIME").alias("source_timestamp"),
            pl.lit(path.stem).alias("instrument_code"),
            pl.lit(relative_path).alias("source_file"),
        )
        .with_columns(_following_session_date_expr())
        .select(
            "instrument_code",
            "source_timestamp",
            "trade_date",
            pl.col("price").alias("adjusted_price"),
            "source_file",
        )
    )


def _read_roll_calendar(
    path: Path, relative_path: str, calendar_type: str
) -> pl.DataFrame:
    frame = pl.read_csv(
        path,
        schema_overrides=_ROLL_SCHEMA,
        null_values=[""],
    )
    expected_columns = set(_ROLL_SCHEMA)
    extra_columns = set(frame.columns) - expected_columns
    # Three shipped futures calendars have a historical unnamed nullable
    # boolean column. Preserve strictness for every other schema variant.
    if extra_columns not in (set(), {""}):
        raise ImportValidationError(
            f"Unexpected roll-calendar columns in {relative_path}: {sorted(extra_columns)}"
        )
    if "" in frame.columns:
        legacy_values = frame.get_column("").drop_nulls()
        if legacy_values.dtype != pl.Boolean or legacy_values.any():
            raise ImportValidationError(
                f"Unexpected trailing roll-calendar values in {relative_path}: "
                f"{legacy_values.unique().to_list()}"
            )
        frame = frame.rename({"": "legacy_flag"})
    else:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Boolean).alias("legacy_flag")
        )

    return (
        frame
        .with_row_index("source_row_number", offset=2)
        .with_columns(
            _timestamp_expr("DATE_TIME").alias("source_timestamp"),
            pl.lit(path.stem).alias("instrument_code"),
            pl.lit(calendar_type).alias("calendar_type"),
            pl.lit(relative_path).alias("source_file"),
        )
        .select(
            "calendar_type",
            "instrument_code",
            "source_row_number",
            "source_timestamp",
            "current_contract",
            "next_contract",
            "carry_contract",
            "legacy_flag",
            "source_file",
        )
    )


def _read_fx(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, schema=_FX_SCHEMA, null_values=[""])
        .with_columns(
            _timestamp_expr("DATETIME").alias("source_timestamp"),
            pl.lit(path.stem).alias("currency_pair"),
            pl.lit(relative_path).alias("source_file"),
        )
        .select(
            "currency_pair",
            "source_timestamp",
            pl.col("PRICE").alias("price"),
            "source_file",
        )
    )


def _read_instrument_config(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, infer_schema_length=10_000)
        .with_columns(pl.lit(relative_path).alias("source_file"))
        .select(
            pl.col("Instrument").cast(pl.String).alias("instrument_code"),
            pl.col("Description").cast(pl.String).alias("description"),
            pl.col("Pointsize").cast(pl.Float64).alias("point_size"),
            pl.col("Currency").cast(pl.String).alias("currency"),
            pl.col("AssetClass").cast(pl.String).alias("asset_class"),
            pl.col("PerBlock").cast(pl.Float64).alias("per_block_cost"),
            pl.col("Percentage").cast(pl.Float64).alias("percentage_cost"),
            pl.col("PerTrade").cast(pl.Float64).alias("per_trade_cost"),
            pl.col("Region").cast(pl.String).alias("region"),
            "source_file",
        )
    )


def _read_roll_config(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, infer_schema_length=10_000)
        .with_columns(pl.lit(relative_path).alias("source_file"))
        .select(
            pl.col("Instrument").cast(pl.String).alias("instrument_code"),
            pl.col("HoldRollCycle").cast(pl.String).alias("hold_roll_cycle"),
            pl.col("RollOffsetDays").cast(pl.Int64).alias("roll_offset_days"),
            pl.col("CarryOffset").cast(pl.Int64).alias("carry_offset"),
            pl.col("PricedRollCycle").cast(pl.String).alias("priced_roll_cycle"),
            pl.col("ExpiryOffset").cast(pl.Int64).alias("expiry_offset"),
            "source_file",
        )
    )


def _read_spread_costs(path: Path, relative_path: str) -> pl.DataFrame:
    return (
        pl.read_csv(path, infer_schema_length=10_000)
        .with_columns(pl.lit(relative_path).alias("source_file"))
        .select(
            pl.col("Instrument").cast(pl.String).alias("instrument_code"),
            pl.col("SpreadCost").cast(pl.Float64).alias("spread_cost"),
            "source_file",
        )
    )


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA meta")
    con.execute("CREATE SCHEMA raw")
    con.execute("CREATE SCHEMA daily")
    con.execute("CREATE SCHEMA qa")

    con.execute(
        """
        CREATE TABLE meta.schema_version (
            version INTEGER NOT NULL,
            imported_at_utc TIMESTAMPTZ NOT NULL,
            importer VARCHAR NOT NULL,
            source_root VARCHAR NOT NULL,
            source_git_commit VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE meta.dataset_manifest (
            relative_path VARCHAR PRIMARY KEY,
            dataset VARCHAR NOT NULL,
            instrument_code VARCHAR,
            sha256 VARCHAR NOT NULL,
            size_bytes UBIGINT NOT NULL,
            modified_time_ns UBIGINT NOT NULL,
            row_count UBIGINT NOT NULL,
            min_source_timestamp TIMESTAMP,
            max_source_timestamp TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.multiple_prices (
            instrument_code VARCHAR NOT NULL,
            source_timestamp TIMESTAMP NOT NULL,
            trade_date DATE NOT NULL,
            price DOUBLE,
            price_contract VARCHAR,
            carry DOUBLE,
            carry_contract VARCHAR,
            forward DOUBLE,
            forward_contract VARCHAR,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.adjusted_prices (
            instrument_code VARCHAR NOT NULL,
            source_timestamp TIMESTAMP NOT NULL,
            trade_date DATE NOT NULL,
            adjusted_price DOUBLE,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.roll_calendars (
            calendar_type VARCHAR NOT NULL,
            instrument_code VARCHAR NOT NULL,
            source_row_number UBIGINT NOT NULL,
            source_timestamp TIMESTAMP NOT NULL,
            current_contract VARCHAR NOT NULL,
            next_contract VARCHAR NOT NULL,
            carry_contract VARCHAR NOT NULL,
            legacy_flag BOOLEAN,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.fx_prices (
            currency_pair VARCHAR NOT NULL,
            source_timestamp TIMESTAMP NOT NULL,
            price DOUBLE,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.instrument_config (
            instrument_code VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            point_size DOUBLE NOT NULL,
            currency VARCHAR NOT NULL,
            asset_class VARCHAR NOT NULL,
            per_block_cost DOUBLE NOT NULL,
            percentage_cost DOUBLE NOT NULL,
            per_trade_cost DOUBLE NOT NULL,
            region VARCHAR NOT NULL,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.roll_config (
            instrument_code VARCHAR NOT NULL,
            hold_roll_cycle VARCHAR NOT NULL,
            roll_offset_days BIGINT NOT NULL,
            carry_offset BIGINT NOT NULL,
            priced_roll_cycle VARCHAR NOT NULL,
            expiry_offset BIGINT NOT NULL,
            source_file VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE raw.spread_costs (
            instrument_code VARCHAR NOT NULL,
            spread_cost DOUBLE NOT NULL,
            source_file VARCHAR NOT NULL
        )
        """
    )


def _insert_frame(
    con: duckdb.DuckDBPyConnection, table: str, frame: pl.DataFrame
) -> None:
    registration = f"_batch_{uuid.uuid4().hex}"
    con.register(registration, frame)
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM {registration}")
    finally:
        con.unregister(registration)


def _manifest_entry(
    source: Path,
    path: Path,
    dataset: str,
    instrument_code: Optional[str],
    frame: pl.DataFrame,
    timestamp_column: Optional[str] = "source_timestamp",
) -> _ManifestEntry:
    stat = path.stat()
    if timestamp_column is None:
        min_timestamp = None
        max_timestamp = None
    else:
        min_timestamp = frame.get_column(timestamp_column).min()
        max_timestamp = frame.get_column(timestamp_column).max()
    return _ManifestEntry(
        relative_path=path.relative_to(source).as_posix(),
        dataset=dataset,
        instrument_code=instrument_code,
        sha256=_sha256(path),
        size_bytes=stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        row_count=frame.height,
        min_source_timestamp=min_timestamp,
        max_source_timestamp=max_timestamp,
    )


def _import_files(
    con: duckdb.DuckDBPyConnection, source: Path
) -> list[_ManifestEntry]:
    manifest: list[_ManifestEntry] = []

    phases: list[tuple[str, Path, str, _CsvReader]] = [
        (
            "multiple_prices",
            source / "multiple_prices_csv",
            "raw.multiple_prices",
            _read_multiple,
        ),
        (
            "adjusted_prices",
            source / "adjusted_prices_csv",
            "raw.adjusted_prices",
            _read_adjusted,
        ),
        ("fx_prices", source / "fx_prices_csv", "raw.fx_prices", _read_fx),
    ]
    for dataset, directory, table, reader in phases:
        files = sorted(directory.glob("*.csv"))
        logger.info("pysystemtrade_import phase=%s files=%d", dataset, len(files))
        for path in files:
            relative_path = path.relative_to(source).as_posix()
            logger.debug(
                "pysystemtrade_import dataset=%s instrument=%s source_file=%s",
                dataset,
                path.stem,
                relative_path,
            )
            frame = reader(path, relative_path)
            _insert_frame(con, table, frame)
            manifest.append(
                _manifest_entry(source, path, dataset, path.stem, frame)
            )

    roll_sources = [
        ("roll_calendars", source / "roll_calendars_csv", "futures"),
        (
            "crypto_spread_roll_calendars",
            source / "crypto_spread_roll_calendars_csv",
            "crypto_spread",
        ),
    ]
    for dataset, directory, calendar_type in roll_sources:
        if not directory.exists():
            continue
        files = sorted(directory.glob("*.csv"))
        logger.info("pysystemtrade_import phase=%s files=%d", dataset, len(files))
        for path in files:
            relative_path = path.relative_to(source).as_posix()
            frame = _read_roll_calendar(path, relative_path, calendar_type)
            _insert_frame(con, "raw.roll_calendars", frame)
            manifest.append(
                _manifest_entry(source, path, dataset, path.stem, frame)
            )

    config_readers: list[tuple[str, str, str, _CsvReader]] = [
        (
            "instrument_config",
            "instrumentconfig.csv",
            "raw.instrument_config",
            _read_instrument_config,
        ),
        ("roll_config", "rollconfig.csv", "raw.roll_config", _read_roll_config),
        ("spread_costs", "spreadcosts.csv", "raw.spread_costs", _read_spread_costs),
    ]
    logger.info("pysystemtrade_import phase=config files=%d", len(config_readers))
    for dataset, filename, table, reader in config_readers:
        path = source / "csvconfig" / filename
        relative_path = path.relative_to(source).as_posix()
        frame = reader(path, relative_path)
        _insert_frame(con, table, frame)
        manifest.append(
            _manifest_entry(
                source,
                path,
                dataset,
                None,
                frame,
                timestamp_column=None,
            )
        )

    return manifest


def _manifest_frame(entries: Iterable[_ManifestEntry]) -> pl.DataFrame:
    entries = list(entries)
    return pl.DataFrame(
        {
            "relative_path": [entry.relative_path for entry in entries],
            "dataset": [entry.dataset for entry in entries],
            "instrument_code": [entry.instrument_code for entry in entries],
            "sha256": [entry.sha256 for entry in entries],
            "size_bytes": [entry.size_bytes for entry in entries],
            "modified_time_ns": [entry.modified_time_ns for entry in entries],
            "row_count": [entry.row_count for entry in entries],
            "min_source_timestamp": [entry.min_source_timestamp for entry in entries],
            "max_source_timestamp": [entry.max_source_timestamp for entry in entries],
        },
        schema={
            "relative_path": pl.String,
            "dataset": pl.String,
            "instrument_code": pl.String,
            "sha256": pl.String,
            "size_bytes": pl.UInt64,
            "modified_time_ns": pl.UInt64,
            "row_count": pl.UInt64,
            "min_source_timestamp": pl.Datetime("us"),
            "max_source_timestamp": pl.Datetime("us"),
        },
    ).sort("relative_path")


def _duplicate_group_count(
    con: duckdb.DuckDBPyConnection, table: str, key_columns: str
) -> int:
    return con.execute(
        f"""
        SELECT count(*)
        FROM (
            SELECT {key_columns}, count(*) AS n
            FROM {table}
            GROUP BY {key_columns}
            HAVING count(*) > 1
        )
        """
    ).fetchone()[0]


def _validate_import(
    con: duckdb.DuckDBPyConnection, manifest: list[_ManifestEntry]
) -> None:
    unique_keys = [
        ("raw.multiple_prices", "instrument_code, source_timestamp"),
        ("raw.adjusted_prices", "instrument_code, source_timestamp"),
        ("raw.roll_calendars", "source_file, source_row_number"),
        ("raw.fx_prices", "currency_pair, source_timestamp"),
        ("raw.instrument_config", "instrument_code"),
        ("raw.roll_config", "instrument_code"),
        ("raw.spread_costs", "instrument_code"),
    ]
    for table, keys in unique_keys:
        duplicate_groups = _duplicate_group_count(con, table, keys)
        if duplicate_groups:
            raise ImportValidationError(
                f"{table} contains {duplicate_groups} duplicate key group(s) for {keys}"
            )

    null_timestamp_checks = [
        ("raw.multiple_prices", "source_timestamp"),
        ("raw.adjusted_prices", "source_timestamp"),
        ("raw.roll_calendars", "source_timestamp"),
        ("raw.fx_prices", "source_timestamp"),
    ]
    for table, column in null_timestamp_checks:
        null_count = con.execute(
            f"SELECT count(*) FROM {table} WHERE {column} IS NULL"
        ).fetchone()[0]
        if null_count:
            raise ImportValidationError(f"{table} contains {null_count} null timestamps")

    for table in ["raw.multiple_prices", "raw.adjusted_prices"]:
        invalid_trade_dates = con.execute(
            f"""
            SELECT count(*)
            FROM {table}
            WHERE trade_date IS NULL
               OR trade_date != CASE
                    WHEN EXTRACT(ISODOW FROM source_timestamp) = 7
                    THEN CAST(source_timestamp AS DATE) + 1
                    ELSE CAST(source_timestamp AS DATE)
                  END
            """
        ).fetchone()[0]
        if invalid_trade_dates:
            raise ImportValidationError(
                f"{table} contains {invalid_trade_dates} invalid normalized trade dates"
            )

    expected_rows = {
        "raw.multiple_prices": sum(
            e.row_count for e in manifest if e.dataset == "multiple_prices"
        ),
        "raw.adjusted_prices": sum(
            e.row_count for e in manifest if e.dataset == "adjusted_prices"
        ),
        "raw.fx_prices": sum(e.row_count for e in manifest if e.dataset == "fx_prices"),
        "raw.roll_calendars": sum(
            e.row_count
            for e in manifest
            if e.dataset in {"roll_calendars", "crypto_spread_roll_calendars"}
        ),
        "raw.instrument_config": sum(
            e.row_count for e in manifest if e.dataset == "instrument_config"
        ),
        "raw.roll_config": sum(e.row_count for e in manifest if e.dataset == "roll_config"),
        "raw.spread_costs": sum(e.row_count for e in manifest if e.dataset == "spread_costs"),
    }
    for table, expected in expected_rows.items():
        actual = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if actual != expected:
            raise ImportValidationError(
                f"{table} row count mismatch: expected={expected} actual={actual}"
            )

    missing_adjusted = con.execute(
        """
        SELECT instrument_code FROM raw.multiple_prices
        EXCEPT
        SELECT instrument_code FROM raw.adjusted_prices
        """
    ).fetchall()
    if missing_adjusted:
        raise ImportValidationError(
            "Multiple-price instruments missing adjusted data: "
            f"{[row[0] for row in missing_adjusted]}"
        )

    for table in ["raw.instrument_config", "raw.roll_config", "raw.spread_costs"]:
        missing = con.execute(
            f"""
            SELECT DISTINCT instrument_code FROM raw.multiple_prices
            EXCEPT
            SELECT instrument_code FROM {table}
            """
        ).fetchall()
        if missing:
            raise ImportValidationError(
                f"Multiple-price instruments missing from {table}: {[row[0] for row in missing]}"
            )


def _create_daily_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Materialize one last-complete observation per normalized session.

    The shipped files mix historical 23:00 daily observations with irregular
    intraday snapshots.  After Sunday-to-Monday normalization, the latest
    complete timestamp is the session close: normally 23:00, or the final
    available complete snapshot when 23:00 is absent.  Selection is performed
    independently for adjusted prices, marks, roll inputs, and carry because
    their validity requirements differ.  Full-frequency raw roll inputs remain
    authoritative for reconstructing Panama prices; this daily table is an
    inspectable EOD projection, not the stitch input.
    """
    con.execute(
        """
        CREATE TABLE daily.adjusted_prices AS
        SELECT
            instrument_code,
            trade_date,
            source_timestamp,
            adjusted_price,
            CASE
                WHEN EXTRACT(HOUR FROM source_timestamp) = 23 THEN 'eod_2300'
                ELSE 'last_complete'
            END AS selection_policy,
            source_file
        FROM raw.adjusted_prices
        WHERE adjusted_price IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY instrument_code, trade_date
            ORDER BY source_timestamp DESC
        ) = 1
        ORDER BY instrument_code, trade_date
        """
    )
    con.execute(
        """
        CREATE TABLE daily.marks AS
        SELECT
            instrument_code,
            trade_date,
            source_timestamp,
            price AS mark_price,
            price_contract AS contract_id,
            CASE
                WHEN EXTRACT(HOUR FROM source_timestamp) = 23 THEN 'eod_2300'
                ELSE 'last_complete'
            END AS selection_policy,
            source_file
        FROM raw.multiple_prices
        WHERE price IS NOT NULL
          AND price_contract IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY instrument_code, trade_date
            ORDER BY source_timestamp DESC
        ) = 1
        ORDER BY instrument_code, trade_date
        """
    )
    con.execute(
        """
        CREATE TABLE daily.roll_inputs AS
        SELECT
            instrument_code,
            trade_date,
            source_timestamp,
            price AS current_price,
            price_contract AS current_contract,
            forward AS forward_price,
            forward_contract,
            CASE
                WHEN EXTRACT(HOUR FROM source_timestamp) = 23 THEN 'eod_2300'
                ELSE 'last_complete'
            END AS selection_policy,
            source_file
        FROM raw.multiple_prices
        WHERE price IS NOT NULL
          AND price_contract IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY instrument_code, trade_date
            ORDER BY source_timestamp DESC
        ) = 1
        ORDER BY instrument_code, trade_date
        """
    )
    con.execute(
        """
        CREATE TABLE daily.carry AS
        SELECT
            instrument_code,
            trade_date,
            source_timestamp,
            price AS current_price,
            price_contract AS current_contract,
            carry AS carry_price,
            carry_contract,
            CASE
                WHEN EXTRACT(HOUR FROM source_timestamp) = 23 THEN 'eod_2300'
                ELSE 'last_complete'
            END AS selection_policy,
            source_file
        FROM raw.multiple_prices
        WHERE price IS NOT NULL
          AND carry IS NOT NULL
          AND price_contract IS NOT NULL
          AND carry_contract IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY instrument_code, trade_date
            ORDER BY source_timestamp DESC
        ) = 1
        ORDER BY instrument_code, trade_date
        """
    )


def _create_qa_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE qa.roll_calendar_duplicate_timestamps AS
        SELECT
            calendar_type,
            instrument_code,
            source_timestamp,
            count(*) AS row_count,
            string_agg(
                current_contract || '>' || next_contract || ':' || carry_contract,
                ';' ORDER BY source_row_number
            ) AS contract_transitions
        FROM raw.roll_calendars
        GROUP BY calendar_type, instrument_code, source_timestamp
        HAVING count(*) > 1
        ORDER BY calendar_type, instrument_code, source_timestamp
        """
    )
    con.execute(
        """
        CREATE TABLE qa.series_coverage AS
        WITH multiple_stats AS (
            SELECT
                instrument_code,
                count(*) AS multiple_rows,
                min(source_timestamp) AS multiple_start,
                max(source_timestamp) AS multiple_end,
                count(price) AS price_rows,
                count(carry) AS carry_rows,
                count(forward) AS forward_rows,
                count(*) FILTER (WHERE price <= 0) AS nonpositive_price_rows
            FROM raw.multiple_prices
            GROUP BY instrument_code
        ),
        daily_stats AS (
            SELECT
                m.instrument_code,
                count(*) AS price_days,
                count(c.trade_date) AS carry_days,
                count(*) FILTER (WHERE r.forward IS NOT NULL) AS forward_days
            FROM daily.marks m
            LEFT JOIN daily.carry c USING (instrument_code, trade_date)
            LEFT JOIN raw.multiple_prices r
              ON r.instrument_code = m.instrument_code
             AND r.source_timestamp = m.source_timestamp
            GROUP BY m.instrument_code
        ),
        adjusted_stats AS (
            SELECT
                instrument_code,
                count(*) AS adjusted_rows,
                min(source_timestamp) AS adjusted_start,
                max(source_timestamp) AS adjusted_end,
                count(*) FILTER (WHERE adjusted_price IS NULL) AS adjusted_null_rows,
                count(*) FILTER (WHERE adjusted_price <= 0) AS adjusted_nonpositive_rows,
                min(adjusted_price) AS adjusted_min,
                max(adjusted_price) AS adjusted_max
            FROM raw.adjusted_prices
            GROUP BY instrument_code
        )
        SELECT
            m.*,
            d.price_days,
            d.carry_days,
            d.forward_days,
            CASE WHEN d.price_days > 0 THEN d.carry_days::DOUBLE / d.price_days END AS carry_day_coverage,
            CASE WHEN d.price_days > 0 THEN d.forward_days::DOUBLE / d.price_days END AS forward_day_coverage,
            a.adjusted_rows,
            a.adjusted_start,
            a.adjusted_end,
            a.adjusted_null_rows,
            a.adjusted_nonpositive_rows,
            a.adjusted_min,
            a.adjusted_max
        FROM multiple_stats m
        LEFT JOIN daily_stats d USING (instrument_code)
        LEFT JOIN adjusted_stats a USING (instrument_code)
        ORDER BY m.instrument_code
        """
    )
    con.execute(
        """
        CREATE TABLE qa.daily_selection AS
        SELECT
            'adjusted_prices' AS stream,
            count(*) AS selected_days,
            count(*) FILTER (WHERE selection_policy = 'eod_2300') AS eod_2300_days,
            count(*) FILTER (WHERE selection_policy = 'last_complete') AS fallback_days
        FROM daily.adjusted_prices
        UNION ALL
        SELECT
            'marks' AS stream,
            count(*) AS selected_days,
            count(*) FILTER (WHERE selection_policy = 'eod_2300') AS eod_2300_days,
            count(*) FILTER (WHERE selection_policy = 'last_complete') AS fallback_days
        FROM daily.marks
        UNION ALL
        SELECT
            'carry' AS stream,
            count(*) AS selected_days,
            count(*) FILTER (WHERE selection_policy = 'eod_2300') AS eod_2300_days,
            count(*) FILTER (WHERE selection_policy = 'last_complete') AS fallback_days
        FROM daily.carry
        ORDER BY stream
        """
    )
    con.execute(
        """
        CREATE TABLE qa.session_date_normalization AS
        SELECT
            'multiple_prices' AS dataset,
            count(*) AS total_rows,
            count(*) FILTER (
                WHERE trade_date != CAST(source_timestamp AS DATE)
            ) AS shifted_rows
        FROM raw.multiple_prices
        UNION ALL
        SELECT
            'adjusted_prices' AS dataset,
            count(*) AS total_rows,
            count(*) FILTER (
                WHERE trade_date != CAST(source_timestamp AS DATE)
            ) AS shifted_rows
        FROM raw.adjusted_prices
        ORDER BY dataset
        """
    )
    con.execute(
        """
        CREATE TABLE qa.dataset_summary AS
        SELECT
            count(*) AS instruments,
            sum(multiple_rows) AS multiple_rows,
            sum(adjusted_rows) AS adjusted_rows,
            min(multiple_start) AS earliest_timestamp,
            max(multiple_end) AS latest_timestamp,
            avg(carry_day_coverage) AS mean_carry_day_coverage,
            median(carry_day_coverage) AS median_carry_day_coverage,
            sum(carry_days)::DOUBLE / nullif(sum(price_days), 0) AS weighted_carry_day_coverage,
            avg(forward_day_coverage) AS mean_forward_day_coverage,
            median(forward_day_coverage) AS median_forward_day_coverage,
            sum(forward_days)::DOUBLE / nullif(sum(price_days), 0) AS weighted_forward_day_coverage,
            count(*) FILTER (WHERE carry_day_coverage >= 0.90) AS carry_instruments_ge_90pct,
            count(*) FILTER (WHERE carry_day_coverage < 0.50) AS carry_instruments_lt_50pct,
            sum(nonpositive_price_rows) AS nonpositive_raw_price_rows,
            count(*) FILTER (WHERE adjusted_min <= 0) AS nonpositive_adjusted_instruments,
            sum(adjusted_null_rows) AS adjusted_null_rows,
            (
                SELECT count(*)
                FROM qa.roll_calendar_duplicate_timestamps
            ) AS roll_duplicate_timestamp_groups
        FROM qa.series_coverage
        """
    )


def build_sidecar(
    source: Path | str,
    output: Path | str,
    *,
    globex_db: Path | str = DEFAULT_GLOBEX_DB_PATH,
    replace: bool = False,
) -> ImportResult:
    """Build and atomically publish a validated pysystemtrade DuckDB sidecar."""
    source = _resolved(Path(source))
    output = _resolved(Path(output))
    globex_db = _resolved(Path(globex_db))
    _validate_paths(source, output, globex_db, replace)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{uuid.uuid4().hex}")
    source_commit = _source_git_commit(source)
    imported_at = datetime.now(timezone.utc)

    logger.info(
        "pysystemtrade_import start source=%s output=%s source_git_commit=%s schema_version=%d",
        source,
        output,
        source_commit,
        SCHEMA_VERSION,
    )

    con: Optional[duckdb.DuckDBPyConnection] = None
    try:
        con = duckdb.connect(str(temporary))
        con.execute("BEGIN TRANSACTION")
        _create_schema(con)
        con.execute(
            "INSERT INTO meta.schema_version VALUES (?, ?, ?, ?, ?)",
            [
                SCHEMA_VERSION,
                imported_at,
                "derivatives_bt_engine.data.pysystemtrade_import",
                str(source),
                source_commit,
            ],
        )
        manifest = _import_files(con, source)
        _insert_frame(con, "meta.dataset_manifest", _manifest_frame(manifest))
        _validate_import(con, manifest)
        _create_daily_tables(con)
        _create_qa_tables(con)
        con.execute("COMMIT")
        con.execute("CHECKPOINT")

        summary = con.execute(
            "SELECT multiple_rows, adjusted_rows FROM qa.dataset_summary"
        ).fetchone()
        con.close()
        con = None

        os.replace(temporary, output)
        result = ImportResult(
            output_path=output,
            source_git_commit=source_commit,
            manifest_files=len(manifest),
            multiple_rows=summary[0],
            adjusted_rows=summary[1],
        )
        logger.info(
            "pysystemtrade_import complete output=%s manifest_files=%d "
            "multiple_rows=%d adjusted_rows=%d",
            result.output_path,
            result.manifest_files,
            result.multiple_rows,
            result.adjusted_rows,
        )
        return result
    except Exception:
        if con is not None:
            try:
                con.execute("ROLLBACK")
            except duckdb.Error:
                pass
            con.close()
        if temporary.exists():
            temporary.unlink()
        logger.exception(
            "pysystemtrade_import failed source=%s output=%s", source, output
        )
        raise


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="Path to pysystemtrade/data/futures",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the new DuckDB sidecar",
    )
    parser.add_argument(
        "--globex-db",
        default=os.getenv("GLOBEX_DB_PATH", str(DEFAULT_GLOBEX_DB_PATH)),
        help="Protected Globex DB path; the importer refuses to use it as output",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Atomically replace an existing sidecar after the new build validates",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    result = build_sidecar(
        args.source,
        args.output,
        globex_db=args.globex_db,
        replace=args.replace,
    )
    print(
        f"Built {result.output_path} from {result.manifest_files} files "
        f"({result.multiple_rows:,} multiple rows; {result.adjusted_rows:,} adjusted rows)"
    )


if __name__ == "__main__":
    main()

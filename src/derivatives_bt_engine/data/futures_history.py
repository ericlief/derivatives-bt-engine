"""Source-neutral futures history streams for research and backtesting.

The existing :class:`FuturesDataLoader` returns one continuous ``close``
column that older callers use for both signals and marking.  That shape is
not safe for additively adjusted data.  This module keeps three concerns
explicit:

``signal``
    A positive index and contract-consistent arithmetic returns for return
    TSMOM and Goulding.
``panama``
    A generated additive continuous price for point-price rules such as
    Carver EWMAC.  Supplied adjusted prices are validation data only.
``marks``
    Actual selected-contract prices and contract identity.
``carry``
    Same-timestamp current/carry observations.  Missing carry is absent, not
    zero-filled.

Both providers open their DuckDB inputs read-only.  No provider writes to the
Globex database or the pysystemtrade sidecar.
"""

from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence

import duckdb
import polars as pl

from derivatives_bt_engine.calculations.continuous_futures import (
    build_continuous_futures,
    select_daily_continuous,
)
from derivatives_bt_engine.calculations.instruments import get_spec
from derivatives_bt_engine.calculations.roll_policy import (
    ContractRollPolicy,
    carver_aligned_globex_roll_policy_set,
)
from derivatives_bt_engine.logging_config import setup_logger


logger = setup_logger()

HISTORY_SCHEMA_VERSION = 11
DEFAULT_PYSYSTEMTRADE_DB_PATH = Path(
    "/home/dev/fin/db/pysystemtrade_reference.duckdb"
)
DEFAULT_GLOBEX_DB_PATH = Path("/home/dev/fin/db/globex_mdp_3.0.duckdb")
DEFAULT_FUTURES_CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "futures"
FUTURES_HISTORY_CACHE_SCHEMA_VERSION = 1

_SIGNAL_COLUMNS = {
    "date",
    "source_timestamp",
    "ret_1d",
    "signal_index",
    "quality_flag",
}
_MARK_COLUMNS = {
    "date",
    "source_timestamp",
    "mark_price",
    "contract_id",
    "is_roll",
    "quality_flag",
}
_CARRY_COLUMNS = {
    "date",
    "source_timestamp",
    "current_price",
    "current_contract",
    "carry_price",
    "carry_contract",
    "quality_flag",
}
_PANAMA_COLUMNS = {
    "date",
    "source_timestamp",
    "panama_price",
    "point_change",
    "quality_flag",
}


class FuturesHistoryProvider(Protocol):
    """Interface implemented by source-specific history providers."""

    def load(self, instrument_code: str) -> "FuturesHistory":
        """Load one source instrument into distinct research streams."""


@dataclass(frozen=True)
class FuturesHistory:
    """One instrument's source-neutral derived and raw-price streams."""

    source: str
    instrument_code: str
    schema_version: int
    signal: pl.DataFrame
    marks: pl.DataFrame
    carry: pl.DataFrame
    metadata: Mapping[str, object] = field(default_factory=dict)
    panama: pl.DataFrame = field(default_factory=lambda: _empty_panama_frame())

    def __post_init__(self) -> None:
        """Normalize date keys and validate stream schemas and levels."""
        _validate_stream("signal", self.signal, _SIGNAL_COLUMNS)
        _validate_stream("marks", self.marks, _MARK_COLUMNS)
        _validate_stream("carry", self.carry, _CARRY_COLUMNS, allow_empty=True)
        _validate_stream("panama", self.panama, _PANAMA_COLUMNS, allow_empty=True)

        if self.signal.filter(
            pl.col("signal_index").is_null() | (pl.col("signal_index") <= 0)
        ).height:
            raise ValueError("signal_index must be non-null and strictly positive")

    def signal_bars(self) -> pl.DataFrame:
        """Adapt the positive signal index to public daily-bar columns.

        This is deliberately signal-only.  It must not be used as an
        executable price or a contract mark. The adapted frame uses ``date``;
        callers entering legacy signal calculations translate that key to
        their internal ``ts_event`` convention at the calculation boundary.
        """
        signal = self.signal
        if "return_valid" in signal.columns:
            signal = signal.filter(
                pl.col("return_valid")
                | (pl.col("quality_flag") == "initial_observation")
            )
        return (
            signal.select(
                "date",
                pl.col("signal_index").alias("close"),
                "ret_1d",
                "quality_flag",
            )
            .sort("date")
        )

    def panama_bars(self) -> pl.DataFrame:
        """Adapt generated additive prices to public daily-bar columns.

        The stored Panama stream and this adapter both use ``point_change`` so
        the EWMAC pipeline has one name from history construction onward. The
        public date key is ``date``; legacy calculations adapt it internally.
        """
        if self.panama.is_empty():
            raise ValueError("history has no generated Panama stream")
        return self.panama.select(
            "date",
            pl.col("panama_price").alias("close"),
            "point_change",
            "quality_flag",
        ).sort("date")


def futures_history_parquet_paths(
    directory: Path | str,
) -> dict[str, Path]:
    """Return the stable Parquet paths for one cached history bundle.

    A bundle stores the four canonical streams separately because they have
    different schemas. The manifest is written last and therefore also acts
    as the cache-completeness marker.
    """
    root = Path(directory)
    return {
        stream: root / f"{stream}.parquet"
        for stream in ("signal", "marks", "carry", "panama", "manifest")
    }


def futures_history_parquet_cache_exists(directory: Path | str) -> bool:
    """Return whether every file in one history Parquet bundle exists."""
    return all(
        path.exists()
        for path in futures_history_parquet_paths(directory).values()
    )


def _history_metadata_json_default(value: object) -> object:
    """Encode non-JSON metadata values with explicit type tags."""
    if isinstance(value, datetime):
        return {"__history_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__history_type__": "date", "value": value.isoformat()}
    if isinstance(value, Path):
        return {"__history_type__": "path", "value": str(value)}
    raise TypeError(f"Unsupported futures-history metadata value: {value!r}")


def _history_metadata_json_hook(value: dict[str, object]) -> object:
    """Restore explicitly tagged metadata values from a cache manifest."""
    value_type = value.get("__history_type__")
    raw_value = value.get("value")
    if value_type == "datetime" and isinstance(raw_value, str):
        return datetime.fromisoformat(raw_value)
    if value_type == "date" and isinstance(raw_value, str):
        return date.fromisoformat(raw_value)
    if value_type == "path" and isinstance(raw_value, str):
        return Path(raw_value)
    return value


def write_futures_history_parquet_cache(
    history: FuturesHistory,
    directory: Path | str,
) -> None:
    """Write one canonical history as an atomic Parquet cache bundle.

    Stream files are replaced before the manifest. A failed or interrupted
    write therefore cannot present a complete new cache to readers.
    """
    paths = futures_history_parquet_paths(directory)
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    paths["manifest"].unlink(missing_ok=True)
    streams = {
        "signal": history.signal,
        "marks": history.marks,
        "carry": history.carry,
        "panama": history.panama,
    }
    for stream, frame in streams.items():
        temporary = paths[stream].with_suffix(".parquet.tmp")
        frame.write_parquet(temporary)
        temporary.replace(paths[stream])

    manifest = pl.DataFrame({
        "cache_schema_version": [FUTURES_HISTORY_CACHE_SCHEMA_VERSION],
        "history_schema_version": [history.schema_version],
        "source": [history.source],
        "instrument_code": [history.instrument_code],
        "metadata_json": [
            json.dumps(
                dict(history.metadata),
                default=_history_metadata_json_default,
                sort_keys=True,
            )
        ],
    })
    temporary_manifest = paths["manifest"].with_suffix(".parquet.tmp")
    manifest.write_parquet(temporary_manifest)
    temporary_manifest.replace(paths["manifest"])


def read_futures_history_parquet_cache(
    directory: Path | str,
) -> FuturesHistory:
    """Read and validate one canonical history Parquet cache bundle."""
    paths = futures_history_parquet_paths(directory)
    if not all(path.exists() for path in paths.values()):
        raise FileNotFoundError(f"Incomplete futures-history cache: {directory}")
    manifest = pl.read_parquet(paths["manifest"])
    if manifest.height != 1:
        raise ValueError("futures-history cache manifest must contain one row")
    row = manifest.row(0, named=True)
    cache_version = int(row["cache_schema_version"])
    if cache_version != FUTURES_HISTORY_CACHE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported futures-history cache schema version: "
            f"{cache_version}"
        )
    metadata = json.loads(
        str(row["metadata_json"]),
        object_hook=_history_metadata_json_hook,
    )
    if not isinstance(metadata, dict):
        raise ValueError("futures-history cache metadata must be an object")
    return FuturesHistory(
        source=str(row["source"]),
        instrument_code=str(row["instrument_code"]),
        schema_version=int(row["history_schema_version"]),
        signal=pl.read_parquet(paths["signal"]),
        marks=pl.read_parquet(paths["marks"]),
        carry=pl.read_parquet(paths["carry"]),
        panama=pl.read_parquet(paths["panama"]),
        metadata=metadata,
    )


def _validate_stream(
    name: str,
    frame: pl.DataFrame,
    required_columns: set[str],
    *,
    allow_empty: bool = False,
) -> None:
    """Validate one dated stream's required columns, rows, and date key."""
    missing = required_columns - set(frame.columns)
    if missing:
        raise ValueError(f"{name} stream is missing columns: {sorted(missing)}")
    if frame.is_empty() and allow_empty:
        return
    if frame.is_empty():
        raise ValueError(f"{name} stream must not be empty")
    if frame.get_column("date").null_count():
        raise ValueError(f"{name} stream contains null dates")
    duplicate_dates = frame.group_by("date").len().filter(pl.col("len") > 1)
    if duplicate_dates.height:
        raise ValueError(f"{name} stream contains duplicate dates")


def _empty_carry_frame() -> pl.DataFrame:
    """Return a typed zero-row carry stream for sources without carry data."""
    return pl.DataFrame(
        schema={
            "date": pl.Date,
            "source_timestamp": pl.Datetime("us"),
            "current_price": pl.Float64,
            "current_contract": pl.String,
            "carry_price": pl.Float64,
            "carry_contract": pl.String,
            "quality_flag": pl.String,
        }
    )


def _empty_panama_frame() -> pl.DataFrame:
    """Return a typed zero-row additive-price stream placeholder."""
    return pl.DataFrame(
        schema={
            "date": pl.Date,
            "source_timestamp": pl.Datetime("us"),
            "panama_price": pl.Float64,
            "point_change": pl.Float64,
            "quality_flag": pl.String,
        }
    )


@dataclass
class PysystemtradeHistoryProvider:
    """Read Carver's imported data and construct a safe daily signal index."""

    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH
    cache_root: Path | str = DEFAULT_FUTURES_CACHE_ROOT
    use_cache: bool = True
    save_cache: bool = True

    def _database_metadata(self) -> tuple[int, str]:
        """Read the sidecar schema version and pinned source commit."""
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
        """Return the schema- and source-versioned cache directory."""
        return (
            Path(self.cache_root)
            / "pysystemtrade"
            / f"v{HISTORY_SCHEMA_VERSION}"
            / source_commit[:12]
        )

    def _cache_paths(self, instrument_code: str, source_commit: str) -> dict[str, Path]:
        """Map each stream name to its instrument-specific Parquet cache."""
        directory = self._cache_directory(source_commit)
        return {
            stream: directory / f"{instrument_code}_{stream}.parquet"
            for stream in ("signal", "marks", "carry", "panama")
        }

    def load(self, instrument_code: str) -> FuturesHistory:
        """Load or build four daily streams for one Carver instrument.

        Cache identity includes the local history schema and upstream source
        commit. The returned :class:`FuturesHistory` owns distinct signal,
        executable-mark, carry, and additive-price dataframes.
        """
        sidecar_version, source_commit = self._database_metadata()
        if sidecar_version != 5:
            raise ValueError(
                f"Unsupported pysystemtrade sidecar schema version: {sidecar_version}"
            )
        cache_paths = self._cache_paths(instrument_code, source_commit)
        cache_hit = self.use_cache and all(
            path.exists() for path in cache_paths.values()
        )

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            config = con.execute(
                """
                SELECT
                    i.description,
                    i.point_size,
                    i.currency,
                    classification.asset_class,
                    classification.economic_subclass,
                    i.region,
                    r.hold_roll_cycle,
                    r.roll_offset_days,
                    r.carry_offset,
                    r.priced_roll_cycle,
                    r.expiry_offset
                FROM raw.instrument_config i
                JOIN ref.instrument_classification classification
                  USING (instrument_code)
                JOIN raw.roll_config r USING (instrument_code)
                WHERE i.instrument_code = ?
                """,
                [instrument_code],
            ).fetchone()
            if config is None:
                raise KeyError(
                    f"Unknown pysystemtrade instrument code: {instrument_code}"
                )

            if cache_hit:
                logger.info(
                    "pysystemtrade_history cache_hit instrument=%s source_commit=%s",
                    instrument_code,
                    source_commit,
                )
                signal = pl.read_parquet(cache_paths["signal"])
                marks = pl.read_parquet(cache_paths["marks"])
                carry = pl.read_parquet(cache_paths["carry"])
                panama = pl.read_parquet(cache_paths["panama"])
            else:
                logger.info(
                    "pysystemtrade_history load instrument=%s source_commit=%s",
                    instrument_code,
                    source_commit,
                )
                signal, marks, carry, panama = self._load_uncached(con, instrument_code)
            if not cache_hit and self.save_cache:
                cache_paths["signal"].parent.mkdir(parents=True, exist_ok=True)
                signal.write_parquet(cache_paths["signal"])
                marks.write_parquet(cache_paths["marks"])
                carry.write_parquet(cache_paths["carry"])
                panama.write_parquet(cache_paths["panama"])
        finally:
            con.close()

        metadata = {
            "description": config[0],
            "point_size": config[1],
            "currency": config[2],
            "asset_class": config[3],
            "economic_subclass": config[4],
            "region": config[5],
            "hold_roll_cycle": config[6],
            "roll_offset_days": config[7],
            "carry_offset": config[8],
            "priced_roll_cycle": config[9],
            "expiry_offset": config[10],
            "source_git_commit": source_commit,
            "sidecar_schema_version": sidecar_version,
            "cache_hit": cache_hit,
        }
        return FuturesHistory(
            source="pysystemtrade",
            instrument_code=instrument_code,
            schema_version=HISTORY_SCHEMA_VERSION,
            signal=signal,
            marks=marks,
            carry=carry,
            metadata=metadata,
            panama=panama,
        )

    @staticmethod
    def _load_uncached(
        con: duckdb.DuckDBPyConnection, instrument_code: str
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """Build signal, mark, carry, and Panama frames from sidecar tables."""
        adjusted = con.execute(
            """
            SELECT
                trade_date AS date,
                source_timestamp,
                adjusted_price
            FROM daily.adjusted_prices
            WHERE instrument_code = ?
            ORDER BY trade_date
            """,
            [instrument_code],
        ).pl()
        raw_roll_inputs = con.execute(
            """
            SELECT
                trade_date AS date,
                source_timestamp,
                price AS current_price,
                price_contract AS current_contract,
                forward AS forward_price,
                forward_contract
            FROM raw.multiple_prices
            WHERE instrument_code = ?
              AND price IS NOT NULL
              AND price_contract IS NOT NULL
            ORDER BY source_timestamp
            """,
            [instrument_code],
        ).pl()
        marks = con.execute(
            """
            SELECT
                trade_date AS date,
                source_timestamp,
                mark_price,
                contract_id
            FROM daily.marks
            WHERE instrument_code = ?
            ORDER BY trade_date
            """,
            [instrument_code],
        ).pl()
        if adjusted.is_empty() or marks.is_empty() or raw_roll_inputs.is_empty():
            raise ValueError(f"No price history for {instrument_code}")

        marks = (
            marks.sort("date")
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

        # ``generated`` is authoritative for roll-neutral returns and Panama
        # increments. The imported adjusted-price level is joined only as a
        # validation reference; it never replaces the locally rebuilt paths.
        generated = select_daily_continuous(build_continuous_futures(raw_roll_inputs))
        signal = generated.signal.join(
            adjusted.select(
                "date",
                pl.col("adjusted_price").alias("source_adjusted_price"),
            ),
            on="date",
            how="left",
        )
        panama = generated.panama.join(
            adjusted.select(
                "date",
                pl.col("adjusted_price").alias("source_adjusted_price"),
            ),
            on="date",
            how="left",
        ).with_columns(
            (pl.col("panama_price") - pl.col("source_adjusted_price"))
            .alias("adjusted_validation_error")
        )

        carry = con.execute(
            """
            SELECT
                trade_date AS date,
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
        return signal, marks, carry, panama


_GLOBEX_ALL_CONTRACTS_SQL = """
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
)
SELECT
    ts_event AS source_timestamp,
    CAST(ts_event AS DATE) AS date,
    open,
    high,
    low,
    close AS mark_price,
    volume,
    instrument_id,
    expiration,
    previous_contract_close
FROM bars
ORDER BY ts_event, expiration
"""


_GLOBEX_VOLUME_FRONT_SQL = """
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
    CAST(b.ts_event AS DATE) AS date,
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


def select_calendar_contract_observations(
    raw: pl.DataFrame,
    policy: ContractRollPolicy,
) -> pl.DataFrame:
    """Select one held contract per session under a calendar roll policy.

    ``raw`` must contain ``date``, ``expiration``, and ``volume`` for
    every available dated contract. The contract calendar is formed before
    daily rows are selected, so a missing held-contract observation remains a
    gap instead of silently substituting another expiry.
    """
    required = {"date", "expiration", "volume"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(
            f"calendar contract input missing columns: {sorted(missing)}"
        )
    if policy.mode != "calendar":
        raise ValueError("calendar contract selection requires a calendar policy")

    all_dates = raw.get_column("date").unique().sort().to_list()
    allowed = (
        raw.filter(
            pl.col("expiration").dt.month().is_in(policy.hold_month_numbers)
        )
        .sort("date", "expiration", "volume")
        .unique(
            subset=["date", "expiration"],
            keep="last",
            maintain_order=True,
        )
    )
    if allowed.is_empty():
        return allowed
    contracts = (
        allowed.group_by("expiration")
        .agg(pl.col("date").min().alias("first_date"))
        .sort("expiration")
        .to_dicts()
    )
    schedule_dates: list[date] = []
    schedule_expirations: list[date] = []
    for session_date in all_dates:
        held_expiration = next(
            (
                row["expiration"]
                for row in contracts
                if row["first_date"] <= session_date
                and session_date
                < row["expiration"] + timedelta(days=policy.roll_offset_days)
            ),
            None,
        )
        if held_expiration is not None:
            schedule_dates.append(session_date)
            schedule_expirations.append(held_expiration)
    schedule = pl.DataFrame({
        "date": pl.Series(schedule_dates, dtype=pl.Date),
        "expiration": pl.Series(schedule_expirations, dtype=pl.Date),
    })
    return schedule.join(
        allowed,
        on=["date", "expiration"],
        how="inner",
    ).sort("date")


def build_contract_history_streams(
    raw: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Transform selected dated-contract rows into canonical history streams.

    Required columns are ``date``, ``source_timestamp``, ``mark_price``,
    ``instrument_id``, ``expiration``, ``volume``, and
    ``previous_contract_close``. The returned tuple contains signal, marks,
    empty carry, and additive continuous-price streams.
    """
    required = {
        "date",
        "source_timestamp",
        "mark_price",
        "instrument_id",
        "expiration",
        "volume",
        "previous_contract_close",
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(
            f"selected contract input missing columns: {sorted(missing)}"
        )
    raw = raw.sort("date").with_columns(
        pl.col("expiration").dt.strftime("%Y%m00").alias("contract_id")
    )
    marks = raw.select(
        "date",
        "source_timestamp",
        "mark_price",
        "contract_id",
        "instrument_id",
        "expiration",
        "volume",
    ).with_columns(
        (pl.col("contract_id") != pl.col("contract_id").shift(1))
        .fill_null(False)
        .alias("is_roll"),
        pl.when(pl.col("mark_price") <= 0)
        .then(pl.lit("nonpositive_mark"))
        .otherwise(pl.lit(""))
        .alias("quality_flag"),
    )

    inputs = raw.select(
        "date",
        "source_timestamp",
        pl.col("mark_price").alias("current_price"),
        pl.col("contract_id").alias("current_contract"),
        "previous_contract_close",
    )
    generated = build_continuous_futures(
        inputs,
        direct_reference_column="previous_contract_close",
    )
    return generated.signal, marks, _empty_carry_frame(), generated.panama


@dataclass
class GlobexHistoryProvider:
    """Build a policy-selected signal stream from paid/raw Globex contracts."""

    db_path: Path | str = field(
        default_factory=lambda: Path(
            os.getenv("GLOBEX_DB_PATH", str(DEFAULT_GLOBEX_DB_PATH))
        ).expanduser()
    )
    cache_root: Path | str = DEFAULT_FUTURES_CACHE_ROOT
    use_cache: bool = True
    save_cache: bool = True
    roll_policy: ContractRollPolicy | None = None

    def _dataset_fingerprint(self) -> str:
        """Fingerprint the read-only Globex database for cache invalidation."""
        stat = Path(self.db_path).stat()
        return f"{stat.st_size}-{stat.st_mtime_ns}"

    def _resolved_roll_policy(self, asset: str) -> ContractRollPolicy:
        """Return an explicit override or the reviewed default for ``asset``."""
        if self.roll_policy is not None:
            return self.roll_policy
        policy, _ = carver_aligned_globex_roll_policy_set().resolve(asset, asset)
        return policy

    def _cache_paths(
        self,
        asset: str,
        policy: ContractRollPolicy,
    ) -> dict[str, Path]:
        """Map stream names to caches keyed by data and roll-policy identity."""
        directory = (
            Path(self.cache_root)
            / "globex"
            / f"v{HISTORY_SCHEMA_VERSION}"
            / self._dataset_fingerprint()
            / policy.cache_key
        )
        return {
            stream: directory / f"{asset}_{stream}.parquet"
            for stream in ("signal", "marks", "carry", "panama")
        }

    def load(self, instrument_code: str) -> FuturesHistory:
        """Load one Globex asset under its resolved held-contract policy.

        The raw selected-contract observations are transformed into separate
        signal, mark, carry, and Panama frames. Cache keys include both the
        database fingerprint and roll policy so policy experiments cannot
        silently reuse a different held path.
        """
        policy = self._resolved_roll_policy(instrument_code)
        cache_paths = self._cache_paths(instrument_code, policy)
        if self.use_cache and all(path.exists() for path in cache_paths.values()):
            logger.info(
                "globex_history cache_hit asset=%s roll_policy=%s",
                instrument_code,
                policy.policy_id,
            )
            signal = pl.read_parquet(cache_paths["signal"])
            marks = pl.read_parquet(cache_paths["marks"])
            carry = pl.read_parquet(cache_paths["carry"])
            panama = pl.read_parquet(cache_paths["panama"])
        else:
            logger.info(
                "globex_history load asset=%s roll_policy=%s hold_cycle=%s "
                "roll_offset_days=%s",
                instrument_code,
                policy.policy_id,
                policy.hold_roll_cycle or None,
                policy.roll_offset_days if policy.mode == "calendar" else None,
            )
            con = duckdb.connect(str(self.db_path), read_only=True)
            try:
                query = (
                    _GLOBEX_VOLUME_FRONT_SQL
                    if policy.mode == "volume_front"
                    else _GLOBEX_ALL_CONTRACTS_SQL
                )
                raw = con.execute(query, [instrument_code]).pl()
            finally:
                con.close()
            if raw.is_empty():
                raise KeyError(f"Unknown or empty Globex futures asset: {instrument_code}")
            if policy.mode == "calendar":
                raw = select_calendar_contract_observations(raw, policy)
                if raw.is_empty():
                    raise ValueError(
                        f"Globex roll policy {policy.policy_id} selected no "
                        f"contract observations for {instrument_code}"
                    )
            signal, marks, carry, panama = build_contract_history_streams(raw)
        cache_hit = self.use_cache and all(
            path.exists() for path in cache_paths.values()
        )
        if not cache_hit and self.save_cache:
            cache_paths["signal"].parent.mkdir(parents=True, exist_ok=True)
            signal.write_parquet(cache_paths["signal"])
            marks.write_parquet(cache_paths["marks"])
            carry.write_parquet(cache_paths["carry"])
            panama.write_parquet(cache_paths["panama"])

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
            panama=panama,
            metadata={
                "multiplier": spec.get("multiplier"),
                "exchange": spec.get("exchange"),
                "active_months": spec.get("active_months"),
                "dataset_fingerprint": self._dataset_fingerprint(),
                "roll_policy": policy.as_dict(),
                "selected_rolls": int(marks.get_column("is_roll").sum()),
            },
        )

@dataclass
class HybridHistoryProvider:
    """Causally splice a long-history provider into a primary provider.

    The primary source owns ``handoff_date`` and all later sessions.  Earlier
    observations come from ``historical``.  Return indices and additive
    continuous levels are rebuilt from their respective daily increments, so
    vendor level bases are never concatenated.
    """

    historical: FuturesHistoryProvider
    primary: FuturesHistoryProvider
    historical_instrument_map: Mapping[str, str] = field(default_factory=dict)
    handoff_date: Optional[date] = None
    historical_date_alignment: str = ""
    historical_date_alignment_through: Optional[date] = None

    def load(self, instrument_code: str) -> FuturesHistory:
        """Splice historical and primary streams at a causal handoff date.

        ``instrument_code`` identifies the primary source; the optional map
        resolves its historical identifier. Levels are reconstructed from
        daily increments rather than concatenated across vendor bases.
        """
        historical_code = self.historical_instrument_map.get(
            instrument_code, instrument_code
        )
        old = self.historical.load(historical_code)
        new = self.primary.load(instrument_code)
        if self.historical_date_alignment:
            if self.historical_date_alignment != "previous_primary_session":
                raise ValueError(
                    f"unsupported historical date alignment: {self.historical_date_alignment}"
                )
            if self.historical_date_alignment_through is None:
                raise ValueError("historical date alignment requires an end date")
            old = align_history_to_previous_sessions(
                old,
                new.signal.get_column("date").to_list(),
                through=self.historical_date_alignment_through,
            )
        primary_start = new.signal.get_column("date").min()
        historical_end = old.signal.get_column("date").max()
        if primary_start is None or historical_end is None:
            raise ValueError(f"hybrid source is empty for {instrument_code}")
        if historical_end < primary_start:
            raise ValueError(
                f"hybrid sources do not overlap for {instrument_code}: "
                f"historical_end={historical_end} primary_start={primary_start}"
            )
        first_primary_return = (
            new.signal.filter(pl.col("return_valid"))
            .get_column("date")
            .min()
        )
        handoff = self.handoff_date or first_primary_return
        if handoff is None:
            raise ValueError(f"primary history has no valid return for {instrument_code}")
        logger.info(
            "hybrid_history instrument=%s historical_source=%s historical_instrument=%s "
            "primary_source=%s handoff_date=%s",
            instrument_code,
            old.source,
            historical_code,
            new.source,
            handoff,
        )

        # Stream ownership changes exactly once: historical owns dates before
        # handoff and primary owns handoff onward. Concatenate daily economic
        # increments first, then rebuild the arbitrary index level below.
        old_signal = old.signal.filter(pl.col("date") < handoff)
        new_signal = new.signal.filter(pl.col("date") >= handoff)
        signal = pl.concat(
            [
                _select_hybrid_signal(old_signal).with_columns(
                    pl.lit(old.source).alias("source_segment")
                ),
                _select_hybrid_signal(new_signal).with_columns(
                    pl.lit(new.source).alias("source_segment")
                ),
            ],
            how="vertical",
        ).sort("date")
        # Vendor signal indices have unrelated bases. Rebase the spliced return
        # path to 100 so the handoff cannot create a false level jump.
        signal = signal.with_columns(
            (
                (1.0 + pl.col("ret_1d").fill_null(0.0)).cum_prod()
                * 100.0
            ).alias("signal_index")
        )

        # Panama levels are vendor-specific, but their point changes carry the
        # roll-neutral increments. Rebuild those increments backward from the
        # latest raw signal price, matching Carver's convention that the most
        # recent contract remains unadjusted while later roll differentials
        # accumulate into earlier prices.
        old_panama = old.panama.filter(pl.col("date") < handoff)
        new_panama = new.panama.filter(pl.col("date") >= handoff)
        increments = pl.concat(
            [
                old_panama.select(
                    "date", "source_timestamp", "point_change",
                    "contract_id", "is_roll", "quality_flag",
                ).with_columns(pl.lit(old.source).alias("source_segment")),
                new_panama.select(
                    "date", "source_timestamp", "point_change",
                    "contract_id", "is_roll", "quality_flag",
                ).with_columns(pl.lit(new.source).alias("source_segment")),
            ],
            how="vertical",
        ).sort("date")
        latest_signal_price = signal.get_column("current_price")[-1]
        total_point_change = increments.get_column("point_change").sum()
        if latest_signal_price is None or total_point_change is None:
            raise ValueError(
                f"hybrid Panama stream has no terminal price for {instrument_code}"
            )
        panama_start = float(latest_signal_price) - float(total_point_change)
        panama = increments.with_columns(
            (
                pl.lit(panama_start)
                + pl.col("point_change").fill_null(0.0).cum_sum()
            ).alias("panama_price")
        )

        # Marks retain actual source levels and contract identity because they
        # drive P&L and roll accounting; unlike signal indices, they are not
        # normalized across the handoff.
        marks = pl.concat(
            [
                old.marks.filter(pl.col("date") < handoff),
                new.marks.filter(pl.col("date") >= handoff),
            ],
            how="diagonal_relaxed",
        ).sort("date").with_columns(
            (pl.col("contract_id") != pl.col("contract_id").shift(1))
            .fill_null(False)
            .alias("is_roll")
        )
        carry = pl.concat(
            [
                old.carry.filter(pl.col("date") < handoff),
                new.carry.filter(pl.col("date") >= handoff),
            ],
            how="diagonal_relaxed",
        ).sort("date")
        metadata = {
            "historical_source": old.source,
            "historical_instrument": historical_code,
            "primary_source": new.source,
            "primary_instrument": instrument_code,
            "handoff_date": handoff,
            "historical_date_alignment": self.historical_date_alignment or None,
            "historical_date_alignment_through": (
                self.historical_date_alignment_through
            ),
            "historical_metadata": dict(old.metadata),
            "primary_metadata": dict(new.metadata),
            "pre_handoff_pnl_quality": "research_approximation",
            "post_handoff_pnl_quality": "primary_contract_marks",
        }
        return FuturesHistory(
            source="hybrid",
            instrument_code=instrument_code,
            schema_version=HISTORY_SCHEMA_VERSION,
            signal=signal,
            marks=marks,
            carry=carry,
            metadata=metadata,
            panama=panama,
        )


def _select_hybrid_signal(signal: pl.DataFrame) -> pl.DataFrame:
    """Select hybrid signal columns while normalizing legacy reference names.

    Older cached Carver histories may store the previous-price field as
    ``reference_price`` while current generated histories call it
    ``ref_price``. The splice needs one stable internal schema before vertical
    concatenation; both fields represent the prior reference price in native
    price points.
    """
    reference_column = next(
        (
            name
            for name in ("ref_price", "reference_price")
            if name in signal.columns
        ),
        None,
    )
    if reference_column is None:
        raise ValueError(
            "hybrid signal is missing ref_price/reference_price column"
        )
    return signal.select(
        "date",
        "source_timestamp",
        "current_price",
        "contract_id",
        pl.col(reference_column).alias("ref_price"),
        "point_change",
        "ret_1d",
        "is_roll",
        "return_valid",
        "quality_flag",
    )


def stitch_futures_histories(
    histories: Sequence[FuturesHistory],
    *,
    instrument_code: str,
    max_source_gap_days: int = 0,
) -> FuturesHistory:
    """Stitch ordered history sources into one duplicate-free daily history.

    ``histories`` must be ordered from the oldest fallback to the newest
    preferred source. Each newer source takes ownership from its first valid
    return onward. Source-specific index and additive-price levels are
    discarded; their daily increments are concatenated and rebuilt into one
    path. Marks retain their actual prices and contract identities.

    Parameters
    ----------
    histories
        Two or more loaded histories ordered by increasing source priority.
    instrument_code
        Identifier assigned to the combined history.
    max_source_gap_days
        Maximum calendar gap allowed between adjacent sources. Zero requires
        overlap. When a positive gap is allowed, the unknown return across the
        gap is omitted rather than inferred, and the gap is recorded in
        metadata.

    Returns
    -------
    FuturesHistory
        Daily signal, mark, carry, and additive-price streams with one row per
        date and a ``source_segment`` column identifying row ownership.
    """
    ordered = list(histories)
    if len(ordered) < 2:
        raise ValueError("history stitching requires at least two sources")
    if max_source_gap_days < 0:
        raise ValueError("max_source_gap_days must be nonnegative")

    handoffs: list[date] = []
    source_gaps: list[dict[str, object]] = []
    for older, newer in zip(ordered, ordered[1:]):
        source_gap: dict[str, object] | None = None
        older_end = older.signal.get_column("date").max()
        newer_start = newer.signal.get_column("date").min()
        if older_end is None or newer_start is None:
            raise ValueError("history stitching cannot use an empty source")
        if older_end < newer_start:
            gap_days = (newer_start - older_end).days
            if gap_days > max_source_gap_days:
                raise ValueError(
                    "history sources do not overlap: "
                    f"older_source={older.source} older_end={older_end} "
                    f"newer_source={newer.source} newer_start={newer_start} "
                    f"gap_days={gap_days} "
                    f"max_source_gap_days={max_source_gap_days}"
                )
            source_gap = {
                "older_source": older.source,
                "newer_source": newer.source,
                "older_end": older_end,
                "newer_start": newer_start,
                "gap_days": gap_days,
            }
            logger.warning(
                "stitched_history source_gap instrument=%s older_source=%s "
                "older_end=%s newer_source=%s newer_start=%s gap_days=%s "
                "missing_return=omitted",
                instrument_code,
                older.source,
                older_end,
                newer.source,
                newer_start,
                gap_days,
            )
        first_valid = (
            newer.signal.filter(pl.col("return_valid"))
            .get_column("date")
            .min()
        )
        if first_valid is None:
            raise ValueError(
                f"history source {newer.source} has no valid return"
            )
        handoffs.append(first_valid)
        if source_gap is not None:
            # The initial newer-source observation establishes the reference
            # price for its first valid daily change. Attach the gap audit to
            # that first retained valid row, which is the first row visible to
            # downstream forecast and cash calculations.
            source_gap["handoff_date"] = first_valid
            source_gaps.append(source_gap)

    def owned_rows(frame: pl.DataFrame, index: int) -> pl.DataFrame:
        """Select the date interval owned by one priority-ordered source."""
        expression = pl.lit(True)
        if index > 0:
            expression &= pl.col("date") >= handoffs[index - 1]
        if index < len(handoffs):
            expression &= pl.col("date") < handoffs[index]
        return frame.filter(expression)

    gap_events = pl.DataFrame(
        [
            {
                "date": gap["handoff_date"],
                "source_gap_before": True,
                "source_gap_days": gap["gap_days"],
                "gap_pnl_omitted": True,
            }
            for gap in source_gaps
        ],
        schema={
            "date": pl.Date,
            "source_gap_before": pl.Boolean,
            "source_gap_days": pl.Int64,
            "gap_pnl_omitted": pl.Boolean,
        },
    )
    signal = (
        pl.concat(
            [
                _select_hybrid_signal(owned_rows(history.signal, index))
                .with_columns(pl.lit(history.source).alias("source_segment"))
                for index, history in enumerate(ordered)
            ],
            how="vertical",
        )
        .sort("date", "source_timestamp")
        .unique(subset="date", keep="last", maintain_order=True)
        .with_columns(
            (
                (1.0 + pl.col("ret_1d").fill_null(0.0)).cum_prod()
                * 100.0
            ).alias("signal_index")
        )
        .join(gap_events, on="date", how="left")
        .with_columns(
            pl.col("source_gap_before").fill_null(False),
            pl.col("gap_pnl_omitted").fill_null(False),
        )
    )

    increments = (
        pl.concat(
            [
                owned_rows(history.panama, index)
                .select(
                    "date",
                    "source_timestamp",
                    "point_change",
                    "contract_id",
                    "is_roll",
                    "quality_flag",
                )
                .with_columns(pl.lit(history.source).alias("source_segment"))
                for index, history in enumerate(ordered)
            ],
            how="vertical",
        )
        .sort("date", "source_timestamp")
        .unique(subset="date", keep="last", maintain_order=True)
    )
    latest_price = signal.get_column("current_price")[-1]
    if latest_price is None:
        raise ValueError("stitched history has no terminal contract price")
    total_point_change = float(
        increments.get_column("point_change").drop_nulls().sum()
    )
    panama = increments.with_columns(
        (
            pl.lit(float(latest_price) - total_point_change)
            + pl.col("point_change").fill_null(0.0).cum_sum()
        ).alias("panama_price")
    )

    marks = (
        pl.concat(
            [
                owned_rows(history.marks, index).with_columns(
                    pl.lit(history.source).alias("source_segment")
                )
                for index, history in enumerate(ordered)
            ],
            how="diagonal_relaxed",
        )
        .sort("date", "source_timestamp")
        .unique(subset="date", keep="last", maintain_order=True)
        .with_columns(
            (pl.col("contract_id") != pl.col("contract_id").shift(1))
            .fill_null(False)
            .alias("is_roll")
        )
    )
    carry = (
        pl.concat(
            [
                owned_rows(history.carry, index).with_columns(
                    pl.lit(history.source).alias("source_segment")
                )
                for index, history in enumerate(ordered)
            ],
            how="diagonal_relaxed",
        )
        .sort("date", "source_timestamp")
        .unique(subset="date", keep="last", maintain_order=True)
    )

    logger.info(
        "stitched_history instrument=%s sources=%s handoffs=%s rows=%d",
        instrument_code,
        [history.source for history in ordered],
        handoffs,
        signal.height,
    )
    return FuturesHistory(
        source="stitched",
        instrument_code=instrument_code,
        schema_version=max(history.schema_version for history in ordered),
        signal=signal,
        marks=marks,
        carry=carry,
        panama=panama,
        metadata={
            "source_priority": [history.source for history in ordered],
            "handoff_dates": handoffs,
            "source_instruments": [
                history.instrument_code for history in ordered
            ],
            "source_metadata": [dict(history.metadata) for history in ordered],
            "source_gaps": source_gaps,
        },
    )


def align_history_to_previous_sessions(
    history: FuturesHistory,
    primary_dates: list[date],
    *,
    through: date,
) -> FuturesHistory:
    """Map legacy dates to the preceding actual primary trading session."""
    sessions = sorted(set(primary_dates))
    if not sessions:
        raise ValueError("primary session calendar is empty")
    date_map: dict[date, date] = {}
    for value in history.signal.get_column("date").to_list():
        if value > through:
            continue
        position = bisect.bisect_left(sessions, value) - 1
        if position >= 0:
            date_map[value] = sessions[position]

    def remap(frame: pl.DataFrame) -> pl.DataFrame:
        """Apply the legacy-to-primary session map and retain one row/date."""
        if frame.is_empty():
            return frame
        return (
            frame.with_columns(
                pl.col("date")
                .replace_strict(date_map, default=pl.col("date"))
                .alias("date")
            )
            .sort("date", "source_timestamp")
            .unique(subset="date", keep="last", maintain_order=True)
        )

    signal = remap(history.signal).sort("date").with_columns(
        pl.col("signal_index").pct_change().alias("ret_1d"),
        (pl.col("contract_id") != pl.col("contract_id").shift(1))
        .fill_null(False)
        .alias("is_roll"),
    ).with_columns(
        pl.when(pl.col("ret_1d").is_not_null())
        .then(pl.col("current_price") / (1.0 + pl.col("ret_1d")))
        .otherwise(None)
        .alias("ref_price"),
        pl.col("ret_1d").is_not_null().alias("return_valid"),
    )
    panama = remap(history.panama).sort("date").with_columns(
        pl.col("panama_price").diff().alias("point_change")
    )
    marks = remap(history.marks).sort("date").with_columns(
        (pl.col("contract_id") != pl.col("contract_id").shift(1))
        .fill_null(False)
        .alias("is_roll")
    )
    metadata = dict(history.metadata)
    metadata.update({
        "date_alignment": "previous_primary_session",
        "date_alignment_through": through,
    })
    return FuturesHistory(
        source=history.source,
        instrument_code=history.instrument_code,
        schema_version=history.schema_version,
        signal=signal,
        marks=marks,
        carry=remap(history.carry),
        metadata=metadata,
        panama=panama,
    )

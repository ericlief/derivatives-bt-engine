from __future__ import annotations
import os
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Optional

import polars as pl

from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Infrastructure ──────────────────────────────────────────────────
_PROCESSED_SUBDIR = "processed"
# When vix_file points at a directory (rather than a specific file), the raw
# CSV is expected at {dir}/processed/{this filename} -- matches the current
# on-disk convention, not something auto-detected.
_VIX_FILENAME_IN_PROCESSED_DIR = "vix.csv"


class BaseDataLoader(ABC):
    """
    Shared lazy-load/parquet-cache behavior and VIX loading for asset-specific
    data loaders (options chain, futures OHLCV, ...).

    Subclasses must declare `data_dir`, `vix_file`, `use_preprocessed`, and
    `save_preprocessed` attributes (e.g. as dataclass fields) and implement
    `load_data()`.
    """

    data_dir: Optional[str]
    vix_file: Optional[str]
    use_preprocessed: bool
    save_preprocessed: bool

    @abstractmethod
    def load_data(self) -> dict:
        """Load and return all data needed for a backtest as a dict."""
        ...

    @staticmethod
    def _resolve_source_paths(data_dir: Optional[str], filename_or_path: str, filename_in_processed_dir: str) -> tuple[str, str]:
        """Resolve a configured source (vix_file) to (raw_path,
        processed_path). filename_or_path may be:

        - a bare filename: joined to data_dir; the parquet cache uses the
          same filename stem, in data_dir.
        - an absolute path to a specific raw file: the parquet cache uses the
          same stem, alongside it.
        - an absolute path to a directory: the raw CSV is expected at
          {dir}/processed/{filename_in_processed_dir}, and the parquet cache
          is the same filename with a .parquet extension, in that same
          processed/ directory.

        filename_or_path and data_dir may each use a leading '~' (expanded
        here) -- os.path.isabs('~/x') is False, so an unexpanded '~/x' would
        otherwise be (wrongly) treated as relative and joined onto data_dir.
        data_dir itself may be None if filename_or_path is always absolute.
        """
        data_dir = os.path.expanduser(data_dir) if data_dir else ""
        filename_or_path = os.path.expanduser(filename_or_path)
        resolved = filename_or_path if os.path.isabs(filename_or_path) else os.path.join(data_dir, filename_or_path)

        if os.path.isdir(resolved):
            processed_dir = os.path.join(resolved, _PROCESSED_SUBDIR)
            raw_path = os.path.join(processed_dir, filename_in_processed_dir)
            stem = os.path.splitext(filename_in_processed_dir)[0]
            return raw_path, os.path.join(processed_dir, f"{stem}.parquet")

        # A specific file (or bare filename already joined to data_dir above)
        # -- parquet cache uses the same stem, alongside it.
        stem = os.path.splitext(os.path.basename(resolved))[0]
        return resolved, os.path.join(os.path.dirname(resolved), f"{stem}.parquet")

    @cached_property
    def _vix_paths(self) -> tuple[str, str]:
        return self._resolve_source_paths(self.data_dir, self.vix_file or "vix.csv", _VIX_FILENAME_IN_PROCESSED_DIR)

    @property
    def _vix_raw_path(self) -> str:
        return self._vix_paths[0]

    @property
    def _vix_processed_path(self) -> str:
        return self._vix_paths[1]

    @staticmethod
    def _read_raw_source(path: str) -> pl.DataFrame:
        """Read a raw CSV or parquet source. Historical raw chain/underlying/
        VIX CSVs use a blank header for the leading date column (equivalent
        to pandas' index_col=0); normalize whatever that first column is
        named (blank, 'Date', 'date', ...) to a lowercase `date` column, and
        lowercase every other column too (some raw sources mix case, e.g.
        'Date,open,high,Low,close'). Shared by every BaseDataLoader subclass
        plus vix_data below."""
        if path.endswith('.parquet'):
            df = pl.read_parquet(path)
        else:
            df = pl.read_csv(path, infer_schema_length=10000)

        first_col = df.columns[0]
        df = df.rename({c: c.lower() for c in df.columns if c != first_col})
        df = df.rename({first_col: 'date'})

        if df['date'].dtype != pl.Date:
            # .cast(pl.Datetime) only reinterprets numeric epoch-like values,
            # not date strings -- it silently nulls every row of a genuine
            # "YYYY-MM-DD" string column. .str.to_datetime() actually parses it.
            df = df.with_columns(pl.col('date').str.to_datetime(strict=False).dt.date().alias('date'))

        return df

    @cached_property
    def vix_data(self) -> pl.DataFrame:
        """Lazy load and cache historical VIX data (shared across loaders) as
        a polars DataFrame with a `date` column."""
        if self.use_preprocessed and os.path.exists(self._vix_processed_path):
            logger.info(f"Loading {self._vix_processed_path}")
            return pl.read_parquet(self._vix_processed_path)

        raw_vix = self._read_raw_source(self._vix_raw_path)
        processed_data = self._preprocess_vix_data(raw_vix)

        if self.save_preprocessed:
            os.makedirs(os.path.dirname(self._vix_processed_path), exist_ok=True)
            processed_data.write_parquet(self._vix_processed_path)
            logger.info(f"Saved data to {self._vix_processed_path}")

        return processed_data

    def _preprocess_vix_data(self, vix_data: pl.DataFrame) -> pl.DataFrame:
        """Clean and preprocess VIX data."""
        logger.info("Preprocessing VIX data...")

        numeric_casts = [pl.col(c).cast(pl.Float64, strict=False) for c in ('open', 'high', 'low', 'close') if c in vix_data.columns]
        df = vix_data.with_columns(numeric_casts) if numeric_casts else vix_data

        return df.sort('date')

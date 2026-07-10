from __future__ import annotations
import os
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Optional

import pandas as pd

from options_bt.utils.logger import setup_logger

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

    @cached_property
    def vix_data(self) -> pd.DataFrame:
        """Lazy load and cache historical VIX data (shared across loaders)."""
        if self.use_preprocessed and os.path.exists(self._vix_processed_path):
            logger.info(f"Loading {self._vix_processed_path}")
            return pd.read_parquet(self._vix_processed_path)

        raw_path = self._vix_raw_path
        if raw_path.endswith('.parquet'):
            raw_vix = pd.read_parquet(raw_path)
            raw_vix = raw_vix.set_index(raw_vix.columns[0])
        else:
            raw_vix = pd.read_csv(raw_path, index_col=0, parse_dates=True)
        processed_data = self._preprocess_vix_data(raw_vix)

        if self.save_preprocessed:
            os.makedirs(os.path.dirname(self._vix_processed_path), exist_ok=True)
            processed_data.to_parquet(self._vix_processed_path)
            logger.info(f"Saved data to {self._vix_processed_path}")

        return processed_data

    def _preprocess_vix_data(self, vix_data: pd.DataFrame) -> pd.DataFrame:
        """Clean and preprocess VIX data."""
        logger.info("Preprocessing VIX data...")

        df = vix_data.copy()

        try:
            df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
        except Exception as e:
            logger.info(f"Index normalization skipped: {e}")

        numeric_cols = ['open', 'high', 'low', 'close']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df.sort_index(inplace=True)

        return df

from __future__ import annotations
import os
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Optional

import pandas as pd

from options_bt.utils.logger import setup_logger

logger = setup_logger()


class BaseDataLoader(ABC):
    """
    Shared lazy-load/pickle-cache behavior and VIX loading for asset-specific
    data loaders (options chain, futures OHLCV, ...).

    Subclasses must declare `data_dir`, `vix_file`, `use_preprocessed`, and
    `save_preprocessed` attributes (e.g. as dataclass fields) and implement
    `load_data()`.
    """

    data_dir: str
    vix_file: Optional[str]
    use_preprocessed: bool
    save_preprocessed: bool

    @abstractmethod
    def load_data(self) -> dict:
        """Load and return all data needed for a backtest as a dict."""
        ...

    @property
    def _vix_raw_path(self) -> str:
        return os.path.join(self.data_dir, self.vix_file or "vix.csv")

    @property
    def _vix_processed_path(self) -> str:
        return os.path.join(self.data_dir, "vix.pkl")

    @cached_property
    def vix_data(self) -> pd.DataFrame:
        """Lazy load and cache historical VIX data (shared across loaders)."""
        if self.use_preprocessed:
            data = self._load_pickle(self._vix_processed_path)
            if data is not None:
                return data

        raw_vix = pd.read_csv(self._vix_raw_path, index_col=0, parse_dates=True)
        processed_data = self._preprocess_vix_data(raw_vix)

        if self.save_preprocessed:
            self._save_pickle(processed_data, self._vix_processed_path)

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

    def _load_pickle(self, file_path: str) -> Optional[pd.DataFrame]:
        """Load a pickle file and return a pandas DataFrame."""
        if os.path.exists(file_path):
            logger.info(f"Loading {file_path}")
            return pd.read_pickle(file_path)
        else:
            logger.info(f"File {file_path} does not exist")
            return None

    def _save_pickle(self, data: pd.DataFrame, file_path: str):
        """Save a pandas DataFrame to a pickle file."""
        try:
            data.to_pickle(file_path)
            logger.info(f"Saved data to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save data to {file_path}: {str(e)}")

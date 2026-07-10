from __future__ import annotations
from dataclasses import dataclass
import os
import time
from typing import Optional, Dict
from functools import cached_property

import polars as pl
import pandas as pd

from options_bt.domain.base_dataloader import BaseDataLoader
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────
_CHAIN_NUMERIC_COLUMNS = ['strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'p_delta', 'c_delta', 'underlying_last']
_CHAIN_BID_ASK_COLUMNS = ['p_bid', 'p_ask', 'c_bid', 'c_ask']
_CHAIN_DATE_COLUMNS = ['quote_readtime', 'trade_date']
_UNDERLYING_NUMERIC_COLUMNS = ['open', 'high', 'low', 'close']

# ── Infrastructure ──────────────────────────────────────────────────
_DEFAULT_UNDERLYING_FILENAME = "spx.csv"
_CHAIN_MULTI_INDEX_CACHE_FILENAME = "chain_multi_index.parquet"
_PROCESSED_SUBDIR = "processed"
# When options_file/spx_file point at a directory (rather than a specific
# file), the raw CSV is expected at {dir}/processed/{this filename} -- these
# match the current on-disk convention, not something auto-detected.
_CHAIN_FILENAME_IN_PROCESSED_DIR = "spx_chain_eod.csv"
_UNDERLYING_FILENAME_IN_PROCESSED_DIR = "spx_eod_preproc.csv"


@dataclass
class OptionsDataLoader(BaseDataLoader):
    data_dir: str
    options_file: str
    use_preprocessed: bool = True
    save_preprocessed: bool = True
    spx_file: Optional[str] = None
    vix_file: Optional[str] = None

    @staticmethod
    def _resolve_source_paths(data_dir: str, filename_or_path: str, filename_in_processed_dir: str) -> tuple[str, str]:
        """Resolve a configured source (options_file/spx_file/vix_file) to
        (raw_path, processed_path). filename_or_path may be:

        - a bare filename: joined to data_dir; the parquet cache uses the
          same filename stem, in data_dir.
        - an absolute path to a specific raw file: the parquet cache uses the
          same stem, alongside it.
        - an absolute path to a directory: the raw CSV is expected at
          {dir}/processed/{filename_in_processed_dir}, and the parquet cache
          is the same filename with a .parquet extension, in that same
          processed/ directory.
        """
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
    def _option_chain_paths(self) -> tuple[str, str]:
        return self._resolve_source_paths(self.data_dir, self.options_file, _CHAIN_FILENAME_IN_PROCESSED_DIR)

    @property
    def _option_chain_raw_path(self) -> str:
        return self._option_chain_paths[0]

    @property
    def _option_chain_processed_path(self) -> str:
        return self._option_chain_paths[1]

    @property
    def _chain_multi_index_processed_path(self) -> str:
        return os.path.join(os.path.dirname(self._option_chain_processed_path), _CHAIN_MULTI_INDEX_CACHE_FILENAME)

    @cached_property
    def _underlying_paths(self) -> tuple[str, str]:
        # NOTE: previously hardcoded to 'spx.csv' regardless of the spx_file
        # field, so spx_file silently did nothing -- now actually respected.
        return self._resolve_source_paths(self.data_dir, self.spx_file or _DEFAULT_UNDERLYING_FILENAME, _UNDERLYING_FILENAME_IN_PROCESSED_DIR)

    @property
    def _underlying_raw_path(self) -> str:
        return self._underlying_paths[0]

    @property
    def _underlying_processed_path(self) -> str:
        return self._underlying_paths[1]

    @staticmethod
    def _read_raw_source(path: str) -> pl.DataFrame:
        """Read a raw CSV or parquet source. Historical raw chain/underlying
        CSVs use a blank header for the leading date column (equivalent to
        pandas' index_col=0); normalize whatever that first column is named
        (blank, 'Date', 'date', ...) to a lowercase `date` column, and
        lowercase every other column too (the real underlying source mixes
        case, e.g. 'Date,open,high,Low,close')."""
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
    def option_chain(self) -> pl.DataFrame:
        """Lazy load and cache the options chain data as a polars DataFrame
        with a `date` column."""
        if self.use_preprocessed and os.path.exists(self._option_chain_processed_path):
            logger.info(f"Loading {self._option_chain_processed_path}")
            return pl.read_parquet(self._option_chain_processed_path)

        raw_options = self._read_raw_source(self._option_chain_raw_path)
        processed_data = self._preprocess_option_chain(raw_options)

        if self.save_preprocessed:
            os.makedirs(os.path.dirname(self._option_chain_processed_path), exist_ok=True)
            processed_data.write_parquet(self._option_chain_processed_path)
            logger.info(f"Saved data to {self._option_chain_processed_path}")

        return processed_data

    @cached_property
    def option_chain_multi_index(self) -> pl.DataFrame:
        """Options chain sorted by (date, strike). polars has no index
        concept, so this is just the chain pre-sorted for the (date, strike)
        lookups that used a pandas MultiIndex before the polars migration."""
        if self.use_preprocessed and os.path.exists(self._chain_multi_index_processed_path):
            logger.info(f"Loading {self._chain_multi_index_processed_path}")
            return pl.read_parquet(self._chain_multi_index_processed_path)

        multi_index = self.option_chain.sort(['date', 'strike'])

        if self.save_preprocessed:
            os.makedirs(os.path.dirname(self._chain_multi_index_processed_path), exist_ok=True)
            multi_index.write_parquet(self._chain_multi_index_processed_path)
            logger.info(f"Saved data to {self._chain_multi_index_processed_path}")

        return multi_index

    @cached_property
    def underlying_data(self) -> pl.DataFrame:
        """Lazy load and cache the SPX underlying data as a polars DataFrame
        with a `date` column."""
        if self.use_preprocessed and os.path.exists(self._underlying_processed_path):
            logger.info(f"Loading {self._underlying_processed_path}")
            return pl.read_parquet(self._underlying_processed_path)

        raw_underlying = self._read_raw_source(self._underlying_raw_path)
        processed_data = self._preprocess_underlying(raw_underlying)

        if self.save_preprocessed:
            os.makedirs(os.path.dirname(self._underlying_processed_path), exist_ok=True)
            processed_data.write_parquet(self._underlying_processed_path)
            logger.info(f"Saved data to {self._underlying_processed_path}")

        return processed_data

    def load_data(self) -> Dict:
        """
        Load and return all data at once.

        Returns pandas DataFrames (option_chain/option_chain_multi_index with
        an *unnamed* DatetimeIndex, matching the pre-migration shape exactly --
        several downstream methods, e.g. OptionSignalGenerator's spread-pairing
        methods, rely on reset_index() producing a literal 'index' column,
        which only happens when the index has no name) since the signal
        generator / position / trade manager / backtester option paths are
        still pandas-based. This to_pandas() conversion is the single scoped
        boundary (per CLAUDE.md's pandas/polars convention); loading and
        preprocessing above it are fully polars already, and this boundary
        should be deleted once those downstream consumers are migrated too.
        """
        data_loading_start = time.time()

        try:
            option_chain = self.option_chain
            option_chain_multi_index = self.option_chain_multi_index
            underlying = self.underlying_data
            vix = self.vix_data

            logger.info(f"Loaded and preprocessed data:")
            logger.info(f"- Normal options chain: {len(option_chain)} rows")
            logger.info(f"- MultiIndex options chain: {len(option_chain_multi_index)} rows")
            logger.info(f"- Underlying data: {len(underlying)} rows")
            logger.info(f"- VIX data: {len(vix)} rows")

            data_loading_time = time.time() - data_loading_start
            logger.info(f"Data loading completed in {data_loading_time:.2f} seconds")

            option_chain_pd = option_chain.to_pandas().set_index('date')
            option_chain_pd.index.name = None
            option_chain_multi_index_pd = option_chain_multi_index.to_pandas().set_index(['date', 'strike'])
            underlying_pd = underlying.to_pandas().set_index('date')
            underlying_pd.index.name = None

            self._check_data_quality(option_chain_pd, underlying_pd, vix)

            return {
                'option_chain': option_chain_pd,
                'option_chain_multi_index': option_chain_multi_index_pd,
                'underlying': underlying_pd,
                'vix': vix,
            }

        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            raise

    def _preprocess_option_chain(self, option_chain: pl.DataFrame) -> pl.DataFrame:
        """
        Clean and preprocess options data to catch and fix common issues.

        Args:
            option_chain: Raw options chain DataFrame

        Returns:
            Cleaned options chain DataFrame
        """
        logger.info("Preprocessing options chain data...")

        df = option_chain

        # Normalize expire_date: coerce to Date (invalid -> null), drop rows
        # with a missing expire_date. A polars Date has no time component,
        # so no separate "normalize" step is needed once cast.
        if 'expire_date' in df.columns:
            if df['expire_date'].dtype != pl.Date:
                # .cast(pl.Datetime) only reinterprets numeric epoch-like
                # values, not date strings -- it silently nulls every row of
                # a genuine "YYYY-MM-DD" string column. .str.to_datetime()
                # actually parses it.
                df = df.with_columns(
                    pl.col('expire_date').str.to_datetime(strict=False).dt.date().alias('expire_date')
                )
            rows_before = df.height
            df = df.filter(pl.col('expire_date').is_not_null())
            rows_dropped = rows_before - df.height
            if rows_dropped > 0:
                logger.info(f"Dropped {rows_dropped} rows with missing expire_dates")

        # Normalize any other date columns that might exist
        for col in _CHAIN_DATE_COLUMNS:
            if col in df.columns and df[col].dtype != pl.Date:
                logger.info(f"Normalizing {col} values...")
                df = df.with_columns(pl.col(col).str.to_datetime(strict=False).dt.date().alias(col))

        # Ensure all numeric columns are properly typed
        numeric_casts = [pl.col(c).cast(pl.Float64, strict=False) for c in _CHAIN_NUMERIC_COLUMNS if c in df.columns]
        if numeric_casts:
            df = df.with_columns(numeric_casts)

        # Filter out any negative prices (set to null instead)
        for col in _CHAIN_BID_ASK_COLUMNS:
            if col in df.columns:
                invalid_prices = df.filter(pl.col(col) < 0).height
                if invalid_prices > 0:
                    logger.info(f"Found {invalid_prices} negative values in {col}, replacing with null")
                    df = df.with_columns(
                        pl.when(pl.col(col) < 0).then(None).otherwise(pl.col(col)).alias(col)
                    )

        # Calculate days to expiration if it doesn't exist
        if 'dte' not in df.columns:
            logger.info("Calculating days to expiration...")
            df = df.with_columns((pl.col('expire_date') - pl.col('date')).dt.total_days().alias('dte'))

        logger.info("Sample of preprocessed data:")
        logger.debug(str(df.head(5)))

        return df

    def _preprocess_underlying(self, spx_data: pl.DataFrame) -> pl.DataFrame:
        """
        Clean and preprocess underlying price data.

        Args:
            spx_data: Raw underlying price DataFrame

        Returns:
            Cleaned underlying price DataFrame
        """
        logger.info("Preprocessing underlying data...")

        df = spx_data

        numeric_casts = [pl.col(c).cast(pl.Float64, strict=False) for c in _UNDERLYING_NUMERIC_COLUMNS if c in df.columns]
        if numeric_casts:
            df = df.with_columns(numeric_casts)

        return df.sort('date')

    def _check_data_quality(self, option_chain: pd.DataFrame, underlying: pd.DataFrame, vix: pd.DataFrame):
        """
        Check data quality for all datasets (options chain, SPX data, and VIX data).
        Verifies required columns exist and checks for missing or invalid values.

        Args:
            option_chain: DataFrame containing options chain data
            underlying: DataFrame containing SPX data
            vix: DataFrame containing VIX data
        """
        datasets = {
            'Option Chain': {
                'df': option_chain,
                'required_cols': ['expire_date', 'strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'underlying_last']
            },
            'Underlying': {
                'df': underlying,
                'required_cols': ['close', 'open', 'high', 'low']
            },
            'VIX': {
                'df': vix,
                'required_cols': ['close']
            }
        }

        for dataset_name, dataset_info in datasets.items():
            df = dataset_info['df']

            logger.debug(f"Type of dataframe: {type(df), df.head()}")

            if len(df.columns) > 50:
                logger.info("Skipping QA for Dask DataFrame")
                continue

            required_cols = dataset_info['required_cols']

            logger.info(f"\nChecking {dataset_name} data quality...")

            # Check for missing values in key columns
            logger.info(f"\nMissing values in {dataset_name}:")
            for col in required_cols:
                if col in df.columns:
                    missing = df[col].isna().sum()
                    percent = (missing / len(df)) * 100 if len(df) > 0 else 0
                    logger.info(f"{col}: {missing} missing values ({percent:.2f}%)")

            # Check date ranges
            if not df.empty:
                logger.info(f"\n{dataset_name} date range: {df.index.min()} to {df.index.max()}")

            # Check for negative or zero values in bid/ask (separately from missing values)
            logger.info("\nZero or negative values (not including NaN):")
            bid_ask_cols = ['p_bid', 'p_ask', 'c_bid', 'c_ask']
            for col in bid_ask_cols:
                if col in df.columns:
                    zero_values = ((df[col] == 0) & ~df[col].isna()).sum()
                    zero_percent = (zero_values / len(df)) * 100 if len(df) > 0 else 0

                    negative_values = ((df[col] < 0) & ~df[col].isna()).sum()
                    negative_percent = (negative_values / len(df)) * 100 if len(df) > 0 else 0

                    nan_values = df[col].isna().sum()
                    nan_percent = (nan_values / len(df)) * 100 if len(df) > 0 else 0

                    logger.info(f"{col}: {zero_values} zeros ({zero_percent:.2f}%), {negative_values} negative ({negative_percent:.2f}%), {nan_values} NaN ({nan_percent:.2f}%)")

            # Sample data
            if not df.empty:
                logger.info("\nSample data:")
                logger.info(df.head(2))

        logger.info("\n=== End Data Quality Check ===\n")

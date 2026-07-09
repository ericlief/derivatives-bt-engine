from __future__ import annotations
from dataclasses import dataclass, field
import os
import time
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property

import pandas as pd
import numpy as np

import logging

from options_bt.domain.enums import OptionType, PositionSide
from options_bt.domain.base_dataloader import BaseDataLoader
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

@dataclass
class OptionsDataLoader(BaseDataLoader):
    data_dir: str
    options_file: str
    use_preprocessed: bool = True
    save_preprocessed: bool = True
    spx_file: Optional[str] = None
    vix_file: Optional[str] = None
    raw_files: Dict[str, str] = field(init=False)
    processed_files: Dict[str, str] = field(init=False)


    def __post_init__(self):
        """Initialize file paths after instance creation"""
        self.raw_files = {
            'option_chain': os.path.join(self.data_dir, self.options_file),
            'underlying': os.path.join(self.data_dir, 'spx.csv'),
            'vix': os.path.join(self.data_dir, 'vix.csv')
        }
        self.processed_files = {
                'option_chain': os.path.join(self.data_dir, "options.pkl"),
                'underlying': os.path.join(self.data_dir, "spx.pkl"),
                'vix': os.path.join(self.data_dir, "vix.pkl"),
                'option_chain_multi_index': os.path.join(self.data_dir, "chain_multi_index.pkl")
            }

    @cached_property
    def option_chain(self) -> pd.DataFrame:
        """Lazy load and cache the options chain data"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['option_chain'])
            if data is not None:
                return data
        
        raw_options = pd.read_csv(self.raw_files['option_chain'], index_col=0, parse_dates=True)
        processed_data = self._preprocess_option_chain(raw_options)
        
        if self.save_preprocessed:
            self._save_pickle(processed_data, self.processed_files['option_chain'])
            
        return processed_data

    @cached_property
    def option_chain_multi_index(self) -> pd.DataFrame:
        """Lazy load and cache the multi-index options chain"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['option_chain_multi_index'])
            if data is not None:
                return data
        
        multi_index = self.option_chain.reset_index().rename(columns={'index': 'date'})
        multi_index = multi_index.set_index(['date', 'strike']).sort_index()
        
        if self.save_preprocessed:
            self._save_pickle(multi_index, self.processed_files['option_chain_multi_index'])
            
        return multi_index

    @cached_property
    def underlying_data(self) -> pd.DataFrame:
        """Lazy load and cache the SPX data"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['underlying'])
            if data is not None:
                return data
        
        raw_underlying = pd.read_csv(self.raw_files['underlying'], index_col=0, parse_dates=True)
        processed_data = self._preprocess_underlying(raw_underlying)
        
        if self.save_preprocessed:
            self._save_pickle(processed_data, self.processed_files['underlying'])
            
        return processed_data

    def load_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Load and return all data at once. Maintains backward compatibility.
        
        Returns:
            dict: {'option_chain': option_chain, 'option_chain_multi_index': option_chain_multi_index, 'underlying': underlying, 'vix': vix}
        """
        data_loading_start = time.time()
        
        try:
            # Access properties to trigger lazy loading
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
            self._check_data_quality(option_chain, underlying, vix)

            return {'option_chain': option_chain, 'option_chain_multi_index': option_chain_multi_index, 'underlying': underlying, 'vix': vix}
            
        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            raise

    def _preprocess_option_chain(self, option_chain: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess options data to catch and fix common issues.
        
        Args:
            option_chain: Raw options chain DataFrame
        
        Returns:
            Cleaned options chain DataFrame
        """
        logger.info("Preprocessing options chain data...")
        
        # Make a copy to avoid modifying the original
        df = option_chain.copy()
        
        # Normalize the DataFrame index if needed - more efficient approach
        try:
            # Check just the first few values instead of the entire index
            sample_size = min(10, len(df.index))
            if sample_size > 0:
                # Take a sample of index values to check if normalization is needed
                sample_indices = df.index[:sample_size]
                has_time_component = False
                
                # Check if any of the sampled indices have time components
                for idx in sample_indices:
                    if hasattr(idx, 'time') and idx.time() != pd.Timestamp('00:00:00').time():
                        has_time_component = True
                        break
                
                # Only normalize if we detected time components
                if has_time_component:
                    logger.info("Normalizing index dates...")
                    df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
                    logger.info("Index normalization complete")
        except Exception as e:
            logger.info(f"Index normalization skipped: {e}")
            # Just skip normalization if there's an issue - it's likely already normalized
            pass
        
        # Check and fix expire_date issues
        if 'expire_date' in df.columns:
            # Vectorized coercion to Timestamp (invalid/unparseable values -> NaT);
            # equivalent to, but far faster than, a per-row iterrows()+.at[] loop.
            null_before = df['expire_date'].isna().sum()
            df['expire_date'] = pd.to_datetime(df['expire_date'], errors='coerce')
            newly_invalid = df['expire_date'].isna().sum() - null_before
            if newly_invalid > 0:
                logger.info(f"{newly_invalid} expire_date values could not be parsed and were set to NaT")

            # Normalize all expire_dates to remove time component
            logger.info("Normalizing expire_date values...")
            df['expire_date'] = df['expire_date'].dt.normalize()
            
            # Drop rows with missing expire_dates
            rows_before = len(df)
            df = df.dropna(subset=['expire_date'])
            rows_dropped = rows_before - len(df)
            logger.info(f"Dropped {rows_dropped} rows with missing expire_dates")
        
        # Normalize any other date columns that might exist
        date_columns = ['quote_readtime', 'trade_date']
        for col in date_columns:
            if col in df.columns:
                logger.info(f"Normalizing {col} values...")
                df[col] = pd.to_datetime(df[col])
                # Use .dt accessor for Series objects
                df[col] = df[col].dt.normalize()
        
        # Ensure all numeric columns are properly typed
        numeric_cols = ['strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'p_delta', 'c_delta', 'underlying_last']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Filter out any negative prices
        for col in ['p_bid', 'p_ask', 'c_bid', 'c_ask']:
            if col in df.columns:
                invalid_prices = (df[col] < 0).sum()
                if invalid_prices > 0:
                    logger.info(f"Found {invalid_prices} negative values in {col}, replacing with NaN")
                    df.loc[df[col] < 0, col] = np.nan
        
        # Calculate days to expiration if it doesn't exist
        if 'dte' not in df.columns:
            logger.info("Calculating days to expiration...")
            df['dte'] = df.apply(lambda row: pd.Timedelta(row['expire_date'] - row.name).days, axis=1)
        
        if 'c_size' in df.columns:
            pass  # need to preprocess


        logger.info("Sample of preprocessed data:")
        logger.debug(str(df.head(5)))

        return df
    
    def _preprocess_underlying(self, spx_data: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess underlying price data.
        
        Args:
            spx_data: Raw underlying price DataFrame
        
        Returns:
            Cleaned underlying price DataFrame
        """
        logger.info("Preprocessing underlying data...")
        
        # Make a copy to avoid modifying the original
        df = spx_data.copy()
        
        # Normalize the DataFrame index if needed
        try:
            df.index = pd.DatetimeIndex([pd.Timestamp(idx).date() for idx in df.index])
        except Exception as e:
            logger.info(f"Index normalization skipped: {e}")
        
        # Ensure all numeric columns are properly typed
        numeric_cols = ['open', 'high', 'low', 'close']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Sort by date
        df.sort_index(inplace=True)
        
        return df

    def _check_data_quality(self, option_chain, underlying, vix):
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
            
            # Skip checking if the DataFrame is a Dask DataFrame
            # if isinstance(df, dd.DataFrame):
            #     logger.info(f"Skipping data quality check for {dataset_name} (Dask DataFrame).")
            #     continue
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
                    # Count zero values (where the column is not NaN and the value is 0)
                    zero_values = ((df[col] == 0) & ~df[col].isna()).sum()
                    zero_percent = (zero_values / len(df)) * 100 if len(df) > 0 else 0
                    
                    # Count negative values (where the column is not NaN and the value is negative)
                    negative_values = ((df[col] < 0) & ~df[col].isna()).sum()
                    negative_percent = (negative_values / len(df)) * 100 if len(df) > 0 else 0
                    
                    # Count NaN values separately
                    nan_values = df[col].isna().sum()
                    nan_percent = (nan_values / len(df)) * 100 if len(df) > 0 else 0
                    
                    logger.info(f"{col}: {zero_values} zeros ({zero_percent:.2f}%), {negative_values} negative ({negative_percent:.2f}%), {nan_values} NaN ({nan_percent:.2f}%)")
            
            # Sample data
            if not df.empty:
                logger.info("\nSample data:")
                logger.info(df.head(2))
        
        logger.info("\n=== End Data Quality Check ===\n")
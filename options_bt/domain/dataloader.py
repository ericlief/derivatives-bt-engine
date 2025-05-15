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
from options_bt.bt import run_multiple_backtests, setup_logger

# from options_bt.bt import is_put, is_call
logger = setup_logger()

@dataclass
class DataLoader:
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
            'options': os.path.join(self.data_dir, self.options_file),
            'spx': os.path.join(self.data_dir, 'spx.csv'),
            'vix': os.path.join(self.data_dir, 'vix.csv')
        }
        self.processed_files = {
                'options': os.path.join(self.data_dir, "options.pkl"),
                'spx': os.path.join(self.data_dir, "spx.pkl"),
                'vix': os.path.join(self.data_dir, "vix.pkl"),
                'chain_multi_index': os.path.join(self.data_dir, "chain_multi_index.pkl")
            }

    @cached_property
    def options_chain(self) -> pd.DataFrame:
        """Lazy load and cache the options chain data"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['options'])
            if data is not None:
                return data
        
        raw_options = pd.read_csv(self.raw_files['options'], index_col=0, parse_dates=True)
        processed_data = self._preprocess_options_data(raw_options)
        
        if self.save_preprocessed:
            self._save_pickle(processed_data, self.processed_files['options'])
            
        return processed_data

    @cached_property
    def options_chain_multi_index(self) -> pd.DataFrame:
        """Lazy load and cache the multi-index options chain"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['chain_multi_index'])
            if data is not None:
                return data
        
        multi_index = self.options_chain.reset_index().rename(columns={'index': 'date'})
        multi_index = multi_index.set_index(['date', 'strike']).sort_index()
        
        if self.save_preprocessed:
            self._save_pickle(multi_index, self.processed_files['chain_multi_index'])
            
        return multi_index

    @cached_property
    def spx_data(self) -> pd.DataFrame:
        """Lazy load and cache the SPX data"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['spx'])
            if data is not None:
                return data
        
        raw_spx = pd.read_csv(self.raw_files['spx'], index_col=0, parse_dates=True)
        processed_data = self._preprocess_spx_data(raw_spx)
        
        if self.save_preprocessed:
            self._save_pickle(processed_data, self.processed_files['spx'])
            
        return processed_data

    @cached_property
    def vix_data(self) -> pd.DataFrame:
        """Lazy load and cache the VIX data"""
        if self.use_preprocessed:
            data = self._load_pickle(self.processed_files['vix'])
            if data is not None:
                return data
        
        raw_vix = pd.read_csv(self.raw_files['vix'], index_col=0, parse_dates=True)
        processed_data = self._preprocess_vix_data(raw_vix)
        
        if self.save_preprocessed:
            self._save_pickle(processed_data, self.processed_files['vix'])
            
        return processed_data

    def load_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Load and return all data at once. Maintains backward compatibility.
        
        Returns:
            tuple: (options_chain, options_chain_multi_index, spx_data, vix_data)
        """
        data_loading_start = time.time()
        
        try:
            # Access properties to trigger lazy loading
            options_chain = self.options_chain
            options_chain_multi_index = self.options_chain_multi_index
            spx_data = self.spx_data
            vix_data = self.vix_data
            
            logger.info(f"Loaded and preprocessed data:")
            logger.info(f"- Normal options chain: {len(options_chain)} rows")
            logger.info(f"- MultiIndex options chain: {len(options_chain_multi_index)} rows")
            logger.info(f"- SPX data: {len(spx_data)} rows")
            logger.info(f"- VIX data: {len(vix_data)} rows")
            
            data_loading_time = time.time() - data_loading_start
            logger.info(f"Data loading completed in {data_loading_time:.2f} seconds")
            self._check_data_quality(options_chain, spx_data, vix_data)

            return options_chain, options_chain_multi_index, spx_data, vix_data
            
        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            raise

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

    def _preprocess_options_data(self, options_chain: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess options data to catch and fix common issues.
        
        Args:
            options_chain: Raw options chain DataFrame
        
        Returns:
            Cleaned options chain DataFrame
        """
        logger.info("Preprocessing options chain data...")
        
        # Make a copy to avoid modifying the original
        df = options_chain.copy()
        
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
            # Count invalid timestamps
            invalid_dates = 0
            fixed_dates = 0
            
            # Sample a few rows to check if fixes are needed
            sample_size = min(100, len(df))
            sample_rows = df.sample(n=sample_size) if sample_size > 0 else df
            
            needs_fixing = False
            for _, row in sample_rows.iterrows():
                if row['expire_date'] is not None and not isinstance(row['expire_date'], pd.Timestamp):
                    needs_fixing = True
                    break
            
            # Only process if we detected issues in the sample
            if needs_fixing:
                logger.info("Fixing invalid expire_date values...")
                # Check for any non-Timestamp objects in expire_date
                for i, row in df.iterrows():
                    if row['expire_date'] is not None and not isinstance(row['expire_date'], pd.Timestamp):
                        invalid_dates += 1
                        try:
                            # Try to convert to Timestamp
                            df.at[i, 'expire_date'] = pd.Timestamp(row['expire_date'])
                            fixed_dates += 1
                        except:
                            # If conversion fails, set to NaT (pandas missing timestamp)
                            df.at[i, 'expire_date'] = pd.NaT
                
                logger.info(f"Fixed {fixed_dates} of {invalid_dates} invalid expire_date values")
            
            # Normalize all expire_dates to remove time component
            logger.info("Normalizing expire_date values...")
            df['expire_date'] = pd.to_datetime(df['expire_date'])
            # Use .dt accessor for Series objects instead of directly calling floor
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
    
    def _preprocess_spx_data(self, spx_data: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess SPX price data.
        
        Args:
            spx_data: Raw SPX price DataFrame
        
        Returns:
            Cleaned SPX price DataFrame
        """
        logger.info("Preprocessing SPX data...")
        
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

    def _preprocess_vix_data(self, vix_data: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess VIX data.
        
        Args:
            vix_data: Raw VIX DataFrame
        
        Returns:
            Cleaned VIX DataFrame
        """
        logger.info("Preprocessing VIX data...")
        
        # Make a copy to avoid modifying the original
        df = vix_data.copy()
        
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
    
    def _check_data_quality(self, options_chain, spx_data, vix_data):
        """
        Check data quality for all datasets (options chain, SPX data, and VIX data).
        Verifies required columns exist and checks for missing or invalid values.
        
        Args:
            options_chain: DataFrame containing options chain data
            spx_data: DataFrame containing SPX data
            vix_data: DataFrame containing VIX data
        """
        datasets = {
            'Options Chain': {
                'df': options_chain,
                'required_cols': ['expire_date', 'strike', 'p_bid', 'p_ask', 'c_bid', 'c_ask', 'underlying_last']
            },
            'SPX': {
                'df': spx_data,
                'required_cols': ['close', 'open', 'high', 'low']
            },
            'VIX': {
                'df': vix_data,
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
"""
DataFrame schemas and validation functions for the options backtesting system.

This module contains standardized schemas for various DataFrames used throughout
the backtesting system, as well as functions for validating and standardizing
DataFrames to match these schemas.
"""

from typing import Dict, List, Optional, TypedDict, Union
import pandas as pd
from .enums import SpreadType
import logging

logger = logging.getLogger(__name__)

# Core DataFrame Schemas

# Options Chain DataFrame
OPTIONS_CHAIN_SCHEMA = {
    'index': 'date',  # pd.DatetimeIndex
    'columns': {
        'expire_date': 'datetime64[ns]',  # Expiration date
        'strike': 'float64',              # Strike price
        'underlying_last': 'float64',     # Last price of underlying
        'p_bid': 'float64',              # Put bid price
        'p_ask': 'float64',              # Put ask price
        'c_bid': 'float64',              # Call bid price
        'c_ask': 'float64',              # Call ask price
        'p_delta': 'float64',            # Put delta
        'c_delta': 'float64',            # Call delta
        'p_iv': 'float64',               # Put implied volatility
        'c_iv': 'float64',               # Call implied volatility
        'p_size': 'int64',               # Put volume
        'c_size': 'int64',               # Call volume
        'dte': 'int64',                  # Days to expiration
    }
}

# Trade Signals DataFrame
TRADE_SIGNALS_SCHEMA = {
    'index': 'date',  # pd.DatetimeIndex
    'columns': {
        'strike': 'float64',
        'expire_date': 'datetime64[ns]',
        'underlying_last': 'float64',
        'bid': 'float64',                # Relevant bid (put or call)
        'ask': 'float64',                # Relevant ask (put or call)
        'delta': 'float64',              # Relevant delta
        'dte': 'int64',
        'spread_type': 'str',            # For spread trades
        'spread_id': 'int64',            # For spread trades
        'leg_number': 'int64',           # For spread trades
        'leg_ratio': 'float64',          # For spread trades
        'position_side': 'str',
        'option_type': 'str',
        'spread_price': 'float64',       # For spread trades
        'margin_required': 'float64'
    }
}

# Position DataFrame
POSITION_SCHEMA = {
    'index': 'date',  # pd.DatetimeIndex
    'columns': {
        'trade_id': 'int64',
        'quantity': 'int64',
        'entry_date': 'datetime64[ns]',
        'expire_date': 'datetime64[ns]',
        'underlying_entry': 'float64',
        'underlying_exit': 'float64',
        'strike': 'float64',
        'option_type': 'str',
        'position_side': 'str',
        'bid': 'float64',
        'ask': 'float64',
        'entry_price': 'float64',
        'exit_price': 'float64',
        'margin_required': 'float64',
        'entry_delta': 'float64',
        'entry_dte': 'int64',
        'close_date': 'datetime64[ns]',
        'spread_type': 'str',
        'spread_id': 'int64',
        'leg_number': 'int64',
        'leg_ratio': 'float64',
        'spread_price': 'float64'
    }
}

# Trade Results DataFrame
TRADE_RESULTS_SCHEMA = {
    'index': 'trade_id',  # Int64Index
    'columns': {
        'quantity': 'int64',
        'option_type': 'str',
        'position_side': 'str',
        'entry_date': 'datetime64[ns]',
        'exit_date': 'datetime64[ns]',
        'expire_date': 'datetime64[ns]',
        'entry_delta': 'float64',
        'exit_delta': 'float64',
        'entry_dte': 'int64',
        'days_held': 'int64',
        'underlying_entry': 'float64',
        'underlying_exit': 'float64',
        'strike': 'float64',
        'entry_price': 'float64',
        'exit_price': 'float64',
        'capital_used': 'float64',
        'option_bp': 'float64',
        'return_on_margin': 'float64',
        'close_reason': 'str',
        'pnl': 'float64',
        'spread_type': 'str',
        'spread_id': 'int64',
        'leg_number': 'int64'
    }
}

def validate_dataframe_schema(df: pd.DataFrame, schema: Dict, name: str = "") -> bool:
    """
    Validate that a DataFrame conforms to the specified schema.
    
    Args:
        df: DataFrame to validate
        schema: Schema dictionary containing index and column specifications
        name: Name of the DataFrame for logging purposes
        
    Returns:
        bool: True if validation passes, False otherwise
    """
    try:
        # Check index type
        if schema['index'] == 'date':
            if not isinstance(df.index, pd.DatetimeIndex):
                logger.error(f"{name}: Index must be DatetimeIndex")
                return False
        elif schema['index'] == 'trade_id':
            if not df.index.dtype == 'int64':
                logger.error(f"{name}: Index must be Int64Index")
                return False
                
        # Check required columns exist with correct types
        for col, dtype in schema['columns'].items():
            if col not in df.columns:
                logger.error(f"{name}: Missing required column {col}")
                return False
            if df[col].dtype != dtype and not pd.isna(df[col]).all():
                try:
                    # Attempt to convert to correct type
                    df[col] = df[col].astype(dtype)
                except:
                    logger.error(f"{name}: Column {col} has incorrect dtype {df[col].dtype}, expected {dtype}")
                    return False
                    
        return True
        
    except Exception as e:
        logger.error(f"Schema validation error for {name}: {str(e)}")
        return False

def standardize_dataframe(df: pd.DataFrame, schema: Dict, name: str = "") -> pd.DataFrame:
    """
    Standardize a DataFrame to match the specified schema.
    
    Args:
        df: DataFrame to standardize
        schema: Schema dictionary containing index and column specifications
        name: Name of the DataFrame for logging purposes
        
    Returns:
        Standardized DataFrame
    """
    try:
        # Create a copy to avoid modifying the original
        result = df.copy()
        
        # Standardize index
        if schema['index'] == 'date':
            if not isinstance(result.index, pd.DatetimeIndex):
                result.index = pd.to_datetime(result.index)
        elif schema['index'] == 'trade_id':
            if not result.index.dtype == 'int64':
                result.index = result.index.astype('int64')
                
        # Add missing columns with NaN values
        for col, dtype in schema['columns'].items():
            if col not in result.columns:
                if dtype in ['int64', 'float64']:
                    result[col] = pd.NA
                else:
                    result[col] = None
                    
        # Convert columns to correct types
        for col, dtype in schema['columns'].items():
            if result[col].dtype != dtype:
                try:
                    result[col] = result[col].astype(dtype)
                except:
                    logger.warning(f"Could not convert column {col} to {dtype}")
                    
        # Reorder columns to match schema
        result = result[list(schema['columns'].keys())]
        
        return result
        
    except Exception as e:
        logger.error(f"Error standardizing {name}: {str(e)}")
        return df

def add_spread_fields(df: pd.DataFrame, spread_type: SpreadType = None) -> pd.DataFrame:
    """
    Add spread-specific fields to a DataFrame if they don't exist.
    
    Args:
        df: DataFrame to modify
        spread_type: Type of spread (None for single legs)
        
    Returns:
        DataFrame with spread fields added
    """
    result = df.copy()
    
    # Add spread fields if not present
    spread_fields = {
        'spread_type': 'str',
        'spread_id': 'int64',
        'leg_number': 'int64',
        'leg_ratio': 'float64',
        'spread_price': 'float64'
    }
    
    for field, dtype in spread_fields.items():
        if field not in result.columns:
            if dtype in ['int64', 'float64']:
                result[field] = pd.NA
            else:
                result[field] = None
                
    # Set spread type if provided
    if spread_type:
        result['spread_type'] = spread_type.value
        
    return result 
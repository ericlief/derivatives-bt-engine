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

class SignalGenerator:
    """Class to generate signals for trading."""
    
    def __init__(self, config: Dict):
        
        self.config: dict = config
        self.start_date: pd.Timestamp = config['start_date']
        self.end_date: pd.Timestamp = config['end_date']
        self.symbol_list: Optional[list] = config['symbol_list'] or []

    def generate_trade_signals(
        self,
        spx_data: pd.DataFrame,
        options_chain: pd.DataFrame,
        option_type: OptionType,
        delta_target: float,
        delta_range: Tuple[float, float],
        dte_target: int,
        dte_range: Tuple[int, int],
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        """
        Generate trade signals based on the provided parameters. These are not the actual trades,
        but rather potential trades filtered for the desired criteria. The DataFrame should have a 
        pd.DateTime index
        
        Args:
            spx_data: DataFrame containing underlying price data
            options_chain: DataFrame containing options chain data
            option_type: Type of option strategy to trade (PUT or CALL)
            delta_target: Target delta value for the trade
            delta_range: Range of delta values to consider
            dte_target: Target days to expiration for the trade
            dte_range: Range of days to expiration to consider
            start_date: Start date for the trade signals
            end_date: End date for the trade signals
        
        Returns:
            DataFrame containing the generated trade signals
        """

        logger.debug(f'Generating trade signals for {option_type}|{delta_target if delta_target else delta_range}|{dte_target if dte_target else dte_range}|{start_date if start_date else "all"}|{end_date if end_date else "all"}')
        
        # Create a copy of the options chain to avoid modifying the original
        chain_df = options_chain.copy()
        
        # Filter by DATE range if provided
        if start_date:
            start_date = pd.to_datetime(start_date)
            chain_df = chain_df[chain_df.index >= start_date]
        
        if end_date:
            end_date = pd.to_datetime(end_date)
            chain_df = chain_df[chain_df.index <= end_date]
            logger.debug(f'Sorting for date range: {start_date}-{end_date}')
            logger.debug(f'Sample chain of length: {len(chain_df)}')
            logger.debug(chain_df.head())

        # Remove columns that are not needed
        prefix = 'p_' if is_put(option_type) else 'c_'
        cols = chain_df.columns
        needed_cols = [col for col in cols if col.startswith(prefix)]
        needed_cols.extend(['strike', 'dte', 'underlying_last', 'expire_date', 'strike_distance', 'strike_distance_pct'])
        chain_df = chain_df[needed_cols]
        

        # Filter out options with zero or negative bids/asks
        bid_col = f'{prefix}bid'
        ask_col = f'{prefix}ask'
        chain_df = chain_df[
            (chain_df[bid_col] > 0) & 
            (chain_df[ask_col] > 0)
        ]
        
        # Filter out options with unreasonable spreads (50% max)
        chain_df['spread_percent'] = ((chain_df[ask_col] - chain_df[bid_col]) / chain_df[bid_col]) * 100
        chain_df = chain_df[chain_df['spread_percent'] <= 50.0]  # Max 50% spread
        
        logger.debug(f'After spread filtering: {len(chain_df)} options remaining')
        logger.debug(chain_df['spread_percent'].describe())

        # Precompute midpoint price for each row
        chain_df['midpoint_price'] = chain_df.apply(
            lambda row: calculate_midpoint_price(row[bid_col], row[ask_col]),   
            axis=1  
        )
        
        # Filter by DTE based on whether we have a single value or range
        if dte_range:
            dte_mask = (chain_df['dte'] >= dte_range[0]) & (chain_df['dte'] <= dte_range[1])
            chain_df = chain_df[dte_mask]
            logger.debug(chain_df['dte'].describe())
            logger.debug(f'Filtering for dte range: {dte_range}')
            logger.debug(f'Sample chain of length: {len(chain_df)}')
            logger.debug(chain_df.head())
            logger.debug(chain_df['dte'].describe())

        elif dte_target:
            logger.debug(chain_df['dte'].describe())
            dte_mask = abs(chain_df['dte'] - dte_target) < 1
            chain_df = chain_df[dte_mask]
            logger.debug(f'Filtering for dte target: {dte_target}')
            logger.debug('Sample chain')
            logger.debug(chain_df.head())
            logger.debug(chain_df['dte'].describe())

        else:
            logger.error('Need to provide either <dte_target> or <dte_range>')
            raise ValueError
        
        # Filter by delta parameters        
        delta_col = 'p_delta' if is_put(option_type) else 'c_delta'
        logger.debug(f'Initial delta distribution')
        logger.debug(chain_df[delta_col].describe())
        
        if delta_range:
            # Handle range case
            if is_put(option_type):
                min_delta = -abs(delta_range[1])  # More negative (more ITM)
                max_delta = -abs(delta_range[0])  # Less negative (more OTM)
            else:
                min_delta = abs(delta_range[0])  # Less positive (more OTM)
                max_delta = abs(delta_range[1])  # More positive (more ITM)

            logger.debug(chain_df[delta_col].describe())
            logger.debug(f'Filtering for delta range: {min_delta} to {max_delta} for {option_type.value}')
            delta_mask = chain_df[delta_col].between(min_delta, max_delta)
            chain_df = chain_df[delta_mask]
            logger.debug(chain_df[delta_col].describe())

            # Sort by delta value while maintaining the date index
            ascending = is_call(option_type)  # Ascending for calls, descending for puts
            chain_df = chain_df.sort_values(by=[delta_col], ascending=ascending)
            trade_signals = chain_df
            logger.debug(f'Sample chain of length: {len(chain_df)}')
            logger.debug(chain_df.head())

        elif delta_target:
            # Handle target case
            if is_put(option_type):
                # For puts, we want negative deltas
                target = -abs(delta_target)
                # For puts, we want to find options with deltas closest to the target (more negative)
                ascending = False
            else:
                # For calls, we want positive deltas
                target = abs(delta_target)
                # For calls, we want to find options with deltas closest to the target (more positive)
                ascending = True

            logger.debug(f'Filtering for delta target: {target} for {option_type.value}')
            delta_diff = abs(chain_df[delta_col] - target)
            chain_df = chain_df.assign(delta_diff=delta_diff)
            
            # Filter out options that are too far from target delta (20% tolerance)
            max_delta_diff = abs(target) * 0.20  # 20% tolerance
            chain_df = chain_df[chain_df['delta_diff'] <= max_delta_diff]
            
            # Sort by delta difference and delta value while maintaining the date index
            chain_df = chain_df.sort_values(by=['delta_diff', delta_col], ascending=[True, ascending])
            trade_signals = chain_df
            logger.debug(f'Sample chain of length: {len(chain_df)}')
            logger.debug(chain_df.head())
        else:
            logger.error('Need to provide either delta_target or delta_range')
            raise ValueError
        
        logger.info(f"Generated {len(trade_signals)} trade signals")
        logger.info("\nSample of trade signals:")
        logger.info(trade_signals.head())
        
        return trade_signals
    
    def generate_spread_signals(
        options_chain: pd.DataFrame,
        spread_type: SpreadType,
        legs_config: List[Dict],
        start_date: str = None,
        end_date: str = None,
        dte_range: Tuple[int, int] = None,
        dte_target: int = None,
        spx_data: pd.DataFrame = None,
    ) -> pd.DataFrame:
        """
        Generate trade signals for option spreads by pairing legs according to the specified spread type.
        
        Args:
            options_chain: DataFrame containing options chain data
            spread_type: Type of spread to generate
            legs_config: List of configurations for each leg of the spread
                Each leg config should have:
                - option_type: OptionType for this leg
                - position_side: PositionSide for this leg
                - delta_target or delta_range: Delta criteria for this leg
                - ratio: Quantity ratio for this leg (default 1)
            start_date: Start date for the trade signals
            end_date: End date for the trade signals
            dte_range: Range of days to expiration to consider
            dte_target: Target days to expiration for the trade
            spx_data: DataFrame containing SPX price data (optional)
        
        Returns:
            DataFrame containing the generated spread signals with legs paired by date
        """
        logger.info(f"Generating {spread_type.value} spread signals...")
        
        if spread_type == SpreadType.NONE:
            raise ValueError("Use generate_trade_signals for single-leg positions")
        
        # Generate signals for each leg separately
        leg_signals = []
        for i, leg_config in enumerate(legs_config):
            option_type = leg_config['option_type']
            position_side = leg_config['position_side']
            delta_target = leg_config.get('delta_target')
            delta_range = leg_config.get('delta_range')
            
            # Ensure either delta_target or delta_range is provided
            if delta_target is None and delta_range is None:
                logger.error(f"Leg {i+1} must have either delta_target or delta_range specified")
                return pd.DataFrame()
            
            # Filter options chain for the 
            leg_df = generate_trade_signals(
                spx_data=spx_data,  # Pass SPX data if available
                options_chain=options_chain,
                option_type=option_type,
                delta_target=delta_target,
                delta_range=delta_range,
                dte_target=dte_target,
                dte_range=dte_range,
                start_date=start_date,
                end_date=end_date,
            )
            
            if leg_df.empty:
                logger.warning(f"No signals generated for leg {i+1} with config: {leg_config}")
                return pd.DataFrame()
            
            # Store the index name before adding columns
            index_name = leg_df.index.name
            
            # Add leg-specific columns
            leg_df['leg_number'] = i + 1
            leg_df['position_side'] = position_side.value if isinstance(position_side, Enum) else position_side
            leg_df['option_type'] = option_type.value if isinstance(option_type, Enum) else option_type
            leg_df['leg_ratio'] = leg_config.get('ratio', 1)
            leg_df['delta_target'] = delta_target
            if delta_range:
                leg_df['delta_range_min'] = delta_range[0]
                leg_df['delta_range_max'] = delta_range[1]
            
            # Restore the index name
            leg_df.index.name = index_name
            
            leg_signals.append(leg_df)
        
        # No valid signals for one or more legs
        if any(df.empty for df in leg_signals):
            logger.warning("One or more legs returned no signals")
            return pd.DataFrame()
        
        # Create spread signals based on the spread type
        if spread_type == SpreadType.VERTICAL:
            return _pair_vertical_spread_legs(leg_signals, spread_type)
        elif spread_type == SpreadType.CALENDAR:
            return _pair_calendar_spread_legs(leg_signals, spread_type)
        elif spread_type == SpreadType.DIAGONAL:
            return _pair_diagonal_spread_legs(leg_signals, spread_type)
        elif spread_type == SpreadType.BUTTERFLY:
            return _pair_butterfly_spread_legs(leg_signals, spread_type)
        elif spread_type == SpreadType.IRON_CONDOR:
            return _pair_iron_condor_spread_legs(leg_signals, spread_type)
        else:
            raise ValueError(f"Unsupported spread type: {spread_type}")

    def _pair_vertical_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
        """
        Pair legs for vertical spreads (same expiration, different strikes).
        
        Args:
            leg_signals: List of DataFrames containing signals for each leg
            spread_type: Type of spread being created
        
        Returns:
            DataFrame with paired spread signals
        """
        if len(leg_signals) != 2:
            raise ValueError(f"Vertical spreads require exactly 2 legs, got {len(leg_signals)}")
        
        logger.debug("Pairing vertical spread legs...")
        
        # Extract the two legs
        leg1 = leg_signals[0].copy()
        leg2 = leg_signals[1].copy()
        
        # Convert index to datetime if it's not already
        leg1.index = pd.to_datetime(leg1.index)
        leg2.index = pd.to_datetime(leg2.index)
        
        # Store index name and ensure it's not None
        index_name = leg1.index.name or 'date'
        leg1.index.name = index_name
        leg2.index.name = index_name
        
        # Reset index to make date a column
        leg1 = leg1.reset_index()
        leg2 = leg2.reset_index()
        
        # Rename columns to distinguish between legs
        leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name and col != "expire_date"}
        leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name and col != "expire_date"}
        
        leg1 = leg1.rename(columns=leg1_cols)
        leg2 = leg2.rename(columns=leg2_cols)
        
        # Merge on date and expiration date to ensure the legs are for the same expiration 
        # and same trading day
        paired = pd.merge(
            leg1,
            leg2,
            on=[index_name, "expire_date"],
            how="inner"
        )
        logger.debug(f"Paired vertical spread legs: {paired.head()}")
        
        # Filter for valid vertical spread criteria
        # For example, ensure the strikes are different
        if len(paired) > 0:
            if spread_type == SpreadType.VERTICAL:
                paired = paired[paired["leg1_strike"] != paired["leg2_strike"]]
                
                # For put vertical spreads, leg1 strike should be higher than leg2 strike for a credit spread
                if is_put(leg_signals[0].iloc[0]["option_type"]) and is_short(leg_signals[0].iloc[0]["position_side"]):
                    paired = paired[paired["leg1_strike"] > paired["leg2_strike"]]
                # For call vertical spreads, leg1 strike should be lower than leg2 strike for a credit spread
                elif is_call(leg_signals[0].iloc[0]["option_type"]) and is_short(leg_signals[0].iloc[0]["position_side"]):
                    paired = paired[paired["leg1_strike"] < paired["leg2_strike"]]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        
        # Calculate spread metrics
        paired["spread_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
        
        # Calculate spread price (add code to adjust based on position side)
        # For a credit spread, we want to sell the first leg and buy the second leg
        paired["leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price (credit if positive, debit if negative)
        # For credit spreads (short first leg, long second leg)
        if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        # For debit spreads (long first leg, short second leg)
        else:
            paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid vertical spreads")
        logger.debug(paired.head())
        
        return paired

    def _pair_calendar_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
        """
        Pair legs for calendar spreads (same strike, different expirations).
        
        Args:
            leg_signals: List of DataFrames containing signals for each leg
            spread_type: Type of spread being created
        
        Returns:
            DataFrame with paired spread signals
        """
        if len(leg_signals) != 2:
            raise ValueError(f"Calendar spreads require exactly 2 legs, got {len(leg_signals)}")
        
        logger.debug("Pairing calendar spread legs...")
        
        # Extract the two legs
        leg1 = leg_signals[0].copy()  # Front month (near-term expiration)
        leg2 = leg_signals[1].copy()  # Back month (far-term expiration)
        
        # Convert index to datetime if it's not already
        leg1.index = pd.to_datetime(leg1.index)
        leg2.index = pd.to_datetime(leg2.index)
        
        # Store index name and ensure it's not None
        index_name = leg1.index.name or 'date'
        leg1.index.name = index_name
        leg2.index.name = index_name
        
        # Reset index to make date a column
        leg1 = leg1.reset_index()
        leg2 = leg2.reset_index()
        
        # Rename columns to distinguish between legs
        leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name}
        leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name}
        
        leg1 = leg1.rename(columns=leg1_cols)
        leg2 = leg2.rename(columns=leg2_cols)
        
        # Merge on date and strike to ensure the legs are for the same strike
        # and same trading day but different expirations
        paired = pd.merge(
            leg1,
            leg2,
            on=[index_name],
            how="inner"
        )
        logger.debug(f"Paired calendar spread legs: {paired.head()}")
        
        # Filter for valid calendar spread criteria
        # Ensure strikes are the same
        paired = paired[paired["leg1_strike"] == paired["leg2_strike"]]
        
        # Ensure expirations are different and in the correct order
        paired = paired[paired["leg1_expire_date"] < paired["leg2_expire_date"]]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        paired["time_width"] = paired.apply(lambda row: pd.Timedelta(row["leg2_expire_date"] - row["leg1_expire_date"]).days, axis=1)
        
        # Calculate leg prices
        paired["leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price (usually a debit for a standard calendar)
        # For standard calendar spreads (short front month, long back month)
        if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        # For reverse calendar spreads (long front month, short back month)
        else:
            paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid calendar spreads")
        
        return paired

    def _pair_diagonal_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
        """
        Pair legs for diagonal spreads (different strikes, different expirations).
        
        Args:
            leg_signals: List of DataFrames containing signals for each leg
            spread_type: Type of spread being created
        
        Returns:
            DataFrame with paired spread signals
        """
        if len(leg_signals) != 2:
            raise ValueError(f"Diagonal spreads require exactly 2 legs, got {len(leg_signals)}")
        
        logger.debug("Pairing diagonal spread legs...")
        
        # Extract the two legs
        leg1 = leg_signals[0].copy()  # Front month, first strike
        leg2 = leg_signals[1].copy()  # Back month, second strike
        
        # Convert index to datetime if it's not already
        leg1.index = pd.to_datetime(leg1.index)
        leg2.index = pd.to_datetime(leg2.index)
        
        # Store index name and ensure it's not None
        index_name = leg1.index.name or 'date'
        leg1.index.name = index_name
        leg2.index.name = index_name
        
        # Reset index to make date a column
        leg1 = leg1.reset_index()
        leg2 = leg2.reset_index()
        
        # Rename columns to distinguish between legs
        leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name}
        leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name}
        
        leg1 = leg1.rename(columns=leg1_cols)
        leg2 = leg2.rename(columns=leg2_cols)
        
        # Merge on date to ensure the legs are for the same trading day
        paired = pd.merge(
            leg1,
            leg2,
            on=[index_name],
            how="inner"
        )
        logger.debug(f"Paired diagonal spread legs: {paired.head()}")
        
        # Filter for valid diagonal spread criteria
        # Ensure expirations are different and in the correct order
        paired = paired[paired["leg1_expire_date"] < paired["leg2_expire_date"]]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        paired["time_width"] = paired.apply(lambda row: pd.Timedelta(row["leg2_expire_date"] - row["leg1_expire_date"]).days, axis=1)
        paired["strike_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
        
        # Calculate leg prices
        paired["leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price
        # For standard diagonal spreads (short front month, long back month)
        if is_short(leg_signals[0].iloc[0]["position_side"]) and is_long(leg_signals[1].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        # For reverse diagonal spreads (long front month, short back month)
        else:
            paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid diagonal spreads")
        
        return paired

    def _pair_butterfly_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
        """
        Pair legs for butterfly spreads (3 strikes, same expiration).
        
        Args:
            leg_signals: List of DataFrames containing signals for each leg
            spread_type: Type of spread being created
        
        Returns:
            DataFrame with paired spread signals
        """
        if len(leg_signals) != 3:
            raise ValueError(f"Butterfly spreads require exactly 3 legs, got {len(leg_signals)}")
        
        logger.debug("Pairing butterfly spread legs...")
        
        # Extract the three legs
        leg1 = leg_signals[0].copy().reset_index()  # Lower strike
        leg2 = leg_signals[1].copy().reset_index()  # Middle strike (2x quantity)
        leg3 = leg_signals[2].copy().reset_index()  # Higher strike
        
        # Convert date columns to pandas Timestamps
        leg1['date'] = pd.to_datetime(leg1['date'])
        leg2['date'] = pd.to_datetime(leg2['date'])
        leg3['date'] = pd.to_datetime(leg3['date'])
        
        # Set index name to make it clear
        leg1 = leg1.rename(columns={"index": "date"})
        leg2 = leg2.rename(columns={"index": "date"})
        leg3 = leg3.rename(columns={"index": "date"})
        
        # Rename columns to distinguish between legs
        leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != "date" and col != "expire_date"}
        leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != "date" and col != "expire_date"}
        leg3_cols = {col: f"leg3_{col}" for col in leg3.columns if col != "date" and col != "expire_date"}
        
        leg1 = leg1.rename(columns=leg1_cols)
        leg2 = leg2.rename(columns=leg2_cols)
        leg3 = leg3.rename(columns=leg3_cols)
        
        # Merge on date and expiration to ensure all legs are for the same expiration
        # and same trading day
        paired = pd.merge(leg1, leg2, on=["date", "expire_date"], how="inner")
        paired = pd.merge(paired, leg3, on=["date", "expire_date"], how="inner")
        logger.debug(f"Paired butterfly spread legs: {paired.head()}")
        
        # Filter for valid butterfly spread criteria
        if len(paired) > 0:
            # Calculate differences between strikes
            paired["diff1"] = paired["leg2_strike"] - paired["leg1_strike"]
            paired["diff2"] = paired["leg3_strike"] - paired["leg2_strike"]
            
            # Keep only rows where the differences are equal (or very close)
            paired = paired[abs(paired["diff1"] - paired["diff2"]) < 0.01]
            
            # Ensure strikes are in ascending order
            paired = paired[
                (paired["leg1_strike"] < paired["leg2_strike"]) & 
                (paired["leg2_strike"] < paired["leg3_strike"])
            ]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        paired["wing_width"] = paired["diff1"]  # Width between strikes
        
        # Calculate leg prices
        paired["leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg1_p_bid"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg2_p_bid"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        paired["leg3_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["leg3_p_bid"] if is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_bid"],
                row["leg3_p_ask"] if is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price
        # Long butterfly: buy wing options, sell 2x middle option
        if is_long(leg_signals[0].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg1_price"] - 2 * paired["leg2_price"] + paired["leg3_price"]
        # Short butterfly: sell wing options, buy 2x middle option
        else:
            paired["spread_price"] = 2 * paired["leg2_price"] - paired["leg1_price"] - paired["leg3_price"]
        
        logger.debug(f"Paired {len(paired)} valid butterfly spreads")
        
        return paired

    def _pair_iron_condor_spread_legs(leg_signals: List[pd.DataFrame], spread_type: SpreadType) -> pd.DataFrame:
        """
        Pair legs for iron condor spreads (4 strikes, same expiration).
        
        Args:
            leg_signals: List of DataFrames containing signals for each leg
            spread_type: Type of spread being created
        
        Returns:
            DataFrame with paired spread signals
        """
        if len(leg_signals) != 4:
            raise ValueError(f"Iron condor spreads require exactly 4 legs, got {len(leg_signals)}")
        
        logger.debug("Pairing iron condor spread legs...")
        
        # Extract the four legs
        put_leg1 = leg_signals[0].copy().reset_index()  # Lower put strike (long)
        put_leg2 = leg_signals[1].copy().reset_index()  # Higher put strike (short)
        call_leg1 = leg_signals[2].copy().reset_index()  # Lower call strike (short)
        call_leg2 = leg_signals[3].copy().reset_index()  # Higher call strike (long)
        
        # Convert date columns to pandas Timestamps
        put_leg1['date'] = pd.to_datetime(put_leg1['date'])
        put_leg2['date'] = pd.to_datetime(put_leg2['date'])
        call_leg1['date'] = pd.to_datetime(call_leg1['date'])
        call_leg2['date'] = pd.to_datetime(call_leg2['date'])
        
        # Set index name to make it clear
        put_leg1 = put_leg1.rename(columns={"index": "date"})
        put_leg2 = put_leg2.rename(columns={"index": "date"})
        call_leg1 = call_leg1.rename(columns={"index": "date"})
        call_leg2 = call_leg2.rename(columns={"index": "date"})
        
        # Rename columns to distinguish between legs
        put_leg1_cols = {col: f"put_leg1_{col}" for col in put_leg1.columns if col != "date" and col != "expire_date"}
        put_leg2_cols = {col: f"put_leg2_{col}" for col in put_leg2.columns if col != "date" and col != "expire_date"}
        call_leg1_cols = {col: f"call_leg1_{col}" for col in call_leg1.columns if col != "date" and col != "expire_date"}
        call_leg2_cols = {col: f"call_leg2_{col}" for col in call_leg2.columns if col != "date" and col != "expire_date"}
        
        put_leg1 = put_leg1.rename(columns=put_leg1_cols)
        put_leg2 = put_leg2.rename(columns=put_leg2_cols)
        call_leg1 = call_leg1.rename(columns=call_leg1_cols)
        call_leg2 = call_leg2.rename(columns=call_leg2_cols)
        
        # Merge on date and expiration to ensure all legs are for the same expiration
        # and same trading day
        paired = pd.merge(put_leg1, put_leg2, on=["date", "expire_date"], how="inner")
        paired = pd.merge(paired, call_leg1, on=["date", "expire_date"], how="inner")
        paired = pd.merge(paired, call_leg2, on=["date", "expire_date"], how="inner")
        logger.debug(f"Paired iron condor spread legs: {paired.head()}")
        
        # Filter for valid iron condor spread criteria
        if len(paired) > 0:
            # Ensure strikes are in the correct order
            paired = paired[
                (paired["put_leg1_strike"] < paired["put_leg2_strike"]) &
                (paired["put_leg2_strike"] < paired["call_leg1_strike"]) &
                (paired["call_leg1_strike"] < paired["call_leg2_strike"])
            ]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        paired["put_width"] = paired["put_leg2_strike"] - paired["put_leg1_strike"]
        paired["call_width"] = paired["call_leg2_strike"] - paired["call_leg1_strike"]
        paired["middle_width"] = paired["call_leg1_strike"] - paired["put_leg2_strike"]
        
        # Calculate leg prices
        paired["put_leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["put_leg1_p_bid"],
                row["put_leg1_p_ask"]
            ),
            axis=1
        )
        
        paired["put_leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["put_leg2_p_bid"],
                row["put_leg2_p_ask"]
            ),
            axis=1
        )
        
        paired["call_leg1_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["call_leg1_c_bid"],
                row["call_leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["call_leg2_price"] = paired.apply(
            lambda row: calculate_midpoint_price(
                row["call_leg2_c_bid"],
                row["call_leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate spread price - assuming standard iron condor (sell the middle strikes, buy the wings)
        # Credit from put vertical spread
        put_spread_price = paired["put_leg2_price"] - paired["put_leg1_price"]
        # Credit from call vertical spread
        call_spread_price = paired["call_leg1_price"] - paired["call_leg2_price"]
        # Total credit from iron condor
        paired["spread_price"] = put_spread_price + call_spread_price
        
        logger.debug(f"Paired {len(paired)} valid iron condor spreads")
        
        return paired

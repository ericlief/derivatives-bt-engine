from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Underflow
import os
import time
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property

import pandas as pd
import numpy as np

from options_bt.domain.enums import *  
from options_bt.domain.base_signal_generator import BaseSignalGenerator
from options_bt.domain.strategy_config import FuturesStrategyConfig, SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

# Create logger instance
logger = setup_logger()

@dataclass
class OptionSignalGenerator(BaseSignalGenerator):
    """Class to generate signals for trading."""

    config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
    option_chain: pd.DataFrame
    underlying: pd.DataFrame
    
    # def __init__(self, 
    #              option_chain: pd.DataFrame,
    #              underlying: pd.DataFrame,
    #              config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]):
    #     super().__init__(config=config)
        
        
    def __post_init__(self):
        super().__init__(config=self.config)
        
        if not isinstance(self.config, (SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig)):
            raise ValueError("Invalid config type")

        logger.info(f"Config: {self.config}")
            
    def generate_single_leg_signals(
        self,
        option_type: OptionType,
        position_side: PositionSide,
        delta_target: Optional[float] = None,
        delta_range: Optional[Tuple[float, float]] = None,
        dte_target: Optional[int] = None,
        dte_range: Optional[Tuple[int, int]] = None,
        early_close_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Generate trade signals based on the provided parameters. These are not the actual trades,
        but rather potential trades filtered for the desired criteria. The DataFrame should have a 
        pd.DateTime index
       
        
        Args:
            option_type: Type of option (call or put)
            position_side: Position side (long or short)
            delta_target: Target delta for the option
            delta_range: Range of deltas for filtering
            dte_target: Target days to expiration
            dte_range: Range of days to expiration
            early_close_days: Days to close early
            start_date: Start date for filtering
            end_date: End date for filtering
        
        Returns:
            DataFrame containing the generated trade signals
        """
           
        logger.debug(f'Generating trade signals for {option_type}|{delta_target if delta_target else delta_range}|{dte_target if dte_target else dte_range}|{start_date if start_date else "all"}|{end_date if end_date else "all"}')
        
        # Filter by DATE range if provided
        option_chain_df = self.option_chain  # Accessing instance variable if needed
        start_date = pd.to_datetime(start_date) if start_date else option_chain_df.index.min()
        option_chain_df = option_chain_df[option_chain_df.index >= start_date]
        end_date = pd.to_datetime(end_date) if end_date else option_chain_df.index.max()
        option_chain_df = option_chain_df[option_chain_df.index <= end_date]
        logger.debug(f'Sorting for date range: {start_date}-{end_date}')
        logger.debug(f'Sample chain of length: {len(option_chain_df)}')
        logger.debug(option_chain_df.head())

        # Remove columns that are not needed
        is_put = OptionType.is_put(option_type)
        prefix = 'p_' if is_put else 'c_'
        cols = option_chain_df.columns
        needed_cols = [col for col in cols if col.startswith(prefix)]
        needed_cols.extend(['strike', 'dte', 'underlying_last', 'expire_date'])
        option_chain_df = option_chain_df[needed_cols]
        
        # Filter out options with zero or negative bids/asks
        bid_col = f'{prefix}bid'
        ask_col = f'{prefix}ask'
        option_chain_df = option_chain_df[
            (option_chain_df[bid_col] > 0) & 
            (option_chain_df[ask_col] > 0)
        ]
        
        # Filter out options with unreasonable spreads (50% max)
        option_chain_df['spread_percent'] = ((option_chain_df[ask_col] - option_chain_df[bid_col]) / option_chain_df[bid_col]) * 100
        option_chain_df = option_chain_df[option_chain_df['spread_percent'] <= 50.0]  # Max 50% spread
        
        logger.debug(f'After spread filtering: {len(option_chain_df)} options remaining')
        logger.debug(option_chain_df['spread_percent'].describe())

        # Precompute midpoint price for each row
        option_chain_df['midpoint_price'] = option_chain_df.apply(
            lambda row: PriceUtils.calculate_midpoint_price(row[bid_col], row[ask_col]),   
            axis=1  
        )
        
        # Filter by DTE based on whether we have a single value or range
        if dte_range:
            dte_mask = (option_chain_df['dte'] >= dte_range[0]) & (option_chain_df['dte'] <= dte_range[1])
            option_chain_df = option_chain_df[dte_mask]
            logger.debug(option_chain_df['dte'].describe())
            logger.debug(f'Filtering for dte range: {dte_range}')
            logger.debug(f'Sample chain of length: {len(option_chain_df)}')
            logger.debug(option_chain_df.head())
            logger.debug(option_chain_df['dte'].describe())

        elif dte_target:
            logger.debug(option_chain_df['dte'].describe())
            dte_mask = abs(option_chain_df['dte'] - dte_target) <= 2  # span of 2 otherwise gaps
            option_chain_df = option_chain_df[dte_mask]
            logger.debug(f'Filtering for dte target: {dte_target}')
            logger.debug('Sample chain')
            logger.debug(option_chain_df.head())
            logger.debug(option_chain_df['dte'].describe())

        else:
            logger.error('Need to provide either <dte_target> or <dte_range>')
            raise ValueError
        
        # Filter by delta parameters        
        delta_col = prefix + 'delta'
        logger.debug(f'Initial delta distribution')
        logger.debug(option_chain_df[delta_col].describe())
        
        if delta_range:    
            # Handle range case
            if is_put:
                min_delta = -abs(delta_range[1])  # More negative (more ITM)
                max_delta = -abs(delta_range[0])  # Less negative (more OTM)
            else:
                min_delta = abs(delta_range[0])  # Less positive (more OTM)
                max_delta = abs(delta_range[1])  # More positive (more ITM)

            logger.debug(option_chain_df[delta_col].describe())
            logger.debug(f'Filtering for delta range: {min_delta} to {max_delta} for {"put" if is_put else "call"}')
            delta_mask = option_chain_df[delta_col].between(min_delta, max_delta)
            option_chain_df = option_chain_df[delta_mask]
            logger.debug(option_chain_df[delta_col].describe())

            # Reset the index to turn the date index into a column
            option_chain_df = option_chain_df.reset_index()

            # Sort by the new date column and then by 'delta_col' or 'midpoint_price' depending on config preferences
            # For long positions: better delta match first, then lower price (less cost)
            # For short positions: better delta match first, then higher price (more premium)
            logger.info(f'Sorting according to trade strategy {self.config.trade_selection_method}:')
            logger.info(option_chain_df.head())
            if self.config.trade_selection_method == TradeSelectionMethod.DELTA_FIRST:
                option_chain_df = option_chain_df.sort_values(by=['index', delta_col, 'midpoint_price'], ascending=[True, True, True if PositionSide.is_long(position_side) else False], kind='mergesort')
            # Sort by the new date column and then by 'midpoint -> premium'
            elif self.config.trade_selection_method == TradeSelectionMethod.PREMIUM_FIRST:   
                option_chain_df = option_chain_df.sort_values(by=['index', 'midpoint_price', delta_col], ascending=[True, True if PositionSide.is_long(position_side) else False, True], kind='mergesort')
            else:
                logger.error('Cannot sort signal df because trade strategy default not defined')

            # Set the index back to the date column if needed
            option_chain_df = option_chain_df.set_index('index')

            
            logger.debug(f'Sample chain of length: {len(option_chain_df)}')
            logger.debug(option_chain_df.head())

        elif delta_target:
            # Handle target case
            if is_put:
                # For puts, we want negative deltas
                target = -abs(delta_target)
                # For puts, we want to find options with deltas closest to the target (more negative)
                # ascending = False
            else:
                # For calls, we want positive deltas
                target = abs(delta_target)
                # For calls, we want to find options with deltas closest to the target (more positive)
                # ascending = True

            logger.debug(f'Filtering for delta target: {target} for {option_type.value}')
            delta_diff = abs(option_chain_df[delta_col] - target)
            option_chain_df = option_chain_df.assign(delta_diff=delta_diff)
            
            # Filter out options that are too far from target delta (20% tolerance)
            max_delta_diff = abs(target) * 0.05  # 5% tolerance
            option_chain_df = option_chain_df[option_chain_df['delta_diff'] <= max_delta_diff]
            
            # Sort by delta difference and delta value while maintaining the date index
            # For long positions: better delta match first, then lower price (less cost)
            # For short positions: better delta match first, then higher price (more premium)
            # option_chain_df = option_chain_df.sort_values(by=['delta_diff', delta_col], ascending=[True, ascending])
            logger.info(f'Sorting according to trade strategy {self.config.trade_selection_method} for {position_side}')
            logger.info(option_chain_df.head())
            option_chain_df = option_chain_df.reset_index()
            if self.config.trade_selection_method == TradeSelectionMethod.DELTA_FIRST:
                option_chain_df = option_chain_df.sort_values(by=['index', 'delta_diff', 'midpoint_price'], ascending=[True, True, True if PositionSide.is_long(position_side) else False], kind='mergesort')
            
            # Sort by the new date column and then by 'midpoint -> premium'
            elif self.config.trade_selection_method == TradeSelectionMethod.PREMIUM_FIRST:
                option_chain_df = option_chain_df.sort_values(by=['index', 'midpoint_price', 'delta_diff'], ascending=[True, True if PositionSide.is_long(position_side) else False, True], kind='mergesort')

            elif self.config.trade_selection_method == TradeSelectionMethod.WEIGHTED:
                option_chain_df['weighted_trade_score'] = self.config.premium_weight * option_chain_df['midpoint_price'] / option_chain_df['midpoint_price'].max() + \
                    self.config.delta_weight * option_chain_df['delta_diff'] / option_chain_df['delta_diff'].max()

            else:
                logger.error('Cannot sort signal df because trade strategy default not defined')
            # option_chain_df = option_chain_df.sort_values(by=['index', 'delta_diff'], ascending=[True, True], kind='mergesort')
            option_chain_df = option_chain_df.set_index('index')
            logger.debug(f'Sample chain of length: {len(option_chain_df)}')
            logger.debug(option_chain_df.head())
        else:
            logger.error('Need to provide either delta_target or delta_range')
            raise ValueError
        
        logger.info(f"Generated {len(option_chain_df)} trade signals")
        logger.info("\nSample of trade signals:")
        logger.info(option_chain_df.head())
        
        return option_chain_df
    
    def generate_futures_signals(
        self,
        futures_type: FuturesType,
        position_side: PositionSide,
        futures_strategy: FuturesStrategy,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> pd.DataFrame:
        """
        Generate trade signals for futures positions.
        """
        logger.info(f"Generating {self.config.futures_type.value} futures signals...")
        
        if futures_type not in [FuturesType.MES]:
            raise ValueError("Invalid futures type. Supported types are: MES")
        
        if futures_strategy not in [FuturesStrategy.LONG_FUTURES, FuturesStrategy.SHORT_FUTURES]:
            raise ValueError("Invalid futures strategy. Supported strategies are: long futures, short futures")

        if position_side not in [PositionSide.LONG, PositionSide.SHORT]:
            raise ValueError("Invalid position side. Supported sides are: long, short")
                
        start_date = pd.to_datetime(start_date) if start_date else self.underlying.index.min()
        end_date = pd.to_datetime(end_date) if end_date else self.underlying.index.max()
        # print(type(start_date))
        roll_dates = self._get_quarterly_roll_dates(start_date, end_date)
        
        # Derive futures from underlying
        underlying = self.underlying[start_date:end_date]
  

        signals = []
        prev_roll = start_date - pd.Timedelta(days=1)
        for row in underlying.itertuples():
            date = row.Index
            # logger.debug(f'Processing {date}')
            for roll_date in roll_dates:
                if prev_roll  < date <= roll_date:
                    # Convert the NamedTuple 'row' to a dictionary, then to a DataFrame
                    # This preserves the original column names.
                    # ._asdict() is available on NamedTuple instances.
                    signal_data = row._asdict()
                    signal = pd.DataFrame([signal_data]).set_index('Index')
                    
                    signal['roll_date'] = roll_date
                    # Add initial_margin to the single-row DataFrame 'signal'
                    signal['initial_margin'] = self.config.futures_type.margin_required 

                    signals.append(signal)  
                    break
                else:
                    prev_roll = roll_date
                

        signals = pd.concat(signals)
        logger.info(f'Generated {len(signals)} future signals:\n {signals.head(40)}')
            
        return signals
        

    def generate_multi_leg_signals(self) -> pd.DataFrame:
        """
        Generate trade signals for option spreads by pairing legs according to the specified spread type.
        
        Args:
            option_chain: DataFrame containing options chain data
            underlying: DataFrame containing underlying price data
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
        
        Returns:
            DataFrame containing the generated spread signals with legs paired by date
        """
        logger.info(f"Generating {self.config.spread_type.value} spread signals...")
        
        if self.config.spread_type == OptionSpreadType.NONE:
            raise ValueError("Use generate_trade_signals for single-leg positions")
        
        # Generate signals for each leg separately
        leg_signals = []
        for i, leg_config in enumerate(self.config.legs):
            option_type = leg_config.option_type
            position_side = leg_config.position_side
            delta_target = leg_config.delta_target
            delta_range = leg_config.delta_range
            if delta_target is None and delta_range is None:
                logger.error(f"Leg {i+1} must have either delta_target or delta_range specified")
                return pd.DataFrame()
            dte_target = leg_config.dte_target
            dte_range = leg_config.dte_range
            if dte_target is None and dte_range is None:
                logger.error(f"Leg {i+1} must have either dte_target or dte_range specified")
                return pd.DataFrame()
            start_date = self.config.start_date
            end_date = self.config.end_date
 
   
            # Filter options chain for the 
            leg_df = self.generate_single_leg_signals(
                option_type=option_type,
                position_side=position_side,
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
            leg_df['leg_ratio'] = getattr(leg_config, 'ratio', 1)
            leg_df['delta_target'] = delta_target
            if delta_range:
                leg_df['delta_range_min'] = delta_range[0]
                leg_df['delta_range_max'] = delta_range[1]
            
            # Restore the index name
            leg_df.index.name = index_name
            
            leg_signals.append(leg_df)
        
        # No valid signals for one or more legs
        if any(df.empty for df in leg_signals):
            logger.error("One or more legs returned no signals")
            return pd.DataFrame()
        
        # Create spread signals based on the spread type
        if self.config.spread_type == OptionSpreadType.VERTICAL:
            return self._pair_vertical_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.CALENDAR:
            return self._pair_calendar_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.DIAGONAL:
            return self._pair_diagonal_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.BUTTERFLY:
            return self._pair_butterfly_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.IRON_CONDOR:
            return self._pair_iron_condor_spread_legs(leg_signals, self.config.spread_type)
        else:
            raise ValueError(f"Unsupported spread type: {self.config.spread_type}")

    def generate_straddle_signals(self, strike_price: float, expiration_date: str) -> pd.DataFrame:
        #TODO Finish
        # 
        call_signals = self.generate_single_leg_signals(
            option_type=OptionType.CALL,
            position_side=PositionSide.LONG,
            # other parameters as needed
        )
        
        put_signals = self.generate_single_leg_signals(
            option_type=OptionType.PUT,
            position_side=PositionSide.LONG,
            # other parameters as needed
        )
        
        # Combine call_signals and put_signals into a single DataFrame
        # Logic to merge or concatenate the DataFrames as needed
        combined_signals = pd.concat([call_signals, put_signals], axis=0)
        
        return combined_signals

    def _pair_vertical_spread_legs(self, leg_signals: List[pd.DataFrame], spread_type: OptionSpreadType) -> pd.DataFrame:
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
        leg1_cols = {col: f"leg1_{col}" for col in leg1.columns if col != index_name and col not in ["expire_date"]}
        leg2_cols = {col: f"leg2_{col}" for col in leg2.columns if col != index_name and col not in ["expire_date"]}
        
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

        # Drop duplicate col
        # print("Columns before drop:", paired.columns.tolist())

        # Find the actual column names
        leg1_underlying = [col for col in paired.columns if col.startswith('leg1_') and 'underlying' in col]
        leg2_underlying = [col for col in paired.columns if col.startswith('leg2_') and 'underlying' in col]

        # print("leg1_underlying columns:", leg1_underlying)
        # print("leg2_underlying columns:", leg2_underlying)

        if leg2_underlying and leg1_underlying:
            # Use the string, not the list
            paired.drop(leg2_underlying[0], axis=1, inplace=True)
            paired['underlying_last'] = paired[leg1_underlying[0]]
            paired.drop(leg1_underlying[0], axis=1, inplace=True)
            # print("Successfully handled underlying columns")
        else:
            # print("ERROR: Could not find underlying columns!")
            pass
        
        logger.debug(f"Paired {len(paired)} vertical spread legs: {paired.head()}")
        
        # Filter for valid vertical spread criteria
        # For example, ensure the strikes are different
        if len(paired) > 0:
            if spread_type == OptionSpreadType.VERTICAL:
                paired = paired[paired["leg1_strike"] != paired["leg2_strike"]]
                
                # For put vertical spreads, leg1 strike should be higher than leg2 strike for a credit spread
                if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) and PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]):
                    paired = paired[paired["leg1_strike"] > paired["leg2_strike"]]
                # For call vertical spreads, leg1 strike should be lower than leg2 strike for a credit spread
                elif OptionType.is_call(leg_signals[0].iloc[0]["option_type"]) and PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]):
                    paired = paired[paired["leg1_strike"] < paired["leg2_strike"]]
        
        # Add spread information
        paired["spread_type"] = spread_type.value
        
        # Calculate spread metrics
        paired["spread_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
        
        # Filter out spreads with excessive width if max_spread_width is set
        if hasattr(self.config, 'max_spread_width') and self.config.max_spread_width is not None:
            original_count = len(paired)
            paired = paired[paired["spread_width"] <= self.config.max_spread_width]
            filtered_count = original_count - len(paired)
            if filtered_count > 0:
                logger.info(f"Filtered out {filtered_count} spreads due to excessive width (> {self.config.max_spread_width} points)")
                logger.debug(f"Maximum spread width in remaining spreads: {paired['spread_width'].max() if len(paired) > 0 else 'N/A'} points")
        
        # Calculate leg prices
        paired["leg1_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg1_p_bid"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg2_p_bid"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        side1 = 1 if PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]) else -1
        side2 = 1 if PositionSide.is_short(leg_signals[1].iloc[0]["position_side"]) else -1
        paired["spread_price"] = side1 * paired["leg1_price"] + side2 * paired["leg2_price"]
        
        # Validate sign of premium (debit or credit)  for vertical
        is_credit_spread = self.config.option_strategy in [OptionStrategy.BEAR_CALL_CREDIT_SPREAD, OptionStrategy.BULL_PUT_CREDIT_SPREAD]
        num_signals_before = len(paired)
        if is_credit_spread:
            paired = paired[paired["spread_price"] > 0]
            if len(paired) < num_signals_before:
                logger.debug(f'Filtered {num_signals_before - len(paired)} negative priced credit spread signals')
        else:
            paired[paired["spread_price"] < 0]
            if len(paired) < num_signals_before:
                logger.debug(f'Filtered {num_signals_before - len(paired)} positive priced debit spread signals')


            # # Calculate net spread price (credit if positive, debit if negative)
        # # For credit spreads (short first leg, long second leg)
        # if PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]) and PositionSide.is_long(leg_signals[1].iloc[0]["position_side"]):
        #     paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        # # For debit spreads (long first leg, short second leg)
        # else:
        #     paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid vertical spreads")
        logger.debug(paired.head())
        
        return paired

    def _pair_calendar_spread_legs(self, leg_signals: List[pd.DataFrame], spread_type: OptionSpreadType) -> pd.DataFrame:
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
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg1_p_bid"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg2_p_bid"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price (usually a debit for a standard calendar)
        # For standard calendar spreads (short front month, long back month)
        if PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]) and PositionSide.is_long(leg_signals[1].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        # For reverse calendar spreads (long front month, short back month)
        else:
            paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid calendar spreads")
        
        return paired

    def _pair_diagonal_spread_legs(self, leg_signals: List[pd.DataFrame], spread_type: OptionSpreadType) -> pd.DataFrame:
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
        paired["spread_width"] = abs(paired["leg1_strike"] - paired["leg2_strike"])
        
        # Filter out spreads with excessive strike width if max_spread_width is set
        if hasattr(self.config, 'max_spread_width') and self.config.max_spread_width is not None:
            original_count = len(paired)
            paired = paired[paired["spread_width"] <= self.config.max_spread_width]
            filtered_count = original_count - len(paired)
            if filtered_count > 0:
                logger.info(f"Filtered out {filtered_count} diagonal spreads due to excessive strike width (> {self.config.max_spread_width} points)")
                logger.debug(f"Maximum strike width in remaining diagonal spreads: {paired['spread_width'].max() if len(paired) > 0 else 'N/A'} points")
        
        # Calculate leg prices
        paired["leg1_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg1_p_bid"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg2_p_bid"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price
        # For standard diagonal spreads (short front month, long back month)
        if PositionSide.is_short(leg_signals[0].iloc[0]["position_side"]) and PositionSide.is_long(leg_signals[1].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg2_price"] - paired["leg1_price"]
        # For reverse diagonal spreads (long front month, short back month)
        else:
            paired["spread_price"] = paired["leg1_price"] - paired["leg2_price"]
        
        # Set the index back to the date column
        paired = paired.set_index(index_name)
        
        logger.debug(f"Paired {len(paired)} valid diagonal spreads")
        
        return paired

    def _pair_butterfly_spread_legs(self, leg_signals: List[pd.DataFrame], spread_type: OptionSpreadType) -> pd.DataFrame:
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
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg1_p_bid"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_bid"],
                row["leg1_p_ask"] if OptionType.is_put(leg_signals[0].iloc[0]["option_type"]) else row["leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg2_p_bid"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_bid"],
                row["leg2_p_ask"] if OptionType.is_put(leg_signals[1].iloc[0]["option_type"]) else row["leg2_c_ask"]
            ),
            axis=1
        )
        
        paired["leg3_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["leg3_p_bid"] if OptionType.is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_bid"],
                row["leg3_p_ask"] if OptionType.is_put(leg_signals[2].iloc[0]["option_type"]) else row["leg3_c_ask"]
            ),
            axis=1
        )
        
        # Calculate net spread price
        # Long butterfly: buy wing options, sell 2x middle option
        if PositionSide.is_long(leg_signals[0].iloc[0]["position_side"]):
            paired["spread_price"] = paired["leg1_price"] - 2 * paired["leg2_price"] + paired["leg3_price"]
        # Short butterfly: sell wing options, buy 2x middle option
        else:
            paired["spread_price"] = 2 * paired["leg2_price"] - paired["leg1_price"] - paired["leg3_price"]
        
        logger.debug(f"Paired {len(paired)} valid butterfly spreads")
        
        return paired

    def _pair_iron_condor_spread_legs(self, leg_signals: List[pd.DataFrame], spread_type: OptionSpreadType) -> pd.DataFrame:
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
        
        # Filter out spreads with excessive width if max_spread_width is set
        if hasattr(self.config, 'max_spread_width') and self.config.max_spread_width is not None:
            original_count = len(paired)
            paired = paired[
                (paired["put_width"] <= self.config.max_spread_width) & 
                (paired["call_width"] <= self.config.max_spread_width)
            ]
            filtered_count = original_count - len(paired)
            if filtered_count > 0:
                logger.info(f"Filtered out {filtered_count} iron condor spreads due to excessive width (> {self.config.max_spread_width} points)")
                logger.debug(f"Maximum put width in remaining spreads: {paired['put_width'].max() if len(paired) > 0 else 'N/A'} points")
                logger.debug(f"Maximum call width in remaining spreads: {paired['call_width'].max() if len(paired) > 0 else 'N/A'} points")
        
        # Calculate leg prices
        paired["put_leg1_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["put_leg1_p_bid"],
                row["put_leg1_p_ask"]
            ),
            axis=1
        )
        
        paired["put_leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["put_leg2_p_bid"],
                row["put_leg2_p_ask"]
            ),
            axis=1
        )
        
        paired["call_leg1_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
                row["call_leg1_c_bid"],
                row["call_leg1_c_ask"]
            ),
            axis=1
        )
        
        paired["call_leg2_price"] = paired.apply(
            lambda row: PriceUtils.calculate_midpoint_price(
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

    @staticmethod
    def _get_quarterly_roll_dates(start_date: pd.Timestamp, end_date: pd.Timestamp) -> List[pd.Timestamp]:
        """
        Helper to identify quarterly roll dates (Monday prior to the third Friday of March, June, September, December).
        
        Args:
            start_date (pd.Timestamp): The start date of the backtest.
            end_date (pd.Timestamp): The end date of the backtest.

        Returns:
            List[pd.Timestamp]: A sorted list of quarterly roll dates within the specified range.
        """
        roll_dates = []
        # Futures for Equity Indices typically roll in March, June, September, December
        roll_months = [3, 6, 9, 12]

        start_year = start_date.year
        end_year = end_date.year

        for year in range(start_year, end_year + 2): # Look a bit ahead to catch rolls past end_date
            for month in roll_months:
                # Find the third Friday of the month
                current_date = pd.Timestamp(year, month, 1)
                
                # Iterate through the month to find the 3rd Friday
                friday_count = 0
                third_friday = None
                while current_date.month == month:
                    if current_date.weekday() == 4: # Friday is weekday 4
                        friday_count += 1
                        if friday_count == 3:
                            third_friday = current_date
                            break
                    current_date += pd.Timedelta(days=1)
                
                if third_friday:
                    # The roll date is the Monday prior to the third Friday
                    # Monday is weekday 0. If third_friday is for example March 21 (a Friday),
                    # going back 4 days (21-4 = 17) gets to the Monday.
                    roll_date = third_friday - pd.Timedelta(days=4)
                    
                    if start_date <= roll_date <= end_date:
                        roll_dates.append(roll_date)
        
        # Sort and return unique dates
        return sorted(list(set(roll_dates)))

    def fetch_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:    
        """
        Fetch data for a given symbol from Yahoo Finance.
        
        Args:
            symbol: The symbol of the stock to fetch data for
            start_date: The start date of the data to fetch
        """
        data = "download_data(symbol, start_date, end_date)"
        return data

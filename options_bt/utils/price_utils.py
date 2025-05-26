from options_bt.domain.enums import OptionType, PositionSide
import pandas as pd
from typing import Optional
from options_bt.utils.logger import setup_logger

logger = setup_logger()

class PriceUtils:
    @staticmethod
    def get_price(option_chain: pd.DataFrame, option_type: OptionType, strike: float, expiration: pd.Timestamp) -> float:
        """Get the price of an option from an option chain."""
        return option_chain.loc[(option_chain['option_type'] == option_type) & (option_chain['strike'] == strike) & (option_chain['expiration'] == expiration), 'price'].values[0]

    @staticmethod
    def calculate_midpoint_price(bid: float, ask: float, threshold: float = 50.0) -> Optional[float]:
        """
        Calculate the midpoint price between bid and ask, with validation.
        
        Args:
            bid: Bid price
            ask: Ask price
            threshold: Threshold for the spread percentage
        Returns:
            Optional[float]: Midpoint price if valid, None if invalid
        """
        if bid <= 0 or ask <= 0:
            return None
            
        spread_pct = ((ask - bid) / bid) * 100
        if spread_pct > threshold:  # Spread too wide (50% threshold)
            logger.warning(f"Bid-ask spread too wide: bid={bid}, ask={ask}, spread={spread_pct:.2f}%")
            return None
            
        return (bid + ask) / 2
    
    @staticmethod
    def get_signed_entry_price(entry_price: float, position_side: PositionSide) -> float:
        """
        Get the entry price with correct sign based on position side.
        - Long positions should have positive entry price (credit/STC)
        - Short positions should have negative entry price (debit/BTC)
        """
        if not entry_price:
            logger.warning(f"No entry price for position")
            return None
            
        
        return abs(entry_price) if PositionSide.is_short(position_side) else -abs(entry_price)
    
    @staticmethod
    def get_signed_exit_price(exit_price: float, position_side: PositionSide) -> float:
        """
        Get the exit price with correct sign based on position side.
        - Long positions should have positive exit price (credit/STC)
        - Short positions should have negative exit price (debit/BTC)
        """
        if exit_price is None:  # because exit may be zero for OTM options
            logger.warning(f"No exit price for position")
            return None
            
        
        return abs(exit_price) if PositionSide.is_long(position_side) else -abs(exit_price)

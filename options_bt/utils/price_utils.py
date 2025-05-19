from options_bt.domain.enums import OptionType
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
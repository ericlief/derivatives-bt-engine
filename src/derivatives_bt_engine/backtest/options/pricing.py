"""Calculate validated fills and expiration values for simulated options."""

from __future__ import annotations

import math
from typing import Optional

from derivatives_bt_engine.backtest.options.types import OptionsType
from derivatives_bt_engine.backtest.position_side import PositionSide
from derivatives_bt_engine.logging_config import setup_logger

logger = setup_logger()


class OptionPricing:
    """Stateless option-pricing conventions used by the simulation engine."""

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
        if bid is None or ask is None or (isinstance(bid, float) and math.isnan(bid)) or (isinstance(ask, float) and math.isnan(ask)):
            return None

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

    @staticmethod
    def calculate_intrinsic_value(strike: float, underlying_price: float, option_type: OptionsType) -> float:
        """Calculate intrinsic value at expiration."""

        if OptionsType.is_put(option_type):
            iv = max(0, strike - underlying_price)
        else:  # Call
            iv = max(0, underlying_price - strike)

        logger.info(f'Calculated intrinsic value for {strike} and {underlying_price} -> {iv}')
        return iv

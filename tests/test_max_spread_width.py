#!/usr/bin/env python3
"""
Test script to demonstrate the new max_spread_width parameter functionality.
This shows how to limit spread widths to prevent excessive margin requirements.
"""

from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.enums import OptionsStrategy, OptionSpreadType, OptionsType, PositionSide
from options_bt.domain.option_leg_config import OptionLegConfig

def test_max_spread_width_config():
    """Test creating a configuration with max_spread_width parameter."""
    
    # Create option leg configurations for a vertical put spread
    leg1 = OptionLegConfig(
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        delta_target=-0.30,
        dte_target=45
    )
    
    leg2 = OptionLegConfig(
        option_type=OptionsType.PUT,
        position_side=PositionSide.LONG,
        delta_target=-0.15,
        dte_target=45
    )
    
    # Create strategy config WITHOUT max_spread_width (unlimited)
    config_unlimited = MultiLegOptionStrategyConfig(
        option_strategy=OptionsStrategy.BEAR_CALL_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        legs=[leg1, leg2],
        quantity=1,
        max_spread_width=None  # No limit
    )
    
    print("Config WITHOUT max_spread_width:")
    print(f"  max_spread_width: {config_unlimited.max_spread_width}")
    print(f"  This allows unlimited spread widths (dangerous for SPX!)")
    print()
    
    # Create strategy config WITH max_spread_width limit
    config_limited = MultiLegOptionStrategyConfig(
        option_strategy=OptionsStrategy.BULL_PUT_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        legs=[leg1, leg2],
        quantity=1,
        max_spread_width=50.0  # Limit to 50 points = $5000 max margin
    )
    
    print("Config WITH max_spread_width:")
    print(f"  max_spread_width: {config_limited.max_spread_width}")
    print(f"  This limits spread width to {config_limited.max_spread_width} points")
    print(f"  Maximum margin requirement: ${config_limited.max_spread_width * 100:.0f}")
    print()
    
    # Example calculations
    print("Example margin calculations:")
    print(f"  SPX spread width 50 points: ${50 * 100:,.0f} margin")
    print(f"  SPX spread width 100 points: ${100 * 100:,.0f} margin")
    print(f"  SPX spread width 800 points: ${800 * 100:,.0f} margin (INSANE!)")
    print()
    
    print("Recommendations:")
    print(f"  - For SPX: Use max_spread_width <= 50-100 points")
    print(f"  - For SPY: Use max_spread_width <= 5-10 points")
    print(f"  - For QQQ: Use max_spread_width <= 10-20 points")
    print(f"  - For IWM: Use max_spread_width <= 5-15 points")

if __name__ == "__main__":
    test_max_spread_width_config()

from options_bt.domain.enums import *
from dataclasses import dataclass
from typing import Optional, List
from abc import ABC, abstractmethod
from options_bt.domain.option_leg_config import OptionLegConfig
from typing import List


@dataclass
class BaseStrategyConfig(ABC):
    """Configuration for a trading strategy."""
    quantity: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_positions: int = 1
    initial_capital: float = 100000
    leverage: float = 1
    max_margin_utilization: float = 0.80
 
@dataclass(kw_only=True)
class BaseOptionStrategyConfig(BaseStrategyConfig, ABC):
    option_strategy: OptionStrategy
    use_underlying_close: bool = False
    early_close_days: Optional[int] = None

@dataclass(kw_only=True)
class SingleLegOptionStrategyConfig(BaseOptionStrategyConfig):
    """Configuration for an option strategy."""

    # leg: OptionLegConfig = field(default_factory=OptionLegConfig)
    leg: OptionLegConfig 

    def __post_init__(self):
        """
        Option strategy types for single leg:
        SHORT_PUT = "short_put"
        LONG_PUT = "long_put"
        SHORT_CALL = "short_call"
        LONG_CALL = "long_call"
        """
        if self.option_strategy == OptionStrategy.LONG_CALL:
            if self.leg.option_type != OptionType.CALL or self.leg.position_side != PositionSide.LONG:
                raise ValueError(f"Option strategy {self.option_strategy} requires one long call leg")
        elif self.option_strategy == OptionStrategy.LONG_PUT:
            if self.leg.option_type != OptionType.PUT or self.leg.position_side != PositionSide.LONG:
                raise ValueError(f"Option strategy {self.option_strategy} requires one long put leg")
        elif self.option_strategy == OptionStrategy.SHORT_CALL:
            if self.leg.option_type != OptionType.CALL or self.leg.position_side != PositionSide.SHORT:
                raise ValueError(f"Option strategy {self.option_strategy} requires one short call leg")        
        elif self.option_strategy == OptionStrategy.SHORT_PUT:
            if self.leg.option_type != OptionType.PUT or self.leg.position_side != PositionSide.SHORT:
                raise ValueError(f"Option strategy {self.option_strategy} requires one short put leg")
        else:
            raise ValueError("Unknown single-leg option strategy")
        
@dataclass(kw_only=True)
class MultiLegOptionStrategyConfig(BaseOptionStrategyConfig):
    spread_type: OptionSpreadType
    legs: List[OptionLegConfig]
    ratio: float = 1.0
    
    def __post_init__(self):
        # Validate legs configuration
        for leg in self.legs:
            if not hasattr(leg, 'option_type') or not hasattr(leg, 'position_side'):
                raise ValueError("Each leg must have 'option_type' and 'position_side' defined")
   
        # Not sure if we should derive ratio form leg quantity here or in the leg config
        
        # Validate spread type
        if self.spread_type not in [OptionSpreadType.VERTICAL, OptionSpreadType.CALENDAR, OptionSpreadType.DIAGONAL, OptionSpreadType.IRON_CONDOR, OptionSpreadType.BUTTERFLY]:
            raise ValueError("Invalid spread type. Supported types are: vertical, calendar, diagonal, iron_condor, butterfly")
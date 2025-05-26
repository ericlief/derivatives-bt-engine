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


@dataclass(kw_only=True)
class MultiLegOptionStrategyConfig(BaseOptionStrategyConfig):
    spread_type: OptionSpreadType
    legs: List[OptionLegConfig]
    ratio: float = 1.0
    
    def __post_init__(self):
        # Validate legs configuration
        for leg in self.legs:
            if 'option_type' not in leg or 'position_side' not in leg:
                raise ValueError("Each leg must have 'option_type' and 'position_side' defined")
   
        # Not sure if we should derive ratio form leg quantity here or in the leg config
        
        # Validate spread type
        if self.spread_type not in [OptionSpreadType.VERTICAL, OptionSpreadType.CALENDAR, OptionSpreadType.DIAGONAL, OptionSpreadType.IRON_CONDOR, OptionSpreadType.BUTTERFLY]:
            raise ValueError("Invalid spread type. Supported types are: vertical, calendar, diagonal, iron_condor, butterfly")
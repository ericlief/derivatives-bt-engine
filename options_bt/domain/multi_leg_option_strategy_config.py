from dataclasses import dataclass
from typing import Optional, Tuple, List    
from options_bt.domain.enums import OptionSpreadType
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.base_option_strategy_config import BaseOptionStrategyConfig

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
from options_bt.domain.enums import BaseStrategyType
from dataclasses import dataclass

@dataclass
class BaseStrategyConfig:
    """Configuration for a trading strategy."""
    strategy_type: BaseStrategyType
    name: str
   

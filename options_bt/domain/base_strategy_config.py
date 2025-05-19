from options_bt.domain.enums import *
from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod

@dataclass
class BaseStrategyConfig(ABC):
    """Configuration for a trading strategy."""
    strategy: BaseStrategy
    quantity: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_positions: int = 1
    initial_capital: float = 100000
    leverage: float = 1
    max_margin_utilization: float = 0.80
 

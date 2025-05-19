from options_bt.domain.base_strategy_config import BaseStrategyConfig
from options_bt.domain.enums import *
from dataclasses import dataclass
from abc import ABC, abstractmethod

@dataclass(kw_only=True)
class BaseOptionStrategyConfig(BaseStrategyConfig, ABC):
    strategy: OptionStrategy
    use_underlying_close: bool = False
    early_close_days: Optional[int] = None

from options_bt.domain.base_strategy_config import BaseStrategyConfig
from options_bt.domain.enums import *
from dataclasses import dataclass, field
from options_bt.domain.base_option_strategy_config import BaseOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig


@dataclass(kw_only=True)
class SingleLegOptionStrategyConfig(BaseOptionStrategyConfig):
    """Configuration for an option strategy."""

    # leg: OptionLegConfig = field(default_factory=OptionLegConfig)
    leg: OptionLegConfig 


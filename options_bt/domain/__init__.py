"""
This module provides the core classes for the options trading domain.
# """

from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.trade_result import OptionTradeResult    
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.backtester import Backtester
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.enums import *

__all__ = ['SingleLegOptionPosition', 'MultiLegOptionPosition', 'OptionsDataLoader', 'Backtester', 'TradeManager', 'OptionSignalGenerator', 'OptionsType', 'PositionSide', 'OptionSpreadType', 'OptionTrade', 'OptionLegConfig', 'SingleLegOptionStrategyConfig', 'MultiLegOptionStrategyConfig', 'OptionTradeResult']
    

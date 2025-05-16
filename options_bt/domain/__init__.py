"""
This module provides the core classes for the options trading domain.
"""

from options_bt.domain.option_position import OptionPosition
from options_bt.domain.spread import Spread
from options_bt.domain.dataloader import DataLoader
from options_bt.domain.backtester import Backtester
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.option_trade import OptionTrade
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.enums import OptionType, PositionSide, SpreadType

__all__ = ['OptionPosition', 'Spread', 'DataLoader', 'Backtester', 'TradeManager', 'OptionSignalGenerator', 'OptionType', 'PositionSide', 'SpreadType', 'OptionTrade']
 

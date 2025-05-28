# tests/domain/test_position.py
import pytest
import pandas as pd
import numpy as np
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.domain.enums import *
from options_bt.domain.trade_manager import TradeManager

from options_bt.domain.dataloader import DataLoader
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from scipy.stats import norm
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

logger = setup_logger()


@pytest.fixture(scope='module')
def setup(mock_backtester, mock_option_signal_generator, mock_data):

    # Instantiate class
     mock_bt = mock_backtester.return_value
     

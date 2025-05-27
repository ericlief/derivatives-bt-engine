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

    config = SingleLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionStrategy.SHORT_CALL,
        initial_capital=100000,
        leverage=1.0,
        start_date="2023-01-01",
        end_date="2023-01-31",
        use_underlying_close=False,
        early_close_days=30,
        max_margin_utilization=0.80,
        max_positions=1,
        # Define the leg of the strategy
        leg=OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.SHORT,
            delta_target=0.30,
            dte_target=30,
            )
            
    )
    tm = TradeManager(config)
    # mock_backtester.return_value.


    
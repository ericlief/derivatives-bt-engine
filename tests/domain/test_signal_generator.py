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
def setup(mock_data):
    pd.set_option('display.max_columns', None)  # Show all columns
    pd.set_option('display.max_colwidth', None)
    # mock_backtester.return_value.

    # Set up data and signal mocking
    option_chain = mock_data['option_chain']
    underlying = mock_data['underlying_price_history']
    logger.info(f'Starting SignalGenerator unittest')
    logger.info('Option chain:')
    logger.info(option_chain.head())
    logger.info('Underlying:')
    logger.info(underlying.head())

    return option_chain, underlying

def validate_dte(signals, config):

    # Check dte target
    if config.leg.dte_target is not None:
        assert all(signals.dte.between(config.leg.dte_target - 1, config.leg.dte_target + 1))
    
    elif config.leg.dte_range is not None:
        assert all(signals.dte.between(*config.leg.dte_range))
    else:
        raise ValueError(f'Neither *_dte_target_* nor *_dte_range_* specified in config: {config}')

def validate_delta(signals, config):

    # Check Single legs
    if isinstance(config, SingleLegOptionStrategyConfig):
        is_put = OptionType.is_put(config.leg.option_type)      
        delta_col = 'p_delta' if is_put else 'c_delta'
    
        # Check delta target
        if config.leg.delta_target is not None:
            target = abs(config.leg.delta_target)  # Use absolute value of the target
            tolerance = 0.20 * target  # 20% tolerance
            min_delta = target - tolerance
            max_delta = target + tolerance

            if is_put:
                # Adjust for put options
                tmp = min_delta
                min_delta = -max_delta
                max_delta = -tmp

            # Assert that the delta values are within the specified range
            logger.debug(f'Testing delta target with a tolerance of {min_delta}-{max_delta}')
            assert all(signals[delta_col].between(min_delta, max_delta))
        
        # Handle range case
        elif config.leg.delta_range is not None:

            if is_put:
                min_delta = -abs(config.leg.delta_range[1])  # More negative (more ITM)
                max_delta = -abs(config.leg.delta_range[0])  # Less negative (more OTM)
            else:
                min_delta = abs(config.leg.delta_range[0])  # Less positive (more OTM)
                max_delta = abs(config.leg.delta_range[1])  # More positive (more ITM)

            assert all(signals[delta_col].between(min_delta, max_delta))

        else:
            raise ValueError(f'Neither *_delta_target_* nor *_delta_range_* specified in config: {config}')

    else:
        #TODO
        pass



def test_signals_single_leg_targets(setup):
     
    option_chain, underlying = setup

    config = SingleLegOptionStrategyConfig(
            quantity=1,
            option_strategy=OptionStrategy.LONG_CALL,
            initial_capital=100000,
            leverage=1.0,
            start_date="2023-01-01",
            end_date="2023-01-31",
            use_underlying_close=False,
            max_margin_utilization=0.80,
            max_positions=5,
            # Define the leg of the strategy
            leg=OptionLegConfig(
                option_type=OptionType.CALL,
                position_side=PositionSide.LONG,
                delta_target=0.30,
                dte_target=30,
                )
    )
    sg = OptionSignalGenerator(option_chain=option_chain, underlying=underlying, config=config)
    signals = sg.generate_single_leg_signals()
    validate_dte(signals, config)
    validate_delta(signals, config)

    config = SingleLegOptionStrategyConfig(
            quantity=1,
            option_strategy=OptionStrategy.SHORT_PUT,
            initial_capital=100000,
            leverage=1.0,
            start_date="2023-01-01",
            end_date="2023-01-31",
            use_underlying_close=False,
            max_margin_utilization=0.80,
            max_positions=5,
            # Define the leg of the strategy
            leg=OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.SHORT,
                delta_target=0.30,
                dte_target=30,
                )
    )
    sg = OptionSignalGenerator(option_chain=option_chain, underlying=underlying, config=config)
    signals = sg.generate_single_leg_signals()
    validate_dte(signals, config)
    validate_delta(signals, config)

def test_signals_single_leg_ranges(setup):
     
    option_chain, underlying = setup


    # Test call
    config = SingleLegOptionStrategyConfig(
            quantity=1,
            option_strategy=OptionStrategy.LONG_CALL,
            initial_capital=100000,
            leverage=1.0,
            start_date="2023-01-01",
            end_date="2023-01-31",
            use_underlying_close=False,
            max_margin_utilization=0.80,
            max_positions=5,
            # Define the leg of the strategy
            leg=OptionLegConfig(
                option_type=OptionType.CALL,
                position_side=PositionSide.LONG,
                delta_range=(0.25, 0.35),
                dte_range=(30, 35)
                )
    )
    sg = OptionSignalGenerator(option_chain=option_chain, underlying=underlying, config=config)
    signals = sg.generate_single_leg_signals()
    validate_dte(signals, config)
    validate_delta(signals, config)
    
    # Test put
    config = SingleLegOptionStrategyConfig(
            quantity=1,
            option_strategy=OptionStrategy.SHORT_PUT,
            initial_capital=100000,
            leverage=1.0,
            start_date="2023-01-01",
            end_date="2023-01-31",
            use_underlying_close=False,
            max_margin_utilization=0.80,
            max_positions=5,
            # Define the leg of the strategy
            leg=OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.SHORT,
                delta_range=(0.25, 0.35),
                dte_range=(30, 35)
                )
    )
    sg = OptionSignalGenerator(option_chain=option_chain, underlying=underlying, config=config)
    signals = sg.generate_single_leg_signals()
    validate_dte(signals, config)
    validate_delta(signals, config)
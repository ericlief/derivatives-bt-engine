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
    logger.info(f'Starting unittest')
    logger.info('Option chain:')
    logger.info(option_chain.head())
    logger.info('Underlying:')
    logger.info(underlying.head())

    return option_chain, underlying

def basic_filter_signals(config, signals):
        
    # Precompute midpoint price for each row
    bid_col = 'c_bid' if OptionType.is_call(config.leg.option_type) else 'p_bid'
    ask_col = 'c_ask' if OptionType.is_call(config.leg.option_type) else 'p_ask'

    signals['midpoint_price'] = signals.apply(
                                    lambda row: PriceUtils.calculate_midpoint_price(row[bid_col], row[ask_col]),
                                        axis=1
    )
    signals['margin_required'] = signals.apply(
                lambda row: SingleLegOptionPosition.calculate_margin(
                    quantity=config.quantity,
                    option_type=config.leg.option_type,
                    position_side=config.leg.position_side,
                    entry_price=row['midpoint_price'],
                    strike=row['strike'],
                    underlying_price=row['underlying_last'],
                    leverage=config.leverage
                    ), 
                axis=1
            )
    return signals

def test_construct_trades_long_call(setup):

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
    option_chain, underlying = setup 

    signals = option_chain[option_chain.c_delta.between(0.25, 0.35)]
    signals = basic_filter_signals(config, signals)
    # Construct trades
    # max_allowed_margin = config.max_margin_utilization * config.initial_capital * config.leverage
    tm = TradeManager(config)
    logger.info(f'Setup for {config.option_strategy} strategy , BP: {tm.option_bp}, Init Cap: {tm.initial_capital}')
    results = tm.construct_and_execute_trades_from_signals(signals, option_chain, underlying)
    assert 'trade_results' in results
    assert 'transactions' in results
    trade_results = results['trade_results']
    transactions = results['transactions']
    assert not trade_results.empty
    assert not transactions.empty

    trade_results['cum_pnl'] = round(trade_results['pnl'].cumsum(), 2)
    total_pnl = trade_results['cum_pnl'].iloc[-1]
    logger.debug(f'Total pnl {total_pnl}')
    total_fees = trade_results['fees'].sum()
    logger.debug(f'Total fees {total_fees}')
    # Check BP
    assert tm.option_bp == tm.initial_capital + total_pnl  # - total_fees

    # For debugging
    logger.info(trade_results.head())
    logger.info(transactions.head())
# @pytest.fixture(scope="module")
def test_construct_trades_short_call(setup):

    config = SingleLegOptionStrategyConfig(
            quantity=1,
            option_strategy=OptionStrategy.SHORT_CALL,
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
                position_side=PositionSide.SHORT,
                delta_target=0.30,
                dte_target=30,
            )
    )
    option_chain, underlying = setup 
    signals = option_chain[option_chain.c_delta.between(0.25, 0.35)]
    signals = basic_filter_signals(config, signals)
    tm = TradeManager(config)
    logger.info(f'Setup for {config.option_strategy} strategy , BP: {tm.option_bp}, Init Cap: {tm.initial_capital}')
     
    # Construct trades
    # max_allowed_margin = config.max_margin_utilization * config.initial_capital * config.leverage

    results = tm.construct_and_execute_trades_from_signals(signals, option_chain, underlying)
    assert 'trade_results' in results
    assert 'transactions' in results
    trade_results = results['trade_results']
    transactions = results['transactions']
    assert not trade_results.empty
    assert not transactions.empty

    trade_results['cum_pnl'] = round(trade_results['pnl'].cumsum(), 2)
    total_pnl = trade_results['cum_pnl'].iloc[-1]
    logger.debug(f'Total pnl {total_pnl}')
    total_fees = trade_results['fees'].sum()
    logger.debug(f'Total fees {total_fees}')
    # Check BP
    assert tm.option_bp == tm.initial_capital + total_pnl  # - total_fees

    # For debugging
    logger.info(trade_results.head())
    logger.info(transactions.head())

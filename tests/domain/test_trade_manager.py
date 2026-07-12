# tests/domain/test_trade_manager.py
import pytest
import numpy as np
import polars as pl
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.domain.enums import *
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from scipy.stats import norm
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

logger = setup_logger()


@pytest.fixture(scope='module')
def setup(mock_data):
    option_chain = mock_data['option_chain']
    underlying = mock_data['underlying_price_history']
    logger.info('Starting unittest')
    logger.info(f'Option chain:\n{option_chain.head()}')
    logger.info(f'Underlying:\n{underlying.head()}')
    return option_chain, underlying


def basic_filter_signals(config, signals: pl.DataFrame) -> pl.DataFrame:
    bid_col = 'c_bid' if OptionType.is_call(config.leg.option_type) else 'p_bid'
    ask_col = 'c_ask' if OptionType.is_call(config.leg.option_type) else 'p_ask'

    signals = signals.with_columns(
        pl.struct([bid_col, ask_col]).map_elements(
            lambda r: PriceUtils.calculate_midpoint_price(r[bid_col], r[ask_col]),
            return_dtype=pl.Float64
        ).alias('midpoint_price')
    )
    signals = signals.with_columns(
        pl.struct(['midpoint_price', 'strike', 'underlying_last']).map_elements(
            lambda r: SingleLegOptionPosition.calculate_margin(
                quantity=config.quantity,
                option_type=config.leg.option_type,
                position_side=config.leg.position_side,
                entry_price=r['midpoint_price'],
                strike=r['strike'],
                underlying_price=r['underlying_last'],
                leverage=config.leverage,
            ),
            return_dtype=pl.Float64
        ).alias('margin_required')
    )
    return signals


def validate_results(tm: TradeManager, results: dict) -> None:
    assert 'trade_results' in results
    assert 'transactions' in results
    trade_results: pl.DataFrame = results['trade_results']
    transactions: pl.DataFrame = results['transactions']
    assert not trade_results.is_empty()
    assert not transactions.is_empty()

    init_cap = tm.initial_capital
    logger.debug(f'Init cap: {init_cap}')
    total_pnl = round(trade_results['pnl'].sum(), 2)
    logger.debug(f'Total pnl {total_pnl}')
    total_fees = trade_results['fees'].sum()
    logger.debug(f'Total fees {total_fees}')
    final_cap = round(init_cap + total_pnl, 2)
    logger.debug(f'Final cap: {final_cap}')
    final_bp = tm.bp
    assert final_bp == init_cap + total_pnl
    assert final_cap == final_bp

    logger.info(trade_results.head())
    logger.info(transactions.head())


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
        leg=OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.LONG,
            delta_target=0.30,
            dte_target=30,
        )
    )
    option_chain, underlying = setup
    signals = option_chain.filter(pl.col('c_delta').is_between(0.25, 0.35))
    signals = basic_filter_signals(config, signals)
    tm = TradeManager(config)
    logger.info(f'Setup for {config.option_strategy} strategy, BP: {tm.bp}, Init Cap: {tm.initial_capital}')
    results = tm.construct_and_execute_trades_from_signals(signals, option_chain, underlying)
    validate_results(tm, results)


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
        leg=OptionLegConfig(
            option_type=OptionType.CALL,
            position_side=PositionSide.SHORT,
            delta_target=0.30,
            dte_target=30,
        )
    )
    option_chain, underlying = setup
    signals = option_chain.filter(pl.col('c_delta').is_between(0.25, 0.35))
    signals = basic_filter_signals(config, signals)
    tm = TradeManager(config)
    logger.info(f'Setup for {config.option_strategy} strategy, BP: {tm.bp}, Init Cap: {tm.initial_capital}')
    results = tm.construct_and_execute_trades_from_signals(signals, option_chain, underlying)
    validate_results(tm, results)

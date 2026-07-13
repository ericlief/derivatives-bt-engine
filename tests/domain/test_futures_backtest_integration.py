"""
End-to-end smoke test for the futures backtest harness: FuturesDataLoader ->
Backtester -> TradeManager -> FuturesPosition, against the real CME Globex
duckdb. Skipped if that db isn't available in the current environment.
"""
import os

import pytest

from options_bt.domain.backtester import Backtester
from options_bt.domain.enums import FuturesStrategy
from options_bt.domain.futures_dataloader import FuturesDataLoader
from options_bt.domain.strategy_config import FuturesStrategyConfig

GLOBEX_DB_PATH = "/home/dev/fin/db/globex_mdp_3.0.duckdb"

pytestmark = pytest.mark.skipif(
    not os.path.exists(GLOBEX_DB_PATH),
    reason=f"CME Globex db not available at {GLOBEX_DB_PATH}",
)


@pytest.fixture(scope='module')
def es_data():
    dl = FuturesDataLoader(asset='ES', db_path=GLOBEX_DB_PATH,
                           use_preprocessed=False, save_preprocessed=False)
    return dl.load_data()


def test_futures_dataloader_shape_matches_backtester_contract(es_data):
    # Backtester.__init__ unconditionally reads these four keys regardless
    # of strategy type.
    for key in ('option_chain', 'option_chain_multi_index', 'underlying', 'vix'):
        assert key in es_data
    assert es_data['underlying'].height > 0
    assert 'close' in es_data['underlying'].columns


def test_long_futures_backtest_runs_end_to_end(es_data):
    config = FuturesStrategyConfig(
        quantity=1,
        futures_type='ES',
        futures_strategy=FuturesStrategy.LONG_FUTURES,
        initial_capital=100000,
        leverage=1.0,
        start_date="2023-01-01",
        end_date="2023-06-30",
    )

    bt = Backtester(data=es_data, save_trades=False, log_to_sheets=False)
    results = bt.run(config)

    trade_results = results['trade_results']
    transactions = results['transactions']

    assert not trade_results.empty
    assert not transactions.empty

    for col in ('trade_id', 'pnl', 'cumulative_pnl', 'capital', 'fees', 'roi'):
        assert col in trade_results.columns

    # capital should track cumulative pnl off the configured starting capital
    assert trade_results['capital'].iloc[0] == pytest.approx(
        config.initial_capital + trade_results['cumulative_pnl'].iloc[0]
    )


def test_short_futures_backtest_runs_end_to_end(es_data):
    config = FuturesStrategyConfig(
        quantity=1,
        futures_type='ES',
        futures_strategy=FuturesStrategy.SHORT_FUTURES,
        initial_capital=100000,
        leverage=1.0,
        start_date="2023-01-01",
        end_date="2023-06-30",
    )

    bt = Backtester(data=es_data, save_trades=False, log_to_sheets=False)
    results = bt.run(config)

    assert not results['trade_results'].empty

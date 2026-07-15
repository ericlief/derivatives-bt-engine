"""
End-to-end smoke test for the futures backtest harness: FuturesDataLoader ->
Backtester -> TradeManager -> FuturesPosition, against the real CME Globex
duckdb. Skipped if that db isn't available in the current environment.
"""
import os

import polars as pl
import pytest

from derivatives_bt_engine.domain.backtester import Backtester
from derivatives_bt_engine.domain.enums import FuturesStrategy
from derivatives_bt_engine.domain.futures_dataloader import FuturesDataLoader
from derivatives_bt_engine.domain.instruments import resolve_price_symbol
from derivatives_bt_engine.domain.strategy_config import FuturesStrategyConfig

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


@pytest.fixture(scope='module', params=['MES', 'MGC'])
def gated_mtm_results(request):
    """A multi-year run with the signal gate enabled so the trade sequence
    includes both same-day close+reopen transitions (natural quarterly
    rolls, gate_reason is null) and multi-day-gap transitions (signal-gate
    -triggered exits followed later by a re-entry once ts_entry_threshold
    clears) -- exercising both cases the mtm join fix needs to get right,
    not just rolls in isolation. Parametrized across two symbols (MES/MGC,
    same convention naked_futures.py uses: load price history via
    resolve_price_symbol -- these micros have no native db history, they
    borrow ES/GC's -- while futures_type stays the raw micro ticker so
    sizing/margin/multiplier come from the micro's own instruments.py
    spec) rather than just one, since the bug this guards against is a
    join-key issue that doesn't depend on any one symbol's own price
    series -- a single-symbol pass wouldn't catch a symbol-specific
    regression in how price data is resolved/joined."""
    symbol = request.param
    price_symbol = resolve_price_symbol(symbol)
    dl = FuturesDataLoader(asset=price_symbol, db_path=GLOBEX_DB_PATH,
                           use_preprocessed=False, save_preprocessed=False)
    data = dl.load_data()
    config = FuturesStrategyConfig(
        quantity=1,
        futures_type=symbol,
        futures_strategy=FuturesStrategy.LONG_FUTURES,
        initial_capital=100000,
        leverage=1.0,
        start_date="2010-01-01",
        end_date="2018-12-31",
        fill_price='mid',
        ts_exit_threshold=0.0,
        ts_entry_threshold=0.5,
    )
    bt = Backtester(data=data, save_trades=False, log_to_sheets=False)
    result = bt.run(config)
    result['_symbol'] = symbol
    return result


def test_mtm_telescopes_through_every_trade_close(gated_mtm_results):
    """Regression test for the join_asof(right_on='opened') bug: every
    trade's OWN closing date must resolve to that trade's own realized
    capital in the daily mtm overlay, whether the next trade reopens the
    same day (a roll) or days/weeks later (a gate-triggered re-entry) --
    not just the first trade in the sequence."""
    trade_results = gated_mtm_results['trade_results']
    stats = gated_mtm_results['stats']
    assert trade_results.height > 5  # need a real multi-trade sequence, not a degenerate 0/1-trade run

    stats_by_date = {row['date']: row['capital'] for row in stats.iter_rows(named=True)}

    mismatches = []
    for row in trade_results.iter_rows(named=True):
        mtm_capital_at_close = stats_by_date.get(row['closed'])
        if mtm_capital_at_close is None:
            continue  # closed date fell outside the stats window (shouldn't happen here, but don't hide a KeyError as a failure of the invariant)
        if mtm_capital_at_close != pytest.approx(row['capital'], abs=0.01):
            mismatches.append((row['trade_id'], row['closed'], row['close_reason'], mtm_capital_at_close, row['capital']))

    assert not mismatches, (
        f"[{gated_mtm_results['_symbol']}] {len(mismatches)} trade(s) whose closing-date mtm capital "
        f"doesn't match their own realized capital "
        f"(trade_id, closed, close_reason, mtm_capital, realized_capital): {mismatches}"
    )


def test_mtm_flat_during_gate_gap(gated_mtm_results):
    """Between one trade's close and the next trade's (later) open -- a
    genuine gap, only possible via a gate-triggered exit since a natural
    roll always reopens same-day -- mtm_capital must stay flat at the
    closed trade's own realized capital, not drift."""
    trade_results = gated_mtm_results['trade_results'].sort('opened')
    stats = gated_mtm_results['stats']
    rows = list(trade_results.iter_rows(named=True))
    assert len(rows) > 5

    gaps_checked = 0
    for prev_row, next_row in zip(rows, rows[1:]):
        if next_row['opened'] <= prev_row['closed']:
            continue  # same-day roll, not a gap -- covered by the telescoping test above
        gap = stats.filter(
            (pl.col('date') > prev_row['closed']) & (pl.col('date') < next_row['opened'])
        )
        if gap.height == 0:
            continue
        gaps_checked += 1
        assert gap['capital'].to_list() == pytest.approx([prev_row['capital']] * gap.height, abs=0.01), (
            f"[{gated_mtm_results['_symbol']}] mtm_capital drifted during the flat gap after trade "
            f"{prev_row['trade_id']} ({prev_row['closed']}) before trade {next_row['trade_id']} opens ({next_row['opened']})"
        )
        assert (gap['mtm_pnl'].abs() < 0.01).all()

    assert gaps_checked > 0, (
        f"[{gated_mtm_results['_symbol']}] expected at least one gate-triggered gap in this window "
        "to actually test flatness against"
    )


def test_mtm_total_matches_realized_total(gated_mtm_results):
    """Aggregate check: summed daily mtm_pnl across the whole backtest
    must equal the final realized cumulative pnl -- the same telescoping
    property as the per-trade test, just end to end over the full window
    instead of trade by trade."""
    trade_results = gated_mtm_results['trade_results']
    stats = gated_mtm_results['stats']

    total_realized_pnl = trade_results['cumulative_pnl'][-1]
    total_mtm_pnl = stats['mtm_pnl'].sum()

    assert total_mtm_pnl == pytest.approx(total_realized_pnl, abs=0.01), gated_mtm_results['_symbol']
    # cum_pnl is itself just a running sum of mtm_pnl -- its final value
    # should agree with the same total via an independent column, not
    # just the .sum() above re-deriving the same number.
    assert stats['cum_pnl'][-1] == pytest.approx(total_realized_pnl, abs=0.01), gated_mtm_results['_symbol']

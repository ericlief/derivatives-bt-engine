"""
Regression test for naked_futures.py's combined total-mtm output
(_build_total_mtm): the existing domain-level mtm tests
(tests/domain/test_futures_backtest_integration.py) only ever run one
symbol at a time -- they never check that the AGGREGATE across multiple
symbols actually equals the sum of each symbol's own independent series.
Against the real CME Globex duckdb. Skipped if that db isn't available.
"""
import os
from argparse import Namespace

import polars as pl
import pytest

from derivatives_bt_engine.strats.naked_futures import _build_total_mtm, _run_one_symbol

GLOBEX_DB_PATH = "/home/dev/fin/db/globex_mdp_3.0.duckdb"

pytestmark = pytest.mark.skipif(
    not os.path.exists(GLOBEX_DB_PATH),
    reason=f"CME Globex db not available at {GLOBEX_DB_PATH}",
)

SYMBOLS = ['MES', 'MGC']
INITIAL_CAPITAL = 100_000.0


@pytest.fixture(scope='module')
def two_symbol_stats():
    """Runs the exact _run_one_symbol path naked_futures.py's own main()
    uses (not a hand-rolled reimplementation), for two symbols with
    different underlying price series (MES->ES, MGC->GC) and a gate
    active so each has a real, non-trivial multi-trade sequence."""
    args = Namespace(
        dir='long', years='2010-2026', quantity=1,
        initial_capital=INITIAL_CAPITAL, leverage=1.0,
        ts_exit_threshold=0.0, ts_entry_threshold=0.5,
        exit_on_ts_crossover=False, no_save=True,
    )
    stats_by_symbol = {}
    for symbol in SYMBOLS:
        _, stats = _run_one_symbol(symbol, args)
        assert stats.height > 100, f"{symbol}: expected a real multi-year daily stats series"
        stats_by_symbol[symbol] = stats
    return stats_by_symbol


def test_total_mtm_equals_sum_of_each_symbol_every_date(two_symbol_stats):
    """The actual ask: total_capital/total_mtm_pnl at EVERY date must
    equal the sum of each individual symbol's own capital/mtm_pnl that
    date -- not just checked in isolation per symbol."""
    total_mtm = _build_total_mtm(SYMBOLS, two_symbol_stats, INITIAL_CAPITAL)
    assert total_mtm.height > 100

    expected_capital = pl.sum_horizontal([f'capital_{s}' for s in SYMBOLS])
    expected_pnl = pl.sum_horizontal([f'mtm_pnl_{s}' for s in SYMBOLS])
    checked = total_mtm.with_columns(
        expected_capital=expected_capital, expected_pnl=expected_pnl,
    )

    capital_mismatches = checked.filter(
        (pl.col('total_capital') - pl.col('expected_capital')).abs() > 0.01
    )
    pnl_mismatches = checked.filter(
        (pl.col('total_mtm_pnl') - pl.col('expected_pnl')).abs() > 0.01
    )
    assert capital_mismatches.height == 0, (
        f"{capital_mismatches.height} date(s) where total_capital != sum of per-symbol capital:\n"
        f"{capital_mismatches.select(['date', 'total_capital', 'expected_capital'] + [f'capital_{s}' for s in SYMBOLS])}"
    )
    assert pnl_mismatches.height == 0, (
        f"{pnl_mismatches.height} date(s) where total_mtm_pnl != sum of per-symbol mtm_pnl:\n"
        f"{pnl_mismatches.select(['date', 'total_mtm_pnl', 'expected_pnl'] + [f'mtm_pnl_{s}' for s in SYMBOLS])}"
    )


def test_total_mtm_telescopes_to_sum_of_each_symbols_own_total(two_symbol_stats):
    """Aggregate-level cross-check, independent of the per-date test
    above: the combined series' final cumulative pnl must equal the sum
    of each symbol's OWN final cumulative pnl (i.e. what naked's
    cross-symbol summary table reports per symbol)."""
    total_mtm = _build_total_mtm(SYMBOLS, two_symbol_stats, INITIAL_CAPITAL)

    per_symbol_final_cum_pnl = sum(
        two_symbol_stats[s]['cum_pnl'][-1] for s in SYMBOLS
    )
    assert total_mtm['total_cum_pnl'][-1] == pytest.approx(per_symbol_final_cum_pnl, abs=0.01)
    assert total_mtm['total_mtm_pnl'].sum() == pytest.approx(per_symbol_final_cum_pnl, abs=0.01)

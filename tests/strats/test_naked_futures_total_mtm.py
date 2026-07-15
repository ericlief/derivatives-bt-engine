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
def two_symbol_daily_mtm():
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
    daily_mtm_by_symbol = {}
    for symbol in SYMBOLS:
        _, daily_mtm = _run_one_symbol(symbol, args)
        assert daily_mtm.height > 100, f"{symbol}: expected a real multi-year daily mtm series"
        daily_mtm_by_symbol[symbol] = daily_mtm
    return daily_mtm_by_symbol


def test_total_mtm_uses_single_shared_capital_every_date(two_symbol_daily_mtm):
    """The actual ask: total_capital is anchored to ONE shared
    INITIAL_CAPITAL baseline, not N independent accounts summed -- i.e.
    total_capital = INITIAL_CAPITAL + sum of each symbol's own cum_pnl
    (capital_<symbol> - INITIAL_CAPITAL, since every symbol was run with
    the same INITIAL_CAPITAL), checked at EVERY date, not just in
    aggregate. total_mtm_pnl is unaffected by the capital-baseline choice
    -- it's still just the sum of each symbol's own daily mtm_pnl."""
    total_mtm, _ = _build_total_mtm(SYMBOLS, two_symbol_daily_mtm, INITIAL_CAPITAL)
    assert total_mtm.height > 100

    expected_cum_pnl = pl.sum_horizontal([pl.col(f'capital_{s}') - INITIAL_CAPITAL for s in SYMBOLS])
    expected_pnl = pl.sum_horizontal([f'mtm_pnl_{s}' for s in SYMBOLS])
    checked = total_mtm.with_columns(
        expected_capital=INITIAL_CAPITAL + expected_cum_pnl, expected_pnl=expected_pnl,
    )

    capital_mismatches = checked.filter(
        (pl.col('total_capital') - pl.col('expected_capital')).abs() > 0.01
    )
    pnl_mismatches = checked.filter(
        (pl.col('total_mtm_pnl') - pl.col('expected_pnl')).abs() > 0.01
    )
    assert capital_mismatches.height == 0, (
        f"{capital_mismatches.height} date(s) where total_capital != INITIAL_CAPITAL + summed per-symbol cum_pnl:\n"
        f"{capital_mismatches.select(['date', 'total_capital', 'expected_capital'] + [f'capital_{s}' for s in SYMBOLS])}"
    )
    assert pnl_mismatches.height == 0, (
        f"{pnl_mismatches.height} date(s) where total_mtm_pnl != sum of per-symbol mtm_pnl:\n"
        f"{pnl_mismatches.select(['date', 'total_mtm_pnl', 'expected_pnl'] + [f'mtm_pnl_{s}' for s in SYMBOLS])}"
    )

    # The whole point of the single-shared-capital change: 2 symbols must
    # NOT start at 2x INITIAL_CAPITAL.
    assert total_mtm['total_capital'][0] == pytest.approx(
        INITIAL_CAPITAL + sum(two_symbol_daily_mtm[s]['mtm_pnl'][0] for s in SYMBOLS), abs=0.01
    )


def test_total_mtm_telescopes_to_sum_of_each_symbols_own_total(two_symbol_daily_mtm):
    """Aggregate-level cross-check, independent of the per-date test
    above: the combined series' final cumulative pnl must equal the sum
    of each symbol's OWN final cumulative pnl (i.e. what naked's
    cross-symbol summary table reports per symbol) -- this identity holds
    regardless of the capital-baseline choice, since it's PnL, not capital."""
    total_mtm, _ = _build_total_mtm(SYMBOLS, two_symbol_daily_mtm, INITIAL_CAPITAL)

    per_symbol_final_cum_pnl = sum(
        two_symbol_daily_mtm[s]['cum_pnl'][-1] for s in SYMBOLS
    )
    assert total_mtm['total_cum_pnl'][-1] == pytest.approx(per_symbol_final_cum_pnl, abs=0.01)
    assert total_mtm['total_mtm_pnl'].sum() == pytest.approx(per_symbol_final_cum_pnl, abs=0.01)


def test_contribution_pct_sums_to_100(two_symbol_daily_mtm):
    """Per-symbol contribution %'s must sum to ~100% of the total (they
    can individually exceed 100% or go negative when symbols' PnL signs
    disagree, but the sum across all symbols must reconcile), and each
    symbol's own final_pnl must match its own reported cum_pnl exactly."""
    total_mtm, contributions = _build_total_mtm(SYMBOLS, two_symbol_daily_mtm, INITIAL_CAPITAL)
    assert len(contributions) == len(SYMBOLS)

    pct_sum = sum(c['pct_of_total'] for c in contributions)
    assert pct_sum == pytest.approx(100.0, abs=0.1)

    by_symbol = {c['symbol']: c for c in contributions}
    for symbol in SYMBOLS:
        assert by_symbol[symbol]['final_pnl'] == pytest.approx(
            two_symbol_daily_mtm[symbol]['cum_pnl'][-1], abs=0.01
        )

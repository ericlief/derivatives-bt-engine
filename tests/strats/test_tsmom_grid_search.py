from datetime import date

import polars as pl

from derivatives_bt_engine.strats import tsmom_grid_search as grid


def _result():
    return {
        'daily_mtm': pl.DataFrame({
            'date': [date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31),
                     date(2022, 1, 1), date(2022, 12, 31)],
            'capital': [100.0, 110.0, 121.0, 108.9, 119.79],
            'cum_pnl': [0.0, 10.0, 21.0, 8.9, 19.79],
            'drawdown_pct': [0.0, 0.0, 0.0, -10.0, -1.0],
        }),
        'trend_signals': [
            {'symbol': 'MES', 'date': date(2021, 1, 1), 'prior_contracts': 0, 'target_contracts': 1},
            {'symbol': 'MES', 'date': date(2022, 1, 1), 'prior_contracts': 1, 'target_contracts': -1},
        ],
        'transactions': pl.DataFrame({'date': [date(2021, 1, 1)], 'fee': [2.0]}),
        'n_days': 5, 'ann_ret_pct': 3.0, 'ann_vol_pct': 10.0,
        'sharpe': .3, 'max_dd_pct': -10.0, 'total_fees': 2.0,
    }


def test_grid_worker_scores_windows_from_its_single_backtest(monkeypatch):
    monkeypatch.setattr(grid, 'run_tsmom_backtest', lambda config: _result())

    summary, metrics, symbols = grid._run_one(
        7, {'vol_target': .15}, ['MES'], date(2020, 1, 1), date(2022, 12, 31),
        date(2021, 1, 1), 2,
    )

    assert summary['combo_id'] == 7
    assert summary['C_capped_rolling_n_windows'] == 2
    assert len(metrics) == 6  # two annual, two expanding, two capped-rolling rows
    assert symbols == [{
        'combo_id': 7, 'vol_target': .15, 'symbol': 'MES', 'final_capital': 119.79,
        'cum_pnl': 19.79, 'max_drawdown_pct': -10.0, 'time_in_trade_pct': 99.9,
        'n_direction_switches': 1,
    }]

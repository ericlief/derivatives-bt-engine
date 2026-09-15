from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.domain.tsmom_window_reporting import (
    score_causal_windows,
    summarize_causal_windows,
)


def _result():
    # The 2021-01-01 return is deliberately derived from the preceding 2020
    # capital. A later report slice must retain that inherited-position return.
    return {
        'daily_mtm': pl.DataFrame({
            'date': [date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31),
                     date(2022, 1, 1), date(2022, 12, 31), date(2023, 6, 30)],
            'capital': [100.0, 110.0, 121.0, 108.9, 119.79, 131.769],
        }),
        'trend_signals': [
            {'symbol': 'MES', 'date': date(2020, 12, 31)},
            {'symbol': 'MES', 'date': date(2021, 1, 1)},
            {'symbol': 'MES', 'date': date(2022, 1, 1)},
        ],
        'transactions': pl.DataFrame({
            'date': [date(2020, 12, 31), date(2021, 1, 1), date(2022, 1, 1)],
            'fee': [1.0, 2.0, 3.0],
        }),
    }


def test_causal_window_scores_slice_one_return_path_without_resetting():
    metrics = score_causal_windows(
        _result(), initial_capital=90_000, oos_start=date(2021, 1, 1), max_width_years=2,
    )

    assert metrics.group_by('scheme').len().sort('scheme').to_dicts() == [
        {'scheme': 'A_annual_oos', 'len': 3},
        {'scheme': 'B_expanding', 'len': 3},
        {'scheme': 'C_capped_rolling', 'len': 3},
    ]
    annual_2021 = metrics.filter(
        (pl.col('scheme') == 'A_annual_oos') & (pl.col('window_start') == '2021-01-01')
    ).row(0, named=True)
    expanding_2021 = metrics.filter(
        (pl.col('scheme') == 'B_expanding') & (pl.col('window_end') == '2021-12-31')
    ).row(0, named=True)

    # Both rows are the same dated slice of the full causal path: +10%, +10%.
    assert annual_2021['n_days'] == 2
    assert annual_2021['final_capital'] == pytest.approx(108_900.0)
    assert annual_2021['total_fees'] == 2.0
    assert annual_2021['rebalance_events'] == 1
    assert annual_2021['sharpe'] == expanding_2021['sharpe']


def test_causal_window_summary_excludes_rows_without_a_computable_sharpe():
    metrics = score_causal_windows(
        _result(), initial_capital=90_000, oos_start=date(2021, 1, 1), max_width_years=2,
    )
    summary = summarize_causal_windows(metrics)
    # One-return annual slices have no sample standard deviation, so they do
    # not contribute a Sharpe to the per-scheme stability statistic.
    assert summary['n_windows'].to_list() == [1, 2, 3]

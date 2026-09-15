from datetime import date

import polars as pl

from derivatives_bt_engine.domain.tsmom_backtester import TsmomBacktestConfig
from derivatives_bt_engine.strats import tsmom_window_scheme as windows


def test_window_scheme_matches_expanding_and_capped_rolling_generators(monkeypatch):
    """Every row is an independent main-TSMOM run with the window bounds set."""
    calls = []

    def fake_run(config):
        calls.append((config.start_date, config.end_date))
        return {
            'daily_mtm': pl.DataFrame({'date': [config.end_date], 'capital': [101_234.567]}),
            'trend_signals': [{'date': config.end_date}],
            'n_days': 252, 'ann_ret_pct': 4.0, 'ann_vol_pct': 10.0,
            'sharpe': .4, 'max_dd_pct': -8.0, 'total_fees': 12.34,
        }

    monkeypatch.setattr(windows, 'run_tsmom_backtest', fake_run)
    config = TsmomBacktestConfig(symbols=['MES'], signal_weighting='goulding', target_portfolio_vol=.15)

    result = windows.run_window_scheme(
        config, anchor_start='2010-01-01', data_end='2016-01-01', step_years=1, max_width_years=5,
    )
    summary = windows.summarize(result)

    assert result.height == 12  # six Scheme-B and six Scheme-C windows
    assert result.filter(pl.col('scheme') == 'B_expanding')['window_start'].unique().to_list() == ['2010-01-01']
    assert result.filter(pl.col('scheme') == 'C_capped_rolling').tail(1)['window_start'][0] == '2011-01-01'
    assert result['final_capital'][0] == 101_234.57
    assert calls[0] == (date(2010, 1, 1), date(2011, 1, 1))
    assert summary.to_dicts() == [
        {'scheme': 'B_expanding', 'n_windows': 6, 'sharpe_mean': .4, 'sharpe_std': 0.0,
         'sharpe_min': .4, 'sharpe_max': .4, 'ann_ret_pct_mean': 4.0,
         'ann_vol_pct_mean': 10.0, 'max_dd_pct_mean': -8.0},
        {'scheme': 'C_capped_rolling', 'n_windows': 6, 'sharpe_mean': .4, 'sharpe_std': 0.0,
         'sharpe_min': .4, 'sharpe_max': .4, 'ann_ret_pct_mean': 4.0,
         'ann_vol_pct_mean': 10.0, 'max_dd_pct_mean': -8.0},
    ]

    capped_subset = windows.run_window_scheme(
        config, anchor_start='2010-01-01', data_end='2016-01-01', step_years=1,
        max_width_years=5, schemes=('C',), window_index_start=5, window_index_end=7,
    )
    assert capped_subset.select(['scheme', 'window_start', 'window_end']).to_dicts() == [
        {'scheme': 'C_capped_rolling', 'window_start': '2011-01-01', 'window_end': '2016-01-01'},
    ]

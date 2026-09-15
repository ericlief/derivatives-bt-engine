from datetime import date

import pytest

from derivatives_bt_engine.domain.tsmom_reporting import (
    PORTFOLIO_COLUMNS,
    SIGNAL_COLUMNS,
    clean_signal_rows,
    portfolio_rows_from_signals,
)


def test_clean_signal_rows_share_live_backtest_schema_without_config_echoes():
    raw = [{
        'date': date(2026, 9, 15), 'symbol': 'MES', 'cluster': 'equity',
        'close': 7685.75, 'mult': 5, 'g_regime': 'bull', 'g_fast': .01, 'g_slow': .02,
        'signal': 1.0, 'ts_fast': .2, 'ts_slow': .3, 'ts': .25, 'contin_signal': .25,
        'ts_regime': 'bull', 'daily_std': .0073, 'hv': .1177, 'risk_scalar': 1.2746,
        'regime_discount': 1.0, 'vix_scalar': 1.0, 'combined_scalar': 1.2746,
        'current_contracts': 0, 'fractional_target_contracts': .4294,
        'final_target_contracts': 0, 'max_contracts': 15,
        'cluster_universe_rank': 1, 'cluster_universe_score': .015,
        'pre_scalar_notional_budget': 12944.96,
        # Static configuration is intentionally present in raw targets but
        # must live only in the run manifest after projection.
        'use_idm': True, 'notional_weighting': 'erc', 'max_active_per_cluster': 2,
    }]

    rows = clean_signal_rows(raw, 'live_20260915')

    assert tuple(rows[0]) == SIGNAL_COLUMNS
    assert rows[0]['as_of'] == '2026-09-15'
    assert rows[0]['frac_tgt_not'] == pytest.approx(.4294 * 7685.75 * 5)
    assert rows[0]['frac_tgt_dvol'] == pytest.approx(.4294 * 7685.75 * 5 * .1177)
    assert rows[0]['pos_dvol'] == 0.0
    assert 'use_idm' not in rows[0]
    assert 'notional_weighting' not in rows[0]
    assert 'max_active_per_cluster' not in rows[0]


def test_portfolio_rows_are_one_snapshot_per_as_of_date():
    rows = clean_signal_rows([
        {'date': date(2026, 9, 15), 'symbol': 'MES', 'cluster': 'equity',
         'close': 100, 'mult': 5, 'hv': .1, 'final_target_contracts': 2},
        {'date': date(2026, 9, 15), 'symbol': 'MNQ', 'cluster': 'equity',
         'close': 100, 'mult': 2, 'hv': .2, 'final_target_contracts': -1},
        {'date': date(2026, 9, 15), 'symbol': 'MCL', 'cluster': 'energy',
         'close': 100, 'mult': 100, 'hv': .3, 'final_target_contracts': 1},
    ], 'backtest_20260915')

    portfolio = portfolio_rows_from_signals(
        rows, 'backtest_20260915', equity_by_as_of={'2026-09-15': 90_000},
        portfolio_fields_by_as_of={'2026-09-15': {'portfolio_risk_target': 13_500}},
    )

    assert len(portfolio) == 1
    assert tuple(portfolio[0]) == PORTFOLIO_COLUMNS
    assert portfolio[0]['n_active_symbols'] == 3
    assert portfolio[0]['n_active_clusters'] == 2
    assert portfolio[0]['gross_position_dvol'] == 3_140
    assert portfolio[0]['portfolio_risk_target'] == 13_500

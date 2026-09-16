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
            'notional_weighting': 'erc', 'pre_scalar_notional_budget': 12944.96,
            'notional_allocation_weight': .0714,
        # Static configuration is intentionally present in raw targets but
        # must live only in the run manifest after projection.
        'use_idm': True, 'notional_weighting': 'erc', 'max_active_per_cluster': 2,
    }]

    rows = clean_signal_rows(raw, 'live_20260915')

    assert tuple(rows[0]) == SIGNAL_COLUMNS
    assert rows[0]['as_of'] == '2026-09-15'
    assert rows[0]['frac_tgt_not'] == 16_501.31
    assert rows[0]['frac_tgt_dvol'] == 1_942.20
    assert rows[0]['pos_dvol'] == 0.0
    assert rows[0]['one_con_not'] == 38_428.75
    assert rows[0]['not_weighting'] == 'erc'
    assert rows[0]['not_alloc_w'] == pytest.approx(.0714)
    assert 'use_idm' not in rows[0]
    assert 'notional_weighting' not in rows[0]
    assert 'max_active_per_cluster' not in rows[0]


def test_clean_signal_rows_round_non_dollar_floats_to_four_decimals():
    rows = clean_signal_rows([{
        'date': date(2026, 9, 15), 'symbol': 'MES', 'close': 7685.751234,
        'mult': 5.123456, 'g_fast': .0123456, 'daily_std': .0073456,
        'hv': .117756, 'fractional_target_contracts': .429456,
        'notional_allocation_weight': .071456,
    }], 'live_20260915')

    assert rows[0]['close'] == 7685.7512
    assert rows[0]['mult'] == 5.1235
    assert rows[0]['g_fast'] == .0123
    assert rows[0]['day_std'] == .0073
    assert rows[0]['hv'] == .1178
    assert rows[0]['frac_con'] == .4295
    assert rows[0]['not_alloc_w'] == .0715


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
    assert portfolio[0]['n_act_symb'] == 3
    assert portfolio[0]['n_act_clus'] == 2
    assert portfolio[0]['gross_pos_dvol'] == 3_140.00
    assert portfolio[0]['port_risk_tgt'] == 13_500.00

import asyncio

# ib_insync's current dependency chain expects an event loop while importing
# under Python 3.14; the production CLI initializes in an IB-capable process.
asyncio.set_event_loop(asyncio.new_event_loop())

from derivatives_bt_engine.live.run_tsmom_rebalance import (
    REBALANCE_COLUMNS,
    _rebalance_delta_rows,
)


def test_rebalance_delta_rows_only_include_actionable_contract_changes():
    rows = _rebalance_delta_rows([
        {'symbol': 'MES', 'cluster': 'equity', 'current_contracts': 1, 'final_target_contracts': 1},
        {'symbol': 'MCL', 'cluster': 'energy', 'current_contracts': 1, 'final_target_contracts': 3},
        {'symbol': 'MTN', 'cluster': 'rates', 'current_contracts': -2, 'final_target_contracts': -4},
        {'symbol': 'MGC', 'cluster': 'metal', 'current_contracts': None, 'final_target_contracts': 1},
    ])

    assert tuple(rows[0]) == REBALANCE_COLUMNS
    assert rows == [
        {'symbol': 'MCL', 'cluster': 'energy', 'cur_con': 1, 'tgt_con': 3,
         'delta_con': 2, 'action': 'BUY'},
        {'symbol': 'MTN', 'cluster': 'rates', 'cur_con': -2, 'tgt_con': -4,
         'delta_con': -2, 'action': 'SELL'},
    ]

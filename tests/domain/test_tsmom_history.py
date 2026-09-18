from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from derivatives_bt_engine.data.futures_overlap import MarketMapping
from derivatives_bt_engine.domain.continuous_futures import build_continuous_futures
from derivatives_bt_engine.domain.futures_history import FuturesHistory
from derivatives_bt_engine.domain import tsmom_history as th


def _history() -> FuturesHistory:
    raw = pl.DataFrame({
        'trade_date': [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)],
        'source_timestamp': [datetime(2020, 1, 1, 23), datetime(2020, 1, 2, 23), datetime(2020, 1, 3, 23)],
        'current_price': [100.0, 101.0, 122.0],
        'current_contract': ['20200100', '20200100', '20200200'],
        'forward_price': [120.0, 121.0, 123.0],
        'forward_contract': ['20200200', '20200200', '20200300'],
    })
    generated = build_continuous_futures(raw)
    marks = generated.signal.select(
        'trade_date', 'source_timestamp',
        pl.col('current_price').alias('mark_price'), 'contract_id', 'is_roll',
        'quality_flag',
    )
    carry = pl.DataFrame(schema={
        'trade_date': pl.Date, 'source_timestamp': pl.Datetime('us'),
        'current_price': pl.Float64, 'current_contract': pl.String,
        'carry_price': pl.Float64, 'carry_contract': pl.String,
        'quality_flag': pl.String,
    })
    return FuturesHistory(
        'pysystemtrade', 'TEST', 5, generated.signal, marks, carry,
        panama=generated.panama,
    )


def test_history_adapter_separates_mark_signal_and_pnl_levels() -> None:
    bars = th.history_to_tsmom_bars(_history())

    assert bars.get_column('close').to_list() == [100.0, 101.0, 122.0]
    assert bars.get_column('pnl_close').to_list() == [120.0, 121.0, 122.0]
    assert bars.get_column('signal_index').to_list()[0] == 100.0
    assert bars.get_column('contract_id').to_list()[-1] == '20200200'
    assert bars.get_column('source_segment').unique().to_list() == ['pysystemtrade']
    assert bars.get_column('pnl_quality').unique().to_list() == ['research_approximation']


def test_candidate_mapping_requires_explicit_research_opt_in(monkeypatch) -> None:
    mapping = MarketMapping('test', 'TEST', 'ES', mapping_status='candidate')
    monkeypatch.setattr(th, 'load_mappings', lambda _path=None: (mapping,))

    with pytest.raises(ValueError, match='approved pysystemtrade mapping'):
        th.load_source_neutral_histories(
            ['ES'], data_source='pysystemtrade', allow_candidate_mappings=False,
        )

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from derivatives_bt_engine.domain.continuous_futures import (
    build_continuous_futures,
    select_daily_continuous,
)
from derivatives_bt_engine.domain.futures_history import FuturesHistory, HybridHistoryProvider
from derivatives_bt_engine.domain.futures_signal_harness import (
    FuturesSignalClass,
    run_futures_signal,
)


def _observations(
    prices: list[float],
    contracts: list[str],
    forwards: list[float | None],
    forward_contracts: list[str | None],
    *,
    start: date = date(2020, 1, 1),
) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(prices))]
    return pl.DataFrame(
        {
            "trade_date": dates,
            "source_timestamp": [datetime.combine(value, datetime.min.time()) for value in dates],
            "current_price": prices,
            "current_contract": contracts,
            "forward_price": forwards,
            "forward_contract": forward_contracts,
        }
    )


def test_panama_reconstruction_and_roll_return_use_forward_reference() -> None:
    result = build_continuous_futures(
        _observations(
            [100.0, 101.0, 122.0],
            ["A", "A", "B"],
            [120.0, 121.0, 130.0],
            ["B", "B", "C"],
        )
    )

    assert result.panama.get_column("panama_price").to_list() == [120.0, 121.0, 122.0]
    assert result.signal.get_column("pt_change_1d").to_list() == [None, 1.0, 1.0]
    assert "normalized_return" not in result.signal.columns
    assert "contract_point_change" not in result.signal.columns
    assert result.signal.get_column("ret_1d")[2] == pytest.approx(122 / 121 - 1)
    assert result.panama.get_column("roll_differential")[2] == pytest.approx(20.0)


def test_nonpositive_prices_keep_point_path_but_mask_ratio_path() -> None:
    result = build_continuous_futures(
        _observations(
            [100.0, -10.0, 5.0, 6.0],
            ["A", "A", "A", "A"],
            [None, None, None, None],
            [None, None, None, None],
        )
    )

    assert result.panama.get_column("pt_change_1d").to_list() == [
        None, -110.0, 15.0, 1.0
    ]
    assert result.signal.get_column("ret_1d").to_list() == [
        None, None, None, pytest.approx(0.2)
    ]
    assert result.signal.get_column("signal_index").to_list() == [100.0, 100.0, 100.0, 120.0]
    assert result.signal.get_column("return_valid").to_list() == [False, False, False, True]
    assert "nonpositive_current" in result.signal.get_column("quality_flag")[1]
    assert "nonpositive_reference" in result.signal.get_column("quality_flag")[2]


def test_daily_selection_chains_all_intraday_moves_and_propagates_invalidity() -> None:
    day_one = date(2020, 1, 1)
    day_two = date(2020, 1, 2)
    observations = pl.DataFrame(
        {
            "trade_date": [day_one, day_one, day_two, day_two],
            "source_timestamp": [
                datetime(2020, 1, 1, 12), datetime(2020, 1, 1, 23),
                datetime(2020, 1, 2, 12), datetime(2020, 1, 2, 23),
            ],
            "current_price": [100.0, 110.0, 0.0, 121.0],
            "current_contract": ["A"] * 4,
            "forward_price": [None] * 4,
            "forward_contract": [None] * 4,
        }
    )

    daily = select_daily_continuous(build_continuous_futures(observations))

    assert daily.signal.height == 2
    assert daily.signal.get_column("ret_1d").to_list() == [None, None]
    assert daily.signal.get_column("return_valid").to_list() == [False, False]
    assert daily.signal.get_column("quality_flag")[1] == "invalid_intraday_return"
    assert daily.panama.get_column("pt_change_1d").to_list() == [None, 11.0]


def _history(source: str, start: date, prices: list[float]) -> FuturesHistory:
    generated = build_continuous_futures(
        _observations(
            prices,
            ["A"] * len(prices),
            [None] * len(prices),
            [None] * len(prices),
            start=start,
        )
    )
    marks = generated.signal.select(
        "trade_date",
        "source_timestamp",
        pl.col("current_price").alias("mark_price"),
        "contract_id",
        "is_roll",
        "quality_flag",
    )
    carry = pl.DataFrame(
        schema={
            "trade_date": pl.Date,
            "source_timestamp": pl.Datetime("us"),
            "current_price": pl.Float64,
            "current_contract": pl.String,
            "carry_price": pl.Float64,
            "carry_contract": pl.String,
            "quality_flag": pl.String,
        }
    )
    return FuturesHistory(source, "TEST", 4, generated.signal, marks, carry, panama=generated.panama)


class _Provider:
    def __init__(self, history: FuturesHistory):
        self.history = history

    def load(self, _instrument_code: str) -> FuturesHistory:
        return self.history


def test_hybrid_provider_rebuilds_levels_and_three_signal_harness_routes_inputs() -> None:
    old = _history("carver", date(2020, 1, 1), [100, 101, 102, 103])
    new = _history("globex", date(2020, 1, 3), [200, 202, 204, 206])
    history = HybridHistoryProvider(
        historical=_Provider(old),
        primary=_Provider(new),
        handoff_date=date(2020, 1, 4),
    ).load("TEST")

    assert history.signal.get_column("trade_date").to_list() == [
        date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3),
        date(2020, 1, 4), date(2020, 1, 5), date(2020, 1, 6),
    ]
    assert history.signal.get_column("source_segment").to_list() == [
        "carver", "carver", "carver", "globex", "globex", "globex"
    ]
    ewmac = run_futures_signal(
        history,
        FuturesSignalClass.CARVER_EWMAC,
        parameters={"fast_span": 2, "slow_span": 4, "vol_span": 2},
    )
    return_tsmom = run_futures_signal(
        history,
        FuturesSignalClass.RETURN_TSMOM,
        parameters={"fast_window": 1, "slow_window": 2},
    )
    goulding = run_futures_signal(
        history,
        FuturesSignalClass.GOULDING_MONTHLY,
        parameters={"fast_months": 1, "slow_months": 2},
    )
    assert "raw_ewmac" in ewmac.columns
    assert "ts_fast" in return_tsmom.columns
    assert "regime" in goulding.columns

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

import derivatives_bt_engine.data.futures_overlap as overlap
from derivatives_bt_engine.data.futures_overlap import (
    MarketMapping,
    build_overlap_report,
    compare_market,
    load_mappings,
    write_overlap_report,
)
from derivatives_bt_engine.domain.futures_history import FuturesHistory


def _empty_carry() -> pl.DataFrame:
    return pl.DataFrame(
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


def _history(source: str, roll_index: int) -> FuturesHistory:
    dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(400)]
    timestamps = [datetime.combine(value, datetime.min.time()) for value in dates]
    returns = [None] + [((index % 9) - 4) * 0.0007 for index in range(1, 400)]
    index_values = []
    level = 100.0
    for value in returns:
        if value is not None:
            level *= 1 + value
        index_values.append(level)
    contracts = [
        "20200300" if index < roll_index else "20200600"
        for index in range(400)
    ]
    is_roll = [False] * 400
    is_roll[roll_index] = True
    signal = pl.DataFrame(
        {
            "trade_date": dates,
            "source_timestamp": timestamps,
            "normalized_return": returns,
            "signal_index": index_values,
            "contract_id": contracts,
            "is_roll": is_roll,
            "quality_flag": [""] * 400,
        }
    )
    marks = pl.DataFrame(
        {
            "trade_date": dates,
            "source_timestamp": timestamps,
            "mark_price": [100.0 + index * 0.01 for index in range(400)],
            "contract_id": contracts,
            "is_roll": is_roll,
            "quality_flag": [""] * 400,
        }
    )
    return FuturesHistory(
        source=source,
        instrument_code="TEST",
        schema_version=1,
        signal=signal,
        marks=marks,
        carry=_empty_carry(),
    )


def _dated_history(source: str, dates: list[date]) -> FuturesHistory:
    timestamps = [datetime.combine(value, datetime.min.time()) for value in dates]
    signal = pl.DataFrame(
        {
            "trade_date": dates,
            "source_timestamp": timestamps,
            "normalized_return": [None, 0.01, 0.02],
            "signal_index": [100.0, 101.0, 103.02],
            "contract_id": ["20200300"] * 3,
            "is_roll": [False] * 3,
            "quality_flag": [""] * 3,
        }
    )
    marks = pl.DataFrame(
        {
            "trade_date": dates,
            "source_timestamp": timestamps,
            "mark_price": [100.0, 101.0, 102.0],
            "contract_id": ["20200300"] * 3,
            "is_roll": [False] * 3,
            "quality_flag": [""] * 3,
        }
    )
    return FuturesHistory(source, "TEST", 1, signal, marks, _empty_carry())


class _Provider:
    def __init__(self, history: FuturesHistory):
        self.history = history

    def load(self, instrument_code: str) -> FuturesHistory:
        return self.history


def test_packaged_mapping_crosswalk_is_typed_and_unique() -> None:
    mappings = load_mappings()

    assert len(mappings) == 14
    assert len({mapping.canonical_market_id for mapping in mappings}) == 14
    crude = next(mapping for mapping in mappings if mapping.canonical_market_id == "crude_wti")
    silver = next(mapping for mapping in mappings if mapping.canonical_market_id == "silver")
    assert crude.mapping_status == "candidate"
    assert "annual December" in crude.known_issue
    assert silver.carver_date_alignment == "previous_globex_session"
    assert silver.carver_date_alignment_through == date(2021, 6, 30)
    assert sum(bool(mapping.carver_date_alignment) for mapping in mappings) == 1


def test_compare_market_reports_returns_trends_and_nearest_roll() -> None:
    mapping = MarketMapping("test", "CARVER", "GLOBEX")
    carver = _history("pysystemtrade", 200)
    globex = _history("globex", 202)

    metrics, rolls, missing_dates, detail = compare_market(
        mapping,
        carver,
        globex,
        start=date(2020, 1, 1),
        end=date(2021, 2, 3),
    )

    assert metrics["common_dates"] == 400
    assert metrics["same_day_nonroll_return_correlation"] == pytest.approx(1.0)
    assert metrics["shift_0_volatility_correlation"] == pytest.approx(1.0)
    assert metrics["best_volatility_correlation"] == pytest.approx(1.0)
    assert metrics["trend_signal_correlation"] == pytest.approx(1.0)
    assert metrics["trend_direction_agreement"] == pytest.approx(1.0)
    assert metrics["contract_month_agreement"] == pytest.approx(398 / 400)
    assert metrics["median_roll_distance_days"] == 2
    assert metrics["review_status"] == "candidate"
    assert rolls.get_column("roll_date_distance_days").to_list() == [2]
    assert missing_dates.is_empty()
    assert detail.height == 400


def test_previous_session_alignment_uses_actual_globex_dates_not_calendar_days() -> None:
    globex_dates = [date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]
    carver_dates = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 1, 8)]
    mapping = MarketMapping(
        "test",
        "CARVER",
        "GLOBEX",
        carver_date_alignment="previous_globex_session",
        carver_date_alignment_through=date(2020, 1, 8),
    )

    aligned, collapsed = overlap._align_carver_dates(
        mapping,
        _dated_history("pysystemtrade", carver_dates),
        _dated_history("globex", globex_dates),
    )

    assert aligned.signal.get_column("trade_date").to_list() == globex_dates
    assert all(value.weekday() < 5 for value in globex_dates)
    assert collapsed == 0


def test_build_and_write_overlap_report(tmp_path: Path) -> None:
    mapping = MarketMapping("test", "CARVER", "GLOBEX")
    report = build_overlap_report(
        [mapping],
        _Provider(_history("pysystemtrade", 200)),
        _Provider(_history("globex", 202)),
        start=date(2020, 1, 1),
        end=date(2021, 2, 3),
    )

    write_overlap_report(
        report,
        tmp_path,
        start=date(2020, 1, 1),
        end=date(2021, 2, 3),
        write_details=True,
    )

    assert (tmp_path / "summary.csv").exists()
    assert (tmp_path / "rolls.csv").exists()
    assert (tmp_path / "missing_dates.csv").exists()
    assert (tmp_path / "details" / "test.csv").exists()
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Every mapping remains `candidate`" in markdown
    assert "Sunday observations are assigned" in markdown
    assert "Return corr. -1 / 0 / +1" in markdown
    assert "Vol corr. -1 / 0 / +1" in markdown
    assert "test: CARVER / GLOBEX" in markdown


def test_cli_main_returns_success_value_not_report_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = build_overlap_report(
        [MarketMapping("test", "CARVER", "GLOBEX")],
        _Provider(_history("pysystemtrade", 200)),
        _Provider(_history("globex", 202)),
        start=date(2020, 1, 1),
        end=date(2021, 2, 3),
    )
    monkeypatch.setattr(overlap, "build_overlap_report", lambda *args, **kwargs: report)

    result = overlap.main(
        [
            "--output-dir",
            str(tmp_path / "report"),
            "--start",
            "2020-01-01",
            "--end",
            "2021-02-03",
        ]
    )

    assert result is None
    assert "Wrote" in capsys.readouterr().out

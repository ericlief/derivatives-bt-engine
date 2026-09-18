from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from derivatives_bt_engine.domain.futures_dataloader import (
    FuturesDataLoader,
    globex_daily_cache_path,
)
from derivatives_bt_engine.domain.futures_history import (
    FuturesHistory,
    GlobexHistoryProvider,
    PysystemtradeHistoryProvider,
)


CARVER_CODES = ["SP500", "GOLD", "CRUDE_W", "US10", "JPY"]
SOURCE_COMMIT = "b" * 40


def _build_carver_sidecar(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA meta")
        con.execute("CREATE SCHEMA raw")
        con.execute(
            """
            CREATE TABLE meta.schema_version (
                version INTEGER,
                imported_at_utc TIMESTAMPTZ,
                importer VARCHAR,
                source_root VARCHAR,
                source_git_commit VARCHAR
            )
            """
        )
        con.execute(
            "INSERT INTO meta.schema_version VALUES (2, now(), 'test', '/test', ?)",
            [SOURCE_COMMIT],
        )
        con.execute(
            """
            CREATE TABLE raw.instrument_config (
                instrument_code VARCHAR,
                description VARCHAR,
                point_size DOUBLE,
                currency VARCHAR,
                asset_class VARCHAR,
                per_block_cost DOUBLE,
                percentage_cost DOUBLE,
                per_trade_cost DOUBLE,
                region VARCHAR,
                source_file VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE raw.adjusted_prices (
                instrument_code VARCHAR,
                source_timestamp TIMESTAMP,
                trade_date DATE,
                adjusted_price DOUBLE,
                source_file VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE raw.roll_config (
                instrument_code VARCHAR,
                hold_roll_cycle VARCHAR,
                roll_offset_days BIGINT,
                carry_offset BIGINT,
                priced_roll_cycle VARCHAR,
                expiry_offset BIGINT,
                source_file VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE raw.multiple_prices (
                instrument_code VARCHAR,
                source_timestamp TIMESTAMP,
                trade_date DATE,
                price DOUBLE,
                price_contract VARCHAR,
                carry DOUBLE,
                carry_contract VARCHAR,
                forward DOUBLE,
                forward_contract VARCHAR,
                source_file VARCHAR
            )
            """
        )
        timestamps = [
            datetime(2020, 1, 1, 23),
            datetime(2020, 1, 2, 23),
            datetime(2020, 1, 3, 23),
            datetime(2020, 1, 4, 23),
        ]
        for code in CARVER_CODES:
            con.execute(
                """
                INSERT INTO raw.instrument_config VALUES
                (?, ?, 10, 'USD', 'Test', 0, 0, 0, 'US', 'config.csv')
                """,
                [code, f"{code} test future"],
            )
            con.execute(
                """
                INSERT INTO raw.roll_config VALUES
                (?, 'HMUZ', -5, 1, 'HMUZ', 14, 'rollconfig.csv')
                """,
                [code],
            )
            for timestamp, adjusted, price, contract in zip(
                timestamps,
                [-10.0, -9.0, None, -7.0],
                [100.0, 200.0, 150.0, 100.0],
                ["20200100", "20200100", "20200100", "20200200"],
            ):
                con.execute(
                    "INSERT INTO raw.adjusted_prices VALUES (?, ?, ?, ?, 'adjusted.csv')",
                    [code, timestamp, timestamp.date(), adjusted],
                )
                con.execute(
                    """
                    INSERT INTO raw.multiple_prices VALUES
                    (?, ?, ?, ?, ?, ?, '20200300', ?, '20200300', 'multiple.csv')
                    """,
                    [
                        code,
                        timestamp,
                        timestamp.date(),
                        price,
                        contract,
                        price - 1,
                        price + 1,
                    ],
                )
    finally:
        con.close()


def _build_globex_database(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE daily (
                ts_event TIMESTAMP,
                instrument_id UINTEGER,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume UBIGINT,
                asset VARCHAR,
                instrument_class VARCHAR,
                security_type VARCHAR,
                expiration DATE
            )
            """
        )
        rows = [
            ("2020-01-01", 1, 100.0, 100, "2020-03-20"),
            ("2020-01-01", 2, 105.0, 50, "2020-06-19"),
            ("2020-01-02", 1, 101.0, 100, "2020-03-20"),
            ("2020-01-02", 2, 106.0, 60, "2020-06-19"),
            ("2020-01-03", 1, 102.0, 10, "2020-03-20"),
            ("2020-01-03", 2, 107.0, 200, "2020-06-19"),
        ]
        for ts_event, instrument_id, close, volume, expiration in rows:
            con.execute(
                """
                INSERT INTO daily VALUES (
                    ?, ?, ?, ?, ?, ?, ?, 'ES', 'F', 'FUT', ?
                )
                """,
                [
                    ts_event,
                    instrument_id,
                    close,
                    close,
                    close,
                    close,
                    volume,
                    expiration,
                ],
            )
    finally:
        con.close()


@pytest.mark.parametrize("instrument_code", CARVER_CODES)
def test_carver_loader_builds_positive_index_from_adjusted_differences(
    tmp_path: Path, instrument_code: str
) -> None:
    sidecar = tmp_path / "carver.duckdb"
    _build_carver_sidecar(sidecar)
    provider = PysystemtradeHistoryProvider(
        db_path=sidecar,
        cache_root=tmp_path / "cache",
        use_cache=False,
        save_cache=False,
    )

    history = provider.load(instrument_code)

    assert history.source == "pysystemtrade"
    assert history.metadata["source_git_commit"] == SOURCE_COMMIT
    assert history.metadata["hold_roll_cycle"] == "HMUZ"
    assert history.signal.get_column("adjusted_price").to_list() == [-10.0, -9.0, -7.0]
    assert history.signal.get_column("normalized_return").to_list() == [
        None,
        pytest.approx(0.005),
        pytest.approx(0.02),
    ]
    assert history.signal.get_column("signal_index").to_list() == [
        pytest.approx(100.0),
        pytest.approx(100.5),
        pytest.approx(102.51),
    ]
    signal_bars = history.signal_bars()
    assert signal_bars.get_column("close").min() > 0
    assert signal_bars.select(pl.col("close").pct_change()).to_series().to_list() == [
        None,
        pytest.approx(0.005),
        pytest.approx(0.02),
    ]
    assert history.marks.get_column("is_roll").to_list() == [
        False,
        False,
        False,
        True,
    ]
    assert history.carry.height == 4


def test_carver_cache_path_is_source_and_version_namespaced(tmp_path: Path) -> None:
    sidecar = tmp_path / "carver.duckdb"
    _build_carver_sidecar(sidecar)
    provider = PysystemtradeHistoryProvider(
        db_path=sidecar,
        cache_root=tmp_path / "cache",
    )

    provider.load("SP500")

    expected = (
        tmp_path
        / "cache"
        / "pysystemtrade"
        / "v2"
        / SOURCE_COMMIT[:12]
        / "SP500_signal.parquet"
    )
    assert expected.exists()


def test_carver_loader_collapses_sunday_into_monday_and_recomputes_return(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "carver.duckdb"
    _build_carver_sidecar(sidecar)
    sunday_timestamp = datetime(2020, 1, 5, 23)
    monday_timestamp = datetime(2020, 1, 6, 23)
    monday_trade_date = date(2020, 1, 6)
    con = duckdb.connect(str(sidecar))
    try:
        con.execute(
            """
            INSERT INTO raw.adjusted_prices VALUES
            ('SP500', ?, ?, -6.0, 'adjusted.csv'),
            ('SP500', ?, ?, -5.0, 'adjusted.csv')
            """,
            [
                sunday_timestamp,
                monday_trade_date,
                monday_timestamp,
                monday_trade_date,
            ],
        )
        con.execute(
            """
            INSERT INTO raw.multiple_prices VALUES
            ('SP500', ?, ?, 100.0, '20200200', 99.0, '20200300',
             101.0, '20200300', 'multiple.csv'),
            ('SP500', ?, ?, 200.0, '20200200', 199.0, '20200300',
             201.0, '20200300', 'multiple.csv')
            """,
            [
                sunday_timestamp,
                monday_trade_date,
                monday_timestamp,
                monday_trade_date,
            ],
        )
    finally:
        con.close()
    provider = PysystemtradeHistoryProvider(
        db_path=sidecar,
        cache_root=tmp_path / "cache",
        use_cache=False,
        save_cache=False,
    )

    history = provider.load("SP500")

    assert history.signal.get_column("trade_date")[-1] == monday_trade_date
    assert history.signal.get_column("source_timestamp")[-1] == monday_timestamp
    assert history.signal.get_column("adjusted_point_change")[-1] == pytest.approx(2.0)
    assert history.signal.get_column("normalized_return")[-1] == pytest.approx(0.01)
    assert date(2020, 1, 5) not in history.signal.get_column("trade_date").to_list()
    assert history.signal.height == 4
    assert history.marks.get_column("trade_date")[-1] == monday_trade_date
    assert history.marks.get_column("source_timestamp")[-1] == monday_timestamp
    assert history.carry.get_column("trade_date")[-1] == monday_trade_date
    assert history.carry.get_column("source_timestamp")[-1] == monday_timestamp


def test_globex_signal_uses_same_contract_change_across_roll(tmp_path: Path) -> None:
    database = tmp_path / "globex.duckdb"
    _build_globex_database(database)
    provider = GlobexHistoryProvider(
        db_path=database,
        cache_root=tmp_path / "cache",
        use_cache=False,
        save_cache=False,
    )

    history = provider.load("ES")
    legacy_daily = FuturesDataLoader(
        asset="ES",
        db_path=str(database),
        data_dir=str(tmp_path / "legacy-cache"),
        use_preprocessed=False,
        save_preprocessed=False,
    ).daily

    assert history.marks.get_column("contract_id").to_list() == [
        "20200300",
        "20200300",
        "20200600",
    ]
    assert history.marks.get_column("is_roll").to_list() == [False, False, True]
    assert history.signal.get_column("normalized_return")[2] == pytest.approx(
        (107.0 - 106.0) / 107.0
    )
    assert history.signal.get_column("normalized_return")[2] != pytest.approx(
        (107.0 - 101.0) / 107.0
    )
    assert history.marks.get_column("mark_price").to_list() == legacy_daily.get_column(
        "close"
    ).to_list()


def test_history_rejects_nonpositive_signal_index() -> None:
    signal = pl.DataFrame(
        {
            "trade_date": [datetime(2020, 1, 1).date()],
            "source_timestamp": [datetime(2020, 1, 1)],
            "normalized_return": [None],
            "signal_index": [0.0],
            "quality_flag": ["bad"],
        }
    )
    marks = pl.DataFrame(
        {
            "trade_date": [datetime(2020, 1, 1).date()],
            "source_timestamp": [datetime(2020, 1, 1)],
            "mark_price": [100.0],
            "contract_id": ["20200300"],
            "is_roll": [False],
            "quality_flag": [""],
        }
    )

    with pytest.raises(ValueError, match="strictly positive"):
        FuturesHistory(
            source="test",
            instrument_code="TEST",
            schema_version=1,
            signal=signal,
            marks=marks,
            carry=pl.DataFrame(
                schema={
                    "trade_date": pl.Date,
                    "source_timestamp": pl.Datetime,
                    "current_price": pl.Float64,
                    "current_contract": pl.String,
                    "carry_price": pl.Float64,
                    "carry_contract": pl.String,
                    "quality_flag": pl.String,
                }
            ),
        )


def test_globex_legacy_cache_path_is_namespaced() -> None:
    path = globex_daily_cache_path("/tmp/cache/futures", "ES")
    assert path == "/tmp/cache/futures/globex/v1/ES_daily.parquet"

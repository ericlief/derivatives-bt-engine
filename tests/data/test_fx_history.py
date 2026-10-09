"""Tests for causal historical FX loading and alignment."""

from datetime import date, datetime

import duckdb
import polars as pl

from derivatives_bt_engine.data.fx_history import (
    align_fx_to_history,
    load_fx_to_usd_history,
)


def _write_fx_database(path):
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA raw")
        con.execute(
            """
            CREATE TABLE raw.fx_prices (
                currency_pair VARCHAR,
                source_timestamp TIMESTAMP,
                price DOUBLE,
                source_file VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO raw.fx_prices VALUES (?, ?, ?, ?)",
            [
                ("EURUSD", datetime(2024, 1, 2), 1.10, "EURUSD.csv"),
                ("EURUSD", datetime(2024, 1, 4), 1.20, "EURUSD.csv"),
            ],
        )
    finally:
        con.close()


def test_fx_alignment_is_backward_only_and_keeps_leading_null(tmp_path):
    database = tmp_path / "reference.duckdb"
    _write_fx_database(database)
    rates = load_fx_to_usd_history("EUR", db_path=database)
    history = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 3), date(2024, 1, 5)],
        "close": [1.0, 2.0, 3.0],
    })

    aligned = align_fx_to_history(history, "EUR", fx_history=rates)

    assert aligned.get_column("fx_to_usd").to_list() == [None, 1.10, 1.20]
    assert aligned.get_column("fx_date").to_list() == [
        None,
        date(2024, 1, 2),
        date(2024, 1, 4),
    ]


def test_usd_alignment_is_an_identity_without_database_access():
    history = pl.DataFrame({"date": [date(2024, 1, 1)]})

    aligned = align_fx_to_history(history, "USD", db_path="does-not-exist")

    assert aligned.get_column("fx_to_usd").to_list() == [1.0]
    assert aligned.get_column("fx_pair").to_list() == ["USDUSD"]

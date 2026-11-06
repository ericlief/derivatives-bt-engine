"""Tests for read/write ownership in the raw IB futures-history store."""

from pathlib import Path

from derivatives_bt_engine.data.ib_futures_history_store import (
    IBFuturesHistoryStore,
)


def test_missing_stored_history_read_does_not_create_database(
    tmp_path: Path,
):
    """An offline read must not initialize or otherwise mutate DuckDB."""
    database = tmp_path / "missing.duckdb"
    store = IBFuturesHistoryStore(database)

    bars = store.load_bars(
        pst_instr_code="NQ",
        contract_months=["202612"],
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
    )

    assert bars.is_empty()
    assert not database.exists()

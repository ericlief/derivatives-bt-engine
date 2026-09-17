from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

import derivatives_bt_engine.data.pysystemtrade_import as importer
from derivatives_bt_engine.data.pysystemtrade_import import (
    ImportResult,
    ImportValidationError,
    build_sidecar,
)


TEST_SOURCE_COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def _record_test_source_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importer, "_source_git_commit", lambda _source: TEST_SOURCE_COMMIT
    )


def _write_source_tree(root: Path) -> Path:
    source = root / "pysystemtrade" / "data" / "futures"
    for directory in [
        "multiple_prices_csv",
        "adjusted_prices_csv",
        "roll_calendars_csv",
        "crypto_spread_roll_calendars_csv",
        "fx_prices_csv",
        "csvconfig",
    ]:
        (source / directory).mkdir(parents=True)

    (source / "multiple_prices_csv" / "TEST.csv").write_text(
        "DATETIME,CARRY,CARRY_CONTRACT,PRICE,PRICE_CONTRACT,FORWARD,FORWARD_CONTRACT\n"
        "2020-01-01 23:00:00,99,20200200,100,20200100,101,20200200\n"
        "2020-01-02 23:00:00,,20200200,101,20200100,102,20200200\n",
        encoding="utf-8",
    )
    (source / "adjusted_prices_csv" / "TEST.csv").write_text(
        "DATETIME,price\n"
        "2020-01-01 23:00:00,-1\n"
        "2020-01-02 23:00:00,0\n",
        encoding="utf-8",
    )
    roll_csv = (
        "DATE_TIME,current_contract,next_contract,carry_contract,\n"
        "2020-01-01 00:00:00,20200100,20200200,20200200,FALSE\n"
        "2020-01-01 00:00:00,20200200,20200300,20200300,FALSE\n"
    )
    (source / "roll_calendars_csv" / "TEST.csv").write_text(
        roll_csv, encoding="utf-8"
    )
    (source / "crypto_spread_roll_calendars_csv" / "BTC.csv").write_text(
        roll_csv, encoding="utf-8"
    )
    (source / "fx_prices_csv" / "EURUSD.csv").write_text(
        "DATETIME,PRICE\n2020-01-01 23:00:00,1.12\n", encoding="utf-8"
    )
    (source / "csvconfig" / "instrumentconfig.csv").write_text(
        "Instrument,Description,Pointsize,Currency,AssetClass,PerBlock,"
        "Percentage,PerTrade,Region\n"
        "TEST,Test future,10,USD,Equity,1.25,0,0,US\n",
        encoding="utf-8",
    )
    (source / "csvconfig" / "rollconfig.csv").write_text(
        "Instrument,HoldRollCycle,RollOffsetDays,CarryOffset,PricedRollCycle,ExpiryOffset\n"
        "TEST,FGHJKMNQUVXZ,5,1,FGHJKMNQUVXZ,0\n",
        encoding="utf-8",
    )
    (source / "csvconfig" / "spreadcosts.csv").write_text(
        "Instrument,SpreadCost\nTEST,0.5\n", encoding="utf-8"
    )
    return source


def test_build_sidecar_imports_typed_data_manifest_and_qa(tmp_path: Path) -> None:
    source = _write_source_tree(tmp_path)
    output = tmp_path / "pysystemtrade_reference.duckdb"
    source_file = source / "multiple_prices_csv" / "TEST.csv"
    source_digest = hashlib.sha256(source_file.read_bytes()).hexdigest()

    result = build_sidecar(source, output)

    assert result.output_path == output
    assert result.manifest_files == 8
    assert result.multiple_rows == 2
    assert result.adjusted_rows == 2
    assert result.source_git_commit == TEST_SOURCE_COMMIT
    assert source_digest == hashlib.sha256(source_file.read_bytes()).hexdigest()
    assert not list(tmp_path.glob(".*.duckdb.tmp-*"))

    con = duckdb.connect(str(output), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM raw.multiple_prices").fetchone()[0] == 2
        assert con.execute(
            "SELECT typeof(price_contract) FROM raw.multiple_prices LIMIT 1"
        ).fetchone()[0] == "VARCHAR"
        assert con.execute(
            "SELECT legacy_flag FROM raw.roll_calendars LIMIT 1"
        ).fetchone()[0] is False
        assert con.execute(
            "SELECT count(*) FROM qa.roll_calendar_duplicate_timestamps"
        ).fetchone()[0] == 2
        assert con.execute(
            "SELECT sha256 FROM meta.dataset_manifest WHERE relative_path = ?",
            ["multiple_prices_csv/TEST.csv"],
        ).fetchone()[0] == source_digest
        coverage = con.execute(
            """
            SELECT price_days, carry_days, forward_days,
                   adjusted_nonpositive_rows
            FROM qa.series_coverage
            WHERE instrument_code = 'TEST'
            """
        ).fetchone()
        assert coverage == (2, 1, 2, 2)
    finally:
        con.close()


def test_duplicate_logical_key_prevents_publication(tmp_path: Path) -> None:
    source = _write_source_tree(tmp_path)
    multiple = source / "multiple_prices_csv" / "TEST.csv"
    with multiple.open("a", encoding="utf-8") as handle:
        handle.write(
            "2020-01-02 23:00:00,100,20200200,101,20200100,102,20200200\n"
        )
    output = tmp_path / "pysystemtrade_reference.duckdb"

    with pytest.raises(ImportValidationError, match="duplicate key group"):
        build_sidecar(source, output)

    assert not output.exists()
    assert not list(tmp_path.glob(".*.duckdb.tmp-*"))


def test_failed_replacement_preserves_existing_output(tmp_path: Path) -> None:
    source = _write_source_tree(tmp_path)
    multiple = source / "multiple_prices_csv" / "TEST.csv"
    with multiple.open("a", encoding="utf-8") as handle:
        handle.write(
            "2020-01-02 23:00:00,100,20200200,101,20200100,102,20200200\n"
        )
    output = tmp_path / "pysystemtrade_reference.duckdb"
    output.write_bytes(b"existing sidecar")

    with pytest.raises(ImportValidationError, match="duplicate key group"):
        build_sidecar(source, output, replace=True)

    assert output.read_bytes() == b"existing sidecar"
    assert not list(tmp_path.glob(".*.duckdb.tmp-*"))


def test_refuses_to_overwrite_protected_globex_database(tmp_path: Path) -> None:
    source = _write_source_tree(tmp_path)
    globex = tmp_path / "globex.duckdb"

    with pytest.raises(ImportValidationError, match="Globex source-of-record"):
        build_sidecar(source, globex, globex_db=globex)


def test_refuses_existing_output_without_replace(tmp_path: Path) -> None:
    source = _write_source_tree(tmp_path)
    output = tmp_path / "pysystemtrade_reference.duckdb"
    output.write_bytes(b"keep me")

    with pytest.raises(FileExistsError, match="--replace"):
        build_sidecar(source, output)

    assert output.read_bytes() == b"keep me"


def test_cli_main_returns_success_value_not_result_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "sidecar.duckdb"
    monkeypatch.setattr(
        importer,
        "build_sidecar",
        lambda *args, **kwargs: ImportResult(
            output_path=output,
            source_git_commit=TEST_SOURCE_COMMIT,
            manifest_files=8,
            multiple_rows=2,
            adjusted_rows=2,
        ),
    )

    result = importer.main(
        ["--source", str(tmp_path / "source"), "--output", str(output)]
    )

    assert result is None
    assert "Built" in capsys.readouterr().out

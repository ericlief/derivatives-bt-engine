"""Tests for roll-aware Interactive Brokers dated-contract acquisition."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.data import ib_futures_dataloader as ib_loader
from derivatives_bt_engine.data.ib_futures_dataloader import (
    IBStitchedHistoryProvider,
    _maximum_hold_cycle_gap_months,
    _roll_aware_duration,
    load_roll_cycle_ib_contract_history,
)
from derivatives_bt_engine.data.ib_futures_history_store import (
    IBFuturesHistoryStore,
)


def test_hold_cycle_gap_wraps_to_the_following_year() -> None:
    """Annual and quarterly cycles include their year-end spacing."""
    assert _maximum_hold_cycle_gap_months("Z") == 12
    assert _maximum_hold_cycle_gap_months("HMUZ") == 3
    assert _maximum_hold_cycle_gap_months("FGHJKMNQUVXZ") == 1


def test_roll_aware_duration_keeps_one_year_for_monthly_cycle() -> None:
    """A short offset plus monthly spacing fits in one year."""
    assert _roll_aware_duration("1 Y", -10, "FGHJKMNQUVXZ") == "1 Y"


def test_roll_aware_duration_expands_annual_cycle_to_two_years() -> None:
    """An annual successor must reach the prior annual contract's cutoff."""
    assert _roll_aware_duration("1 Y", -60, "Z") == "2 Y"
    assert _roll_aware_duration("1 Y", -60, "X") == "2 Y"


def test_roll_aware_duration_includes_spacing_for_far_deferred_cycle() -> None:
    """A 700-day lead plus quarterly spacing requires three years."""
    assert _roll_aware_duration("1 Y", -700, "HMUZ") == "3 Y"


def test_roll_aware_duration_preserves_larger_requested_window() -> None:
    """Explicit caller coverage is retained when it already suffices."""
    assert _roll_aware_duration("4 Y", -700, "HMUZ") == "4 Y"


def test_stitched_cache_identity_uses_effective_duration(monkeypatch) -> None:
    """A truncated annual bundle cannot satisfy a later two-year request."""
    monkeypatch.setattr(
        ib_loader,
        "_load_roll_config",
        lambda *_args, **_kwargs: ("Z", -60, -1, "HKNUZ"),
    )
    monkeypatch.setattr(
        ib_loader,
        "_history_input_fingerprint",
        lambda path: {"path": str(path), "size": 0, "mtime_ns": 0},
    )
    expanded = IBStitchedHistoryProvider(ib=None, duration="1 Y")
    explicit = IBStitchedHistoryProvider(ib=None, duration="2 Y")

    expanded_path = expanded._cache_directory("ZW", "WHEAT", None, "ZW")
    explicit_path = explicit._cache_directory("ZW", "WHEAT", None, "ZW")

    assert expanded_path == explicit_path


def test_stitched_cache_identity_includes_coverage_policy(monkeypatch) -> None:
    """Strengthened raw-coverage checks invalidate older derived bundles."""
    monkeypatch.setattr(
        ib_loader,
        "_load_roll_config",
        lambda *_args, **_kwargs: ("Z", -60, -1, "HKNUZ"),
    )
    monkeypatch.setattr(
        ib_loader,
        "_history_input_fingerprint",
        lambda path: {"path": str(path), "size": 0, "mtime_ns": 0},
    )
    provider = IBStitchedHistoryProvider(ib=None, duration="1 Y")
    current_path = provider._cache_directory("ZW", "WHEAT", None, "ZW")

    monkeypatch.setattr(ib_loader, "IB_DATED_COVERAGE_VERSION", 0)
    prior_path = provider._cache_directory("ZW", "WHEAT", None, "ZW")

    assert current_path != prior_path


def test_stored_history_rejects_shorter_expired_contract_fetch(
    tmp_path,
    monkeypatch,
) -> None:
    """A one-year raw fetch cannot satisfy annual two-year roll coverage."""
    history_db = tmp_path / "ib_futures_history.duckdb"
    store = IBFuturesHistoryStore(history_db)
    store.initialize()
    store.upsert_contract_and_bars(
        contract={
            "con_id": 202512,
            "pst_instr_code": "WHEAT",
            "ib_symbol": "ZW",
            "exchange": "CBOT",
            "currency": "USD",
            "multiplier": 50.0,
            "price_magnifier": 1.0,
            "contract_month": "202512",
            "expiration": date(2025, 12, 12),
            "local_symbol": "ZWZ5",
            "trading_class": "ZW",
        },
        bars=pl.DataFrame(
            {
                "date": [date(2024, 12, 13), date(2025, 12, 12)],
                "close": [550.0, 540.0],
            }
        ),
        duration="1 Y",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
        requested_end="20251213 23:59:59",
        complete_contract=True,
    )
    monkeypatch.setattr(
        ib_loader,
        "load_broker_mapping",
        lambda _path: pl.DataFrame(
            {
                "instrument_code": ["WHEAT"],
                "ib_symbol": ["ZW"],
                "ib_exchange": ["CBOT"],
                "ib_currency": ["USD"],
                "ib_multiplier": [50.0],
                "price_magnifier": [1.0],
                "ignore_weekly": [False],
            }
        ),
    )
    monkeypatch.setattr(
        ib_loader,
        "_load_roll_config",
        lambda *_args, **_kwargs: ("Z", -60, 0, "Z"),
    )

    with pytest.raises(RuntimeError, match=r"2 Y coverage.*202512"):
        load_roll_cycle_ib_contract_history(
            None,
            pst_instr_code="WHEAT",
            as_of=date(2026, 11, 10),
            prior_years=1,
            db_path=tmp_path / "unused_reference.duckdb",
            mapping_path=tmp_path / "unused_mapping.yaml",
            history_db_path=history_db,
            update_history=False,
        )

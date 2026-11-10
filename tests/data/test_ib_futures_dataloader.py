"""Tests for roll-aware Interactive Brokers dated-contract acquisition."""

from derivatives_bt_engine.data import ib_futures_dataloader as ib_loader
from derivatives_bt_engine.data.ib_futures_dataloader import (
    IBStitchedHistoryProvider,
    _maximum_hold_cycle_gap_months,
    _roll_aware_duration,
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

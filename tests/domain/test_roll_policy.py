import pytest

from derivatives_bt_engine.domain.roll_policy import (
    ContractRollPolicy,
    carver_aligned_globex_roll_policy_set,
    parse_roll_policy_overrides,
)


def test_requested_symbol_override_precedes_raw_root_default():
    policies = carver_aligned_globex_roll_policy_set()

    full, full_resolution = policies.resolve("ZC", "ZC")
    micro, micro_resolution = policies.resolve("MZC", "ZC")

    assert full.hold_roll_cycle == "Z"
    assert full.roll_offset_days == -60
    assert full.carver_instrument == "CORN"
    assert full_resolution == "raw_symbol_default"
    assert micro.hold_roll_cycle == "HKNUZ"
    assert micro.roll_offset_days == -30
    assert micro.carver_instrument == "CORN_mini"
    assert micro_resolution == "requested_symbol_override"


def test_unmapped_requested_symbol_inherits_raw_root_policy():
    policy, resolution = carver_aligned_globex_roll_policy_set().resolve(
        "MES",
        "ES",
    )

    assert policy.hold_roll_cycle == "HMUZ"
    assert policy.roll_offset_days == -5
    assert resolution == "raw_symbol_default"


def test_run_override_replaces_pinned_policy():
    overrides = parse_roll_policy_overrides(["MZC:Z:-60"])
    policies = carver_aligned_globex_roll_policy_set(overrides)

    policy, resolution = policies.resolve("MZC", "ZC")

    assert policy.hold_roll_cycle == "Z"
    assert policy.roll_offset_days == -60
    assert policy.source == "run_override"
    assert resolution == "requested_symbol_override"


def test_roll_policy_validation_rejects_invalid_cycle():
    with pytest.raises(ValueError, match="invalid CME month"):
        ContractRollPolicy(
            policy_id="bad",
            hold_roll_cycle="HZ!",
            roll_offset_days=-5,
        )

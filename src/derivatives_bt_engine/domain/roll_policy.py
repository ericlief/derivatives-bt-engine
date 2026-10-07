"""Reusable held-contract roll policies for raw futures contract data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Mapping

from derivatives_bt_engine.domain.instruments import (
    CARVER_ROLL_POLICY_SOURCE_COMMIT,
    CME_MONTH_LETTERS,
    GLOBEX_ROLL_POLICY_DEFAULTS,
    GLOBEX_ROLL_POLICY_OVERRIDES,
)


VOLUME_FRONT_POLICY_ID = "volume_front"


@dataclass(frozen=True)
class ContractRollPolicy:
    """One contract-agnostic algorithm and its instrument parameters."""

    policy_id: str
    mode: str = "calendar"
    hold_roll_cycle: str = ""
    roll_offset_days: int = 0
    source: str = ""
    carver_instrument: str = ""

    def __post_init__(self) -> None:
        """Validate mode-specific cycle and pre-expiry offset invariants."""
        if self.mode not in {"calendar", "volume_front"}:
            raise ValueError(
                "roll policy mode must be 'calendar' or 'volume_front'"
            )
        if self.mode == "volume_front":
            if self.hold_roll_cycle:
                raise ValueError("volume-front policy cannot specify a hold cycle")
            return
        if not self.hold_roll_cycle:
            raise ValueError("calendar roll policy requires a hold cycle")
        invalid = set(self.hold_roll_cycle) - set(CME_MONTH_LETTERS)
        if invalid:
            raise ValueError(
                f"hold cycle contains invalid CME month letters: {sorted(invalid)}"
            )
        if len(set(self.hold_roll_cycle)) != len(self.hold_roll_cycle):
            raise ValueError("hold cycle contains duplicate month letters")
        month_numbers = [CME_MONTH_LETTERS[letter] for letter in self.hold_roll_cycle]
        if month_numbers != sorted(month_numbers):
            raise ValueError("hold cycle must be in calendar-month order")
        if self.roll_offset_days > 0:
            raise ValueError("roll offset must be zero or before expiration")

    @property
    def hold_month_numbers(self) -> tuple[int, ...]:
        """Translate the ordered CME month-letter cycle to month numbers."""
        return tuple(CME_MONTH_LETTERS[letter] for letter in self.hold_roll_cycle)

    @property
    def cache_key(self) -> str:
        """Return a stable identity for caches derived under this policy."""
        if self.mode == "volume_front":
            return VOLUME_FRONT_POLICY_ID
        identity = (
            f"{self.policy_id}|{self.mode}|{self.hold_roll_cycle}|"
            f"{self.roll_offset_days}|{self.source}|{self.carver_instrument}"
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return f"calendar_{digest}"

    def as_dict(self) -> dict[str, object]:
        """Serialize the policy for run manifests and history metadata."""
        return {
            "policy_id": self.policy_id,
            "mode": self.mode,
            "hold_roll_cycle": self.hold_roll_cycle or None,
            "roll_offset_days": (
                self.roll_offset_days if self.mode == "calendar" else None
            ),
            "source": self.source or None,
            "carver_instrument": self.carver_instrument or None,
        }


VOLUME_FRONT_POLICY = ContractRollPolicy(
    policy_id=VOLUME_FRONT_POLICY_ID,
    mode="volume_front",
    source="legacy_globex_default",
)


def _policy_from_spec(spec: Mapping[str, object]) -> ContractRollPolicy:
    """Convert one pinned instrument metadata record into a roll policy."""
    carver_instrument = str(spec["carver_instrument"])
    return ContractRollPolicy(
        policy_id=f"carver_{carver_instrument}",
        hold_roll_cycle=str(spec["hold_roll_cycle"]),
        roll_offset_days=int(spec["roll_offset_days"]),
        source=f"pysystemtrade_{CARVER_ROLL_POLICY_SOURCE_COMMIT[:12]}",
        carver_instrument=carver_instrument,
    )


@dataclass(frozen=True)
class RollPolicySet:
    """Default policy with requested-symbol and raw-root overrides."""

    default: ContractRollPolicy = VOLUME_FRONT_POLICY
    raw_defaults: Mapping[str, ContractRollPolicy] = field(default_factory=dict)
    overrides: Mapping[str, ContractRollPolicy] = field(default_factory=dict)

    def resolve(
        self,
        requested_symbol: str,
        raw_symbol: str,
    ) -> tuple[ContractRollPolicy, str]:
        """Resolve requested-symbol override, raw-root default, then fallback."""
        requested = requested_symbol.upper()
        raw = raw_symbol.upper()
        if requested in self.overrides:
            return self.overrides[requested], "requested_symbol_override"
        if raw in self.raw_defaults:
            return self.raw_defaults[raw], "raw_symbol_default"
        return self.default, "policy_set_default"

    def with_overrides(
        self,
        overrides: Mapping[str, ContractRollPolicy] | None,
    ) -> "RollPolicySet":
        """Return an immutable copy with normalized run-local overrides."""
        if not overrides:
            return self
        merged = dict(self.overrides)
        merged.update({symbol.upper(): policy for symbol, policy in overrides.items()})
        return replace(self, overrides=merged)


def carver_aligned_globex_roll_policy_set(
    overrides: Mapping[str, ContractRollPolicy] | None = None,
) -> RollPolicySet:
    """Return pinned Carver defaults plus optional run-local overrides."""
    raw_defaults = {
        symbol: _policy_from_spec(spec)
        for symbol, spec in GLOBEX_ROLL_POLICY_DEFAULTS.items()
    }
    requested_overrides = {
        symbol: _policy_from_spec(spec)
        for symbol, spec in GLOBEX_ROLL_POLICY_OVERRIDES.items()
    }
    return RollPolicySet(
        raw_defaults=raw_defaults,
        overrides=requested_overrides,
    ).with_overrides(overrides)


def calendar_roll_policy_override(
    symbol: str,
    hold_roll_cycle: str,
    roll_offset_days: int,
) -> tuple[str, ContractRollPolicy]:
    """Build one explicit CLI/programmatic calendar-policy override."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("roll policy override requires a symbol")
    cycle = hold_roll_cycle.strip().upper()
    return normalized_symbol, ContractRollPolicy(
        policy_id=f"override_{normalized_symbol}_{cycle}_{roll_offset_days}",
        hold_roll_cycle=cycle,
        roll_offset_days=roll_offset_days,
        source="run_override",
    )


def parse_roll_policy_overrides(
    values: list[str] | tuple[str, ...],
) -> dict[str, ContractRollPolicy]:
    """Parse repeated ``SYMBOL:HOLD_CYCLE:ROLL_OFFSET_DAYS`` values."""
    parsed: dict[str, ContractRollPolicy] = {}
    for value in values:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError(
                "roll policy override must be SYMBOL:HOLD_CYCLE:ROLL_OFFSET_DAYS"
            )
        symbol, cycle, offset = parts
        try:
            offset_days = int(offset)
        except ValueError as exc:
            raise ValueError(
                f"roll policy offset must be an integer, got {offset!r}"
            ) from exc
        normalized_symbol, policy = calendar_roll_policy_override(
            symbol,
            cycle,
            offset_days,
        )
        if normalized_symbol in parsed:
            raise ValueError(
                f"duplicate roll policy override for {normalized_symbol}"
            )
        parsed[normalized_symbol] = policy
    return parsed

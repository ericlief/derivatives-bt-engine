"""Load broker/account execution restrictions from versioned configuration.

The source instrument universe remains a research catalogue. This module
applies a named execution profile when building live broker mappings, without
embedding account-specific venue or product policy in calculations or Phase 1
pipeline code. Restricted instruments remain available to historical loaders
but are omitted from live market snapshots.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional


DEFAULT_EXECUTION_PROFILE = "ibkr_us"
DEFAULT_EXECUTION_PROFILES_PATH = Path(__file__).with_name(
    "ib_execution_profiles.json"
)


@lru_cache(maxsize=None)
def load_execution_profiles(
    path: Path | str = DEFAULT_EXECUTION_PROFILES_PATH,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return validated execution profiles from the packaged JSON config.

    Parameters
    ----------
    path
        JSON file containing a top-level ``profiles`` object. Each profile can
        define ``product_restrictions`` and ``exchange_restrictions`` maps from
        uppercase IB identifiers to stable exclusion reasons.

    Returns
    -------
    dict
        Profile mappings normalized to uppercase identifiers and string
        reasons. The result is cached per resolved path.
    """
    config_path = Path(path).expanduser().resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw_profiles = raw.get("profiles") if isinstance(raw, dict) else None
    if not isinstance(raw_profiles, dict):
        raise ValueError(
            "execution profile config must contain a profiles object: "
            f"{config_path}"
        )

    profiles: dict[str, dict[str, dict[str, str]]] = {}
    for profile_name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            raise ValueError(
                f"execution profile {profile_name!r} must be an object"
            )
        profile: dict[str, dict[str, str]] = {}
        for restriction_type in (
            "product_restrictions",
            "exchange_restrictions",
        ):
            raw_restrictions = raw_profile.get(restriction_type, {})
            if not isinstance(raw_restrictions, dict):
                raise ValueError(
                    f"{profile_name}.{restriction_type} must be an object"
                )
            profile[restriction_type] = {
                str(identifier).strip().upper(): str(reason).strip()
                for identifier, reason in raw_restrictions.items()
                if str(identifier).strip() and str(reason).strip()
            }
        profiles[str(profile_name).strip()] = profile
    return profiles


def resolve_execution_eligibility(
    symbol: str,
    *,
    ib_symbol: Optional[str] = None,
    exchange: Optional[str] = None,
    profile: str = DEFAULT_EXECUTION_PROFILE,
    config_path: Path | str = DEFAULT_EXECUTION_PROFILES_PATH,
) -> dict[str, object]:
    """Return execution status for one mapped instrument and account profile.

    Product identifiers are checked before the exchange so the configuration
    can carry a more specific reason. A missing restriction means the live
    snapshot must still verify contract qualification and account permission.
    """
    profile_config = load_execution_profiles(config_path).get(profile, {})
    product_restrictions = profile_config.get("product_restrictions", {})
    exchange_restrictions = profile_config.get("exchange_restrictions", {})

    reason = next(
        (
            product_restrictions[identifier.upper()]
            for identifier in (ib_symbol, symbol)
            if identifier and identifier.upper() in product_restrictions
        ),
        None,
    )
    if reason is None and exchange:
        reason = exchange_restrictions.get(exchange.upper())
    return {
        "execution_profile": profile,
        "execution_eligible": reason is None,
        "execution_restriction_reason": reason,
    }

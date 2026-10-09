"""Convert executable-contract cost rates into annual subsystem SR costs.

Phase 1 measures one-way trading and roll costs as fractions of one contract's
annual dollar volatility.  Phase 2 supplies the combined subsystem's measured
turnover.  This module joins those two concepts without knowing anything about
CSV schemas, market-data providers, or portfolio selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AnnualSRCost:
    """Annual trading, roll, and total costs expressed in SR units."""

    ann_trade_cost_sr: float
    ann_roll_cost_sr: float
    ann_cost_sr: float


def calculate_annual_sr_cost(
    *,
    trade_sr: float,
    roll_sr: float,
    subsystem_turnover: float,
    ann_rolls: float,
) -> AnnualSRCost:
    """Return annual SR costs for one combined subsystem.

    Parameters
    ----------
    trade_sr
        One-way non-roll trading cost divided by annual dollar volatility per
        contract.
    roll_sr
        Cost of one complete contract roll divided by annual dollar
        volatility.  Phase 1 defines this as one spread plus two commissions,
        so it must not be doubled again here.
    subsystem_turnover
        Annual turnover measured from the actual combined forecast position.
    ann_rolls
        Expected complete contract rolls per year under the selected roll
        policy.

    Returns
    -------
    AnnualSRCost
        Trading cost, roll cost, and their sum in annual SR units.
    """
    values = {
        "trade_sr": trade_sr,
        "roll_sr": roll_sr,
        "subsystem_turnover": subsystem_turnover,
        "ann_rolls": ann_rolls,
    }
    for name, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")

    ann_trade_cost_sr = trade_sr * subsystem_turnover
    ann_roll_cost_sr = roll_sr * ann_rolls
    return AnnualSRCost(
        ann_trade_cost_sr=ann_trade_cost_sr,
        ann_roll_cost_sr=ann_roll_cost_sr,
        ann_cost_sr=ann_trade_cost_sr + ann_roll_cost_sr,
    )

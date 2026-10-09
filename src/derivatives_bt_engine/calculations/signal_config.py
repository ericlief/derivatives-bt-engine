"""Configuration object for comparing the repository's trend models.

The calculation implementations live in :mod:`continuous_momentum` and
:mod:`goulding`.  This module only provides the combined parameter object
used by comparison scripts and backtests that intentionally run both models.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from derivatives_bt_engine.calculations.continuous_momentum import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
)
from derivatives_bt_engine.calculations.goulding import (
    GOULDING_FAST_MONTHS,
    GOULDING_SLOW_MONTHS,
)


class SignalModel(str, Enum):
    """Economic construction used to turn prices into trend strength."""

    CONTINUOUS = "continuous"
    GOULDING_MONTHLY = "goulding_monthly"


@dataclass
class SignalSpec:
    """Strategy/test-level signal configuration -- deliberately separate
    from instrument metadata (instruments.py's INSTRUMENTS dict): this
    describes HOW to compute a signal from a price series, not WHICH
    instrument it's for. annualization_days is the one exception carried
    here as a plain field rather than looked up internally -- the caller
    resolves it per-instrument (instruments.resolve_annualization_days) and
    passes the result in, keeping this dataclass itself instrument-
    agnostic and easily reusable/comparable across a parameter sweep.

    Defaults reproduce this project's original, long-standing continuous-
    model behavior (63/252-day windows, 0.4/0.6 weights, 0.5 discount,
    252-day annualization); use the goulding() factory below for Goulding
    et al.'s own 2/12-month parameterization instead of setting
    fast_months/slow_months by hand."""
    fast_window: int = DEFAULT_FAST_WINDOW
    slow_window: int = DEFAULT_SLOW_WINDOW
    vol_fast_window: Optional[int] = None  # None -> fast_window (horizon-matched)
    vol_slow_window: Optional[int] = None  # None -> slow_window (horizon-matched)
    annualization_days: int = DEFAULT_ANNUALIZATION_DAYS
    w_fast: float = 0.4
    w_slow: float = 0.6
    discount: float = 0.5

    fast_months: int = GOULDING_FAST_MONTHS
    slow_months: int = GOULDING_SLOW_MONTHS

    # Goulding's eq. 7 dynamic-reweight inputs -- plain pass-through values
    # for _goulding_direction(), NOT estimated here. Pooled, expanding-window
    # a_Co/a_Re ESTIMATION (eq. 8-10) is a multi-symbol process that doesn't
    # fit this dataclass's per-symbol, stateless fields -- see
    # calculations.goulding.estimate_mixing_params, which implements it and passes the
    # result into _goulding_direction directly. 0.5/0.5 is the paper's own
    # uninformed fallback and collapses eq. 7 to a flat, no-op reweight.
    a_co: float = 0.5
    a_re: float = 0.5

    def __post_init__(self):
        """Reject contradictory horizons, weights, and mixing parameters."""
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window/slow_window must be positive")
        if self.fast_window >= self.slow_window:
            # Not mathematically required by continuous_momentum itself,
            # but "fast" >= "slow" contradicts the field names and is
            # almost certainly a caller error (e.g. args swapped) rather
            # than an intentional config -- reject loudly instead of
            # silently computing a "fast" trend slower than its own "slow"
            # counterpart.
            raise ValueError(f"fast_window ({self.fast_window}) must be < slow_window ({self.slow_window})")
        if self.vol_fast_window is not None and self.vol_fast_window <= 0:
            raise ValueError("vol_fast_window must be positive when set")
        if self.vol_slow_window is not None and self.vol_slow_window <= 0:
            raise ValueError("vol_slow_window must be positive when set")
        if self.fast_months <= 0 or self.slow_months <= 0:
            raise ValueError("fast_months/slow_months must be positive")
        if self.fast_months >= self.slow_months:
            raise ValueError(f"fast_months ({self.fast_months}) must be < slow_months ({self.slow_months})")
        if self.w_fast + self.w_slow <= 0:
            # continuous_momentum's ts denominator clips at 1e-12 so this
            # wouldn't crash -- it would silently degenerate to ts=tanh(0)=0
            # every row instead. Catch the misconfiguration loudly instead.
            raise ValueError("w_fast + w_slow must be positive")
        if not (0.0 <= self.a_co <= 1.0) or not (0.0 <= self.a_re <= 1.0):
            raise ValueError("a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range)")

    @staticmethod
    def goulding(fast_months: int = GOULDING_FAST_MONTHS, slow_months: int = GOULDING_SLOW_MONTHS,
                 a_co: float = 0.5, a_re: float = 0.5) -> "SignalSpec":
        """Convenience factory for Goulding et al.'s own parameterization
        (2-month fast / 12-month slow, genuine calendar months via
        goulding_monthly's group_by_dynamic -- not a trading-day
        approximation)."""
        return SignalSpec(fast_months=fast_months, slow_months=slow_months, a_co=a_co, a_re=a_re)

    def continuous_kwargs(self) -> dict:
        """Unpack the subset of fields continuous_momentum() takes."""
        return dict(
            fast_window=self.fast_window, slow_window=self.slow_window,
            vol_fast_window=self.vol_fast_window, vol_slow_window=self.vol_slow_window,
            annualization_days=self.annualization_days,
            w_fast=self.w_fast, w_slow=self.w_slow, discount=self.discount,
        )

    def goulding_kwargs(self) -> dict:
        """Unpack the subset of fields goulding_monthly() takes."""
        return dict(fast_months=self.fast_months, slow_months=self.slow_months)

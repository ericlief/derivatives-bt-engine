"""Configure individual legs of simulated option positions."""

from dataclasses import dataclass
from typing import Optional
from derivatives_bt_engine.backtest.options.types import OptionsType
from derivatives_bt_engine.backtest.position_side import PositionSide
from typing import Tuple


@dataclass
class OptionLegConfig:
    """Describe one option leg's type, side, delta, and expiry criteria.

    Exactly one delta criterion may be supplied, except for a derived long
    spread leg validated by ``MultiLegOptionStrategyConfig``.  Exactly one DTE
    criterion is always required.
    """

    option_type: OptionsType
    position_side: PositionSide
    delta_target: Optional[float] = None
    delta_range: Optional[Tuple[float, float]] = None
    dte_target: Optional[int] = None
    dte_range: Optional[Tuple[int, int]] = None
    early_close_after_dit: Optional[int] = None  # For complex assymetric strategies
    early_close_on_dte: Optional[int] = None  # For complex assymetric strategies

    def __post_init__(self):
        """Validate mutually exclusive delta and DTE selection criteria."""
        # delta_target/delta_range may both be left None for a LONG leg whose
        # strike is instead derived from its paired SHORT leg's strike +/-
        # MultiLegOptionStrategyConfig.max_spread_width (use_spread_width=True)
        # -- validated there, since it needs to see the whole legs list.
        if self.dte_target is None and self.dte_range is None:
            raise ValueError("Must provide either dte_target or dte_range")

        if self.delta_target is not None and self.delta_range is not None:
            raise ValueError("Provide only one of delta_target or delta_range")

        if self.dte_target is not None and self.dte_range is not None:
            raise ValueError("Provide only one of dte_target or dte_range")

from dataclasses import dataclass
from typing import Optional
from options_bt.domain.enums import OptionType, PositionSide
from typing import Tuple

@dataclass
class OptionLegConfig:
    option_type: OptionType
    position_side: PositionSide
    delta_target: Optional[float] = None
    delta_range: Optional[Tuple[float, float]] = None
    dte_target: Optional[int] = None
    dte_range: Optional[Tuple[int, int]] = None
    quantity: int = 1
    early_close_days: Optional[int] = None  # For complex assymetric strategies

    def __post_init__(self):
        if self.delta_target is None and self.delta_range is None:
            raise ValueError("Must provide either delta_target or delta_range")

        if self.dte_target is None and self.dte_range is None:
            raise ValueError("Must provide either dte_target or dte_range")

        if self.delta_target is not None and self.delta_range is not None:
            raise ValueError("Provide only one of delta_target or delta_range")

        if self.dte_target is not None and self.dte_range is not None:
            raise ValueError("Provide only one of dte_target or dte_range")
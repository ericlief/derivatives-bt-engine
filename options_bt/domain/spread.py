from dataclasses import dataclass, field
from typing import List, Optional, Dict
import pandas as pd
import logging
from options_bt.domain.enums import SpreadType
from options_bt.domain.position import Position

logger = logging.getLogger(__name__)

@dataclass
class Spread:
    """Class representing a multi-leg option spread."""
    spread_id: Optional[int] = None
    spread_type: SpreadType
    legs: List[Position] = field(default_factory=list)
    entry_date: Optional[pd.Timestamp] = None
    leg_ratios: Dict[int, float] = field(default_factory=dict)  # Maps leg number to ratio
    spread_price: Optional[float] = None
    
    def __post_init__(self):
        """Validate spread configuration after initialization."""
        if isinstance(self.spread_type, str):
            self.spread_type = SpreadType(self.spread_type.lower())
            
        # Set default leg ratios if not provided
        if not self.leg_ratios:
            self.leg_ratios = {i: 1.0 for i in range(len(self.legs))}
            
        self.validate_spread()

    def validate_spread(self):
        """Validate spread configuration based on type."""
        if self.spread_type == SpreadType.VERTICAL:
            if len(self.legs) != 2:
                raise ValueError("Vertical spread must have exactly 2 legs")
            # Validate strikes and sides
            if self.legs[0].strike == self.legs[1].strike:
                raise ValueError("Vertical spread legs must have different strikes")
            if self.legs[0].position_side == self.legs[1].position_side:
                raise ValueError("Vertical spread legs must have opposite sides")
                
        elif self.spread_type == SpreadType.CALENDAR:
            if len(self.legs) != 2:
                raise ValueError("Calendar spread must have exactly 2 legs")
            # Validate expiration dates and strikes
            if self.legs[0].expire_date == self.legs[1].expire_date:
                raise ValueError("Calendar spread legs must have different expiration dates")
            if self.legs[0].strike != self.legs[1].strike:
                raise ValueError("Calendar spread legs must have same strike")
                
        elif self.spread_type == SpreadType.BUTTERFLY:
            if len(self.legs) != 3:
                raise ValueError("Butterfly spread must have exactly 3 legs")
            # Validate strikes and ratios
            strikes = sorted([leg.strike for leg in self.legs])
            if not (strikes[1] - strikes[0] == strikes[2] - strikes[1]):
                raise ValueError("Butterfly spread must have equal wing widths")
            # Validate butterfly ratios (1:2:1)
            if self.leg_ratios != {0: 1.0, 1: 2.0, 2: 1.0}:
                raise ValueError("Butterfly spread must have 1:2:1 ratio")
                
        elif self.spread_type == SpreadType.IRON_CONDOR:
            if len(self.legs) != 4:
                raise ValueError("Iron condor must have exactly 4 legs")
            # Additional iron condor validations would go here

    def calculate_margin(self, leverage: float = 1.0) -> float:
        """Calculate total margin requirement for the spread."""
        if self.spread_type == SpreadType.NONE:
            return sum(leg.calculate_margin(leverage) for leg in self.legs)
            
        # For defined risk spreads, margin is the maximum possible loss
        if self.spread_type == SpreadType.VERTICAL:
            strikes = sorted([leg.strike for leg in self.legs])
            return abs(strikes[1] - strikes[0]) * 100
            
        elif self.spread_type == SpreadType.IRON_CONDOR:
            legs = sorted(self.legs, key=lambda x: x.strike)
            put_spread_width = abs(legs[1].strike - legs[0].strike)
            call_spread_width = abs(legs[3].strike - legs[2].strike)
            return max(put_spread_width, call_spread_width) * 100
            
        elif self.spread_type == SpreadType.BUTTERFLY:
            legs = sorted(self.legs, key=lambda x: x.strike)
            return abs(legs[2].strike - legs[0].strike) * 100
            
        # For undefined risk spreads, sum individual margins
        return sum(leg.calculate_margin(leverage) for leg in self.legs)

    def calculate_spread_price(self) -> float:
        """Calculate the net price of the spread."""
        return sum(leg.entry_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))

    def calculate_pnl(self) -> float:
        """Calculate total P&L for the spread."""
        return sum(leg.calculate_pnl() * self.leg_ratios[i] for i, leg in enumerate(self.legs))

    def to_dict(self) -> Dict:
        """Convert spread to dictionary format."""
        return {
            'spread_type': self.spread_type.value,
            'spread_id': self.spread_id,
            'entry_date': self.entry_date,
            'legs': [leg.to_dict() for leg in self.legs],
            'leg_ratios': self.leg_ratios,
            'spread_price': self.spread_price or self.calculate_spread_price(),
            'margin_required': self.calculate_margin()
        } 
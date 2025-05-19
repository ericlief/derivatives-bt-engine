from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple
from functools import cached_property
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

from options_bt.domain.enums import *
from options_bt.utils.logger import setup_logger

logger = setup_logger()
 
 

@dataclass
class BasePosition(ABC):
    """Base class for any trading position."""
    trade_id: int
    quantity: int
    position_side: Union[PositionSide, str]
    entry_date: pd.Timestamp
    entry_price: float

    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.position_side, str):
            self.position_side = PositionSide(self.position_side.lower())

    @abstractmethod
    def is_closed(self) -> bool:
        """Check if position is closed. Must be implemented by subclasses."""
        pass
    
    
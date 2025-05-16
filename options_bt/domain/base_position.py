from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple
from functools import cached_property
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np


from options_bt.utils.logger import setup_logger

logger = setup_logger()
 
 

@dataclass
class BasePosition(ABC):
    """Base class for any trading position."""
    trade_id: int
    quantity: int
    entry_date: pd.Timestamp
    entry_price: float

    @abstractmethod
    def is_closed(self) -> bool:
        """Check if position is closed. Must be implemented by subclasses."""
        pass
    
    
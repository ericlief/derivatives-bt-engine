from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple
from functools import cached_property

import pandas as pd
import numpy as np

import logging

from options_bt.bt import setup_logger

logger = setup_logger()
 
 

@dataclass
class BasePosition:
    trade_id: int
    quantity: int
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None

    def is_closed(self) -> bool:
        return self.exit_date is not None
    
    
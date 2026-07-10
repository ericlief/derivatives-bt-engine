from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
import os
import time
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property
from abc import ABC, abstractmethod
import polars as pl
import numpy as np

import logging

from options_bt.domain.enums import OptionType, PositionSide
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

class BaseSignalGenerator(ABC):
    """Class to generate signals for trading."""

    def __init__(self, config: Dict):

        self.config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig] = config
        self.start_date: date = date.fromisoformat(config.start_date)
        self.end_date: date = date.fromisoformat(config.end_date)
        # self.symbol_list: Optional[list] = config.symbol_list or []

    @abstractmethod
    def fetch_data(self) -> pl.DataFrame:
        pass
"""Define the base interface shared by historical signal generators."""

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

from derivatives_bt_engine.backtest.options.types import OptionsType
from derivatives_bt_engine.backtest.position_side import PositionSide
from derivatives_bt_engine.backtest.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from derivatives_bt_engine.logging_config import setup_logger

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

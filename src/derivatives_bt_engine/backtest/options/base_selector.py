"""Define the shared interface for historical option-contract selectors.

The option engine turns an option chain into dated executable candidates.
Futures adapters do not inherit this interface because they have neither
option legs nor contract-selection criteria.
"""

from datetime import date
from typing import Union
from abc import ABC, abstractmethod

import polars as pl

from derivatives_bt_engine.backtest.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig

class BaseContractSelector(ABC):
    """Common date-window state for option contract selectors.

    Parameters
    ----------
    config
        Single- or multi-leg option configuration whose ISO date strings
        bound candidate selection.
    """

    def __init__(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]):
        """Initialize the selector's inclusive strategy date window."""
        self.config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig] = config
        self.start_date: date = date.fromisoformat(config.start_date)
        self.end_date: date = date.fromisoformat(config.end_date)

    @abstractmethod
    def fetch_data(self) -> pl.DataFrame:
        """Return the source option-chain frame owned by the selector."""
        raise NotImplementedError

from dataclasses import dataclass
from typing import Optional, Dict, Union
import pandas as pd
from options_bt.domain.enums import OptionType, PositionSide, SpreadType
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

@dataclass
class BaseTrade:
    """
    Represents a completed trade with entry and exit details.
    Designed to wdork efficiently with pandas DataFrames.
    """
    trade_id: int
    quantity: int
    option_type: Union[OptionType, str]
    position_side: Union[PositionSide, str]
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expire_date: pd.Timestamp
    entry_delta: float
    exit_delta: float
    entry_dte: int
    days_held: int
    underlying_entry: float
    underlying_exit: float
    strike: float
    entry_price: float
    exit_price: float
    capital_used: float  # Margin or cost basis
    option_bp: float    # Available buying power after trade
    return_on_margin: float
    close_reason: str
    pnl: float
    spread_type: Optional[str] = SpreadType.NONE.value
    spread_id: Optional[int] = None
    leg_number: Optional[int] = None
from dataclasses import dataclass
from typing import Optional, Dict, Union
import pandas as pd
from options_bt.domain.enums import *  
from options_bt.domain.base_trade import BaseTrade
from options_bt.utils.logger import setup_logger

logger = setup_logger()

@dataclass
class OptionTrade(BaseTrade):
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

    # @classmethod
    # def from_position(cls, position: Position, **kwargs) -> 'TradeResult':
    #     """Create TradeResult from a Position object."""
    #     return cls(
    #         trade_id=position.trade_id,
    #         quantity=position.quantity,
    #         option_type=position.option_type,
    #         position_side=position.position_side,
    #         entry_date=position.entry_date,
    #         exit_date=position.close_date or position.expire_date,
    #         expire_date=position.expire_date,
    #         entry_delta=position.entry_delta,
    #         exit_delta=position.exit_delta,
    #         entry_dte=position.entry_dte,
    #         days_held=(position.close_date or position.expire_date - position.entry_date).days,
    #         underlying_entry=position.underlying_entry,
    #         underlying_exit=position.underlying_exit,
    #         strike=position.strike,
    #         entry_price=position.entry_price,
    #         exit_price=position.exit_price,
    #         capital_used=position.margin_required,
    #         option_bp=kwargs.get('option_bp', 0),
    #         return_on_margin=kwargs.get('return_on_margin', 0),
    #         close_reason=kwargs.get('close_reason', 'expired'),
    #         pnl=position.calculate_pnl(),
    #         spread_type=kwargs.get('spread_type', SpreadType.NONE.value),
    #         spread_id=kwargs.get('spread_id'),
    #         leg_number=kwargs.get('leg_number')
    #     )

    def to_dict(self) -> Dict:
        """Convert to dictionary for DataFrame creation."""
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'option_type': self.option_type.value if isinstance(self.option_type, OptionType) else self.option_type,
            'position_side': self.position_side.value if isinstance(self.position_side, PositionSide) else self.position_side,
            'entry_date': self.entry_date,
            'exit_date': self.exit_date,
            'expire_date': self.expire_date,
            'entry_delta': self.entry_delta,
            'exit_delta': self.exit_delta,
            'entry_dte': self.entry_dte,
            'days_held': self.days_held,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'capital_used': self.capital_used,
            'option_bp': self.option_bp,
            'return_on_margin': self.return_on_margin,
            'close_reason': self.close_reason,
            'pnl': self.pnl,
            'spread_type': self.spread_type,
            'spread_id': self.spread_id,
            'leg_number': self.leg_number
        }

    @classmethod
    def from_dataframe_row(cls, row: pd.Series) -> 'OptionTrade':
        """Create TradeResult from a DataFrame row."""
        return cls(**row.to_dict())

    @staticmethod
    def to_dataframe(results: list['OptionTrade']) -> pd.DataFrame:
        """Convert list of TradeResults to DataFrame."""
        return pd.DataFrame([r.to_dict() for r in results]) 
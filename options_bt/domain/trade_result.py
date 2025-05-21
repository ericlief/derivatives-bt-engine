from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Dict, Union
import pandas as pd
from options_bt.domain.enums import *  
from options_bt.bt import setup_logger      

# from options_bt.domain.position import SingleLegOptionPosition

logger = setup_logger()

@dataclass(kw_only=True)
class BaseTradeResult:
    """
    Represents a completed trade with entry and exit details.
    Designed to wdork efficiently with pandas DataFrames.
    """
    # metadata
    trade_id: int
    quantity: int
    
    # entry details 
    opened: pd.Timestamp
    closed: pd.Timestamp

    # trade records 
    transactions: List[dict]

    # results
    bp: float    # Available buying power after trade
    pnl: float
    return_on_margin: float 

    def __post_init__(self):
        if self.closed:
            self.days_held = (self.closed - self.opened).days
        else:
            self.days_held = None
            

@dataclass(kw_only=True)
class OptionTradeResult(BaseTradeResult):
    """
    Represents a completed trade with entry and exit details.
    Designed to wdork efficiently with pandas DataFrames.
    """
    # metadata
    option_strategy: Optional[Union[OptionStrategy, str]]
    closed_reason: Optional[str]
    premium: float
    fees: float

    # @classmethod
    # def from_position(cls, position: OptionPosition, exit_data: Dict) -> OptionTrade:
    #     """
    #     Create a trade from a position and exit data.
        
    #     Args:
    #         position: The option position
    #         exit_data: Dictionary containing exit information:
    #             - exit_date: When the position was closed
    #             - exit_price: Exit price
    #             - exit_delta: Delta at exit
    #             - underlying_exit: Underlying price at exit
    #             - close_reason: Why the position was closed ('expired' or 'early closure')
    #     """
    #     # Validate required exit data
    #     required_fields = ['exit_date', 'exit_price', 'exit_delta', 'underlying_exit']
    #     missing_fields = [f for f in required_fields if f not in exit_data]
    #     if missing_fields:
    #         raise ValueError(f"Missing required exit data fields: {missing_fields}")
            
    #     exit_date = pd.Timestamp(exit_data['exit_date'])
    #     days_held = (exit_date - position.entry_date).days
        
    #     return cls(
    #         trade_id=position.trade_id,
    #         quantity=position.quantity,
    #         option_type=position.option_type,
    #         position_side=position.position_side,
    #         entry_date=position.entry_date,
    #         exit_date=exit_date,
    #         expire_date=position.expire_date,
    #         entry_delta=position.entry_delta,
    #         exit_delta=exit_data['exit_delta'],
    #         entry_dte=position.entry_dte,
    #         days_held=days_held,
    #         underlying_entry=position.underlying_entry,
    #         underlying_exit=exit_data['underlying_exit'],
    #         strike=position.strike,
    #         entry_price=position.entry_price,
    #         exit_price=exit_data['exit_price'],
    #         capital_used=position.margin_required,
    #         option_bp=exit_data.get('option_bp', 0),  # Optional
    #         return_on_margin=exit_data.get('return_on_margin') or  # Use provided or calculate
    #             (position.calculate_pnl() / position.margin_required * 100 if position.margin_required > 0 else 0),
    #         close_reason=exit_data.get('close_reason', 'expired' if exit_date == position.expire_date else 'early closure'),
    #         pnl=exit_data.get('pnl') or position.calculate_pnl(),  # Use provided or calculate
    #         spread_type=getattr(position, 'spread_type', SpreadType.NONE.value),
    #         spread_id=getattr(position, 'spread_id', None),
    #         leg_number=getattr(position, 'leg_number', None)
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
    def from_dataframe_row(cls, row: pd.Series) -> 'OptionTradeResult':
        """Create TradeResult from a DataFrame row."""
        return cls(**row.to_dict())

    @staticmethod
    def to_dataframe(results: list['OptionTradeResult']) -> pd.DataFrame:
        """Convert list of TradeResults to DataFrame."""
        return pd.DataFrame([r.to_dict() for r in results]) 
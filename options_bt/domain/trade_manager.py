from typing import Optional, Dict, Union, List, NamedTuple, Tuple
import pandas as pd
import logging
from options_bt.domain.enums import OptionType, PositionSide, SpreadType, TradeResult
from options_bt.domain.spread import Spread
from options_bt.domain.trade import TradeResult   
from options_bt.domain.position import Position

logger = logging.getLogger(__name__)

class TradeManager:
    """Class to manage trade creation and execution."""
    
    def __init__(self, initial_capital: float = 100000.0, leverage: float = 1.0):
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.option_bp = initial_capital
        self.trade_counter = 0
        self.open_positions: List[Position] = []
    
    def create_trade_from_signal(
        self,
        trade_signal: NamedTuple,
        quantity: int,
        option_type: OptionType,
        position_side: PositionSide,
        delta_target: float,
        entry_date: pd.Timestamp,
        early_close_days: Optional[int] = None,
        delta_range: Optional[Tuple[float, float]] = None,
    ) -> Optional[Position]:
        """
        Creates a Position object from a given trade signal.
        
        Args:
            trade_signal: Named tuple from pandas itertuples containing signal data
            quantity: Number of contracts
            option_type: Type of option (PUT/CALL)
            position_side: Side of position (LONG/SHORT)
            delta_target: Target delta for the trade
            entry_date: Entry date for the trade
            early_close_days: Optional days before expiration to close
            delta_range: Optional range of acceptable deltas
            
        Returns:
            Optional[Position]: Created position if valid, None otherwise
        """
        # Validate entry date
        min_valid_date = pd.Timestamp('1990-01-01')
        if not isinstance(entry_date, pd.Timestamp) or entry_date <= min_valid_date:
            logger.error(f"Invalid entry date {entry_date}")
            return None
            
        # Validate expire_date exists and is valid
        if not trade_signal.expire_date:
            logger.error(f"expire_date is missing for trade signal on {trade_signal.Index}")
            return None
            
        expire_date = trade_signal.expire_date
        if not isinstance(expire_date, pd.Timestamp) or expire_date <= min_valid_date:
            logger.error(f"Invalid expire date {expire_date}")
            return None
            
        if expire_date <= entry_date:
            logger.error(f"Expire date {expire_date} is not after entry date {entry_date}")
            return None
        
        # Validate strike value
        if not hasattr(trade_signal, 'strike') or pd.isna(trade_signal.strike):
            logger.error(f"Missing strike value in trade signal on {trade_signal.Index}")
            return None
        
        # Get entry price (already validated in signal generation)
        entry_price = trade_signal.midpoint_price
        if entry_price is None:
            logger.error(f"Missing midpoint price for trade signal on {trade_signal.Index}")
            return None
            
        # Adjust entry price sign based on position side
        signed_entry_price = -entry_price if PositionSide.is_long(position_side) else entry_price

        # Calculate DTE
        entry_dte = pd.Timedelta(trade_signal.expire_date - entry_date).days
        
        # Create the position
        position = Position(
            trade_id=self.trade_counter,
            quantity=quantity,
            option_type=option_type,
            position_side=position_side,
            strike=trade_signal.strike,
            expire_date=trade_signal.expire_date,
            entry_date=entry_date,
            entry_price=signed_entry_price,
            entry_delta=trade_signal.p_delta if OptionType.is_put(option_type) else trade_signal.c_delta,
            entry_dte=trade_signal.dte if hasattr(trade_signal, 'dte') else entry_dte,
            underlying_entry=trade_signal.underlying_last,
            margin_required=trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else 0,
            close_date=entry_date + pd.Timedelta(days=early_close_days) if early_close_days is not None else None,
        )
        
        return position
    
    def execute_trade(self, trade: Position) -> Tuple[Optional[Position], float]:
        """
        Execute a trade with the current buying power and leverage.
        
        Args:
            trade: Position to execute
            
        Returns:
            Tuple of (executed trade if successful, updated option buying power)
        """
        if trade is None:
            return None, self.option_bp
            
        # Use spread price for spreads, individual leg price for single legs
        if isinstance(trade, Spread) and trade.spread_type != SpreadType.NONE.value:
            if pd.isna(trade.spread_price):
                logger.error(f"Missing spread_price for spread {trade.spread_id} leg {trade.leg_number}")
                return None, self.option_bp
            premium = abs(trade.spread_price) * 100 * trade.quantity
        else:
            premium = abs(trade.entry_price) * 100 * trade.quantity

        # Calculate effective margin requirement with leverage
        effective_margin = trade.margin_required / self.leverage
        if effective_margin is None or effective_margin <= 0:
            logger.error(f"Invalid margin requirement for trade on {trade.entry_date}")
            return None, self.option_bp
        
        # Open LONG position
        if trade.is_long:
            # Check if enough buying power to buy the option
            if self.option_bp >= premium:
                self.option_bp -= premium  # Deduct premium
                return trade, self.option_bp
            else:
                logger.warning(f"Insufficient buying power (${self.option_bp}) to buy option. Required: ${premium:.2f}")
                return None, self.option_bp

        # Open SHORT position
        elif trade.is_short:
            # Check if enough buying power for margin
            if self.option_bp >= effective_margin:
                self.option_bp += premium  # Credit premium
                self.option_bp -= effective_margin  # Reserve margin
                return trade, self.option_bp
            else:
                logger.warning(f"Insufficient buying power (${self.option_bp}) to sell option. Required margin: ${effective_margin:.2f}")
                return None, self.option_bp

        return None, self.option_bp 
    

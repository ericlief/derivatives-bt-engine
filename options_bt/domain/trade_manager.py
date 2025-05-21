from typing import Optional, Dict, Union, List, NamedTuple, Tuple
import pandas as pd
import logging
from options_bt.domain.enums import *
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig

logger = setup_logger()

class TradeManager:
    """Class to manage trade creation and execution."""
    
    def __init__(self, initial_capital: float = 100000.0, leverage: float = 1.0, max_margin_utilization: float = 0.80, 
                 max_positions: int = 1, early_close_days: Optional[int] = None, use_underlying_close: bool = False):
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.option_bp = initial_capital
        self.max_margin_utilization = max_margin_utilization
        self.max_positions = max_positions
        self.trade_counter = 0
        self.open_positions: List[OptionPosition] = []
    
    def build_trade_from_signal(
        self,
        trade_signal: NamedTuple,
        entry_date: pd.Timestamp,
        config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
    ) -> Optional[SingleLegOptionPosition]:
        """
        Creates a OptionPosition object from a given trade signal.
        
        Args:       
            trade_signal: NamedTuple,
            config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig],
            entry_date: pd.Timestamp,
            early_close_days: Optional[int] = None,
            delta_range: Optional[Tuple[float, float]] = None

            
            Example config:
                config = SingleLegOptionStrategyConfig(
                    strategy=OptionStrategy.SHORT_CALL,
                    quantity=1,
                    initial_capital=100000,
                    leverage=1.0,
                    start_date="2020-01-01",
                    end_date="2020-12-31",
                    use_underlying_close=False,
                    early_close_days=30,
                    max_margin_utilization=0.80,
                    max_positions=1,
                    # Define the leg of the strategy
                    leg=OptionLegConfig(
                        option_type=OptionType.CALL,
                        position_side=PositionSide.SHORT,
                        delta_target=0.75,
                        dte_range=(42, 45),
                        )
        Returns:
            Optional[OptionPosition]: Created position if valid, None otherwise
        """
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)
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
        entry_price = trade_signal.midpoint_price if trade_signal.midpoint_price is not None else trade_signal.spread_price
        if entry_price is None:
            logger.error(f"Missing midpoint price for trade signal on {trade_signal.Index}")
            return None
            
        # Adjust entry price sign based on position side
        signed_entry_price = -entry_price if PositionSide.is_long(config.position_side) and is_spread else spentry_price

        # Calculate DTE
        entry_dte = trade_signal.dte if hasattr(trade_signal, 'dte') else pd.Timedelta(trade_signal.expire_date - entry_date).days
        
        # Get margin required from signal if available, otherwise use config
        # TODO: Add margin required to signal with util method
        margin_required = trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else None
        
        # Create the position
        position = SingleLegOptionPosition(
            trade_id=self.trade_counter,
            quantity=config.quantity,
            option_type=config.leg.option_type,
            position_side=config.position_side,
            strike=trade_signal.strike,
            entry_date=entry_date,
            expire_date=trade_signal.expire_date,
            entry_price=signed_entry_price,
            entry_delta=trade_signal.p_delta if OptionType.is_put(config.leg.option_type) else trade_signal.c_delta,
            entry_dte=entry_dte,
            underlying_entry=trade_signal.underlying_last,
            margin_required=margin_required,
            close_date=entry_date + pd.Timedelta(days=early_close_days) if early_close_days is not None else None,
        )

        # if is_spread:
        #     position = MultiLegOptionPosition(
        #         trade_id=self.trade_counter,
        #         quantity=quantity,
        #         option_type=option_type,
        #         position_side=position_side,
        #         strike=trade_signal.strike,
        #         expire_date=trade_signal.expire_date,
        #     entry_date=entry_date,
        #     entry_price=signed_entry_price,
        #     entry_delta=trade_signal.p_delta if OptionType.is_put(option_type) else trade_signal.c_delta,
        #     entry_dte=trade_signal.dte if hasattr(trade_signal, 'dte') else entry_dte,
        #     underlying_entry=trade_signal.underlying_last,
        #     margin_required=trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else 0,
        #     close_date=entry_date + pd.Timedelta(days=early_close_days) if early_close_days is not None else None,
        # )
        
        return position
    
    def execute_trade(self, trade: SingleLegOptionPosition) -> Tuple[Optional[SingleLegOptionPosition], float]:
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
        if isinstance(trade, Spread) and trade.spread_type != OptionSpreadType.NONE.value:
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
    
    
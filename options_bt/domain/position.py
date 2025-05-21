from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property
from abc import ABC, abstractmethod
import unittest
import pandas as pd
import numpy as np

from options_bt.domain.dataloader import DataLoader
from options_bt.domain.enums import *
from options_bt.domain.trade_result import BaseTradeResult, OptionTradeResult
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils
logger = setup_logger()
 
 
@dataclass(kw_only=True)
class BasePosition(ABC):
    """Base class for any trading position."""
    trade_id: Optional[int] = None
    quantity: int
    position_side: Optional[Union[PositionSide, str]] = None
    entry_date: pd.Timestamp
    entry_price: float
    margin_required: Optional[float] = None

    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.position_side, str):
            self.position_side = PositionSide(self.position_side.lower())
        if isinstance(self.entry_date, str):
            self.entry_date = pd.Timestamp(self.entry_date)


    # @abstractmethod
    # @cached_property
    # def margin_required(self) -> float: 
    #     """Calculate total margin requirement for the position."""
    #     pass
   
    # @abstractmethod
    # def is_closed(self) -> bool:
    #     """Check if position is closed. Must be implemented by subclasses."""
    #     pass
    
    @abstractmethod
    def calculate_pnl(self, exit_price: Optional[float] = None) -> float:
        """Calculate profit and loss for the position."""
        pass
 
    @cached_property
    def signed_entry_price(self) -> float:
        """
        Get the entry price with correct sign based on position side.
        - Long positions should have negative entry price (debit/BTO)
        - Short positions should have positive entry price (credit/STO)
        """
        if not self.entry_price:
            logger.warning(f"No entry price for position {self.trade_id}")
            return None
            
        # try:
        #     if self.is_long:
        #         assert self.entry_price <= 0  # debit premium, buy to open (BTO)
        #     elif self.is_short:
        #         assert self.entry_price >= 0  # credit premium, sell to open (STO)
        # except AssertionError:
            # logger.debug(f'Fixing sign of entry price {self.entry_price}')
        return -abs(self.entry_price) if self.is_long else abs(self.entry_price)
            
        # return self.entry_price

    
    @abstractmethod
    def calculate_margin(quantity, 
                         margin_req_percent: float = 0.15, 
                         leverage: float = 1.0) -> float:
        """Calculate margin requirement for the position."""
        pass

@dataclass(kw_only=True)
class BaseOptionPosition(BasePosition, ABC):
    """Abstract base class for any option position."""
    # Required parameters (no defaults)
  
    expire_date: pd.Timestamp
    entry_dte: int
    underlying_entry: float


    # Should go into Trade class
    # exit_date: Optional[pd.Timestamp] = None
    # exit_price: Optional[float] = None
    # exit_delta: Optional[float] = None
    # underlying_exit: Optional[float] = None
    close_date: Optional[pd.Timestamp] = None  # For early closure

    def __post_init__(self):
        super().__post_init__()

        """Validate and convert types after initialization."""
    
        if isinstance(self.close_date, str):
            self.close_date = pd.Timestamp(self.close_date)
            
        if isinstance(self.expire_date, str):
            self.expire_date = pd.Timestamp(self.expire_date)   

        # Calculate margin required based on entry price and underlying entry
        # if self.entry_price is not None and self.underlying_entry is not None:
        #     self.margin_required = self.calculate_margin()




    @cached_property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]

    @cached_property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]

    @cached_property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]

    @cached_property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]

    # def is_closed(self) -> bool:
    #     """Check if position is closed based on exit information."""
    #     pass
        # return (self.exit_date is not None or  # Normal exit
        #         (self.expire_date is not None and pd.Timestamp.now() >= self.expire_date))  # Expired

    # @property
    # def is_open(self) -> bool:
    #     """Check if position is currently open."""
    #     return not self.is_closed()

    @cached_property
    def get_signed_entry_price(self) -> float:
        """
        Get the entry price with correct sign based on position side.
        - Long positions should have negative entry price (debit/BTO)
        - Short positions should have positive entry price (credit/STO)
        """
        if not self.entry_price:
            return 0
            
        try:
            if self.is_long:
                assert self.entry_price <= 0  # debit premium, buy to open (BTO)
            elif self.is_short:
                assert self.entry_price >= 0  # credit premium, sell to open (STO)
        except AssertionError:
            logger.debug(f'Fixing sign of entry price {self.entry_price}')
            return -abs(self.entry_price) if self.is_long else abs(self.entry_price)
            
        return self.entry_price

    @cached_property
    def get_signed_exit_price(self) -> float:
        """
        Get the exit price with correct sign based on position side.
        - Long positions should have positive exit price (credit/STC)
        - Short positions should have negative exit price (debit/BTC)
        """
        if not self.exit_price:
            return 0
            
        try:
            if self.is_long:
                assert self.exit_price >= 0  # sell to close (STC)
            elif self.is_short:
                assert self.exit_price <= 0  # buy to close (BTC)
        except AssertionError:
            logger.debug(f'Fixing sign of exit price {self.exit_price}')
            return abs(self.exit_price) if self.is_long else -abs(self.exit_price)
            
        return self.exit_price

    def calculate_margin(self, leverage: float = 1.0) -> float:
        """Calculate margin requirement for the position."""
        if not self.entry_price or not self.underlying_entry:
            return 0

        if self.is_long:
            return 0  # No margin required for long positions
        
        # For short positions, use IB's formula
        margin_req_percent = 0.15  # 15% for index options
        
        # Calculate out-of-the-money amount
        if self.is_put:
            otm_amount = max(0, self.underlying_entry - self.strike)
        else:  # Call
            otm_amount = max(0, self.strike - self.underlying_entry)
        
        # IB's margin formula
        margin = (
            abs(self.entry_price) +  # Option price
            max(
                (margin_req_percent * self.underlying_entry - otm_amount),
                (0.10 * self.underlying_entry)
            )
        ) * 100 * self.quantity  # Convert to dollars
        
        return round(margin / leverage, 2)

    def calculate_pnl(self, exit_price: Optional[float] = None) -> float:
        """
        Calculate P&L for the position.
        
        Args:
            exit_price: Optional exit price. If not provided, returns 0 (unrealized P&L).
        """
        if exit_price is None:
            return 0
            
        # Get correctly signed prices
        entry = self.get_signed_entry_price
        # For long positions, exit price should be positive (credit/STC)
        # For short positions, exit price should be negative (debit/BTC)
        signed_exit = abs(exit_price) if self.is_long else -abs(exit_price)
        
        pnl = entry + signed_exit  # Signs are already correct
        
        # For long positions, clamp loss to zero
        if self.is_long:
            return max(0, pnl * 100 * self.quantity)
        return pnl * 100 * self.quantity

    @abstractmethod
    def close_position(self) -> Optional[BaseTradeResult]:
        """
        Close this position and calculate results.
        """
        pass 
    
    @staticmethod
    def calculate_intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        pass    



    @classmethod
    def create_vertical_spread(
        cls,
        strikes: List[float],
        option_type: OptionType,
        expire_date: pd.Timestamp,
        is_credit: bool = True
    ) -> List[ OptionPosition]:
        """
        Factory method to create a vertical spread.
        
        Args:
            strikes: [short_strike, long_strike]
            option_type: PUT or CALL
            expire_date: Expiration date
            is_credit: If True, creates credit spread (default)
        """
        if len(strikes) != 2:
            raise ValueError("Vertical spread requires exactly 2 strikes")

        # For credit spreads:
        # PUT: Sell higher strike, buy lower strike
        # CALL: Sell lower strike, buy higher strike
        if is_credit:
            if option_type == OptionType.PUT:
                short_strike, long_strike = max(strikes), min(strikes)
            else:  # CALL
                short_strike, long_strike = min(strikes), max(strikes)
        else:  # Debit spreads are opposite
            if option_type == OptionType.PUT:
                short_strike, long_strike = min(strikes), max(strikes)
            else:  # CALL
                short_strike, long_strike = max(strikes), min(strikes)

        return [
            cls(strike=short_strike, option_type=option_type, position_side=PositionSide.SHORT, expire_date=expire_date),
            cls(strike=long_strike, option_type=option_type, position_side=PositionSide.LONG, expire_date=expire_date)
        ]

    @classmethod
    def create_iron_condor(
        cls,
        put_strikes: List[float],
        call_strikes: List[float],
        expire_date: pd.Timestamp
    ) -> List[' OptionPosition']:
        """
        Factory method to create an iron condor.
        
        Args:
            put_strikes: [long_put_strike, short_put_strike]
            call_strikes: [short_call_strike, long_call_strike]
            expire_date: Expiration date
        """
        if len(put_strikes) != 2 or len(call_strikes) != 2:
            raise ValueError("Iron condor requires exactly 2 strikes for puts and 2 for calls")

        put_spread = cls.create_vertical_spread(put_strikes, OptionType.PUT, expire_date, is_credit=True)
        call_spread = cls.create_vertical_spread(call_strikes, OptionType.CALL, expire_date, is_credit=True)
        return put_spread + call_spread

    def to_dict(self) -> Dict:
        """Convert position to dictionary format."""
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'entry_date': self.entry_date,
            'expire_date': self.expire_date,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'option_type': self.option_type.value if isinstance(self.option_type, OptionType) else self.option_type,
            'position_side': self.position_side.value if isinstance(self.position_side, PositionSide) else self.position_side,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'entry_delta': self.entry_delta,
            'exit_delta': self.exit_delta,
            'entry_dte': self.entry_dte,
            'close_date': self.close_date,
            'margin_required': self.margin_required
        }

    # def from_row(self, row: NamedTuple, quantity: int, option_type: OptionType, position_side: PositionSide, delta_target: float, entry_date: pd.Timestamp, early_close_days: int, delta_range: Tuple[float, float] = None) -> Position:
    #     trade_id = row.trade_id
    #     quantity: int = 1
    #     option_type: Union[OptionType, str]
    #     position_side: Union[PositionSide, str]
    #     strike: float
    #     expire_date: pd.Timestamp

    #     # Entry state
    #     entry_date: Optional[pd.Timestamp] = None
    #     entry_price: Optional[float] = None
    #     underlying_entry: Optional[float] = None
    #     margin_required: Optional[float] = None  # Store margin requirement

    #     delta_col = "p_delta" if is_put(option_type) else "c_delta"
    #     trade_delta = row[delta_col]

@dataclass(kw_only=True)
class SingleLegOptionPosition(BaseOptionPosition):
    """Core option position. Represents a single 'open' option contract position."""
    # Required parameters (no defaults)

    option_type: Union[OptionType, str]
    strike: float
    entry_delta: float
    entry_dte: int

    # Should go into Trade class
    # exit_date: Optional[pd.Timestamp] = None
    # exit_price: Optional[float] = None
    # exit_delta: Optional[float] = None
    # underlying_exit: Optional[float] = None
    close_date: Optional[pd.Timestamp] = None  # For early closure

    def __post_init__(self):
        super().__post_init__()

        """Validate and convert types after initialization."""
        if isinstance(self.option_type, str):
            self.option_type = OptionType(self.option_type.lower())
      
        if isinstance(self.entry_date, str):
            self.entry_date = pd.Timestamp(self.entry_date)
            
        if isinstance(self.close_date, str):
            self.close_date = pd.Timestamp(self.close_date)
            
        if isinstance(self.expire_date, str):
            self.expire_date = pd.Timestamp(self.expire_date)   

        # Calculate margin required based on entry price and underlying entry
        if self.entry_price is not None and self.underlying_entry is not None:
            self.margin_required = self.calculate_margin( self.quantity, self.option_type, self.position_side, self.underlying_entry, self.entry_price, self.strike )

    @cached_property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]

    @cached_property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]

    @cached_property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]

    @cached_property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]

    def is_ITM(self, underlying_price: float) -> bool:
        """Check if position is in the money."""
        return self.is_put and underlying_price <= self.strike or self.is_call and underlying_price >= self.strike

    def is_closed(self) -> bool:
        """Check if position is closed based on exit information."""
        pass
        # return (self.exit_date is not None or  # Normal exit
        #         (self.expire_date is not None and pd.Timestamp.now() >= self.expire_date))  # Expired

    # @property
    # def is_open(self) -> bool:
    #     """Check if position is currently open."""
    #     return not self.is_closed()

   

    # @cached_property
    # def get_signed_exit_price(self) -> float:
    #     """
    #     Get the exit price with correct sign based on position side.
    #     - Long positions should have positive exit price (credit/STC)
    #     - Short positions should have negative exit price (debit/BTC)
    #     """
    #     if not self.exit_price:
    #         return 0
            
    #     try:
    #         if self.is_long:
    #             assert self.exit_price >= 0  # sell to close (STC)
    #         elif self.is_short:
    #             assert self.exit_price <= 0  # buy to close (BTC)
    #     except AssertionError:
    #         logger.debug(f'Fixing sign of exit price {self.exit_price}')
    #         return abs(self.exit_price) if self.is_long else -abs(self.exit_price)
            
    #     return self.exit_price
    @staticmethod
    def construct_from_signal(
            trade_signal: NamedTuple,
            entry_date: pd.Timestamp,
            position_side: PositionSide,    
            option_type: OptionType,
            quantity: int,  
            early_close_days: int,
        ) -> Optional[SingleLegOptionPosition]:
            """
                Creates a OptionPosition object from a given trade signal.
                
                Args:       
                    trade_signal: NamedTuple,
                    entry_date: pd.Timestamp,
                    position_side: PositionSide,    
                    option_type: OptionType,
                    quantity: int,  
                    early_close_days: int,

                
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
                Optional[SingleLegOptionPosition]: Created position if valid, None otherwise
            """     
            # is_spread = isinstance(self.config, MultiLegOptionStrategyConfig)
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
            signed_entry_price = -entry_price if PositionSide.is_long(position_side) else entry_price

            # Calculate DTE
            entry_dte = trade_signal.dte if hasattr(trade_signal, 'dte') else pd.Timedelta(trade_signal.expire_date - entry_date).days
            
            # Get margin required from signal if available, otherwise use config
            margin_required = trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else None
            
            # Create the position
            position = SingleLegOptionPosition(
                trade_id=None,
                quantity=quantity,
                option_type=option_type,
                position_side=position_side,
                strike=trade_signal.strike,
                entry_date=entry_date,
                expire_date=trade_signal.expire_date,
                entry_price=signed_entry_price,
                entry_delta=trade_signal.p_delta if OptionType.is_put(option_type) else trade_signal.c_delta,
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

    @staticmethod
    def calculate_margin(quantity: int,
                         option_type: Union[OptionType, str],
                         position_side: Union[PositionSide, str],
                         underlying_price: float, 
                         entry_price: float, 
                         strike: float,
                         leverage: float = 1.0,
                         margin_req_percent: float = 0.15) -> float:
        """
        Calculate required margin for option position using IB's formula for Index Options.
        
        Args:
            underlying_price (float): Current price of the underlying asset.
            entry_price (float): Option premium, which is the mid of the bid and ask prices.
            position_side (Union[PositionSide, str]): Indicates whether the position is LONG or SHORT.
            strike (float): The strike price of the option.
            option_type (Union[OptionType, str]): The type of the option, which can be PUT or CALL.
            margin_req_percent (float, optional): The margin requirement percentage. Defaults to 0.15, which is the value for Interactive Brokers.
        
        Returns:
            float: The required margin in dollars.
        """
        # Convert string to enum if needed
        # if isinstance(position_side, str):
        #     position_side = PositionSide.LONG if position_side.lower() == "long" else PositionSide.SHORT
        
        # For long positions, margin is just the cost of the option
        # There is no margin req for Long positions
        if PositionSide.is_long(position_side):
            # return round(entry_price * 100, 2)  # Convert to dollars
            return 0
        
        # For short positions, use IB's formula for Index Options
        else:  # PositionSide.SHORT
            # Calculate out-of-the-money amount
            if OptionType.is_put(option_type): 
                # For puts: OTM when strike > underlying, ITM when strike <= underlying
                otm_amount = max(0, underlying_price - strike)
            else:  # CALL
                # For calls: OTM when strike >= underlying, ITM when strike < underlying
                otm_amount = max(0, strike - underlying_price)
            
            # IB's margin formula for Index Options
            margin_required = (
                entry_price +  # Option price
                max(
                    # First term: 15% of underlying price minus OTM amount
                    (margin_req_percent * underlying_price - otm_amount),
                    # Second term: 10% of underlying price
                    (0.10 * underlying_price)
                )
            ) * 100 * quantity  # Convert to dollars

            return round(margin_required/leverage, 2)   
        
    # def calculate_margin(self, leverage: float = 1.0) -> float:
    #     """Calculate margin requirement for the position."""
    #     if not self.entry_price or not self.underlying_entry:
    #         return 0

    #     if self.is_long:
    #         return 0  # No margin required for long positions
        
    #     # For short positions, use IB's formula
    #     margin_req_percent = 0.15  # 15% for index options
        
    #     # Calculate out-of-the-money amount
    #     if self.is_put:
    #         otm_amount = max(0, self.underlying_entry - self.strike)
    #     else:  # Call
    #         otm_amount = max(0, self.strike - self.underlying_entry)
        
    #     # IB's margin formula
    #     margin = (
    #         abs(self.entry_price) +  # Option price
    #         max(
    #             (margin_req_percent * self.underlying_entry - otm_amount),
    #             (0.10 * self.underlying_entry)
    #         )
    #     ) * 100 * self.quantity  # Convert to dollars
        
    #     return round(margin / leverage, 2)

    def calculate_pnl(self, exit_price: Optional[float] = None, fees: float = 0.00) -> Optional[float]:
        """
        Calculate P&L for the position.
        
        Args:
            exit_price: Optional exit price. If not provided, returns None (unrealized P&L).
        """
        if exit_price is None:
            return None
            
        # Get correctly signed prices
        entry = self.get_signed_entry_price
        # For long positions, exit price should be positive (credit/STC)
        # For short positions, exit price should be negative (debit/BTC)
        signed_exit = abs(exit_price) if self.is_long else -abs(exit_price)
        
        pnl = entry + signed_exit - fees  # Signs are already correct
        
        # For long positions, clamp loss to zero
        if self.is_long:
            return max(0, pnl * 100 * self.quantity)
        return pnl * 100 * self.quantity

    def close_position(self, 
                      option_chain: pd.DataFrame, 
                      underlying_price_history: pd.DataFrame,
                      option_bp: float) -> Optional[OptionTradeResult]:
        """
        Close this position and calculate results.
        
        Args:
            option_chain: pd.DataFrame, 
            underlying_price_history: pd.DataFrame,
            option_bp: float
        
        Returns:
            Optional[OptionTradeResult]: Trade result object if successful, None if closing data is unavailable.
        """
        close_reason = None
        min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970

        # Validate entry_date
        if self.entry_date <= min_valid_date:
            logger.error(f"Invalid entry date: {self.entry_date} - skipping trade")
            return None
        
        # Early closure, get close date with validation
        if self.close_date is not None:
            close_reason = 'early closure'
            close_date = self.close_date
        elif self.expire_date is not None:
            close_reason = 'expired'
            close_date = self.expire_date
        else:
            logger.error("Both close_date and expire_date are None - skipping trade")
            return None
        
        logger.debug(f'Close Reason: {close_reason}')

        # Validate close_date
        if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
            logger.error(f"Invalid close date: {close_date} - skipping trade")
            return None
        
        # Ensure close_date is not before entry_date
        if close_date < self.entry_date:
            logger.error(f"Close date {close_date} is before entry date {self.entry_date} - skipping trade")
            return None
        
        # Get closing data
        closing_data = self._get_closing_data(option_chain, underlying_price_history)
        if closing_data is None:
            logger.warning("Skipping trade due to missing close data")
            return None
            
        # Update position with closing data
        exit_price = PriceUtils.get_signed_exit_price(closing_data['exit_price'], self.position_side)
        exit_delta = closing_data['exit_delta']
        underlying_exit = closing_data['underlying_exit']
        
       
        
        # Update buying power
        premium = self.get_signed_exit_price * 100 * self.quantity
        option_bp += premium  # Add/subtract exit premium (already signed)

        # Restore margin for short positions
        if self.is_short:
            option_bp += self.margin_required

        fees = 1.78 # per option contract at Tastyworks
        if close_reason == 'expired' and self.is_ITM(underlying_exit):
            fees += 5.00 # exercise fee
        fees *= self.quantity

        # Calculate P&L
        pnl = self.calculate_pnl(exit_price, fees)
        logger.debug(f'Calculated pnl: {pnl}')

        # Calculate days held
        days_held = pd.Timedelta(close_date - self.entry_date).days
        if days_held < 0:
            logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
            return None
    
         # Prepare trade result
        transaction =  {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'option_type': self.option_type.value,
            'position_side': self.position_side.value,
            'entry_date': self.entry_date,
            'exit_date': close_date,
            'expire_date': self.expire_date,
            'entry_delta': round(self.entry_delta, 2),
            'exit_delta': round(self.exit_delta, 2),
            'entry_dte': self.entry_dte,
            'days_held': days_held,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'entry_price': round(self.entry_price, 2),
            'exit_price': round(self.exit_price, 2),
            'capital_used': self.margin_required,
            'option_bp': round(option_bp, 2),
            'return_on_margin': round(pnl / self.margin_required * 100, 2) if self.margin_required > 0 else 0,
            'close_reason': close_reason,
            'pnl': round(pnl, 2),
        }
        trade_result = OptionTradeResult(
            trade_id=self.trade_id,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=close_date,
            option_strategy=self.option_strategy,
            closed_reason=close_reason,
            premium=premium,
            fees=fees,
            pnl=pnl,
            bp=option_bp,
            return_on_margin=pnl / self.margin_required * 100,
            transactions=[transaction] 
        )
        return trade_result
        
       

    def _get_closing_data(self, option_chain: pd.DataFrame, underlying_price_history: pd.DataFrame) -> Optional[Dict]:
        """
        Get closing price data for this position.
        Args:
            option_chain: DataFrame containing full option chain data.
            underlying_price_history: DataFrame containing underlying price data.
        Returns:
            Optional[Dict]: Dictionary with closing data if successful, None if no valid data found.
        """
        # If no close_date, this is an expiration
        if not self.close_date:
            if self.expire_date not in underlying_price_history.index:
                logger.warning(f"No valid closing data found for position with expire date {self.expire_date}")
                return None
            
            # Get underlying price at close
            underlying_close = underlying_price_history.loc[self.expire_date, 'close']
            
            # Calculate intrinsic value at expiration
            exit_price = self._calculate_intrinsic_value(underlying_close)
            exit_price = -abs(exit_price) if self.is_long else abs(exit_price)

            # Get delta value at expiration
            delta_col = "p_delta" if self.is_put else 'c_delta'
            filtered_df = option_chain[
                (option_chain.index == self.expire_date) &
                (option_chain['expire_date'] == self.expire_date) &
                (option_chain['strike'] == self.strike)
            ]
            
            exit_delta = round(filtered_df[delta_col].iloc[0], 2) if not filtered_df.empty else None

            return {
                'underlying_exit': underlying_close,
                'exit_price': exit_price,
                'exit_delta': exit_delta
            }
        
        # Early close - get data from close_date forward (up to 5 days)
        date_range = pd.date_range(self.close_date, self.close_date + pd.Timedelta(days=5))
        filtered_df = option_chain[
            (option_chain.index.isin(date_range)) & 
            (option_chain['expire_date'] == self.expire_date) &
            (option_chain['strike'] == self.strike)    
        ].sort_index()
        
        if filtered_df.empty:
            logger.warning(f"No valid prices found within 5 days of close date {self.close_date}")
            return None
            
        bid_col = "p_bid" if self.is_put else "c_bid"
        ask_col = "p_ask" if self.is_put else "c_ask"
        delta_col = "p_delta" if self.is_put else 'c_delta'

        # Try each date until we find valid prices
        for _, row in filtered_df.iterrows():
            bid = row[bid_col]
            ask = row[ask_col]
            underlying_close = row['underlying_last']
            exit_delta = round(row[delta_col], 2)
            
            mid_price = PriceUtils.calculate_midpoint_price(bid, ask)
            if mid_price is not None:
                return {
                    'underlying_exit': underlying_close,
                    'exit_price': mid_price,
                    'exit_delta': exit_delta
                }
        
        logger.error(f"No valid closing prices found for strike {self.strike} and expire date {self.expire_date}")
        return None

    def intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        if self.is_put:
            return max(0, self.strike - underlying_price)
        else:  # Call
            return max(0, underlying_price - self.strike)

    # def _calculate_midpoint_price(self, bid: float, ask: float) -> Optional[float]:
    #     """Calculate midpoint price with validation."""
    #     if bid <= 0 or ask <= 0:
    #         return None
            
    #     spread_pct = ((ask - bid) / bid) * 100
    #     if spread_pct > 50.0:  # Spread too wide
    #         logger.warning(f"Bid-ask spread too wide: bid={bid}, ask={ask}, spread={spread_pct:.2f}%")
    #         return None
            
    #     return (bid + ask) / 2

    # @classmethod
    # def create_vertical_spread(
    #     cls,
    #     strikes: List[float],
    #     option_type: OptionType,
    #     expire_date: pd.Timestamp,
    #     is_credit: bool = True
    # ) -> List[ OptionPosition]:
    #     """
    #     Factory method to create a vertical spread.
        
    #     Args:
    #         strikes: [short_strike, long_strike]
    #         option_type: PUT or CALL
    #         expire_date: Expiration date
    #         is_credit: If True, creates credit spread (default)
    #     """
    #     if len(strikes) != 2:
    #         raise ValueError("Vertical spread requires exactly 2 strikes")

    #     # For credit spreads:
    #     # PUT: Sell higher strike, buy lower strike
    #     # CALL: Sell lower strike, buy higher strike
    #     if is_credit:
    #         if option_type == OptionType.PUT:
    #             short_strike, long_strike = max(strikes), min(strikes)
    #         else:  # CALL
    #             short_strike, long_strike = min(strikes), max(strikes)
    #     else:  # Debit spreads are opposite
    #         if option_type == OptionType.PUT:
    #             short_strike, long_strike = min(strikes), max(strikes)
    #         else:  # CALL
    #             short_strike, long_strike = max(strikes), min(strikes)

    #     return [
    #         cls(strike=short_strike, option_type=option_type, position_side=PositionSide.SHORT, expire_date=expire_date),
    #         cls(strike=long_strike, option_type=option_type, position_side=PositionSide.LONG, expire_date=expire_date)
    #     ]

    # @classmethod
    # def create_iron_condor(
    #     cls,
    #     put_strikes: List[float],
    #     call_strikes: List[float],
    #     expire_date: pd.Timestamp
    # ) -> List[' OptionPosition']:
    #     """
    #     Factory method to create an iron condor.
        
    #     Args:
    #         put_strikes: [long_put_strike, short_put_strike]
    #         call_strikes: [short_call_strike, long_call_strike]
    #         expire_date: Expiration date
    #     """
    #     if len(put_strikes) != 2 or len(call_strikes) != 2:
    #         raise ValueError("Iron condor requires exactly 2 strikes for puts and 2 for calls")

    #     put_spread = cls.create_vertical_spread(put_strikes, OptionType.PUT, expire_date, is_credit=True)
    #     call_spread = cls.create_vertical_spread(call_strikes, OptionType.CALL, expire_date, is_credit=True)
    #     return put_spread + call_spread

    def to_dict(self) -> Dict:
        """Convert position to dictionary format."""
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'entry_date': self.entry_date,
            'expire_date': self.expire_date,
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'strike': self.strike,
            'option_type': self.option_type.value if isinstance(self.option_type, OptionType) else self.option_type,
            'position_side': self.position_side.value if isinstance(self.position_side, PositionSide) else self.position_side,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'entry_delta': self.entry_delta,
            'exit_delta': self.exit_delta,
            'entry_dte': self.entry_dte,
            'close_date': self.close_date,
            'margin_required': self.margin_required
        }

    # def from_row(self, row: NamedTuple, quantity: int, option_type: OptionType, position_side: PositionSide, delta_target: float, entry_date: pd.Timestamp, early_close_days: int, delta_range: Tuple[float, float] = None) -> Position:
    #     trade_id = row.trade_id
    #     quantity: int = 1
    #     option_type: Union[OptionType, str]
    #     position_side: Union[PositionSide, str]
    #     strike: float
    #     expire_date: pd.Timestamp

    #     # Entry state
    #     entry_date: Optional[pd.Timestamp] = None
    #     entry_price: Optional[float] = None
    #     underlying_entry: Optional[float] = None
    #     margin_required: Optional[float] = None  # Store margin requirement

    #     delta_col = "p_delta" if is_put(option_type) else "c_delta"
    #     trade_delta = row[delta_col]

@dataclass(kw_only=True)
class MultiLegOptionPosition(BaseOptionPosition):
    """Class representing a multi-leg option spread.""" 
    spread_id: int
    spread_type: OptionSpreadType
    legs: List[SingleLegOptionPosition] 
    leg_ratios: Dict[int, float] = 1   # Maps leg number to ratio
    # spread_price: Optional[float] = None  # property will calculate the net price
    # net_price: Optional[float] = None
    expire_date: Optional[pd.Timestamp] = None # Common expiration date for the spread
    entry_dte: int = 0  # Common DTE for the spread
    underlying_entry: Optional[float] = None  # Common underlying entry price for the spread

    def __post_init__(self):
        super().__post_init__()

        # Validate spread configuration after initialization
        if isinstance(self.spread_type, str):
            self.spread_type = OptionSpreadType(self.spread_type.lower())
            
        # Set default leg ratios if not provided
        if not self.leg_ratios:
            self.leg_ratios = {i: 1.0 for i in range(len(self.legs))}
        
        # Check if all legs have the same expiration date
        is_same = False
        init_expire_date = self.legs[0].expire_date
        for leg in self.legs[1:]:
            if leg.expire_date != init_expire_date:
                is_same = False
                break
            else:
                is_same = True
                
        # Set the common expiration date if all legs have the same expiration date
        self.expire_date = pd.to_datetime(self.legs[0].expire_date) if is_same else None

        # Check if all legs have the same entry DTE
        is_same = False
        init_entry_dte = self.legs[0].entry_dte
        for leg in self.legs[1:]:
            if leg.entry_dte != init_entry_dte:
                is_same = False 
                break
            else:
                is_same = True

        # Set the common entry DTE if all legs have the same entry DTE
        self.entry_dte = self.legs[0].entry_dte if is_same else None
        
        # Determine position side based on total entry price, assuming SHORT if total premium is positive
        total_entry_price = self.spread_price  #sum(leg.signed_entry_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))
        self.position_side = PositionSide.LONG if total_entry_price > 0 else PositionSide.SHORT
        
        # Assuming same underlying entry price for all legs
        self.underlying_entry = self.legs[0].underlying_entry

        # Validate spread configuration
        self.validate_spread()

    def validate_spread(self):
        """Validate spread configuration based on type."""
        if self.spread_type == OptionSpreadType.VERTICAL:
            if len(self.legs) != 2:
                raise ValueError("Vertical spread must have exactly 2 legs")
            # Validate strikes and sides
            if self.legs[0].strike == self.legs[1].strike:
                raise ValueError("Vertical spread legs must have different strikes")
            if self.legs[0].position_side == self.legs[1].position_side:
                raise ValueError("Vertical spread legs must have opposite sides")
                
        elif self.spread_type == OptionSpreadType.CALENDAR:
            if len(self.legs) != 2:
                raise ValueError("Calendar spread must have exactly 2 legs")
            # Validate expiration dates and strikes
            if self.legs[0].expire_date == self.legs[1].expire_date:
                raise ValueError("Calendar spread legs must have different expiration dates")
            if self.legs[0].strike != self.legs[1].strike:
                raise ValueError("Calendar spread legs must have same strike")
                
        elif self.spread_type == OptionSpreadType.BUTTERFLY:
            if len(self.legs) != 3:
                raise ValueError("Butterfly spread must have exactly 3 legs")
            # Validate strikes and ratios
            strikes = sorted([leg.strike for leg in self.legs])
            if not (strikes[1] - strikes[0] == strikes[2] - strikes[1]):
                raise ValueError("Butterfly spread must have equal wing widths")
            # Validate butterfly ratios (1:2:1)
            if self.leg_ratios != {0: 1.0, 1: 2.0, 2: 1.0}:
                raise ValueError("Butterfly spread must have 1:2:1 ratio")
                
        elif self.spread_type == OptionSpreadType.IRON_CONDOR:
            if len(self.legs) != 4:
                raise ValueError("Iron condor must have exactly 4 legs")
            # Additional iron condor validations would go here

    @cached_property
    def spread_price(self) -> float:
        """Calculate the net price of the spread."""
        return sum(leg.get_signed_entry_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))

    @cached_property    
    def max_risk(self) -> float:
        """Calculate maximum risk for defined-risk spreads."""
        if self.spread_type == OptionSpreadType.VERTICAL:
            strikes = sorted([leg.strike for leg in self.legs])
            return abs(strikes[1] - strikes[0]) * 100
            
        elif self.spread_type == OptionSpreadType.IRON_CONDOR:
            legs = sorted(self.legs, key=lambda x: x.strike)
            put_spread_width = abs(legs[1].strike - legs[0].strike)
            call_spread_width = abs(legs[3].strike - legs[2].strike)
            return max(put_spread_width, call_spread_width) * 100
            
        elif self.spread_type == OptionSpreadType.BUTTERFLY:
            legs = sorted(self.legs, key=lambda x: x.strike)
            return abs(legs[2].strike - legs[0].strike) * 100
            
        return None  # For undefined risk spreads

    @cached_property
    def margin_required(self) -> float: 
        """Calculate total margin requirement for the spread."""
        if self.spread_type == OptionSpreadType.NONE:
            return sum(leg.calculate_margin() for leg in self.legs)
            
        # For defined risk spreads, use max_risk
        max_risk = self.max_risk
        if max_risk is not None:
            return max_risk
            
        # For undefined risk spreads, sum individual margins
        return sum(leg.calculate_margin() for leg in self.legs)

    def calculate_pnl(self, exit_price: float) -> float:
        """Calculate total P&L for the spread."""
        return sum(leg.calculate_pnl(exit_price=exit_price) * self.leg_ratios[i] for i, leg in enumerate(self.legs))

    def to_dict(self) -> Dict:
        """Convert spread to dictionary format."""
        return {
            'spread_type': self.spread_type.value,
            'spread_id': self.spread_id,
            'legs': [leg.to_dict() for leg in self.legs],
            'leg_ratios': self.leg_ratios,
            'spread_price': self.spread_price or self.net_price,
            'margin_required': self.margin_required
        } 
    
    @staticmethod
    def calculate_margin(spread_type: OptionSpreadType,
                         legs: Tuple[List[SingleLegOptionPosition], NamedTuple]
                         ) -> float:
        """
        Calculate margin requirement for a spread position.
        
        Args:
            leg_group: DataFrame containing the legs of the spread
            
        Returns:
            float: Total margin required for the spread
        """
        # logger.debug(f'Calculating margin for spread type: {leg_group.iloc[0]["spread_type"]}')
        legs = list(legs.itertuples()) if isinstance(legs, pd.NamedTuple) else legs
        return 0
        # # For diagonal spreads, margin depends on whether it's a long or short diagonal spread
        # if OptionSpreadType.is_spread_type(spread_type, OptionSpreadType.DIAGONAL):   
        #     total_margin = 0
        #     for leg in legs:
        #         leg_margin = SingleLegOptionPosition.calculate_margin(
        #             leg.quantity,
        #             leg.option_type,
        #             leg.position_side,
        #             leg.underlying_entry,
        #             leg.entry_price,
        #             leg.strike,
        #             leg.expire_date
        #         )
        #         total_margin += leg_margin
        #     return total_margin
        
        # # For vertical spreads, margin is the width of the spread
        # elif OptionSpreadType.is_spread_type(spread_type, OptionSpreadType.VERTICAL):
        #     if len(legs) != 2:
        #         raise ValueError(f"Vertical spread must have exactly 2 legs, got {len(legs)}")
        #     strikes = sorted([leg.strike for leg in legs]) if isinstance(legs, SingleLegOptionPosition) else sorted([leg.get_attr(f'leg_{i}_strike') for i in range(len(legs))])
        #     return abs(strikes[1] - strikes[0]) * 100
        
        # # For calendar spreads, margin is the width of the spread
        # elif OptionSpreadType.is_spread_type(spread_type, OptionSpreadType.CALENDAR):
        #     if len(legs) != 2:
        #         raise ValueError(f"Calendar spread must have exactly 2 legs, got {len(legs)}")
        #     strikes = sorted([leg.strike for leg in legs])
        #     return abs(strikes[1] - strikes[0]) * 100
        
        # # For iron condors, margin is the width of the put spread plus the width of the call spread
        # elif OptionSpreadType.is_spread_type(spread_type, OptionSpreadType.IRON_CONDOR):
        #     if len(legs) != 4:
        #         raise ValueError(f"Iron condor must have exactly 4 legs, got {len(legs)}")
            
        #     # Sort legs by strike price
        #     legs.sort(key=lambda x: x.strike) if isinstance(legs, SingleLegOptionPosition) else legs.sort(key=lambda x: x.get_attr(f'leg_{i}_strike') for i in range(len(legs)))    
        #     strikes = sorted([leg.strike for leg in legs])
            
        #     # Calculate width of put spread (first two legs) and call spread (last two legs)
        #     put_spread_width = abs(strikes[1] - strikes[0])
        #     call_spread_width = abs(strikes[3] - strikes[2])
            
        #     return (put_spread_width + call_spread_width) * 100
        
        # # For butterflies, margin is the width of the spread
        # elif OptionSpreadType.is_spread_type(spread_type, OptionSpreadType.BUTTERFLY):
        #     if len(legs) != 3:
        #         raise ValueError(f"Butterfly spread must have exactly 3 legs, got {len(legs)}")
        #     strikes = sorted([leg.strike for leg in legs])
        #     return abs(strikes[2] - strikes[0]) * 100
        
        # else:
        #     raise ValueError(f"Unsupported spread type: {spread_type}")
    

class TestOptionPositions(unittest.TestCase):

    def setUp(self):

        # Set up data paths
        DATA_PATH = "/Users/liefe/Data/spx"
        OPTIONS_FILE = "options_chain_preprocessed.csv"

        self.dl = DataLoader(data_dir=DATA_PATH, options_file=OPTIONS_FILE, use_preprocessed=True, save_preprocessed=False)
        self.data = self.dl.load_data()


        """Set up test data for the tests."""
        # Sample data for a single leg option position
        self.single_leg_long = SingleLegOptionPosition(
            trade_id=1,
            quantity=10,
            option_type=OptionType.CALL,
            position_side=PositionSide.LONG,
            entry_date=pd.Timestamp('2023-01-01'),
            entry_price=5.0,
            strike=4000.0,
            expire_date=pd.Timestamp('2023-12-31'),
            entry_delta=0.5,
            entry_dte=30,
            underlying_entry=95.0
        )
        self.single_leg_short = SingleLegOptionPosition(
            trade_id=1,
            quantity=10,
            option_type=OptionType.PUT,
            position_side=PositionSide.SHORT,
            entry_date=pd.Timestamp('2023-01-01'),
            entry_price=5.0,
            strike=100.0,
            expire_date=pd.Timestamp('2023-12-31'),
            entry_delta=0.5,
            entry_dte=30,
            underlying_entry=95.0
        )
        # Sample data for a multi-leg option position
        self.multi_leg = MultiLegOptionPosition(
            trade_id=2,
            quantity=5,
            position_side=PositionSide.SHORT,
            entry_date=pd.Timestamp('2023-01-01'),
            entry_price=3.0,
            spread_id=1,
            spread_type=OptionSpreadType.VERTICAL,
            legs=[
                SingleLegOptionPosition(
                    trade_id=3,
                    quantity=5,
                    option_type=OptionType.CALL,
                    position_side=PositionSide.SHORT,
                    entry_date=pd.Timestamp('2023-01-01'),
                    entry_price=3.0,
                    strike=100.0,
                    expire_date=pd.Timestamp('2023-12-31'),
                    entry_delta=0.5,
                    entry_dte=30,
                    underlying_entry=95.0
                ),
                SingleLegOptionPosition(
                    trade_id=4,
                    quantity=5,
                    option_type=OptionType.CALL,
                    position_side=PositionSide.LONG,
                    entry_date=pd.Timestamp('2023-01-01'),
                    entry_price=2.0,
                    strike=105.0,
                    expire_date=pd.Timestamp('2023-12-31'),
                    entry_delta=0.4,
                    entry_dte=30,
                    underlying_entry=95.0
                )
            ],
            leg_ratios={0: 1, 1: 1}
        )

    def test_single_leg_margin(self):
        """Test margin calculation for a single leg option position."""
        margin = self.single_leg_long.calculate_margin(quantity=self.single_leg_long.quantity,
                                                  option_type=self.single_leg_long.option_type,
                                                  position_side=self.single_leg_long.position_side,
                                                  underlying_price=self.single_leg_long.underlying_entry,
                                                  entry_price=self.single_leg_long.entry_price,
                                                  strike=self.single_leg_long.strike )
        self.assertEqual(margin, 0)  # Assuming long position has no margin
        margin = self.single_leg_short.calculate_margin(quantity=self.single_leg_short.quantity,
                                                  option_type=self.single_leg_short.option_type,
                                                  position_side=self.single_leg_short.position_side,
                                                  underlying_price=self.single_leg_short.underlying_entry,
                                                  entry_price=self.single_leg_short.entry_price,
                                                  strike=self.single_leg_short.strike )
        self.assertEqual(margin, 19250)   
    # def test_multi_leg_margin(self):
    #     """Test margin calculation for a multi-leg option position."""
    #     margin = self.multi_leg.calculate_margin(quantity=self.multi_leg.quantity)
    #     self.assertIsInstance(margin, float)  # Check if margin is a float

    def test_single_leg_pnl(self):
        """Test P&L calculation for a single leg option position."""
        pnl = self.single_leg_long.calculate_pnl(exit_price=6.0)  # Example exit price
        self.assertEqual(pnl, 1000.0)  # Assuming entry price was 5.0 and quantity is 10
        pnl = self.single_leg_short.calculate_pnl(exit_price=10.0)  # Example exit price
        self.assertEqual(pnl, -5000.0)  # Assuming entry price was 5.0 and quantity is 10

    def test_instance_vars(self):
        self.assertEqual(self.single_leg_long.position_side, PositionSide.LONG)
        self.assertEqual(self.single_leg_short.position_side, PositionSide.SHORT)
        # self.assertEqual(self.multi_leg.position_side, PositionSide.SHORT)  
        self.assertEqual(self.single_leg_long.is_put, False)        
        self.assertEqual(self.single_leg_short.is_put, True)
        self.assertEqual(self.single_leg_long.is_call, True)
        self.assertEqual(self.single_leg_short.is_call, False)
        self.assertEqual(self.single_leg_long.is_long, True)
        self.assertEqual(self.single_leg_short.is_long, False)
    
    def test_intrinsic_value(self):
        self.assertEqual(self.single_leg_long.intrinsic_value(underlying_price=105), 5)
        self.assertEqual(self.single_leg_long.intrinsic_value(underlying_price=95), 0)
        self.assertEqual(self.single_leg_short.intrinsic_value(underlying_price=105), 0)
        self.assertEqual(self.single_leg_short.intrinsic_value(underlying_price=95), 5)
    # def test_multi_leg_pnl(self):
    #     """Test P&L calculation for a multi-leg option position."""
    #     pnl = self.multi_leg.calculate_pnl(exit_price=4.0)  # Example exit price
    #     self.assertIsInstance(pnl, float)  # Check if P&L is a float

    def test_close_position(self):
        """Test closing a position."""
        trade_result = self.single_leg_long.close_position(option_chain=self.data['option_chain'],
                                                          underlying_price_history=self.data['underlying_price_history'],
                                                          option_bp=10000)
        transaction = trade_result.transactions[0]
        self.assertIsInstance(trade_result, OptionTradeResult)
        self.assertEqual(trade_result.pnl, 1000.0)
        self.assertEqual(trade_result.quantity, 10)
        self.assertEqual(transaction['option_type'], OptionType.CALL)
        self.assertEqual(transaction['position_side'], PositionSide.LONG)
        self.assertEqual(transaction['entry_date'], pd.Timestamp('2023-01-01'))
        self.assertEqual(transaction['exit_date'], pd.Timestamp('2023-01-01'))
        self.assertEqual(transaction['expire_date'], pd.Timestamp('2023-12-31'))
        self.assertEqual(transaction['entry_delta'], 0.5)
        self.assertEqual(transaction['exit_delta'], 0.5)
        self.assertEqual(transaction['entry_dte'], 30)
        self.assertEqual(transaction['days_held'], 0)
        self.assertEqual(transaction['underlying_entry'], 95.0)
        self.assertEqual(transaction['underlying_exit'], 105.0)
        self.assertEqual(transaction['strike'], 100.0)
        self.assertEqual(transaction['entry_price'], 5.0)
        
if __name__ == '__main__':
    unittest.main()
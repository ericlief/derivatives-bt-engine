from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property
from abc import ABC, abstractmethod
from numpy import info
import pandas as pd

from options_bt.domain.dataloader import DataLoader
from options_bt.domain.enums import *
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.trade_result import BaseTradeResult, OptionTradeResult
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils
logger = setup_logger()
 
 
@dataclass(kw_only=True)
class BasePosition(ABC):
    """Base class for any trading position."""
    trade_id: Optional[int] = None
    transaction_id: Optional[int] = None
    quantity: int
    position_side: Optional[Union[PositionSide, str]] = None
    entry_date: pd.Timestamp
    entry_price: float
    margin_required: Optional[float] = None
    fees: Optional[float] = None   # added 
    # pnl: Optional[float] = None
    
    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.position_side, str):
            self.position_side = PositionSide(self.position_side.lower())
        if isinstance(self.entry_date, str):
            self.entry_date = pd.Timestamp(self.entry_date)

 
    @property
    def signed_entry_price(self) -> float:
        """
        Get the entry price with correct sign based on position side.
        - Long positions should have negative entry price (debit/BTO)
        - Short positions should have positive entry price (credit/STO)
        """
        if hasattr(self, 'legs') and self.legs:
            return self.spread_price
        return -abs(self.entry_price) if PositionSide.is_long(self.position_side) else abs(self.entry_price)

    @property
    def signed_exit_price(self) -> float:
        """
        Get the exit price with correct sign based on position side.
        - Long positions should have positive exit price (credit/STC)
        - Short positions should have negative exit price (debit/BTC)
        """

        return abs(self.exit_price) if PositionSide.is_long(self.position_side) else -abs(self.exit_price)
    
    @abstractmethod
    def calculate_pnl(self, underlying_price_history: pd.DataFrame, close_reason: Optional[str] = 'expiration', commission: Optional[float] = 0.0) -> Optional[float]:
        """Calculate profit and loss for the position."""
        pass
 
    @abstractmethod
    def close(self, 
            option_chain: pd.DataFrame, 
            underlying_price_history: pd.DataFrame,
    ) -> Optional[Tuple[BaseTradeResult, Dict, float]]:
        """
        Close this position and calculate results.
        
        Args:
            option_chain: pd.DataFrame,
            underlying_price_history: pd.DataFrame,
        
        Returns:
            Optional[Tuple[BaseTradeResult, Dict, float]]: Tuple of (trade_result_dict, transaction_dict, bp_effect) if successful, None if closing data is unavailable.
        """
        pass 
    
    @abstractmethod
    def calculate_margin(self, leverage: float = 1.0) -> float:
        """Calculate margin requirement for the position."""
        pass

@dataclass(kw_only=True)
class BaseOptionPosition(BasePosition, ABC):
    """Abstract base class for any option position."""
    # Required parameters (no defaults)
    option_strategy: OptionStrategy
    expire_date: pd.Timestamp
    entry_dte: int
    underlying_entry: float
    option_type: Union[OptionType, str]  # Add missing option_type property
    strike: float  # Add missing strike property
    entry_delta: float  # Add missing entry_delta property


    # Should go into Trade class
    # exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_delta: Optional[float] = None
    underlying_exit: Optional[float] = None
    close_date: Optional[pd.Timestamp] = None  # For early closure
    close_reason: Optional[str] = None  # expiration, early closure
    
    def __post_init__(self):
        super().__post_init__()

        """Validate and convert types after initialization."""
    
        if isinstance(self.close_date, str):
            self.close_date = pd.Timestamp(self.close_date)
            
        if isinstance(self.expire_date, str):
            self.expire_date = pd.Timestamp(self.expire_date)   

        if isinstance(self.option_strategy, str):
            self.option_strategy=OptionStrategy(self.option_strategy)
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
    @abstractmethod
    def is_ITM(sefl, underlying_price) -> bool:
        pass
   
    @cached_property
    def premium(self) -> float:
        """
        Get the unsigned premium for the position.
        """
        pass
    
    @cached_property
    def signed_premium(self) -> float:
        """
        Get the signed premium for the position.
        """
        return -abs(self.premium) if self.is_long else abs(self.premium)

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
        
        Formula: premium + max(15% × underlying - OTM amount, 10% × underlying)
        
        Args:
            quantity: Number of contracts
            option_type: PUT or CALL
            position_side: LONG or SHORT
            underlying_price: Current price of the underlying asset
            entry_price: Option premium (mid of bid/ask)
            strike: Strike price of the option
            leverage: Leverage multiplier (default 1.0)
            margin_req_percent: Margin requirement percentage (default 15% for index options)
        
        Returns:
            float: Required margin in dollars
        """
        # No margin required for long positions
        if PositionSide.is_long(position_side):
            return 0.0
        
        # For short positions, calculate OTM amount based on option type
        if OptionType.is_put(option_type):
            otm_amount = max(0, underlying_price - strike)  # OTM when underlying > strike
        else:  # CALL
            otm_amount = max(0, strike - underlying_price)  # OTM when strike > underlying
        
        # Calculate margin using IB's formula
        margin = (
            entry_price +  # Premium
            max(
                (margin_req_percent * underlying_price - otm_amount),  # 15% of underlying minus OTM amount
                (0.10 * underlying_price)  # Minimum 10% of underlying
            )
        ) * 100 * quantity  # Convert to dollars (100 shares per contract)
        
        return round(margin / leverage, 2)

    def calculate_position_margin(self, leverage: float = 1.0) -> float:
        """Calculate margin requirement for the position using instance variables."""
        
        if not self.entry_price or not self.underlying_entry:
            return 0.0

        return self.calculate_margin(
            quantity=self.quantity,
            option_type=self.option_type,
            position_side=self.position_side,
            underlying_price=self.underlying_entry,
            entry_price=abs(self.entry_price),  # Use absolute value since sign is handled by position side
            strike=self.strike,
            leverage=leverage
        )

    def calculate_pnl(
        self,
        option_chain: pd.DataFrame,
        underlying_price_history: pd.DataFrame,
        close_reason: Optional[str] = 'expiration',
        commission: Optional[float] = 1.78,
        exercise_fee: Optional[float] = 5.0
    ) -> Optional[float]:
        """
        Calculate profit and loss (P&L) for the position, considering all relevant parameters.

        Args:
            option_chain (pd.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset.
            close_reason (Optional[str], optional): Reason for closing the position ('expiration', 'early closure', etc.). Defaults to 'expiration'.
            commission (Optional[float], optional): Transaction fees per contract. Defaults to 1.78.
            exercise_fee (Optional[float], optional): Exercise fee for ITM options per contract. Defaults to 5.0.

        Returns:
            Optional[float]: P&L amount in dollars, or None if exit_price or underlying_exit is not available.
        """
            
        # Get correctly signed prices
        # signed_entry_price = self.signed_entry_price
        # if self.exit_price is None or self.underlying_exit is None:
        #     logger.warning('Either option or underlying exit price instance variables not set correctly for pnl calculation')
        #     return None
     
        # Expiration, get intrinsic value using underlying exit if it has not already been calculated 
        # if close_reason == 'expired':

        #     logger.debug(f'Retrieving data for expiration for {self.option_type}, {self.expire_date}')
            
        #     # # Check if self.exit_price has already been set
        #     # if self.exit_price is None or self.underlying_exit is None:   
        #     #      self._update_closing_data(option_chain, underlying_price_history)
        #     # else:
        #     #     exit_price = self.exit_price  # Use the instance variable if it has been set

        #     # signed_exit_price = self.signed_exit_price
        #     logger.debug(f'self exit price: {self.exit_price}')
        #     logger.debug(f'signed self exit price: {self.signed_exit_price}')
        #     logger.debug(f'Exit price for {self.option_strategy}|{self.underlying_exit}: {self.signed_exit_price}')

        # Early closure, use option exit price
        # elif close_reason == 'early closure':
        #     # if self.exit_price is not None:
        #     logger.debug(f'Calculating pnl for early closure for {self.option_type}, {self.expire_date, {self.exit_price}}')
            # For long positions, exit price should be positive (credit/STC)
            # For short positions, exit price should be negative (debit/BTC)
            # signed_exit_price = self.signed_exit_price if self.exit_price is not None else PriceUtils.get_signed_exit_price(exit_price, self.position_side)

            # else:
                # logger.debug(f'Need to provide exit_price for early closure (pnl) {self.option_type}, {self.expire_date, {exit_price}}')
                # return None
           
        # else:
        #     logger.debug(f'Invalid close reason {close_reason}') 
        #     return None
        
        # Calculate PnL
        pnl = (self.signed_exit_price + self.signed_entry_price) * 100 * self.quantity
        
        # Subtract fees
        fees = commission if commission else 0  # For expiration nad early closure
        # Expiration and Exercise
        if close_reason == 'expiration': 
            itm = self.is_ITM(self.underlying_exit)
            if self.underlying_exit is not None:
                if itm:
                    fees += exercise_fee if exercise_fee else 0  # exercise fee
                    logger.debug(f'Expiration ITM Exercise fees: {fees} per contract')
                    self.close_reason = 'exercise'
                else:
                    logger.debug(f'Expiration OTM fees: {fees} per contract')
                    self.close_reason = close_reason

            else: 
                logger.debug(f'On expiration but not able to calculate intrinsic value. Perhaps an early closure on expiration day {self.option_strategy}')
                return None
        # Early Closure
        else: 
            logger.debug(f'Early Closure fees: {fees} per contract')
            self.close_reason = close_reason


        fees = round(fees * self.quantity, 2)
        self.fees = fees  # Should we return instead?
        logger.info(f"Calculated fees for transaction: {self.transaction_id}|{self.option_strategy}{self.option_type}: {self.fees}")
         # fees *= self.quantity
        
        # Calculate P&L
        pnl -= fees
        # self.pnl = pnl    ???
        logger.info(f"Calculated pnl for {self.option_strategy}{self.option_type}: {pnl}")

        return round(pnl, 2)

    @abstractmethod
    def calculate_intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        pass    

    @abstractmethod
    def _update_closing_data(self, option_chain: pd.DataFrame, underlying_price_history: pd.DataFrame) -> bool:
        """
        Update the instance with closing price data for the position.
        
        Args:
            option_chain (pd.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        pass

    @classmethod
    def create_vertical_spread(
        cls,
        strikes: List[float],
        option_type: OptionType,
        expire_date: pd.Timestamp,
        is_credit: bool = True
    ) -> List['OptionPosition']:
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
    ) -> List['OptionPosition']:
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

    @staticmethod
    def create_transaction(position: BaseOptionPosition, date: date, type: str, bp_effect: float = None) -> dict:
        """
        Create a transaction dictionary for an option position.

        Args:
            position: The position object to create transaction for.
            date (date): The date of the transaction.
            type (str): The type of transaction ('open' or 'close').
            bp_effect (float): Current buying power for bp_effect calculation.

        Returns:
            dict: A dictionary containing transaction details.
        """
        
        # days_held = (date - position.entry_date).days

        if type.lower() == 'open':
            price = round(position.entry_price, 2)
            if position.is_long:
                operation = 'BTO'
                effect = 'debit'
            else:
                operation = 'STO'
                effect = 'credit'
        elif type.lower() == 'close':
            price = round(position.exit_price, 2)
            if position.close_reason is not None:
               operation = position.close_reason  # either expiration, exercise, or early closure
            else:
                logger.error(f'Could not determine closing operation for {position}')   
                operation = "UNDEF"
            if position.is_long: 
                effect = 'credit'
            else:
                effect = 'debit'
        else:
            logger.error('Undefined operation type')
            raise ValueError('Undefined operation type. Needs to be either open or close')
  
        return  {
                'transaction_id': position.transaction_id,
                'trade_id': position.trade_id,
                'date': date,
                'type': operation,
                'option_type': position.option_type.value if isinstance(position.option_type, OptionType) else position.option_type,
                'position_side': position.position_side,
                'expire_date': position.expire_date,
                'entry_delta': round(position.entry_delta, 2),
                'exit_delta': round(position.exit_delta, 2) if position.exit_delta is not None else None,
                'entry_dte': position.entry_dte,
                # 'days_held': days_held,
                'underlying_entry': position.underlying_entry,
                'underlying_exit': position.underlying_exit if position.underlying_exit is not None else None,
                'strike': position.strike,
                'price': price,
                'effect': effect,
                'bp_effect': round(bp_effect, 2) if bp_effect is not None else '',
                'fees': round(position.fees, 2) if position.fees is not None else 0
            }


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
        # NOTE: This is not needed anymore since we are using the margin required from the signal it should not compute margin for indiv spread legs
        # if self.entry_price is not None and self.underlying_entry is not None and self.margin_required is None:
        #     self.margin_required = self.calculate_position_margin()

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
        """
        Determine if the position is in the money (ITM) based on the current underlying price.

        Args:
            underlying_price (float): The current price of the underlying asset.

        Returns:
            bool: True if the position is in the money, False otherwise.
        """
        if underlying_price is None:
            logger.error(f'Cannot determine if ITM for {self.option_strategy} because underlying price is None')
            raise ValueError("Underlying price must be provided to determine if the position is in the money (ITM).")
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

    @cached_property
    def premium(self) -> float:
        """
        Get the unsigned premium for the position.
        """
        return self.entry_price * self.quantity * 100

    # @cached_property
    # def signed_entry_price(self) -> float:
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
            option_strategy: OptionStrategy,
            entry_date: pd.Timestamp,
            position_side: PositionSide,    
            option_type: OptionType,
            quantity: int,  
            early_close_after_dit: int = None,
            early_close_on_dte: int = None,
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
            # signed_entry_price = -entry_price if PositionSide.is_long(position_side) else entry_price
            # TODO: check for consistency in code

            # Calculate DTE
            entry_dte = trade_signal.dte if hasattr(trade_signal, 'dte') else pd.Timedelta(trade_signal.expire_date - entry_date).days
            
            # Get margin required from signal if available, otherwise use config
            margin_required = trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else logger.warning(f"Missing margin required for trade signal on {trade_signal.Index}")

            # Early closure
            close_date = (entry_date + pd.Timedelta(days=early_close_after_dit) if early_close_after_dit else
                          trade_signal.expire_date - pd.Timedelta(days=early_close_on_dte) if early_close_on_dte else
                          None)

            # Create the position
            position = SingleLegOptionPosition(
                option_strategy=option_strategy,
                quantity=quantity,
                option_type=option_type,
                position_side=position_side,
                strike=trade_signal.strike,
                entry_date=entry_date,
                expire_date=trade_signal.expire_date,
                entry_price=abs(entry_price),
                entry_delta=trade_signal.p_delta if OptionType.is_put(option_type) else trade_signal.c_delta,
                entry_dte=entry_dte,
                underlying_entry=trade_signal.underlying_last,
                margin_required=margin_required,
                close_date=close_date,
            )

            logger.debug(f'Constructing position from symbol')
            logger.debug(f'{option_strategy} | Premium: {position.premium}')
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
        
       

    def _update_closing_data(self, option_chain: pd.DataFrame, 
        underlying_price_history: pd.DataFrame, 
        force: bool = False) -> bool:
        """
        Update the instance with closing price data for the position.
        
        Args:
            option_chain (pd.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        # Handle single-leg positions (existing logic)
        return self._update_single_leg_closing_data(option_chain, underlying_price_history, force)

    def _update_single_leg_closing_data(self, 
        option_chain: pd.DataFrame, 
        underlying_price_history: pd.DataFrame,
        force: bool = False  
    ) -> bool:
        """Update closing data for single-leg positions (existing logic)."""
        
        # If no close_date, this is an expiration
        if not self.close_date:
            # 1) Try underlying history close
            if self.expire_date in underlying_price_history.index:
                underlying_close = underlying_price_history.loc[self.expire_date, 'close']
            else:
                logger.warning(f"No underlying close for {self.expire_date}; falling back to option chain 'underlying_last'")
                # 2) Try option chain rows on the same calendar date (and expiry)
                oc_same_day = option_chain[
                    (option_chain.index == self.expire_date) &
                    (option_chain['expire_date'] == self.expire_date)
                ].sort_index()

                if not oc_same_day.empty and 'underlying_last' in oc_same_day:
                    underlying_close = float(oc_same_day['underlying_last'].iloc[0])
                else:
                    # 3) Try nearest prior chain row for this expiry
                    oc_exp = option_chain[option_chain['expire_date'] == self.expire_date].sort_index()
                    prior = oc_exp[oc_exp.index <= self.expire_date].tail(1)
                    if not prior.empty and 'underlying_last' in prior:
                        underlying_close = float(prior['underlying_last'].iloc[0])
                    else:
                        logger.error(f"No valid (expiration) closing prices found for strike {self.strike} and expire date {self.expire_date}")
                        return False
            
            logger.info(f'Expiration - underlying close: {underlying_close}')
            
            # Calculate intrinsic value at expiration
            exit_price = self.calculate_intrinsic_value(underlying_close)
            logger.info(f'Expiration {self.expire_date} - strike {self.strike} - exit price: {exit_price}')

            # Get delta value at expiration (best-effort from chain on the day)
            delta_col = "p_delta" if self.is_put else 'c_delta'
            filtered_df = option_chain[
                (option_chain.index == self.expire_date) &
                (option_chain['expire_date'] == self.expire_date) &
                (option_chain['strike'] == self.strike)
            ]
        
            logger.debug(f'filtered_df: {filtered_df}')

            exit_delta = round(filtered_df[delta_col].iloc[0], 2) if not filtered_df.empty else None

            if exit_price is not None:
                self.underlying_exit = underlying_close
                self.exit_price = exit_price
                self.exit_delta = exit_delta
                return True  # Successfully updated

            return False  # Failed to update
   
        # Early close - get data from close_date forward (up to 5 days)
        # Exact close_date filtering with normalization and better diagnostics
        close_dt = pd.Timestamp(self.close_date).normalize()
        exp_dt = pd.Timestamp(self.expire_date).normalize()

        # Force close, e.g. if need to close all positions at end of period 
        if force:  
            # Look both forward and backward when force closing to handle wide spreads
            date_range = pd.date_range(close_dt - pd.Timedelta(days=2), close_dt + pd.Timedelta(days=2)) 
            filtered_df = option_chain[
                (option_chain.index.isin(date_range)) & 
                (option_chain['expire_date'] == exp_dt) &
                (option_chain['strike'] == self.strike)    
            ].sort_index()
        # Otherwise, just early close
        else:
            date_range = pd.date_range(close_dt, close_dt + pd.Timedelta(days=2)) 
            filtered_df = option_chain[
                (option_chain.index.isin(date_range)) &
                (option_chain['expire_date'] == exp_dt) &
                (option_chain['strike'] == self.strike)
            ]

        if filtered_df.empty:
            logger.warning(f"No valid prices found within 2 days of close date {self.close_date}")
            around = option_chain.loc[
                (option_chain.index >= close_dt - pd.Timedelta(days=5)) &
                (option_chain.index <= close_dt + pd.Timedelta(days=5))
            ]
            nearby_dates = sorted(around.index.unique().tolist())
            logger.debug(f"Nearby chain rows around {close_dt} (count={len(around)}): {nearby_dates[:5]}")
            logger.warning(f"No valid prices found in date range around close date {self.close_date} "
                   f"(expire={self.expire_date}, strike={self.strike}); "
                   f"index_dtype={option_chain.index.dtype}")
            return False
            
        bid_col = "p_bid" if self.is_put else "c_bid"
        ask_col = "p_ask" if self.is_put else "c_ask"
        delta_col = "p_delta" if self.is_put else 'c_delta'

        # Try each date until we find valid prices
        wide_spread_dates = []
        for row in filtered_df.itertuples():
            date = row.Index
            bid = getattr(row, bid_col)
            ask = getattr(row, ask_col)
            underlying_close = underlying_price_history.loc[date, 'close']   # added underlying data here
            # Use last close in options data if available 
            if underlying_close is None:
                if hasattr(row, 'underlying_last'):
                    underlying_close = row.underlying_last
                else:
                    logger.error(f'Cannot get closing data because no underlying available for {self.option_strategy}')
                    return False
            exit_delta = round(getattr(row, delta_col), 2)
            logger.debug(f'Calculating midpoint for {bid}-{ask}')
            mid_price = PriceUtils.calculate_midpoint_price(bid, ask)
            if mid_price is not None or not pd.isna(mid_price):
                # Update instance variables only if mid_price is valid
                self.underlying_exit = underlying_close
                self.exit_price = mid_price
                self.exit_delta = exit_delta 
                self.close_date = date   # update since actual close date may have changed
                return True  # Successfully updated
            else:
                # Track dates with wide spreads for potential fallback
                spread_pct = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
                wide_spread_dates.append((date, bid, ask, spread_pct))
        
        # If all spreads are too wide, try using the closest date with the narrowest spread
        if wide_spread_dates: # and force
            logger.warning(f"All spreads too wide in date range, attempting fallback for strike {self.strike}")
            # Sort by date proximity to close_date, then by spread width
            wide_spread_dates.sort(key=lambda x: (abs((x[0] - close_dt).days), x[3]))
            
            # Use the closest date with the narrowest spread, even if it's wide
            fallback_date, fallback_bid, fallback_ask, fallback_spread = wide_spread_dates[0]
            logger.warning(f"Using fallback pricing: date={fallback_date}, bid={fallback_bid}, ask={fallback_ask}, spread={fallback_spread:.2f}%")
            
            # Get underlying price for fallback date
            fallback_underlying = underlying_price_history.loc[fallback_date, 'close']
            if fallback_underlying is None:
                logger.error(f'Cannot get underlying price for fallback date {fallback_date}')
                return False
            
            # Use midpoint even if spread is wide (as last resort)
            fallback_mid_price = (fallback_bid + fallback_ask) / 2
            fallback_delta = round(filtered_df.loc[fallback_date, delta_col], 2) if fallback_date in filtered_df.index else None
            
            self.underlying_exit = fallback_underlying
            self.exit_price = fallback_mid_price
            self.exit_delta = fallback_delta
            self.close_date = fallback_date
            logger.warning(f"Fallback successful: using mid_price={fallback_mid_price} for strike {self.strike}")
            return True
        
        logger.error(f"No valid (early close) closing prices found for strike {self.strike} and expire date {self.expire_date}")
        return False  # Failed to update

 
    def calculate_intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        
        if self.is_put:
            iv = max(0, self.strike - underlying_price)
        else:  # Call        
            iv = max(0, underlying_price - self.strike)
        
        logger.info(f'Calculated intrinsic value for {self.strike} and {underlying_price}->{iv}')
        return iv
    
    def reset(self):
        """Reset the position state for testing."""
        self.exit_price = None
        self.exit_delta = None
        self.underlying_exit = None
        self.close_date = None
        # Reset any other relevant state variables
        return self
    
    def to_dict(self) -> Dict:
        """Convert position to dictionary format."""
        return {
            'trade_id': self.trade_id,
            'quantity': self.quantity,
            'opened': self.entry_date,
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

    def close(self, 
            option_chain: pd.DataFrame, 
            underlying_price_history: pd.DataFrame,
            force: bool = False
    ) -> Optional[Tuple[OptionTradeResult, Dict, float]]:
        """
        Close this single-leg position and calculate results.
        
        Args:
            option_chain: pd.DataFrame, 
            underlying_price_history: pd.DataFrame,
            force: bool = False (if closing at end of backtest)
        
        Returns:
            Optional[Tuple[OptionTradeResult, Dict, float]]: Tuple of (trade_result_dict, transaction_dict, bp_effect) if successful, None if closing data is unavailable.
        """
        logger.info(f"Closing Trade #{self.trade_id}|Trans #{self.transaction_id}|{self.option_strategy}|{self.option_type}|{self.position_side}")
        bp_effect = 0
        close_reason = None
        min_valid_date = pd.Timestamp('1990-01-01')  # Arbitrary date well after 1970

        # Validate entry_date
        if self.entry_date <= min_valid_date:
            logger.error(f"Invalid entry date: {self.entry_date} - skipping trade")
            return None, None, None
        
        # Early closure, get close date with validation
        if self.close_date is not None:
            close_reason = 'early closure'
            close_date = self.close_date
        elif self.expire_date is not None:
            close_reason = 'expiration'
            close_date = self.expire_date
        else:
            logger.error("Both close_date and expire_date are None - skipping trade")
            return None, None, None
        
        # logger.debug(f'Closing {self.trade_id}')
        logger.info(f'Date: {close_date} - Close Reason: {close_reason}')

        # Validate close_date
        if not isinstance(close_date, pd.Timestamp) or close_date <= min_valid_date:
            logger.error(f"Invalid close date: {close_date} - skipping trade")
            return None, None, None
        
        # Ensure close_date is not before entry_date
        if close_date < self.entry_date:
            logger.error(f"Close date {close_date} is before entry date {self.entry_date} - skipping trade")
            return None, None, None
        
        # Update class with closing data
        if not self._update_closing_data(option_chain, underlying_price_history, force):
            logger.error("Skipping trade due to missing close data")
            return None, None, None
            
        # Validate that we have the required exit data
        if self.exit_price is None or self.underlying_exit is None:
            logger.warning("Skipping trade due to missing exit data after update")
            return None, None, None
            
        logger.debug(f'Exit price: {self.exit_price} | Underlying close: {self.underlying_exit}')

        # Add or subtract closing price/premium from buying power
        bp_effect += round(self.signed_exit_price * self.quantity * 100, 2)  # Add/subtract exit premium  
        logger.debug(f'BP Effect: {bp_effect}')
        
        # Restore margin for short positions only if single legs
        if self.is_short:
            bp_effect += self.margin_required if self.margin_required is not None else 0
            logger.debug(f'Updated BP with margin requirement of {self.margin_required}: {bp_effect}')

        
        pnl = self.calculate_pnl(option_chain=option_chain, underlying_price_history=underlying_price_history, close_reason=close_reason)
        logger.debug(f'Calculated pnl: {pnl}')

        # Deduct fees from buying power
        logger.debug(f'Deducting fees from BP: {bp_effect}')
        bp_effect -= self.fees if self.fees is not None else 0 # Deduct fees from buying power 
        logger.debug(f'Deducted fees from BP: {bp_effect}')

        # Calculate days held
        days_held = pd.Timedelta(close_date - self.entry_date).days
        if days_held < 0:
            logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
            return None, None, None
    
        logger.debug(f'ready to return result. PnL: {pnl}')

        # Create transaction 
        transaction = self.create_transaction(self, close_date, 'close', bp_effect)
        
        # Prepare trade result
        trade_result = OptionTradeResult(
            trade_id=self.trade_id,
            option_strategy=self.option_strategy.value if isinstance(self.option_strategy, OptionStrategy) else self.option_strategy,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=close_date,
            days_held=days_held,
            close_reason=close_reason,
            premium=round(self.premium, 2),
            fees=round(self.fees, 2),
            pnl=round(pnl, 2),
            bp=None,
            capital_used=round(self.margin_required, 2) if self.margin_required is not None else round(abs(self.entry_price) * self.quantity * 100, 2),
            roi=round(pnl / self.margin_required * 100, 2) if self.margin_required is not None and self.margin_required != 0 else round(pnl / (abs(self.entry_price) * self.quantity * 100) * 100, 2),
        )
        return trade_result, transaction, bp_effect

@dataclass(kw_only=True)
class MultiLegOptionPosition(BaseOptionPosition):
    """Class representing a multi-leg option spread.""" 
    spread_type: OptionSpreadType
    legs: List[SingleLegOptionPosition] 
    leg_ratios: Dict[int, float] = None  # Maps leg index to ratio
    # spread_price: Optional[float] = None  # property will calculate the net price
    # net_price: Optional[float] = None
    expire_date: Optional[pd.Timestamp] = None # Common expiration date for the spread
    entry_dte: int = 0  # Common DTE for the spread
    underlying_entry: Optional[float] = None  # Common underlying entry price for the spread
    option_type: Union[OptionType, str] = None  # Will be derived from legs
    strike: float = None  # Will be derived from legs
    entry_delta: float = None  # Will be derived from legs

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
        # total_entry_price = self.spread_price  #sum(leg.signed_entry_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))
        self.position_side = PositionSide.SHORT if self.entry_price > 0 else PositionSide.LONG
        
        # Assuming same underlying entry price for all legs
        self.underlying_entry = self.legs[0].underlying_entry
        
        # Set derived properties from legs
        if self.legs:
            # Use the first leg's option type and strike as representative
            self.option_type = self.legs[0].option_type
            self.strike = self.legs[0].strike
            # Calculate weighted average entry delta
            total_delta = sum(leg.entry_delta * self.leg_ratios.get(i, 1.0) for i, leg in enumerate(self.legs))
            total_ratio = sum(self.leg_ratios.get(i, 1.0) for i in range(len(self.legs)))
            self.entry_delta = total_delta / total_ratio if total_ratio > 0 else 0.0

        logger.info(f'Creating spread with signed entryprice: {self.signed_entry_price}')
        logger.info(f'Creating spread with spread price: {self.spread_price}')

        assert abs(round(self.signed_entry_price, 2)) == abs(round(self.spread_price, 2))

        # Validate spread configuration
        self.validate_spread()

    def _update_closing_data(self, option_chain: pd.DataFrame, underlying_price_history: pd.DataFrame) -> bool:
        """
        Update the instance with closing price data for the position.
        
        Args:
            option_chain (pd.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        return self._update_multileg_closing_data(option_chain, underlying_price_history)

    def is_ITM(self, underlying_price: float) -> bool:
        """
        Determine if the position is in the money (ITM) based on the current underlying price.
        For multi-leg positions, this is a simplified check based on the first leg.

        Args:
            underlying_price (float): The current price of the underlying asset.

        Returns:
            bool: True if the position is in the money, False otherwise.
        """
        if underlying_price is None:
            logger.error(f'Cannot determine if ITM for {self.option_strategy} because underlying price is None')
            raise ValueError("Underlying price must be provided to determine if the position is in the money (ITM).")
        
        # Use the first leg's option type and strike for ITM calculation
        if self.legs:
            first_leg = self.legs[0]
            return first_leg.is_ITM(underlying_price)
        
        # Fallback to base implementation
        return self.option_type == OptionType.PUT and underlying_price <= self.strike or self.option_type == OptionType.CALL and underlying_price >= self.strike

    def calculate_intrinsic_value(self, underlying_price: float) -> float:
        """
        Calculate intrinsic value at expiration.
        For multi-leg positions, this calculates the net intrinsic value across all legs.

        Args:
            underlying_price (float): The underlying price at expiration.

        Returns:
            float: Net intrinsic value of the spread.
        """
        if not self.legs:
            return 0.0
        
        net_intrinsic_value = 0.0
        for i, leg in enumerate(self.legs):
            ratio = self.leg_ratios.get(i, 1.0)
            leg_intrinsic = leg.calculate_intrinsic_value(underlying_price)
            
            # Adjust for position side
            if leg.position_side == PositionSide.SHORT:
                net_intrinsic_value += leg_intrinsic * ratio  # Short legs are positive
            else:
                net_intrinsic_value -= leg_intrinsic * ratio  # Long legs are negative
        
        return net_intrinsic_value

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
        
        spread_price = sum(leg.signed_entry_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))
        # assert self.signed_entry_price == spread_price

        return round(spread_price, 2)

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
    def premium(self) -> float:
        # dollars, includes per-leg quantities; ignore self.quantity here
        return round(100 * sum(leg.signed_entry_price * leg.quantity for leg in self.legs), 2)

    @cached_property
    def margin_required(self) -> float: 
        """Calculate total margin requirement for the spread."""
        if self.spread_type == OptionSpreadType.NONE:
            return sum(leg.calculate_position_margin() for leg in self.legs)
            
        # For defined risk spreads, use max_risk
        max_risk = self.max_risk
        if max_risk is not None:
            return max_risk
            
        # For undefined risk spreads, sum individual margins
        return sum(leg.calculate_position_margin() for leg in self.legs)

    def calculate_pnl(self, option_chain:pd.DataFrame, underlying_price_history: pd.DataFrame, close_reason: Optional[str]=None,  commission: Optional[float]=None, exercise_fee: Optional[float]=None) -> Optional[float]:
        """Calculate total P&L for the spread."""
        total_pnl = 0.0
        total_fees = 0.0
        
        # Calculate spread exit price from individual legs' exit prices
        if self.spread_type == OptionSpreadType.VERTICAL:
            self._calculate_vertical_spread_exit_price()
        elif self.spread_type == OptionSpreadType.CALENDAR:
            self._calculate_calendar_spread_exit_price()
        elif self.spread_type == OptionSpreadType.IRON_CONDOR:
            self._calculate_iron_condor_exit_price()
        elif self.spread_type == OptionSpreadType.BUTTERFLY:
            self._calculate_butterfly_spread_exit_price()
        else:
            self._calculate_simple_spread_exit_price()
        
        # Set underlying exit to the first leg's underlying exit (they should be the same)
        if self.legs and hasattr(self.legs[0], 'underlying_exit'):
            self.underlying_exit = self.legs[0].underlying_exit
        
        for i, leg in enumerate(self.legs):
            # Pass the leg's determined exit_price and underlying_exit to its PnL calculation
            leg_pnl = leg.calculate_pnl(option_chain=option_chain, underlying_price_history=underlying_price_history, close_reason=close_reason, commission=commission, exercise_fee=exercise_fee)
            if leg_pnl is not None:
                total_pnl += leg_pnl * self.leg_ratios[i]
                total_fees += leg.fees * self.leg_ratios[i] if leg.fees is not None else 0.0

        self.fees = round(total_fees, 2)  # NOTE that fees take quantity at leg level
        return round(total_pnl, 2)

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






    @staticmethod
    def construct_from_signal(
            trade_signal: NamedTuple,
            config: MultiLegOptionStrategyConfig,
            entry_date: pd.Timestamp,
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

                MultiLegOptionStrategyConfig(
                    quantity=1,
                    option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
                    spread_type=OptionSpreadType.VERTICAL,
                    leg_ratio={0: 1.0, 1: 2.0, 2: 2.0, 3: 1.0},
                    initial_capital=100000,
                    leverage=1.0,
                    start_date="2020-01-01",
                    end_date="2020-12-31",
                    use_underlying_close=False,
                    early_close_days=30,
                    max_margin_utilization=0.80,
                    max_positions=1,
                    max_spread_width=100,
                    max_trade_loss=5000.00,
                    trade_selection_method=TradeSelectionMethod.PREMIUM_FIRST,
                    
                    # Define the leg of the strategy
                    legs=[
                        OptionLegConfig(
                        option_type=OptionType.PUT,
                        position_side=PositionSide.SHORT,
                        # delta_range=(0.65, 0.75), # one 
                        delta_target=0.75,          # or another
                        dte_range=(40, 45),
                        ),
                        OptionLegConfig(
                        option_type=OptionType.PUT,
                        position_side=PositionSide.LONG,
                        # delta_range=(0.65, 0.75), # one 
                        delta_target=0.55,          # or another
                        dte_range=(40, 45),
                        )
                    ],
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
            
            # Retrieve and construct legs for the spread
            n_legs = len(config.legs)
            legs = []
            for i in range(n_legs):
                # Get keys
                leg_n = f"leg{i+1}_"
                option_type = config.legs[i].option_type
                position_side = config.legs[i].position_side

                # Strike
                strike_str = leg_n + 'strike'
                # Direct attribute access (more reliable with NamedTuple)
                strike = getattr(trade_signal, strike_str)
                if strike is None or pd.isna(strike):
                    logger.error(f"Missing strike value/s in trade signal on {trade_signal.Index}")
                    return None

                # Delta
                type_prefix = "p_" if OptionType.is_put(option_type) else "c_"
                prefix = leg_n + type_prefix
                delta_str = prefix + 'delta'
                entry_delta = getattr(trade_signal, delta_str)
                if entry_delta is None or pd.isna(entry_date):
                    logger.error(f"Missing delta value/s in trade signal on {trade_signal.Index}")
                    return None

                # DTE
                dte_str = leg_n + 'dte'
                entry_dte = getattr(trade_signal, dte_str)
                if entry_dte is None or pd.isna(entry_dte):
                    logger.error(f"Missing dte value/s in trade signal on {trade_signal.Index}")
                    return None
                
                # Entry price (midpoint_price)
                price_str = leg_n + 'midpoint_price'
                entry_price = getattr(trade_signal, price_str)
                if entry_price is None or pd.isna(entry_price):
                    logger.error(f"Missing midpoint price value/s in trade signal on {trade_signal.Index}")
                    return None

                position = SingleLegOptionPosition(
                    option_strategy=config.option_strategy,
                    quantity=quantity, # Individual legs should always have a quantity of 1, as the overall spread quantity is handled by MultiLegOptionPosition
                    option_type=option_type,
                    position_side=position_side,
                    strike=strike,
                    entry_date=entry_date,
                    expire_date=trade_signal.expire_date,
                    entry_price=abs(entry_price),
                    entry_delta=entry_delta,
                    entry_dte=entry_dte,
                    underlying_entry=trade_signal.underlying_last,
                    margin_required=margin_required,
                    close_date=entry_date + pd.Timedelta(days=config.early_close_days) if config.early_close_days is not None else None,
                )

                legs.append(position)

            # Get entry price (already validated in signal generation)
            entry_price = round(trade_signal.spread_price, 2)
            if entry_price is None:
                logger.error(f"Missing spread price for trade signal on {trade_signal.Index}")
                return None
                
            position_side = PositionSide('short') if entry_price >= 0 else PositionSide('long')
            # Adjust entry price sign based on position side
            # signed_entry_price = -entry_price if PositionSide.is_long(position_side) else entry_price
         
            
            # Validate dte value
            # n_legs = len(config.legs)
            # for i in range(n_legs, 1):
            #     leg_prefix = f"leg{i}_"
            #     if not hasattr(trade_signal, leg_prefix + 'dte') or pd.isna(getattr(trade_signal, leg_prefix + 'dte')):
            #         logger.error(f"Missing dte value in trade signal on leg {i} of {trade_signal.Index}")
            #         return None
            entry_dte = getattr(trade_signal, 'leg1_dte')   
            
            # Get margin required from signal if available, otherwise use config
            margin_required = trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else logger.warning(f"Missing margin required for trade signal on {trade_signal.Index}")

            # Create the position
            position = MultiLegOptionPosition(
                quantity=config.quantity,  # spread quantity only set
                option_type=config.legs[0].option_type,
                option_strategy=config.option_strategy,
                spread_type=config.spread_type,
                legs=legs,
                leg_ratios=config.leg_ratios,
                position_side=position_side,
                entry_date=entry_date,
                expire_date=trade_signal.expire_date,
                entry_price=abs(entry_price),  # Store positive price, use signed accessors
                entry_dte=entry_dte,
                underlying_entry=trade_signal.underlying_last,
                margin_required=margin_required,
                close_date=entry_date + pd.Timedelta(days=config.early_close_days) if config.early_close_days is not None else None,
            )

            logger.debug(f'Constructing spread position from symbol')
            logger.debug(f'{config.option_strategy} | Premium: {position.premium}')
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
    
    def _update_multileg_closing_data(self, option_chain: pd.DataFrame, underlying_price_history: pd.DataFrame) -> bool:
        """Update closing data for multi-leg positions."""

        logger.debug(f"Updating closing data for {self.spread_type} spread with {len(self.legs)} legs")
        
        # Update each individual leg first
        success = True
        for i, leg in enumerate(self.legs):
            logger.debug(f"Updating leg {i+1}: {leg.option_type} {leg.position_side} {leg.strike}")
            if not leg._update_single_leg_closing_data(option_chain, underlying_price_history):
                success = False
                logger.warning(f"Failed to update leg {i+1}")
        
        if not success:
            return False
        
        # Calculate net spread exit price based on spread type
        if self.spread_type == OptionSpreadType.VERTICAL:
            self._calculate_vertical_spread_exit_price()
        elif self.spread_type == OptionSpreadType.CALENDAR:
            self._calculate_calendar_spread_exit_price()
        elif self.spread_type == OptionSpreadType.IRON_CONDOR:
            self._calculate_iron_condor_exit_price()
        elif self.spread_type == OptionSpreadType.BUTTERFLY:
            self._calculate_butterfly_spread_exit_price()
        else:
            logger.warning(f"Unknown spread type {self.spread_type}, using simple leg aggregation")
            self._calculate_simple_spread_exit_price()
        
        # Set underlying exit to the first leg's underlying exit (they should be the same)
        if self.legs:
            self.underlying_exit = self.legs[0].underlying_exit
        
        return True

    def _calculate_vertical_spread_exit_price(self):
        """Calculate exit price for vertical spreads."""
        if len(self.legs) != 2:
            logger.error(f"Vertical spread must have exactly 2 legs, got {len(self.legs)}")
            return
        
        # For vertical spreads, exit price is the difference between leg exit prices
        # Adjust for position side (credit vs debit spread)
        leg1_exit = self.legs[0].exit_price
        leg2_exit = self.legs[1].exit_price
        
        # Net spread exit = long leg exit price - short leg exit price
        if self.legs[0].position_side == PositionSide.LONG:
            self.exit_price = leg1_exit - leg2_exit
        else:  # short leg1
            self.exit_price = leg2_exit - leg1_exit
        
        logger.debug(f"Vertical spread exit price: {self.exit_price} (leg1: {leg1_exit}, leg2: {leg2_exit})")

    def _calculate_calendar_spread_exit_price(self):
        """Calculate exit price for calendar spreads."""
        if len(self.legs) != 2:
            logger.error(f"Calendar spread must have exactly 2 legs, got {len(self.legs)}")
            return
        
        # For calendar spreads, exit price is typically the difference between leg exit prices
        # This assumes you're closing both legs at the same time
        leg1_exit = self.legs[0].exit_price
        leg2_exit = self.legs[1].exit_price
        
        if self.legs[0].position_side == PositionSide.SHORT and self.legs[1].position_side == PositionSide.LONG:
            # Standard calendar: short front month, long back month
            self.exit_price = leg2_exit - leg1_exit
        else:
            # Reverse calendar: long front month, short back month
            self.exit_price = leg1_exit - leg2_exit
        
        logger.debug(f"Calendar spread exit price: {self.exit_price}")

    def _calculate_iron_condor_exit_price(self):
        """Calculate exit price for iron condors."""
        if len(self.legs) != 4:
            logger.error(f"Iron condor must have exactly 4 legs, got {len(self.legs)}")
            return
        
        # Iron condor exit price is the sum of the two spreads
        # Put spread: legs[0] (long) - legs[1] (short)
        # Call spread: legs[2] (short) - legs[3] (long)
        put_spread_exit = self.legs[0].exit_price - self.legs[1].exit_price
        call_spread_exit = self.legs[2].exit_price - self.legs[3].exit_price
        
        self.exit_price = put_spread_exit + call_spread_exit
        logger.debug(f"Iron condor exit price: {self.exit_price} (put: {put_spread_exit}, call: {call_spread_exit})")

    def _calculate_butterfly_spread_exit_price(self):
        """Calculate exit price for butterfly spreads."""
        if len(self.legs) != 3:
            logger.error(f"Butterfly spread must have exactly 3 legs, got {len(self.legs)}")
            return
        
        # Butterfly exit price: 2 * middle leg - outer legs
        # Assuming 1:2:1 ratio
        middle_exit = self.legs[1].exit_price
        outer1_exit = self.legs[0].exit_price
        outer2_exit = self.legs[2].exit_price
        
        self.exit_price = 2 * middle_exit - outer1_exit - outer2_exit
        logger.debug(f"Butterfly spread exit price: {self.exit_price}")

    def _calculate_simple_spread_exit_price(self):
        """Calculate exit price by simple aggregation of leg exit prices."""
        # Simple approach: sum up all leg exit prices weighted by their ratios
        total_exit = 0
        for i, leg in enumerate(self.legs):
            ratio = self.leg_ratios.get(i, 1.0)
            if leg.position_side == PositionSide.SHORT:
                total_exit += leg.exit_price * ratio  # Short legs are positive
            else:
                total_exit -= leg.exit_price * ratio  # Long legs are negative
        
        self.exit_price = total_exit
        logger.debug(f"Simple spread exit price: {self.exit_price}")

    def close(self,
            option_chain: pd.DataFrame,
            underlying_price_history: pd.DataFrame,
            force: bool = True) -> Optional[Tuple[Dict, List[Dict], float]]:
        """
        Close this multi-leg position and calculate results for each leg and the spread.
        
        Args:
            option_chain: pd.DataFrame,
            underlying_price_history: pd.DataFrame,
        
        Returns:
            Optional[Tuple[OptionTradeResult, List[Dict], float]]: Tuple of (trade_result_dict, list_of_transaction_dicts, total_bp_effect) if successful, None if closing data is unavailable.
        """
        all_trade_results = []
        all_transactions = []
        total_bp_effect = 0.0
        
        logger.debug(f'Closing spread {self.trade_id}')
        # Close each leg individually and collect results
        for i, leg in enumerate(self.legs):
            # Each leg's close method returns (trade_result, transaction, bp_effect)
            logger.debug(f'Leg {i+1} using close_date={leg.close_date} expire_date={leg.expire_date}')
            leg_trade_result, leg_transaction_dict, leg_bp_effect = leg.close(
                option_chain=option_chain,
                underlying_price_history=underlying_price_history,
                force=force)
            
            if leg_trade_result is None or leg_transaction_dict is None:
                logger.error(f"Skipping spread closure due to missing closing data for leg {i+1}")
                return None, None, None
            
            all_trade_results.append(leg_trade_result)
            all_transactions.append(leg_transaction_dict)
            total_bp_effect += leg_bp_effect

        # After all legs are closed, update the spread's overall PnL and fees
        # Calculate spread exit price from individual legs' exit prices with proper ratios
        # self.exit_price = sum(leg.signed_exit_price * self.leg_ratios[i] for i, leg in enumerate(self.legs))
        
        # Set underlying exit to the first leg's underlying exit (they should be the same)
        if self.legs and hasattr(self.legs[0], 'underlying_exit'):
            self.underlying_exit = self.legs[0].underlying_exit
        
        # Calculate spread PnL from individual legs with proper ratios
        # spread_pnl = sum((leg.signed_exit_price - leg.signed_entry_price) * self.leg_ratios[i] for i, leg in enumerate(self.legs)) * 100 * self.quantity
        spread_pnl = round(sum(trade_result.pnl for trade_result in all_trade_results), 2)   # * self.quantity
        if spread_pnl is None:
            logger.error("Failed to calculate spread PnL during closure.")
            return None, None, None
        logger.debug(f'Calculated spread PnL: {spread_pnl}')

        # The total capital used for the spread is its margin required (already a cached_property)
        spread_capital_used = self.margin_required
        if spread_capital_used is None: # Fallback if margin_required is not yet set
            spread_capital_used = round(sum(leg_res.capital_used for leg_res in all_trade_results), 2)

        # Calculate return on margin for the spread
        # return_on_margin = round(spread_pnl / spread_capital_used * 100, 2) if spread_capital_used > 0 else 0
        
        all_fees = round(sum(res.fees for res in all_trade_results), 2)

        # The total_bp_effect is already calculated from individual legs above
        # No need for additional manual calculation since each leg handles its own bp_effect

        # Determine the close_date for the spread (e.g., the latest close date of its legs)
        # close_date = self.close_date if self.close_date else self.expire_date # Prioritize explicit close_date, then expire_date
        close_date = self.legs[0].close_date if self.legs[0].close_date else self.legs[0].expire_date # actual close date for legs may have been altered (e.g. forced in close all)

        if not close_date and all_transactions:
            # Fallback to the close_date of the first leg if no explicit spread close_date
            close_date = all_transactions[0]['exit_date']
        
        if not close_date:
            logger.error(f"Could not determine close date for spread {self.trade_id}")
            return None, None, None

        # Calculate days_held for the spread
        days_held = (close_date.date() - self.entry_date.date()).days
        if days_held < 0:
            logger.warning(f"Calculated negative days held ({days_held}) for spread {self.trade_id}")
            days_held = 0 # Or handle as an error if appropriate

        # Get close reason from first leg (should be same for all legs)
        close_reason = all_trade_results[0].close_reason

        # Construct the aggregated trade result for the spread
        aggregated_trade_result = OptionTradeResult(
            trade_id=self.trade_id,
            option_strategy=self.option_strategy.value if isinstance(self.option_strategy, OptionStrategy) else self.option_strategy,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=close_date,
            days_held=days_held,
            close_reason=close_reason,
            premium=round(self.premium, 2),
            fees=all_fees,
            pnl=spread_pnl,
            bp=None,
            capital_used=round(self.margin_required, 2),
            roi=round(spread_pnl / self.margin_required * 100, 2) if self.margin_required is not None and self.margin_required != 0 else 0,
        )
        
        return aggregated_trade_result, all_transactions, total_bp_effect
    
    @staticmethod
    def construct_from_signal(
            trade_signal: NamedTuple,
            config: MultiLegOptionStrategyConfig,
            entry_date: pd.Timestamp,
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

                MultiLegOptionStrategyConfig(
                    quantity=1,
                    option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
                    spread_type=OptionSpreadType.VERTICAL,
                    leg_ratio={0: 1.0, 1: 2.0, 2: 2.0, 3: 1.0},
                    initial_capital=100000,
                    leverage=1.0,
                    start_date="2020-01-01",
                    end_date="2020-12-31",
                    use_underlying_close=False,
                    early_close_days=30,
                    max_margin_utilization=0.80,
                    max_positions=1,
                    max_spread_width=100,
                    max_trade_loss=5000.00,
                    trade_selection_method=TradeSelectionMethod.PREMIUM_FIRST,
                    
                    # Define the leg of the strategy
                    legs=[
                        OptionLegConfig(
                        option_type=OptionType.PUT,
                        position_side=PositionSide.SHORT,
                        # delta_range=(0.65, 0.75), # one 
                        delta_target=0.75,          # or another
                        dte_range=(40, 45),
                        ),
                        OptionLegConfig(
                        option_type=OptionType.PUT,
                        position_side=PositionSide.LONG,
                        # delta_range=(0.65, 0.75), # one 
                        delta_target=0.55,          # or another
                        dte_range=(40, 45),
                        )
                    ],
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

            # Early closure
            close_date = (entry_date + pd.Timedelta(days=config.early_close_after_dit) if config.early_close_after_dit else
                          trade_signal.expire_date - pd.Timedelta(days=config.early_close_on_dte) if config.early_close_on_dte else
                          None)
            
            # Retrieve and construct legs for the spread
            n_legs = len(config.legs)
            legs = []
            for i in range(n_legs):
                # Get keys
                leg_n = f"leg{i+1}_"
                quantity = config.quantity * config.leg_ratios[i]  # effective quantity
                option_type = config.legs[i].option_type
                position_side = config.legs[i].position_side

                # Strike
                strike_str = leg_n + 'strike'
                # Direct attribute access (more reliable with NamedTuple)
                strike = getattr(trade_signal, strike_str)
                if strike is None or pd.isna(strike):
                    logger.error(f"Missing strike value/s in trade signal on {trade_signal.Index}")
                    return None

                # Delta
                type_prefix = "p_" if OptionType.is_put(option_type) else "c_"
                prefix = leg_n + type_prefix
                delta_str = prefix + 'delta'
                entry_delta = getattr(trade_signal, delta_str)
                if entry_delta is None or pd.isna(entry_date):
                    logger.error(f"Missing delta value/s in trade signal on {trade_signal.Index}")
                    return None

                # DTE
                dte_str = leg_n + 'dte'
                entry_dte = getattr(trade_signal, dte_str)
                if entry_dte is None or pd.isna(entry_dte):
                    logger.error(f"Missing dte value/s in trade signal on {trade_signal.Index}")
                    return None
                
                # Entry price (midpoint_price)
                price_str = leg_n + 'midpoint_price'
                entry_price = getattr(trade_signal, price_str)
                if entry_price is None or pd.isna(entry_price):
                    logger.error(f"Missing midpoint price value/s in trade signal on {trade_signal.Index}")
                    return None
                
       

                position = SingleLegOptionPosition(
                    option_strategy=config.option_strategy,
                    quantity=quantity, # Individual legs should always have a quantity of 1, as the overall spread quantity is handled by MultiLegOptionPosition
                    option_type=option_type,
                    position_side=position_side,
                    strike=strike,
                    entry_date=entry_date,
                    expire_date=trade_signal.expire_date,
                    entry_price=abs(entry_price),
                    entry_delta=entry_delta,
                    entry_dte=entry_dte,
                    underlying_entry=trade_signal.underlying_last,
                    close_date=close_date,
                )

                legs.append(position)

            # Get entry price (already validated in signal generation)
            entry_price = round(trade_signal.spread_price, 2)
            if entry_price is None:
                logger.error(f"Missing spread price for trade signal on {trade_signal.Index}")
                return None
                
            position_side = PositionSide('short') if entry_price >= 0 else PositionSide('long')
            # Adjust entry price sign based on position side
            # signed_entry_price = -entry_price if PositionSide.is_long(position_side) else entry_price
         
            
            # Validate dte value
            # n_legs = len(config.legs)
            # for i in range(n_legs, 1):
            #     leg_prefix = f"leg{i}_"
            #     if not hasattr(trade_signal, leg_prefix + 'dte') or pd.isna(getattr(trade_signal, leg_prefix + 'dte')):
            #         logger.error(f"Missing dte value in trade signal on leg {i} of {trade_signal.Index}")
            #         return None
            entry_dte = getattr(trade_signal, 'leg1_dte')   
            
            # Get margin required from signal if available, otherwise use config
            margin_required = trade_signal.margin_required if hasattr(trade_signal, 'margin_required') else logger.warning(f"Missing margin required for trade signal on {trade_signal.Index}")

            # Create the position
            position = MultiLegOptionPosition(
                quantity=config.quantity,
                option_type=config.legs[0].option_type,
                option_strategy=config.option_strategy,
                spread_type=config.spread_type,
                legs=legs,
                leg_ratios=config.leg_ratios,
                position_side=position_side,
                entry_date=entry_date,
                expire_date=trade_signal.expire_date,
                entry_price=abs(entry_price),  # Store positive price, use signed accessors
                entry_dte=entry_dte,
                underlying_entry=trade_signal.underlying_last,
                margin_required=margin_required,
                close_date=close_date,
            )

            logger.debug(f'Constructing potential spread position from symbol')
            logger.debug(f'{config.option_strategy} | Price: {position.spread_price} | Premium: {position.premium}')
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
        
       

    def _update_multileg_closing_data(self, option_chain: pd.DataFrame, underlying_price_history: pd.DataFrame) -> bool:
        """Update closing data for multi-leg positions."""

        logger.debug(f"Updating closing data for {self.spread_type} spread with {len(self.legs)} legs")
        
        # Update each individual leg first
        success = True
        for i, leg in enumerate(self.legs):
            logger.debug(f"Updating leg {i+1}: {leg.option_type} {leg.position_side} {leg.strike}")
            if not leg._update_single_leg_closing_data(option_chain, underlying_price_history):
                success = False
                logger.warning(f"Failed to update leg {i+1}")
        
        if not success:
            return False
        
        # Calculate net spread exit price based on spread type
        if self.spread_type == OptionSpreadType.VERTICAL:
            self._calculate_vertical_spread_exit_price()
        elif self.spread_type == OptionSpreadType.CALENDAR:
            self._calculate_calendar_spread_exit_price()
        elif self.spread_type == OptionSpreadType.IRON_CONDOR:
            self._calculate_iron_condor_exit_price()
        elif self.spread_type == OptionSpreadType.BUTTERFLY:
            self._calculate_butterfly_spread_exit_price()
        else:
            logger.warning(f"Unknown spread type {self.spread_type}, using simple leg aggregation")
            self._calculate_simple_spread_exit_price()
        
        # Set underlying exit to the first leg's underlying exit (they should be the same)
        if self.legs:
            self.underlying_exit = self.legs[0].underlying_exit
        
        return True

    def _calculate_vertical_spread_exit_price(self):
        """Calculate exit price for vertical spreads."""
        if len(self.legs) != 2:
            logger.error(f"Vertical spread must have exactly 2 legs, got {len(self.legs)}")
            return
        
        # For vertical spreads, exit price is the difference between leg exit prices
        # Adjust for position side (credit vs debit spread)
        leg1_exit = self.legs[0].exit_price
        leg2_exit = self.legs[1].exit_price
        
        # Net spread exit = long leg exit price - short leg exit price
        if self.legs[0].position_side == PositionSide.LONG:
            self.exit_price = leg1_exit - leg2_exit
        else:  # short leg1
            self.exit_price = leg2_exit - leg1_exit
        
        logger.debug(f"Vertical spread exit price: {self.exit_price} (leg1: {leg1_exit}, leg2: {leg2_exit})")

    def _calculate_calendar_spread_exit_price(self):
        """Calculate exit price for calendar spreads."""
        if len(self.legs) != 2:
            logger.error(f"Calendar spread must have exactly 2 legs, got {len(self.legs)}")
            return
        
        # For calendar spreads, exit price is typically the difference between leg exit prices
        # This assumes you're closing both legs at the same time
        leg1_exit = self.legs[0].exit_price
        leg2_exit = self.legs[1].exit_price
        
        if self.legs[0].position_side == PositionSide.SHORT and self.legs[1].position_side == PositionSide.LONG:
            # Standard calendar: short front month, long back month
            self.exit_price = leg2_exit - leg1_exit
        else:
            # Reverse calendar: long front month, short back month
            self.exit_price = leg1_exit - leg2_exit
        
        logger.debug(f"Calendar spread exit price: {self.exit_price}")

    def _calculate_iron_condor_exit_price(self):
        """Calculate exit price for iron condors."""
        if len(self.legs) != 4:
            logger.error(f"Iron condor must have exactly 4 legs, got {len(self.legs)}")
            return
        
        # Iron condor exit price is the sum of the two spreads
        # Put spread: legs[0] (long) - legs[1] (short)
        # Call spread: legs[2] (short) - legs[3] (long)
        put_spread_exit = self.legs[0].exit_price - self.legs[1].exit_price
        call_spread_exit = self.legs[2].exit_price - self.legs[3].exit_price
        
        self.exit_price = put_spread_exit + call_spread_exit
        logger.debug(f"Iron condor exit price: {self.exit_price} (put: {put_spread_exit}, call: {call_spread_exit})")

    def _calculate_butterfly_spread_exit_price(self):
        """Calculate exit price for butterfly spreads."""
        if len(self.legs) != 3:
            logger.error(f"Butterfly spread must have exactly 3 legs, got {len(self.legs)}")
            return
        
        # Butterfly exit price: 2 * middle leg - outer legs
        # Assuming 1:2:1 ratio
        middle_exit = self.legs[1].exit_price
        outer1_exit = self.legs[0].exit_price
        outer2_exit = self.legs[2].exit_price
        
        self.exit_price = 2 * middle_exit - outer1_exit - outer2_exit
        logger.debug(f"Butterfly spread exit price: {self.exit_price}")

    def _calculate_simple_spread_exit_price(self):
        """Calculate exit price by simple aggregation of leg exit prices."""
        # Simple approach: sum up all leg exit prices weighted by their ratios
        total_exit = 0
        for i, leg in enumerate(self.legs):
            ratio = self.leg_ratios.get(i, 1.0)
            if leg.position_side == PositionSide.SHORT:
                total_exit += leg.exit_price * ratio  # Short legs are positive
            else:
                total_exit -= leg.exit_price * ratio  # Long legs are negative
        
        self.exit_price = total_exit
        logger.debug(f"Simple spread exit price: {self.exit_price}")
        

class FuturesPosition(BasePosition):
    """Class representing a futures position."""
    futures_type: FuturesType
    futures_strategy: FuturesStrategy
    
    underlying_entry: float # The price of the underlying at entry
    underlying_exit: Optional[float] = None # The price of the underlying at exit
    exit_price: Optional[float] = None # For futures, this will usually be the underlying_exit

    roll_date: pd.Timestamp # The date when this futures contract is expected to be rolled
    close_reason: Optional[str] = None # 'roll', 'early closure', etc.
    initial_margin: float # Initial margin for one contract

    def __post_init__(self):
        super().__post_init__()
        if isinstance(self.futures_type, str):
            self.futures_type = FuturesType(self.futures_type)
        if isinstance(self.futures_strategy, str):
            self.futures_strategy = FuturesStrategy(self.futures_strategy)
        
        if isinstance(self.roll_date, str):
            self.roll_date = pd.Timestamp(self.roll_date)
        
        # Set contract multiplier from enum based on futures_type
        # Corrected: Access the 'multiplier' property from the enum instance
        self.contract_multiplier: float = self.futures_type.multiplier 
        
        # For futures, entry_price is the underlying price at entry, so we set underlying_entry
        # This ensures consistency with how futures P&L is typically calculated (exit - entry) * multiplier * quantity
        if self.entry_price:
            self.underlying_entry = self.entry_price

        # Calculate initial margin if not provided
        if self.margin_required is None: # We now have initial_margin as a required attribute
             self.margin_required = self.calculate_margin()

    @property
    def signed_entry_price(self) -> float:
        """
        For futures, the signed entry price is based on the entry price (underlying_entry for P&L)
        and contract multiplier. This represents the total value change per point.
        """
        # For futures, entry_price itself is not a credit/debit, but the change in price is.
        # However, to be consistent with the P&L calculation (exit_price - entry_price),
        # we can represent the "cost" of opening a long future as negative total value,
        # and "revenue" of opening a short future as positive total value for consistency
        # with option premiums if we absolutely must.
        # But for futures, it's more straightforward to just consider the change from entry to exit.
        # For now, let's return 0 as the 'signed entry price' for futures,
        # as the margin is the primary capital commitment and P&L is based on price difference.
        return 0.0 # Futures don't have an "entry price" in the same way options have premiums.
                   # Their value is derived from the underlying price movement.

    @property
    def signed_exit_price(self) -> float:
        """
        For futures, the signed exit price will represent the change in value
        from entry, effectively capturing the P&L per contract.
        """
        if self.underlying_exit is None or self.underlying_entry is None:
            return 0.0
        
        price_diff = (self.underlying_exit - self.underlying_entry) * self.contract_multiplier
        if PositionSide.is_long(self.position_side):
            return price_diff # Positive for long if price goes up
        else: # Short position
            return -price_diff # Positive for short if price goes down

    def calculate_pnl(self, underlying_price_history: pd.DataFrame, close_reason: Optional[str] = 'roll', commission: Optional[float] = 1.0) -> Optional[float]:
        """
        Calculate profit and loss (P&L) for the futures position.
        
        Args:
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset (SPX).
            close_reason (Optional[str], optional): Reason for closing the position ('roll', 'early closure', etc.). Defaults to 'roll'.
            commission (Optional[float], optional): Transaction fees per contract. Defaults to 1.0 (typical for MES).

        Returns:
            Optional[float]: P&L amount in dollars, or None if exit_price or underlying_exit is not available.
        """
        if self.underlying_exit is None or self.underlying_entry is None:
            logger.warning('Underlying entry or exit price not set correctly for futures pnl calculation')
            return None

        # P&L for futures is simply (exit_price - entry_price) * quantity * contract_multiplier
        pnl = (self.underlying_exit - self.underlying_entry) * self.quantity * self.contract_multiplier

        # Subtract fees
        fees = commission * self.quantity
        self.fees = round(fees, 2)
        pnl -= self.fees
        self.close_reason = close_reason # Set the close reason on the position object

        logger.info(f"Calculated pnl for {self.futures_type} futures: {pnl}")
        return round(pnl, 2)

    def calculate_margin(self, leverage: float = 1.0) -> float:
        """
        Calculate margin requirement for the futures position.
        Uses the provided initial_margin attribute.
        """
        return round(self.initial_margin * self.quantity / leverage, 2)

    def _update_closing_data(self, underlying_price_history: pd.DataFrame, close_date: pd.Timestamp) -> bool:
        """
        Update the instance with closing price data for the futures position.
        
        Args:
            underlying_price_history (pd.DataFrame): DataFrame containing historical prices of the underlying asset.
            close_date (pd.Timestamp): The date at which the position is being closed.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        if close_date not in underlying_price_history.index:
            logger.error(f"No underlying closing price available for {close_date} for futures position.")
            return False
        
        self.underlying_exit = underlying_price_history.loc[close_date, 'close']
        self.exit_price = self.underlying_exit # For futures, exit price is the underlying price
        
        return True

    def close(self,
            option_chain: pd.DataFrame, # Not used for futures, but kept for method signature compatibility
            underlying_price_history: pd.DataFrame,
            force: bool = False,
            close_reason: Optional[str] = None # Added close_reason parameter
    ) -> Optional[Tuple[BaseTradeResult, Dict, float]]:
        """
        Close this futures position and calculate results.
        
        Args:
            option_chain: pd.DataFrame (not used for futures)
            underlying_price_history: pd.DataFrame,
            force: bool = False (if closing at end of backtest)
        
        Returns:
            Optional[Tuple[BaseTradeResult, Dict, float]]: Tuple of (trade_result, transaction_dict, bp_effect) if successful, None otherwise.
        """
        logger.info(f"Closing Trade #{self.trade_id}|Trans #{self.transaction_id}|{self.futures_type}|{self.position_side}")
        bp_effect = 0
        
        # Determine the actual close date
        effective_close_date = self.roll_date if self.roll_date and not force else underlying_price_history.index.max() # Use max date if forced

        if not self._update_closing_data(underlying_price_history, effective_close_date):
            logger.error(f"Skipping futures trade due to missing close data for {self.futures_type} on {effective_close_date}")
            return None, None, None

        # For long futures, when closing, the initial margin is released.
        # For short futures, the initial margin is released.
        bp_effect += self.margin_required # Release margin
        
        # Pass commission to pnl calculation. If None, it will use futures_type.transaction_commission
        pnl = self.calculate_pnl(underlying_price_history=underlying_price_history, close_reason=close_reason, commission=self.futures_type.transaction_commission)
        if pnl is None:
            logger.error(f"Failed to calculate PnL for futures position {self.futures_type}")
            return None, None, None

        bp_effect += pnl # PnL affects buying power
        
        # Create transaction 
        transaction = {
            'transaction_id': self.transaction_id,
            'trade_id': self.trade_id,
            'date': effective_close_date,
            'type': 'close',
            'instrument_type': 'futures',
            'futures_type': self.futures_type.value,
            'position_side': self.position_side.value,
            'entry_price': self.entry_price, # This is the underlying_entry from the futures perspective
            'exit_price': self.exit_price,   # This is the underlying_exit from the futures perspective
            'underlying_entry': self.underlying_entry,
            'underlying_exit': self.underlying_exit,
            'quantity': self.quantity,
            'contract_multiplier': self.contract_multiplier,
            'pnl': pnl,
            'fees': self.fees,
            'bp_effect': round(bp_effect, 2)
        }

        # Prepare trade result
        trade_result = BaseTradeResult(
            trade_id=self.trade_id,
            strategy=self.futures_type.value,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=effective_close_date,
            days_held=(effective_close_date - self.entry_date).days,
            close_reason=self.close_reason, # Use the close_reason set during pnl calculation
            fees=self.fees,
            pnl=pnl,
            bp=None, # Will be set by TradeManager
            capital_used=self.margin_required,
            roi=round(pnl / self.margin_required * 100, 2) if self.margin_required != 0 else 0,
        )

        return trade_result, transaction, bp_effect

    @staticmethod
    def construct_from_signal(
            trade_signal: NamedTuple,
            futures_strategy: FuturesStrategy,
            entry_date: pd.Timestamp,
            position_side: PositionSide,
            futures_type: FuturesType,
            quantity: int,
            # initial_margin: float, # Removed as it's now an attribute of FuturesPosition, derived from signal
            roll_date: pd.Timestamp,
        ) -> Optional['FuturesPosition']:
            """
            Creates a FuturesPosition object from a given trade signal.
            
            Args:
                trade_signal: NamedTuple, containing underlying_last (SPX price)
                futures_strategy: FuturesStrategy (e.g., LONG_FUTURES)
                entry_date: pd.Timestamp
                position_side: PositionSide (e.g., LONG)
                futures_type: FuturesType (e.g., MES)
                quantity: int (number of contracts)
                initial_margin: float (initial margin requirement for one contract)
                roll_date: pd.Timestamp (next roll date)

            Returns:
                Optional[FuturesPosition]: Created position if valid, None otherwise
            """
            min_valid_date = pd.Timestamp('1990-01-01')
            if not isinstance(entry_date, pd.Timestamp) or entry_date <= min_valid_date:
                logger.error(f"Invalid entry date {entry_date}")
                return None
            
            if not hasattr(trade_signal, 'close') or pd.isna(trade_signal.close):
                logger.error(f"Missing underlying_last in trade signal on {trade_signal.Index}")
                return None
            
            underlying_entry = trade_signal.close
            
            # For futures, entry_price is the underlying price itself
            entry_price = underlying_entry 

            position = FuturesPosition(
                trade_id=None, # Will be set by TradeManager
                transaction_id=None, # Will be set by TradeManager
                quantity=quantity,
                position_side=position_side,
                entry_date=entry_date,
                entry_price=entry_price, # This is the underlying price at entry
                futures_type=futures_type,
                futures_strategy=futures_strategy,
                initial_margin=futures_type.initial_margin, # Corrected: get initial_margin from the enum instance
                roll_date=roll_date,
                margin_required=None, # Will be calculated in post_init
            )
            # Calculate margin after all attributes are set (depends on quantity and initial_margin)
            # This line is now redundant as margin_required is calculated in __post_init__
            # position.margin_required = position.calculate_margin() 

            logger.debug(f'Constructing futures position from signal: {futures_type} | Entry: {entry_price} | Margin: {position.margin_required}')
            
            return position
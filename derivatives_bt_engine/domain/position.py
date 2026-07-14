from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import Optional, Dict, Union, List, NamedTuple, Tuple
from functools import cached_property
from abc import ABC, abstractmethod
import polars as pl

from derivatives_bt_engine.domain.enums import *
from derivatives_bt_engine.domain.instruments import get_spec
from derivatives_bt_engine.domain.strategy_config import MultiLegOptionStrategyConfig
from derivatives_bt_engine.domain.trade_result import BaseTradeResult, OptionTradeResult, FuturesTradeResult
from derivatives_bt_engine.utils.logger import setup_logger
from derivatives_bt_engine.utils.price_utils import PriceUtils
logger = setup_logger()
 
 
@dataclass(kw_only=True)
class BasePosition(ABC):
    """Base class for any trading position."""
    trade_id: Optional[int] = None
    transaction_id: Optional[int] = None
    quantity: int
    position_side: Optional[Union[PositionSide, str]] = None
    entry_date: date
    entry_price: float
    margin_required: Optional[float] = None
    fees: Optional[float] = None   # added
    # pnl: Optional[float] = None

    def __post_init__(self):
        """Validate and convert types after initialization."""
        if isinstance(self.position_side, str):
            self.position_side = PositionSide(self.position_side.lower())
        if isinstance(self.entry_date, str):
            self.entry_date = date.fromisoformat(self.entry_date)

 
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
    def calculate_pnl(self, underlying_price_history: pl.DataFrame, close_reason: Optional[str] = 'expiration', commission: Optional[float] = 0.0) -> Optional[float]:
        """Calculate profit and loss for the position."""
        pass
 
    @abstractmethod
    def close(self, 
            option_chain: pl.DataFrame, 
            underlying_price_history: pl.DataFrame,
    ) -> Optional[Tuple[BaseTradeResult, Dict, float]]:
        """
        Close this position and calculate results.
        
        Args:
            option_chain: pl.DataFrame,
            underlying_price_history: pl.DataFrame,
        
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
    option_strategy: OptionsStrategy
    expire_date: date
    entry_dte: int
    underlying_entry: float
    option_type: Union[OptionsType, str]  # Add missing option_type property
    strike: float  # Add missing strike property
    entry_delta: float  # Add missing entry_delta property


    # Should go into Trade class
    # exit_date: Optional[date] = None
    multiplier: Optional[float] = 100  # default to stock or index
    exit_price: Optional[float] = None
    exit_delta: Optional[float] = None
    underlying_exit: Optional[float] = None
    close_date: Optional[date] = None  # For early closure
    close_reason: Optional[str] = None  # expiration, early closure

    def __post_init__(self):
        super().__post_init__()

        """Validate and convert types after initialization."""

        if isinstance(self.close_date, str):
            self.close_date = date.fromisoformat(self.close_date)

        if isinstance(self.expire_date, str):
            self.expire_date = date.fromisoformat(self.expire_date)

        if isinstance(self.option_strategy, str):
            self.option_strategy=OptionsStrategy(self.option_strategy)
        # Calculate margin required based on entry price and underlying entry
        # if self.entry_price is not None and self.underlying_entry is not None:
        #     self.margin_required = self.calculate_margin()

    @cached_property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionsType.PUT, OptionsType.PUT.value, "put"]

    @cached_property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionsType.CALL, OptionsType.CALL.value, "call"]

    @cached_property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]

    @cached_property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]

    @staticmethod
    def _as_date(value) -> date:
        """Normalize a datetime/date/str to a plain datetime.date for
        comparison against the polars (pl.Date) option chain/underlying
        price history. Mirrors FuturesPosition._as_date."""
        if isinstance(value, str):
            return date.fromisoformat(value)
        if isinstance(value, datetime):  # datetime subclasses date but carries a time component
            return value.date()
        return value

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
                         option_type: Union[OptionsType, str],
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
        if OptionsType.is_put(option_type):
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
        option_chain: pl.DataFrame,
        underlying_price_history: pl.DataFrame,
        close_reason: Optional[str] = 'expiration',
        commission: Optional[float] = 1.78,
        exercise_fee: Optional[float] = 5.0
    ) -> Optional[float]:
        """
        Calculate profit and loss (P&L) for the position, considering all relevant parameters.

        Args:
            option_chain (pl.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pl.DataFrame): DataFrame containing historical prices of the underlying asset.
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
        pnl = (self.signed_exit_price + self.signed_entry_price) * self.multiplier * self.quantity
        
        # Subtract fees
        fees = commission if commission else 0  # For expiration and early closure
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

        # logger.info('Normalizing pnl and calculating return per unit risk')

        return round(pnl, 2)

    @abstractmethod
    def calculate_intrinsic_value(self, underlying_price: float) -> float:
        """Calculate intrinsic value at expiration."""
        pass    

    @abstractmethod
    def _update_closing_data(self, option_chain: pl.DataFrame, underlying_price_history: pl.DataFrame) -> bool:
        """
        Update the instance with closing price data for the position.
        
        Args:
            option_chain (pl.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pl.DataFrame): DataFrame containing historical prices of the underlying asset.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        pass

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
            'option_type': self.option_type.value if isinstance(self.option_type, OptionsType) else self.option_type,
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
                'option_type': position.option_type.value if isinstance(position.option_type, OptionsType) else position.option_type,
                'position_side': position.position_side.value if isinstance(position.position_side, PositionSide) else position.position_side,
                'expire_date': position.expire_date,
                'entry_delta': round(position.entry_delta, 2),
                'exit_delta': round(position.exit_delta, 2) if position.exit_delta is not None else None,
                'entry_dte': position.entry_dte,
                # 'days_held': days_held,
                'underlying_entry': position.underlying_entry,
                'underlying_exit': position.underlying_exit if position.underlying_exit is not None else None,
                'strike': position.strike,
                'price': price,
                # quantity/multiplier: needed (alongside strike/expire_date/
                # option_type/position_side, already here) to mark this leg
                # to market on an arbitrary date -- see
                # Backtester.calculate_options_mtm_drawdown, which sources
                # per-leg contract terms from these 'open' transaction rows
                # (trade_results is spread-level only and has neither).
                'quantity': position.quantity,
                'multiplier': position.multiplier,
                'effect': effect,
                'bp_effect': round(bp_effect, 2) if bp_effect is not None else None,
                'fees': round(position.fees, 2) if position.fees is not None else 0
            }


@dataclass(kw_only=True)
class SingleLegOptionPosition(BaseOptionPosition):
    """Core option position. Represents a single 'open' option contract position."""
    # Required parameters (no defaults)

    option_type: Union[OptionsType, str]
    strike: float
    entry_delta: float
    entry_dte: int

    # Should go into Trade class
    # exit_date: Optional[date] = None
    # exit_price: Optional[float] = None
    # exit_delta: Optional[float] = None
    # underlying_exit: Optional[float] = None
    close_date: Optional[date] = None  # For early closure

    def __post_init__(self):
        super().__post_init__()

        """Validate and convert types after initialization."""
        if isinstance(self.option_type, str):
            self.option_type = OptionsType(self.option_type.lower())

        if isinstance(self.entry_date, str):
            self.entry_date = date.fromisoformat(self.entry_date)

        if isinstance(self.close_date, str):
            self.close_date = date.fromisoformat(self.close_date)

        if isinstance(self.expire_date, str):
            self.expire_date = date.fromisoformat(self.expire_date)

        # Calculate margin required based on entry price and underlying entry
        # NOTE: This is not needed anymore since we are using the margin required from the signal it should not compute margin for indiv spread legs
        # if self.entry_price is not None and self.underlying_entry is not None and self.margin_required is None:
        #     self.margin_required = self.calculate_position_margin()

    @cached_property
    def is_put(self) -> bool:
        """Check if position is a put option."""
        return self.option_type in [OptionsType.PUT, OptionsType.PUT.value, "put"]

    @cached_property
    def is_call(self) -> bool:
        """Check if position is a call option."""
        return self.option_type in [OptionsType.CALL, OptionsType.CALL.value, "call"]

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
            trade_signal: dict,
            option_strategy: OptionsStrategy,
            entry_date: date,
            position_side: PositionSide,
            option_type: OptionsType,
            quantity: int,
            early_close_after_dit: int = None,
            early_close_on_dte: int = None,
        ) -> Optional[SingleLegOptionPosition]:
            """
                Creates a OptionPosition object from a given trade signal.

                Args:
                    trade_signal: dict (a polars row, from iter_rows(named=True))
                    entry_date: date,
                    position_side: PositionSide,
                    option_type: OptionsType,
                    quantity: int,
                    early_close_after_dit: int,
                    early_close_on_dte: int,

            Returns:
                Optional[SingleLegOptionPosition]: Created position if valid, None otherwise
            """
            min_valid_date = date(1990, 1, 1)
            if not isinstance(entry_date, date) or entry_date <= min_valid_date:
                logger.error(f"Invalid entry date {entry_date}")
                return None

            # Validate expire_date exists and is valid
            raw_expire_date = trade_signal.get('expire_date')
            if not raw_expire_date:
                logger.error(f"expire_date is missing for trade signal on {trade_signal.get('date')}")
                return None

            expire_date = SingleLegOptionPosition._as_date(raw_expire_date)
            if expire_date <= min_valid_date:
                logger.error(f"Invalid expire date {expire_date}")
                return None

            if expire_date <= entry_date:
                logger.error(f"Expire date {expire_date} is not after entry date {entry_date}")
                return None

            # Validate strike value
            strike = trade_signal.get('strike')
            if strike is None or (isinstance(strike, float) and math.isnan(strike)):
                logger.error(f"Missing strike value in trade signal on {trade_signal.get('date')}")
                return None

            # Get entry price (already validated in signal generation)
            entry_price = trade_signal.get('midpoint_price')
            if entry_price is None:
                entry_price = trade_signal.get('spread_price')
            if entry_price is None:
                logger.error(f"Missing midpoint price for trade signal on {trade_signal.get('date')}")
                return None

            # Calculate DTE
            entry_dte = trade_signal.get('dte')
            if entry_dte is None:
                entry_dte = (expire_date - entry_date).days

            # Get margin required from signal if available
            margin_required = trade_signal.get('margin_required')
            if margin_required is None:
                logger.warning(f"Missing margin required for trade signal on {trade_signal.get('date')}")

            # Early closure
            close_date = (entry_date + timedelta(days=early_close_after_dit) if early_close_after_dit else
                          expire_date - timedelta(days=early_close_on_dte) if early_close_on_dte else
                          None)

            entry_delta = trade_signal.get('p_delta') if OptionsType.is_put(option_type) else trade_signal.get('c_delta')

            # Create the position
            position = SingleLegOptionPosition(
                option_strategy=option_strategy,
                quantity=quantity,
                option_type=option_type,
                position_side=position_side,
                strike=strike,
                entry_date=entry_date,
                expire_date=expire_date,
                entry_price=round(abs(entry_price), 2),
                entry_delta=round(entry_delta, 2) if entry_delta is not None else None,
                entry_dte=entry_dte,
                underlying_entry=trade_signal.get('underlying_last'),
                margin_required=round(margin_required, 2) if margin_required is not None else None,
                close_date=close_date,
            )

            logger.debug(f'Constructing position from symbol')
            logger.debug(f'{option_strategy} | Premium: {position.premium}')

            return position

       

    def _update_closing_data(self, option_chain: pl.DataFrame,
        underlying_price_history: pl.DataFrame,
        force: bool = False) -> bool:
        """
        Update the instance with closing price data for the position.

        Args:
            option_chain (pl.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pl.DataFrame): DataFrame containing historical prices of the underlying asset.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        # Handle single-leg positions (existing logic)
        return self._update_single_leg_closing_data(option_chain, underlying_price_history, force)

    def _update_single_leg_closing_data(self,
        option_chain: pl.DataFrame,
        underlying_price_history: pl.DataFrame,
        force: bool = False
    ) -> bool:
        """Update closing data for single-leg positions (existing logic)."""

        # If no close_date, this is an expiration
        if not self.close_date:
            expire_key = self._as_date(self.expire_date)

            # 1) Try underlying history close
            match = underlying_price_history.filter(pl.col('date') == expire_key)
            if match.height > 0:
                underlying_close = match['close'][0]
            else:
                logger.warning(f"No underlying close for {self.expire_date}; falling back to option chain 'underlying_last'")
                # 2) Try option chain rows on the same calendar date (and expiry)
                oc_same_day = option_chain.filter(
                    (pl.col('date') == expire_key) & (pl.col('expire_date') == expire_key)
                ).sort('date')

                if oc_same_day.height > 0 and 'underlying_last' in oc_same_day.columns:
                    underlying_close = float(oc_same_day['underlying_last'][0])
                else:
                    # 3) Try nearest prior chain row for this expiry
                    oc_exp = option_chain.filter(pl.col('expire_date') == expire_key).sort('date')
                    prior = oc_exp.filter(pl.col('date') <= expire_key).tail(1)
                    if prior.height > 0 and 'underlying_last' in prior.columns:
                        underlying_close = float(prior['underlying_last'][0])
                    else:
                        logger.error(f"No valid (expiration) closing prices found for strike {self.strike} and expire date {self.expire_date}")
                        return False

            logger.info(f'Expiration - underlying close: {underlying_close}')

            # Calculate intrinsic value at expiration
            exit_price = self.calculate_intrinsic_value(underlying_close)
            logger.info(f'Expiration {self.expire_date} - strike {self.strike} - exit price: {exit_price}')

            # Get delta value at expiration (best-effort from chain on the day)
            delta_col = "p_delta" if self.is_put else 'c_delta'
            filtered_df = option_chain.filter(
                (pl.col('date') == expire_key) &
                (pl.col('expire_date') == expire_key) &
                (pl.col('strike') == self.strike)
            )

            logger.debug(f'filtered_df: {filtered_df}')

            exit_delta = round(filtered_df[delta_col][0], 2) if filtered_df.height > 0 else None

            if exit_price is not None:
                self.underlying_exit = underlying_close
                self.exit_price = exit_price
                self.exit_delta = exit_delta
                return True  # Successfully updated

            return False  # Failed to update

        # Early close - get data from close_date forward (up to 5 days)
        close_dt = self._as_date(self.close_date)
        exp_dt = self._as_date(self.expire_date)

        # Force close, e.g. if need to close all positions at end of period
        if force:
            # Look both forward and backward when force closing to handle wide spreads
            date_range = [close_dt + timedelta(days=d) for d in range(-2, 3)]
            filtered_df = option_chain.filter(
                pl.col('date').is_in(date_range) &
                (pl.col('expire_date') == exp_dt) &
                (pl.col('strike') == self.strike)
            ).sort('date')
        # Otherwise, just early close
        else:
            date_range = [close_dt + timedelta(days=d) for d in range(0, 3)]
            filtered_df = option_chain.filter(
                pl.col('date').is_in(date_range) &
                (pl.col('expire_date') == exp_dt) &
                (pl.col('strike') == self.strike)
            )

        if filtered_df.height == 0:
            logger.warning(f"No valid prices found within 2 days of close date {self.close_date}")
            around = option_chain.filter(
                (pl.col('date') >= close_dt - timedelta(days=5)) &
                (pl.col('date') <= close_dt + timedelta(days=5))
            )
            nearby_dates = sorted(around['date'].unique().to_list())
            logger.debug(f"Nearby chain rows around {close_dt} (count={around.height}): {nearby_dates[:5]}")
            logger.warning(f"No valid prices found in date range around close date {self.close_date} "
                   f"(expire={self.expire_date}, strike={self.strike})")
            return False

        bid_col = "p_bid" if self.is_put else "c_bid"
        ask_col = "p_ask" if self.is_put else "c_ask"
        delta_col = "p_delta" if self.is_put else 'c_delta'

        # Try each date until we find valid prices
        wide_spread_dates = []
        for row in filtered_df.iter_rows(named=True):
            row_date = row['date']
            bid = row[bid_col]
            ask = row[ask_col]
            underlying_match = underlying_price_history.filter(pl.col('date') == row_date)
            underlying_close = underlying_match['close'][0] if underlying_match.height > 0 else None
            # Use last close in options data if available
            if underlying_close is None:
                if row.get('underlying_last') is not None:
                    underlying_close = row['underlying_last']
                else:
                    logger.error(f'Cannot get closing data because no underlying available for {self.option_strategy}')
                    return False
            exit_delta = round(row[delta_col], 2) if row[delta_col] is not None else None
            logger.debug(f'Calculating midpoint for {bid}-{ask}')
            mid_price = PriceUtils.calculate_midpoint_price(bid, ask)
            if mid_price is not None and not (isinstance(mid_price, float) and math.isnan(mid_price)):
                # Update instance variables only if mid_price is valid
                self.underlying_exit = underlying_close
                self.exit_price = mid_price
                self.exit_delta = exit_delta
                self.close_date = row_date   # update since actual close date may have changed (already a plain date from the polars row)
                return True  # Successfully updated
            else:
                # Track dates with wide spreads for potential fallback (only if not null)
                if bid is not None and ask is not None:
                    spread_pct = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
                    wide_spread_dates.append((row_date, bid, ask, spread_pct, exit_delta))

        # If all spreads are too wide, try using the closest date with the narrowest spread
        if wide_spread_dates:
            logger.warning(f"All spreads too wide in date range, attempting fallback for strike {self.strike}")
            # Sort by date proximity to close_date, then by spread width
            wide_spread_dates.sort(key=lambda x: (abs((x[0] - close_dt).days), x[3]))

            # Use the closest date with the narrowest spread, even if it's wide
            fallback_date, fallback_bid, fallback_ask, fallback_spread, fallback_delta = wide_spread_dates[0]
            logger.warning(f"Using fallback pricing: date={fallback_date}, bid={fallback_bid}, ask={fallback_ask}, spread={fallback_spread:.2f}%")

            # Check if fallback prices are null
            if fallback_bid is None or fallback_ask is None:
                logger.error(f'Fallback bid/ask are missing for strike {self.strike} on date {fallback_date}. Cannot close position.')
                return False

            # Get underlying price for fallback date
            fallback_underlying_match = underlying_price_history.filter(pl.col('date') == fallback_date)
            if fallback_underlying_match.height == 0:
                logger.error(f'Cannot get underlying price for fallback date {fallback_date}')
                return False
            fallback_underlying = fallback_underlying_match['close'][0]

            # Use midpoint even if spread is wide (as last resort)
            fallback_mid_price = (fallback_bid + fallback_ask) / 2

            self.underlying_exit = fallback_underlying
            self.exit_price = fallback_mid_price
            self.exit_delta = fallback_delta
            self.close_date = fallback_date  # already a plain date from the polars row
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
            'option_type': self.option_type.value if isinstance(self.option_type, OptionsType) else self.option_type,
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
            option_chain: pl.DataFrame, 
            underlying_price_history: pl.DataFrame,
            force: bool = False
    ) -> Optional[Tuple[OptionTradeResult, Dict, float]]:
        """
        Close this single-leg position and calculate results.
        
        Args:
            option_chain: pl.DataFrame, 
            underlying_price_history: pl.DataFrame,
            force: bool = False (if closing at end of backtest)
        
        Returns:
            Optional[Tuple[OptionTradeResult, Dict, float]]: Tuple of (trade_result_dict, transaction_dict, bp_effect) if successful, None if closing data is unavailable.
        """
        logger.info(f"Closing Trade #{self.trade_id}|Trans #{self.transaction_id}|{self.option_strategy}|{self.option_type}|{self.position_side}")
        bp_effect = 0
        close_reason = None
        min_valid_date = date(1990, 1, 1)  # Arbitrary date well after 1970

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
        if not isinstance(close_date, date) or close_date <= min_valid_date:
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
        logger.debug(f'Calculated raw pnl: {pnl}')

        # Deduct fees from buying power
        logger.debug(f'Deducting fees from BP: {bp_effect}')
        bp_effect -= self.fees if self.fees is not None else 0 # Deduct fees from buying power 
        logger.debug(f'Deducted fees from BP: {bp_effect}')

        # Calculate days held
        days_held = (close_date - self.entry_date).days
        if days_held < 0:
            logger.error(f"Calculated negative days held ({days_held}) - skipping trade")
            return None, None, None
    
        logger.debug(f'ready to return result. PnL: {pnl}')

        # Create transaction 
        transaction = self.create_transaction(self, close_date, 'close', bp_effect)
        
        # Calculate ROI as % of margin (capital at risk)
        roi = None
        strat_effective_risk = None
        if self.option_strategy in [
            OptionsStrategy.SHORT_CALL,
            OptionsStrategy.SHORT_PUT,
            OptionsStrategy.LONG_CALL,
            OptionsStrategy.LONG_PUT,
        ]:
            strat_pnl = pnl
            if self.is_long:
                strat_effective_risk = abs(self.entry_price * self.quantity * self.multiplier)
            else:  # short
                if self.margin_required is not None:
                    strat_effective_risk = self.margin_required
                else:
                    logger.error(
                        'Cannot calculate pnl for strategy %s because required margin is unknown',
                        self.option_strategy.value
                    )
                    strat_effective_risk = None

            roi = strat_pnl / strat_effective_risk * 100 if strat_effective_risk is not None else None

        capital_used = round(self.margin_required, 2) if self.margin_required is not None else round(abs(self.entry_price) * self.quantity * 100, 2)

        # Prepare trade result
        trade_result = OptionTradeResult(
            trade_id=self.trade_id,
            option_strategy=self.option_strategy.value if isinstance(self.option_strategy, OptionsStrategy) else self.option_strategy,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=close_date,
            days_held=days_held,
            close_reason=close_reason,
            premium=round(self.premium, 2),
            fees=round(self.fees, 2),
            pnl=round(pnl, 4),
            roi=roi,
            bp=None,
            capital_used=capital_used,
        )
        return trade_result, transaction, bp_effect

@dataclass(kw_only=True)
class MultiLegOptionPosition(BaseOptionPosition):
    """Class representing a multi-leg option strategy.""" 

    spread_type: OptionSpreadType
    legs: List[SingleLegOptionPosition] 
    leg_ratios: Dict[int, float] = None  # Maps leg index to ratio
    # spread_price: Optional[float] = None  # property will calculate the net price
    spread_width: Optional[float] = None
    max_loss: Optional[float] = None
    # net_price: Optional[float] = None
    expire_date: Optional[date] = None # Common expiration date for the spread
    entry_dte: int = 0  # Common DTE for the spread
    underlying_entry: Optional[float] = None  # Common underlying entry price for the spread
    option_type: Union[OptionsType, str] = None  # Will be derived from legs
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
        self.expire_date = self.legs[0].expire_date if is_same else None

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

    def _update_closing_data(self, option_chain: pl.DataFrame, underlying_price_history: pl.DataFrame) -> bool:
        """
        Update the instance with closing price data for the position.
        
        Args:
            option_chain (pl.DataFrame): DataFrame containing the full option chain data.
            underlying_price_history (pl.DataFrame): DataFrame containing historical prices of the underlying asset.

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
        return self.option_type == OptionsType.PUT and underlying_price <= self.strike or self.option_type == OptionsType.CALL and underlying_price >= self.strike

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
            
            # Validate strikes and ratios TODO: add some check for broken-wing flies
            # strikes = sorted([leg.strike for leg in self.legs])
            # if not (strikes[1] - strikes[0] == strikes[2] - strikes[1]):
            #     raise ValueError("Butterfly spread must have equal wing widths")
            
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

    def calculate_pnl(self, option_chain:pl.DataFrame, underlying_price_history: pl.DataFrame, close_reason: Optional[str]=None,  commission: Optional[float]=None, exercise_fee: Optional[float]=None) -> Optional[float]:
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
    
    def close(self,
            option_chain: pl.DataFrame,
            underlying_price_history: pl.DataFrame,
            force: bool = True) -> Optional[Tuple[Dict, List[Dict], float]]:
        """
        Close this multi-leg position and calculate results for each leg and the spread.

        Args:
            option_chain: pl.DataFrame,
            underlying_price_history: pl.DataFrame,
        
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

        roi = spread_pnl / self.margin_required * 100

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
        days_held = (close_date - self.entry_date).days
        if days_held < 0:
            logger.warning(f"Calculated negative days held ({days_held}) for spread {self.trade_id}")
            days_held = 0 # Or handle as an error if appropriate

        # Get close reason from first leg (should be same for all legs)
        close_reason = all_trade_results[0].close_reason

        # Construct the aggregated trade result for the spread
        aggregated_trade_result = OptionTradeResult(
            trade_id=self.trade_id,
            option_strategy=self.option_strategy.value if isinstance(self.option_strategy, OptionsStrategy) else self.option_strategy,
            quantity=self.quantity,
            opened=self.entry_date,
            closed=close_date,
            days_held=days_held,
            close_reason=close_reason,
            premium=round(self.premium, 2),
            fees=all_fees,
            pnl=spread_pnl,
            roi=roi,
            bp=None,
            capital_used=round(self.margin_required, 2),
        )
        
        return aggregated_trade_result, all_transactions, total_bp_effect
    
    @staticmethod
    def construct_from_signal(
            trade_signal: dict,
            config: MultiLegOptionStrategyConfig,
            entry_date: date,
        ) -> Optional[MultiLegOptionPosition]:
            """
                Creates a MultiLegOptionPosition object from a given trade signal.

                Args:
                    trade_signal: dict (a polars row, from iter_rows(named=True))
                    config: MultiLegOptionStrategyConfig
                    entry_date: date

                Returns:
                    Optional[MultiLegOptionPosition]: Created position if valid, None otherwise
            """
            # Validate entry date
            min_valid_date = date(1990, 1, 1)
            if not isinstance(entry_date, date) or entry_date <= min_valid_date:
                logger.error(f"Invalid entry date {entry_date}")
                return None

            # Validate expire_date exists and is valid
            raw_expire_date = trade_signal.get('expire_date')
            if not raw_expire_date:
                logger.error(f"expire_date is missing for trade signal on {trade_signal.get('date')}")
                return None

            expire_date = MultiLegOptionPosition._as_date(raw_expire_date)
            if expire_date <= min_valid_date:
                logger.error(f"Invalid expire date {expire_date}")
                return None

            if expire_date <= entry_date:
                logger.error(f"Expire date {expire_date} is not after entry date {entry_date}")
                return None

            # Early closure
            close_date = (entry_date + timedelta(days=config.early_close_after_dit) if config.early_close_after_dit else
                          expire_date - timedelta(days=config.early_close_on_dte) if config.early_close_on_dte else
                          None)

            # Retrieve and construct legs for the spread
            n_legs = len(config.legs)
            # Iron condor signals use put_leg1_/put_leg2_/call_leg1_/call_leg2_
            # column prefixes (see _pair_iron_condor_spread_legs), not the
            # generic leg1_/leg2_/... prefixes every other spread type uses.
            if config.spread_type == OptionSpreadType.IRON_CONDOR:
                leg_prefixes = ['put_leg1_', 'put_leg2_', 'call_leg1_', 'call_leg2_']
                # Iron condor pairing never coalesces underlying_last into a
                # shared column the way vertical spreads do -- it's duplicated
                # per leg instead (same value on every leg), so read it off
                # leg 1's copy.
                underlying_last = trade_signal.get(leg_prefixes[0] + 'underlying_last')
            else:
                leg_prefixes = [f"leg{i+1}_" for i in range(n_legs)]
                underlying_last = trade_signal.get('underlying_last')
            multiplier = getattr(config, 'multiplier', 100)
            legs = []
            for i in range(n_legs):
                # Get keys
                leg_n = leg_prefixes[i]
                quantity = config.quantity * config.leg_ratios[i]  # effective quantity
                option_type = config.legs[i].option_type
                position_side = config.legs[i].position_side

                # Strike
                strike = trade_signal.get(leg_n + 'strike')
                if strike is None or (isinstance(strike, float) and math.isnan(strike)):
                    logger.error(f"Missing strike value/s in trade signal on {trade_signal.get('date')}")
                    return None

                # Delta
                type_prefix = "p_" if OptionsType.is_put(option_type) else "c_"
                entry_delta = trade_signal.get(leg_n + type_prefix + 'delta')
                if entry_delta is None or (isinstance(entry_delta, float) and math.isnan(entry_delta)):
                    logger.error(f"Missing delta value/s in trade signal on {trade_signal.get('date')}")
                    return None

                # DTE
                entry_dte = trade_signal.get(leg_n + 'dte')
                if entry_dte is None:
                    logger.error(f"Missing dte value/s in trade signal on {trade_signal.get('date')}")
                    return None

                # Entry price (midpoint_price)
                entry_price = trade_signal.get(leg_n + 'midpoint_price')
                if entry_price is None or (isinstance(entry_price, float) and math.isnan(entry_price)):
                    logger.error(f"Missing midpoint price value/s in trade signal on {trade_signal.get('date')}")
                    return None

                position = SingleLegOptionPosition(
                    option_strategy=config.option_strategy,
                    quantity=quantity, # Individual legs should always have a quantity of 1, as the overall spread quantity is handled by MultiLegOptionPosition
                    multiplier=multiplier,
                    option_type=option_type,
                    position_side=position_side,
                    strike=strike,
                    entry_date=entry_date,
                    expire_date=expire_date,
                    entry_price=abs(entry_price),
                    entry_delta=entry_delta,
                    entry_dte=entry_dte,
                    underlying_entry=underlying_last,
                    close_date=close_date,
                )

                legs.append(position)

            # Get entry price (already validated in signal generation)
            spread_price = trade_signal.get('spread_price')
            if spread_price is None:
                logger.error(f"Missing spread price for trade signal on {trade_signal.get('date')}")
                return None
            entry_price = round(spread_price, 2)

            position_side = PositionSide('short') if entry_price >= 0 else PositionSide('long')

            entry_dte = trade_signal.get(leg_prefixes[0] + 'dte')

            # Get margin required from signal if available, otherwise use config
            margin_required = trade_signal.get('margin_required')
            if margin_required is None:
                logger.warning(f"Missing margin required for trade signal on {trade_signal.get('date')}")

            # Create the position
            position = MultiLegOptionPosition(
                quantity=config.quantity,
                multiplier=multiplier,
                option_type=config.legs[0].option_type,
                option_strategy=config.option_strategy,
                spread_type=config.spread_type,
                legs=legs,
                leg_ratios=config.leg_ratios,
                position_side=position_side,
                entry_date=entry_date,
                expire_date=expire_date,
                entry_price=abs(entry_price),  # Store positive price, use signed accessors
                entry_dte=entry_dte,
                underlying_entry=underlying_last,
                spread_width=trade_signal.get('spread_width'),
                margin_required=margin_required,
                close_date=close_date,
            )

            logger.debug(f'Constructing potential spread position from symbol')
            logger.debug(f'{config.option_strategy} | Price: {position.spread_price} | Premium: {position.premium}')

            return position

    def _update_multileg_closing_data(self, option_chain: pl.DataFrame, underlying_price_history: pl.DataFrame) -> bool:
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
        

@dataclass(kw_only=True)
class FuturesPosition(BasePosition):
    """Class representing a futures position."""
    futures_type: str
    futures_strategy: FuturesStrategy

    # No separate "underlying" price for futures (unlike options, where the
    # underlying is a distinct cash index) — entry_price/exit_price are the
    # futures contract's own open/close prices.
    exit_price: Optional[float] = None

    roll_date: date # The date when this futures contract is expected to be rolled
    close_reason: Optional[str] = None # 'roll', 'early closure', etc.
    initial_margin: float # Initial margin for one contract
    close_date: Optional[date] = None # Set by TradeManager for early closure (e.g. VIX trigger)
    fill_price: str = 'close' # 'close' or 'mid' ((high+low)/2) — see FuturesStrategyConfig.fill_price

    @property
    def expire_date(self) -> date:
        """Futures have no option-style expiration — the roll_date is the
        equivalent trigger TradeManager uses to close/roll the position."""
        return self.roll_date

    def __post_init__(self):
        super().__post_init__()
        if isinstance(self.futures_strategy, str):
            self.futures_strategy = FuturesStrategy(self.futures_strategy)

        if isinstance(self.roll_date, str):
            self.roll_date = date.fromisoformat(self.roll_date)

        # Contract multiplier for this symbol, from instruments.py's spec
        # registry (no per-instrument enum -- see enums.py's FuturesStrategy
        # comment).
        self.mult: float = get_spec(self.futures_type)['multiplier']

        # Calculate initial margin if not provided
        if self.margin_required is None: # We now have initial_margin as a required attribute
             self.margin_required = self.calculate_margin()

    def calculate_pnl(self, underlying_price_history: pl.DataFrame, close_reason: Optional[str] = 'roll', commission: Optional[float] = 1.0) -> Optional[float]:
        """
        Calculate P&L using the same signed cash-flow convention as options:
          signed_entry_price = -entry for long (debit), +entry for short (credit)
          signed_exit_price  = +exit  for long (credit), -exit  for short (debit)
          pnl = (signed_exit + signed_entry) * mult * quantity - fees

        commission is per contract per side; doubled here to cover the round trip.
        """
        if self.exit_price is None or self.entry_price is None:
            logger.warning('Entry or exit price not set correctly for futures pnl calculation')
            return None

        pnl = (self.signed_exit_price + self.signed_entry_price) * self.mult * self.quantity

        fees = commission * 2 * self.quantity
        self.fees = round(fees, 2)
        pnl -= self.fees
        self.close_reason = close_reason

        logger.info(f"Calculated pnl for {self.futures_type} futures: {pnl}")
        return round(pnl, 2)

    def calculate_margin(self, leverage: float = 1.0) -> float:
        """
        Calculate margin requirement for the futures position.
        Uses the provided initial_margin attribute.
        """
        return round(self.initial_margin * self.quantity / leverage, 2)

    def _update_closing_data(self, underlying_price_history: pl.DataFrame, close_date: date) -> bool:
        """
        Update the instance with closing price data for the futures position.

        Args:
            underlying_price_history (pl.DataFrame): DataFrame containing historical prices of the underlying asset.
            close_date (date): The date at which the position is being closed.

        Returns:
            bool: True if closing data was successfully updated, False otherwise.
        """
        close_date = self._as_date(close_date)
        match = underlying_price_history.filter(pl.col('ts_event') == close_date)
        if match.height == 0:
            logger.error(f"No underlying closing price available for {close_date} for futures position.")
            return False

        if self.fill_price == 'mid':
            self.exit_price = (match['high'][0] + match['low'][0]) / 2
        else:
            self.exit_price = match['close'][0]

        return True

    @staticmethod
    def _as_date(value) -> date:
        """Normalize a datetime/date/str to a plain datetime.date for
        comparison against the polars (pl.Date) underlying price history."""
        if isinstance(value, str):
            return date.fromisoformat(value)
        if isinstance(value, datetime):  # datetime subclasses date but carries a time component
            return value.date()
        return value

    def close(self,
            option_chain: pl.DataFrame, # Not used for futures, but kept for method signature compatibility
            underlying_price_history: pl.DataFrame,
            force: bool = False,
            close_reason: Optional[str] = None # Added close_reason parameter
    ) -> Optional[Tuple[BaseTradeResult, Dict, float]]:
        """
        Close this futures position and calculate results.

        Args:
            option_chain: pl.DataFrame (not used for futures)
            underlying_price_history: pl.DataFrame,
            force: bool = False (if closing at end of backtest)

        Returns:
            Optional[Tuple[BaseTradeResult, Dict, float]]: Tuple of (trade_result, transaction_dict, bp_effect) if successful, None otherwise.
        """
        logger.info(f"Closing Trade #{self.trade_id}|Trans #{self.transaction_id}|{self.futures_type}|{self.position_side}")
        bp_effect = 0

        # Determine the actual close date
        effective_close_date = self.roll_date if self.roll_date and not force else underlying_price_history['ts_event'].max() # Use max date if forced

        if not self._update_closing_data(underlying_price_history, effective_close_date):
            logger.error(f"Skipping futures trade due to missing close data for {self.futures_type} on {effective_close_date}")
            return None, None, None

        # For long futures, when closing, the initial margin is released.
        # For short futures, the initial margin is released.
        bp_effect += self.margin_required # Release margin
        
        # Pass commission to pnl calculation. If None, it will use get_spec(futures_type)['commission']
        pnl = self.calculate_pnl(underlying_price_history=underlying_price_history, close_reason=close_reason, commission=get_spec(self.futures_type)['commission'])
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
            'futures_type': self.futures_type,
            'position_side': self.position_side.value,
            'open': self.entry_price,
            'close': self.exit_price,
            'quantity': self.quantity,
            'mult': self.mult,
            'pnl': pnl,
            'fees': self.fees,
            'bp_effect': round(bp_effect, 2)
        }

        # Prepare trade result
        trade_result = FuturesTradeResult(
            trade_id=self.trade_id,
            futures_strategy=self.futures_strategy.value,
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
            trade_signal: dict,
            futures_strategy: FuturesStrategy,
            entry_date: date,
            position_side: PositionSide,
            futures_type: str,
            quantity: int,
            # initial_margin: float, # Removed as it's now an attribute of FuturesPosition, derived from signal
            roll_date: date,
            fill_price: str = 'close',
        ) -> Optional['FuturesPosition']:
            """
            Creates a FuturesPosition object from a given trade signal.

            Args:
                trade_signal: dict (a polars row, from iter_rows(named=True)),
                              containing the underlying 'close'/'high'/'low' prices
                futures_strategy: FuturesStrategy (e.g., LONG_FUTURES)
                entry_date: date
                position_side: PositionSide (e.g., LONG)
                futures_type: str symbol (e.g., 'MES') -- looked up via
                              instruments.get_spec, not an enum member
                quantity: int (number of contracts)
                initial_margin: float (initial margin requirement for one contract)
                roll_date: date (next roll date)
                fill_price: 'close' or 'mid' ((high+low)/2) — see FuturesStrategyConfig.fill_price

            Returns:
                Optional[FuturesPosition]: Created position if valid, None otherwise
            """
            min_valid_date = date(1990, 1, 1)
            # date covers both datetime.date and datetime.datetime (a date subclass)
            if not isinstance(entry_date, date) or entry_date <= min_valid_date:
                logger.error(f"Invalid entry date {entry_date}")
                return None

            close_price = trade_signal.get('close')
            if close_price is None or (isinstance(close_price, float) and math.isnan(close_price)):
                logger.error(f"Missing close price in trade signal on {trade_signal.get('ts_event')}")
                return None

            if fill_price == 'mid':
                entry_price = (trade_signal['high'] + trade_signal['low']) / 2
            else:
                entry_price = close_price

            position = FuturesPosition(
                trade_id=None, # Will be set by TradeManager
                transaction_id=None, # Will be set by TradeManager
                quantity=quantity,
                position_side=position_side,
                entry_date=entry_date,
                entry_price=entry_price,
                futures_type=futures_type,
                futures_strategy=futures_strategy,
                fill_price=fill_price,
                initial_margin=get_spec(futures_type)['initial_margin'],
                roll_date=roll_date,
                margin_required=None, # Will be calculated in post_init
            )
            # Calculate margin after all attributes are set (depends on quantity and initial_margin)
            # This line is now redundant as margin_required is calculated in __post_init__
            # position.margin_required = position.calculate_margin() 

            logger.debug(f'Constructing futures position from signal: {futures_type} | Entry: {entry_price} | Margin: {position.margin_required}')

            return position

    @staticmethod
    def create_transaction(position: 'FuturesPosition', date: date, type: str, bp_effect: float = None) -> dict:
        """
        Create a transaction dictionary for a futures position open. Mirrors
        the shape of the inline 'close' transaction dict built in close()
        above, since both feed the same transactions DataFrame.
        """
        if type.lower() != 'open':
            logger.error(f'FuturesPosition.create_transaction only handles open transactions, got {type!r}')
            raise ValueError("FuturesPosition.create_transaction only handles 'open' transactions")

        return {
            'transaction_id': position.transaction_id,
            'trade_id': position.trade_id,
            'date': date,
            'type': 'open',
            'instrument_type': 'futures',
            'futures_type': position.futures_type,
            'position_side': position.position_side.value,
            'open': position.entry_price,
            'close': None,
            'quantity': position.quantity,
            'mult': position.mult,
            'pnl': None,
            'fees': position.fees,
            'bp_effect': round(bp_effect, 2) if bp_effect is not None else '',
        }
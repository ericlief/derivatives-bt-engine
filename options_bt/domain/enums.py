from __future__ import annotations
from enum import Enum
from typing import TypedDict, Optional, Union, NamedTuple
import pandas as pd

class OptionType(str, Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Union[OptionType, str, pd.Series]) -> bool:
        """
        Check if the value represents a PUT option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.PUT
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @staticmethod
    def is_call(value: Union[OptionType, str, pd.Series]) -> bool:
        """
        Check if the value represents a CALL option.
        
        Args:
            value: Can be OptionType enum, string, or pandas Series with 'option_type' column
            
        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.CALL
        elif isinstance(value, pd.Series) and 'option_type' in value:
            return value.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.CALL, OptionType.CALL.value, "call"]
        elif isinstance(value, str):
            return value.lower() == "call"
        return False

class PositionSide(str, Enum):
    """Position side enumeration."""
    LONG = "long"  # Buying options
    SHORT = "short"  # Selling/writing options

    @staticmethod
    def is_long(value: Union[PositionSide, str, pd.Series]) -> bool:
        """
        Check if the value represents a LONG position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.LONG
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif isinstance(value, str):
            return value.lower() == "long"
        return False

    @staticmethod
    def is_short(value: Union[PositionSide, str, pd.Series]) -> bool:
        """
        Check if the value represents a SHORT position.
        
        Args:
            value: Can be PositionSide enum, string, or pandas Series with 'position_side' column
            
        Returns:
            bool: True if SHORT, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.SHORT
        elif isinstance(value, pd.Series) and 'position_side' in value:
            return value.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.SHORT, PositionSide.SHORT.value, "short"]
        elif isinstance(value, str):
            return value.lower() == "short"
        return False

class OptionSpreadType(str, Enum):
    """Spread type enumeration."""
    NONE = "none"      # Single leg position
    VERTICAL = "vertical"  # Vertical spread (same expiration, different strikes)
    CALENDAR = "calendar"  # Calendar spread (same strike, different expirations)
    DIAGONAL = "diagonal"  # Diagonal spread (different strikes, different expirations)
    IRON_CONDOR = "iron_condor"  # Iron condor (4 legs)
    BUTTERFLY = "butterfly"  # Butterfly spread (3 legs)

    @staticmethod
    def is_spread_type(value: Union[OptionSpreadType, str, pd.Series, pd.DataFrame], spread_type: OptionSpreadType) -> bool:
        """
        Check if a value matches the given spread type.
        
        Args:
            value: Can be SpreadType enum, string, pandas Series, or DataFrame with 'spread_type' column
            spread_type: SpreadType to check against
            
        Returns:
            bool: True if types match, False otherwise
        """
        if isinstance(value, OptionSpreadType):
            return value == spread_type
        elif isinstance(value, str):
            return value.lower() == spread_type.value
        elif isinstance(value, pd.DataFrame) and 'spread_type' in value.columns:
            return value.iloc[0]['spread_type'].lower() == spread_type.value
        elif isinstance(value, pd.Series) and 'spread_type' in value:
            return value['spread_type'].lower() == spread_type.value
        return False

class BaseStrategy(str, Enum):
    """Base class for strategy types."""
    pass

class OptionStrategy(BaseStrategy):
    """Option strategy type enumeration."""
    SHORT_PUT = "short put"
    LONG_PUT = "long put"
    SHORT_CALL = "short call"
    LONG_CALL = "long call"
    BULL_PUT_CREDIT_SPREAD = "bull put credit spread"
    BEAR_PUT_DEBIT_SPREAD = "bear put debit spread"
    BULL_CALL_DEBIT_SPREAD = "bull call debit spread"
    BEAR_CALL_CREDIT_SPREAD = "bear call credit spread"
    CUSTOM_STRATEGY = "custom strategy"
    IRON_CONDOR = "iron condor"
    BUTTERFLY = "butterfly"
    STRADDLE = "straddle"
    STRANGLE = "strangle"

class FuturesType(Enum):
    """Futures contract type enumeration, with associated properties."""
    # Value format: (mult, initial_margin, commission)
    # Multipliers are fixed CME contract specs (high confidence). Margins
    # are CME SPAN maintenance margin and move with volatility/exchange
    # resets — MES/ES values were given; the rest below are rough estimates
    # only, scaled from typical CME margin levels for these products. Verify
    # against current CME/broker figures before relying on them for sizing.
    # Commission is per contract, per side (i.e. half of round trip) and
    # reuses the existing per-contract tiers (standard vs micro) for the new
    # symbols below; calculate_pnl() doubles it for the full round trip.
    MES = (5, 3406.84, 1.24) # Micro E-mini S&P 500
    ES = (50, 34068.38, 1.70) # E-mini S&P 500
    MNQ = (2, 2900.0, 1.24) # Micro E-mini Nasdaq-100 -- margin estimated, verify
    NQ = (20, 29000.0, 1.70) # E-mini Nasdaq-100 -- margin estimated, verify
    MYM = (0.5, 1100.0, 1.24) # Micro E-mini Dow -- margin estimated, verify
    YM = (5, 11000.0, 1.70) # E-mini Dow -- margin estimated, verify
    M2K = (5, 900.0, 1.24) # Micro E-mini Russell 2000 -- margin estimated, verify
    RTY = (50, 9000.0, 1.70) # E-mini Russell 2000 -- margin estimated, verify
    ZN = (1000, 2156.25, 1.67) # 10-Year T-Note (CBOT) -- margin estimated, verify
    ZT = (2000, 1380.75, 3.04) # 2-Year T-Note (CBOT) -- margin estimated, verify
    SI = (5000, 68233.0, 1.70) # Silver (COMEX) -- margin estimated, verify
    CL = (1000, 18750.0, 1.70) # Crude Oil (NYMEX) -- margin estimated, verify
    ZL = (600, 3000.0, 1.70) # Soybean Oil (CBOT) -- margin estimated, verify
    ZC = (50, 1500.0, 1.70) # Corn (CBOT) -- margin estimated, verify
    ZS = (50, 3000.0, 1.70) # Soybeans (CBOT) -- margin estimated, verify
    ZW = (50, 2500.0, 1.70) # Wheat (CBOT) -- margin estimated, verify
    NIY = (500, 10000.0, 1.70) # Nikkei 225 Yen-denominated (CME) -- margin estimated, verify.
                               # NOTE: contract is JPY-denominated (Y500/point); this
                               # codebase's PnL math has no FX conversion, so PnL will
                               # come out in JPY, not USD, unless that's added separately.
    # Python identifiers can't start with a digit, so the FX futures whose
    # actual exchange ticker starts with one (6J, 6L, 6M) are named with a
    # leading underscore here -- use FuturesType.from_symbol('6J') to look
    # them up by their real ticker rather than FuturesType['6J'] (invalid).
    _6J = (12_500_000, 3000.0, 1.70) # Japanese Yen (CME) -- margin estimated, verify
    _6L = (100_000, 3500.0, 1.70) # Brazilian Real (CME) -- margin estimated, verify
    _6M = (500_000, 2000.0, 1.70) # Mexican Peso (CME) -- margin estimated, verify
    # SOX = (?, ?, 1.70) # Not added: genuinely unsure of this contract's point
    # value/margin (possibly a Small Exchange product, not a standard CME index
    # future I have reliable specs for) -- ask before adding rather than guess.

    def __new__(cls, mult: float, initial_margin: float, commission: float):
        obj = object.__new__(cls)
        # _value_ must be unique per member or Python's Enum silently turns
        # same-valued members into aliases of each other (e.g. ES/RTY/ZC/ZS/ZW
        # all have mult=50 -- using just `mult` here collapsed them into one).
        # The full tuple is unique across every member defined below.
        obj._value_ = (mult, initial_margin, commission)
        obj.mult = mult
        obj.initial_margin = initial_margin
        obj.commission = commission
        return obj

    @classmethod
    def from_symbol(cls, symbol: str) -> 'FuturesType':
        """Look up by the actual exchange ticker (e.g. '6J'), handling the
        leading-underscore workaround for tickers Python can't name directly."""
        name = symbol.upper()
        if name[0].isdigit():
            name = f'_{name}'
        return cls[name]

    @property
    def multiplier(self) -> float:
        return self.mult

    @property
    def margin_required(self) -> float:
        return self.initial_margin
    
    @property
    def transaction_commission(self) -> float:
        return self.commission

class FuturesStrategy(BaseStrategy):
    """Futures strategy type enumeration."""
    LONG_FUTURES = "long_futures"  
    SHORT_FUTURES = "short_futures"


class TradeSelectionMethod(str, Enum):
    """Trade selection method enumeration."""
    PREMIUM_FIRST = "premium first"
    DELTA_FIRST = "delta first"
    WEIGHTED = "weighted"

from __future__ import annotations
from enum import Enum
from typing import TypedDict, Optional, Union, NamedTuple

from options_bt.domain.instruments import INSTRUMENTS, BACKTEST_ONLY_SPECS

class OptionType(str, Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"

    @staticmethod
    def is_put(value: Union[OptionType, str]) -> bool:
        """
        Check if the value represents a PUT option.

        Args:
            value: Can be OptionType enum or string

        Returns:
            bool: True if PUT, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.PUT
        elif hasattr(value, 'option_type'):  # Check for NamedTuple or similar
            return value.option_type in [OptionType.PUT, OptionType.PUT.value, "put"]
        elif isinstance(value, str):
            return value.lower() == "put"
        return False

    @staticmethod
    def is_call(value: Union[OptionType, str]) -> bool:
        """
        Check if the value represents a CALL option.

        Args:
            value: Can be OptionType enum or string

        Returns:
            bool: True if CALL, False otherwise
        """
        if isinstance(value, OptionType):
            return value == OptionType.CALL
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
    def is_long(value: Union[PositionSide, str]) -> bool:
        """
        Check if the value represents a LONG position.

        Args:
            value: Can be PositionSide enum or string

        Returns:
            bool: True if LONG, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.LONG
        elif hasattr(value, 'position_side'):  # Check for NamedTuple or similar
            return value.position_side in [PositionSide.LONG, PositionSide.LONG.value, "long"]
        elif isinstance(value, str):
            return value.lower() == "long"
        return False

    @staticmethod
    def is_short(value: Union[PositionSide, str]) -> bool:
        """
        Check if the value represents a SHORT position.

        Args:
            value: Can be PositionSide enum or string

        Returns:
            bool: True if SHORT, False otherwise
        """
        if isinstance(value, PositionSide):
            return value == PositionSide.SHORT
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

# FuturesType's Globex/db ticker -> INSTRUMENTS dict key, for the 3 FX
# symbols where they diverge (INSTRUMENTS keys by IBKR-facing ticker, not
# the raw Globex root -- see instruments.py's db_symbol field docs).
_FX_TICKER_TO_INSTRUMENTS_KEY = {'6J': 'JPY', '6L': 'BRE', '6M': '6M'}


def _spec(db_symbol: str) -> tuple[float, float, float]:
    """(mult, initial_margin, commission) for one FuturesType member,
    sourced from instruments.py's INSTRUMENTS/BACKTEST_ONLY_SPECS -- the
    single source of truth for these numbers (see that module's docstring
    for provenance/estimation caveats and why BACKTEST_ONLY_SPECS is a
    separate dict). `db_symbol` is the real exchange ticker (e.g. '6J'),
    mapped to its INSTRUMENTS key where they diverge."""
    key = _FX_TICKER_TO_INSTRUMENTS_KEY.get(db_symbol, db_symbol)
    info = INSTRUMENTS.get(key) or BACKTEST_ONLY_SPECS[key]
    return (info['multiplier'], info['initial_margin'], info['commission'])


class FuturesType(Enum):
    """Futures contract type enumeration, with associated properties.
    Multiplier/margin/commission values live in instruments.py (see
    _spec() above and that module's docstring), not here."""
    MES = _spec('MES') # Micro E-mini S&P 500
    ES = _spec('ES') # E-mini S&P 500
    MNQ = _spec('MNQ') # Micro E-mini Nasdaq-100
    NQ = _spec('NQ') # E-mini Nasdaq-100
    MYM = _spec('MYM') # Micro E-mini Dow
    YM = _spec('YM') # E-mini Dow
    M2K = _spec('M2K') # Micro E-mini Russell 2000
    RTY = _spec('RTY') # E-mini Russell 2000
    ZN = _spec('ZN') # 10-Year T-Note (CBOT)
    TN = _spec('TN') # Ultra 10-Year T-Note (CBOT)
    MTN = _spec('MTN') # Micro 10-Year T-Note (CBOT)
    ZT = _spec('ZT') # 2-Year T-Note (CBOT)
    GC = _spec('GC') # Gold (COMEX)
    SI = _spec('SI') # Silver (COMEX)
    CL = _spec('CL') # Crude Oil (NYMEX)
    ZL = _spec('ZL') # Soybean Oil (CBOT)
    ZC = _spec('ZC') # Corn (CBOT)
    ZS = _spec('ZS') # Soybeans (CBOT)
    ZW = _spec('ZW') # Wheat (CBOT)
    NIY = _spec('NIY') # Nikkei 225 Yen-denominated (CME) -- JPY-denominated
                        # (Y500/point); this codebase's PnL math has no FX
                        # conversion, so PnL comes out in JPY, not USD,
                        # unless that's added separately.
    # Python identifiers can't start with a digit, so the FX futures whose
    # actual exchange ticker starts with one (6J, 6L, 6M) are named with a
    # leading underscore here -- use FuturesType.from_symbol('6J') to look
    # them up by their real ticker rather than FuturesType['6J'] (invalid).
    _6J = _spec('6J') # Japanese Yen (CME)
    _6L = _spec('6L') # Brazilian Real (CME)
    _6M = _spec('6M') # Mexican Peso (CME)
    # SOX: not added, see instruments.py's BACKTEST_ONLY_SPECS docstring.

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


class TrendRegime(str, Enum):
    """TSMOM trend regime from classify_regime() -- sign agreement between
    the fast (~3mo) and slow (~12mo) trend-strength scores."""
    BULL = "bull"
    CORRECTION = "correction"
    BEAR = "bear"
    REBOUND = "rebound"
    UNKNOWN = "unknown"


class VolRegime(str, Enum):
    """TSMOM vol-spike regime from check_vol_regime() -- current vol
    (VX front-month live, or spot VIX in the backtest) vs its trailing
    63-day MA. Portfolio-wide and VIX/VX-driven -- feeds market_stress_scale,
    not signal_confidence (see SignalConfidenceRegime, which is per-
    instrument and asset-specific instead)."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    SPIKE = "spike"
    EXTREME = "extreme"


class SignalConfidenceRegime(str, Enum):
    """Per-instrument, asset-specific vol-regime classification from
    classify_signal_confidence() -- THIS instrument's own short-window/
    long-window realized-vol ratio vs its own history. NOT VIX/VX-driven
    (contrast VolRegime, which is portfolio-wide) -- catches e.g. a corn-
    harvest or JPY-intervention vol spike that broad-market VX/VIX has no
    visibility into. Feeds signal_confidence, an opt-in discount on trust
    in that instrument's own trend signal."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

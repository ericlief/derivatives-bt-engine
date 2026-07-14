from derivatives_bt_engine.domain.enums import *
from dataclasses import dataclass
from typing import Optional, List, Dict
from abc import ABC, abstractmethod
from derivatives_bt_engine.domain.instruments import known_futures_symbols
from derivatives_bt_engine.domain.option_leg_config import OptionLegConfig
from typing import List
from typing import Optional, Tuple


@dataclass
class BaseStrategyConfig(ABC):
    """Configuration for a trading strategy."""
    quantity: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_positions: int = 1
    initial_capital: float = 100000
    leverage: float = 1
    max_margin_utilization: float = 0.80
    vix_range: Optional[Tuple[float, float]] = None
    vix_max: Optional[float] = None
    
@dataclass(kw_only=True)
class BaseOptionStrategyConfig(BaseStrategyConfig, ABC):
    option_strategy: OptionsStrategy
    use_underlying_close: bool = False
    early_close_after_dit: Optional[int] = None
    early_close_on_dte: Optional[int] = None
    trade_selection_method: Optional[TradeSelectionMethod] = TradeSelectionMethod.DELTA_FIRST
    delta_weight: float = 0.5
    premium_weight: float = 0.5
    multiplier: Optional[float] = 100

@dataclass(kw_only=True)
class SingleLegOptionStrategyConfig(BaseOptionStrategyConfig):
    """Configuration for an option strategy."""

    # leg: OptionLegConfig = field(default_factory=OptionLegConfig)
    leg: OptionLegConfig 

    def __post_init__(self):
        """
        Option strategy types for single leg:
        SHORT_PUT = "short_put"
        LONG_PUT = "long_put"
        SHORT_CALL = "short_call"
        LONG_CALL = "long_call"
        """
        if self.option_strategy == OptionsStrategy.LONG_CALL:
            if self.leg.option_type != OptionsType.CALL or self.leg.position_side != PositionSide.LONG:
                raise ValueError(f"Option strategy {self.option_strategy} requires one long call leg")
        elif self.option_strategy == OptionsStrategy.LONG_PUT:
            if self.leg.option_type != OptionsType.PUT or self.leg.position_side != PositionSide.LONG:
                raise ValueError(f"Option strategy {self.option_strategy} requires one long put leg")
        elif self.option_strategy == OptionsStrategy.SHORT_CALL:
            if self.leg.option_type != OptionsType.CALL or self.leg.position_side != PositionSide.SHORT:
                raise ValueError(f"Option strategy {self.option_strategy} requires one short call leg")        
        elif self.option_strategy == OptionsStrategy.SHORT_PUT:
            if self.leg.option_type != OptionsType.PUT or self.leg.position_side != PositionSide.SHORT:
                raise ValueError(f"Option strategy {self.option_strategy} requires one short put leg")
        else:
            raise ValueError("Unknown single-leg option strategy")
        
@dataclass(kw_only=True)
class MultiLegOptionStrategyConfig(BaseOptionStrategyConfig):
    spread_type: OptionSpreadType
    legs: List[OptionLegConfig]
    leg_ratios: Dict[int, float] = None
    max_spread_width: Optional[float] = None  # Maximum spread width in points (e.g., 50 for SPX means max $5000 margin)
    max_trade_loss: Optional[float] = None # Position-based risk management (e.g. $500)
    premium_ratio: Optional[float] = None # Only trades with 1/3 of width, etc.
    # When True, any LONG leg without a delta_target/delta_range has its
    # strike derived from the matching SHORT leg of the same option_type
    # (same-day, same expiration), placed max_spread_width points further
    # out-of-the-money -- overrides delta-based long-leg selection for
    # verticals and iron condors.
    use_spread_width: bool = False

    def __post_init__(self):
        # Validate legs configuration
        for leg in self.legs:
            if not hasattr(leg, 'option_type') or not hasattr(leg, 'position_side'):
                raise ValueError("Each leg must have 'option_type' and 'position_side' defined")

        # A leg with no delta_target/delta_range is only valid as a LONG leg
        # under use_spread_width, and only if a matching SHORT leg (same
        # option_type, itself delta-based) exists to anchor it.
        for leg in self.legs:
            has_delta = leg.delta_target is not None or leg.delta_range is not None
            if has_delta:
                continue
            if not self.use_spread_width or leg.position_side != PositionSide.LONG:
                raise ValueError(
                    "Leg has neither delta_target nor delta_range; this is only "
                    "allowed for a LONG leg when use_spread_width=True"
                )
            anchor = next(
                (l for l in self.legs if l.option_type == leg.option_type
                 and l.position_side == PositionSide.SHORT
                 and (l.delta_target is not None or l.delta_range is not None)),
                None,
            )
            if anchor is None:
                raise ValueError(
                    f"use_spread_width=True: no matching SHORT {leg.option_type} leg "
                    "with a delta_target/delta_range found to anchor this LONG leg's strike"
                )

        if self.use_spread_width and self.max_spread_width is None:
            raise ValueError("use_spread_width=True requires max_spread_width to be set")

        # Not sure if we should derive ratio form leg quantity here or in the leg config
         # Set default leg ratios if not provided
        if not self.leg_ratios:
            self.leg_ratios = {i: 1.0 for i in range(len(self.legs))}

        # Validate spread type
        if self.spread_type not in [OptionSpreadType.VERTICAL, OptionSpreadType.CALENDAR, OptionSpreadType.DIAGONAL, OptionSpreadType.IRON_CONDOR, OptionSpreadType.BUTTERFLY]:
            raise ValueError("Invalid spread type. Supported types are: vertical, calendar, diagonal, iron_condor, butterfly")

@dataclass(kw_only=True)    
class FuturesStrategyConfig(BaseStrategyConfig):

    futures_type: str
    futures_strategy: FuturesStrategy
    # Fill price model for entry/exit, since there's no bid/ask in the daily
    # OHLCV data this is sourced from: 'close' (the day's settlement price,
    # current default/unchanged behavior) or 'mid' ((high+low)/2, a rough
    # proxy for average fill price across the day's traded range).
    fill_price: str = 'close'
    # Signal-based entry/exit gates (checked daily by TradeManager, same
    # early_closure/entry-gating path as vix_max/vix_range) -- use the raw
    # tsmom_signal.calculate_trend_strength() values, with no vol-target
    # scalar/momentum_discount/risk-budget applied (those are TSMOM
    # position-sizing concerns, not exit conditions). Direction-aware: a
    # LONG position exits/is blocked from entry on weakness, a SHORT
    # position on the mirrored condition.
    #
    # ts_exit_threshold/ts_entry_threshold are deliberately separate (not
    # one threshold reused for both) so entry can require the signal to
    # recover past a stronger bar than the one that triggered the exit --
    # without that gap, a position closed for weak signal reopens the
    # instant the signal ticks back over the same single line, causing
    # rapid close/reopen thrashing (confirmed empirically: a single shared
    # threshold took ES 2010-2026 from 63 trades to 854-1402).
    ts_exit_threshold: Optional[float] = None
    ts_entry_threshold: Optional[float] = None
    # ts3m/ts1y crossover has no natural "threshold" (it's a sign/ordering
    # comparison, not a magnitude) -- entry is blocked by the mirrored
    # condition of whatever would trigger an exit, no separate parameter.
    exit_on_ts_crossover: bool = False

    def __post_init__(self):
        if self.futures_type not in known_futures_symbols():
            raise ValueError(f"Invalid futures type. Supported types are: {sorted(known_futures_symbols())}")

        if self.futures_strategy not in [FuturesStrategy.LONG_FUTURES, FuturesStrategy.SHORT_FUTURES]:
            raise ValueError("Invalid futures strategy. Supported strategies are: long futures, short futures")

        if self.fill_price not in ('close', 'mid'):
            raise ValueError(f"Invalid fill_price {self.fill_price!r}. Supported values are: 'close', 'mid'")

        if self.futures_strategy in [FuturesStrategy.LONG_FUTURES]:
            self.position_side = PositionSide.LONG

        if self.futures_strategy in [FuturesStrategy.SHORT_FUTURES]:
            self.position_side = PositionSide.SHORT

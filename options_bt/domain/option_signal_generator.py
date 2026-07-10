from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Union, List, Tuple

import polars as pl
import pandas as pd

from options_bt.domain.enums import *
from options_bt.domain.base_signal_generator import BaseSignalGenerator
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.strategy_config import FuturesStrategyConfig, SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.utils.logger import setup_logger

# Create logger instance
logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────
_MAX_SPREAD_PERCENT = 50.0                  # max bid-ask spread, as % of bid
_DTE_TARGET_TOLERANCE_DAYS = 2               # +/- days tolerance around dte_target
_DELTA_TARGET_TOLERANCE_PCT = 0.05           # 5% tolerance around delta_target
_BUTTERFLY_STRIKE_SPACING_TOLERANCE = 0.01   # float tolerance for equal wing widths


@dataclass
class OptionSignalGenerator(BaseSignalGenerator):
    """Class to generate signals for trading.

    option_chain/underlying are still pandas (matching what Backtester hands
    in today) -- converted to polars once here and used internally
    throughout. generate_single_leg_signals()/generate_multi_leg_signals()
    convert back to pandas at their return boundary, since the position /
    trade manager / backtester option paths are still pandas-based. Both
    boundary conversions are the single scoped conversion points (per
    CLAUDE.md's pandas/polars convention) and should be deleted once those
    downstream consumers are migrated too.
    """

    config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
    option_chain: pd.DataFrame
    underlying: pd.DataFrame

    def __post_init__(self):
        super().__init__(config=self.config)

        if not isinstance(self.config, (SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig)):
            raise ValueError("Invalid config type")

        logger.info(f"Config: {self.config}")

        # option_chain's DatetimeIndex is unnamed -- reset_index() names the
        # new column 'index', not 'date'.
        self._option_chain_pl = pl.from_pandas(self.option_chain.reset_index()).rename({'index': 'date'})

    def fetch_data(self) -> pd.DataFrame:
        return self.option_chain

    def generate_single_leg_signals(
        self,
        option_type: OptionType,
        position_side: PositionSide,
        delta_target: Optional[float] = None,
        delta_range: Optional[Tuple[float, float]] = None,
        dte_target: Optional[int] = None,
        dte_range: Optional[Tuple[int, int]] = None,
        early_close_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Generate trade signals based on the provided parameters. These are not the actual trades,
        but rather potential trades filtered for the desired criteria.

        Args:
            option_type: Type of option (call or put)
            position_side: Position side (long or short)
            delta_target: Target delta for the option
            delta_range: Range of deltas for filtering
            dte_target: Target days to expiration
            dte_range: Range of days to expiration
            early_close_days: Days to close early
            start_date: Start date for filtering
            end_date: End date for filtering

        Returns:
            DataFrame containing the generated trade signals, indexed by date
        """
        result = self._generate_single_leg_signals_pl(
            option_type=option_type,
            position_side=position_side,
            delta_target=delta_target,
            delta_range=delta_range,
            dte_target=dte_target,
            dte_range=dte_range,
            early_close_days=early_close_days,
            start_date=start_date,
            end_date=end_date,
        )
        return result.to_pandas().set_index('date')

    def _generate_single_leg_signals_pl(
        self,
        option_type: OptionType,
        position_side: PositionSide,
        delta_target: Optional[float] = None,
        delta_range: Optional[Tuple[float, float]] = None,
        dte_target: Optional[int] = None,
        dte_range: Optional[Tuple[int, int]] = None,
        early_close_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pl.DataFrame:
        logger.debug(f'Generating trade signals for {option_type}|{delta_target if delta_target else delta_range}|{dte_target if dte_target else dte_range}|{start_date if start_date else "all"}|{end_date if end_date else "all"}')

        chain = self._option_chain_pl

        # Filter by DATE range if provided
        start = date.fromisoformat(start_date) if start_date else chain['date'].min()
        end = date.fromisoformat(end_date) if end_date else chain['date'].max()
        chain = chain.filter((pl.col('date') >= start) & (pl.col('date') <= end))
        logger.debug(f'Sorting for date range: {start}-{end}')
        logger.debug(f'Sample chain of length: {chain.height}')

        # Remove columns that are not needed
        is_put = OptionType.is_put(option_type)
        prefix = 'p_' if is_put else 'c_'
        needed_cols = [col for col in chain.columns if col.startswith(prefix)]
        needed_cols.extend(['date', 'strike', 'dte', 'underlying_last', 'expire_date'])
        chain = chain.select(needed_cols)

        # Filter out options with zero or negative bids/asks
        bid_col = f'{prefix}bid'
        ask_col = f'{prefix}ask'
        chain = chain.filter((pl.col(bid_col) > 0) & (pl.col(ask_col) > 0))

        # Filter out options with unreasonable spreads
        chain = chain.with_columns(
            (((pl.col(ask_col) - pl.col(bid_col)) / pl.col(bid_col)) * 100).alias('spread_percent')
        ).filter(pl.col('spread_percent') <= _MAX_SPREAD_PERCENT)
        logger.debug(f'After spread filtering: {chain.height} options remaining')

        # Midpoint price. Equivalent to PriceUtils.calculate_midpoint_price(bid, ask)
        # for every surviving row here (bid/ask are already > 0 and spread-filtered
        # above, so its validity checks can never fire) -- this is the vectorized form.
        chain = chain.with_columns(((pl.col(bid_col) + pl.col(ask_col)) / 2).alias('midpoint_price'))

        # Filter by DTE based on whether we have a single value or range
        if dte_range:
            chain = chain.filter((pl.col('dte') >= dte_range[0]) & (pl.col('dte') <= dte_range[1]))
            logger.debug(f'Filtering for dte range: {dte_range}, remaining: {chain.height}')
        elif dte_target:
            chain = chain.filter((pl.col('dte') - dte_target).abs() <= _DTE_TARGET_TOLERANCE_DAYS)
            logger.debug(f'Filtering for dte target: {dte_target}, remaining: {chain.height}')
        else:
            logger.error('Need to provide either <dte_target> or <dte_range>')
            raise ValueError

        # Filter by delta parameters
        delta_col = prefix + 'delta'

        if delta_range:
            if is_put:
                min_delta = -abs(delta_range[1])  # More negative (more ITM)
                max_delta = -abs(delta_range[0])   # Less negative (more OTM)
            else:
                min_delta = abs(delta_range[0])    # Less positive (more OTM)
                max_delta = abs(delta_range[1])     # More positive (more ITM)

            logger.debug(f'Filtering for delta range: {min_delta} to {max_delta} for {"put" if is_put else "call"}')
            chain = chain.filter(pl.col(delta_col).is_between(min_delta, max_delta))
            chain = self._sort_by_trade_selection(chain, position_side, delta_col)

        elif delta_target:
            target = -abs(delta_target) if is_put else abs(delta_target)

            logger.debug(f'Filtering for delta target: {target} for {option_type.value}')
            chain = chain.with_columns((pl.col(delta_col) - target).abs().alias('delta_diff'))

            max_delta_diff = abs(target) * _DELTA_TARGET_TOLERANCE_PCT
            chain = chain.filter(pl.col('delta_diff') <= max_delta_diff)

            logger.info(f'Sorting according to trade strategy {self.config.trade_selection_method} for {position_side}')
            if self.config.trade_selection_method == TradeSelectionMethod.WEIGHTED:
                chain = chain.with_columns(
                    (self.config.premium_weight * pl.col('midpoint_price') / pl.col('midpoint_price').max() +
                     self.config.delta_weight * pl.col('delta_diff') / pl.col('delta_diff').max()).alias('weighted_trade_score')
                )
            chain = self._sort_by_trade_selection(chain, position_side, 'delta_diff')

        else:
            logger.error('Need to provide either delta_target or delta_range')
            raise ValueError

        logger.info(f"Generated {chain.height} trade signals")
        return chain

    def _sort_by_trade_selection(self, chain: pl.DataFrame, position_side: PositionSide, primary_col: str) -> pl.DataFrame:
        """Stable-sort candidate rows per date by the configured trade-selection
        method, so the best candidate per date sorts first (a downstream
        consumer picks the head of each date group)."""
        method = self.config.trade_selection_method
        price_ascending = PositionSide.is_long(position_side)  # cheaper first when buying, pricier first when selling

        if method == TradeSelectionMethod.DELTA_FIRST:
            return chain.sort(['date', primary_col, 'midpoint_price'], descending=[False, False, not price_ascending], maintain_order=True)
        elif method == TradeSelectionMethod.PREMIUM_FIRST:
            return chain.sort(['date', 'midpoint_price', primary_col], descending=[False, not price_ascending, False], maintain_order=True)
        elif method == TradeSelectionMethod.WEIGHTED and 'weighted_trade_score' in chain.columns:
            return chain.sort(['date', 'weighted_trade_score'], maintain_order=True)
        else:
            logger.error('Cannot sort signal df because trade strategy default not defined')
            return chain

    def _tag_leg_signals(self, leg_df: pl.DataFrame, i: int, leg_config: OptionLegConfig) -> pl.DataFrame:
        """Attach leg-identifying columns (leg_number, position_side, option_type,
        leg_ratio, delta_target[/range]) shared by every leg regardless of how
        its candidate strikes were selected (delta-based or width-derived)."""
        option_type = leg_config.option_type
        position_side = leg_config.position_side
        leg_df = leg_df.with_columns([
            pl.lit(i + 1).alias('leg_number'),
            pl.lit(position_side.value if isinstance(position_side, Enum) else position_side).alias('position_side'),
            pl.lit(option_type.value if isinstance(option_type, Enum) else option_type).alias('option_type'),
            pl.lit(getattr(leg_config, 'ratio', 1)).alias('leg_ratio'),
            pl.lit(leg_config.delta_target).alias('delta_target'),
        ])
        if leg_config.delta_range:
            leg_df = leg_df.with_columns([
                pl.lit(leg_config.delta_range[0]).alias('delta_range_min'),
                pl.lit(leg_config.delta_range[1]).alias('delta_range_max'),
            ])
        return leg_df

    def _derive_width_based_leg_signals_pl(self, leg_config: OptionLegConfig, anchor_signals: pl.DataFrame, width: float) -> pl.DataFrame:
        """Derive a LONG leg's candidate strikes from its matching SHORT leg's
        best-per-date candidate (anchor_signals, already sorted best-first per
        date by _sort_by_trade_selection), placed `width` points further
        out-of-the-money -- used in place of delta-based selection when
        MultiLegOptionStrategyConfig.use_spread_width is set.
        """
        is_put = OptionType.is_put(leg_config.option_type)
        prefix = 'p_' if is_put else 'c_'
        direction = -1 if is_put else 1  # further OTM: lower strike for puts, higher for calls

        # One row per (date, expire_date): the anchor's best candidate. A row
        # index preserves that ordering across the join_asof below, which
        # requires re-sorting by strike.
        anchor = anchor_signals.unique(subset=['date', 'expire_date'], keep='first', maintain_order=True)
        anchor = anchor.with_row_index('_anchor_idx').with_columns(
            (pl.col('strike') + direction * width).alias('target_strike')
        ).select(['_anchor_idx', 'date', 'expire_date', 'target_strike'])

        chain = self._option_chain_pl
        needed_cols = [col for col in chain.columns if col.startswith(prefix)]
        needed_cols.extend(['date', 'strike', 'dte', 'underlying_last', 'expire_date'])
        chain = chain.select(needed_cols)

        bid_col, ask_col = f'{prefix}bid', f'{prefix}ask'
        chain = chain.filter((pl.col(bid_col) > 0) & (pl.col(ask_col) > 0))
        chain = chain.with_columns(
            (((pl.col(ask_col) - pl.col(bid_col)) / pl.col(bid_col)) * 100).alias('spread_percent')
        ).filter(pl.col('spread_percent') <= _MAX_SPREAD_PERCENT)
        chain = chain.with_columns(((pl.col(bid_col) + pl.col(ask_col)) / 2).alias('midpoint_price'))

        if leg_config.dte_range:
            chain = chain.filter((pl.col('dte') >= leg_config.dte_range[0]) & (pl.col('dte') <= leg_config.dte_range[1]))
        elif leg_config.dte_target:
            chain = chain.filter((pl.col('dte') - leg_config.dte_target).abs() <= _DTE_TARGET_TOLERANCE_DAYS)

        anchor_sorted = anchor.sort(['date', 'expire_date', 'target_strike'])
        chain_sorted = chain.sort(['date', 'expire_date', 'strike'])

        joined = anchor_sorted.join_asof(
            chain_sorted,
            left_on='target_strike',
            right_on='strike',
            by=['date', 'expire_date'],
            strategy='nearest',
        )
        joined = joined.sort('_anchor_idx').drop(['_anchor_idx', 'target_strike'])
        joined = joined.drop_nulls(subset=['strike'])  # no chain match for that date/expire_date group

        logger.info(f"Derived {joined.height} width-based ({width}pt) trade signals for {leg_config.option_type.value}")
        return joined

    def generate_multi_leg_signals(self) -> pd.DataFrame:
        """
        Generate trade signals for option spreads by pairing legs according to the specified spread type.

        Returns:
            DataFrame containing the generated spread signals with legs paired by date, indexed by date
        """
        logger.info(f"Generating {self.config.spread_type.value} spread signals...")

        if self.config.spread_type == OptionSpreadType.NONE:
            raise ValueError("Use generate_trade_signals for single-leg positions")

        use_spread_width = getattr(self.config, 'use_spread_width', False)

        # Pass 1: generate delta-based candidate strikes for every leg that
        # has a delta_target/delta_range. Width-derived long legs (no delta;
        # only valid when use_spread_width=True, enforced in
        # MultiLegOptionStrategyConfig.__post_init__) are left as None here
        # and filled in during pass 2, since they depend on their anchor
        # SHORT leg's signals already having been generated.
        leg_signals: List[Optional[pl.DataFrame]] = [None] * len(self.config.legs)
        for i, leg_config in enumerate(self.config.legs):
            has_delta = leg_config.delta_target is not None or leg_config.delta_range is not None
            if not has_delta:
                continue

            dte_target = leg_config.dte_target
            dte_range = leg_config.dte_range
            if dte_target is None and dte_range is None:
                logger.error(f"Leg {i+1} must have either dte_target or dte_range specified")
                return pd.DataFrame()

            leg_df = self._generate_single_leg_signals_pl(
                option_type=leg_config.option_type,
                position_side=leg_config.position_side,
                delta_target=leg_config.delta_target,
                delta_range=leg_config.delta_range,
                dte_target=dte_target,
                dte_range=dte_range,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
            )

            if leg_df.height == 0:
                logger.warning(f"No signals generated for leg {i+1} with config: {leg_config}")
                return pd.DataFrame()

            leg_signals[i] = self._tag_leg_signals(leg_df, i, leg_config)

        # Pass 2: derive width-based LONG legs from their matching anchor
        # SHORT leg (same option_type), now that anchors are generated.
        if use_spread_width:
            for i, leg_config in enumerate(self.config.legs):
                if leg_signals[i] is not None:
                    continue
                anchor_idx = next(
                    j for j, lc in enumerate(self.config.legs)
                    if lc.option_type == leg_config.option_type and lc.position_side == PositionSide.SHORT
                )
                leg_df = self._derive_width_based_leg_signals_pl(leg_config, leg_signals[anchor_idx], self.config.max_spread_width)

                if leg_df.height == 0:
                    logger.warning(f"No width-derived signals for leg {i+1} with config: {leg_config}")
                    return pd.DataFrame()

                leg_signals[i] = self._tag_leg_signals(leg_df, i, leg_config)

        # No valid signals for one or more legs
        if any(df is None or df.height == 0 for df in leg_signals):
            logger.error("One or more legs returned no signals")
            return pd.DataFrame()

        # Create spread signals based on the spread type
        if self.config.spread_type == OptionSpreadType.VERTICAL:
            result = self._pair_vertical_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.CALENDAR:
            result = self._pair_calendar_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.DIAGONAL:
            result = self._pair_diagonal_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.BUTTERFLY:
            result = self._pair_butterfly_spread_legs(leg_signals, self.config.spread_type)
        elif self.config.spread_type == OptionSpreadType.IRON_CONDOR:
            result = self._pair_iron_condor_spread_legs(leg_signals, self.config.spread_type)
        else:
            raise ValueError(f"Unsupported spread type: {self.config.spread_type}")

        if result.height == 0:
            return pd.DataFrame()
        return result.to_pandas().set_index('date')

    def _pair_vertical_spread_legs(self, leg_signals: List[pl.DataFrame], spread_type: OptionSpreadType) -> pl.DataFrame:
        """Pair legs for vertical spreads (same expiration, different strikes)."""
        if len(leg_signals) != 2:
            raise ValueError(f"Vertical spreads require exactly 2 legs, got {len(leg_signals)}")

        logger.debug("Pairing vertical spread legs...")

        leg1, leg2 = leg_signals[0], leg_signals[1]
        leg1_option_type = leg1['option_type'][0]
        leg1_position_side = leg1['position_side'][0]
        leg2_option_type = leg2['option_type'][0]
        leg2_position_side = leg2['position_side'][0]

        # underlying_last is identical across legs (same day's quote) -- keep
        # one shared copy rather than prefixing+coalescing after the join.
        leg1 = leg1.rename({c: f'leg1_{c}' for c in leg1.columns if c not in ('date', 'expire_date', 'underlying_last')})
        leg2 = leg2.drop('underlying_last')
        leg2 = leg2.rename({c: f'leg2_{c}' for c in leg2.columns if c not in ('date', 'expire_date')})

        paired = leg1.join(leg2, on=['date', 'expire_date'], how='inner', maintain_order='left')
        logger.debug(f"Paired {paired.height} vertical spread legs")

        if paired.height > 0 and spread_type == OptionSpreadType.VERTICAL:
            paired = paired.filter(pl.col('leg1_strike') != pl.col('leg2_strike'))

            # For put vertical spreads, leg1 strike should be higher than leg2 strike for a credit spread
            if OptionType.is_put(leg1_option_type) and PositionSide.is_short(leg1_position_side):
                paired = paired.filter(pl.col('leg1_strike') > pl.col('leg2_strike'))
            # For call vertical spreads, leg1 strike should be lower than leg2 strike for a credit spread
            elif OptionType.is_call(leg1_option_type) and PositionSide.is_short(leg1_position_side):
                paired = paired.filter(pl.col('leg1_strike') < pl.col('leg2_strike'))

        paired = paired.with_columns([
            pl.lit(spread_type.value).alias('spread_type'),
            (pl.col('leg1_strike') - pl.col('leg2_strike')).abs().alias('spread_width'),
        ])

        if self.config.max_spread_width is not None:
            before = paired.height
            paired = paired.filter(pl.col('spread_width') <= self.config.max_spread_width)
            if paired.height < before:
                logger.info(f"Filtered out {before - paired.height} spreads due to excessive width (> {self.config.max_spread_width} points)")

        leg1_bid = 'leg1_p_bid' if OptionType.is_put(leg1_option_type) else 'leg1_c_bid'
        leg1_ask = 'leg1_p_ask' if OptionType.is_put(leg1_option_type) else 'leg1_c_ask'
        leg2_bid = 'leg2_p_bid' if OptionType.is_put(leg2_option_type) else 'leg2_c_bid'
        leg2_ask = 'leg2_p_ask' if OptionType.is_put(leg2_option_type) else 'leg2_c_ask'
        paired = paired.with_columns([
            ((pl.col(leg1_bid) + pl.col(leg1_ask)) / 2).alias('leg1_price'),
            ((pl.col(leg2_bid) + pl.col(leg2_ask)) / 2).alias('leg2_price'),
        ])

        side1 = 1 if PositionSide.is_short(leg1_position_side) else -1
        side2 = 1 if PositionSide.is_short(leg2_position_side) else -1
        paired = paired.with_columns((side1 * pl.col('leg1_price') + side2 * pl.col('leg2_price')).alias('spread_price'))

        # Validate sign of premium (debit or credit) for vertical
        is_credit_spread = self.config.option_strategy in [OptionStrategy.BEAR_CALL_CREDIT_SPREAD, OptionStrategy.BULL_PUT_CREDIT_SPREAD]
        before = paired.height
        if is_credit_spread:
            paired = paired.filter(pl.col('spread_price') > 0)
            if paired.height < before:
                logger.debug(f'Filtered {before - paired.height} negative priced credit spread signals')
        else:
            # bug fix: the pre-migration pandas version computed this filter
            # but never assigned it back to `paired`, so debit spreads were
            # never actually filtered by sign. Now actually applied.
            paired = paired.filter(pl.col('spread_price') < 0)
            if paired.height < before:
                logger.debug(f'Filtered {before - paired.height} positive priced debit spread signals')

        logger.debug(f"Paired {paired.height} valid vertical spreads")
        return paired

    def _pair_calendar_spread_legs(self, leg_signals: List[pl.DataFrame], spread_type: OptionSpreadType) -> pl.DataFrame:
        """Pair legs for calendar spreads (same strike, different expirations)."""
        if len(leg_signals) != 2:
            raise ValueError(f"Calendar spreads require exactly 2 legs, got {len(leg_signals)}")

        logger.debug("Pairing calendar spread legs...")

        leg1, leg2 = leg_signals[0], leg_signals[1]  # front month, back month
        leg1_option_type = leg1['option_type'][0]
        leg1_position_side = leg1['position_side'][0]
        leg2_option_type = leg2['option_type'][0]
        leg2_position_side = leg2['position_side'][0]

        leg1 = leg1.rename({c: f'leg1_{c}' for c in leg1.columns if c not in ('date', 'underlying_last')})
        leg2 = leg2.drop('underlying_last')
        leg2 = leg2.rename({c: f'leg2_{c}' for c in leg2.columns if c != 'date'})

        # Merge on date only (same strike, different expirations by design)
        paired = leg1.join(leg2, on=['date'], how='inner', maintain_order='left')
        logger.debug(f"Paired {paired.height} calendar spread legs")

        paired = paired.filter(pl.col('leg1_strike') == pl.col('leg2_strike'))
        paired = paired.filter(pl.col('leg1_expire_date') < pl.col('leg2_expire_date'))

        paired = paired.with_columns([
            pl.lit(spread_type.value).alias('spread_type'),
            (pl.col('leg2_expire_date') - pl.col('leg1_expire_date')).dt.total_days().alias('time_width'),
        ])

        leg1_bid = 'leg1_p_bid' if OptionType.is_put(leg1_option_type) else 'leg1_c_bid'
        leg1_ask = 'leg1_p_ask' if OptionType.is_put(leg1_option_type) else 'leg1_c_ask'
        leg2_bid = 'leg2_p_bid' if OptionType.is_put(leg2_option_type) else 'leg2_c_bid'
        leg2_ask = 'leg2_p_ask' if OptionType.is_put(leg2_option_type) else 'leg2_c_ask'
        paired = paired.with_columns([
            ((pl.col(leg1_bid) + pl.col(leg1_ask)) / 2).alias('leg1_price'),
            ((pl.col(leg2_bid) + pl.col(leg2_ask)) / 2).alias('leg2_price'),
        ])

        # Standard calendar spread (short front month, long back month)
        if PositionSide.is_short(leg1_position_side) and PositionSide.is_long(leg2_position_side):
            paired = paired.with_columns((pl.col('leg2_price') - pl.col('leg1_price')).alias('spread_price'))
        # Reverse calendar spread (long front month, short back month)
        else:
            paired = paired.with_columns((pl.col('leg1_price') - pl.col('leg2_price')).alias('spread_price'))

        logger.debug(f"Paired {paired.height} valid calendar spreads")
        return paired

    def _pair_diagonal_spread_legs(self, leg_signals: List[pl.DataFrame], spread_type: OptionSpreadType) -> pl.DataFrame:
        """Pair legs for diagonal spreads (different strikes, different expirations)."""
        if len(leg_signals) != 2:
            raise ValueError(f"Diagonal spreads require exactly 2 legs, got {len(leg_signals)}")

        logger.debug("Pairing diagonal spread legs...")

        leg1, leg2 = leg_signals[0], leg_signals[1]  # front month/first strike, back month/second strike
        leg1_option_type = leg1['option_type'][0]
        leg1_position_side = leg1['position_side'][0]
        leg2_option_type = leg2['option_type'][0]
        leg2_position_side = leg2['position_side'][0]

        leg1 = leg1.rename({c: f'leg1_{c}' for c in leg1.columns if c not in ('date', 'underlying_last')})
        leg2 = leg2.drop('underlying_last')
        leg2 = leg2.rename({c: f'leg2_{c}' for c in leg2.columns if c != 'date'})

        # Merge on date only (same trading day; different strikes and expirations by design)
        paired = leg1.join(leg2, on=['date'], how='inner', maintain_order='left')
        logger.debug(f"Paired {paired.height} diagonal spread legs")

        paired = paired.filter(pl.col('leg1_expire_date') < pl.col('leg2_expire_date'))

        paired = paired.with_columns([
            pl.lit(spread_type.value).alias('spread_type'),
            (pl.col('leg2_expire_date') - pl.col('leg1_expire_date')).dt.total_days().alias('time_width'),
            (pl.col('leg1_strike') - pl.col('leg2_strike')).abs().alias('spread_width'),
        ])

        if self.config.max_spread_width is not None:
            before = paired.height
            paired = paired.filter(pl.col('spread_width') <= self.config.max_spread_width)
            if paired.height < before:
                logger.info(f"Filtered out {before - paired.height} diagonal spreads due to excessive strike width (> {self.config.max_spread_width} points)")

        leg1_bid = 'leg1_p_bid' if OptionType.is_put(leg1_option_type) else 'leg1_c_bid'
        leg1_ask = 'leg1_p_ask' if OptionType.is_put(leg1_option_type) else 'leg1_c_ask'
        leg2_bid = 'leg2_p_bid' if OptionType.is_put(leg2_option_type) else 'leg2_c_bid'
        leg2_ask = 'leg2_p_ask' if OptionType.is_put(leg2_option_type) else 'leg2_c_ask'
        paired = paired.with_columns([
            ((pl.col(leg1_bid) + pl.col(leg1_ask)) / 2).alias('leg1_price'),
            ((pl.col(leg2_bid) + pl.col(leg2_ask)) / 2).alias('leg2_price'),
        ])

        # Standard diagonal spread (short front month, long back month)
        if PositionSide.is_short(leg1_position_side) and PositionSide.is_long(leg2_position_side):
            paired = paired.with_columns((pl.col('leg2_price') - pl.col('leg1_price')).alias('spread_price'))
        # Reverse diagonal spread (long front month, short back month)
        else:
            paired = paired.with_columns((pl.col('leg1_price') - pl.col('leg2_price')).alias('spread_price'))

        logger.debug(f"Paired {paired.height} valid diagonal spreads")
        return paired

    def _pair_butterfly_spread_legs(self, leg_signals: List[pl.DataFrame], spread_type: OptionSpreadType) -> pl.DataFrame:
        """Pair legs for butterfly spreads (3 strikes, same expiration)."""
        if len(leg_signals) != 3:
            raise ValueError(f"Butterfly spreads require exactly 3 legs, got {len(leg_signals)}")

        logger.debug("Pairing butterfly spread legs...")

        leg1, leg2, leg3 = leg_signals  # lower strike, middle strike (2x qty), higher strike
        leg1_option_type = leg1['option_type'][0]
        leg1_position_side = leg1['position_side'][0]
        leg2_option_type = leg2['option_type'][0]
        leg3_option_type = leg3['option_type'][0]

        leg1 = leg1.rename({c: f'leg1_{c}' for c in leg1.columns if c not in ('date', 'expire_date', 'underlying_last')})
        leg2 = leg2.drop('underlying_last')
        leg2 = leg2.rename({c: f'leg2_{c}' for c in leg2.columns if c not in ('date', 'expire_date')})
        leg3 = leg3.drop('underlying_last')
        leg3 = leg3.rename({c: f'leg3_{c}' for c in leg3.columns if c not in ('date', 'expire_date')})

        paired = leg1.join(leg2, on=['date', 'expire_date'], how='inner', maintain_order='left')
        paired = paired.join(leg3, on=['date', 'expire_date'], how='inner', maintain_order='left')
        logger.debug(f"Paired {paired.height} butterfly spread legs")

        if paired.height > 0:
            paired = paired.with_columns([
                (pl.col('leg2_strike') - pl.col('leg1_strike')).alias('diff1'),
                (pl.col('leg3_strike') - pl.col('leg2_strike')).alias('diff2'),
            ])
            # Keep only rows where the strike spacing is equal (or very close)
            paired = paired.filter((pl.col('diff1') - pl.col('diff2')).abs() < _BUTTERFLY_STRIKE_SPACING_TOLERANCE)
            # Ensure strikes are in ascending order
            paired = paired.filter((pl.col('leg1_strike') < pl.col('leg2_strike')) & (pl.col('leg2_strike') < pl.col('leg3_strike')))

        paired = paired.with_columns([
            pl.lit(spread_type.value).alias('spread_type'),
            pl.col('diff1').alias('wing_width'),
        ])

        leg1_bid = 'leg1_p_bid' if OptionType.is_put(leg1_option_type) else 'leg1_c_bid'
        leg1_ask = 'leg1_p_ask' if OptionType.is_put(leg1_option_type) else 'leg1_c_ask'
        leg2_bid = 'leg2_p_bid' if OptionType.is_put(leg2_option_type) else 'leg2_c_bid'
        leg2_ask = 'leg2_p_ask' if OptionType.is_put(leg2_option_type) else 'leg2_c_ask'
        leg3_bid = 'leg3_p_bid' if OptionType.is_put(leg3_option_type) else 'leg3_c_bid'
        leg3_ask = 'leg3_p_ask' if OptionType.is_put(leg3_option_type) else 'leg3_c_ask'
        paired = paired.with_columns([
            ((pl.col(leg1_bid) + pl.col(leg1_ask)) / 2).alias('leg1_price'),
            ((pl.col(leg2_bid) + pl.col(leg2_ask)) / 2).alias('leg2_price'),
            ((pl.col(leg3_bid) + pl.col(leg3_ask)) / 2).alias('leg3_price'),
        ])

        # Long butterfly: buy wing options, sell 2x middle option
        if PositionSide.is_long(leg1_position_side):
            paired = paired.with_columns((pl.col('leg1_price') - 2 * pl.col('leg2_price') + pl.col('leg3_price')).alias('spread_price'))
        # Short butterfly: sell wing options, buy 2x middle option
        else:
            paired = paired.with_columns((2 * pl.col('leg2_price') - pl.col('leg1_price') - pl.col('leg3_price')).alias('spread_price'))

        logger.debug(f"Paired {paired.height} valid butterfly spreads")
        return paired

    def _pair_iron_condor_spread_legs(self, leg_signals: List[pl.DataFrame], spread_type: OptionSpreadType) -> pl.DataFrame:
        """Pair legs for iron condor spreads (4 strikes, same expiration)."""
        if len(leg_signals) != 4:
            raise ValueError(f"Iron condor spreads require exactly 4 legs, got {len(leg_signals)}")

        logger.debug("Pairing iron condor spread legs...")

        # legs[0]=long put (lower strike), legs[1]=short put (higher strike),
        # legs[2]=short call (lower strike), legs[3]=long call (higher strike)
        put_leg1, put_leg2, call_leg1, call_leg2 = leg_signals

        put_leg1 = put_leg1.rename({c: f'put_leg1_{c}' for c in put_leg1.columns if c not in ('date', 'expire_date')})
        put_leg2 = put_leg2.rename({c: f'put_leg2_{c}' for c in put_leg2.columns if c not in ('date', 'expire_date')})
        call_leg1 = call_leg1.rename({c: f'call_leg1_{c}' for c in call_leg1.columns if c not in ('date', 'expire_date')})
        call_leg2 = call_leg2.rename({c: f'call_leg2_{c}' for c in call_leg2.columns if c not in ('date', 'expire_date')})

        # Merge on date and expiration to ensure all legs are for the same
        # expiration and same trading day
        paired = put_leg1.join(put_leg2, on=['date', 'expire_date'], how='inner', maintain_order='left')
        paired = paired.join(call_leg1, on=['date', 'expire_date'], how='inner', maintain_order='left')
        paired = paired.join(call_leg2, on=['date', 'expire_date'], how='inner', maintain_order='left')
        logger.debug(f"Paired {paired.height} iron condor spread legs")

        # Ensure strikes are in the correct order
        if paired.height > 0:
            paired = paired.filter(
                (pl.col('put_leg1_strike') < pl.col('put_leg2_strike')) &
                (pl.col('put_leg2_strike') < pl.col('call_leg1_strike')) &
                (pl.col('call_leg1_strike') < pl.col('call_leg2_strike'))
            )

        paired = paired.with_columns([
            pl.lit(spread_type.value).alias('spread_type'),
            (pl.col('put_leg2_strike') - pl.col('put_leg1_strike')).alias('put_width'),
            (pl.col('call_leg2_strike') - pl.col('call_leg1_strike')).alias('call_width'),
            (pl.col('call_leg1_strike') - pl.col('put_leg2_strike')).alias('middle_width'),
        ])

        if self.config.max_spread_width is not None:
            before = paired.height
            paired = paired.filter(
                (pl.col('put_width') <= self.config.max_spread_width) &
                (pl.col('call_width') <= self.config.max_spread_width)
            )
            if paired.height < before:
                logger.info(f"Filtered out {before - paired.height} iron condor spreads due to excessive width (> {self.config.max_spread_width} points)")

        paired = paired.with_columns([
            ((pl.col('put_leg1_p_bid') + pl.col('put_leg1_p_ask')) / 2).alias('put_leg1_price'),
            ((pl.col('put_leg2_p_bid') + pl.col('put_leg2_p_ask')) / 2).alias('put_leg2_price'),
            ((pl.col('call_leg1_c_bid') + pl.col('call_leg1_c_ask')) / 2).alias('call_leg1_price'),
            ((pl.col('call_leg2_c_bid') + pl.col('call_leg2_c_ask')) / 2).alias('call_leg2_price'),
        ])

        # Total credit: put-vertical credit (short - long) + call-vertical credit (short - long)
        paired = paired.with_columns(
            ((pl.col('put_leg2_price') - pl.col('put_leg1_price')) +
             (pl.col('call_leg1_price') - pl.col('call_leg2_price'))).alias('spread_price')
        )

        # bug fix: the pre-migration pandas version had no credit/debit sign
        # check at all for iron condor (unlike verticals), so a net-debit
        # "condor" could pass through. Iron condors are entered for a net
        # credit, so require spread_price > 0, matching verticals' convention.
        before = paired.height
        paired = paired.filter(pl.col('spread_price') > 0)
        if paired.height < before:
            logger.debug(f'Filtered {before - paired.height} non-credit iron condor signals')

        logger.debug(f"Paired {paired.height} valid iron condor spreads")
        return paired

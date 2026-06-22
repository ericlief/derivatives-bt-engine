from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List

import polars as pl

from options_bt.domain.base_signal_generator import BaseSignalGenerator
from options_bt.domain.enums import FuturesStrategy, FuturesType, PositionSide
from options_bt.domain.strategy_config import FuturesStrategyConfig
from options_bt.utils.logger import setup_logger

logger = setup_logger()


@dataclass
class FuturesSignalGenerator(BaseSignalGenerator):
    """
    Polars-native signal generator for futures strategies. Futures have no
    spreads/legs, so this is a single, simple signal: each underlying bar
    is tagged with the next quarterly roll date and the contract's initial
    margin, becoming a one-row-per-day "hold until roll" signal.
    """

    config: FuturesStrategyConfig
    underlying: pl.DataFrame

    def __post_init__(self):
        super().__init__(config=self.config)

    def fetch_data(self) -> pl.DataFrame:
        return self.underlying

    def generate_futures_signals(
        self,
        futures_type: FuturesType,
        position_side: PositionSide,
        futures_strategy: FuturesStrategy,
        start_date,
        end_date,
    ) -> pl.DataFrame:
        """Generate trade signals for futures positions."""
        logger.info(f"Generating {self.config.futures_type.value} futures signals...")

        if futures_type not in [FuturesType.MES]:
            raise ValueError("Invalid futures type. Supported types are: MES")

        if futures_strategy not in [FuturesStrategy.LONG_FUTURES, FuturesStrategy.SHORT_FUTURES]:
            raise ValueError("Invalid futures strategy. Supported strategies are: long futures, short futures")

        if position_side not in [PositionSide.LONG, PositionSide.SHORT]:
            raise ValueError("Invalid position side. Supported sides are: long, short")

        start = self._to_date(start_date) if start_date else self.underlying['ts_event'].min()
        end = self._to_date(end_date) if end_date else self.underlying['ts_event'].max()

        roll_dates = self._get_quarterly_roll_dates(start, end)
        if not roll_dates:
            logger.warning("No quarterly roll dates found in range — no signals generated")
            return pl.DataFrame()

        roll_dates_df = pl.DataFrame({'roll_date': roll_dates}).sort('roll_date')

        underlying = (
            self.underlying
            .filter((pl.col('ts_event') >= start) & (pl.col('ts_event') <= end))
            .sort('ts_event')
        )

        # For each bar, attach the next (>=) quarterly roll date — equivalent
        # to "hold this contract until it rolls." Bars past the last roll
        # date in range get no match (null) and are dropped, matching the
        # original row-loop's behavior of producing no signal in that case.
        signals = underlying.join_asof(roll_dates_df, left_on='ts_event', right_on='roll_date', strategy='forward')
        signals = signals.filter(pl.col('roll_date').is_not_null())
        signals = signals.with_columns(pl.lit(self.config.futures_type.margin_required).alias('initial_margin'))

        logger.info(f'Generated {len(signals)} future signals:\n {signals.head(40)}')

        return signals

    @staticmethod
    def _to_date(value) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _get_quarterly_roll_dates(start_date: date, end_date: date) -> List[date]:
        """
        Quarterly roll dates (Monday prior to the third Friday of March,
        June, September, December) within [start_date, end_date].
        """
        roll_dates = []
        roll_months = [3, 6, 9, 12]

        for year in range(start_date.year, end_date.year + 2):  # look ahead a bit to catch rolls past end_date
            for month in roll_months:
                third_friday = FuturesSignalGenerator._third_friday(year, month)
                if third_friday is None:
                    continue
                roll_date = third_friday - timedelta(days=4)  # Monday prior
                if start_date <= roll_date <= end_date:
                    roll_dates.append(roll_date)

        return sorted(set(roll_dates))

    @staticmethod
    def _third_friday(year: int, month: int) -> date | None:
        d = date(year, month, 1)
        friday_count = 0
        while d.month == month:
            if d.weekday() == 4:  # Friday
                friday_count += 1
                if friday_count == 3:
                    return d
            d += timedelta(days=1)
        return None

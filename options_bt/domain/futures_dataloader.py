from __future__ import annotations
from dataclasses import dataclass
import os
import time
from functools import cached_property
from typing import Optional

import duckdb
import polars as pl

from options_bt.domain.base_dataloader import BaseDataLoader
from options_bt.utils.logger import setup_logger

logger = setup_logger()

# Front-month roll: for each trading date, take the not-yet-expired futures
# contract for `asset` with the nearest expiration.
_CONTINUOUS_FRONT_MONTH_SQL = """
WITH futs AS (
    -- DISTINCT: `instruments` can carry duplicate metadata snapshot rows
    -- (same instrument_id/expiration recorded at different ts_event) which
    -- would otherwise fan out the join below.
    SELECT DISTINCT instrument_id, expiration
    FROM instruments
    WHERE asset = ? AND instrument_class = 'F' AND security_type = 'FUT'
),
bars AS (
    SELECT o.instrument_id, o.ts_event, o.open, o.high, o.low, o.close, o.volume, f.expiration
    FROM ohlcv o
    JOIN futs f USING (instrument_id)
    WHERE o.ts_event < CAST(f.expiration AS DATE)
      -- `instrument_id` gets recycled by the exchange over long enough time
      -- spans -- several of our futures' ids were previously assigned to
      -- unrelated instruments (calendar spreads, options, even a different
      -- asset's spread) whose `instruments` metadata history was never
      -- captured locally, so a plain id+expiration join silently pulls in
      -- that prior owner's bars too (confirmed: years of bogus low/negative
      -- "CL" prices that were actually old calendar-spread/option bars).
      -- `ohlcv.symbol` is the raw ticker captured per-bar at ingestion time,
      -- independent of and more reliable than `instruments` -- a genuine
      -- outright futures contract's ticker is always root+month code+1-2
      -- digit year (e.g. "CLN7"), never hyphenated (a spread, e.g.
      -- "CLX8-CLZ9") or space-suffixed (an option strike, e.g. "NQQ2
      -- P1840"). Checking it directly sidesteps relying on `instruments`
      -- having complete history at all.
      AND regexp_matches(o.symbol, '^' || ? || '[FGHJKMNQUVXZ][0-9]{1,2}$')
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY ts_event ORDER BY expiration ASC) AS rn
    FROM bars
)
SELECT ts_event, open, high, low, close, volume, instrument_id
FROM ranked
WHERE rn = 1
ORDER BY ts_event
"""


@dataclass
class FuturesDataLoader(BaseDataLoader):
    """Loads continuous front-month futures OHLCV from the CME Globex MDP db, as polars DataFrames."""

    asset: str
    db_path: str = "/home/dev/fin/db/globex_mdp_3.0.duckdb"
    data_dir: str = "."
    vix_file: Optional[str] = None
    use_preprocessed: bool = True
    save_preprocessed: bool = True

    @property
    def _ohlcv_processed_path(self) -> str:
        return os.path.join(self.data_dir, f"{self.asset}_ohlcv.parquet")

    @cached_property
    def ohlcv(self) -> pl.DataFrame:
        """Lazy load and cache the continuous front-month futures OHLCV series."""
        if self.use_preprocessed and os.path.exists(self._ohlcv_processed_path):
            logger.info(f"Loading {self._ohlcv_processed_path}")
            return pl.read_parquet(self._ohlcv_processed_path)

        con = duckdb.connect(self.db_path, read_only=True)
        try:
            df = con.sql(_CONTINUOUS_FRONT_MONTH_SQL, params=[self.asset, self.asset]).pl()
        finally:
            con.close()

        df = df.with_columns(pl.col('ts_event').cast(pl.Date)).sort('ts_event')

        if self.save_preprocessed:
            df.write_parquet(self._ohlcv_processed_path)
            logger.info(f"Saved data to {self._ohlcv_processed_path}")

        return df

    def load_data(self) -> dict:
        """
        Load and return continuous futures OHLCV (and VIX, if configured),
        shaped to match Backtester's data contract.

        Backtester.__init__ unconditionally reads data['option_chain'],
        data['option_chain_multi_index'], data['underlying'], and
        data['vix'] regardless of strategy type, and
        FuturesSignalGenerator derives the futures price series straight
        from `underlying` (option_chain doesn't apply to futures) — so the
        continuous front-month series goes in as 'underlying', and the
        unused option_chain slots are empty placeholders. All values are
        polars DataFrames; vix comes from the (pandas-based, shared with
        OptionsDataLoader) BaseDataLoader.vix_data and is converted once
        here at the boundary.
        """
        data_loading_start = time.time()

        ohlcv = self.ohlcv
        logger.info(f"Loaded {self.asset} continuous futures OHLCV: {len(ohlcv)} rows")

        if self.vix_file is not None:
            vix = pl.from_pandas(self.vix_data.reset_index())
        else:
            vix = pl.DataFrame()

        result = {
            'option_chain': pl.DataFrame(),
            'option_chain_multi_index': pl.DataFrame(),
            'underlying': ohlcv,
            'vix': vix,
        }

        if self.vix_file is not None:
            logger.info(f"- VIX data: {len(result['vix'])} rows")

        logger.info(f"Data loading completed in {time.time() - data_loading_start:.2f} seconds")

        return result

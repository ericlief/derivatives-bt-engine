from __future__ import annotations
from dataclasses import dataclass, field
import os
import time
from functools import cached_property
from typing import Optional

import duckdb
import polars as pl
from dotenv import load_dotenv

from options_bt.domain.base_dataloader import BaseDataLoader
from options_bt.utils.logger import setup_logger

load_dotenv()

# ── Infrastructure ─────────────────────────────────────────────────────────
_DEFAULT_GLOBEX_DB_PATH = '/home/dev/fin/db/globex_mdp_3.0.duckdb'

logger = setup_logger()

# Front-month roll: for each trading date, take the not-yet-expired futures
# contract for `asset` with the nearest expiration.
#
# `ohlcv_enriched` (the databento pipeline's build_db.py) already solves
# `instrument_id` recycling more thoroughly than a plain join against
# `instruments` could: it parses each bar's own raw `symbol` (independent of
# `instruments`, which can have incomplete or entirely-missing history for a
# given id) to recover asset/instrument_class/security_type directly from the
# ticker shape, only trusts the ASOF-joined `instruments` row when it agrees
# with that parse, and falls back through `expiry_lookup` (borrowed from any
# other contract sharing the same month) and `rule_expirations` (empirically
# fit CME contract-termination rules) when `instruments` has no expiration at
# all for that id. This is what correctly excludes spreads/options that later
# had their id recycled into an outright future (instrument_class lands NULL
# for those, not 'F') and recovers full history `instruments` alone can't
# provide (confirmed against CL: this view's instrument_class='F' filter
# alone, with no further filtering on our part, gives the genuine 2010-2026
# front-month series, correctly including the real Apr 2020 negative-WTI
# print).
_CONTINUOUS_FRONT_MONTH_SQL = """
WITH bars AS (
    SELECT instrument_id, ts_event, open, high, low, close, volume, expiration
    FROM ohlcv_enriched
    WHERE asset = ? AND instrument_class = 'F' AND security_type = 'FUT'
      AND expiration IS NOT NULL
      AND ts_event < CAST(expiration AS DATE)
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
    db_path: str = field(default_factory=lambda: os.getenv('GLOBEX_DB_PATH', _DEFAULT_GLOBEX_DB_PATH))
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
            df = con.sql(_CONTINUOUS_FRONT_MONTH_SQL, params=[self.asset]).pl()
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
        data['underlying'], and data['vix'] regardless of strategy type, and
        FuturesSignalGenerator derives the futures price series straight
        from `underlying` (option_chain doesn't apply to futures) -- so the
        continuous front-month series goes in as 'underlying', and the
        unused option_chain slot is an empty placeholder. All values are
        polars DataFrames; vix comes from the (polars-native, shared with
        OptionsDataLoader) BaseDataLoader.vix_data, renamed here from `date`
        to `ts_event` to match this loader's own date-column convention (the
        rest of the futures path keys off `ts_event`, matching the raw
        databento series).
        """
        data_loading_start = time.time()

        ohlcv = self.ohlcv
        logger.info(f"Loaded {self.asset} continuous futures OHLCV: {len(ohlcv)} rows")

        if self.vix_file is not None:
            vix = self.vix_data.rename({'date': 'ts_event'})
        else:
            vix = pl.DataFrame()

        result = {
            'option_chain': pl.DataFrame(),
            'underlying': ohlcv,
            'vix': vix,
        }

        if self.vix_file is not None:
            logger.info(f"- VIX data: {len(result['vix'])} rows")

        logger.info(f"Data loading completed in {time.time() - data_loading_start:.2f} seconds")

        return result

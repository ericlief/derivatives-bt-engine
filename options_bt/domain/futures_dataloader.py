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
-- `instrument_id` gets recycled by the exchange over long enough time spans
-- -- a handful of our futures' ids were previously assigned to unrelated,
-- long-expired instruments (e.g. old options on a different asset). `ohlcv`
-- has no symbol/asset column, only instrument_id, so a plain id join pulls
-- in that prior owner's bars too. Bars dated before the prior owner's own
-- expiration belong to that prior owner, not to this asset -- exclude them.
prior_owner_cutoff AS (
    SELECT i.instrument_id, MAX(i.expiration) AS prior_expiration
    FROM instruments i
    JOIN futs f USING (instrument_id)
    WHERE i.asset != ?
    GROUP BY i.instrument_id
),
bars AS (
    SELECT o.instrument_id, o.ts_event, o.open, o.high, o.low, o.close, o.volume, f.expiration
    FROM ohlcv o
    JOIN futs f USING (instrument_id)
    LEFT JOIN prior_owner_cutoff p USING (instrument_id)
    WHERE o.ts_event < CAST(f.expiration AS DATE)
      AND (p.prior_expiration IS NULL OR o.ts_event >= CAST(p.prior_expiration AS DATE))
      -- Heuristic stopgap, not a real fix: some recycled instrument_ids have
      -- no second `instruments` row at all recording their prior owner (the
      -- ETL/Databento metadata history is incomplete for them), so the join
      -- above can't catch them. Confirmed case: CL picked up a sustained
      -- run of negative prices through all of 2018-2019 from one such id --
      -- no outright futures contract legitimately prices at or below zero
      -- (the one real historical exception, WTI's brief negative print in
      -- Apr 2020, isn't present in this dataset at all). Excluding non-
      -- positive prices removes that contamination without touching real
      -- data; it does NOT catch every recycled-id artifact (e.g. some
      -- positive-but-implausible low prices have also been observed) --
      -- flag any backtest results that still look suspicious.
      AND o.open > 0 AND o.high > 0 AND o.low > 0 AND o.close > 0
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

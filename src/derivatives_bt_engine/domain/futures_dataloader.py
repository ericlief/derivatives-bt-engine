from __future__ import annotations
from dataclasses import dataclass, field
import os
import time
from functools import cached_property
from typing import Optional

import duckdb
import polars as pl
from dotenv import load_dotenv

from derivatives_bt_engine.domain.base_dataloader import BaseDataLoader
from derivatives_bt_engine.utils.logger import setup_logger

load_dotenv()

# ── Infrastructure ─────────────────────────────────────────────────────────
_DEFAULT_GLOBEX_DB_PATH = '/home/dev/fin/db/globex_mdp_3.0.duckdb'

logger = setup_logger()

# Front-month roll: for each trading date, take the not-yet-expired futures
# contract for `asset` with the HIGHEST VOLUME that day (real liquidity, not
# nearest expiration) -- STICKY once picked, never reverting to an
# earlier-expiring contract.
#
# `daily` (the databento pipeline's build_db.py; formerly `ohlcv_enriched`)
# already solves `instrument_id` recycling more thoroughly than a plain join against
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
#
# Two bugs, one fix. Originally this ranked by `expiration ASC` alone --
# whichever not-yet-expired contract had the soonest expiration AMONG THOSE
# WITH A PRINTED BAR THAT DAY, recomputed independently for EVERY date with
# no memory of which contract was "front" yesterday. That produced two
# distinct failure modes, both confirmed directly against real data:
#
#   1. Near-expiry flip-flop (e.g. ZN, 2023-03-19: the Mar'23 contract has
#      no bar at all that day, so the naive pick jumps to Jun'23;
#      2023-03-20: Mar'23 prints once more and the pick reverts to it, a
#      lower price level; 2023-03-21 on: Mar'23 goes quiet for good) -- a
#      pure contract-switch artifact read by every downstream consumer as
#      if it were one instrument's real price move.
#   2. Off-month contamination (e.g. GC gold, which lists thin non-primary
#      months alongside its real active ones -- confirmed 2025-12-21: the
#      Jan'26 contract trades a token 20 lots, over a MONTH before its own
#      expiry, while Feb'26 -- the real active month -- already trades
#      5,137. "Nearest expiration, any trade that day" latched onto Jan
#      anyway, for weeks, purely because it kept printing a handful of
#      trades). This is a mistimed *initial* selection, not a reversion --
#      a monotonicity guard alone doesn't fix it, since the naive pick
#      never actually favored Feb until Jan finally went silent.
#
# Fixed by ranking on volume instead of proximity to expiration (real
# liquidity, not calendar distance, decides "front month" -- the standard
# resolution for both failure modes at once), wrapped in the same
# stickiness guarantee as before: a running max of the naive daily pick's
# own expiration (`naive_front`, `sticky_expiration` -- monotonically
# non-decreasing by construction over ts_event), re-selecting each date's
# actual bar against that sticky expiration rather than the naive one-off
# pick, so a stray one-day volume anomaly right at a genuine crossover
# can't cause a reversion either. If the sticky contract itself has no bar
# on some date, the date is simply dropped (an honest gap) rather than
# silently substituting a different, unrelated instrument's price.
_CONTINUOUS_FRONT_MONTH_SQL = """
WITH bars AS (
    SELECT instrument_id, ts_event, open, high, low, close, volume, expiration
    FROM daily
    WHERE asset = ? AND instrument_class = 'F' AND security_type = 'FUT'
      AND expiration IS NOT NULL
      AND ts_event < expiration
),
naive_ranked AS (
    SELECT *, row_number() OVER (PARTITION BY ts_event ORDER BY volume DESC, expiration ASC) AS rn
    FROM bars
),
naive_front AS (
    SELECT ts_event, expiration,
           max(expiration) OVER (ORDER BY ts_event ROWS UNBOUNDED PRECEDING) AS sticky_expiration
    FROM naive_ranked
    WHERE rn = 1
)
SELECT b.ts_event, b.open, b.high, b.low, b.close, b.volume, b.instrument_id, b.expiration
FROM bars b
JOIN naive_front f ON b.ts_event = f.ts_event AND b.expiration = f.sticky_expiration
ORDER BY b.ts_event
"""


def assert_monotonic_expiration(df: pl.DataFrame, asset: str) -> None:
    """Guards the invariant _CONTINUOUS_FRONT_MONTH_SQL's sticky-expiration
    logic is supposed to guarantee by construction: a continuous
    front-month series' `expiration` must never decrease from one row to
    the next (sorted by ts_event). Raises loudly rather than letting a
    regression (e.g. someone editing the SQL again without preserving this
    property, or a bad cached parquet built under a pre-fix version of this
    module) silently reintroduce the flip-flop-between-contracts bug this
    was written to catch. `df` must have `ts_event` and `expiration`
    columns and be usable as-is (sorted internally here, not assumed
    pre-sorted)."""
    if df.height == 0 or 'expiration' not in df.columns:
        return
    d = df.sort('ts_event')
    violations = d.filter(pl.col('expiration') < pl.col('expiration').shift(1))
    if violations.height > 0:
        raise ValueError(
            f"{asset}: continuous front-month series has {violations.height} row(s) where "
            f"expiration decreased from the prior date -- a stale contract-switch/flip-flop "
            f"bug (see _CONTINUOUS_FRONT_MONTH_SQL's docstring). First offending date: "
            f"{violations['ts_event'][0]}."
        )


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
    def _daily_processed_path(self) -> str:
        return os.path.join(self.data_dir, f"{self.asset}_daily.parquet")

    @cached_property
    def daily(self) -> pl.DataFrame:
        """Lazy load and cache the continuous front-month futures daily OHLCV series."""
        if self.use_preprocessed and os.path.exists(self._daily_processed_path):
            logger.info(f"Loading {self._daily_processed_path}")
            df = pl.read_parquet(self._daily_processed_path)
            assert_monotonic_expiration(df, self.asset)
            return df

        con = duckdb.connect(self.db_path, read_only=True)
        try:
            df = con.sql(_CONTINUOUS_FRONT_MONTH_SQL, params=[self.asset]).pl()
        finally:
            con.close()

        df = df.with_columns(pl.col('ts_event').cast(pl.Date)).sort('ts_event')
        assert_monotonic_expiration(df, self.asset)

        if self.save_preprocessed:
            df.write_parquet(self._daily_processed_path)
            logger.info(f"Saved data to {self._daily_processed_path}")

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

        daily = self.daily
        logger.info(f"Loaded {self.asset} continuous futures daily OHLCV: {len(daily)} rows")

        if self.vix_file is not None:
            vix = self.vix_data.rename({'date': 'ts_event'})
        else:
            vix = pl.DataFrame()

        result = {
            'option_chain': pl.DataFrame(),
            'underlying': daily,
            'vix': vix,
        }

        if self.vix_file is not None:
            logger.info(f"- VIX data: {len(result['vix'])} rows")

        logger.info(f"Data loading completed in {time.time() - data_loading_start:.2f} seconds")

        return result

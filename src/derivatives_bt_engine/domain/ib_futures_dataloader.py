"""
Loads continuous front-month futures OHLCV from live IB (Interactive
Brokers) continuous-contract bars, as an alternative to FuturesDataLoader's
duckdb-backed `daily` table -- shaped identically (same load_data() ->
{'option_chain', 'underlying', 'vix'} contract, same 'underlying' schema:
ts_event/open/high/low/close/volume) so Backtester can consume either
source interchangeably. Used by strats/naked_futures.py's --use-ib-data.

Fetches a fixed 3-year lookback (DEFAULT_IB_DURATION), the same
durationStr live.tsmom_rebalance._compute_signal already uses for its own
IB call -- a backtest run this way replays the same history window the
live signal computation would see today. --years ranges longer than
~3 years will only replay whatever of that window survives Backtester.
run()'s own date-range filter; that's an inherent limit of IB-backed
replay (IB's continuous-contract bars aren't meant to stand in for a full
historical database), not a bug -- use the duckdb path (naked_futures.py's
default) for full-history backtests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import polars as pl
from ib_tools.ibpysync import IBPySync

from derivatives_bt_engine.domain.base_dataloader import BaseDataLoader
from derivatives_bt_engine.domain.instruments import INSTRUMENTS, resolve_signal_symbol
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_IB_DURATION = '3 y'
DEFAULT_IB_BAR_SIZE = '1 day'


def connect_ib(ib: IBPySync, host: str, ports: list[int], client_id: int) -> None:
    """Try each candidate port once, in order. Unlike live/run_tsmom_rebalance.
    py's own connect_with_retry (which loops forever every 30s -- appropriate
    for an unattended live-rebalance cron that may race IB Gateway's own
    boot), a one-shot interactive backtest should fail fast with a clear
    error if TWS/Gateway isn't already running, not hang indefinitely."""
    last_exc: Optional[Exception] = None
    for port in ports:
        try:
            ib.connect(host, port, client_id)
            logger.info(f"Connected to IB at {host}:{port} client_id={client_id}")
            return
        except Exception as exc:
            logger.warning(f"Cannot connect to IB at {host}:{port} ({exc})")
            last_exc = exc
    raise RuntimeError(
        f"Could not connect to IB on any of {ports} at {host} -- is TWS/IB Gateway running? "
        f"Last error: {last_exc}"
    )


@dataclass
class IBFuturesDataLoader(BaseDataLoader):
    """Loads one symbol's continuous front-month futures OHLCV from live IB.

    `asset` must be the raw traded symbol (e.g. 'MES', not naked_futures.py's
    duckdb-oriented resolve_price_symbol(symbol) result) -- signal-symbol
    substitution for thin/short-history contracts happens internally via
    resolve_signal_symbol (signal_symbol > ib_symbol > symbol; deliberately
    excludes db_symbol, which is duckdb-only and not necessarily a valid IB
    ticker -- see domain/instruments.py's docstring), mirroring
    live.tsmom_rebalance._compute_signal's own resolution exactly so a
    backtest run with --use-ib-data borrows history the same way live
    signal computation does.

    `ib` must already be connected (see connect_ib above) -- this loader
    never manages the connection lifecycle itself, since a multi-symbol run
    fetches every symbol over one shared connection (IB's historical-data
    pacing limits make a connection-per-symbol/-worker approach unsafe)."""

    asset: str
    ib: IBPySync
    data_dir: str = "."
    vix_file: Optional[str] = None
    use_preprocessed: bool = False
    save_preprocessed: bool = False
    duration: str = DEFAULT_IB_DURATION
    bar_size: str = DEFAULT_IB_BAR_SIZE

    @cached_property
    def daily(self) -> pl.DataFrame:
        """Lazy fetch and cache this symbol's continuous front-month IB bars."""
        instr = {**INSTRUMENTS.get(self.asset, {}), 'symbol': self.asset}
        signal_symbol = resolve_signal_symbol(instr)
        if signal_symbol != self.asset:
            logger.info(f"{self.asset}: borrowing {signal_symbol}'s continuous IB history")

        cont = IBPySync.cont_future(signal_symbol, exchange=instr.get('exchange', 'CME'))
        self.ib.qualify_contracts(cont)
        bars = self.ib.get_historical_bars(cont, duration=self.duration, bar_size=self.bar_size)
        if bars is None or bars.height == 0:
            raise RuntimeError(f"No IB historical bars returned for {self.asset} ({signal_symbol})")

        return (
            bars.rename({'date': 'ts_event'})
            .with_columns(pl.col('ts_event').cast(pl.Date))
            .select(['ts_event', 'open', 'high', 'low', 'close', 'volume'])
            .sort('ts_event')
        )

    def load_data(self) -> dict:
        """Same contract as FuturesDataLoader.load_data() -- see its own
        docstring for why 'underlying' carries the continuous series and
        'option_chain' is an empty placeholder."""
        data_loading_start = time.time()

        daily = self.daily
        logger.info(f"Loaded {self.asset} continuous futures daily OHLCV via IB: {len(daily)} rows")

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

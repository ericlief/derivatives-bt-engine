"""Load continuous and dated futures history from Interactive Brokers.

Loads continuous front-month futures OHLCV from live IB (Interactive
Brokers) continuous-contract bars, as an alternative to FuturesDataLoader's
duckdb-backed `daily` table -- shaped identically (same load_data() ->
{'option_chain', 'underlying', 'vix'} contract, same 'underlying' schema:
ts_event/open/high/low/close/volume) so Backtester can consume either
source interchangeably. Used by strats/naked_futures.py's --use-ib-data.

The roll-cycle helper separately downloads raw dated-contract histories for
the prior and current calendar years. Those overlapping histories retain
contract identity and are inputs for a locally controlled continuous-futures
stitch; they do not use IB's opaque continuous-contract adjustment.

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

import calendar
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Optional

import duckdb
import polars as pl
from ib_tools.ibpysync import IBPySync

from derivatives_bt_engine.data.base_dataloader import BaseDataLoader
from derivatives_bt_engine.calculations.instruments import (
    CME_MONTH_LETTERS,
    INSTRUMENTS,
    resolve_signal_symbol,
)
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH as DEFAULT_REFERENCE_DB_PATH,
)
from derivatives_bt_engine.data.pysystemtrade_ib import (
    DEFAULT_IB_MAPPING_PATH as DEFAULT_BROKER_MAPPING_PATH,
    load_pysystemtrade_ib_mapping as load_broker_mapping,
)
from derivatives_bt_engine.logging_config import setup_logger

logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_IB_DURATION = '3 y'
DEFAULT_IB_BAR_SIZE = '1 day'
DEFAULT_IB_DATED_CONTRACT_DURATION = '1 Y'

_LME_MONTHLY_INSTRUMENTS = {
    'ALUMINIUM_LME',
    'COPPER_LME',
    'LEAD_LME',
    'NICKEL_LME',
    'TIN_LME',
    'ZINC_LME',
}
_EUREX_DAILY_INSTRUMENTS = {'MSCIWORLD', 'MSCIASIA'}


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


def _format_ib_multiplier(value: float) -> str:
    """Format a mapped IB multiplier without an unnecessary decimal suffix."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("IB multiplier must be finite and positive")
    return str(int(value)) if value.is_integer() else format(value, "g")


def _load_roll_config(
    instrument_code: str,
    db_path: Path | str,
) -> tuple[str, int]:
    """Return the held-contract cycle and roll offset for one instrument."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT hold_roll_cycle, roll_offset_days
            FROM raw.roll_config
            WHERE instrument_code = ?
            """,
            [instrument_code],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"No roll configuration for {instrument_code}")
    return str(row[0]), int(row[1])


def _is_monthly_contract_symbol(
    instrument_code: str,
    local_symbol: str,
) -> bool:
    """Identify monthly contracts for products with weekly or daily expiries."""
    if instrument_code == 'VIX':
        if re.fullmatch(r"VX\d\d[A-Z]\d", local_symbol):
            return False
        return re.fullmatch(r"VX[A-Z]\d", local_symbol) is not None
    if instrument_code in _EUREX_DAILY_INSTRUMENTS:
        return not local_symbol.endswith('D')
    if instrument_code in _LME_MONTHLY_INSTRUMENTS:
        try:
            day = int(local_symbol[-2:])
        except ValueError:
            return False
        return 15 <= day <= 21
    if instrument_code == 'GAS_US':
        if re.fullmatch(r"HHW[A-Z]\d{3}", local_symbol):
            return False
        return re.fullmatch(r"NG[A-Z]\d", local_symbol) is not None
    raise ValueError(
        f"{instrument_code}: IgnoreWeekly is set but no monthly-contract "
        "resolver is defined"
    )


def _select_contract_detail(
    details: list,
    *,
    instrument_code: str,
    contract_month: str,
    ignore_weekly: bool,
):
    """Return the unique IB detail matching a configured contract month."""
    matching = [
        detail
        for detail in details
        if str(getattr(detail, 'contractMonth', ''))[:6] == contract_month
    ]
    if ignore_weekly and len(matching) > 1:
        matching = [
            detail
            for detail in matching
            if _is_monthly_contract_symbol(
                instrument_code,
                str(detail.contract.localSymbol),
            )
        ]
    if not matching:
        return None
    if len(matching) != 1:
        local_symbols = [str(detail.contract.localSymbol) for detail in matching]
        raise ValueError(
            f"{instrument_code} {contract_month}: expected one IB contract, "
            f"found {local_symbols}"
        )
    return matching[0]


def _contract_expiration_date(detail, contract_month: str) -> date:
    """Return the best dated expiry supplied by IB for a resolved contract."""
    candidates = (
        str(getattr(detail, 'realExpirationDate', '')),
        str(detail.contract.lastTradeDateOrContractMonth),
    )
    for value in candidates:
        if len(value) >= 8 and value[:8].isdigit():
            return datetime.strptime(value[:8], '%Y%m%d').date()
    year = int(contract_month[:4])
    month = int(contract_month[4:6])
    return date(year, month, calendar.monthrange(year, month)[1])


def load_roll_cycle_ib_contract_history(
    ib: IBPySync,
    instrument_code: str,
    *,
    as_of: date | None = None,
    prior_years: int = 1,
    duration: str = DEFAULT_IB_DATED_CONTRACT_DURATION,
    bar_size: str = DEFAULT_IB_BAR_SIZE,
    what_to_show: str = 'TRADES',
    use_rth: bool = True,
    db_path: Path | str = DEFAULT_REFERENCE_DB_PATH,
    mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH,
) -> pl.DataFrame:
    """Download and concatenate dated IB histories on a configured roll cycle.

    Parameters
    ----------
    ib
        Connected synchronous IB client.
    instrument_code
        Instrument identifier, such as ``GOLD_micro``.
    as_of
        Date defining the current contract year. Defaults to today.
    prior_years
        Number of complete calendar years before ``as_of.year`` to include.
        The default requests the prior and current contract years.
    duration
        IB history duration requested separately for every resolved contract.
    bar_size, what_to_show, use_rth
        Values forwarded to ``IBPySync.get_historical_bars``.
    db_path, mapping_path
        Reference roll configuration and broker mapping inputs.

    Returns
    -------
    polars.DataFrame
        Overlapping dated-contract bars concatenated by ``trade_date`` and
        ``contract_month``. Contract IDs, local symbols, exact expiries, and
        Roll metadata are retained so a later step can select the held
        contract and construct roll-neutral increments.

    Notes
    -----
    This helper deliberately requests dated ``FUT`` contracts rather than
    ``CONTFUT``. For expired contracts, the one-year request ends just after
    that contract's expiry; otherwise it ends at the latest available bar.
    Missing unavailable contract months are logged and skipped.
    """
    if prior_years < 0:
        raise ValueError("prior_years must be nonnegative")
    effective_as_of = as_of or date.today()
    mapping = load_broker_mapping(mapping_path).filter(
        pl.col('instrument_code') == instrument_code
    )
    if mapping.height != 1:
        raise KeyError(f"No unique broker mapping for {instrument_code}")
    broker = mapping.row(0, named=True)
    hold_roll_cycle, roll_offset_days = _load_roll_config(
        instrument_code,
        db_path,
    )
    invalid_cycle = set(hold_roll_cycle) - set(CME_MONTH_LETTERS)
    if invalid_cycle:
        raise ValueError(
            f"{instrument_code}: invalid hold-roll cycle letters "
            f"{sorted(invalid_cycle)}"
        )

    years = range(effective_as_of.year - prior_years, effective_as_of.year + 1)
    contract_months = [
        f"{year:04d}{CME_MONTH_LETTERS[month_letter]:02d}"
        for year in years
        for month_letter in hold_roll_cycle
    ]
    ib_symbol = str(broker['ib_symbol'])
    exchange = str(broker['ib_exchange'])
    currency = str(broker['ib_currency'])
    multiplier = _format_ib_multiplier(float(broker['ib_multiplier']))
    ignore_weekly = bool(broker['ignore_weekly'])
    logger.info(
        "ib_dated_history start instrument=%s ib_symbol=%s exchange=%s "
        "hold_roll_cycle=%s contract_months=%s duration=%s",
        instrument_code,
        ib_symbol,
        exchange,
        hold_roll_cycle,
        contract_months,
        duration,
    )

    histories: list[pl.DataFrame] = []
    for contract_month in contract_months:
        pattern = IBPySync.future(
            ib_symbol,
            exchange=exchange,
            currency=currency,
            expiration=contract_month,
            multiplier=multiplier,
        )
        pattern.includeExpired = True
        try:
            details = ib.req_contract_details(pattern)
        except Exception as exc:
            logger.warning(
                "ib_dated_history contract_lookup_failed instrument=%s "
                "contract_month=%s error=%s",
                instrument_code,
                contract_month,
                exc,
            )
            continue
        detail = _select_contract_detail(
            details,
            instrument_code=instrument_code,
            contract_month=contract_month,
            ignore_weekly=ignore_weekly,
        )
        if detail is None:
            logger.debug(
                "ib_dated_history contract_unavailable instrument=%s "
                "contract_month=%s",
                instrument_code,
                contract_month,
            )
            continue

        contract = detail.contract
        contract.includeExpired = True
        expiration = _contract_expiration_date(detail, contract_month)
        end_date = (
            (expiration + timedelta(days=1)).strftime('%Y%m%d 23:59:59')
            if expiration < effective_as_of
            else ''
        )
        try:
            bars = ib.get_historical_bars(
                contract,
                end_date=end_date,
                duration=duration,
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
            )
        except Exception as exc:
            logger.warning(
                "ib_dated_history bars_failed instrument=%s "
                "contract_month=%s contract_id=%s error=%s",
                instrument_code,
                contract_month,
                contract.conId,
                exc,
            )
            continue
        if bars is None or bars.is_empty():
            logger.debug(
                "ib_dated_history bars_empty instrument=%s contract_month=%s "
                "contract_id=%s",
                instrument_code,
                contract_month,
                contract.conId,
            )
            continue
        if 'date' not in bars.columns:
            raise ValueError(
                f"{instrument_code} {contract_month}: IB bars missing date column"
            )
        histories.append(
            bars.rename({'date': 'trade_date'}).with_columns(
                pl.col('trade_date').cast(pl.Date),
                pl.lit('ib').alias('source'),
                pl.lit(instrument_code).alias('instrument_code'),
                pl.lit(ib_symbol).alias('ib_symbol'),
                pl.lit(exchange).alias('ib_exchange'),
                pl.lit(currency).alias('ib_currency'),
                pl.lit(float(broker['ib_multiplier'])).alias('ib_multiplier'),
                pl.lit(float(broker['price_magnifier'])).alias('price_magnifier'),
                pl.lit(hold_roll_cycle).alias('hold_roll_cycle'),
                pl.lit(roll_offset_days).alias('roll_offset_days'),
                pl.lit(contract_month).alias('contract_month'),
                pl.lit(expiration).alias('expiration'),
                pl.lit(int(contract.conId)).alias('ib_contract_id'),
                pl.lit(str(contract.localSymbol)).alias('ib_local_symbol'),
                pl.lit(str(contract.tradingClass)).alias('ib_trading_class'),
            )
        )

    if not histories:
        raise RuntimeError(
            f"No IB dated-contract history returned for {instrument_code}"
        )
    result = (
        pl.concat(histories, how='diagonal_relaxed')
        .unique(
            subset=['ib_contract_id', 'trade_date'],
            keep='last',
            maintain_order=True,
        )
        .sort('trade_date', 'contract_month')
    )
    logger.info(
        "ib_dated_history complete instrument=%s contracts=%d rows=%d "
        "start=%s end=%s",
        instrument_code,
        result.get_column('ib_contract_id').n_unique(),
        result.height,
        result.get_column('trade_date').min(),
        result.get_column('trade_date').max(),
    )
    return result


@dataclass
class IBFuturesDataLoader(BaseDataLoader):
    """Loads one symbol's continuous front-month futures OHLCV from live IB.

    `asset` must be the raw traded symbol (e.g. 'MES', not naked_futures.py's
    duckdb-oriented resolve_price_symbol(symbol) result) -- signal-symbol
    substitution for thin/short-history contracts happens internally via
    resolve_signal_symbol (signal_symbol > ib_symbol > symbol; deliberately
    excludes db_symbol, which is duckdb-only and not necessarily a valid IB
    ticker -- see calculations/instruments.py's docstring), mirroring
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

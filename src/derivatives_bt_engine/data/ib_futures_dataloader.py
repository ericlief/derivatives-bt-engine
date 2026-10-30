"""Load continuous and dated futures history from Interactive Brokers.

Loads continuous front-month futures OHLCV from live IB (Interactive
Brokers) continuous-contract bars, as an alternative to FuturesDataLoader's
duckdb-backed `daily` table -- shaped identically (same load_data() ->
{'option_chain', 'underlying', 'vix'} contract, same 'underlying' schema:
ts_event/open/high/low/close/volume) so Backtester can consume either
source interchangeably. Used by strats/naked_futures.py's --use-ib-data.

The roll-cycle helper separately downloads raw dated-contract histories from
the configured priced cycle, including the contracts required by the carry
offset. The forward horizon follows the held-contract roll offset. Those raw
observations retain contract identity and feed locally controlled held, carry,
and continuous streams; they do not use IB's opaque adjustment.

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
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field, replace
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
from derivatives_bt_engine.calculations.roll_policy import ContractRollPolicy
from derivatives_bt_engine.data.futures_history import (
    DEFAULT_FUTURES_CACHE_ROOT,
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH as DEFAULT_REFERENCE_DB_PATH,
    HISTORY_SCHEMA_VERSION,
    FuturesHistory,
    GlobexHistoryProvider,
    PysystemtradeHistoryProvider as ReferenceHistoryProvider,
    build_contract_history_streams,
    futures_history_parquet_cache_exists,
    read_futures_history_parquet_cache,
    select_calendar_contract_observations,
    stitch_futures_histories,
    write_futures_history_parquet_cache,
)
from derivatives_bt_engine.data.ib_futures_history_store import (
    DEFAULT_IB_FUTURES_HISTORY_DB_PATH,
    IBFuturesHistoryStore,
)
from derivatives_bt_engine.data.pysystemtrade_ib import (
    DEFAULT_IB_MAPPING_PATH as DEFAULT_BROKER_MAPPING_PATH,
    load_pysystemtrade_ib_instruments,
    load_pysystemtrade_ib_mapping as load_broker_mapping,
)
from derivatives_bt_engine.logging_config import setup_logger

logger = setup_logger()

DEFAULT_MAX_SOURCE_GAP_DAYS = 31

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
    pst_instr_code: str,
    db_path: Path | str,
) -> tuple[str, int, int, str]:
    """Return hold, timing, carry, and priced-cycle roll configuration."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT
                hold_roll_cycle,
                roll_offset_days,
                carry_offset,
                priced_roll_cycle
            FROM raw.roll_config
            WHERE instrument_code = ?
            """,
            [pst_instr_code],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"No roll configuration for PST instrument {pst_instr_code}")
    return str(row[0]), int(row[1]), int(row[2]), str(row[3])


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


def _duration_days(duration: str) -> int | None:
    """Return approximate calendar coverage for an IB duration string."""
    match = re.fullmatch(r"\s*(\d+)\s*([YMWD])\s*", duration.upper())
    if match is None:
        return None
    amount = int(match.group(1))
    unit_days = {"Y": 365, "M": 30, "W": 7, "D": 1}
    return amount * unit_days[match.group(2)]


def _roll_aware_duration(duration: str, roll_offset_days: int) -> str:
    """Expand dated-contract coverage enough to reach the policy roll date."""
    required_days = max(365, abs(roll_offset_days))
    supplied_days = _duration_days(duration)
    if supplied_days is not None and supplied_days >= required_days:
        return duration
    return f"{math.ceil(required_days / 365)} Y"


def _shift_contract_month(
    contract_month: str,
    priced_roll_cycle: str,
    offset: int,
) -> str:
    """Shift a YYYYMM contract by positions in the configured priced cycle."""
    year = int(contract_month[:4])
    month = int(contract_month[4:6])
    cycle_months = [CME_MONTH_LETTERS[letter] for letter in priced_roll_cycle]
    try:
        cycle_index = cycle_months.index(month)
    except ValueError as exc:
        raise ValueError(
            f"contract month {contract_month} is absent from priced cycle "
            f"{priced_roll_cycle}"
        ) from exc
    shifted_index = cycle_index + offset
    shifted_year = year + shifted_index // len(cycle_months)
    shifted_month = cycle_months[shifted_index % len(cycle_months)]
    return f"{shifted_year:04d}{shifted_month:02d}"


def _build_configured_carry_frame(
    raw: pl.DataFrame,
    selected: pl.DataFrame,
    *,
    priced_roll_cycle: str,
    carry_offset: int,
) -> pl.DataFrame:
    """Join configured carry-contract marks to selected held-contract rows."""
    if selected.is_empty():
        return pl.DataFrame()
    carry_months = [
        _shift_contract_month(
            str(contract_month),
            priced_roll_cycle,
            carry_offset,
        )
        for contract_month in selected.get_column("contract_month").to_list()
    ]
    current = selected.select(
        "date",
        "source_timestamp",
        pl.col("mark_price").alias("current_price"),
        (pl.col("contract_month").cast(pl.String) + "00").alias(
            "current_contract"
        ),
    ).with_columns(
        pl.Series("carry_contract_month", carry_months, dtype=pl.String)
    )
    carry_marks = raw.select(
        "date",
        pl.col("contract_month")
        .cast(pl.String)
        .alias("carry_contract_month"),
        pl.col("mark_price").alias("carry_price"),
    ).unique(
        subset=["date", "carry_contract_month"],
        keep="last",
        maintain_order=True,
    )
    return (
        current.join(
            carry_marks,
            on=["date", "carry_contract_month"],
            how="left",
        )
        .with_columns(
            (pl.col("carry_contract_month") + "00").alias("carry_contract"),
            pl.when(pl.col("carry_price").is_null())
            .then(pl.lit("missing_carry_mark"))
            .otherwise(pl.lit(""))
            .alias("quality_flag"),
        )
        .select(
            "date",
            "source_timestamp",
            "current_price",
            "current_contract",
            "carry_price",
            "carry_contract",
            "quality_flag",
        )
        .sort("date")
    )


def load_roll_cycle_ib_contract_history(
    ib: IBPySync | None,
    *,
    pst_instr_code: str,
    as_of: date | None = None,
    prior_years: int = 1,
    duration: str = DEFAULT_IB_DATED_CONTRACT_DURATION,
    bar_size: str = DEFAULT_IB_BAR_SIZE,
    what_to_show: str = 'TRADES',
    use_rth: bool = True,
    db_path: Path | str = DEFAULT_REFERENCE_DB_PATH,
    mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH,
    history_db_path: Path | str = DEFAULT_IB_FUTURES_HISTORY_DB_PATH,
    update_history: bool = True,
) -> pl.DataFrame:
    """Download priced-cycle IB histories for held and carry construction.

    Parameters
    ----------
    ib
        Connected synchronous IB client when ``update_history`` is true.
        Stored-only reads may pass ``None``.
    pst_instr_code
        PST instrument code owning the IB contract mapping and roll policy.
    as_of
        Date defining the current contract year. Defaults to today.
    prior_years
        Number of complete calendar years before ``as_of.year`` to include.
        The default starts with the prior and current priced-contract years.
        The forward contract horizon follows the configured hold-roll offset,
        and carry companions follow the configured priced-cycle offset.
    duration
        IB history duration requested separately for every resolved contract.
        It is expanded to whole years when the configured roll offset requires
        an earlier part of the contract's trading life.
    bar_size, what_to_show, use_rth
        Values forwarded to ``IBPySync.get_historical_bars``.
    db_path, mapping_path
        Reference roll configuration and broker mapping inputs.
    history_db_path
        Dedicated DuckDB containing raw IB futures contracts and bars.
    update_history
        When true, fetch missing/current contract histories from IB and upsert
        them before reading. When false, perform no broker request and require
        sufficient rows to already exist in ``history_db_path``.

    Returns
    -------
    polars.DataFrame
        Overlapping dated-contract bars concatenated by ``date`` and
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
    if update_history and ib is None:
        raise ValueError("ib is required when update_history is true")
    effective_as_of = as_of or date.today()
    mapping = load_broker_mapping(mapping_path).filter(
        pl.col('instrument_code') == pst_instr_code
    )
    if mapping.height != 1:
        raise KeyError(f"No unique broker mapping for PST instrument {pst_instr_code}")
    broker = mapping.row(0, named=True)
    (
        hold_roll_cycle,
        roll_offset_days,
        carry_offset,
        priced_roll_cycle,
    ) = _load_roll_config(
        pst_instr_code,
        db_path,
    )
    if not hold_roll_cycle:
        raise ValueError(f"{pst_instr_code}: hold-roll cycle must not be empty")
    invalid_cycle = set(hold_roll_cycle) - set(CME_MONTH_LETTERS)
    if invalid_cycle:
        raise ValueError(
            f"{pst_instr_code}: invalid hold-roll cycle letters "
            f"{sorted(invalid_cycle)}"
        )
    invalid_priced_cycle = set(priced_roll_cycle) - set(CME_MONTH_LETTERS)
    if invalid_priced_cycle:
        raise ValueError(
            f"{pst_instr_code}: invalid priced-roll cycle letters "
            f"{sorted(invalid_priced_cycle)}"
        )
    if not set(hold_roll_cycle).issubset(set(priced_roll_cycle)):
        raise ValueError(
            f"{pst_instr_code}: hold-roll cycle {hold_roll_cycle} is not a "
            f"subset of priced-roll cycle {priced_roll_cycle}"
        )

    effective_duration = _roll_aware_duration(duration, roll_offset_days)
    # Far-deferred STIR policies can hold contracts several years before
    # expiry. Include contract years through that forward horizon; otherwise
    # no requested contract can be eligible on current observation dates.
    forward_year = (
        effective_as_of + timedelta(days=abs(roll_offset_days))
    ).year
    years = range(
        effective_as_of.year - prior_years,
        forward_year + 1,
    )
    base_contract_months = [
        f"{year:04d}{CME_MONTH_LETTERS[month_letter]:02d}"
        for year in years
        for month_letter in priced_roll_cycle
    ]
    # A calendar policy can roll out of the last contract in the requested
    # forward year. Append its first successor rather than every contract in
    # another year; the latter would create avoidable IB security-definition
    # failures for contracts not listed yet.
    base_contract_months.append(
        f"{forward_year + 1:04d}"
        f"{CME_MONTH_LETTERS[hold_roll_cycle[0]]:02d}"
    )
    # Acquire the raw priced universe independently from the held path. Add
    # each configured carry companion, including any contract just outside the
    # base year range, so later policy construction never invents carry data.
    contract_months = sorted({
        *base_contract_months,
        *(
            _shift_contract_month(
                contract_month,
                priced_roll_cycle,
                carry_offset,
            )
            for contract_month in base_contract_months
        ),
    })
    ib_symbol = str(broker['ib_symbol'])
    exchange = str(broker['ib_exchange'])
    currency = str(broker['ib_currency'])
    multiplier = _format_ib_multiplier(float(broker['ib_multiplier']))
    ignore_weekly = bool(broker['ignore_weekly'])
    store = IBFuturesHistoryStore(history_db_path)
    complete_months = store.complete_contract_months(
        pst_instr_code=pst_instr_code,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=use_rth,
        duration=effective_duration,
    )
    logger.info(
        "ib_dated_history start instrument=%s ib_symbol=%s exchange=%s "
        "hold_roll_cycle=%s priced_roll_cycle=%s carry_offset=%s "
        "contract_months=%s duration=%s requested_duration=%s",
        pst_instr_code,
        ib_symbol,
        exchange,
        hold_roll_cycle,
        priced_roll_cycle,
        carry_offset,
        contract_months,
        effective_duration,
        duration,
    )

    for contract_month in contract_months if update_history else []:
        # A successful expired-contract request is immutable at the raw-source
        # boundary. Current contracts remain refreshable on each cache miss.
        if contract_month in complete_months:
            logger.debug(
                "ib_dated_history raw_cache_complete instrument=%s "
                "contract_month=%s",
                pst_instr_code,
                contract_month,
            )
            continue
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
                pst_instr_code,
                contract_month,
                exc,
            )
            continue
        detail = _select_contract_detail(
            details,
            instrument_code=pst_instr_code,
            contract_month=contract_month,
            ignore_weekly=ignore_weekly,
        )
        if detail is None:
            logger.debug(
                "ib_dated_history contract_unavailable instrument=%s "
                "contract_month=%s",
                pst_instr_code,
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
                duration=effective_duration,
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
            )
        except Exception as exc:
            store.log_fetch_failure(
                pst_instr_code=pst_instr_code,
                contract_month=contract_month,
                duration=effective_duration,
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
                requested_end=end_date,
                error=str(exc),
                con_id=int(contract.conId),
            )
            logger.warning(
                "ib_dated_history bars_failed instrument=%s "
                "contract_month=%s contract_id=%s error=%s",
                pst_instr_code,
                contract_month,
                contract.conId,
                exc,
            )
            continue
        if bars is None or bars.is_empty():
            logger.debug(
                "ib_dated_history bars_empty instrument=%s contract_month=%s "
                "contract_id=%s",
                pst_instr_code,
                contract_month,
                contract.conId,
            )
            continue
        if 'date' not in bars.columns:
            raise ValueError(
                f"PST instrument {pst_instr_code} {contract_month}: "
                "IB bars missing date column"
            )
        store.upsert_contract_and_bars(
            contract={
                'con_id': int(contract.conId),
                'pst_instr_code': pst_instr_code,
                'ib_symbol': ib_symbol,
                'exchange': exchange,
                'currency': currency,
                'multiplier': float(broker['ib_multiplier']),
                'price_magnifier': float(broker['price_magnifier']),
                'contract_month': contract_month,
                'expiration': expiration,
                'local_symbol': str(contract.localSymbol),
                'trading_class': str(contract.tradingClass),
            },
            bars=bars,
            duration=effective_duration,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_rth=use_rth,
            requested_end=end_date,
            complete_contract=expiration < effective_as_of,
        )

    result = store.load_bars(
        pst_instr_code=pst_instr_code,
        contract_months=contract_months,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=use_rth,
    )
    if result.is_empty():
        raise RuntimeError(
            "No raw IB dated-contract history is available for PST instrument "
            f"{pst_instr_code} in {Path(history_db_path).expanduser()}"
        )
    result = (
        result.with_columns(
            pl.lit(hold_roll_cycle).alias('hold_roll_cycle'),
            pl.lit(roll_offset_days).alias('roll_offset_days'),
            pl.lit(carry_offset).alias('carry_offset'),
            pl.lit(priced_roll_cycle).alias('priced_roll_cycle'),
        )
        .unique(
            subset=['ib_contract_id', 'date'],
            keep='last',
            maintain_order=True,
        )
        .sort('date', 'contract_month')
    )
    logger.info(
        "ib_dated_history complete pst_instr_code=%s contracts=%d rows=%d "
        "start=%s end=%s",
        pst_instr_code,
        result.get_column('ib_contract_id').n_unique(),
        result.height,
        result.get_column('date').min(),
        result.get_column('date').max(),
    )
    return result


def build_ib_contract_history(
    contract_history: pl.DataFrame,
) -> FuturesHistory:
    """Build canonical daily history from overlapping IB contract bars.

    The input is the dataframe returned by
    :func:`load_roll_cycle_ib_contract_history`. The configured hold cycle and
    roll offset select one dated contract per session; the priced cycle and
    carry offset select its carry companion. Contract-consistent daily changes
    then produce signal, mark, carry, and additive-price streams.

    Parameters
    ----------
    contract_history
        Overlapping daily rows for every downloaded dated contract.

    Returns
    -------
    FuturesHistory
        One-row-per-session IB history with actual contract marks and locally
        constructed continuous signal paths.
    """
    required = {
        'date',
        'close',
        'volume',
        'contract_month',
        'expiration',
        'ib_contract_id',
        'pst_instr_code',
        'hold_roll_cycle',
        'roll_offset_days',
        'carry_offset',
        'priced_roll_cycle',
    }
    missing = required - set(contract_history.columns)
    if missing:
        raise ValueError(
            f"IB contract history missing columns: {sorted(missing)}"
        )
    if contract_history.is_empty():
        raise ValueError("IB contract history must not be empty")

    identity_columns = (
        'pst_instr_code',
        'hold_roll_cycle',
        'roll_offset_days',
        'carry_offset',
        'priced_roll_cycle',
    )
    identities = contract_history.select(identity_columns).unique()
    if identities.height != 1:
        raise ValueError(
            "IB contract history must contain one instrument and roll policy"
        )
    identity = identities.row(0, named=True)
    pst_instr_code = str(identity['pst_instr_code'])
    carry_offset = int(identity['carry_offset'])
    priced_roll_cycle = str(identity['priced_roll_cycle'])
    policy = ContractRollPolicy(
        policy_id=f"configured_{pst_instr_code}",
        hold_roll_cycle=str(identity['hold_roll_cycle']),
        roll_offset_days=int(identity['roll_offset_days']),
        source="reference_config",
    )

    # Preserve the within-contract close and IB contract month before the
    # roll-policy selection. Contract-cycle membership comes from YYYYMM,
    # while the actual expiry date controls the cutoff; these differ for
    # products such as December WTI crude, which expires in November.
    raw = (
        contract_history.unique(
            subset=['ib_contract_id', 'date'],
            keep='last',
            maintain_order=True,
        )
        .sort('ib_contract_id', 'date')
        .with_columns(
            pl.col('date')
            .cast(pl.Datetime('us'))
            .alias('source_timestamp'),
            pl.col('close').cast(pl.Float64).alias('mark_price'),
            pl.col('ib_contract_id').cast(pl.String).alias('instrument_id'),
            pl.col('close')
            .cast(pl.Float64)
            .shift(1)
            .over('ib_contract_id')
            .alias('previous_contract_close'),
        )
        .sort('date', 'expiration')
    )
    selected = select_calendar_contract_observations(raw, policy)
    if selected.is_empty():
        raise ValueError(
            f"roll policy selected no IB rows for PST instrument {pst_instr_code}"
        )
    signal, marks, _, panama = build_contract_history_streams(selected)
    carry = _build_configured_carry_frame(
        raw,
        selected,
        priced_roll_cycle=priced_roll_cycle,
        carry_offset=carry_offset,
    )
    return FuturesHistory(
        source='ib',
        instrument_code=pst_instr_code,
        schema_version=HISTORY_SCHEMA_VERSION,
        signal=signal,
        marks=marks,
        carry=carry,
        panama=panama,
        metadata={
            'hold_roll_cycle': policy.hold_roll_cycle,
            'roll_offset_days': policy.roll_offset_days,
            'carry_offset': carry_offset,
            'priced_roll_cycle': priced_roll_cycle,
            'contracts': contract_history.get_column(
                'ib_contract_id'
            ).n_unique(),
            'raw_rows': contract_history.height,
            'selected_rows': selected.height,
        },
    )


@dataclass
class IBHistoryProvider:
    """Load canonical IB history through the raw dated-contract store.

    The provider implements the common ``load(instrument_code)`` history
    interface. ``instrument_code`` is the PST code owning broker identity and
    roll configuration. Raw bars are read from the isolated DuckDB after an
    optional broker update; no adjusted or stitched levels are persisted there.
    """

    ib: IBPySync | None
    as_of: date | None = None
    prior_years: int = 1
    duration: str = DEFAULT_IB_DATED_CONTRACT_DURATION
    bar_size: str = DEFAULT_IB_BAR_SIZE
    what_to_show: str = 'TRADES'
    use_rth: bool = True
    reference_db_path: Path | str = DEFAULT_REFERENCE_DB_PATH
    mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH
    history_db_path: Path | str = DEFAULT_IB_FUTURES_HISTORY_DB_PATH
    update_history: bool = True
    contract_history: pl.DataFrame | None = field(
        init=False,
        default=None,
        repr=False,
    )

    def load(self, instrument_code: str) -> FuturesHistory:
        """Load raw bars for one PST code and build its canonical streams."""
        self.contract_history = load_roll_cycle_ib_contract_history(
            self.ib,
            pst_instr_code=instrument_code,
            as_of=self.as_of,
            prior_years=self.prior_years,
            duration=self.duration,
            bar_size=self.bar_size,
            what_to_show=self.what_to_show,
            use_rth=self.use_rth,
            db_path=self.reference_db_path,
            mapping_path=self.mapping_path,
            history_db_path=self.history_db_path,
            update_history=self.update_history,
        )
        return build_ib_contract_history(self.contract_history)


def _history_input_fingerprint(path: Path | str) -> dict[str, object]:
    """Return stable local-file identity for a stitched-cache input."""
    resolved = Path(path).expanduser().resolve()
    stat = resolved.stat()
    return {
        'path': str(resolved),
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
    }


@dataclass
class IBStitchedHistoryProvider:
    """Load reference and IB history, using Globex when it is available.

    Raw IB observations live in a dedicated DuckDB through
    :class:`IBHistoryProvider`. The final duplicate-free ``FuturesHistory`` and
    its overlapping IB audit rows are cached as one Parquet bundle. A cache hit
    therefore performs no IB request and no restitching. Instruments absent
    from the Globex database stitch directly from the long reference history
    into IB history.
    """

    ib: IBPySync | None
    globex_asset: str | None
    # Optional mapped history source for execution overlays (e.g. MGC -> GC).
    signal_symbol: str | None = None
    as_of: date | None = None
    prior_years: int = 1
    duration: str = DEFAULT_IB_DATED_CONTRACT_DURATION
    bar_size: str = DEFAULT_IB_BAR_SIZE
    what_to_show: str = 'TRADES'
    use_rth: bool = True
    reference_db_path: Path | str = DEFAULT_REFERENCE_DB_PATH
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH
    mapping_path: Path | str = DEFAULT_BROKER_MAPPING_PATH
    history_db_path: Path | str = DEFAULT_IB_FUTURES_HISTORY_DB_PATH
    cache_root: Path | str = DEFAULT_FUTURES_CACHE_ROOT
    use_cache: bool = True
    save_cache: bool = True
    update_history: bool = True
    max_source_gap_days: int = DEFAULT_MAX_SOURCE_GAP_DAYS
    contract_history: pl.DataFrame | None = field(
        init=False,
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Validate the permitted direct-source gap used during stitching."""
        if self.max_source_gap_days < 0:
            raise ValueError("max_source_gap_days must be nonnegative")

    def _cache_directory(self, symbol: str, pst_instr_code: str) -> Path:
        """Return a versioned cache directory for all stitched-view inputs."""
        effective_as_of = self.as_of or date.today()
        identity = {
            'history_schema_version': HISTORY_SCHEMA_VERSION,
            'symbol': symbol,
            'pst_instr_code': pst_instr_code,
            'signal_symbol': self.signal_symbol or symbol,
            'globex_asset': self.globex_asset,
            'as_of': effective_as_of.isoformat(),
            'prior_years': self.prior_years,
            'duration': self.duration,
            'bar_size': self.bar_size,
            'what_to_show': self.what_to_show,
            'use_rth': self.use_rth,
            'max_source_gap_days': self.max_source_gap_days,
            'reference_db': _history_input_fingerprint(self.reference_db_path),
            'globex_db': (
                _history_input_fingerprint(self.globex_db_path)
                if self.globex_asset is not None
                else None
            ),
            'mapping': _history_input_fingerprint(self.mapping_path),
            'ib_history_db': _history_input_fingerprint(self.history_db_path),
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode('utf-8')
        ).hexdigest()[:20]
        safe_symbol = re.sub(r'[^A-Za-z0-9_.-]+', '_', symbol)
        return (
            Path(self.cache_root)
            / 'stitched'
            / f'v{HISTORY_SCHEMA_VERSION}'
            / safe_symbol
            / digest
        )

    def load(self, symbol: str) -> FuturesHistory:
        """Load an executable symbol and resolve its history mapping.

        ``symbol`` is the executable/public key. ``signal_symbol`` can select
        a different mapped history source for execution overlays such as MGC
        using GC signals. The resolved PST code stays internal to this loader.

        Parameters
        ----------
        symbol
            Executable symbol used for the returned history/cache identity.
        signal_symbol
            Optional public or IB symbol resolved through the instrument
            mapping to select the reference history and roll configuration.

        Returns
        -------
        FuturesHistory
            Stitched history keyed by the executable symbol.
        """
        resolved_signal_symbol = self.signal_symbol or symbol
        instrument_rows = load_pysystemtrade_ib_instruments(
            db_path=self.reference_db_path,
            mapping_path=self.mapping_path,
        )
        signal_rows = [
            row for row in instrument_rows
            if row["symbol"] == resolved_signal_symbol
        ]
        if not signal_rows:
            signal_rows = [
                row for row in instrument_rows
                if row["ib_symbol"] == resolved_signal_symbol
            ]
        if len(signal_rows) != 1:
            raise KeyError(
                f"signal symbol {resolved_signal_symbol!r} does not resolve "
                "to one PST/IB history mapping"
            )
        pst_instr_code = str(signal_rows[0]["pst_instr_code"])
        cache_directory = self._cache_directory(symbol, pst_instr_code)
        contract_path = cache_directory / 'ib_contract_history.parquet'
        cache_hit = (
            self.use_cache
            and not self.update_history
            and futures_history_parquet_cache_exists(cache_directory)
        )
        if cache_hit:
            history = read_futures_history_parquet_cache(cache_directory)
            self.contract_history = (
                pl.read_parquet(contract_path)
                if contract_path.exists()
                else None
            )
            logger.info(
                'stitched_ib_history cache_hit symbol=%s pst_instr_code=%s '
                'cache=%s',
                symbol,
                pst_instr_code,
                cache_directory,
            )
            return replace(
                history,
                metadata={
                    **dict(history.metadata),
                    'cache_hit': True,
                    'cache_directory': str(cache_directory),
                },
            )

        def load_ib_segment(prior_years: int) -> FuturesHistory:
            """Load IB history and retain its dated-contract audit rows."""
            ib_provider = IBHistoryProvider(
                ib=self.ib,
                as_of=self.as_of,
                prior_years=prior_years,
                duration=self.duration,
                bar_size=self.bar_size,
                what_to_show=self.what_to_show,
                use_rth=self.use_rth,
                reference_db_path=self.reference_db_path,
                mapping_path=self.mapping_path,
                history_db_path=self.history_db_path,
                update_history=self.update_history,
            )
            loaded = ib_provider.load(pst_instr_code)
            if ib_provider.contract_history is None:
                raise RuntimeError(
                    'IB history provider did not retain raw audit rows'
                )
            self.contract_history = (
                ib_provider.contract_history.with_columns(
                    pl.lit(symbol).alias('symbol')
                )
            )
            return loaded

        # Resolve the shared calendar policy from the reference configuration,
        # not from IB history: IB may reject an expired contract even though
        # the same policy can still select valid Globex observations.
        hold_roll_cycle, roll_offset_days, _, _ = _load_roll_config(
            pst_instr_code,
            self.reference_db_path,
        )
        roll_policy = ContractRollPolicy(
            policy_id=f'configured_{pst_instr_code}',
            hold_roll_cycle=hold_roll_cycle,
            roll_offset_days=roll_offset_days,
            source='reference_config',
        )
        reference_history = ReferenceHistoryProvider(
            db_path=self.reference_db_path,
            cache_root=self.cache_root,
            use_cache=self.use_cache,
            save_cache=self.save_cache,
        ).load(pst_instr_code)
        histories = [reference_history]
        if self.globex_asset is not None:
            try:
                globex_history = GlobexHistoryProvider(
                    db_path=self.globex_db_path,
                    cache_root=self.cache_root,
                    use_cache=self.use_cache,
                    save_cache=self.save_cache,
                    roll_policy=roll_policy,
                ).load(self.globex_asset)
            except KeyError:
                logger.info(
                    'stitched_ib_history globex_unavailable symbol=%s '
                    'pst_instr_code=%s globex_asset=%s fallback=reference_to_ib',
                    symbol,
                    pst_instr_code,
                    self.globex_asset,
                )
            else:
                histories.append(globex_history)

        ib_prior_years = self.prior_years
        ib_history: FuturesHistory | None = None
        try:
            ib_history = load_ib_segment(ib_prior_years)
        except Exception as exc:
            # IB is the newest optional segment. Contract-definition failures
            # (common for expired contracts) should not erase valid reference
            # or Globex history; expose the missing source in logs/metadata.
            logger.warning(
                'stitched_ib_history ib_unavailable symbol=%s '
                'pst_instr_code=%s fallback_sources=%s reason=%s',
                symbol,
                pst_instr_code,
                [history.source for history in histories],
                exc,
            )

        direct_reference_to_ib = (
            ib_history is not None and len(histories) == 1
        )
        if direct_reference_to_ib and ib_history is not None:
            reference_end = reference_history.signal.get_column('date').max()
            ib_start = ib_history.signal.get_column('date').min()
            while (
                reference_end is not None
                and ib_start is not None
                and reference_end < ib_start
            ):
                previous_ib_start = ib_start
                ib_prior_years += 1
                logger.info(
                    'stitched_ib_history overlap_backfill symbol=%s '
                    'pst_instr_code=%s reference_end=%s ib_start=%s '
                    'prior_years=%s',
                    symbol,
                    pst_instr_code,
                    reference_end,
                    ib_start,
                    ib_prior_years,
                )
                try:
                    ib_history = load_ib_segment(ib_prior_years)
                except Exception as exc:
                    logger.warning(
                        'stitched_ib_history ib_backfill_unavailable '
                        'symbol=%s pst_instr_code=%s prior_years=%s reason=%s',
                        symbol,
                        pst_instr_code,
                        ib_prior_years,
                        exc,
                    )
                    break
                ib_start = ib_history.signal.get_column('date').min()
                if ib_start is None or ib_start >= previous_ib_start:
                    break
        if ib_history is not None:
            histories.append(ib_history)
        if len(histories) == 1:
            history = replace(
                reference_history,
                instrument_code=symbol,
                metadata={
                    **dict(reference_history.metadata),
                    'available_sources': ['pysystemtrade'],
                    'ib_unavailable': ib_history is None,
                },
            )
        else:
            history = stitch_futures_histories(
                histories,
                instrument_code=symbol,
                max_source_gap_days=(
                    self.max_source_gap_days
                    if direct_reference_to_ib
                    else 0
                ),
            )
            history = replace(
                history,
                metadata={
                    **dict(history.metadata),
                    'available_sources': [item.source for item in histories],
                    'ib_unavailable': ib_history is None,
                },
            )
        if self.update_history and self.contract_history is not None:
            # Live acquisition changes the raw-database fingerprint. Save the
            # derived bundle under the post-update identity so the next
            # stored-only call can reuse it.
            cache_directory = self._cache_directory(symbol, pst_instr_code)
            contract_path = cache_directory / 'ib_contract_history.parquet'
        history = replace(
            history,
            metadata={
                **dict(history.metadata),
                'cache_hit': False,
                'cache_directory': str(cache_directory),
                'ib_history_db': str(Path(self.history_db_path).expanduser()),
                'ib_prior_years': ib_prior_years,
                'max_source_gap_days': self.max_source_gap_days,
            },
        )
        if self.save_cache:
            cache_directory.mkdir(parents=True, exist_ok=True)
            if self.contract_history is not None:
                temporary_contract_path = contract_path.with_suffix(
                    '.parquet.tmp'
                )
                self.contract_history.write_parquet(temporary_contract_path)
                temporary_contract_path.replace(contract_path)
            write_futures_history_parquet_cache(history, cache_directory)
        return history


def build_stitched_futures_history(
    contract_history: pl.DataFrame,
    *,
    symbol: str,
    globex_asset: str,
    pst_instr_code: str | None = None,
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH,
    reference_db_path: Path | str = DEFAULT_REFERENCE_DB_PATH,
    use_cache: bool = True,
    save_cache: bool = True,
) -> FuturesHistory:
    """Combine reference, Globex, and IB histories by date and source priority.

    The reference source supplies the oldest segment, Globex replaces it from
    the first valid Globex return, and IB replaces Globex from the first valid
    IB return. The IB roll policy is also applied to Globex so both recent
    segments hold the same contract cycle. Duplicate dates are removed before
    continuous levels are rebuilt from daily increments.

    Parameters
    ----------
    contract_history
        Overlapping dated-contract rows returned by the IB download helper.
    symbol
        Executable instrument key that owns the returned stitched history.
    globex_asset
        Raw Globex asset identifier, such as ``GC`` or ``ES``.
    pst_instr_code
        Long-history PST identifier. Defaults to the instrument in
        ``contract_history``.
    globex_db_path, reference_db_path
        Read-only database inputs for the middle and oldest history segments.
    use_cache, save_cache
        Cache controls forwarded to both database-backed providers.

    Returns
    -------
    FuturesHistory
        A duplicate-free history whose ``source_segment`` column records
        ownership of every signal, mark, and additive-price row.
    """
    ib_history = build_ib_contract_history(contract_history)
    reference_code = pst_instr_code or ib_history.instrument_code
    roll_policy = ContractRollPolicy(
        policy_id=f"configured_{ib_history.instrument_code}",
        hold_roll_cycle=str(ib_history.metadata['hold_roll_cycle']),
        roll_offset_days=int(ib_history.metadata['roll_offset_days']),
        source="reference_config",
    )
    globex_history = GlobexHistoryProvider(
        db_path=globex_db_path,
        use_cache=use_cache,
        save_cache=save_cache,
        roll_policy=roll_policy,
    ).load(globex_asset)
    reference_history = ReferenceHistoryProvider(
        db_path=reference_db_path,
        use_cache=use_cache,
        save_cache=save_cache,
    ).load(reference_code)
    return stitch_futures_histories(
        [reference_history, globex_history, ib_history],
        instrument_code=symbol,
    )


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

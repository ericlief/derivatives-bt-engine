"""IBKR futures contract cost and risk diagnostic.

The diagnostic deliberately separates research history from execution data:

* by default, the full roll-neutral pysystemtrade history supplies Carver's
  70% fast/30% slow mixed point-volatility estimate (32-session EWM standard
  deviation plus a ten-year EWM anchor); and
* Carver's IB mapping resolves the dated contract that supplies current price,
  expiry, and an optional live bid/ask snapshot.  Point value, commission,
  native currency, and FX conversion remain explicit report inputs.

All 252 instruments with usable Carver history have candidate IB identities;
qualification against the connected account determines actual availability.
IB dated and continuous history remain explicit comparison modes.  A missing
quote is never treated as a zero spread, and spread-dependent costs remain
null.

Run with TWS/IB Gateway available::

    .venv/bin/python -m derivatives_bt_engine.data.futures_cost_risk \
      --instruments all-pysystemtrade

The bid/ask result is a point-in-time snapshot, not a historical slippage
estimate.  Repeated snapshots or realized fills are required for that.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import polars as pl

from derivatives_bt_engine.data.pysystemtrade_ib import load_pysystemtrade_ib_instruments
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.instruments import (
    get_spec,
    resolve_annualization_days,
    resolve_signal_symbol,
)
from derivatives_bt_engine.domain.volatility import (
    CARVER_BUSINESS_DAYS_PER_YEAR,
    CARVER_FAST_VOL_SPAN,
    CARVER_SLOW_VOL_WEIGHT,
    CARVER_SLOW_VOL_YEARS,
    carver_mixed_point_volatility,
)
from derivatives_bt_engine.live.tsmom_rebalance import build_instruments, _resolve_contract
from derivatives_bt_engine.utils.logger import setup_logger


log = logging.getLogger("derivatives_bt_engine.data.futures_cost_risk")

DEFAULT_DURATION = "1 Y"
DEFAULT_MIN_DAYS = 7
DEFAULT_QUOTE_WAIT_SECONDS = 3.0
MARKET_DATA_TYPES = {
    "live": 1,
    "frozen": 2,
    "delayed": 3,
    "delayed-frozen": 4,
}
AUTO_MARKET_DATA_SEQUENCE = ("live", "delayed", "delayed-frozen")
TWO_DECIMAL_MONEY_COLUMNS = {
    "notional_native_per_contract",
    "notional_per_contract",
    "daily_dollar_vol_per_contract",
    "annual_dollar_vol_per_contract",
    "commission_per_side",
    "commission_round_trip",
    "one_way_spread_cash",
    "round_trip_spread_cash",
    "one_way_total_cost",
    "round_trip_total_cost",
}


def _positive_finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def volatility_from_bars(
    bars: pl.DataFrame,
    *,
    annualization_days: int,
    fast_span: int = CARVER_FAST_VOL_SPAN,
    slow_years: int = CARVER_SLOW_VOL_YEARS,
    slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
) -> dict:
    """Calculate Carver mixed point volatility from ordinary price bars."""
    if annualization_days <= 0:
        raise ValueError("annualization_days must be positive")
    if bars is None or bars.height == 0:
        raise ValueError("price history is empty")

    date_col = "ts_event" if "ts_event" in bars.columns else "date"
    if date_col not in bars.columns or "close" not in bars.columns:
        raise ValueError("price history requires date/ts_event and close columns")

    clean = (
        bars.select(pl.col(date_col).alias("ts_event"), pl.col("close").cast(pl.Float64))
        .filter(pl.col("close").is_finite() & (pl.col("close") > 0))
        .unique(subset=["ts_event"], keep="last")
        .sort("ts_event")
    )
    mixed = carver_mixed_point_volatility(
        clean.with_columns(pl.col("close").diff().alias("pt_change_1d")),
        point_change_col="pt_change_1d",
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )
    last = mixed.tail(1)
    mixed_point_vol = _positive_finite(last["mixed_point_vol"][0])
    if mixed_point_vol is None:
        raise ValueError("latest price history does not produce a valid volatility estimate")

    returns = clean["close"].pct_change().drop_nulls().tail(fast_span)
    zero_return_fraction = (
        int((returns == 0).sum()) / returns.len()
        if returns.len() else None
    )

    start = clean["ts_event"].min()
    end = clean["ts_event"].max()
    return {
        "history_rows": clean.height,
        "history_start": start,
        "history_end": end,
        "fast_point_vol": last["fast_point_vol"][0],
        "slow_point_vol": last["slow_point_vol"][0],
        "mixed_point_vol": mixed_point_vol,
        "vol_observations": last["vol_observations"][0],
        "slow_history_years": last["slow_history_years"][0],
        "fast_vol_span": last["fast_vol_span"][0],
        "slow_vol_span": last["slow_vol_span"][0],
        "slow_vol_weight": last["slow_vol_weight"][0],
        "zero_return_fraction": zero_return_fraction,
        "reference_price": clean["close"][-1],
    }


def volatility_from_pysystemtrade_history(
    provider: PysystemtradeHistoryProvider,
    instrument_code: str,
    *,
    annualization_days: int,
    fast_span: int = CARVER_FAST_VOL_SPAN,
    slow_years: int = CARVER_SLOW_VOL_YEARS,
    slow_weight: float = CARVER_SLOW_VOL_WEIGHT,
) -> dict:
    """Calculate mixed point vol from the full roll-neutral Carver history."""
    history = provider.load(instrument_code)
    bars = history.panama_bars()
    mixed = carver_mixed_point_volatility(
        bars,
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )
    last = mixed.tail(1)
    value = _positive_finite(last["mixed_point_vol"][0])
    if value is None:
        raise ValueError(f"{instrument_code}: no valid mixed point volatility")
    changes = bars["pt_change_1d"].drop_nulls().tail(fast_span)
    if history.carry.height:
        reference_price = history.carry.sort("trade_date")["current_price"][-1]
    else:
        reference_price = history.marks.sort("trade_date")["mark_price"][-1]
    return {
        "history_rows": bars.height,
        "history_start": bars["ts_event"].min(),
        "history_end": bars["ts_event"].max(),
        "fast_point_vol": last["fast_point_vol"][0],
        "slow_point_vol": last["slow_point_vol"][0],
        "mixed_point_vol": value,
        "vol_observations": last["vol_observations"][0],
        "slow_history_years": last["slow_history_years"][0],
        "fast_vol_span": last["fast_vol_span"][0],
        "slow_vol_span": last["slow_vol_span"][0],
        "slow_vol_weight": last["slow_vol_weight"][0],
        "zero_return_fraction": (
            int((changes == 0).sum()) / changes.len() if changes.len() else None
        ),
        "reference_price": reference_price,
    }


def build_cost_risk_row(
    *,
    symbol: str,
    signal_symbol: str,
    contract_id: str,
    expiration: str,
    current_price: float,
    multiplier: float,
    commission_per_side: float,
    mixed_point_vol: float,
    annualization_days: int,
    history_rows: int,
    history_start,
    history_end,
    vol_source: str = "dated_contract",
    vol_reference_price: Optional[float] = None,
    fast_point_vol: Optional[float] = None,
    slow_point_vol: Optional[float] = None,
    vol_observations: Optional[int] = None,
    slow_history_years: Optional[float] = None,
    fast_vol_span: int = CARVER_FAST_VOL_SPAN,
    slow_vol_span: Optional[int] = None,
    slow_vol_weight: float = CARVER_SLOW_VOL_WEIGHT,
    zero_return_fraction: Optional[float] = None,
    currency: str = "USD",
    fx_to_usd: float = 1.0,
    fx_asof=None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    price_source: str = "dated_contract",
    quote_timestamp_utc: Optional[str] = None,
    quote_quality: str = "live_snapshot",
) -> dict:
    """Calculate notional, dollar vol, and risk-scaled execution costs.

    A valid live bid/ask is interpreted as a full quoted width.  Expected
    one-way crossing cost is half that width from mid; round-trip spread cost
    is the full width.  Commission is per contract per side.
    """
    price = _positive_finite(current_price)
    mult = _positive_finite(multiplier)
    point_vol = _positive_finite(mixed_point_vol)
    fx = _positive_finite(fx_to_usd)
    commission = _positive_finite(commission_per_side)
    if price is None or mult is None or point_vol is None or fx is None:
        raise ValueError(
            "current_price, multiplier, mixed_point_vol, and fx_to_usd must be positive"
        )
    # A genuinely zero commission is allowed even though _positive_finite
    # intentionally rejects zero for prices, multipliers, and vol.
    if commission is None:
        try:
            commission = float(commission_per_side)
        except (TypeError, ValueError) as exc:
            raise ValueError("commission_per_side must be finite and non-negative") from exc
        if not math.isfinite(commission) or commission < 0:
            raise ValueError("commission_per_side must be finite and non-negative")

    bid_value = _positive_finite(bid)
    ask_value = _positive_finite(ask)
    spread_valid = bid_value is not None and ask_value is not None and ask_value >= bid_value
    full_spread_points = ask_value - bid_value if spread_valid else None
    one_way_spread_cash_native = full_spread_points * mult / 2.0 if spread_valid else None
    round_trip_spread_cash_native = full_spread_points * mult if spread_valid else None

    notional_native = price * mult
    notional = notional_native * fx
    daily_dollar_vol = point_vol * mult * fx
    annual_dollar_vol = daily_dollar_vol * math.sqrt(annualization_days)
    vol_reference = _positive_finite(vol_reference_price) or price
    daily_return_vol = point_vol / abs(vol_reference)
    annual_return_vol = daily_return_vol * math.sqrt(annualization_days)
    one_way_commission_native = commission
    round_trip_commission_native = commission * 2.0
    one_way_commission = one_way_commission_native * fx
    round_trip_commission = round_trip_commission_native * fx
    one_way_spread_cash = (
        one_way_spread_cash_native * fx if one_way_spread_cash_native is not None else None
    )
    round_trip_spread_cash = (
        round_trip_spread_cash_native * fx if round_trip_spread_cash_native is not None else None
    )
    one_way_total = (
        one_way_commission + one_way_spread_cash
        if one_way_spread_cash is not None else None
    )
    round_trip_total = (
        round_trip_commission + round_trip_spread_cash
        if round_trip_spread_cash is not None else None
    )

    return {
        "symbol": symbol,
        "signal_symbol": signal_symbol,
        "contract_id": contract_id,
        "expiration": expiration,
        "price": price,
        "price_source": price_source,
        "currency": currency,
        "fx_to_usd": fx,
        "fx_asof": fx_asof,
        "multiplier": mult,
        "notional_native_per_contract": notional_native,
        "notional_per_contract": notional,
        "fast_point_vol": fast_point_vol,
        "slow_point_vol": slow_point_vol,
        "mixed_point_vol": point_vol,
        "vol_reference_price": vol_reference,
        "daily_return_vol": daily_return_vol,
        "annual_return_vol": annual_return_vol,
        "daily_dollar_vol_per_contract": daily_dollar_vol,
        "annual_dollar_vol_per_contract": annual_dollar_vol,
        "commission_per_side": one_way_commission,
        "commission_round_trip": round_trip_commission,
        "bid": bid_value,
        "ask": ask_value,
        "full_spread_points": full_spread_points,
        "one_way_spread_cash": one_way_spread_cash,
        "round_trip_spread_cash": round_trip_spread_cash,
        "one_way_total_cost": one_way_total,
        "round_trip_total_cost": round_trip_total,
        "one_way_cost_per_annual_dollar_vol": (
            one_way_total / annual_dollar_vol if one_way_total is not None else None
        ),
        "round_trip_cost_per_annual_dollar_vol": (
            round_trip_total / annual_dollar_vol if round_trip_total is not None else None
        ),
        "one_way_cost_bps_notional": (
            one_way_total / notional * 10_000.0 if one_way_total is not None else None
        ),
        "spread_quality": quote_quality if spread_valid else "unknown_no_bid_ask",
        "quote_timestamp_utc": quote_timestamp_utc,
        "annualization_days": annualization_days,
        "history_rows": history_rows,
        "history_start": history_start,
        "history_end": history_end,
        "vol_source": vol_source,
        "vol_observations": vol_observations,
        "slow_history_years": slow_history_years,
        "fast_vol_span": fast_vol_span,
        "slow_vol_span": slow_vol_span,
        "slow_vol_weight": slow_vol_weight,
        "zero_return_fraction": zero_return_fraction,
    }


def _ticker_values_once(ib, contract, wait_seconds: float, mode: str) -> dict:
    ib.set_market_data_type(MARKET_DATA_TYPES[mode])
    ticker = ib.req_mkt_data(contract, generic_ticks="")
    try:
        ib.sleep(wait_seconds)
        bid = _positive_finite(getattr(ticker, "bid", None))
        ask = _positive_finite(getattr(ticker, "ask", None))
        last = _positive_finite(getattr(ticker, "last", None))
        close = _positive_finite(getattr(ticker, "close", None))
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and ask >= bid else None
        return {
            "bid": bid,
            "ask": ask,
            "last": last,
            "close": close,
            "mid": mid,
            "market_data_type": mode,
        }
    finally:
        try:
            ib.cancel_mkt_data(contract)
        except Exception as exc:
            log.debug(
                "quote_cancel_failed contract=%s mode=%s reason=%s",
                contract, mode, exc,
            )


def _ticker_values(
    ib,
    contract,
    wait_seconds: float,
    market_data_type: str,
) -> dict:
    modes = (
        AUTO_MARKET_DATA_SEQUENCE
        if market_data_type == "auto"
        else (market_data_type,)
    )
    best_available = None
    attempts = []
    for mode in modes:
        attempts.append(mode)
        try:
            quote = _ticker_values_once(ib, contract, wait_seconds, mode)
        except Exception as exc:
            log.debug(
                "quote_mode_failed contract=%s mode=%s reason=%s",
                contract, mode, exc,
            )
            continue
        if quote["mid"] is not None:
            quote["market_data_attempts"] = ",".join(attempts)
            return quote
        if best_available is None and (
            quote["last"] is not None or quote["close"] is not None
        ):
            best_available = quote

    if best_available is None:
        best_available = {
            "bid": None,
            "ask": None,
            "last": None,
            "close": None,
            "mid": None,
            "market_data_type": modes[-1],
        }
    best_available["market_data_attempts"] = ",".join(attempts)
    return best_available


def _latest_dated_close(ib, contract) -> Optional[float]:
    bars = ib.get_historical_bars(
        contract,
        duration="5 D",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=False,
    )
    if bars is None or bars.height == 0 or "close" not in bars.columns:
        return None
    return _positive_finite(bars.sort("date").tail(1)["close"][0])


def load_latest_fx_to_usd(db_path: Path | str) -> dict[str, dict]:
    """Latest imported Carver FX conversion for each native currency."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        fx = con.execute(
            """
            SELECT currency_pair, source_timestamp, price
            FROM raw.fx_prices
            QUALIFY row_number() OVER (
                PARTITION BY currency_pair ORDER BY source_timestamp DESC
            ) = 1
            """
        ).pl()
    finally:
        con.close()
    result = {"USD": {"rate": 1.0, "asof": None}}
    for row in fx.iter_rows(named=True):
        pair = row["currency_pair"]
        if pair.endswith("USD"):
            result[pair[:-3]] = {"rate": row["price"], "asof": row["source_timestamp"]}
    return result


def _history_volatility(
    ib,
    instr: dict,
    *,
    vol_source: str,
    duration: str,
    use_rth: bool,
    fast_span: int,
    slow_years: int,
    slow_weight: float,
    pysystemtrade_provider: Optional[PysystemtradeHistoryProvider],
    dated_contract=None,
) -> tuple[int, dict]:
    from ib_tools.ibpysync import IBPySync

    symbol = instr["symbol"]
    if vol_source == "pysystemtrade":
        if pysystemtrade_provider is None:
            raise ValueError("pysystemtrade vol source requires a provider")
        annualization_days = CARVER_BUSINESS_DAYS_PER_YEAR
        return annualization_days, volatility_from_pysystemtrade_history(
            pysystemtrade_provider,
            instr.get("instrument_code", symbol),
            annualization_days=annualization_days,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
        )

    if vol_source == "continuous":
        signal_symbol = resolve_signal_symbol(instr)
        vol_contract = IBPySync.cont_future(
            signal_symbol,
            exchange=instr.get("exchange", "CME"),
            currency=instr.get("ib_currency") or "USD",
        )
        ib.qualify_contracts(vol_contract)
    elif vol_source == "dated":
        if dated_contract is None:
            raise ValueError("dated vol source requires the resolved dated contract")
        vol_contract = dated_contract
    else:
        raise ValueError(f"Unknown vol_source {vol_source!r}")

    bars = ib.get_historical_bars(
        vol_contract,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=use_rth,
    )
    annualization_days = int(instr.get("annualization_days", resolve_annualization_days(symbol)))
    return annualization_days, volatility_from_bars(
        bars,
        annualization_days=annualization_days,
        fast_span=fast_span,
        slow_years=slow_years,
        slow_weight=slow_weight,
    )


def diagnose_instrument(
    ib,
    instr: dict,
    *,
    duration: str,
    min_days: int,
    quote_wait_seconds: float,
    use_rth: bool,
    vol_source: str,
    fast_span: int,
    slow_years: int,
    slow_weight: float,
    market_data_type: str,
    pysystemtrade_provider: Optional[PysystemtradeHistoryProvider],
    fx_by_currency: dict[str, dict],
) -> dict:
    symbol = instr["symbol"]
    try:
        spec = get_spec(symbol)
    except KeyError:
        # Explicit JSON configs may describe an IBKR contract outside the
        # repository registry.  Such rows must carry their own executable
        # multiplier and commission; never invent either value.
        spec = {}
    multiplier = instr.get("multiplier", spec.get("multiplier"))
    commission = instr.get("commission", spec.get("commission"))
    if multiplier is None or commission is None:
        raise ValueError(
            f"{symbol}: multiplier and commission are required in the registry or JSON config"
        )
    signal_symbol = resolve_signal_symbol(instr)
    annualization_days = vol = None
    if vol_source != "dated":
        annualization_days, vol = _history_volatility(
            ib,
            instr,
            vol_source=vol_source,
            duration=duration,
            use_rth=use_rth,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
            pysystemtrade_provider=pysystemtrade_provider,
        )
    if instr.get("ignore_weekly"):
        raise RuntimeError(
            f"{symbol}: IB mapping requires specialised weekly/daily expiry filtering; "
            "candidate mapping retained but live contract selection skipped"
        )
    contract = _resolve_contract(ib, instr, min_days)
    if vol_source == "dated":
        annualization_days, vol = _history_volatility(
            ib,
            instr,
            vol_source=vol_source,
            duration=duration,
            use_rth=use_rth,
            fast_span=fast_span,
            slow_years=slow_years,
            slow_weight=slow_weight,
            pysystemtrade_provider=pysystemtrade_provider,
            dated_contract=contract,
        )
    assert annualization_days is not None and vol is not None

    quote = _ticker_values(ib, contract, quote_wait_seconds, market_data_type)
    resolved_market_data_type = quote["market_data_type"]
    quote_source = resolved_market_data_type.replace("-", "_")
    if quote["mid"] is not None:
        price = quote["mid"]
        price_source = f"{quote_source}_bid_ask_mid"
    elif quote["last"] is not None:
        price = quote["last"]
        price_source = f"{quote_source}_last"
    elif quote["close"] is not None:
        price = quote["close"]
        price_source = f"{quote_source}_previous_close"
    else:
        price = _latest_dated_close(ib, contract)
        price_source = "dated_contract_daily_close"
    if price is None:
        raise RuntimeError(f"{symbol}: no usable price for resolved dated contract")

    expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
    local_symbol = getattr(contract, "localSymbol", "") or ""
    con_id = getattr(contract, "conId", "") or ""
    contract_id = local_symbol or f"{getattr(contract, 'symbol', symbol)}:{con_id}"
    currency = instr.get("currency", "USD")
    fx_info = fx_by_currency.get(currency)
    if fx_info is None:
        raise ValueError(f"{symbol}: no {currency}USD conversion available")
    row = build_cost_risk_row(
        symbol=symbol,
        signal_symbol=signal_symbol,
        contract_id=contract_id,
        expiration=expiry,
        current_price=price,
        multiplier=multiplier,
        commission_per_side=commission,
        mixed_point_vol=vol["mixed_point_vol"],
        annualization_days=annualization_days,
        history_rows=vol["history_rows"],
        history_start=vol["history_start"],
        history_end=vol["history_end"],
        vol_source={
            "pysystemtrade": "pysystemtrade_roll_neutral",
            "continuous": "ib_continuous_explicit",
            "dated": "ib_dated_contract",
        }[vol_source],
        fast_point_vol=vol["fast_point_vol"],
        slow_point_vol=vol["slow_point_vol"],
        vol_observations=vol["vol_observations"],
        slow_history_years=vol["slow_history_years"],
        fast_vol_span=vol["fast_vol_span"],
        slow_vol_span=vol["slow_vol_span"],
        slow_vol_weight=vol["slow_vol_weight"],
        zero_return_fraction=vol["zero_return_fraction"],
        currency=currency,
        fx_to_usd=fx_info["rate"],
        fx_asof=fx_info["asof"],
        bid=quote["bid"],
        ask=quote["ask"],
        price_source=price_source,
        quote_timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        quote_quality=f"{resolved_market_data_type}_snapshot",
        vol_reference_price=vol["reference_price"],
    )
    row.update({
        "ib_symbol": instr.get("ib_symbol", symbol),
        "ib_exchange": instr.get("exchange"),
        "ib_currency": instr.get("ib_currency"),
        "ib_multiplier": instr.get("ib_multiplier"),
        "price_magnifier": instr.get("price_magnifier"),
        "mapping_status": instr.get("mapping_status", "local_registry"),
        "ib_availability": "contract_qualified",
        "quote_market_data_type": resolved_market_data_type,
        "quote_market_data_attempts": quote["market_data_attempts"],
        "carver_spread_points": instr.get("carver_spread_points"),
    })
    return row


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--instruments",
        default="all-pysystemtrade",
        help="Carver instrument codes, local traded symbols, JSON config, or all-pysystemtrade (default)",
    )
    parser.add_argument("--duration", default=DEFAULT_DURATION,
                        help="IB history request duration for the selected vol source (default: %(default)s)")
    parser.add_argument("--fast-vol-span", type=int, default=CARVER_FAST_VOL_SPAN,
                        help="Fast EWM point-vol span (default: %(default)s, Advanced Futures Trading)")
    parser.add_argument("--slow-vol-years", type=int, default=CARVER_SLOW_VOL_YEARS)
    parser.add_argument("--slow-vol-weight", type=float, default=CARVER_SLOW_VOL_WEIGHT)
    parser.add_argument(
        "--vol-source",
        choices=["pysystemtrade", "dated", "continuous"],
        default="pysystemtrade",
        help="Roll-neutral Carver history (default), or explicit IB comparison surface",
    )
    parser.add_argument("--pysystemtrade-db", type=Path, default=DEFAULT_PYSYSTEMTRADE_DB_PATH)
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS,
                        help="Minimum days to expiry for resolved traded contract (default: %(default)s)")
    parser.add_argument("--quote-wait-seconds", type=float, default=DEFAULT_QUOTE_WAIT_SECONDS)
    parser.add_argument(
        "--market-data-type",
        choices=["auto", *MARKET_DATA_TYPES],
        default="auto",
        help="IB quote mode (default: auto tries live, delayed, then delayed-frozen)",
    )
    parser.add_argument("--use-rth", action="store_true",
                        help="Use regular-hours-only volatility bars; default uses the full futures session")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=23)
    parser.add_argument("--offline", action="store_true",
                        help="Build the full mixed-vol/mapping comparison without connecting to IB")
    parser.add_argument("--output", type=Path, default=None,
                        help="CSV output path (default: timestamped file under results/)")
    parser.add_argument("--no-save", action="store_true")
    return parser.parse_args(argv)


def _error_row(
    instr: dict,
    exc: Exception,
    *,
    vol: Optional[dict] = None,
    annualization_days: int = CARVER_BUSINESS_DAYS_PER_YEAR,
    fx_by_currency: Optional[dict[str, dict]] = None,
) -> dict:
    identity = {
        "symbol": instr.get("symbol"),
        "signal_symbol": resolve_signal_symbol(instr),
        "ib_symbol": instr.get("ib_symbol"),
        "ib_exchange": instr.get("exchange"),
        "ib_currency": instr.get("ib_currency"),
        "ib_multiplier": instr.get("ib_multiplier"),
        "price_magnifier": instr.get("price_magnifier"),
        "currency": instr.get("currency", "USD"),
        "mapping_status": instr.get("mapping_status", "local_registry"),
        "ib_availability": "unavailable_or_unverified",
        "carver_spread_points": instr.get("carver_spread_points"),
        "spread_quality": "error",
        "error": str(exc),
    }
    if vol is None or vol.get("reference_price") is None:
        return identity
    currency = instr.get("currency", "USD")
    fx_info = (fx_by_currency or {}).get(currency)
    if fx_info is None:
        return identity
    try:
        row = build_cost_risk_row(
            symbol=instr["symbol"],
            signal_symbol=resolve_signal_symbol(instr),
            contract_id="",
            expiration="",
            current_price=vol["reference_price"],
            multiplier=instr["multiplier"],
            commission_per_side=instr["commission"],
            mixed_point_vol=vol["mixed_point_vol"],
            annualization_days=annualization_days,
            history_rows=vol["history_rows"],
            history_start=vol["history_start"],
            history_end=vol["history_end"],
            vol_source="pysystemtrade_roll_neutral",
            vol_reference_price=vol["reference_price"],
            fast_point_vol=vol["fast_point_vol"],
            slow_point_vol=vol["slow_point_vol"],
            vol_observations=vol["vol_observations"],
            slow_history_years=vol["slow_history_years"],
            fast_vol_span=vol["fast_vol_span"],
            slow_vol_span=vol["slow_vol_span"],
            slow_vol_weight=vol["slow_vol_weight"],
            zero_return_fraction=vol["zero_return_fraction"],
            currency=currency,
            fx_to_usd=fx_info["rate"],
            fx_asof=fx_info["asof"],
            price_source="pysystemtrade_last_current_price",
        )
    except Exception:
        return identity
    row.update(identity)
    return row


def _load_instruments(spec: str, pysystemtrade_db: Path | str) -> list[dict]:
    path = Path(spec)
    if path.exists() and path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("instrument JSON must contain a list of instrument objects")
        return loaded
    carver = load_pysystemtrade_ib_instruments(pysystemtrade_db)
    if spec == "all-pysystemtrade":
        return carver
    carver_by_code = {row["instrument_code"]: row for row in carver}
    requested = [value.strip() for value in spec.split(",") if value.strip()]
    selected = []
    local = []
    for value in requested:
        if value in carver_by_code:
            selected.append(carver_by_code[value])
        else:
            local.append(value)
    if local:
        selected.extend(build_instruments(local, max_notional=None, max_contracts=1))
    return selected


def _round_report_decimals(report: pl.DataFrame) -> pl.DataFrame:
    """Round report floats for human-facing CSV output.

    Monetary contract/cost fields use cents; all other floating-point
    diagnostics retain four decimal places. Counts and identifiers are not
    cast or rounded.
    """
    expressions = []
    for name, dtype in report.schema.items():
        if dtype in (pl.Float32, pl.Float64):
            decimals = 2 if name in TWO_DECIMAL_MONEY_COLUMNS else 4
            expressions.append(pl.col(name).round(decimals))
    return report.with_columns(expressions)


def _emit_report(report: pl.DataFrame, args) -> pl.DataFrame:
    report = report.with_columns(
        pl.lit(datetime.now(timezone.utc).isoformat(timespec="seconds")).alias(
            "report_generated_at_utc"
        )
    )
    output_report = _round_report_decimals(report)
    summary_columns = [
        "symbol",
        "ib_symbol",
        "ib_exchange",
        "price",
        "currency",
        "notional_per_contract",
        "mixed_point_vol",
        "annual_dollar_vol_per_contract",
        "slow_history_years",
        "ib_availability",
        "error",
    ]
    print(output_report.select(
        column for column in summary_columns if column in output_report.columns
    ))
    print(f"Report rows={output_report.height} columns={output_report.width}")
    if not args.no_save:
        output = args.output
        if output is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = Path(__file__).resolve().parents[3] / "results" / f"futures_cost_risk_{stamp}.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output_report.write_csv(output)
        log.info("futures_cost_risk complete output=%s rows=%d", output, output_report.height)
        print(f"Saved {output}")
    return report


def run(argv=None) -> pl.DataFrame:
    """Build, emit, and return the report for Python/notebook callers."""
    args = parse_args(argv)
    setup_logger()
    instruments = _load_instruments(args.instruments, args.pysystemtrade_db)
    pysystemtrade_provider = (
        PysystemtradeHistoryProvider(db_path=args.pysystemtrade_db)
        if args.vol_source == "pysystemtrade" else None
    )
    fx_by_currency = load_latest_fx_to_usd(args.pysystemtrade_db)

    if args.offline:
        if pysystemtrade_provider is None:
            raise ValueError("--offline currently requires --vol-source pysystemtrade")
        rows = []
        for instr in instruments:
            try:
                vol = volatility_from_pysystemtrade_history(
                    pysystemtrade_provider,
                    instr.get("instrument_code", instr["symbol"]),
                    annualization_days=CARVER_BUSINESS_DAYS_PER_YEAR,
                    fast_span=args.fast_vol_span,
                    slow_years=args.slow_vol_years,
                    slow_weight=args.slow_vol_weight,
                )
                row = _error_row(
                    instr,
                    RuntimeError("IB contract qualification not run (--offline)"),
                    vol=vol,
                    fx_by_currency=fx_by_currency,
                )
                row["ib_availability"] = "not_checked_offline"
                row["spread_quality"] = "unknown_no_bid_ask"
                row["error"] = None
                rows.append(row)
            except Exception as exc:
                rows.append(_error_row(instr, exc, fx_by_currency=fx_by_currency))
        return _emit_report(pl.DataFrame(rows, infer_schema_length=None), args)

    # eventkit still asks asyncio for a current main-thread loop at import
    # time.  Python 3.14 no longer creates one implicitly, so establish it
    # before importing ib_tools/ib_insync.  IBPySync subsequently owns its
    # background loop as usual.
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    from ib_tools.ibpysync import IBPySync

    log.info(
        "futures_cost_risk start instruments=%d duration=%s fast_vol_span=%d "
        "slow_vol_years=%d slow_vol_weight=%.3f vol_source=%s "
        "market_data_type=%s use_rth=%s",
        len(instruments), args.duration, args.fast_vol_span, args.slow_vol_years,
        args.slow_vol_weight, args.vol_source, args.market_data_type, args.use_rth,
    )
    ib = IBPySync()
    ib.connect(args.host, args.port, args.client_id)
    rows = []
    try:
        for instr in instruments:
            symbol = instr["symbol"]
            try:
                row = diagnose_instrument(
                    ib,
                    instr,
                    duration=args.duration,
                    min_days=args.min_days,
                    quote_wait_seconds=args.quote_wait_seconds,
                    use_rth=args.use_rth,
                    vol_source=args.vol_source,
                    fast_span=args.fast_vol_span,
                    slow_years=args.slow_vol_years,
                    slow_weight=args.slow_vol_weight,
                    market_data_type=args.market_data_type,
                    pysystemtrade_provider=pysystemtrade_provider,
                    fx_by_currency=fx_by_currency,
                )
                rows.append(row)
                log.info(
                    "futures_cost_risk selected symbol=%s contract=%s notional_usd=%.2f "
                    "annual_dvol_usd=%.2f round_trip_cost_usd=%s spread_quality=%s",
                    symbol, row["contract_id"], row["notional_per_contract"],
                    row["annual_dollar_vol_per_contract"], row["round_trip_total_cost"],
                    row["spread_quality"],
                )
            except Exception as exc:
                log.warning("futures_cost_risk rejected symbol=%s reason=%s", symbol, exc)
                fallback_vol = None
                if pysystemtrade_provider is not None:
                    try:
                        _, fallback_vol = _history_volatility(
                            ib,
                            instr,
                            vol_source="pysystemtrade",
                            duration=args.duration,
                            use_rth=args.use_rth,
                            fast_span=args.fast_vol_span,
                            slow_years=args.slow_vol_years,
                            slow_weight=args.slow_vol_weight,
                            pysystemtrade_provider=pysystemtrade_provider,
                        )
                    except Exception as vol_exc:
                        log.warning(
                            "futures_cost_risk volatility_fallback_failed symbol=%s reason=%s",
                            symbol, vol_exc,
                        )
                rows.append(_error_row(
                    instr,
                    exc,
                    vol=fallback_vol,
                    fx_by_currency=fx_by_currency,
                ))
    finally:
        ib.disconnect()

    return _emit_report(pl.DataFrame(rows, infer_schema_length=None), args)


def main(argv=None) -> None:
    """Console-script boundary; successful commands must return exit status zero."""
    run(argv)


if __name__ == "__main__":
    main()

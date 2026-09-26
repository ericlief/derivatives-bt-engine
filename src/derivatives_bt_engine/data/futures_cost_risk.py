"""IBKR futures contract cost and risk diagnostic.

The diagnostic deliberately separates IB surfaces:

* by default, one year requested from the resolved dated contract supplies
  the most recent 63-session return-volatility estimate; and
* that same dated contract supplies the current price, expiry, and optional
  live bid/ask snapshot, while the executable-contract registry supplies its
  multiplier and commission.

The dated-contract default avoids the non-reconstructable adjustment and
roll differences observed in IB's continuous surface.  Continuous history is
available only as an explicit ``--vol-source continuous`` comparison mode.
A missing quote is never treated as a zero spread.  The report leaves
spread-dependent costs null and labels their quality accordingly.

Run with TWS/IB Gateway available::

    .venv/bin/python -m derivatives_bt_engine.data.futures_cost_risk \
      --instruments MES,MNQ,MCL,MGC,SIL,VXM

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

import polars as pl

from derivatives_bt_engine.domain.instruments import (
    INSTRUMENTS,
    get_spec,
    resolve_annualization_days,
    resolve_signal_symbol,
)
from derivatives_bt_engine.domain.signal import DEFAULT_FAST_WINDOW, build_features, continuous_momentum
from derivatives_bt_engine.live.tsmom_rebalance import build_instruments, _resolve_contract
from derivatives_bt_engine.utils.logger import setup_logger


log = logging.getLogger("derivatives_bt_engine.data.futures_cost_risk")

DEFAULT_DURATION = "1 Y"
DEFAULT_MIN_DAYS = 7
DEFAULT_QUOTE_WAIT_SECONDS = 3.0


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
    vol_window: int = DEFAULT_FAST_WINDOW,
) -> dict:
    """Return the same annualized return vol used by the live TSMOM sizer.

    ``continuous_momentum`` defines sizing vol as the latest rolling standard
    deviation of simple daily returns over its fast-vol window, multiplied by
    ``sqrt(annualization_days)``.  Keeping that convention here makes the
    resulting one-contract dollar vol directly comparable with live and
    backtest sizing reports.
    """
    if vol_window < 2:
        raise ValueError("vol_window must be at least 2")
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
    if clean.height <= vol_window:
        raise ValueError(
            f"price history has {clean.height} usable rows; need more than {vol_window}"
        )

    features = build_features(clean)
    momentum = continuous_momentum(
        features,
        fast_window=vol_window,
        slow_window=max(vol_window + 1, vol_window * 4),
        vol_fast_window=vol_window,
        annualization_days=annualization_days,
    )
    last = momentum.tail(1)
    daily_vol = _positive_finite(last["std_fast"][0])
    annual_vol = _positive_finite(last["hv_fast"][0])
    if daily_vol is None or annual_vol is None:
        raise ValueError("latest price history does not produce a valid volatility estimate")

    returns = features["ret_1d"].drop_nulls().tail(vol_window)
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
        "daily_return_vol": daily_vol,
        "annual_return_vol": annual_vol,
        "zero_return_fraction": zero_return_fraction,
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
    annual_return_vol: float,
    annualization_days: int,
    history_rows: int,
    history_start,
    history_end,
    vol_source: str = "dated_contract",
    zero_return_fraction: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    price_source: str = "dated_contract",
    quote_timestamp_utc: Optional[str] = None,
) -> dict:
    """Calculate notional, dollar vol, and risk-scaled execution costs.

    A valid live bid/ask is interpreted as a full quoted width.  Expected
    one-way crossing cost is half that width from mid; round-trip spread cost
    is the full width.  Commission is per contract per side.
    """
    price = _positive_finite(current_price)
    mult = _positive_finite(multiplier)
    ann_vol = _positive_finite(annual_return_vol)
    commission = _positive_finite(commission_per_side)
    if price is None or mult is None or ann_vol is None:
        raise ValueError("current_price, multiplier, and annual_return_vol must be positive")
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
    one_way_spread_cash = full_spread_points * mult / 2.0 if spread_valid else None
    round_trip_spread_cash = full_spread_points * mult if spread_valid else None

    notional = price * mult
    annual_dollar_vol = notional * ann_vol
    daily_dollar_vol = annual_dollar_vol / math.sqrt(annualization_days)
    one_way_commission = commission
    round_trip_commission = commission * 2.0
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
        "multiplier": mult,
        "notional_per_contract": notional,
        "daily_return_vol": ann_vol / math.sqrt(annualization_days),
        "annual_return_vol": ann_vol,
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
        "spread_quality": "live_snapshot" if spread_valid else "unknown_no_live_bid_ask",
        "quote_timestamp_utc": quote_timestamp_utc,
        "annualization_days": annualization_days,
        "history_rows": history_rows,
        "history_start": history_start,
        "history_end": history_end,
        "vol_source": vol_source,
        "zero_return_fraction": zero_return_fraction,
    }


def _ticker_values(ib, contract, wait_seconds: float) -> dict:
    ticker = ib.req_mkt_data(contract)
    try:
        ib.sleep(wait_seconds)
        bid = _positive_finite(getattr(ticker, "bid", None))
        ask = _positive_finite(getattr(ticker, "ask", None))
        last = _positive_finite(getattr(ticker, "last", None))
        close = _positive_finite(getattr(ticker, "close", None))
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and ask >= bid else None
        return {"bid": bid, "ask": ask, "last": last, "close": close, "mid": mid}
    finally:
        ib.cancel_mkt_data(contract)


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


def diagnose_instrument(
    ib,
    instr: dict,
    *,
    duration: str,
    vol_window: int,
    min_days: int,
    quote_wait_seconds: float,
    use_rth: bool,
    vol_source: str,
) -> dict:
    from ib_tools.ibpysync import IBPySync

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
    contract = _resolve_contract(ib, instr, min_days)

    if vol_source == "continuous":
        vol_contract = IBPySync.cont_future(signal_symbol, exchange=instr.get("exchange", "CME"))
        ib.qualify_contracts(vol_contract)
    else:
        vol_contract = contract
    bars = ib.get_historical_bars(
        vol_contract,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=use_rth,
    )
    annualization_days = int(instr.get("annualization_days", resolve_annualization_days(symbol)))
    vol = volatility_from_bars(
        bars,
        annualization_days=annualization_days,
        vol_window=vol_window,
    )

    quote = _ticker_values(ib, contract, quote_wait_seconds)
    if quote["mid"] is not None:
        price = quote["mid"]
        price_source = "live_bid_ask_mid"
    elif quote["last"] is not None:
        price = quote["last"]
        price_source = "live_last"
    elif quote["close"] is not None:
        price = quote["close"]
        price_source = "live_previous_close"
    else:
        price = _latest_dated_close(ib, contract)
        price_source = "dated_contract_daily_close"
    if price is None:
        raise RuntimeError(f"{symbol}: no usable price for resolved dated contract")

    expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
    local_symbol = getattr(contract, "localSymbol", "") or ""
    con_id = getattr(contract, "conId", "") or ""
    contract_id = local_symbol or f"{getattr(contract, 'symbol', symbol)}:{con_id}"
    return build_cost_risk_row(
        symbol=symbol,
        signal_symbol=signal_symbol,
        contract_id=contract_id,
        expiration=expiry,
        current_price=price,
        multiplier=multiplier,
        commission_per_side=commission,
        annual_return_vol=vol["annual_return_vol"],
        annualization_days=annualization_days,
        history_rows=vol["history_rows"],
        history_start=vol["history_start"],
        history_end=vol["history_end"],
        vol_source=("ib_continuous_explicit" if vol_source == "continuous" else "ib_dated_contract"),
        zero_return_fraction=vol["zero_return_fraction"],
        bid=quote["bid"],
        ask=quote["ask"],
        price_source=price_source,
        quote_timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--instruments",
        default=",".join(sorted(INSTRUMENTS)),
        help="Comma-separated traded symbols or JSON instrument config (default: live universe)",
    )
    parser.add_argument("--duration", default=DEFAULT_DURATION,
                        help="IB history request duration for the selected vol source (default: %(default)s)")
    parser.add_argument("--vol-window", type=int, default=DEFAULT_FAST_WINDOW,
                        help="Daily-return rolling-vol window (default: %(default)s, live TSMOM fast window)")
    parser.add_argument(
        "--vol-source",
        choices=["dated", "continuous"],
        default="dated",
        help="IB history surface used for volatility (default: dated; continuous is explicit comparison only)",
    )
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS,
                        help="Minimum days to expiry for resolved traded contract (default: %(default)s)")
    parser.add_argument("--quote-wait-seconds", type=float, default=DEFAULT_QUOTE_WAIT_SECONDS)
    parser.add_argument("--use-rth", action="store_true",
                        help="Use regular-hours-only volatility bars; default uses the full futures session")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=23)
    parser.add_argument("--output", type=Path, default=None,
                        help="CSV output path (default: timestamped file under results/)")
    parser.add_argument("--no-save", action="store_true")
    return parser.parse_args(argv)


def _error_row(instr: dict, exc: Exception) -> dict:
    return {
        "symbol": instr.get("symbol"),
        "signal_symbol": resolve_signal_symbol(instr),
        "spread_quality": "error",
        "error": str(exc),
    }


def _load_instruments(spec: str) -> list[dict]:
    path = Path(spec)
    if path.exists() and path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("instrument JSON must contain a list of instrument objects")
        return loaded
    return build_instruments(spec.split(","), max_notional=None, max_contracts=1)


def main(argv=None) -> pl.DataFrame:
    args = parse_args(argv)
    setup_logger()
    instruments = _load_instruments(args.instruments)

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
        "futures_cost_risk start instruments=%d duration=%s vol_window_days=%d vol_source=%s use_rth=%s",
        len(instruments), args.duration, args.vol_window, args.vol_source, args.use_rth,
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
                    vol_window=args.vol_window,
                    min_days=args.min_days,
                    quote_wait_seconds=args.quote_wait_seconds,
                    use_rth=args.use_rth,
                    vol_source=args.vol_source,
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
                rows.append(_error_row(instr, exc))
    finally:
        ib.disconnect()

    report = pl.DataFrame(rows, infer_schema_length=None)
    print(report)
    if not args.no_save:
        output = args.output
        if output is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = Path(__file__).resolve().parents[3] / "results" / f"futures_cost_risk_{stamp}.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        report.write_csv(output)
        log.info("futures_cost_risk complete output=%s rows=%d", output, report.height)
        print(f"Saved {output}")
    return report


if __name__ == "__main__":
    main()

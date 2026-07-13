"""
Tests for options_bt.domain.tsmom_backtester — the multi-symbol monthly-
rebalance TSMOM backtest engine. Uses synthetic price/VIX data throughout
(monkeypatched in place of load_portfolio_data) so these run fast and
without a duckdb/CSV dependency.
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from options_bt.domain import tsmom_backtester as tb
from options_bt.domain.tsmom_backtester import (
    TsmomBacktestConfig,
    check_vol_regime,
    _compute_vix_regime_series,
    _month_end_dates,
    run_tsmom_backtest,
)


def _trading_dates(start: date, n: int) -> list[date]:
    """n business days starting at `start` (skips weekends only, no holiday calendar needed for tests)."""
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _price_df(start: date, n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    dates = _trading_dates(start, n)
    return pl.DataFrame({
        'ts_event': dates, 'open': close, 'high': close, 'low': close,
        'close': close, 'volume': [1000] * n,
    })


def _vix_df(start: date, n: int, level: float) -> pl.DataFrame:
    dates = _trading_dates(start, n)
    return pl.DataFrame({'date': dates, 'vix_close': [level] * n})


# ── check_vol_regime ─────────────────────────────────────────────────────────

def test_check_vol_regime_bands():
    assert check_vol_regime(1.0) == 'normal'
    assert check_vol_regime(1.4) == 'elevated'
    assert check_vol_regime(1.6) == 'spike'
    assert check_vol_regime(2.5) == 'extreme'
    assert check_vol_regime(None) == 'normal'


# ── _compute_vix_regime_series ───────────────────────────────────────────────

def test_compute_vix_regime_series_flat_is_normal():
    vix = _vix_df(date(2020, 1, 1), 100, level=15.0)
    out = _compute_vix_regime_series(vix)
    assert 'vix_ma63' in out.columns and 'vix_ratio' in out.columns and 'vol_regime' in out.columns
    last = out.tail(1)
    assert last['vix_ratio'][0] == pytest.approx(1.0)
    assert last['vol_regime'][0] == 'normal'


def test_compute_vix_regime_series_spike_detected():
    base = _vix_df(date(2020, 1, 1), 90, level=15.0)
    spike = _vix_df(base['date'][-1] + timedelta(days=3), 5, level=40.0)
    vix = pl.concat([base, spike])
    out = _compute_vix_regime_series(vix)
    assert out.tail(1)['vol_regime'][0] in ('spike', 'extreme')


# ── _month_end_dates ──────────────────────────────────────────────────────────

def test_month_end_dates_lands_on_last_trading_day_per_month():
    df = _price_df(date(2021, 1, 1), 70, drift=0.0)  # spans Jan-Apr 2021
    ends = _month_end_dates({'X': df})
    months_seen = sorted({(d.year, d.month) for d in ends})
    assert len(months_seen) >= 3
    for d in ends:
        next_day_same_month = df.filter(
            (pl.col('ts_event') > d) & (pl.col('ts_event').dt.month() == d.month)
        )
        assert next_day_same_month.height == 0


# ── run_tsmom_backtest (monkeypatched data) ─────────────────────────────────

def _patch_data(monkeypatch, price_data: dict, vix: pl.DataFrame):
    monkeypatch.setattr(tb, 'load_portfolio_data', lambda symbols: (price_data, vix))


def test_seeds_position_from_last_month_end_before_start_date(monkeypatch):
    """With start_date set, the first event should be a seed dated at the
    last month-end *before* start_date (not start_date itself), and the
    position should already be on as of day one of the window -- not
    flat-until-the-first-in-window-rebalance."""
    price_data = {'X': _price_df(date(2018, 1, 1), 460, drift=0.0015, vol=0.005, seed=1)}
    vix = _vix_df(date(2018, 1, 1), 460, level=15.0)
    _patch_data(monkeypatch, price_data, vix)
    monkeypatch.setattr(tb.FuturesType, 'from_symbol', staticmethod(lambda s: tb.FuturesType.ES))

    all_dates = sorted(price_data['X']['ts_event'].to_list())
    start_date = all_dates[-30]  # well past the 64-bar minimum, comfortably inside the series

    config = TsmomBacktestConfig(symbols=['X'], max_notional=50_000, max_contracts=5, start_date=start_date)
    result = run_tsmom_backtest(config)

    seed_events = [e for e in result['events'] if e['is_seed']]
    assert seed_events, "expected a seed event before start_date"
    for e in seed_events:
        assert e['date'] < start_date

    first_day_capital = result['stats'].filter(pl.col('date') == start_date)
    assert first_day_capital.height == 1
    # held_contracts must already reflect the seed target on day one --
    # confirmed indirectly: at least one symbol holds a nonzero position
    # immediately (no "wait for the next month-end" gap).
    held_after_seed = {e['symbol']: e['target_contracts'] for e in seed_events}
    assert any(v != 0 for v in held_after_seed.values())


def test_long_uptrend_produces_long_position(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    vix = _vix_df(date(2018, 1, 1), 500, level=15.0)
    _patch_data(monkeypatch, price_data, vix)

    config = TsmomBacktestConfig(symbols=['X'], max_notional=50_000, max_contracts=5)
    monkeypatch.setattr(tb.FuturesType, 'from_symbol', staticmethod(lambda s: tb.FuturesType.ES))

    result = run_tsmom_backtest(config)
    later_events = [e for e in result['events'] if e['target_contracts'] is not None][-5:]
    assert any(e['target_contracts'] > 0 for e in later_events)


def test_portfolio_capital_aggregates_across_symbols(monkeypatch):
    a = _price_df(date(2018, 1, 1), 400, drift=0.001, vol=0.005, seed=2)
    b = _price_df(date(2018, 1, 1), 400, drift=0.001, vol=0.005, seed=3)
    price_data = {'A': a, 'B': b}
    vix = _vix_df(date(2018, 1, 1), 400, level=15.0)
    _patch_data(monkeypatch, price_data, vix)
    monkeypatch.setattr(tb.FuturesType, 'from_symbol', staticmethod(lambda s: tb.FuturesType.ES))

    config_ab = TsmomBacktestConfig(symbols=['A', 'B'], max_notional=50_000, max_contracts=5)
    result_ab = run_tsmom_backtest(config_ab)

    # Single-symbol runs (same data/config) should sum to the same total
    # PnL as the combined two-symbol portfolio, confirming each symbol's
    # daily MTM contribution is aggregated additively, not overwritten.
    _patch_data(monkeypatch, {'A': a}, vix)
    result_a = run_tsmom_backtest(TsmomBacktestConfig(symbols=['A'], max_notional=50_000, max_contracts=5))
    _patch_data(monkeypatch, {'B': b}, vix)
    result_b = run_tsmom_backtest(TsmomBacktestConfig(symbols=['B'], max_notional=50_000, max_contracts=5))

    combined_pnl = result_a['stats']['cum_pnl'][-1] + result_b['stats']['cum_pnl'][-1]
    assert result_ab['stats']['cum_pnl'][-1] == pytest.approx(combined_pnl, abs=1.0)


def test_vix_spike_holds_positions_unchanged(monkeypatch):
    """ratio ~25/15=1.67 lands in the 'spike' band (not 'extreme') -- the
    gate should hold prior positions exactly, with signal computation
    skipped entirely (signal/regime both None on those events).
    Price data runs ~60 trading days past the (short) VIX spike, so the
    spike's rebalance date isn't the literal last date in the window
    (which the last-date-in-window cutoff excludes) and the rolling
    63-day VIX MA doesn't have time to absorb the spike before then."""
    price_data = {'X': _price_df(date(2018, 1, 1), 460, drift=0.0015, vol=0.005, seed=4)}
    base_vix = _vix_df(date(2018, 1, 1), 395, level=15.0)
    spike_vix = _vix_df(base_vix['date'][-1] + timedelta(days=1), 5, level=25.0)
    vix = pl.concat([base_vix, spike_vix])
    _patch_data(monkeypatch, price_data, vix)
    monkeypatch.setattr(tb.FuturesType, 'from_symbol', staticmethod(lambda s: tb.FuturesType.ES))

    config = TsmomBacktestConfig(symbols=['X'], max_notional=50_000, max_contracts=5)
    result = run_tsmom_backtest(config)

    spike_events = [e for e in result['events'] if e['vol_regime'] == 'spike']
    assert spike_events, "expected the synthetic VIX spike to trigger at least one gated rebalance"
    for e in spike_events:
        assert e['signal'] is None
        assert e['regime'] is None
        assert e['target_contracts'] == e['prior_contracts']


def test_vix_extreme_halves_positions(monkeypatch):
    """ratio ~35/15=2.33 lands in 'extreme' -- the gate should halve
    (round-to-nearest) the prior position rather than hold or resize via
    the signal. Same spike-then-runway construction as the 'spike' test
    above, just a higher level."""
    price_data = {'X': _price_df(date(2018, 1, 1), 460, drift=0.0015, vol=0.005, seed=4)}
    base_vix = _vix_df(date(2018, 1, 1), 395, level=15.0)
    spike_vix = _vix_df(base_vix['date'][-1] + timedelta(days=1), 5, level=35.0)
    vix = pl.concat([base_vix, spike_vix])
    _patch_data(monkeypatch, price_data, vix)
    monkeypatch.setattr(tb.FuturesType, 'from_symbol', staticmethod(lambda s: tb.FuturesType.ES))

    config = TsmomBacktestConfig(symbols=['X'], max_notional=50_000, max_contracts=5)
    result = run_tsmom_backtest(config)

    extreme_events = [e for e in result['events'] if e['vol_regime'] == 'extreme']
    assert extreme_events, "expected the synthetic VIX blowout to trigger at least one 'extreme' rebalance"
    for e in extreme_events:
        assert e['target_contracts'] == round(e['prior_contracts'] / 2)

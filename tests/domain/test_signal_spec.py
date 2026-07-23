"""
Tests for domain.signal_spec -- the modular SignalModel/WindowBasis layer
on top of tsmom_signal.calculate_trend_strength (which stays canonical and
untouched).
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.enums import SignalModel, TrendRegime, WindowBasis
from derivatives_bt_engine.domain.signal_spec import (
    GOULDING_FAST_MONTHS,
    GOULDING_SLOW_MONTHS,
    SignalSpec,
    compute_signal,
)
from derivatives_bt_engine.domain.tsmom_signal import calculate_trend_strength


def _trading_dates(start: date, n: int) -> list[date]:
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
    return pl.DataFrame({'ts_event': dates, 'close': close})


# ── SignalSpec validation ───────────────────────────────────────────────

def test_signal_spec_defaults_are_classic_observations():
    spec = SignalSpec()
    assert spec.model == SignalModel.CLASSIC_TS
    assert spec.window_basis == WindowBasis.OBSERVATIONS
    assert spec.fast_days == 63
    assert spec.slow_days == 252


def test_signal_spec_rejects_bad_model():
    with pytest.raises(ValueError):
        SignalSpec(model='not_a_model')


def test_signal_spec_rejects_bad_a_co_a_re():
    with pytest.raises(ValueError):
        SignalSpec(a_co=1.5)
    with pytest.raises(ValueError):
        SignalSpec(a_re=-0.1)


def test_goulding_factory_uses_paper_horizons():
    spec = SignalSpec.goulding()
    assert spec.model == SignalModel.GOULDING_DYNAMIC
    assert spec.fast_months == GOULDING_FAST_MONTHS == 2
    assert spec.slow_months == GOULDING_SLOW_MONTHS == 12
    assert spec.window_basis == WindowBasis.CALENDAR


# ── CLASSIC_TS + OBSERVATIONS: must reproduce calculate_trend_strength exactly ──

def test_classic_observations_matches_calculate_trend_strength_exactly():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=1)
    direct = calculate_trend_strength(df.clone())
    spec = SignalSpec()  # bare default
    via_spec = compute_signal(df.clone(), spec)

    for col in ('ts_fast', 'ts_slow', 'signal', 'dd', 'daily_std', 'hv'):
        a = direct[col].fill_null(-999.0).to_list()
        b = via_spec[col].fill_null(-999.0).to_list()
        assert a == pytest.approx(b), f"{col} diverged between calculate_trend_strength and compute_signal"


def test_classic_observations_annualization_days_pass_through():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=2)
    direct = calculate_trend_strength(df.clone(), annualization_days=259)
    via_spec = compute_signal(df.clone(), SignalSpec(annualization_days=259))
    a = direct['hv'].fill_null(-999.0).to_list()
    b = via_spec['hv'].fill_null(-999.0).to_list()
    assert a == pytest.approx(b)


# ── CLASSIC_TS + CALENDAR ────────────────────────────────────────────────

def test_classic_calendar_window_days_roughly_match_fast_months():
    df = _price_df(date(2018, 1, 1), 500, drift=0.0005, seed=3)
    spec = SignalSpec(window_basis=WindowBasis.CALENDAR, fast_months=3, slow_months=12)
    out = compute_signal(df, spec)
    wd = out['window_days_3m'].drop_nulls()
    assert len(wd) > 0
    # ~21 trading days/month * 3 -- allow a wide band since calendar months
    # vary in length and this is a business-day-only synthetic calendar.
    assert 55 <= wd.median() <= 70


def test_classic_calendar_produces_bounded_signal():
    df = _price_df(date(2018, 1, 1), 500, drift=0.002, vol=0.005, seed=4)
    spec = SignalSpec(window_basis=WindowBasis.CALENDAR)
    out = compute_signal(df, spec)
    signal = out['signal'].drop_nulls()
    assert len(signal) > 0
    assert signal.min() >= -1.0 and signal.max() <= 1.0


# ── GOULDING_DYNAMIC ─────────────────────────────────────────────────────

def test_goulding_observations_runs_and_bounds_signal():
    df = _price_df(date(2018, 1, 1), 400, drift=0.0015, vol=0.005, seed=5)
    out = compute_signal(df, SignalSpec.goulding(window_basis=WindowBasis.OBSERVATIONS))
    signal = out['signal'].drop_nulls()
    assert len(signal) > 0
    assert signal.min() >= -1.0 and signal.max() <= 1.0
    assert set(out['regime'].drop_nulls().unique().to_list()) <= {'BULL', 'BEAR', 'CORRECTION', 'REBOUND', 'UNKNOWN'}


def test_goulding_calendar_runs_and_bounds_signal():
    df = _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=6)
    out = compute_signal(df, SignalSpec.goulding(window_basis=WindowBasis.CALENDAR))
    signal = out['signal'].drop_nulls()
    assert len(signal) > 0
    assert signal.min() >= -1.0 and signal.max() <= 1.0


def test_goulding_flat_a_co_a_re_zeros_out_disagreement_states():
    # a_co=a_re=0.5 (the default) must make Correction/Rebound rows exactly
    # flat (weight 0), per eq. 7's own derivation.
    df = _price_df(date(2018, 1, 1), 400, drift=0.0, vol=0.01, seed=7)
    out = compute_signal(df, SignalSpec.goulding(window_basis=WindowBasis.OBSERVATIONS, a_co=0.5, a_re=0.5))
    dis = out.filter(pl.col('regime').is_in(['CORRECTION', 'REBOUND']))
    if dis.height > 0:
        assert dis['signal'].abs().max() < 1e-9


def test_goulding_nonflat_a_co_a_re_biases_disagreement_states():
    df = _price_df(date(2018, 1, 1), 400, drift=0.0, vol=0.01, seed=7)
    out = compute_signal(df, SignalSpec.goulding(window_basis=WindowBasis.OBSERVATIONS, a_co=0.2, a_re=0.8))
    correction = out.filter(pl.col('regime') == 'CORRECTION')
    rebound = out.filter(pl.col('regime') == 'REBOUND')
    if correction.height > 0:
        # a_co < 0.5 -> weight = 1 - 2*a_co > 0 (tilt toward slow/long)
        assert (correction['signal'] > 0).all()
    if rebound.height > 0:
        # a_re > 0.5 -> weight = 2*a_re - 1 > 0 (tilt toward fast/long)
        assert (rebound['signal'] > 0).all()


def test_unsupported_combination_raises():
    # A valid spec must NOT raise from compute_signal's own dispatch.
    compute_signal(_price_df(date(2018, 1, 1), 100, drift=0.0), SignalSpec())
    # Bypassing __post_init__'s validation (direct attribute mutation after
    # construction) and asking compute_signal to dispatch on it anyway must
    # still fail loudly, not silently return something wrong.
    spec = SignalSpec()
    spec.model = 'bogus'
    with pytest.raises(ValueError):
        compute_signal(_price_df(date(2018, 1, 1), 100, drift=0.0), spec)

"""
Tests for ib_tools.tsmom_signal — pure TSMOM math, no IB dependency.
"""

import math

import numpy as np
import polars as pl
import pytest

from options_bt.domain.tsmom_signal import (
    calculate_trend_strength,
    classify_regime,
    compute_position_scalar,
)


def _price_df(n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 4000 * np.exp(np.cumsum(rets))
    return pl.DataFrame({'close': close})


# ── calculate_trend_strength ────────────────────────────────────────────────

def test_trend_strength_columns_present():
    df = calculate_trend_strength(_price_df(400, drift=0.001))
    for col in ('trend_strength', 'ts3m', 'ts1y', 'daily_std', 'r1y_pct', 'dd', 'peak'):
        assert col in df.columns
    # dropped intermediate/raw columns
    for col in ('log_price', 'r1d', 'w3', 'w1'):
        assert col not in df.columns


def test_trend_strength_null_until_63_bars():
    df = calculate_trend_strength(_price_df(100, drift=0.0))
    # before 63 bars, ts3m/trend_strength must be null
    assert df['trend_strength'][:63].null_count() == 63
    assert df['trend_strength'][63:].null_count() == 0


def test_trend_strength_sign_matches_strong_uptrend():
    df = calculate_trend_strength(_price_df(400, drift=0.003, vol=0.005))
    last = df.tail(1)['trend_strength'][0]
    assert last > 0


def test_trend_strength_sign_matches_strong_downtrend():
    df = calculate_trend_strength(_price_df(400, drift=-0.003, vol=0.005))
    last = df.tail(1)['trend_strength'][0]
    assert last < 0


def test_trend_strength_bounded():
    df = calculate_trend_strength(_price_df(400, drift=0.0005, vol=0.01))
    vals = df['trend_strength'].drop_nulls()
    assert vals.min() >= -1.0
    assert vals.max() <= 1.0


def test_trend_strength_falls_back_to_ts3m_before_252_bars():
    # between 63 and 252 bars, ts1y is null so w1=0 and the signal should
    # equal tanh(ts3m) exactly (w3/(w3+0) == 1)
    df = calculate_trend_strength(_price_df(150, drift=0.002, vol=0.005))
    row = df.tail(1)
    ts3m = row['ts3m'][0]
    ts1y = row['ts1y'][0]
    trend = row['trend_strength'][0]
    assert ts1y is None
    assert math.isclose(trend, math.tanh(ts3m), rel_tol=1e-9)


# ── classify_regime ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('ts1y,ts3m,expected', [
    (1.0, 1.0, 'Bull'),
    (1.0, -1.0, 'Correction'),
    (-1.0, -1.0, 'Bear'),
    (-1.0, 1.0, 'Rebound'),
])
def test_classify_regime(ts1y, ts3m, expected):
    assert classify_regime(ts3m, ts1y) == expected


def test_classify_regime_unknown_on_none():
    assert classify_regime(None, 1.0) == 'Unknown'
    assert classify_regime(1.0, None) == 'Unknown'


def test_classify_regime_unknown_on_nan():
    assert classify_regime(float('nan'), 1.0) == 'Unknown'


def test_classify_regime_unknown_on_zero():
    assert classify_regime(0.0, 1.0) == 'Unknown'
    assert classify_regime(1.0, 0.0) == 'Unknown'


# ── compute_position_scalar ──────────────────────────────────────────────────

def test_position_scalar_sign_follows_trend_strength():
    pos = compute_position_scalar(0.5, 0.01, vol_target=0.15, regime='Bull')
    neg = compute_position_scalar(-0.5, 0.01, vol_target=0.15, regime='Bear')
    assert pos > 0
    assert neg < 0


def test_position_scalar_clamped_to_unit_range():
    # extreme trend_strength * max vol_scalar must still clamp to [-1, 1]
    scalar = compute_position_scalar(1.0, daily_std_last=0.0001, vol_target=0.50, regime='Bull')
    assert -1.0 <= scalar <= 1.0
    assert scalar == 1.0  # vol_scalar clamps to 2.0, but final result re-clamps


def test_position_scalar_vol_scalar_clamp_floor():
    # very high realized vol should clamp vol_scalar to the 0.25 floor, not go to ~0
    scalar = compute_position_scalar(1.0, daily_std_last=1.0, vol_target=0.15, regime='Bull')
    assert math.isclose(scalar, 0.25, rel_tol=1e-6)


def test_position_scalar_discount_applied_for_disagreement_regimes():
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime='Bull', regime_discount=0.5)
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime='Correction', regime_discount=0.5)
    assert math.isclose(correction, bull * 0.5, rel_tol=1e-9)


def test_position_scalar_discount_disabled_at_1():
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime='Correction', regime_discount=1.0)
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime='Bull', regime_discount=1.0)
    assert math.isclose(correction, bull, rel_tol=1e-9)


def test_position_scalar_zero_on_null_trend_strength():
    assert compute_position_scalar(None, 0.01, vol_target=0.15, regime='Bull') == 0.0
    assert compute_position_scalar(float('nan'), 0.01, vol_target=0.15, regime='Bull') == 0.0


def test_position_scalar_neutral_vol_scalar_on_missing_std():
    # daily_std_last unusable -> vol_scalar treated as neutral (1.0), not a crash
    scalar = compute_position_scalar(0.4, None, vol_target=0.15, regime='Bull')
    assert math.isclose(scalar, 0.4, rel_tol=1e-9)

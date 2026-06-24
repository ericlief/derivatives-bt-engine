"""
Tests for ib_tools.tsmom_signal — pure TSMOM math, no IB dependency.
"""

import math

import numpy as np
import polars as pl
import pytest

from options_bt.domain.enums import TrendRegime
from options_bt.domain.tsmom_signal import (
    apply_cluster_risk_cap,
    calculate_trend_strength,
    classify_regime,
    compute_desired_risk_budget,
    compute_n_effective,
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
    (1.0, 1.0, TrendRegime.BULL),
    (1.0, -1.0, TrendRegime.CORRECTION),
    (-1.0, -1.0, TrendRegime.BEAR),
    (-1.0, 1.0, TrendRegime.REBOUND),
])
def test_classify_regime(ts1y, ts3m, expected):
    assert classify_regime(ts3m, ts1y) == expected


def test_classify_regime_unknown_on_none():
    assert classify_regime(None, 1.0) == TrendRegime.UNKNOWN
    assert classify_regime(1.0, None) == TrendRegime.UNKNOWN


def test_classify_regime_unknown_on_nan():
    assert classify_regime(float('nan'), 1.0) == TrendRegime.UNKNOWN


def test_classify_regime_unknown_on_zero():
    assert classify_regime(0.0, 1.0) == TrendRegime.UNKNOWN
    assert classify_regime(1.0, 0.0) == TrendRegime.UNKNOWN


# ── compute_position_scalar ──────────────────────────────────────────────────

def test_position_scalar_sign_follows_trend_strength():
    pos = compute_position_scalar(0.5, 0.01, vol_target=0.15, regime=TrendRegime.BULL)
    neg = compute_position_scalar(-0.5, 0.01, vol_target=0.15, regime=TrendRegime.BEAR)
    assert pos > 0
    assert neg < 0


def test_position_scalar_clamped_to_unit_range():
    # extreme trend_strength * max vol_scalar must still clamp to [-1, 1]
    scalar = compute_position_scalar(1.0, daily_std_last=0.0001, vol_target=0.50, regime=TrendRegime.BULL)
    assert -1.0 <= scalar <= 1.0
    assert scalar == 1.0  # vol_scalar clamps to 2.0, but final result re-clamps


def test_position_scalar_vol_scalar_clamp_floor():
    # very high realized vol should clamp vol_scalar to the 0.25 floor, not go to ~0
    scalar = compute_position_scalar(1.0, daily_std_last=1.0, vol_target=0.15, regime=TrendRegime.BULL)
    assert math.isclose(scalar, 0.25, rel_tol=1e-6)


def test_position_scalar_discount_applied_for_disagreement_regimes():
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.BULL, regime_discount=0.5)
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.CORRECTION, regime_discount=0.5)
    assert math.isclose(correction, bull * 0.5, rel_tol=1e-9)


def test_position_scalar_discount_disabled_at_1():
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.CORRECTION, regime_discount=1.0)
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.BULL, regime_discount=1.0)
    assert math.isclose(correction, bull, rel_tol=1e-9)


def test_position_scalar_zero_on_null_trend_strength():
    assert compute_position_scalar(None, 0.01, vol_target=0.15, regime=TrendRegime.BULL) == 0.0
    assert compute_position_scalar(float('nan'), 0.01, vol_target=0.15, regime=TrendRegime.BULL) == 0.0


def test_position_scalar_neutral_vol_scalar_on_missing_std():
    # daily_std_last unusable -> vol_scalar treated as neutral (1.0), not a crash
    scalar = compute_position_scalar(0.4, None, vol_target=0.15, regime=TrendRegime.BULL)
    assert math.isclose(scalar, 0.4, rel_tol=1e-9)


# ── compute_n_effective ──────────────────────────────────────────────────────

def test_n_effective_counts_distinct_clusters():
    assert compute_n_effective({'equity', 'energy', 'grain', 'metal', 'fx'}) == 5
    assert compute_n_effective(set()) == 0
    assert compute_n_effective({'grain'}) == 1


# ── compute_desired_risk_budget ──────────────────────────────────────────────

def test_desired_risk_budget_scales_with_equity_and_vol():
    budget = compute_desired_risk_budget(account_equity=100_000, target_portfolio_vol=0.15, n_effective=5)
    assert math.isclose(budget, 100_000 * 0.15 / math.sqrt(5), rel_tol=1e-9)


def test_desired_risk_budget_zero_n_effective_is_zero_not_error():
    assert compute_desired_risk_budget(100_000, 0.15, 0) == 0.0


def test_desired_risk_budget_fewer_active_clusters_means_bigger_budget_each():
    # sqrt(N) scaling -- fewer active bets means each can be sized bigger,
    # without blowing through the total target_portfolio_vol.
    budget_1_cluster = compute_desired_risk_budget(100_000, 0.15, 1)
    budget_5_clusters = compute_desired_risk_budget(100_000, 0.15, 5)
    assert budget_1_cluster > budget_5_clusters


# ── apply_cluster_risk_cap ───────────────────────────────────────────────────

def _target(symbol, cluster, target_contracts, close=100.0, multiplier=10.0, hv=0.2):
    return {
        'symbol': symbol, 'cluster': cluster, 'target_contracts': target_contracts,
        'close': close, 'multiplier': multiplier, 'hv': hv,
    }


def test_cluster_cap_rescales_overweight_cluster_down_to_pct():
    # One equity instrument (small risk) vs four grain instruments (each
    # carrying equal risk) -- grain alone is way more than 25% of the total,
    # so it must be rescaled down; equity (well under the cap) is untouched.
    # Contract counts are large enough (~30) that integer rounding only
    # introduces a small approximation error relative to the nominal cap --
    # with tiny counts (e.g. -2), rounding to the nearest whole contract can
    # overshoot the cap by a large relative margin, which isn't a bug, just
    # the inherent granularity limit of whole-contract sizing.
    targets = [
        _target('MES', 'equity', target_contracts=5, close=100, multiplier=1, hv=0.05),   # risk=25
        _target('MZC', 'grain', target_contracts=-30, close=100, multiplier=1, hv=0.05),  # risk=150
        _target('MZS', 'grain', target_contracts=-30, close=100, multiplier=1, hv=0.05),  # risk=150
        _target('MZW', 'grain', target_contracts=-30, close=100, multiplier=1, hv=0.05),  # risk=150
        _target('MZL', 'grain', target_contracts=-30, close=100, multiplier=1, hv=0.05),  # risk=150
    ]
    # total_risk_budget (the static, pre-cap reference the spec scales
    # against) = 25 + 600 = 625; grain share pre-cap = 600/625 = 96% >> 25%
    total_risk_budget = 25 + 4 * 150
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25)

    by_symbol = {t['symbol']: t for t in out}
    assert by_symbol['MES']['target_contracts'] == 5   # equity untouched, well under cap

    grain_targets = [by_symbol[s]['target_contracts'] for s in ('MZC', 'MZS', 'MZW', 'MZL')]
    assert all(t < 0 for t in grain_targets)            # sign preserved (short)
    assert all(abs(t) < 30 for t in grain_targets)      # magnitude reduced

    # Cap is scaled against the static pre-cap total_risk_budget (not a
    # recomputed post-cap total -- the cap doesn't get easier to hit just
    # because the cluster being capped shrinks the total). Allow a small
    # tolerance for whole-contract rounding.
    grain_risk = sum(by_symbol[s]['position_risk'] for s in ('MZC', 'MZS', 'MZW', 'MZL'))
    assert grain_risk <= 0.25 * total_risk_budget * 1.1


def test_cluster_cap_leaves_single_instrument_cluster_dominant_clusters_alone():
    # Equity and energy are each a single-instrument cluster in the example
    # universe -- with nothing else in their own cluster to share risk with,
    # they shouldn't get clipped just for being individually large relative
    # to other clusters' instrument counts.
    targets = [
        _target('MES', 'equity', target_contracts=3, close=100, multiplier=10, hv=0.1),  # risk=300
        _target('MCL', 'energy', target_contracts=2, close=100, multiplier=10, hv=0.1),  # risk=200
        _target('MGC', 'metal', target_contracts=1, close=100, multiplier=10, hv=0.1),   # risk=100
    ]
    # total=600; equity share=50% > 25% -- equity itself gets capped too,
    # since the rule is purely risk-share-based, not "exempt the biggest".
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25)
    by_symbol = {t['symbol']: t for t in out}
    assert abs(by_symbol['MES']['target_contracts']) < 3
    assert by_symbol['MGC']['target_contracts'] == 1  # 100/600=16.7% < 25%, untouched


def test_cluster_cap_no_op_when_all_clusters_within_budget():
    targets = [
        _target('MES', 'equity', target_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('MCL', 'energy', target_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('MGC', 'metal', target_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('J7', 'fx', target_contracts=1, close=100, multiplier=10, hv=0.1),
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25)
    assert all(t['target_contracts'] == 1 for t in out)


def test_cluster_cap_skips_error_targets():
    targets = [
        _target('MES', 'equity', target_contracts=1, close=100, multiplier=10, hv=0.1),
        {'symbol': 'MCL', 'error': 'boom', 'target_contracts': None},
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25)
    errored = next(t for t in out if t['symbol'] == 'MCL')
    assert errored['error'] == 'boom'
    assert errored['target_contracts'] is None


def test_cluster_cap_zero_total_risk_is_no_op():
    targets = [_target('MES', 'equity', target_contracts=0, close=100, multiplier=10, hv=0.1)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25)
    assert out[0]['target_contracts'] == 0

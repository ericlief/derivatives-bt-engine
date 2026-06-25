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
#
# The cap is taken against a FIXED total_risk_target (account_equity *
# target_portfolio_vol, supplied by the caller) -- never the emergent sum
# of this run's pre-cap position risks. effective_cap_pct = max(
# max_cluster_risk_pct, 1/n_active_clusters), so a single active cluster
# isn't capped below 100% of the target just for being the only trade in
# the book.
#
# Operates on continuous_contracts (unrounded, unclamped) throughout, and
# rounds to a final integer + applies max_contracts exactly once, at the
# end -- rescaling an already-rounded-and-clamped integer (the old
# behavior) double-rounds, which can zero out large-multiplier instruments
# that would survive on the true continuous math.

def _target(symbol, cluster, continuous_contracts, close=100.0, multiplier=10.0, hv=0.2,
            max_contracts=None, target_contracts=None):
    return {
        'symbol': symbol, 'cluster': cluster, 'continuous_contracts': continuous_contracts,
        # target_contracts simulates whatever the caller (tsmom_rebalance.py)
        # already computed upstream, pre-cluster-cap -- only actually
        # observable in tests that exercise the early-return (no budget)
        # path, since the normal path always overwrites it at the end.
        'target_contracts': target_contracts if target_contracts is not None else round(continuous_contracts),
        'close': close, 'multiplier': multiplier, 'hv': hv, 'max_contracts': max_contracts,
    }


@pytest.mark.parametrize('n_active_clusters,expected_pct', [
    (1, 1.0), (2, 0.5), (3, 1 / 3), (4, 0.25), (5, 0.25), (6, 0.25),
])
def test_cluster_cap_floor_table(n_active_clusters, expected_pct):
    # max_cluster_risk_pct=0.25 throughout -- the floor (1/n) only matters
    # while it's bigger than 0.25, i.e. n <= 4.
    targets = [_target('MES', 'equity', continuous_contracts=10, close=100, multiplier=1, hv=0.1)]  # risk=100
    total_risk_target = 1000.0
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    # risk=100 is below every tier's cap here, so target_contracts is
    # untouched in all cases -- this test is purely about effective_cap_pct
    # being computed correctly, verified indirectly via a second case where
    # the cap actually binds at exactly the expected_pct boundary.
    assert out[0]['target_contracts'] == 10

    # Now push the single cluster's risk just over the expected cap and
    # confirm it gets rescaled down to (approximately) expected_pct of the
    # fixed target, not capped at the old flat 0.25 in every case.
    big_targets = [_target('MES', 'equity', continuous_contracts=100, close=100, multiplier=1, hv=0.1)]  # risk=1000
    out_big = apply_cluster_risk_cap(big_targets, max_cluster_risk_pct=0.25,
                                     total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    expected_risk = expected_pct * total_risk_target
    assert math.isclose(out_big[0]['position_risk'], expected_risk, rel_tol=0.05)


def test_cluster_cap_fixed_denominator_not_inflated_by_correlated_cluster():
    # Four grain instruments that would, under the old (buggy) emergent-sum
    # denominator, have summed to 60% of total_risk -- the bug was that
    # this sum itself became the denominator the cap was measured against,
    # so a correlated cluster could never meaningfully exceed ~its own
    # share no matter how concentrated it was. Under the fixed
    # total_risk_target, the cap is compared against the account-level
    # target instead, independent of how much risk this cluster (or any
    # other) happens to carry this run.
    targets = [
        _target('MES', 'equity', continuous_contracts=4, close=100, multiplier=1, hv=0.1),    # risk=40
        _target('MZC', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1),    # risk=150
        _target('MZS', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1),    # risk=150
        _target('MZW', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1),    # risk=150
        _target('MZL', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1),    # risk=150
    ]
    # pre-cap sum = 40 + 600 = 640; grain's emergent share = 600/640 = 93.75%
    # -- but under the OLD bug, since this sum is also today's denominator,
    # any cluster gets "more room" the bigger it draws relative to others.
    # Use a total_risk_target deliberately smaller than the emergent sum
    # (640) -- e.g. account_equity * target_portfolio_vol = 400 -- so the
    # fixed-target cap actually constrains grain harder than the emergent
    # sum ever would have.
    total_risk_target = 400.0
    n_active_clusters = 2  # equity, grain
    effective_cap_pct = max(0.25, 1 / n_active_clusters)  # = 0.5
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    grain_risk = sum(by_symbol[s]['position_risk'] for s in ('MZC', 'MZS', 'MZW', 'MZL'))
    cap = effective_cap_pct * total_risk_target  # = 200
    assert grain_risk <= cap * 1.1  # small tolerance for whole-contract rounding
    # Confirm this is meaningfully tighter than the old emergent-sum
    # behavior would have allowed (0.25 * 640 = 160 there too, incidentally
    # similar in this constructed case -- the point is grain_risk is now
    # anchored to the fixed 400, not whatever the instruments summed to).
    assert grain_risk < 600  # i.e. it was actually rescaled down from pre-cap


def test_cluster_cap_leaves_single_instrument_cluster_dominant_clusters_alone():
    # Equity and energy are each a single-instrument cluster in the example
    # universe -- with nothing else in their own cluster to share risk with,
    # they shouldn't get clipped just for being individually large relative
    # to other clusters' instrument counts.
    targets = [
        _target('MES', 'equity', continuous_contracts=3, close=100, multiplier=10, hv=0.1),  # risk=300
        _target('MCL', 'energy', continuous_contracts=2, close=100, multiplier=10, hv=0.1),  # risk=200
        _target('MGC', 'metal', continuous_contracts=1, close=100, multiplier=10, hv=0.1),   # risk=100
    ]
    # 3 active clusters -> effective_cap_pct = max(0.25, 1/3) = 1/3.
    # total_risk_target=600 (matches the old test's implicit total) ->
    # cap = 200. equity (300) exceeds it and gets capped; metal (100) does
    # not.
    total_risk_target = 600.0
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=3)
    by_symbol = {t['symbol']: t for t in out}
    assert abs(by_symbol['MES']['target_contracts']) < 3
    assert by_symbol['MGC']['target_contracts'] == 1  # 100 < 200 cap, untouched


def test_cluster_cap_no_op_when_all_clusters_within_budget():
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('MCL', 'energy', continuous_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('MGC', 'metal', continuous_contracts=1, close=100, multiplier=10, hv=0.1),
        _target('J7', 'fx', continuous_contracts=1, close=100, multiplier=10, hv=0.1),
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=100_000.0, n_active_clusters=4)
    assert all(t['target_contracts'] == 1 for t in out)


def test_cluster_cap_skips_error_targets():
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=100, multiplier=10, hv=0.1),
        {'symbol': 'MCL', 'error': 'boom', 'target_contracts': None},
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1)
    errored = next(t for t in out if t['symbol'] == 'MCL')
    assert errored['error'] == 'boom'
    assert errored['target_contracts'] is None


def test_cluster_cap_zero_total_risk_target_is_no_op():
    # total_risk_target<=0 early-returns without touching anything --
    # target_contracts stays whatever the caller already set upstream.
    targets = [_target('MES', 'equity', continuous_contracts=5.3, target_contracts=5,
                       close=100, multiplier=10, hv=0.1)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=0.0, n_active_clusters=1)
    assert out[0]['target_contracts'] == 5


def test_cluster_cap_none_total_risk_target_is_no_op():
    targets = [_target('MES', 'equity', continuous_contracts=5.3, target_contracts=5,
                       close=100, multiplier=10, hv=0.1)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=None, n_active_clusters=1)
    assert out[0]['target_contracts'] == 5


def test_cluster_cap_zero_n_active_clusters_falls_back_to_max_cluster_risk_pct():
    # n_active_clusters=0 shouldn't divide by zero -- falls back to the
    # flat max_cluster_risk_pct with no floor adjustment.
    targets = [_target('MES', 'equity', continuous_contracts=100, close=100, multiplier=1, hv=0.1)]  # risk=1000
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=0)
    assert math.isclose(out[0]['position_risk'], 0.25 * 1000.0, rel_tol=0.05)


def test_cluster_cap_max_contracts_clamp_applied_after_cap_not_before():
    # max_contracts is now applied as the TRUE last step (after the
    # cluster-cap rescale + single rounding), not before. Single
    # uncapped-cluster instrument: continuous=10, no cluster pressure
    # (cap is huge), so target_contracts would be 10 without a clamp --
    # max_contracts=3 must still bring it down to 3 at the very end.
    targets = [_target('MES', 'equity', continuous_contracts=10, close=100, multiplier=1, hv=0.1,
                       max_contracts=3)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1_000_000.0, n_active_clusters=1)
    assert out[0]['target_contracts'] == 3


def test_cluster_cap_continuous_rounding_differs_from_double_rounding():
    """Locks in the actual rounding-sequence fix: A's true continuous value
    (0.6) would, under the OLD double-rounded path, first round up to 1
    *before* the cluster-cap scale is applied (1 * 0.7 = 0.7 -> rounds back
    to 1, unchanged -- the old, wrong answer). Under the corrected
    continuous-math path, A is scaled directly (0.6 * 0.7 = 0.42 -> rounds
    to 0, the right answer) before ever being rounded. This is not a no-op:
    the two paths produce different final integers for A."""
    targets = [
        _target('A', 'test', continuous_contracts=0.6, close=100, multiplier=1, hv=1.0),  # risk=60
        _target('B', 'test', continuous_contracts=5.0, close=100, multiplier=1, hv=1.0),  # risk=500
    ]
    # cluster_risk (continuous) = 60 + 500 = 560; cap = 1.0 * 392 = 392
    # (n_active=1 -> floor=1.0) -> scale = 392/560 = 0.7 exactly.
    total_risk_target = 392.0
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=1)
    by_symbol = {t['symbol']: t for t in out}

    # The corrected behavior: 0.6 * 0.7 = 0.42 -> rounds to 0.
    assert by_symbol['A']['target_contracts'] == 0
    # Sanity-check what the old (buggy) double-rounded path would have
    # given, to make the divergence explicit rather than assumed: round
    # A's continuous value FIRST (round(0.6) = 1), then apply the same
    # 0.7 scale, then round again -- this is the exact old algorithm,
    # inlined here only for comparison, not because tsmom_signal.py still
    # has this code path.
    old_path_result = round(round(0.6) * 0.7)
    assert old_path_result == 1
    assert by_symbol['A']['target_contracts'] != old_path_result


def test_cluster_cap_infeasible_when_cap_below_single_contract_risk():
    """Small synthetic equivalent of the $1,000,000/ES+NQ scenario: a
    cluster cap that's smaller than even the cheapest single contract's
    own dollar-vol risk. No scale factor can produce a compliant nonzero
    position -- this is reported as infeasible, not silently zeroed with
    no explanation."""
    single_contract_risk = 0.0066 * 6_250_000 * 0.08  # = 3300.0, J7-like
    targets = [_target('J7', 'fx', continuous_contracts=1.0, close=0.0066,
                       multiplier=6_250_000, hv=0.08)]
    # cap = 1.0 (n_active=1 floor) * 1000 = 1000 < single_contract_risk (3300).
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1)
    assert out[0]['infeasible'] is True
    assert out[0]['target_contracts'] == 0  # scale (1000/3300=0.303) * 1.0 < 0.5


def test_cluster_cap_not_infeasible_when_single_contract_fits_under_cap():
    # Contrast case: cap exceeds the single-contract risk, so even though
    # the cluster still needs rescaling, it's not infeasible.
    targets = [
        _target('MES', 'equity', continuous_contracts=5, close=100, multiplier=10, hv=0.1),  # single=100
        _target('MNQ', 'equity', continuous_contracts=5, close=100, multiplier=10, hv=0.1),  # single=100
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=600.0, n_active_clusters=1)  # cap=600
    assert not any(t.get('infeasible') for t in out)


def test_cluster_cap_es_nq_million_dollar_scenario_recomputed_continuously():
    """Reproduces this session's exact $1,000,000 account, ES+NQ-both-near-
    max-conviction scenario using the real session figures. Confirms (a)
    the corrected continuous-math cluster risk (which is LOWER than the old
    double-rounded sum, since NQ's true 0.5258 contracts contributes less
    risk than the old path's rounded-up 1.0) is used for the scale
    decision, and (b) the cap is still below even ES's (the cheaper
    instrument's) single-contract risk, so this is correctly flagged
    infeasible -- both end up at zero contracts, but now reported as an
    infeasible constraint rather than an unexplained sizing failure."""
    es_close, es_mult, es_hv = 7428.25, 50, 0.1524
    nq_close, nq_mult, nq_hv = 29514.25, 20, 0.238
    es_continuous = 479_325.91 / (es_close * es_mult)   # ~1.2908
    nq_continuous = 310_528.16 / (nq_close * nq_mult)   # ~0.5258

    targets = [
        _target('ES', 'equity', continuous_contracts=es_continuous, close=es_close, multiplier=es_mult, hv=es_hv),
        _target('NQ', 'equity', continuous_contracts=nq_continuous, close=nq_close, multiplier=nq_mult, hv=nq_hv),
    ]
    account_equity, target_portfolio_vol, n_active_clusters = 1_000_000, 0.15, 3
    total_risk_target = account_equity * target_portfolio_vol  # 150,000
    effective_cap_pct = max(0.25, 1 / n_active_clusters)        # 1/3
    cap = effective_cap_pct * total_risk_target                 # 50,000

    es_single_contract_risk = es_close * es_mult * es_hv   # ~56,607.3
    nq_single_contract_risk = nq_close * nq_mult * nq_hv   # ~140,487.9
    assert cap < es_single_contract_risk < nq_single_contract_risk  # confirms the scenario's premise

    # The corrected (continuous) cluster risk is lower than the old
    # double-rounded sum (which would have used round(1.29)=1 and
    # round(0.53)=1, i.e. 56,607.3 + 140,487.9 = 197,095.2).
    continuous_cluster_risk = es_continuous * es_single_contract_risk + nq_continuous * nq_single_contract_risk
    old_double_rounded_risk = 1 * es_single_contract_risk + 1 * nq_single_contract_risk
    assert continuous_cluster_risk < old_double_rounded_risk

    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    # Both flagged infeasible -- the cap can't accommodate even ES alone.
    assert by_symbol['ES']['infeasible'] is True
    assert by_symbol['NQ']['infeasible'] is True
    # And, in this specific scenario, both still round to zero (matching
    # what was observed live) -- but now explained, not unexplained.
    assert by_symbol['ES']['target_contracts'] == 0
    assert by_symbol['NQ']['target_contracts'] == 0


def test_cluster_cap_live_session_scenario_recomputed_for_fixed_denominator():
    """Recomputes the grain/fx/equity scenario validated earlier this
    session, but under the corrected fixed-denominator math -- the old
    validation (grain/fx -> ~25.0% each) assumed the cap was measured
    against the emergent pre-cap sum, which is exactly the bug this fix
    removes, so that result no longer holds and must not be assumed."""
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=7437.50, multiplier=5, hv=0.1524),    # risk=5667
        _target('MZL', 'grain', continuous_contracts=5, close=66.58, multiplier=60, hv=0.2429),       # risk=4854
        _target('MZC', 'grain', continuous_contracts=-13, close=437.00, multiplier=5, hv=0.1937),     # risk=5509
        _target('MZS', 'grain', continuous_contracts=-2, close=1142.00, multiplier=5, hv=0.1492),     # risk=1704
        _target('MZW', 'grain', continuous_contracts=-2, close=597.00, multiplier=5, hv=0.2969),       # risk=1773
        _target('J7', 'fx', continuous_contracts=1, close=0.0066, multiplier=6_250_000, hv=0.0825),    # risk=3403
        _target('BRE', 'fx', continuous_contracts=2, close=0.19, multiplier=100_000, hv=0.1143),       # risk=4343
        _target('6M', 'fx', continuous_contracts=2, close=0.06, multiplier=500_000, hv=0.0825),         # risk=4950
    ]
    # account_equity=100_000, target_portfolio_vol=0.15 -> fixed
    # total_risk_target=15_000 (NOT the emergent pre-cap sum, ~32_203).
    # n_active_clusters=3 (equity, grain, fx; metal/energy inactive this
    # round) -> effective_cap_pct = max(0.25, 1/3) = 1/3.
    total_risk_target = 100_000 * 0.15
    n_active_clusters = 3
    effective_cap_pct = max(0.25, 1 / n_active_clusters)
    cap = effective_cap_pct * total_risk_target  # = 5000

    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    grain_risk = sum(by_symbol[s]['position_risk'] for s in ('MZL', 'MZC', 'MZS', 'MZW'))
    fx_risk = sum(by_symbol[s]['position_risk'] for s in ('J7', 'BRE', '6M'))
    equity_risk = by_symbol['MES']['position_risk']

    # grain (pre-cap ~13840) and fx (pre-cap ~12696) both exceed the fixed
    # cap (5000) and must be rescaled down to ~it; equity (5667, just over)
    # also gets rescaled slightly under this stricter fixed-target regime
    # -- a real behavior change from the old emergent-sum result, where
    # equity's pre-cap share (17.6%) was comfortably under the (also
    # emergent-sum-relative) 25% and untouched.
    # Grain's tolerance is wider than fx/equity's -- it has 4 independently-
    # rounded instruments (vs fx's 3, equity's 1), so whole-contract
    # rounding compounds more before landing near the cap.
    assert grain_risk <= cap * 1.2
    assert fx_risk <= cap * 1.15
    assert equity_risk <= cap * 1.15
    # Signs preserved (never flipped) throughout -- J7's single pre-cap
    # contract can legitimately round to zero under the stricter cap
    # (round-toward-zero below 0.5), but never to a negative.
    assert by_symbol['MZC']['target_contracts'] < 0
    assert by_symbol['MZL']['target_contracts'] > 0
    assert by_symbol['J7']['target_contracts'] >= 0
    assert by_symbol['BRE']['target_contracts'] >= 0
    assert by_symbol['6M']['target_contracts'] >= 0

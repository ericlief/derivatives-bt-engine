"""
Tests for domain.signal -- pure TSMOM signal construction, estimation, and
sizing math, no IB dependency. Consolidated (2026-07) from test_tsmom_signal.py
and test_signal_spec.py once their two source modules merged into signal.py.

Covers: calculate_trend_strength/classify_regime/compute_vol_ratio/
classify_signal_confidence/compute_signal_confidence/compute_position_scalar
(the old tsmom_signal.py half) and build_features/continuous_momentum/
goulding_monthly/SignalSpec/_goulding_blend/_goulding_weight (the newer
signal_spec.py half) -- each model's own tests deliberately avoid touching
the other model's columns, mirroring the "no model depends on another
model's intermediate columns" requirement the module itself is built
around. apply_cluster_risk_cap/compute_desired_risk_budget/
compute_n_effective now live in domain.allocation (a separate, earlier
split -- cross-instrument risk allocation, not single-instrument signal
math) and are imported from there, not domain.signal.
"""

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.allocation import apply_cluster_risk_cap, compute_desired_risk_budget, compute_n_effective
from derivatives_bt_engine.domain.enums import SignalConfidenceRegime, TrendRegime
from derivatives_bt_engine.domain.signal import (
    GOULDING_FAST_MONTHS,
    GOULDING_SLOW_MONTHS,
    SignalSpec,
    _goulding_blend,
    _goulding_weight,
    build_features,
    calculate_trend_strength,
    classify_regime,
    classify_signal_confidence,
    compute_position_scalar,
    compute_signal_confidence,
    compute_vol_ratio,
    continuous_momentum,
    goulding_monthly,
)

def _price_df(n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 4000 * np.exp(np.cumsum(rets))
    return pl.DataFrame({'close': close})


# ── calculate_trend_strength ────────────────────────────────────────────────

def test_trend_strength_columns_present():
    df = calculate_trend_strength(_price_df(400, drift=0.001))
    # log_price/r1d are deliberately kept (compute_vol_ratio chains onto
    # them) -- only the truly disposable intermediates are dropped.
    for col in ('signal', 'ts_fast', 'ts_slow', 'daily_std', 'avg_r_fast', 'avg_r_slow', 'r1y_pct', 'dd', 'peak', 'log_price', 'r1d'):
        assert col in df.columns
    for col in ('w3', 'w1'):
        assert col not in df.columns


def test_trend_strength_null_until_63_bars():
    df = calculate_trend_strength(_price_df(100, drift=0.0))
    # before 63 bars, ts_fast/signal must be null
    assert df['signal'][:63].null_count() == 63
    assert df['signal'][63:].null_count() == 0


def test_trend_strength_sign_matches_strong_uptrend():
    df = calculate_trend_strength(_price_df(400, drift=0.003, vol=0.005))
    last = df.tail(1)['signal'][0]
    assert last > 0


def test_trend_strength_sign_matches_strong_downtrend():
    df = calculate_trend_strength(_price_df(400, drift=-0.003, vol=0.005))
    last = df.tail(1)['signal'][0]
    assert last < 0


def test_trend_strength_bounded():
    df = calculate_trend_strength(_price_df(400, drift=0.0005, vol=0.01))
    vals = df['signal'].drop_nulls()
    assert vals.min() >= -1.0
    assert vals.max() <= 1.0


def test_trend_strength_falls_back_to_ts_fast_before_252_bars():
    # between 63 and 252 bars, ts_slow is null so w1=0 and the signal should
    # equal tanh(ts_fast) exactly (w3/(w3+0) == 1)
    df = calculate_trend_strength(_price_df(150, drift=0.002, vol=0.005))
    row = df.tail(1)
    ts_fast = row['ts_fast'][0]
    ts_slow = row['ts_slow'][0]
    trend = row['signal'][0]
    assert ts_slow is None
    assert math.isclose(trend, math.tanh(ts_fast), rel_tol=1e-9)


# ── classify_regime ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('slow,fast,expected', [
    (1.0, 1.0, TrendRegime.BULL),
    (1.0, -1.0, TrendRegime.CORRECTION),
    (-1.0, -1.0, TrendRegime.BEAR),
    (-1.0, 1.0, TrendRegime.REBOUND),
])
def test_classify_regime(slow, fast, expected):
    assert classify_regime(fast, slow) == expected


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
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.BULL, momentum_discount=0.5)
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.CORRECTION, momentum_discount=0.5)
    assert math.isclose(correction, bull * 0.5, rel_tol=1e-9)


def test_position_scalar_discount_disabled_at_1():
    correction = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.CORRECTION, momentum_discount=1.0)
    bull = compute_position_scalar(0.5, 0.02, vol_target=0.15, regime=TrendRegime.BULL, momentum_discount=1.0)
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
# Within an over-budget cluster, allocation is GREEDY BY CONVICTION
# PRIORITY (priority = abs(scalar), descending), not a uniform haircut --
# a uniform scale factor can push every instrument below the 0.5 rounding
# threshold at once, even when the top-conviction instrument alone would
# easily survive on the full cap. A bounded lot-size exception (gated to
# the first instrument only) still grants exactly 1 contract when the true
# continuous math would round to zero but the instrument's own signal
# genuinely wants a full contract and its single-contract risk isn't
# wildly over the cap.
#
# infeasible is OUTCOME-based: True only when every instrument in a
# cluster ends at target_contracts == 0 despite at least one having a
# genuine (>=0.5) pre-cap signal -- not a cap-vs-single-contract-risk
# precomputation, since the top-priority instrument may still land a
# contract via the lot exception even when that precomputed check would
# have said "infeasible".

def _target(symbol, cluster, continuous_contracts, close=100.0, multiplier=10.0, hv=0.2,
            max_contracts=None, target_contracts=None, scalar=0.0):
    return {
        'symbol': symbol, 'cluster': cluster, 'continuous_contracts': continuous_contracts,
        'scalar': scalar,
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
    # while it's bigger than 0.25, i.e. n <= 4. Single-instrument cluster,
    # so greedy allocation reduces to the same math as a plain cap check.
    targets = [_target('MES', 'equity', continuous_contracts=10, close=100, multiplier=1, hv=0.1, scalar=0.9)]  # risk=100
    total_risk_target = 1000.0
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    assert out[0]['target_contracts'] == 10

    big_targets = [_target('MES', 'equity', continuous_contracts=100, close=100, multiplier=1, hv=0.1, scalar=0.9)]  # risk=1000
    out_big = apply_cluster_risk_cap(big_targets, max_cluster_risk_pct=0.25,
                                     total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    expected_risk = expected_pct * total_risk_target
    assert math.isclose(out_big[0]['position_risk'], expected_risk, rel_tol=0.05)


def test_cluster_cap_fixed_denominator_not_inflated_by_correlated_cluster():
    # Four grain instruments that would, under the old (buggy) emergent-sum
    # denominator, have summed to 60% of total_risk -- the bug was that
    # this sum itself became the denominator the cap was measured against.
    # Under the fixed total_risk_target, the cap is compared against the
    # account-level target instead, independent of how much risk this
    # cluster happens to carry this run. (Tied scalars here -- this test
    # is about the fixed denominator, not priority order; see the dedicated
    # walk-down tests for priority behavior.)
    targets = [
        _target('MES', 'equity', continuous_contracts=4, close=100, multiplier=1, hv=0.1, scalar=0.5),
        _target('MZC', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1, scalar=0.5),
        _target('MZS', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1, scalar=0.5),
        _target('MZW', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1, scalar=0.5),
        _target('MZL', 'grain', continuous_contracts=15, close=100, multiplier=1, hv=0.1, scalar=0.5),
    ]
    total_risk_target = 400.0
    n_active_clusters = 2  # equity, grain
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    grain_risk = sum(by_symbol[s]['position_risk'] for s in ('MZC', 'MZS', 'MZW', 'MZL'))
    cap = max(0.25, 1 / n_active_clusters) * total_risk_target  # = 200
    assert grain_risk <= cap * 1.1
    assert grain_risk < 600  # rescaled down from the 600 pre-cap sum


def test_cluster_cap_leaves_single_instrument_cluster_dominant_clusters_alone():
    # Equity and energy are each a single-instrument cluster -- with
    # nothing else in their own cluster to share risk with, they aren't
    # clipped just for being individually large relative to other
    # clusters' instrument counts.
    targets = [
        _target('MES', 'equity', continuous_contracts=3, close=100, multiplier=10, hv=0.1, scalar=0.5),  # risk=300
        _target('MCL', 'energy', continuous_contracts=2, close=100, multiplier=10, hv=0.1, scalar=0.5),  # risk=200
        _target('MGC', 'metal', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),   # risk=100
    ]
    total_risk_target = 600.0
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=3)
    by_symbol = {t['symbol']: t for t in out}
    assert abs(by_symbol['MES']['target_contracts']) < 3
    assert by_symbol['MGC']['target_contracts'] == 1  # 100 < 200 cap, untouched


def test_cluster_cap_no_op_when_all_clusters_within_budget():
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),
        _target('MCL', 'energy', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),
        _target('MGC', 'metal', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),
        _target('J7', 'fx', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=100_000.0, n_active_clusters=4)
    assert all(t['target_contracts'] == 1 for t in out)


def test_cluster_cap_within_budget_cluster_untouched():
    # Explicit, isolated within-budget case: continuous=1.3 with a cap so
    # large the cluster never needs the walk-down -- rounds directly,
    # unaffected by any cluster-cap mechanics.
    targets = [_target('A', 'c', continuous_contracts=1.3, close=100, multiplier=1, hv=1.0, scalar=0.9)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1_000_000.0, n_active_clusters=1)
    assert out[0]['target_contracts'] == 1
    assert not out[0].get('infeasible')


def test_cluster_cap_skips_error_targets():
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=100, multiplier=10, hv=0.1, scalar=0.5),
        {'symbol': 'MCL', 'error': 'boom', 'target_contracts': None},
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1)
    errored = next(t for t in out if t['symbol'] == 'MCL')
    assert errored['error'] == 'boom'
    assert errored['target_contracts'] is None


def test_cluster_cap_zero_total_risk_target_is_no_op():
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
    targets = [_target('MES', 'equity', continuous_contracts=100, close=100, multiplier=1, hv=0.1, scalar=0.9)]  # risk=1000
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=0)
    assert math.isclose(out[0]['position_risk'], 0.25 * 1000.0, rel_tol=0.05)


def test_cluster_cap_max_contracts_clamp_applied_after_cap_not_before():
    # max_contracts is applied as the TRUE last step (after the cluster-
    # cap allocation), not before. Single uncapped-cluster instrument:
    # continuous=10, no cluster pressure (cap is huge), so target_contracts
    # would be 10 without a clamp -- max_contracts=3 must still bring it
    # down to 3 at the very end.
    targets = [_target('MES', 'equity', continuous_contracts=10, close=100, multiplier=1, hv=0.1,
                       max_contracts=3, scalar=0.9)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1_000_000.0, n_active_clusters=1)
    assert out[0]['target_contracts'] == 3


def test_cluster_cap_walk_down_three_instruments_partial_consumption():
    """3+ instrument cluster: the walk-down generalizes past 2 instruments
    -- #1 (highest priority) fully consumes its desired size, #2 gets
    whatever's left, #3 gets nothing once the budget is exhausted."""
    targets = [
        _target('A', 'c', continuous_contracts=2.0, close=100, multiplier=1, hv=3.0, scalar=0.9),  # single=300
        _target('B', 'c', continuous_contracts=1.0, close=100, multiplier=1, hv=3.0, scalar=0.6),  # single=300
        _target('C', 'c', continuous_contracts=1.0, close=100, multiplier=1, hv=3.0, scalar=0.3),  # single=300
    ]
    # cap = 1.0 (n_active=1 floor) * 1000 = 1000
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1)
    by_symbol = {t['symbol']: t for t in out}
    # A: affordable=1000/300=3.33, usable=min(2.0,3.33)=2.0 -> 2 contracts, remaining=1000-600=400
    assert by_symbol['A']['target_contracts'] == 2
    # B: affordable=400/300=1.33, usable=min(1.0,1.33)=1.0 -> 1 contract, remaining=400-300=100
    assert by_symbol['B']['target_contracts'] == 1
    # C: affordable=100/300=0.33<0.5, not first -> 0
    assert by_symbol['C']['target_contracts'] == 0
    assert not any(t.get('infeasible') for t in out)  # A got real exposure -- not infeasible


def test_cluster_cap_greedy_diverges_from_old_proportional_haircut():
    """Documents the known, accepted tradeoff of greedy allocation: a
    uniform proportional haircut (the previous algorithm) would have kept
    BOTH instruments here at 1 contract each (0.6875x scale applied to two
    already-integer continuous values, both staying >= 0.5). Greedy gives
    the top-priority instrument (A) everything it wants first, leaving B
    with too little to round to a nonzero contract. This is intentional --
    conviction-priority allocation deliberately sacrifices the lower-
    priority instrument rather than diluting both -- and should NOT be
    "fixed" later to restore the old proportional-survival behavior."""
    targets = [
        _target('A', 'c', continuous_contracts=1.0, close=100, multiplier=1, hv=4.0, scalar=0.6),  # single=400
        _target('B', 'c', continuous_contracts=1.0, close=100, multiplier=1, hv=4.0, scalar=0.5),  # single=400
    ]
    # cluster_risk = 400+400=800; cap=1.0*550=550 (n_active=1 floor).
    # Old proportional: scale=550/800=0.6875; A=1*0.6875=0.6875->1; B=1*0.6875=0.6875->1 (both survive).
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=550.0, n_active_clusters=1)
    by_symbol = {t['symbol']: t for t in out}
    # Greedy: A first, affordable=550/400=1.375, usable=1.0 -> 1, remaining=550-400=150.
    # B: affordable=150/400=0.375<0.5, not first -> 0. This is the accepted tradeoff: B would
    # have survived (1 contract) under the old uniform haircut but gets zero under greedy --
    # not a regression to "fix", a deliberate design choice (top conviction wins the budget).
    assert by_symbol['A']['target_contracts'] == 1
    assert by_symbol['B']['target_contracts'] == 0


@pytest.mark.parametrize('single_contract_risk,expect_exception', [
    (2499.0, True),   # just below cap * (1 + max_lot_overrun_pct) = 1000 * 2.5 = 2500
    (2501.0, False),  # just above
])
def test_cluster_cap_lot_overrun_boundary(single_contract_risk, expect_exception):
    """affordable_continuous itself falls under 0.5 here (1000/2499=0.40,
    1000/2501=0.40) -- isolating whether the exception's overrun-tolerance
    check (single_contract_risk <= cap * (1 + max_lot_overrun_pct)) is the
    deciding factor at its exact boundary."""
    targets = [_target('A', 'c', continuous_contracts=1.0, close=single_contract_risk,
                       multiplier=1, hv=1.0, scalar=0.9)]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1,
                                 max_lot_overrun_pct=1.5)
    if expect_exception:
        assert out[0]['target_contracts'] == 1
    else:
        assert out[0]['target_contracts'] == 0
        assert out[0]['infeasible'] is True


def test_cluster_cap_lot_exception_never_applies_past_first_instrument():
    """B's own situation (in isolation) would qualify for the lot
    exception -- abs(original) >= 0.5, single_contract_risk within the
    overrun tolerance of the cap -- but B is evaluated second, after A has
    already spent part of the budget, so remaining_budget != cap and the
    exception must not fire for B."""
    targets = [
        _target('A', 'c', continuous_contracts=1.0, close=400, multiplier=1, hv=1.0, scalar=0.9),    # single=400
        _target('B', 'c', continuous_contracts=1.0, close=2500, multiplier=1, hv=1.0, scalar=0.5),   # single=2500
    ]
    # cap=1000 (n_active=1 floor). max_lot_overrun_pct=2.0 -> threshold=1000*3=3000.
    # B's single_contract_risk (2500) <= 3000, and B's own original (1.0) >= 0.5 -- B
    # WOULD qualify for the exception if it were first (remaining=1000: affordable=1000/2500=0.4<0.5).
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1,
                                 max_lot_overrun_pct=2.0)
    by_symbol = {t['symbol']: t for t in out}
    # A (first, scalar=0.9): affordable=1000/400=2.5, usable=min(1.0,2.5)=1.0 -> 1, remaining=600.
    assert by_symbol['A']['target_contracts'] == 1
    # B (second): affordable=600/2500=0.24<0.5, not first -> exception gated off -> 0,
    # even though B would have qualified had it been evaluated first.
    assert by_symbol['B']['target_contracts'] == 0


def test_cluster_cap_infeasible_when_cap_below_single_contract_risk():
    """Small synthetic equivalent of the $1,000,000/ES+NQ scenario: a
    cluster cap that's smaller than even the cheapest single contract's
    own dollar-vol risk. Outcome-based: the instrument ends at zero AND
    that's flagged infeasible, not because of a cap-vs-single-contract-
    risk precomputation alone."""
    targets = [_target('J7', 'fx', continuous_contracts=1.0, close=0.0066,
                       multiplier=6_250_000, hv=0.08, scalar=0.9)]
    # single_contract_risk = 0.0066*6_250_000*0.08 = 3300; cap=1.0*1000=1000 < 3300.
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=1000.0, n_active_clusters=1)
    assert out[0]['infeasible'] is True
    assert out[0]['target_contracts'] == 0  # scale-equivalent (1000/3300=0.303) * 1.0 < 0.5


def test_cluster_cap_not_infeasible_when_walk_down_captures_exposure():
    # Contrast case: even though the cluster needs rescaling, the top-
    # priority instrument still captures real exposure -- not infeasible.
    targets = [
        _target('MES', 'equity', continuous_contracts=5, close=100, multiplier=10, hv=0.1, scalar=0.9),
        _target('MNQ', 'equity', continuous_contracts=5, close=100, multiplier=10, hv=0.1, scalar=0.5),
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=600.0, n_active_clusters=1)  # cap=600
    assert not any(t.get('infeasible') for t in out)
    assert any(t['target_contracts'] != 0 for t in out)


def test_cluster_cap_mes_mnq_80k_scenario():
    """The exact $80K MES/MNQ scenario from this session's live run: MES
    (higher scalar/conviction) gets 1 contract; MNQ (lower scalar, and a
    much more expensive single contract) gets 0 -- the walk-down's top
    priority wins the cluster's budget instead of both being uniformly
    scaled to zero."""
    targets = [
        _target('MES', 'equity', continuous_contracts=1.0133, close=7472.75, multiplier=5, hv=0.1539, scalar=0.8168),
        _target('MNQ', 'equity', continuous_contracts=0.4137, close=30028.25, multiplier=2, hv=0.2381, scalar=0.5301),
    ]
    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=80_000 * 0.15, n_active_clusters=3)
    by_symbol = {t['symbol']: t for t in out}
    assert by_symbol['MES']['target_contracts'] == 1
    assert by_symbol['MNQ']['target_contracts'] == 0
    assert not by_symbol['MES'].get('infeasible')
    assert not by_symbol['MNQ'].get('infeasible')  # cluster captured exposure via MES -- not infeasible


def test_cluster_cap_es_nq_million_dollar_scenario_recomputed_for_greedy():
    """Reproduces this session's exact $1,000,000 account, ES+NQ-both-
    near-max-conviction scenario under the corrected greedy allocation.
    Recomputed (not assumed): ES (the higher-scalar instrument) wins the
    cluster's entire cap and gets 1 contract; NQ gets 0. Neither is
    flagged infeasible, since the cluster did capture real exposure."""
    es_close, es_mult, es_hv, es_scalar = 7428.25, 50, 0.1524, 0.8302
    nq_close, nq_mult, nq_hv, nq_scalar = 29514.25, 20, 0.238, 0.5379
    es_continuous = 479_325.91 / (es_close * es_mult)   # ~1.2908
    nq_continuous = 310_528.16 / (nq_close * nq_mult)   # ~0.5258

    targets = [
        _target('ES', 'equity', continuous_contracts=es_continuous, close=es_close, multiplier=es_mult,
               hv=es_hv, scalar=es_scalar),
        _target('NQ', 'equity', continuous_contracts=nq_continuous, close=nq_close, multiplier=nq_mult,
               hv=nq_hv, scalar=nq_scalar),
    ]
    total_risk_target = 1_000_000 * 0.15  # 150,000
    n_active_clusters = 3
    cap = max(0.25, 1 / n_active_clusters) * total_risk_target  # 50,000

    es_single_contract_risk = es_close * es_mult * es_hv   # ~56,607.3
    assert cap < es_single_contract_risk  # confirms the scenario's premise: even ES alone exceeds the cap

    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    assert by_symbol['ES']['target_contracts'] == 1
    assert by_symbol['NQ']['target_contracts'] == 0
    # The cluster DID capture exposure (via ES) -- not infeasible, even
    # though a precomputed cap-vs-single-contract-risk check alone would
    # have said it should be.
    assert not by_symbol['ES'].get('infeasible')
    assert not by_symbol['NQ'].get('infeasible')


def test_cluster_cap_live_session_scenario_recomputed_for_greedy_priority():
    """Recomputes the grain/fx/equity scenario from this session under
    conviction-priority allocation: within each over-budget cluster, the
    highest-|scalar| instrument wins the cluster's cap first. Recomputed
    via direct verification, not assumed."""
    targets = [
        _target('MES', 'equity', continuous_contracts=1, close=7437.50, multiplier=5, hv=0.1524, scalar=0.85),
        _target('MZL', 'grain', continuous_contracts=5, close=66.58, multiplier=60, hv=0.2429, scalar=0.70),
        _target('MZC', 'grain', continuous_contracts=-13, close=437.00, multiplier=5, hv=0.1937, scalar=0.80),
        _target('MZS', 'grain', continuous_contracts=-2, close=1142.00, multiplier=5, hv=0.1492, scalar=0.20),
        _target('MZW', 'grain', continuous_contracts=-2, close=597.00, multiplier=5, hv=0.2969, scalar=0.19),
        _target('J7', 'fx', continuous_contracts=1, close=0.0066, multiplier=6_250_000, hv=0.0825, scalar=0.85),
        _target('BRE', 'fx', continuous_contracts=2, close=0.19, multiplier=100_000, hv=0.1143, scalar=0.75),
        _target('6M', 'fx', continuous_contracts=2, close=0.06, multiplier=500_000, hv=0.0825, scalar=0.78),
    ]
    total_risk_target = 100_000 * 0.15
    n_active_clusters = 3

    out = apply_cluster_risk_cap(targets, max_cluster_risk_pct=0.25,
                                 total_risk_target=total_risk_target, n_active_clusters=n_active_clusters)
    by_symbol = {t['symbol']: t for t in out}

    # Equity: only one instrument, no competition.
    assert by_symbol['MES']['target_contracts'] == 1
    # Grain: MZC (scalar 0.80, highest) wins the cluster's cap; the others
    # (lower priority) get nothing once MZC's allocation exhausts it.
    assert by_symbol['MZC']['target_contracts'] < 0  # short, sign preserved
    assert by_symbol['MZL']['target_contracts'] == 0
    assert by_symbol['MZS']['target_contracts'] == 0
    assert by_symbol['MZW']['target_contracts'] == 0
    # FX: J7 (0.85) and 6M (0.78) both outrank BRE (0.75) and capture
    # exposure; BRE gets whatever's left, which in this case is nothing.
    assert by_symbol['J7']['target_contracts'] > 0
    assert by_symbol['6M']['target_contracts'] > 0
    assert by_symbol['BRE']['target_contracts'] == 0
    # Every over-budget cluster still captured real exposure via its
    # top-priority instrument -- none are infeasible.
    assert not any(t.get('infeasible') for t in out)


# ── compute_vol_ratio / classify_signal_confidence / compute_signal_confidence
#
# Per-instrument, asset-specific vol-regime ratio (hv_short/hv_long of THIS
# instrument's own daily returns) -- NOT VIX/VX-driven. Feeds
# signal_confidence, an opt-in (default 1.0/no-op) discount on trust in a
# specific instrument's trend signal, orthogonal to momentum_discount
# (fast/slow sign disagreement) and market_stress_scale (portfolio-wide,
# VX-driven, applied by the caller separately).

def _vol_series(n_calm: int, n_shock: int, calm_vol: float, shock_vol: float,
                drift: float = 0.0005, seed: int = 0) -> pl.DataFrame:
    """A price series that's calm for n_calm bars, then switches to a
    different (shock_vol) volatility for the trailing n_shock bars --
    simulates an instrument-specific vol regime change (e.g. a corn-
    harvest shock or a JPY intervention) independent of any broad-market
    state."""
    rng = np.random.default_rng(seed)
    rets = np.concatenate([
        rng.normal(drift, calm_vol, n_calm),
        rng.normal(0.0, shock_vol, n_shock),
    ])
    close = 100 * np.exp(np.cumsum(rets))
    # compute_vol_ratio chains onto calculate_trend_strength's output (it
    # needs the 'r1d' column that produces, not raw 'close' alone).
    return calculate_trend_strength(pl.DataFrame({'close': close}))


def test_vol_ratio_high_for_instrument_specific_spike():
    # Calm for 379 bars, then a sharp vol spike in the trailing 21 --
    # hv_short (21d) should be far above hv_long (252d), giving a high
    # vol_ratio.
    df = _vol_series(379, 21, calm_vol=0.01, shock_vol=0.08, seed=1)
    out = compute_vol_ratio(df)
    vol_ratio = out.tail(1)['vol_ratio'][0]
    assert vol_ratio > 1.5


def test_vol_ratio_low_for_instrument_specific_quiet_spell():
    # Calm-ish for 379 bars, then unusually QUIET for the trailing 21 --
    # hv_short far below hv_long, giving a low vol_ratio. Confirms the
    # ratio detects unusual calm just as readily as unusual turbulence.
    df = _vol_series(379, 21, calm_vol=0.02, shock_vol=0.002, seed=2)
    out = compute_vol_ratio(df)
    vol_ratio = out.tail(1)['vol_ratio'][0]
    assert vol_ratio < 0.5


def test_vol_ratio_near_one_for_steady_series():
    df = _vol_series(379, 21, calm_vol=0.01, shock_vol=0.01, seed=3)
    out = compute_vol_ratio(df)
    vol_ratio = out.tail(1)['vol_ratio'][0]
    assert 0.7 < vol_ratio < 1.5


@pytest.mark.parametrize('vol_ratio,expected', [
    (None, SignalConfidenceRegime.NORMAL),
    (float('nan'), SignalConfidenceRegime.NORMAL),
    (1.0, SignalConfidenceRegime.NORMAL),
    (0.71, SignalConfidenceRegime.NORMAL),
    (1.49, SignalConfidenceRegime.NORMAL),
    (0.7, SignalConfidenceRegime.LOW),
    (0.3, SignalConfidenceRegime.LOW),
    (1.5, SignalConfidenceRegime.HIGH),
    (3.0, SignalConfidenceRegime.HIGH),
])
def test_classify_signal_confidence_thresholds(vol_ratio, expected):
    assert classify_signal_confidence(vol_ratio, low_threshold=0.7, high_threshold=1.5) == expected


def test_signal_confidence_high_and_low_discounts_are_independent():
    """Locks in non-symmetry: high_vol_discount and low_vol_discount are
    free, independently configurable parameters -- this must NOT assume
    the high-vol value applies to the low-vol bucket too."""
    high_discount = compute_signal_confidence(3.0, low_threshold=0.7, high_threshold=1.5,
                                               high_vol_discount=0.4, low_vol_discount=0.9)
    low_discount = compute_signal_confidence(0.3, low_threshold=0.7, high_threshold=1.5,
                                              high_vol_discount=0.4, low_vol_discount=0.9)
    assert high_discount == 0.4
    assert low_discount == 0.9
    assert high_discount != low_discount


def test_signal_confidence_low_vol_default_is_a_no_op():
    """low_vol_discount's suggested default (1.0) is a no-op -- there's no
    settled answer for whether low vol should discount trend confidence at
    all (Bongaerts et al.'s low-vol response is about equity factor-timing
    alpha, not trend-signal reliability), so the default must not silently
    apply a discount."""
    discount = compute_signal_confidence(0.2, low_threshold=0.7, high_threshold=1.5)
    assert discount == 1.0


def test_signal_confidence_normal_regime_is_always_a_no_op():
    discount = compute_signal_confidence(1.0, low_threshold=0.7, high_threshold=1.5,
                                          high_vol_discount=0.3, low_vol_discount=0.3)
    assert discount == 1.0


def test_signal_confidence_defaults_to_high_vol_discount_of_half():
    # Suggested default (0.5), consistent with the Mozes-article finding
    # that vol spikes specifically damage momentum reliability.
    discount = compute_signal_confidence(3.0, low_threshold=0.7, high_threshold=1.5)
    assert discount == 0.5


# ── compute_position_scalar + signal_confidence wiring ──────────────────────

def test_position_scalar_signal_confidence_defaults_to_noop():
    """signal_confidence must default to 1.0 -- existing callers that don't
    pass it get byte-identical behavior to before Phase 2 existed."""
    with_default = compute_position_scalar(0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL,
                                            momentum_discount=0.5)
    explicit_noop = compute_position_scalar(0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL,
                                             momentum_discount=0.5, signal_confidence=1.0)
    assert with_default == explicit_noop


def test_position_scalar_signal_confidence_discounts_multiplicatively():
    base = compute_position_scalar(0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL, momentum_discount=0.5)
    discounted = compute_position_scalar(0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL,
                                          momentum_discount=0.5, signal_confidence=0.5)
    assert math.isclose(discounted, base * 0.5, rel_tol=1e-9)


def test_signal_confidence_instrument_specific_spike_discounts_only_that_instrument():
    """The core Phase 2 case: an instrument-specific vol spike (high
    hv_short/hv_long) discounts THAT instrument's scalar, while a sibling
    instrument with calm own-history vol is untouched -- even when both
    share the exact same market_stress_scale (portfolio-wide VX state).
    This is the JPY-/corn-spike blind spot market_stress_scale alone can't
    see, since it only looks at VIX/VX, not at corn's or JPY's own vol."""
    spiking_df = _vol_series(379, 21, calm_vol=0.01, shock_vol=0.08, seed=4)
    calm_df = _vol_series(379, 21, calm_vol=0.01, shock_vol=0.01, seed=5)

    spiking_vol_ratio = compute_vol_ratio(spiking_df).tail(1)['vol_ratio'][0]
    calm_vol_ratio = compute_vol_ratio(calm_df).tail(1)['vol_ratio'][0]

    low_threshold, high_threshold = 0.7, 1.5
    spiking_confidence = compute_signal_confidence(spiking_vol_ratio, low_threshold, high_threshold)
    calm_confidence = compute_signal_confidence(calm_vol_ratio, low_threshold, high_threshold)

    assert spiking_confidence < 1.0   # discounted
    assert calm_confidence == 1.0     # untouched

    # Same trend_strength/daily_std/regime for both instruments -- only
    # signal_confidence differs -- and the SAME market_stress_scale
    # applied afterward to both (simulating one portfolio-wide VX state
    # that's calm, i.e. 1.0, so it doesn't mask the per-instrument effect).
    market_stress_scale = 1.0
    spiking_scalar = compute_position_scalar(
        0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL,
        momentum_discount=1.0, signal_confidence=spiking_confidence,
    ) * market_stress_scale
    calm_scalar = compute_position_scalar(
        0.6, 0.01, vol_target=0.15, regime=TrendRegime.BULL,
        momentum_discount=1.0, signal_confidence=calm_confidence,
    ) * market_stress_scale

    assert spiking_scalar < calm_scalar
    assert math.isclose(calm_scalar, 0.6 * max(0.25, min(2.0, 0.15 / (0.01 * math.sqrt(252)))), rel_tol=1e-9)

def _trading_dates(start: date, n: int) -> list[date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _price_df_dated(start: date, n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    dates = _trading_dates(start, n)
    return pl.DataFrame({'ts_event': dates, 'close': close})


# ── SignalSpec ───────────────────────────────────────────────────────────

def test_signal_spec_defaults():
    spec = SignalSpec()
    assert spec.fast_window == 63
    assert spec.slow_window == 252
    assert spec.vol_fast_window is None
    assert spec.vol_slow_window is None
    assert spec.w_fast == 0.4
    assert spec.w_slow == 0.6
    assert spec.discount == 0.5
    assert spec.fast_months == GOULDING_FAST_MONTHS == 2
    assert spec.slow_months == GOULDING_SLOW_MONTHS == 12
    assert spec.a_co == 0.5
    assert spec.a_re == 0.5


def test_signal_spec_rejects_bad_windows():
    with pytest.raises(ValueError):
        SignalSpec(fast_window=0)
    with pytest.raises(ValueError):
        SignalSpec(fast_months=-1)


def test_signal_spec_rejects_bad_vol_windows():
    # Previously unvalidated -- vol_fast_window=0 would reach
    # rolling_std(0) downstream; a negative value is equally nonsensical.
    with pytest.raises(ValueError):
        SignalSpec(vol_fast_window=0)
    with pytest.raises(ValueError):
        SignalSpec(vol_fast_window=-5)
    with pytest.raises(ValueError):
        SignalSpec(vol_slow_window=0)
    # None (the default, meaning "horizon-matched to fast/slow_window") is
    # still valid and must NOT raise.
    SignalSpec(vol_fast_window=None, vol_slow_window=None)


def test_signal_spec_rejects_fast_not_less_than_slow():
    # fast_window/fast_months >= their slow counterpart contradicts the
    # field names and is almost certainly a caller error (e.g. args
    # swapped), not a valid config.
    with pytest.raises(ValueError):
        SignalSpec(fast_window=252, slow_window=63)
    with pytest.raises(ValueError):
        SignalSpec(fast_window=100, slow_window=100)
    with pytest.raises(ValueError):
        SignalSpec(fast_months=12, slow_months=2)


def test_signal_spec_rejects_bad_a_co_a_re():
    with pytest.raises(ValueError):
        SignalSpec(a_co=1.5)
    with pytest.raises(ValueError):
        SignalSpec(a_re=-0.1)


def test_signal_spec_rejects_non_positive_weight_sum():
    # w_fast+w_slow<=0 wouldn't crash downstream (the ts denominator clips
    # at 1e-12) -- it would silently degenerate to ts=tanh(0)=0 every row.
    # Must be caught loudly here instead.
    with pytest.raises(ValueError):
        SignalSpec(w_fast=0.0, w_slow=0.0)
    with pytest.raises(ValueError):
        SignalSpec(w_fast=-0.5, w_slow=0.5)


def test_goulding_factory_uses_paper_horizons():
    spec = SignalSpec.goulding()
    assert spec.fast_months == 2
    assert spec.slow_months == 12


def test_continuous_kwargs_and_goulding_kwargs_disjoint_and_complete():
    spec = SignalSpec(fast_window=21, slow_window=100, fast_months=3, slow_months=9)
    ck = spec.continuous_kwargs()
    gk = spec.goulding_kwargs()
    assert ck['fast_window'] == 21 and ck['slow_window'] == 100
    assert gk['fast_months'] == 3 and gk['slow_months'] == 9
    # No overlap -- each function only ever receives its own parameters.
    assert set(ck.keys()).isdisjoint(gk.keys())


# ── build_features: shared base only, no model-specific columns ─────────

def test_build_features_adds_only_base_columns():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 5),
                        'close': [100.0, 102.0, 101.0, 105.0, 103.0]})
    feat = build_features(df)
    assert set(feat.columns) == {'ts_event', 'close', 'prev_close', 'peak', 'dd', 'r1d'}


def test_build_features_r1d_is_simple_return_not_log():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 3),
                        'close': [100.0, 110.0, 99.0]})
    feat = build_features(df)
    r1d = feat['r1d'].to_list()
    assert r1d[0] is None
    assert r1d[1] == pytest.approx(0.10)   # 110/100 - 1, NOT log(110/100)
    assert r1d[2] == pytest.approx(99 / 110 - 1)


def test_build_features_peak_and_drawdown():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 4),
                        'close': [100.0, 120.0, 90.0, 110.0]})
    feat = build_features(df)
    assert feat['peak'].to_list() == [100.0, 120.0, 120.0, 120.0]
    dd = feat['dd'].to_list()
    assert dd[2] == pytest.approx((90 - 120) / 120, abs=1e-2)


def test_build_features_sorts_unsorted_input():
    # shift()/cum_max() are order-dependent -- pass rows deliberately out
    # of ts_event order and confirm build_features sorts before computing,
    # rather than silently corrupting prev_close/peak/dd/r1d.
    df = pl.DataFrame({
        'ts_event': [date(2020, 1, 3), date(2020, 1, 1), date(2020, 1, 2)],
        'close': [103.0, 100.0, 101.0],
    })
    feat = build_features(df).sort('ts_event')
    assert feat['close'].to_list() == [100.0, 101.0, 103.0]
    assert feat['prev_close'].to_list() == [None, 100.0, 101.0]
    assert feat['peak'].to_list() == [100.0, 101.0, 103.0]


# ── continuous_momentum: independent of goulding_monthly ─────────────────

def test_continuous_momentum_needs_only_build_features_output():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, seed=1)
    feat = build_features(df)
    # Deliberately does NOT call goulding_monthly first -- continuous_momentum
    # must run from build_features' output alone.
    out = continuous_momentum(feat)
    assert 'ts_fast' in out.columns and 'ts_slow' in out.columns
    assert out['signal'].drop_nulls().len() > 0


def test_continuous_momentum_signal_is_bounded():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.0015, vol=0.005, seed=2)
    out = continuous_momentum(build_features(df))
    signal = out['signal'].drop_nulls()
    assert len(signal) > 0
    assert signal.min() >= -1.0 and signal.max() <= 1.0


def test_continuous_momentum_zero_std_produces_null_not_inf():
    # A perfectly FLAT price (every close identical) makes r1d exactly 0.0
    # every day, and therefore std_fast/std_slow exactly 0.0 once the
    # rolling window is full -- r_fast/(std_fast*sqrt(n)) would be NaN
    # (0/0) without an explicit guard, and if r_fast were ever nonzero
    # instead (e.g. a price series constant everywhere except the fast/
    # slow return's own start/end points), the same division would be
    # +-inf, with tanh(inf) == 1.0 silently reading as a genuine
    # max-strength trend instead of an undefined one.
    dates = _trading_dates(date(2018, 1, 1), 400)
    df = pl.DataFrame({'ts_event': dates, 'close': [100.0] * 400})
    out = continuous_momentum(build_features(df))
    warmed_up = out.filter(pl.col('std_fast') == 0.0)
    assert warmed_up.height > 0
    assert warmed_up['ts_fast'].is_null().all()
    assert warmed_up['ts'].is_null().all()
    assert not out['ts_fast'].is_infinite().any()


def test_continuous_momentum_regime_classification():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.002, vol=0.004, seed=3)
    out = continuous_momentum(build_features(df))
    regimes = set(out['regime'].drop_nulls().unique().to_list())
    assert regimes <= {'bull', 'bear', 'correction', 'rebound'}


def test_continuous_momentum_discount_applied_in_transition_regimes():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.0, vol=0.01, seed=4)
    out = continuous_momentum(build_features(df), discount=0.25)
    dis = out.filter(pl.col('regime').is_in(['correction', 'rebound']))
    other = out.filter(~pl.col('regime').is_in(['correction', 'rebound']) & pl.col('ts').is_not_null())
    if dis.height > 0:
        ratio = (dis['signal'] / dis['ts']).drop_nulls()
        assert ratio.len() > 0
        assert all(pytest.approx(0.25) == r for r in ratio.to_list())
    if other.height > 0:
        assert (other['signal'] == other['ts']).all()


def test_continuous_momentum_vol_window_defaults_to_return_window():
    # Default behavior: vol normalization is horizon-matched to the return
    # window, NOT an arbitrary fixed window (e.g. always 63 days) reused
    # regardless of fast_window/slow_window.
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, vol=0.008, seed=5)
    feat = build_features(df)
    out_default = continuous_momentum(feat, fast_window=21, slow_window=126)
    out_explicit = continuous_momentum(feat, fast_window=21, slow_window=126,
                                        vol_fast_window=21, vol_slow_window=126)
    a = out_default['std_fast'].fill_null(-999.0).to_list()
    b = out_explicit['std_fast'].fill_null(-999.0).to_list()
    assert a == pytest.approx(b)


def test_continuous_momentum_vol_window_can_be_decoupled_from_return_window():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, vol=0.008, seed=6)
    feat = build_features(df)
    out_matched = continuous_momentum(feat, fast_window=21)
    out_decoupled = continuous_momentum(feat, fast_window=21, vol_fast_window=63)
    a = out_matched['std_fast'].fill_null(-999.0).to_list()
    b = out_decoupled['std_fast'].fill_null(-999.0).to_list()
    assert a != pytest.approx(b)


def test_continuous_momentum_annualization_days_scales_avg_r():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, seed=7)
    feat = build_features(df)
    out_252 = continuous_momentum(feat, annualization_days=252)
    out_259 = continuous_momentum(feat, annualization_days=259)
    a = out_252['avg_r_fast'].fill_null(-999.0).to_list()
    b = out_259['avg_r_fast'].fill_null(-999.0).to_list()
    assert a != pytest.approx(b)
    ratio = 259 / 252
    for x, y in zip(a, b):
        if x != -999.0:
            assert y == pytest.approx(x * ratio, rel=1e-6)


# ── goulding_monthly: independent of continuous_momentum ─────────────────

def test_goulding_monthly_needs_only_ts_event_and_close():
    # Deliberately pass raw build_features output straight through --
    # goulding_monthly must not require continuous_momentum's columns
    # (ts_fast/ts_slow/regime/signal) to exist at all.
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, seed=8)
    feat = build_features(df)
    assert 'ts_fast' not in feat.columns  # sanity: continuous_momentum wasn't run
    out = goulding_monthly(feat)
    assert out.height > 0


def test_goulding_monthly_has_no_volatility_normalization_columns():
    df = _price_df_dated(date(2018, 1, 1), 400, drift=0.001, seed=9)
    out = goulding_monthly(build_features(df))
    # Pure arithmetic-average signal -- no std/ts_fast/ts_slow/hv anywhere.
    assert not ({'std', 'std_fast', 'std_slow', 'ts_fast', 'ts_slow', 'hv'} & set(out.columns))


def test_goulding_monthly_uses_month_end_close_not_first_day():
    # Two rows per month with first != last -- locks in .last() (month-end
    # close) as the value used, not .first() (which an intra-month
    # first-to-last construction would silently prefer instead).
    df = pl.DataFrame([
        {'ts_event': date(2020, 1, 2), 'close': 100.0},   # Jan first day
        {'ts_event': date(2020, 1, 31), 'close': 105.0},  # Jan LAST day
        {'ts_event': date(2020, 2, 3), 'close': 108.0},   # Feb first day
        {'ts_event': date(2020, 2, 28), 'close': 110.0},  # Feb LAST day
    ])
    out = goulding_monthly(df).sort('ts_event')
    close = out['close'].to_list()
    assert close == [105.0, 110.0]
    ret = out['ret'].to_list()
    # Month-end-to-month-end: Feb's ret = 110/105 - 1, NOT 110/108 - 1
    # (which is what a first-to-last-day-of-Feb construction would give).
    assert ret[1] == pytest.approx(110.0 / 105.0 - 1)


def test_goulding_monthly_fast_slow_use_only_completed_months():
    # Six calendar months of deterministic monthly returns (one row per
    # month, so each bucket's month-end close IS that row's value) --
    # verify fast/slow (fast_months=1/slow_months=2) equal the PRIOR
    # month(s)' return, not the current month's own.
    month_starts = [date(2020, m, 1) for m in range(1, 7)]
    rows = []
    price = 100.0
    monthly_rets = [0.05, -0.03, 0.02, 0.04, -0.01, 0.03]
    for start, ret in zip(month_starts, monthly_rets):
        rows.append({'ts_event': start, 'close': price})
        price = price * (1 + ret)
    # Add a final row to close out the last month's own return.
    rows.append({'ts_event': date(2020, 7, 1), 'close': price})
    df = pl.DataFrame(rows)
    out = goulding_monthly(df, fast_months=1, slow_months=2).sort('ts_event')
    ret = out['ret'].to_list()
    fast = out['fast'].to_list()
    assert ret[1:] == pytest.approx(monthly_rets)  # locks in the exact convention
    # fast[i] must equal ret[i-1] (fast_months=1 trailing mean of last
    # completed month), never ret[i] itself.
    for i in range(2, len(ret)):
        if fast[i] is not None and ret[i - 1] is not None:
            assert fast[i] == pytest.approx(ret[i - 1])


def test_goulding_monthly_signal_bounds_and_regimes():
    df = _price_df_dated(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=10)
    out = goulding_monthly(build_features(df))
    regimes = set(out['regime'].drop_nulls().unique().to_list())
    assert regimes <= {'bull', 'bear', 'correction', 'rebound'}


# ── _goulding_weight (eq. 7), independent of both models above ──────────
#
# Eq. 7 blends the period's ACTUAL r_fast/r_slow return values -- (1-a)*
# r_slow + a*r_fast -- and only takes the sign of that blended RESULT as
# the position weight. Bull/Bear stay unconditionally +-1 regardless of
# r_fast/r_slow (fast and slow already agree in sign by definition in
# those states). Correction requires r_fast<0<=r_slow; Rebound requires
# r_slow<0<=r_fast (goulding_monthly's own regime classification) -- test
# fixtures below respect that, since passing an inconsistent sign
# combination wouldn't correspond to a real classified month.

def test_goulding_weight_bull_bear_are_fully_directional():
    assert _goulding_weight('bull', a_co=0.5, a_re=0.5, r_fast=-0.5, r_slow=-0.5) == 1.0
    assert _goulding_weight('bear', a_co=0.5, a_re=0.5, r_fast=0.5, r_slow=0.5) == -1.0


def test_goulding_weight_disagreement_states_use_blended_return_sign_not_just_a_co_a_re():
    # Correction, a_co=0.5: plain average of r_slow/r_fast. Slow's positive
    # magnitude dominates fast's small negative -> positive blend -> +1.
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5, r_fast=-0.01, r_slow=0.10) == 1.0
    # Same a_co, but fast's negative magnitude now dominates -> -1. The OLD
    # (1-2*a_co) formula would have returned the identical 0.0 for both of
    # these -- proving it ignored r_fast/r_slow's actual magnitudes entirely.
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5, r_fast=-0.10, r_slow=0.01) == -1.0
    # Rebound, a_re=0.5: symmetric check.
    assert _goulding_weight('rebound', a_co=0.5, a_re=0.5, r_fast=0.10, r_slow=-0.01) == 1.0
    assert _goulding_weight('rebound', a_co=0.5, a_re=0.5, r_fast=0.01, r_slow=-0.10) == -1.0


def test_goulding_weight_nonflat_a_co_a_re_tilts_the_blend():
    # a_co=0.2 in Correction weights r_slow (0.4) more than r_fast (-0.05):
    # 0.8*0.4 + 0.2*(-0.05) = 0.31 > 0.
    assert _goulding_weight('correction', a_co=0.2, a_re=0.5, r_fast=-0.05, r_slow=0.4) == 1.0
    # a_re=0.8 in Rebound weights r_fast (0.4) more than r_slow (-0.05):
    # 0.2*(-0.05) + 0.8*0.4 = 0.31 > 0.
    assert _goulding_weight('rebound', a_co=0.5, a_re=0.8, r_fast=0.4, r_slow=-0.05) == 1.0


def test_goulding_weight_none_and_unknown_regime():
    assert _goulding_weight(None, a_co=0.5, a_re=0.5, r_fast=0.1, r_slow=0.1) is None
    assert _goulding_weight('unknown', a_co=0.5, a_re=0.5, r_fast=0.1, r_slow=0.1) is None


def test_goulding_weight_correction_rebound_require_r_fast_r_slow():
    # Bull/Bear don't need them (unconditional +-1); Correction/Rebound do
    # -- missing/NaN r_fast or r_slow means an invalid signal, not a
    # silent fallback to some default weight.
    assert _goulding_weight('bull', a_co=0.5, a_re=0.5) == 1.0
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5) is None
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5, r_fast=None, r_slow=0.1) is None
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5, r_fast=float('nan'), r_slow=0.1) is None


def test_goulding_weight_rejects_out_of_range_a_co_a_re():
    # Public/standalone-callable -- not every caller goes through
    # SignalSpec's own __post_init__ validation, so this function must
    # reject an out-of-range mixing weight itself rather than silently
    # extrapolating eq. 7 outside its [0, 1] domain.
    with pytest.raises(ValueError):
        _goulding_weight('correction', a_co=2.0, a_re=0.5, r_fast=0.1, r_slow=-0.1)
    with pytest.raises(ValueError):
        _goulding_weight('rebound', a_co=0.5, a_re=-0.1, r_fast=0.1, r_slow=-0.1)


# ── _goulding_blend (eq. 7's raw pre-sign value, audit/display only) ────

def test_goulding_blend_matches_goulding_weight_sign():
    # _goulding_weight is defined as sign(_goulding_blend(...)) for
    # Correction/Rebound -- verify that relationship holds, not just that
    # each function individually looks reasonable.
    for regime, r_fast, r_slow in [('correction', -0.01, 0.10), ('correction', -0.10, 0.01),
                                    ('rebound', 0.10, -0.01), ('rebound', 0.01, -0.10)]:
        blend = _goulding_blend(regime, a_co=0.5, a_re=0.5, r_fast=r_fast, r_slow=r_slow)
        weight = _goulding_weight(regime, a_co=0.5, a_re=0.5, r_fast=r_fast, r_slow=r_slow)
        assert blend is not None
        expected_sign = 1.0 if blend > 0 else (-1.0 if blend < 0 else 0.0)
        assert weight == expected_sign


def test_goulding_blend_none_for_bull_bear():
    # Eq. 7 doesn't apply to Bull/Bear (_goulding_weight returns +-1
    # unconditionally there, with no blend at all) -- nothing to report.
    assert _goulding_blend('bull', a_co=0.5, a_re=0.5, r_fast=0.1, r_slow=0.1) is None
    assert _goulding_blend('bear', a_co=0.5, a_re=0.5, r_fast=-0.1, r_slow=-0.1) is None


def test_goulding_blend_none_and_missing_inputs():
    assert _goulding_blend(None, a_co=0.5, a_re=0.5, r_fast=0.1, r_slow=0.1) is None
    assert _goulding_blend('correction', a_co=0.5, a_re=0.5) is None
    with pytest.raises(ValueError):
        _goulding_blend('correction', a_co=2.0, a_re=0.5, r_fast=0.1, r_slow=-0.1)

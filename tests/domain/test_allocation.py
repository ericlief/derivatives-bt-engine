"""
Tests for domain.allocation -- risk allocation, both single-instrument
(compute_position_scalar: this instrument's own vol-targeted risk_scalar)
and cross-instrument (compute_n_effective/compute_desired_risk_budget/
apply_cluster_risk_cap/compute_idm: how instruments/clusters get weighted
relative to each other, given they aren't independent bets).

Consolidated (2026-07) from test_signal.py (formerly test_tsmom_signal.py),
once compute_position_scalar itself moved out of domain/signal.py into
domain/allocation.py -- it's risk-SIZING math, not signal CONSTRUCTION, so
it belongs with the rest of this module's risk-allocation functions, not
alongside build_features/continuous_momentum/goulding_monthly.
compute_n_effective/compute_desired_risk_budget/apply_cluster_risk_cap's own
tests had already been left behind in test_signal.py after an earlier,
incomplete module split (only their imports were fixed at the time, not
their physical test-file location) -- moved here now too, completing that
split properly.
"""

import math

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.allocation import (
    apply_cluster_risk_cap,
    compute_desired_risk_budget,
    compute_n_effective,
    compute_position_scalar,
)
from derivatives_bt_engine.domain.enums import TrendRegime
from derivatives_bt_engine.domain.signal import (
    calculate_trend_strength,
    compute_signal_confidence,
    compute_vol_ratio,
)


def _vol_series(n_calm: int, n_shock: int, calm_vol: float, shock_vol: float,
                drift: float = 0.0005, seed: int = 0) -> pl.DataFrame:
    """A price series that's calm for n_calm bars, then switches to a
    different (shock_vol) volatility for the trailing n_shock bars --
    simulates an instrument-specific vol regime change independent of any
    broad-market state."""
    rng = np.random.default_rng(seed)
    rets = np.concatenate([
        rng.normal(drift, calm_vol, n_calm),
        rng.normal(0.0, shock_vol, n_shock),
    ])
    close = 100 * np.exp(np.cumsum(rets))
    return calculate_trend_strength(pl.DataFrame({'close': close}))


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


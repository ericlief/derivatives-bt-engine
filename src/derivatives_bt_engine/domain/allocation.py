"""
Risk allocation -- how much risk/budget each POSITION gets, both at the
single-instrument level (compute_position_scalar: this instrument's own
vol-targeted risk_scalar) and across instruments/clusters relative to each
other (everything else here), given they aren't independent bets.

compute_position_scalar moved here (2026-07) from domain/signal.py, which
had held it since before this module existed -- it's genuinely risk-SIZING
math (vol-targeting, momentum/signal-confidence discounts), not signal
CONSTRUCTION (trend detection), so it belongs in the risk module, not
alongside build_features/continuous_momentum/goulding_monthly. This was the
second half of the same split that originally created this module:
compute_n_effective/compute_desired_risk_budget/apply_cluster_risk_cap moved
here unchanged from the old tsmom_signal.py (see git history for that
mechanical move); _bounded_ewm_correlation_matrix/compute_idm moved here from
derivatives_bt_engine.strats.tsmom_binary_vol_parity_backtest, which
originally built them for its own idm_scaling feature -- this is now the one
canonical implementation, not a duplicate, so any other caller (the live
system's own compute_desired_risk_budget, tsmom_backtester.py) can reuse the
same correlation math rather than re-deriving it.

compute_n_effective/compute_desired_risk_budget assume active clusters are
UNCORRELATED (their 1/sqrt(n_effective) scaling is exactly compute_idm's own
formula at rho=0) -- compute_idm generalizes that to the REAL measured
correlation. Not yet wired together (compute_desired_risk_budget still uses
the zero-correlation assumption as of this move) -- see
research/cta-layer-separation-risk-budgeting.md and this project's own
tsmom_risk_budget_diagnostic.py for the ERC/HRP comparison this was
motivated by.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import polars as pl
import scipy.cluster.hierarchy as sch
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from derivatives_bt_engine.domain.enums import TrendRegime
from derivatives_bt_engine.domain.signal import DEFAULT_ANNUALIZATION_DAYS

log = logging.getLogger(__name__)


def compute_position_scalar(trend_strength, daily_std_last, vol_target: float,
                             regime: TrendRegime, regime_discount: float = 0.5,
                             signal_confidence: float = 1.0,
                             annualization_days=DEFAULT_ANNUALIZATION_DAYS) -> float:
    """
    Layers 2-4 of the position sizing framework (plus the opt-in layer 5,
    signal_confidence), combined into a single scalar in [-1, +1]:

        scalar = trend_strength * risk_scalar * regime_discount * signal_confidence

    Long-only filtering (signal_scalar = max(0, trend_strength)) is the
    caller's responsibility — pass the already-filtered trend_strength in
    for long-only accounts. This function stays pure w.r.t. direction.

    risk_scalar = vol_target / current_realized_vol, clamped to [0.25, 2.0]
    -- a risk-equalization ratio driven by THIS instrument's own realized
    vol, nothing regime- or market-wide about it.
    current_realized_vol = daily_std_last * sqrt(annualization_days) -- the
    annualization is scaling daily_std_last up to an annual figure; the
    window daily_std_last happened to be estimated over (63-day rolling, in
    this project's callers) is irrelevant to that scaling itself.
    Callers should pass the SAME annualization_days used to compute
    daily_std_last in the first place (instruments.resolve_annualization_days)
    -- this project's confirmed universe splits 252 (CBOT grains) vs. 259
    (everything else checked); the plain 252 default here reproduces prior
    behavior exactly for anyone not passing an instrument-specific value.

    regime_discount is applied only for Correction/Rebound (disagreement
    between the fast and slow momentum signal — lower conviction);
    Bull/Bear/Unknown get a discount factor of 1.0. Despite the similar
    "discount" shape, this is unrelated to vix_scalar (the
    portfolio-wide, VX-driven de-risking lever applied by the caller in
    derivatives_bt_engine.live.tsmom_rebalance, not in here).

    signal_confidence (default 1.0, no-op) is a separate, opt-in, per-
    instrument discount on trust in THIS instrument's signal when its own
    vol_ratio (short-window/long-window realized vol, asset-specific, NOT
    VIX/VX-driven) is unusual relative to its own history -- see
    compute_signal_confidence() in signal.py. Orthogonal to regime_discount
    (which is about fast/slow sign disagreement, not vol) and to
    vix_scalar (which is portfolio-wide, not per-instrument).
    """
    if trend_strength is None or (isinstance(trend_strength, float) and math.isnan(trend_strength)):
        return 0.0

    if daily_std_last is None or (isinstance(daily_std_last, float) and math.isnan(daily_std_last)) or daily_std_last <= 0:
        risk_scalar = 1.0   # insufficient history to size by vol — neutral
    else:
        current_realized_vol = daily_std_last * math.sqrt(annualization_days) # annualized realized vol, estimated from daily_std_last's own trailing window
        risk_scalar = vol_target / current_realized_vol # 0.15/0.60 ~= 0.25, this is not hv_long here, but a param
        risk_scalar = max(0.25, min(2.0, risk_scalar))

    regime_discount = regime_discount if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0

    scalar = trend_strength * risk_scalar * regime_discount * signal_confidence
    return max(-1.0, min(1.0, scalar))


def compute_n_effective(active_clusters: set) -> int:
    """Count of clusters carrying a live signal -- the portfolio's
    effective number of independent bets, not its raw instrument count
    (e.g. 4 grain micros moving on one shared ag-complex factor are one
    bet, not four)."""
    return len(active_clusters)


def compute_desired_risk_budget(account_equity: float, target_portfolio_vol: float,
                                 n_effective: int) -> float:
    """Per-cluster dollar risk budget, sqrt(N)-scaled so total portfolio
    risk (assuming roughly independent clusters) targets
    account_equity * target_portfolio_vol regardless of how many clusters
    are currently active."""
    if n_effective <= 0:
        return 0.0
    return account_equity * target_portfolio_vol / math.sqrt(n_effective)


def apply_cluster_risk_cap(targets: list[dict], max_cluster_risk_pct: float,
                            total_risk_target: float, n_active_clusters: int,
                            max_lot_overrun_pct: float = 0.5) -> list[dict]:
    """
    Second pass over an already-sized targets list: for any cluster whose
    aggregate dollar-vol risk exceeds its share of a fixed, account-equity-
    derived risk target, allocates that cluster's capped budget by
    conviction priority rather than a uniform haircut, so e.g. 4 grain
    micros that are each individually sized correctly don't collectively
    become one oversized bet on the ag-complex factor they all share.

    The cap is taken against total_risk_target (account_equity *
    target_portfolio_vol, computed by the caller) -- a fixed number, NOT
    the emergent sum of this run's pre-cap position risks. effective_cap_pct
    = max(max_cluster_risk_pct, 1/n_active_clusters) -- the 1/n floor means
    a single active cluster isn't capped below 100% of the target just for
    being the only trade in the book.

    Allocation within an over-budget cluster (replaces a uniform scale
    factor, which produces an all-or-nothing failure mode: scaling every
    instrument down by the same factor can push ALL of them below the 0.5
    rounding threshold at once, even when the top-conviction instrument
    alone would easily survive on the full cap):

      1. Sort the cluster's instruments by priority = abs(scalar),
         descending -- scalar (not raw signal) since it already folds in
         vol-targeting, regime discount, and the long-only filter.
      2. Walk the sorted list with remaining_budget starting at the cap.
         For each instrument: affordable_continuous = remaining_budget /
         single_contract_risk (0 if remaining_budget <= 0); usable =
         min(abs(original_continuous), affordable_continuous); contracts =
         round(usable) if usable >= 0.5 else 0.
      3. Lot-size exception, first instrument only (remaining_budget still
         == cap, i.e. nothing spent yet): if usable < 0.5 (would round to
         zero) but the instrument's own signal genuinely wants a full
         contract (abs(original_continuous) >= 0.5) and its single-
         contract risk isn't wildly over the cap (<= cap * (1 +
         max_lot_overrun_pct)), it still gets exactly 1 contract -- a
         clearly-wanted top-conviction position shouldn't be sacrificed
         purely to discrete contract granularity. remaining_budget can go
         negative after this fires, which is intentional: it guarantees
         every later instrument in the walk gets 0.
      4. remaining_budget -= contracts * single_contract_risk; continue
         down the sorted list.

    Clusters within budget (risk <= cap) are untouched. Mixed-sign
    clusters work without special-casing -- risk is abs()-based, priority
    is abs(scalar), direction is sign(original_continuous).

    infeasible is an OUTCOME-based flag, computed after every cluster's
    walk-down (or no-op) is final: True for every instrument in a cluster
    where ALL instruments end up at target_contracts == 0 despite at least
    one having abs(continuous_contracts) >= 0.5 pre-cap -- i.e. a real
    signal existed but the cluster genuinely captured no exposure. This is
    NOT decided by a cap-vs-single-contract-risk precomputation (the top-
    priority instrument may still land a contract via the lot exception
    even when that precomputed check would have said "infeasible") --
    only the actual result matters.

    Each target dict must carry 'cluster', 'continuous_contracts', 'scalar',
    'close', 'multiplier', 'hv' (already computed per-instrument by the
    caller). Targets with an 'error' key, or missing one of those fields,
    are left untouched and excluded from the risk totals. Mutates and
    returns the same list (adds/overwrites 'target_contracts' and
    'position_risk', and 'infeasible' where applicable).
    """
    valid = [
        t for t in targets
        if not t.get('error')
        and t.get('continuous_contracts') is not None
        and t.get('cluster') is not None
        and t.get('close') is not None
        and t.get('multiplier') is not None
        and t.get('hv') is not None
    ]

    cluster_risk: dict[str, float] = {}
    for t in valid:
        position_risk = abs(t['continuous_contracts']) * t['close'] * t['multiplier'] * t['hv']
        cluster_risk[t['cluster']] = cluster_risk.get(t['cluster'], 0.0) + position_risk

    if total_risk_target is None or total_risk_target <= 0:
        return targets

    effective_cap_pct = max(max_cluster_risk_pct, 1.0 / n_active_clusters) if n_active_clusters > 0 else max_cluster_risk_pct
    cap = effective_cap_pct * total_risk_target

    over_budget_clusters = {cluster for cluster, risk in cluster_risk.items() if risk > cap}

    for cluster in over_budget_clusters:
        members = [t for t in valid if t['cluster'] == cluster]
        # Priority order is fixed before any mutation -- read each
        # instrument's original, unscaled continuous_contracts once, up
        # front, so later iterations of this loop can't see a value
        # another instrument's walk step already changed.
        members_sorted = sorted(members, key=lambda t: abs(t.get('scalar') or 0.0), reverse=True)
        original_continuous = {t['symbol']: t['continuous_contracts'] for t in members_sorted}

        remaining_budget = cap
        for i, t in enumerate(members_sorted):
            is_first = (i == 0)
            orig = original_continuous[t['symbol']]
            single_contract_risk = t['close'] * t['multiplier'] * t['hv']

            if remaining_budget <= 0:
                contracts = 0
            else:
                affordable_continuous = remaining_budget / single_contract_risk if single_contract_risk else 0.0
                usable_continuous = min(abs(orig), affordable_continuous)

                if (is_first and usable_continuous < 0.5 and abs(orig) >= 0.5
                        and single_contract_risk <= cap * (1 + max_lot_overrun_pct)):
                    contracts = 1
                else:
                    # math.floor(x + 0.5), not round(): round() ties to even
                    # (round(2.5) == 2), which would silently under-allocate
                    # a genuine 0.5-contract signal half the time.
                    contracts = math.floor(usable_continuous + 0.5) if usable_continuous >= 0.5 else 0

            sign = 1 if orig > 0 else (-1 if orig < 0 else 0)
            t['target_contracts'] = sign * contracts
            remaining_budget -= contracts * single_contract_risk

    # Clusters within budget (and any target not part of an over-budget
    # cluster) round their continuous value directly, once -- equivalent
    # to a no-op scale of 1.0 through the same single-rounding rule the
    # walk-down above uses.
    for t in valid:
        if t['cluster'] in over_budget_clusters:
            continue
        scaled = t['continuous_contracts']
        sign = 1 if scaled > 0 else (-1 if scaled < 0 else 0)
        magnitude = 0 if abs(scaled) < 0.5 else math.floor(abs(scaled) + 0.5)
        t['target_contracts'] = sign * magnitude

    # max_contracts clamp is the true last step, after sizing is otherwise
    # final, then position_risk is recomputed from that final value.
    for t in valid:
        max_contracts = t.get('max_contracts')
        if max_contracts is not None:
            t['target_contracts'] = max(-max_contracts, min(max_contracts, t['target_contracts']))
        t['position_risk'] = abs(t['target_contracts']) * t['close'] * t['multiplier'] * t['hv']

    # Outcome-based infeasibility: every instrument in the cluster ended up
    # at zero despite at least one having a genuine (>=0.5) pre-cap signal.
    for cluster in over_budget_clusters:
        members = [t for t in valid if t['cluster'] == cluster]
        had_real_signal = any(abs(t['continuous_contracts']) >= 0.5 for t in members)
        all_zero = all(t['target_contracts'] == 0 for t in members)
        if had_real_signal and all_zero:
            log.warning(
                "%s cluster captured zero exposure despite a live signal -- cap ($%.0f) "
                "could not accommodate any instrument in this cluster even with conviction-"
                "priority allocation; consider raising max_cluster_risk_pct, reducing "
                "n_effective's denominator effect, or excluding large-multiplier "
                "instruments from this account",
                cluster, cap,
            )
            for t in members:
                t['infeasible'] = True

    return targets


# Minimum rows in a bounded EWM correlation window before trusting the
# estimate at all -- see _bounded_ewm_correlation_matrix's own docstring.
MIN_IDM_WINDOW_ROWS = 63

# compute_symbol_notional_budget's notional_weighting choices -- see that
# function's own docstring for what each one does.
NOTIONAL_WEIGHTING_SCHEMES = ('flat', 'erc', 'hrp')


def build_returns_wide(price_data: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per date (inner-joined across every symbol's own daily
    close -- only dates common to ALL symbols survive), one column per
    symbol, values = that symbol's own simple daily return
    (close.pct_change()). Pure polars -- no pandas, per this project's own
    CLAUDE.md convention (pandas stays scoped to a single library call
    site, e.g. HRPOpt, never leaks into general data-handling code).
    Shared by every caller of _bounded_ewm_correlation_matrix (both TSMOM
    backtesters) -- build ONCE per backtest run and reuse at every
    rebalance date's own bounded-window slice; recomputing the raw return
    series per rebalance would be pure waste, only the EWM calc itself
    genuinely needs to run once per (rebalance date, bounded window)
    pair."""
    wide = None
    for sym, df in price_data.items():
        s = df.sort('ts_event').select('ts_event', pl.col('close').pct_change().alias(sym))
        wide = s if wide is None else wide.join(s, on='ts_event', how='inner')
    return wide.sort('ts_event').drop_nulls()


def _bounded_ewm_correlation_matrix(returns_wide: pl.DataFrame, symbols: list[str], as_of: date,
                                     window_years: float, halflife: float,
                                     min_rows: int = MIN_IDM_WINDOW_ROWS
                                     ) -> tuple[Optional[np.ndarray], bool]:
    """EWM-weighted correlation among `symbols`, computed ONLY from the
    trailing `window_years` slice of returns_wide ending STRICTLY before
    `as_of` (no lookahead) -- a genuinely BOUNDED window, not an unbounded
    full-history EWM. This distinction matters: a plain `.ewm_mean(half_life=h)`
    applied to the ENTIRE historical series never fully zeroes out old data
    -- it decays toward negligible weight but asymptotically, so a few
    percent of a 2026 correlation estimate could technically still trace
    back to 2010 even at a short halflife. Slicing to a bounded window
    FIRST, then computing the EWM only within that slice, guarantees
    exactly zero weight on anything older than `window_years` -- the EWM
    only supplies the within-window recency emphasis (Carver's "regime"
    weighting), not the outer bound on history.

    Built as a single joint Gram-matrix decomposition, not n*(n+1)/2
    separately-estimated pairwise correlations. Polars' own
    `.ewm_mean(half_life=..., adjust=True)` (the default `adjust`) is, at
    any given row, exactly a normalized static weight vector over the
    rows up to and including it: weight of row t is (1-alpha)^(age of t),
    alpha = 1 - 2**(-1/halflife), normalized to sum to 1 -- so evaluating
    "at the last row of the bounded slice" is equivalent to a single
    static weight vector `w` anchored at that last row. That lets the
    whole n x n covariance matrix be built in one shot: `X` = the bounded
    slice as a plain (T x n) array, `mu = w @ X` the weighted mean,
    `Z = sqrt(w)[:, None] * (X - mu)`, `cov = Z.T @ Z`. This is
    numerically identical (confirmed to ~1e-16, well within float noise)
    to computing each pair's ewm_cov(x, y) = ewm_mean(x*y) - ewm_mean(x) *
    ewm_mean(y) separately, but ~9x faster at n=12 (one BLAS matmul over
    the whole universe instead of O(n^2) individual polars EWM
    evaluations) and PSD *by construction* -- `cov` is a Gram matrix
    (`Z.T @ Z`), so no post-hoc eigenvalue check is needed the way a set
    of independently-estimated pairwise correlations would require, and
    each diagonal entry (a sum of squares) can't land below zero the way
    an independently-estimated ewm_var(x) occasionally does on
    floating-point noise.

    `returns_wide`: one row per date, one column per symbol, simple daily
    returns (a caller-built, synchronized wide frame -- e.g.
    tsmom_binary_vol_parity_backtest.py's own _build_returns_wide).

    Returns (h, has_corr_data). h is a dense n x n matrix (n = len(symbols),
    1.0 diagonal, 0.0 for any symbol missing from returns_wide, in
    `symbols`' own order) -- the canonical output now that this function
    builds one joint matrix rather than independently-estimated pairs (see
    above); there's no pairwise dict anywhere in this estimator to hand
    back. compute_idm/compute_erc_weights/compute_hrp_weights/
    compute_notional_split still accept a hand-built corr_pairs dict as an
    alternative input on their own signatures (a genuine ergonomic win for
    a test fixture or any other caller that doesn't have this function's
    output -- see e.g. test_allocation.py's _EQUAL_CORR) -- but that's
    those functions' own public interface, not something this internal
    estimator should round-trip through just to hand back a dict nothing
    here actually consumes.

    has_corr_data is an EXPLICIT usability signal, not something a caller
    should infer from h being None vs. eye(n): h alone can't distinguish
    "trust this window, but fewer than 2 symbols had data in it" (h =
    np.eye(len(symbols)), a mathematically valid "assume independent"
    matrix) from "don't trust this window at all" (h = None, too few
    rows). Both cases should be treated as NO usable correlation data by
    every caller (crediting independence for a symbol with zero return
    history in-window would grant it diversification credit with no
    actual evidence) -- has_corr_data is False for both, so a caller can
    always just do `h = h if has_corr_data else None` immediately after
    calling this, then forward that single, unambiguous `h` on: None means
    "nothing usable" to compute_idm/compute_erc_weights/compute_hrp_
    weights/compute_notional_split's own `h is None` fallback path, a real
    matrix means use it directly. This replaces the old implicit
    contract, where corr_pairs' OWN dict truthiness (None vs. {} vs.
    non-empty) was the only thing distinguishing these states -- accidental,
    since nothing about a dict being empty vs. None is inherently
    meaningful, just a side effect of what {} happened to mean here.

    (None, False) if the bounded slice itself has fewer than `min_rows`
    (too little history this early in the backtest to trust any
    correlation estimate); (np.eye(len(symbols)), False) if fewer than 2
    symbols have data in that slice (trust the window, but nothing to
    correlate); (h, True) otherwise."""
    window_start = as_of - timedelta(days=int(window_years * 365.25))
    sl = returns_wide.filter((pl.col('ts_event') >= window_start) & (pl.col('ts_event') < as_of))
    if sl.height < min_rows:
        return None, False

    present = [s for s in symbols if s in sl.columns]
    if len(present) < 2:
        return np.eye(len(symbols)), False

    # Static weight vector equivalent to ewm_mean(half_life=..., adjust=True)
    # evaluated at the last row of the slice: row t (0-indexed from the
    # start) gets weight (1-alpha)^((T-1)-t), normalized to sum to 1.
    T = sl.height
    alpha = 1.0 - 2.0 ** (-1.0 / halflife)
    age = (T - 1) - np.arange(T)
    weights = (1.0 - alpha) ** age
    weights /= weights.sum()

    X = sl.select(present).to_numpy()
    mu = weights @ X
    Z = np.sqrt(weights)[:, None] * (X - mu)
    cov = Z.T @ Z
    d = np.sqrt(np.diag(cov))
    with np.errstate(invalid='ignore', divide='ignore'):
        corr_present = cov / np.outer(d, d)
    # nan (0/0, a zero-variance/constant column in-window) defaults to the
    # same 0.0 off-diagonal np.eye already carries for an absent symbol;
    # +-inf can't arise mathematically here (Cauchy-Schwarz bounds |cov_ij|
    # by d_i*d_j, so a zero denominator forces a zero numerator too) but is
    # guarded the same way as a defensive floor against float noise, same
    # as the explicit clip below.
    corr_present = np.nan_to_num(corr_present, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(corr_present, -1.0, 1.0, out=corr_present)
    np.fill_diagonal(corr_present, 1.0)

    # Sized/ordered to the FULL requested `symbols`, not just `present` --
    # a symbol missing from returns_wide keeps its np.eye default (1.0
    # diag, 0.0 off-diag), matching _corr_matrix_from_pairs' own fallback
    # for a symbol absent from corr_pairs, so h is directly usable by any
    # active_symbols-ordered caller without reindexing. Embedded via one
    # vectorized fancy-index assignment, not a Python-level double loop --
    # the whole point of building corr_present as a matrix in the first
    # place was to get this out of per-element Python overhead.
    n = len(symbols)
    idx = {s: i for i, s in enumerate(symbols)}
    present_idx = np.array([idx[s] for s in present])
    h = np.eye(n)
    h[np.ix_(present_idx, present_idx)] = corr_present

    return h, True


def _corr_matrix_from_pairs(active_symbols: list[str],
                             corr_pairs: Optional[dict[tuple[str, str], float]]) -> np.ndarray:
    """Dense n x n correlation matrix (1.0 on the diagonal, corr_pairs off
    it, in active_symbols' own order) -- shared by compute_idm,
    compute_erc_weights, and compute_hrp_weights so all three price
    diversification off exactly the same pairwise correlation estimate,
    not three independently-reconstructed copies of it."""
    n = len(active_symbols)
    idx = {s: i for i, s in enumerate(active_symbols)}
    h = np.eye(n)
    for (a, b), corr in (corr_pairs or {}).items():
        if a in idx and b in idx:
            i, j = idx[a], idx[b]
            h[i, j] = h[j, i] = corr
    return h


def compute_erc_weights(active_symbols: list[str],
                         corr_pairs: Optional[dict[tuple[str, str], float]],
                         h: Optional[np.ndarray] = None) -> dict[str, float]:
    """Equal Risk Contribution weights (Maillard, Roncalli & Teiletche,
    2010), solved on the CORRELATION matrix directly rather than a raw
    covariance matrix -- deliberately, not a simplification of convenience:
    every instrument reaching this system's portfolio-level sizing has
    ALREADY been vol-equalized by compute_position_scalar's own
    risk_scalar (vol_target / current_realized_vol), so folding in each
    instrument's raw variance a second time here would double-count that
    normalization. Treating the correlation matrix (1.0 diagonal) as the
    relevant "covariance" is exactly the same simplification compute_idm's
    own W @ H @ W_t already makes.

    scipy.optimize.minimize (SLSQP) -- the same textbook formulation as
    scripts/tsmom_risk_budget_diagnostic.py's own compute_erc_weights (see
    that module's docstring for the original brief and why PyPortfolioOpt/
    riskfolio-lib weren't used there either): long-only, weights summing to
    1, minimizing the spread of each asset's risk contribution
    RC_i = w_i * (H @ w)_i / sqrt(w' H w). Unlike that read-only, manually-
    run diagnostic, this runs once per rebalance date across an entire
    backtest, so a non-convergent solve falls back to flat 1/n (logged),
    rather than raising -- one bad date's numerical hiccup shouldn't abort
    an otherwise-multi-year backtest run.

    Falls back to uniform 1/n (matching compute_idm's own fallback
    convention) when fewer than 2 active_symbols, or neither corr_pairs
    nor h carries any usable correlation data.

    `h`: pass the dense matrix directly if a caller already built it (e.g.
    _bounded_ewm_correlation_matrix's own output, or compute_symbol_
    notional_budget, which also needs it for compute_idm) -- skips
    _corr_matrix_from_pairs' otherwise-redundant rebuild, and works even
    when the caller has no corr_pairs dict at all. Built from corr_pairs
    when omitted."""
    n = len(active_symbols)
    if n < 2 or (not corr_pairs and h is None):
        return {s: 1.0 / n for s in active_symbols} if n > 0 else {}
    if h is None:
        h = _corr_matrix_from_pairs(active_symbols, corr_pairs)

    def risk_contributions(w):
        port_var = w @ h @ w
        if port_var <= 0:
            return np.zeros(n)
        marginal = h @ w
        return w * marginal / math.sqrt(port_var)

    def objective(w):
        rc = risk_contributions(w)
        return np.sum((rc - rc.mean()) ** 2)

    w0 = np.full(n, 1.0 / n)
    bounds = [(1e-6, 1.0)] * n
    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]
    result = minimize(objective, w0, method='SLSQP', bounds=bounds, constraints=constraints,
                      options={'maxiter': 1000, 'ftol': 1e-12})
    if not result.success:
        log.warning("ERC optimization failed to converge (%s) -- falling back to flat 1/n weights",
                    result.message)
        return {s: 1.0 / n for s in active_symbols}
    return dict(zip(active_symbols, result.x))


def _cluster_var(h: np.ndarray, cluster_idx: list[int]) -> float:
    """Inverse-variance-weighted cluster variance (Lopez de Prado's own
    getClusterVar) -- degenerates to a plain average of h's sub-block since
    every diagonal entry of h is 1.0 by construction (see
    compute_hrp_weights' own docstring for why that's the correct
    simplification here, not an oversight). Clamped to a small positive
    floor: h's off-diagonal entries are independently-estimated pairwise
    correlations, not guaranteed to form a jointly positive-semidefinite
    matrix for cluster sizes > 2, so this quadratic form can land a hair
    below zero on a near-degenerate block -- the caller's own alpha
    computation divides by (var_left + var_right), so a floor here matters
    more than it would for a lone diagonal read."""
    sub = h[np.ix_(cluster_idx, cluster_idx)]
    ivp = 1.0 / np.diag(sub)
    ivp /= ivp.sum()
    return max(float(ivp @ sub @ ivp), 1e-12)


def _hrp_recursive_bisection(h: np.ndarray, order: list[int]) -> np.ndarray:
    """Lopez de Prado's getRecBipart (2016), translated from his own
    pandas-Series reference implementation to a plain numpy array indexed
    by original (pre-reorder) position -- `order` (the dendrogram leaf
    order) determines split structure only; weights are written back at
    each element's original index either way, so the returned array is
    already aligned to the caller's own active_symbols order."""
    w = np.ones(len(order))
    clusters = [order]
    while clusters:
        clusters = [c[i:j] for c in clusters
                    for i, j in ((0, len(c) // 2), (len(c) // 2, len(c)))
                    if len(c) > 1]
        for i in range(0, len(clusters), 2):
            left, right = clusters[i], clusters[i + 1]
            var_left = _cluster_var(h, left)
            var_right = _cluster_var(h, right)
            alpha = min(1.0, max(0.0, 1.0 - var_left / (var_left + var_right)))
            w[left] *= alpha
            w[right] *= 1.0 - alpha
    # Guaranteed to sum to ~1 already by the recursive halving/complement
    # property (alpha + (1 - alpha) == 1 at every split); normalizing is
    # just a floating-point-drift correction, not a structural fix.
    return w / w.sum()


def compute_hrp_weights(active_symbols: list[str],
                         corr_pairs: Optional[dict[tuple[str, str], float]],
                         h: Optional[np.ndarray] = None) -> dict[str, float]:
    """Hierarchical Risk Parity (Lopez de Prado, 2016), reimplemented here
    in pure numpy/scipy rather than calling PyPortfolioOpt's HRPOpt
    (scripts/tsmom_risk_budget_diagnostic.py's own choice, for a one-off,
    manually-run comparison). Two reasons this needs its own
    implementation instead of reusing that one: HRPOpt requires a pandas
    DataFrame input (this project's CLAUDE.md keeps pandas scoped to a
    single library call site, not a per-rebalance-date hot path across an
    entire backtest), and HRPOpt.optimize() guards its linkage_method
    argument against a private scipy attribute that scipy >= 1.18 removed
    entirely, needing its own compatibility shim. scipy.cluster.hierarchy's
    linkage/leaves_list gives the same dendrogram leaf order directly, with
    no pandas and no plotting dependency (dendrogram() itself is never
    called here -- leaves_list reads the leaf order straight off the
    linkage matrix).

    Like compute_erc_weights, operates on the correlation matrix directly
    rather than a raw covariance matrix, for the same reason: every
    instrument is already vol-equalized upstream by compute_position_
    scalar's own risk_scalar. The one accepted consequence (see
    _cluster_var's own docstring): HRP's usual "size inversely to each
    cluster member's own variance" step degenerates to an equal split
    within a cluster, since every diagonal entry of the correlation matrix
    is 1.0 -- exactly consistent with that vol-equalization having already
    happened, not a lost feature.

    Falls back to uniform 1/n (matching compute_idm's own fallback
    convention) when fewer than 2 active_symbols, or neither corr_pairs
    nor h carries any usable correlation data.

    `h`: pass the dense matrix directly if a caller already built it --
    skips _corr_matrix_from_pairs' otherwise-redundant rebuild, and works
    even when the caller has no corr_pairs dict at all. Built from
    corr_pairs when omitted."""
    n = len(active_symbols)
    if n < 2 or (not corr_pairs and h is None):
        return {s: 1.0 / n for s in active_symbols} if n > 0 else {}
    if h is None:
        h = _corr_matrix_from_pairs(active_symbols, corr_pairs)

    distance = np.sqrt(np.clip(0.5 * (1.0 - h), 0.0, None))
    np.fill_diagonal(distance, 0.0)
    condensed = squareform(distance, checks=False)
    link = sch.linkage(condensed, method='single')
    order = sch.leaves_list(link).tolist()

    w = _hrp_recursive_bisection(h, order)
    return dict(zip(active_symbols, w))


def compute_notional_split(active_symbols: list[str], corr_pairs: Optional[dict[tuple[str, str], float]],
                            notional_weighting: str, h: Optional[np.ndarray] = None) -> dict[str, float]:
    """The 'flat'/'erc'/'hrp' fraction of the total dollar-vol budget each
    active symbol gets -- the same split compute_symbol_notional_budget
    computes internally (and, when use_idm=True, feeds into compute_idm as
    its own weight vector), pulled out into its own function so a caller
    that already has active_symbols/corr_pairs can inspect the split
    itself directly (e.g. reporting/diagnostics -- what fraction of the
    book did ERC/HRP actually give this symbol), not just the resulting
    dollar figure. 'flat': 1/n each. 'erc'/'hrp': compute_erc_weights/
    compute_hrp_weights on corr_pairs -- see either's own docstring for
    the fallback-to-flat behavior when corr_pairs is None/empty or
    n < 2.

    `h`: forwarded to compute_erc_weights/compute_hrp_weights if a caller
    already built the dense matrix from this same `corr_pairs` -- see
    either's own docstring. Ignored for 'flat'."""
    if notional_weighting not in NOTIONAL_WEIGHTING_SCHEMES:
        raise ValueError(f"notional_weighting must be one of {NOTIONAL_WEIGHTING_SCHEMES}, "
                          f"got {notional_weighting!r}")
    if not active_symbols:
        return {}
    if notional_weighting == 'flat':
        return {s: 1.0 / len(active_symbols) for s in active_symbols}
    if notional_weighting == 'erc':
        return compute_erc_weights(active_symbols, corr_pairs, h)
    return compute_hrp_weights(active_symbols, corr_pairs, h)


def compute_symbol_notional_budget(active_symbols: list[str], returns_wide: Optional[pl.DataFrame],
                                    as_of: date, capital: float, target_portfolio_vol: float,
                                    vol_target: float, idm_window_years: float,
                                    idm_halflife_days: float,
                                    notional_weighting: str = 'flat',
                                    use_idm: bool = True,
                                    h: Optional[np.ndarray] = None) -> dict[str, float]:
    """IDM-derived per-symbol notional_budget for TsmomBacktestConfig's
    target_portfolio_vol path (tsmom_backtester.py's run_tsmom_backtest) --
    the correlation-aware alternative to sizing off a flat config.max_notional.
    Computes a total target DOLLAR VOL budget (capital * target_portfolio_vol *
    IDM, where IDM captures active_symbols' REAL measured correlation rather
    than assuming independence), splits it across active_symbols per
    `notional_weighting`, then divides by vol_target to convert each
    symbol's own dollar-vol share back into the notional_budget
    _compute_signal_row actually expects.

    The division by vol_target matters: scalar already folds in
    risk_scalar = vol_target / current_realized_vol (compute_position_scalar's
    own per-instrument vol-equalization), so passing the dollar-vol target
    straight through as notional_budget would apply vol_target a SECOND
    time on top of an already-vol-target-derived figure -- confirmed
    directly during this function's extraction from tsmom_backtester.py,
    where an earlier version did exactly that and undershot a 15% target by
    ~24x.

    notional_weighting (NOTIONAL_WEIGHTING_SCHEMES) selects how the total
    dollar-vol budget is split ACROSS active_symbols:
      'flat' (default -- exact prior behavior, unchanged): the total is
        split equally across active_symbols regardless of correlation
        structure -- every symbol gets the same budget.
      'erc' / 'hrp': split via compute_erc_weights/compute_hrp_weights on
        the SAME bounded correlation matrix already built for IDM -- a
        correlated cluster's members collectively end up with roughly one
        undiversified bet's worth of budget, rather than each member
        separately claiming an equal share of the whole book. This is the
        automated, data-driven alternative to Carver's hand-assigned
        subgroups that research/cta-layer-separation-risk-budgeting.md
        flags as this system's least-precedented, unaddressed gap.

    The split is computed FIRST and then fed into compute_idm as `weights`
    (when use_idm=True) -- NOT the flat 1/n vector regardless of
    notional_weighting, as an earlier version of this function did. Using
    mismatched weights would double-count diversification: IDM would credit
    the active set's correlation structure once (sized as if the total were
    about to be split flat), and then 'erc'/'hrp' would credit it again by
    reshaping that same total's distribution to reduce correlated exposure
    further, systematically undershooting target_portfolio_vol beyond the
    imprecision this module's own docstring already documents for 'flat'.
    Feeding the SAME weight vector into both the multiplier and the split
    means IDM answers "how diversified is the portfolio given the weights
    I'm actually about to use," not a hypothetical flat one -- exactly one
    diversification adjustment, self-consistently applied. For 'flat' this
    is numerically identical to the prior flat-vector behavior.

    use_idm: True (default) applies the IDM multiplier as described above.
    False skips it entirely (total_dollar_vol_target = capital *
    target_portfolio_vol, no correlation-based up/down-sizing of the total)
    -- for notional_weighting='erc'/'hrp' this isolates the diversification
    adjustment to the split alone (closer to Option B in
    research/cta-layer-separation-risk-budgeting.md's IDM-vs-allocation
    framing); for 'flat' it removes correlation-awareness altogether.

    Returns {} if active_symbols is empty or returns_wide is None -- the
    caller's own probe pass already means every such symbol's target is 0
    regardless of budget, so there's nobody to size for. A too-short
    bounded correlation window (_bounded_ewm_correlation_matrix's own
    min_rows floor) instead makes compute_idm (and, under 'erc'/'hrp',
    the weighting itself) fall back to its own flat/no-adjustment default,
    not an early return here.

    `h`: pass it directly if a caller already ran
    _bounded_ewm_correlation_matrix for this exact (active_symbols, as_of,
    idm_window_years, idm_halflife_days) -- e.g. the live rebalance report,
    which needs h itself for notional_weight_by_symbol before ever calling
    this function. Skips rerunning the EWM estimation over returns_wide a
    second time. None here always means "no usable correlation data" (the
    caller must have already squashed a False has_corr_data into None --
    see _bounded_ewm_correlation_matrix's own docstring), which is exactly
    what triggers compute_notional_split/compute_idm's own flat/no-
    adjustment fallback below. Recomputed from returns_wide when h is
    omitted (the default)."""
    if notional_weighting not in NOTIONAL_WEIGHTING_SCHEMES:
        raise ValueError(f"notional_weighting must be one of {NOTIONAL_WEIGHTING_SCHEMES}, "
                          f"got {notional_weighting!r}")
    if not active_symbols or returns_wide is None:
        return {}
    if h is None:
        h, has_corr_data = _bounded_ewm_correlation_matrix(returns_wide, active_symbols, as_of,
                                                            idm_window_years, idm_halflife_days)
        h = h if has_corr_data else None
    split = compute_notional_split(active_symbols, None, notional_weighting, h)
    idm_multiplier = compute_idm(active_symbols, None, weights=split, h=h) if use_idm else 1.0
    total_dollar_vol_target = capital * target_portfolio_vol * idm_multiplier

    return {s: (total_dollar_vol_target * split[s]) / vol_target for s in active_symbols}


def compute_idm(active_symbols: list[str], corr_pairs: Optional[dict[tuple[str, str], float]],
                 weights: Optional[dict[str, float]] = None,
                 h: Optional[np.ndarray] = None) -> float:
    """Carver's Instrument Diversification Multiplier, exact matrix form:
    IDM = 1 / sqrt(W @ H @ W_t) -- W the weight vector (equal, 1/n each,
    when `weights` is None; otherwise `weights` normalized so
    sum(abs(w)) == 1, preserving each symbol's relative and signed share),
    H the REAL pairwise correlation matrix (1.0 on the diagonal,
    corr_pairs off it) -- NOT the average-correlation algebraic shortcut
    (1/sqrt(1/N + (1-1/N)*avg_corr)), which is only exactly equivalent to
    this when every pairwise correlation happens to be identical AND
    weights are equal. Since _bounded_ewm_correlation_matrix already
    builds the full matrix, using it directly costs nothing extra over
    averaging its entries down to one scalar first.

    Per Carver's own convention, H is floored at 0 here (negative
    pairwise correlations clamped to 0) before computing IDM -- NOT for
    compute_erc_weights/compute_hrp_weights, which use the real signed
    matrix, since a negative correlation is a genuine hedging signal
    those optimizers should weigh into the allocation itself. IDM asks a
    different question -- how much bigger can the total book be, given
    today's measured diversification -- and a negative correlation is
    precisely the estimate least likely to survive a stress regime:
    correlations tend to converge upward exactly when diversification
    would matter most, so IDM shouldn't credit extra leverage for a hedge
    that may not hold.

    Falls back to 1.0 (no diversification adjustment) whenever there's
    nothing meaningful to compute from: fewer than 2 active_symbols, or
    neither corr_pairs nor h carries any usable correlation data (not
    enough bounded-window history yet, or fewer than 2 symbols had
    synchronized return data).

    `h`: pass the dense (real, signed) matrix directly if a caller already
    built it (e.g. compute_symbol_notional_budget, which also needs it
    for compute_erc_weights/compute_hrp_weights) -- skips
    _corr_matrix_from_pairs' otherwise-redundant rebuild, and works even
    when the caller has no corr_pairs dict at all. The 0-floor above is
    still applied here regardless of which path `h` came from -- it's an
    IDM-specific adjustment, not a property of the shared matrix itself.
    Built from corr_pairs when omitted."""
    n = len(active_symbols)
    if n < 2 or (not corr_pairs and h is None):
        return 1.0
    if h is None:
        h = _corr_matrix_from_pairs(active_symbols, corr_pairs)
    h = np.clip(h, 0.0, 1.0)
    if weights is None:
        w = np.full(n, 1.0 / n)
    else:
        raw = np.array([weights.get(s, 0.0) for s in active_symbols])
        total_abs = np.abs(raw).sum()
        w = raw / total_abs if total_abs > 0 else np.full(n, 1.0 / n)
    port_var = w @ h @ w
    if port_var <= 0:
        return 1.0
    return 1.0 / math.sqrt(port_var)

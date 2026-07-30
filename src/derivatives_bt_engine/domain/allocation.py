"""
Cross-instrument risk allocation -- how much risk/budget each instrument or
cluster gets relative to the others, given they aren't independent bets.

Split out from tsmom_signal.py (2026-07), which is about a SINGLE
instrument's own signal-to-scalar math (compute_position_scalar) -- these
functions are a genuinely different concern: given several instruments each
already have their own signal, how do you size them RELATIVE to each other.
compute_n_effective/compute_desired_risk_budget/apply_cluster_risk_cap moved
here unchanged from tsmom_signal.py (see git history for the mechanical
move); _bounded_ewm_correlation_matrix/compute_idm moved here from
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

import itertools
import logging
import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import polars as pl

log = logging.getLogger(__name__)


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
    ]

    cluster_risk: dict[str, float] = {}
    for t in valid:
        hv = t.get('hv') or 0.0
        position_risk = abs(t['continuous_contracts']) * t['close'] * t['multiplier'] * hv
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
            single_contract_risk = t['close'] * t['multiplier'] * (t.get('hv') or 0.0)

            if remaining_budget <= 0:
                contracts = 0
            else:
                affordable_continuous = remaining_budget / single_contract_risk if single_contract_risk else 0.0
                usable_continuous = min(abs(orig), affordable_continuous)

                if (is_first and usable_continuous < 0.5 and abs(orig) >= 0.5
                        and single_contract_risk <= cap * (1 + max_lot_overrun_pct)):
                    contracts = 1
                else:
                    contracts = round(usable_continuous) if usable_continuous >= 0.5 else 0

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
        magnitude = 0 if abs(scaled) < 0.5 else round(abs(scaled))
        t['target_contracts'] = sign * magnitude

    # max_contracts clamp is the true last step, after sizing is otherwise
    # final, then position_risk is recomputed from that final value.
    for t in valid:
        max_contracts = t.get('max_contracts')
        if max_contracts is not None:
            t['target_contracts'] = max(-max_contracts, min(max_contracts, t['target_contracts']))
        t['position_risk'] = abs(t['target_contracts']) * t['close'] * t['multiplier'] * (t.get('hv') or 0.0)

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


def _bounded_ewm_correlation_matrix(returns_wide: pl.DataFrame, symbols: list[str], as_of: date,
                                     window_years: float, halflife: float,
                                     min_rows: int = MIN_IDM_WINDOW_ROWS) -> Optional[dict[tuple[str, str], float]]:
    """Pairwise EWM-weighted correlation among `symbols`, computed ONLY
    from the trailing `window_years` slice of returns_wide ending
    STRICTLY before `as_of` (no lookahead) -- a genuinely BOUNDED window,
    not an unbounded full-history EWM. This distinction matters: a plain
    `.ewm_mean(half_life=h)` applied to the ENTIRE historical series never
    fully zeroes out old data -- it decays toward negligible weight but
    asymptotically, so a few percent of a 2026 correlation estimate could
    technically still trace back to 2010 even at a short halflife. Slicing
    to a bounded window FIRST, then computing the EWM only within that
    slice, guarantees exactly zero weight on anything older than
    `window_years` -- the EWM only supplies the within-window recency
    emphasis (Carver's "regime" weighting), not the outer bound on
    history.

    EWM correlation itself is the standard product-moment formula (no
    pandas .ewm().cov() equivalent needed -- polars' own
    `.ewm_mean(half_life=...)` is sufficient to build it directly, and this
    project's own CLAUDE.md keeps pandas scoped to single library call
    sites like HRPOpt, never general data-handling code):
        ewm_cov(x, y)  = ewm_mean(x*y) - ewm_mean(x) * ewm_mean(y)
        ewm_var(x)     = ewm_mean(x*x) - ewm_mean(x) ** 2
        ewm_corr(x, y) = ewm_cov(x, y) / sqrt(ewm_var(x) * ewm_var(y))
    evaluated at the LAST row of the bounded slice (i.e. the most recent
    EWM value as of the end of that window).

    `returns_wide`: one row per date, one column per symbol, simple daily
    returns (a caller-built, synchronized wide frame -- e.g.
    tsmom_binary_vol_parity_backtest.py's own _build_returns_wide).

    Returns {(a, b): corr} for every pair of `symbols` present as columns
    in returns_wide (a symbol missing from returns_wide -- e.g. too new to
    have a synchronized row yet -- is silently excluded from all pairs,
    not errored); an empty dict if fewer than 2 symbols are present; or
    None if the bounded slice itself has fewer than `min_rows` (too little
    history this early in the backtest to trust any correlation
    estimate)."""
    window_start = as_of - timedelta(days=int(window_years * 365.25))
    sl = returns_wide.filter((pl.col('ts_event') >= window_start) & (pl.col('ts_event') < as_of))
    if sl.height < min_rows:
        return None

    present = [s for s in symbols if s in sl.columns]
    if len(present) < 2:
        return {}

    pairs = list(itertools.combinations(present, 2))
    exprs = []
    for a, b in pairs:
        mean_a = pl.col(a).ewm_mean(half_life=halflife)
        mean_b = pl.col(b).ewm_mean(half_life=halflife)
        mean_ab = (pl.col(a) * pl.col(b)).ewm_mean(half_life=halflife)
        mean_a2 = (pl.col(a) * pl.col(a)).ewm_mean(half_life=halflife)
        mean_b2 = (pl.col(b) * pl.col(b)).ewm_mean(half_life=halflife)
        cov = mean_ab - mean_a * mean_b
        var_a = mean_a2 - mean_a ** 2
        var_b = mean_b2 - mean_b ** 2
        exprs.append((cov / (var_a * var_b).sqrt()).last().alias(f'{a}__{b}'))

    row = sl.select(exprs).row(0)
    return {pair: val for pair, val in zip(pairs, row) if val is not None and not math.isnan(val)}


def compute_idm(active_symbols: list[str], corr_pairs: Optional[dict[tuple[str, str], float]],
                 weights: Optional[dict[str, float]] = None) -> float:
    """Carver's Instrument Diversification Multiplier, exact matrix form:
    IDM = 1 / sqrt(W @ H @ W_t) -- W the weight vector (equal, 1/n each,
    when `weights` is None; otherwise `weights` normalized so
    sum(abs(w)) == 1, preserving each symbol's relative and signed share),
    H the REAL pairwise correlation matrix (1.0 on the diagonal,
    corr_pairs off it) -- NOT the average-correlation algebraic shortcut
    (1/sqrt(1/N + (1-1/N)*avg_corr)), which is only exactly equivalent to
    this when every pairwise correlation happens to be identical AND
    weights are equal. Since _bounded_ewm_correlation_matrix already
    computes every individual pair, using the real matrix costs nothing
    extra over averaging them down to one scalar first.

    Falls back to 1.0 (no diversification adjustment) whenever there's
    nothing meaningful to compute from: fewer than 2 active_symbols, or
    corr_pairs is None/empty (not enough bounded-window history yet, or
    fewer than 2 symbols had synchronized return data)."""
    n = len(active_symbols)
    if n < 2 or not corr_pairs:
        return 1.0
    idx = {s: i for i, s in enumerate(active_symbols)}
    h = np.eye(n)
    for (a, b), corr in corr_pairs.items():
        if a in idx and b in idx:
            i, j = idx[a], idx[b]
            h[i, j] = h[j, i] = corr
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

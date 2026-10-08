"""Causal covariance and correlation estimation for futures portfolios.

This module converts per-instrument close histories into synchronized returns
and estimates a bounded, exponentially weighted covariance Gram matrix and its
correlation form.  Allocation consumes the resulting matrix and coverage mask;
it does not own the statistical estimator.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl


# Minimum rows in a bounded EWM correlation window before trusting the
# estimate at all -- see bounded_ewm_correlation_matrix's own docstring.
MIN_CORRELATION_WINDOW_ROWS = 63


def build_returns_wide(price_data: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per date (inner-joined across every symbol's own daily
    close -- only dates common to ALL symbols survive), one column per
    symbol, values = that symbol's own simple daily return
    (close.pct_change()). Pure Polars: pandas stays scoped to a single
    library call site, e.g. HRPOpt, and does not leak into general data
    handling.
    Shared by every caller of bounded_ewm_correlation_matrix (both TSMOM
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


def bounded_ewm_correlation_matrix(returns_wide: pl.DataFrame, symbols: list[str], as_of: date,
                                     window_years: float, halflife: float,
                                     min_rows: int = MIN_CORRELATION_WINDOW_ROWS
                                     ) -> tuple[np.ndarray, np.ndarray]:
    """EWM-weighted correlation among `symbols`, computed ONLY from the
    trailing `window_years` slice of returns_wide ending STRICTLY before
    `as_of` (no lookahead) -- a genuinely BOUNDED window, not an unbounded
    full-history EWM. This distinction matters: a plain `.ewm_mean(half_life=hl)`
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

    Returns (H, covered). H is ALWAYS a dense n x n matrix (n = len(symbols),
    never None -- np.eye(n) is a real, usable "nothing measured" placeholder,
    not an error signal); the canonical output now that this function builds
    one joint matrix rather than independently-estimated pairs (see above).
    compute_idm/compute_erc_weights/compute_hrp_weights/compute_notional_
    split all take H directly too (H is their sole correlation-data input --
    no hand-built corr_pairs dict accepted on those functions' own
    signatures either; a caller with only pairwise correlations writes H
    directly, e.g. test_allocation.py's own fixtures), so there's no
    pairwise dict anywhere in this path at all -- one representation, not
    two that could drift apart.

    covered is a length-n boolean array (True where `symbols[i]` actually
    had return data in this window, `symbols`' own order) -- an EXPLICIT,
    PER-SYMBOL usability signal, not something a caller should infer from H
    itself. This matters because H's identity-default entries (1.0 diag,
    0.0 off-diag) for an uncovered symbol are a computational placeholder,
    not a measurement: H[i, j] = 0 for an uncovered symbol i means "no
    correlation was measured," NOT "this symbol was measured and found to
    be uncorrelated with j." Treating those two as the same thing silently
    manufactures fake diversification credit -- confirmed directly: adding
    one zero-history symbol to an otherwise-real 2-asset correlation matrix
    inflated IDM by 34% and handed that symbol the largest individual ERC
    weight of the three, purely because "unknown" was encoded as "measured
    independent." Every caller that cares about this distinction (currently
    compute_notional_split/compute_symbol_notional_budget, both of which
    accept `covered` and route uncovered symbols to a capped, correlation-
    blind fallback allocation instead of letting them participate in H at
    all -- see UNCOVERED_BUDGET_CAP_FRACTION) must consult `covered`
    directly rather than trusting H's own entries for an uncovered symbol.

    A caller that doesn't care about the covered/uncovered distinction (or
    is calling compute_idm/compute_erc_weights/compute_hrp_weights
    directly, without going through compute_notional_split/compute_symbol_
    notional_budget) should still guard against the DEGENERATE case where
    NOTHING is covered (covered.all() is False and covered.any() is False,
    i.e. `covered.sum() == 0`): H is np.eye(n) there too, and passing it
    straight through would manufacture the same fake-independence credit
    for the WHOLE active set, not just one symbol -- squash H to None in
    that case (`H = None if not covered.any() else H`) so compute_idm's
    etc. own `H is None` fallback fires instead.

    (np.eye(len(symbols)), all-False) if the bounded slice itself has fewer
    than `min_rows` (too little history this early in the backtest to trust
    ANY correlation estimate, regardless of which symbols technically have
    a column) or if fewer than 2 symbols have data in that slice; (H,
    per-symbol coverage) otherwise, where H's covered rows/columns are real
    measurements and its uncovered ones are the identity placeholder."""
    if 'date' in returns_wide.columns:
        date_column = 'date'
    elif 'ts_event' in returns_wide.columns:
        # Existing return-TSMOM callers still expose the older generic event
        # key; daily forecast/selection frames use the clearer ``date`` name.
        date_column = 'ts_event'
    else:
        raise ValueError("returns_wide must contain 'date' or 'ts_event'")
    window_start = as_of - timedelta(days=int(window_years * 365.25))
    sl = returns_wide.filter(
        (pl.col(date_column) >= window_start) & (pl.col(date_column) < as_of)
    )
    n = len(symbols)
    if sl.height < min_rows:
        return np.eye(n), np.zeros(n, dtype=bool)

    present = [s for s in symbols if s in sl.columns]
    covered = np.array([s in present for s in symbols])
    if len(present) < 2:
        return np.eye(n), covered

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
    C = Z.T @ Z
    d = np.sqrt(np.diag(C))
    with np.errstate(invalid='ignore', divide='ignore'):
        corr_present = C / np.outer(d, d)
    # nan (0/0, a zero-variance/constant column in-window) defaults to the
    # same 0.0 off-diagonal np.eye already carries for an absent symbol;
    # +-inf can't arise mathematically here (Cauchy-Schwarz bounds |cov_ij|
    # by d_i*d_j, so a zero denominator forces a zero numerator too) but is
    # guarded the same way as a defensive floor against float noise, same
    # as the explicit clip below.
    corr_present = np.nan_to_num(corr_present, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(corr_present, -1.0, 1.0, out=corr_present)
    np.fill_diagonal(corr_present, 1.0)

    # Embed the correlation matrix for symbols with data into the FULL
    # requested symbol universe, preserving `symbols` order. Symbols with
    # no data retain the identity fallback (diag=1, off-diag=0) -- a
    # placeholder for "unmeasured," not evidence of independence; `covered`
    # (built above, before this block) is what tells a caller which is
    # which.
    idx = {s: i for i, s in enumerate(symbols)}
    present_idx = np.array([idx[s] for s in present])
    H = np.eye(n)
    H[np.ix_(present_idx, present_idx)] = corr_present

    return H, covered

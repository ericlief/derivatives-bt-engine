"""Monthly Goulding momentum forecasts and causal mixing estimation.

This module owns the complete Goulding, Harvey, and Mazzoleni signal path:
monthly return aggregation, regime classification, equation-7 blending,
continuous forecast normalization, and expanding-window pooled estimates of
the Correction and Rebound mixing parameters.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional, cast

import polars as pl


GOULDING_FAST_MONTHS = 2
GOULDING_SLOW_MONTHS = 12
GOULDING_SIGNAL_MODES = ("binary", "continuous")
GOULDING_FORECAST_TARGET_ABS = 0.5
GOULDING_FORECAST_CAP = 1.0
GOULDING_FORECAST_MIN_OBS = 12
MIN_MONTHS_PER_PHASE = 12
_DEGENERATE_EPS = 1e-10


def goulding_monthly(df: pl.DataFrame, fast_months: int = GOULDING_FAST_MONTHS,
                      slow_months: int = GOULDING_SLOW_MONTHS) -> pl.DataFrame:
    """Goulding, Harvey & Mazzoleni's own monthly momentum construction --
    independent of continuous_momentum; takes only build_features' output
    and uses only 'ts_event'/'close' from it (ignores r1d/dd/peak
    entirely). NO volatility normalization anywhere in this function
    (contrast continuous_momentum's ts_fast/ts_slow, which are vol-scaled)
    -- ret/fast/slow are pure arithmetic returns and their trailing
    averages.

    Monthly return is a SIMPLE return between consecutive month-END closes
    (this month's last close / prior month's last close - 1) -- the
    standard academic-finance monthly-return convention (CRSP, Fama-
    French, and by extension Goulding et al.'s own eq. 1-2), NOT an intra-
    month first-to-last-trading-day return: that alternative would silently
    exclude the single trading day's return spanning the prior month's
    close -> this month's first trading day from every month's figure,
    understating every single month's realized return by exactly one
    day's move. fast/slow are the trailing mean of the last fast_months/
    slow_months COMPLETED months -- shift(1) before rolling_mean, so month
    m's own (still-forming) return never leaks into its own signal; no
    lookahead.

    Defensively sorts by ts_event first -- group_by_dynamic requires a
    sorted temporal column, and this function is documented as
    independently callable on bare OHLCV (not required to go through
    build_features first), so it can't rely on a caller having sorted."""
    monthly = df.sort('ts_event').group_by_dynamic('ts_event', every='1mo', closed='left').agg(
        pl.col('close').last(),
    )
    monthly = monthly.with_columns(
        ret=pl.col('close') / pl.col('close').shift(1) - 1,
    )
    monthly = monthly.with_columns(
        fast=pl.col('ret').shift(1).rolling_mean(fast_months),
        slow=pl.col('ret').shift(1).rolling_mean(slow_months),
    )
    monthly = monthly.with_columns(
        regime=(
            pl.when((pl.col('fast') < 0) & (pl.col('slow') < 0)).then(pl.lit('bear'))
            .when((pl.col('fast') >= 0) & (pl.col('slow') >= 0)).then(pl.lit('bull'))
            .when((pl.col('fast') < 0) & (pl.col('slow') >= 0)).then(pl.lit('correction'))
            .when((pl.col('fast') >= 0) & (pl.col('slow') < 0)).then(pl.lit('rebound'))
        ),
    )
    return monthly


def _goulding_blend(regime_val: Optional[str], a_co: float, a_re: float,
                     r_fast: Optional[float] = None, r_slow: Optional[float] = None) -> Optional[float]:
    """The raw eq. 7 blended value -- (1-a_Co)*r_SLOW + a_Co*r_FAST in
    Correction, (1-a_Re)*r_SLOW + a_Re*r_FAST in Rebound -- BEFORE taking
    its sign. _goulding_direction calls this internally and returns
    sign(this) alongside this raw value itself, in a single (direction,
    blend) call, so a caller never needs to invoke this function a second
    time just to get the audit value -- e.g.
    src/derivatives_bt_engine/strats/tsmom_binary_vol_parity_backtest.py's rebalance CSV
    reports the raw score alongside the actual +-1/0 position weight from
    that one call, so a reader isn't left wondering how e.g. a_co=0.5
    (which looks like it should be a "neutral" input) produced a nonzero
    directional weight -- it's because the blend of the ACTUAL r_fast/
    r_slow landed nonzero, not because a_co itself carried directional
    information at 0.5. Still exposed as its own function (rather than
    folded entirely into _goulding_direction) because it's independently
    unit-tested and is the single place eq. 7's math and its input
    validation live.

    None for Bull/Bear (eq. 7 doesn't apply there -- they're
    unconditionally +-1 in _goulding_direction, with no blend to report)
    or when regime_val/r_fast/r_slow are missing/invalid. See
    _goulding_direction's own docstring for the full r_fast/r_slow
    semantics (Goulding's lagged trailing-average momentum signals, not a
    same-period realized return) and the a_co/a_re range/regime-
    consistency checks, both applied here too since this function is
    equally public and independently callable."""
    if not (0.0 <= a_co <= 1.0) or not (0.0 <= a_re <= 1.0):
        raise ValueError(f"a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range), got a_co={a_co}, a_re={a_re}")
    if regime_val is None:
        return None
    r = regime_val.lower()
    if r == 'correction':
        weight = a_co
    elif r == 'rebound':
        weight = a_re
    else:
        return None
    if (r_fast is None or r_slow is None
            or (isinstance(r_fast, float) and math.isnan(r_fast))
            or (isinstance(r_slow, float) and math.isnan(r_slow))):
        return None
    # Invariant check, not input validation (hence assert, not raise) --
    # goulding_monthly's own classification is exactly Correction:
    # fast<0<=slow, Rebound: fast>=0>slow, so a regime_val/r_fast/r_slow
    # combination that violates this can only mean the caller's regime and
    # signal came from different, inconsistent sources (a future refactor
    # decoupling them, or corrupted/mismatched input), not a real Goulding
    # month -- catch that loudly during development rather than silently
    # blending a state that couldn't have produced this regime label.
    assert (
        (r == 'correction' and r_fast < 0 <= r_slow)
        or (r == 'rebound' and r_slow < 0 <= r_fast)
    ), (f"regime={regime_val!r} inconsistent with r_fast={r_fast}/r_slow={r_slow} -- "
        "goulding_monthly's own classification requires Correction: fast<0<=slow, "
        "Rebound: slow<0<=fast")
    return (1.0 - weight) * r_slow + weight * r_fast


def _goulding_direction(regime_val: Optional[str], a_co: float, a_re: float,
                         r_fast: Optional[float] = None, r_slow: Optional[float] = None,
                         ) -> Optional[tuple[float, Optional[float]]]:
    """(direction, blend). direction is eq. 7's sign(blend): (1-a_Co)*
    r_SLOW + a_Co*r_FAST in Correction, (1-a_Re)*r_SLOW + a_Re*r_FAST in
    Rebound, sign taken AFTER blending. r_fast/r_slow here are meant to be
    Goulding's own r_FAST/r_SLOW -- the SAME lagged, trailing-average
    momentum signals eq. 4 uses to classify Bull/Bear/Correction/Rebound in
    the first place (this project's own goulding_monthly()'s `fast`/`slow`
    columns: `ret.shift(1).rolling_mean(fast_months/slow_months)`, i.e. the
    mean of the last N COMPLETED months, never including the current/
    still-forming one) -- NOT the realized return of the period about to
    be traded. Passing a same-period realized return instead would be
    genuine look-ahead; this function has no way to detect that misuse
    from the float values alone, so getting the caller's r_fast/r_slow
    right is the caller's responsibility (see
    src/derivatives_bt_engine/strats/tsmom_binary_vol_parity_backtest.py's own g_fast/g_slow, which
    are goulding_monthly's `fast`/`slow` read via a forward-matched
    rebalance-date join -- verified end to end, not merely assumed).

    This is "direction" and not "weight": Goulding's eq. 7 decides which
    way a position points (+1/-1/0), never its size -- see resolve_trend_
    direction's own docstring's "Goulding decides direction, vol-parity
    decides size". This module's own binary sign(signal) direction
    convention (matching Bull/Bear's unconditional +-1, and scripts/
    tsmom_binary_vol_parity_backtest.py's flat_discount mode's direction=
    sign(ts)) then takes sign(blend) as that direction -- always +1/-1/0,
    never a magnitude in between. blend landing on EXACTLY 0.0 (routed to
    the flat 0.0 case below) is astronomically unlikely with real return
    data and isn't divided by anywhere in this function, so it doesn't
    carry the same numerical-stability risk an epsilon-guarded 1/x would;
    left as a plain equality check deliberately, not tightened to an
    abs()-epsilon.

    blend (the raw eq. 7 value BEFORE taking its sign, i.e.
    _goulding_blend's own return value) is surfaced here too -- rather
    than making a caller call _goulding_blend a second time with the same
    args just to get the audit value alongside direction -- always None in
    Bull/Bear (eq. 7 doesn't apply there -- direction is unconditionally
    +-1, nothing to blend), never None whenever direction itself resolves
    in Correction/Rebound (the None-input/invalid-regime cases below
    return the whole tuple as None, not a (None, None) pair, so a caller
    checking direction alone still catches every unresolvable case).

    CORRECTED from an earlier version that computed (1 - 2*a_co)/
    (2*a_re - 1) directly from a_co/a_re alone, with no r_fast/r_slow
    input at all. That formula is only reachable by substituting FIXED
    unit signs for r_slow/r_fast into eq. 7 BEFORE blending (r_slow=+1,
    r_fast=-1 in Correction; r_slow=-1, r_fast=+1 in Rebound) -- i.e. it
    took the sign of each leg first and blended signs, discarding the
    period's real relative fast/slow magnitudes entirely. Two different
    Correction months with the same a_co but very different actual
    r_fast/r_slow values produced the identical direction under the old
    formula; the paper's own eq. 7 blends the real signal values and only
    takes the sign of the RESULT, not of each input beforehand. a_co=
    a_re=0.5 (the uninformed fallback) still makes eq. 7 degenerate to a
    flat 50/50 average of r_fast/r_slow -- no longer unconditionally zero,
    since a genuine (non-degenerate) blended value can still be nonzero --
    but the flat-in-disagreement-states behavior at exactly a_co=a_re=0.5
    no longer holds the way the old, sign-only formula guaranteed it
    would."""
    if not (0.0 <= a_co <= 1.0) or not (0.0 <= a_re <= 1.0):
        # SignalSpec.__post_init__ enforces this range when a caller goes
        # through that dataclass, but this function is public and directly
        # callable on its own (e.g. estimate_mixing_params's result is
        # already clamped before it gets here, but nothing forces a caller
        # to go through that path) -- reject an out-of-range mixing weight
        # loudly rather than silently extrapolating eq. 7 outside its own
        # [0, 1] domain. Checked here too (not just inside _goulding_blend
        # below) so it still fires for Bull/Bear, which return before ever
        # reaching _goulding_blend at all.
        raise ValueError(f"a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range), got a_co={a_co}, a_re={a_re}")
    if regime_val is None:
        return None
    r = regime_val.lower()
    if r == 'bull':
        return 1.0, None
    if r == 'bear':
        return -1.0, None
    blend = _goulding_blend(regime_val, a_co, a_re, r_fast, r_slow)
    if blend is None:
        return None
    direction = 1.0 if blend > 0 else (-1.0 if blend < 0 else 0.0)
    return direction, blend


def goulding_continuous_raw(regime_val: Optional[str], a_co: float, a_re: float,
                             r_fast: Optional[float] = None,
                             r_slow: Optional[float] = None) -> Optional[float]:
    """Raw continuous Goulding forecast before normalization or clipping.

    Bull/Bear use the simple mean of the two agreeing monthly momentum
    horizons. Correction/Rebound use the paper's equation-7 blend, retaining
    magnitude instead of collapsing the result to its sign. This is a pure
    forecast: volatility scaling, portfolio risk budgets, confidence, VIX,
    and integer-contract effects belong downstream.
    """
    if not (0.0 <= a_co <= 1.0) or not (0.0 <= a_re <= 1.0):
        raise ValueError(
            f"a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range), "
            f"got a_co={a_co}, a_re={a_re}"
        )
    if regime_val is None or r_fast is None or r_slow is None:
        return None
    try:
        fast = float(r_fast)
        slow = float(r_slow)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fast) or not math.isfinite(slow):
        return None

    regime = regime_val.lower()
    if regime == 'bull':
        assert fast >= 0 and slow >= 0, (
            f"regime={regime_val!r} inconsistent with r_fast={fast}/r_slow={slow}"
        )
        return (fast + slow) / 2.0
    if regime == 'bear':
        assert fast < 0 and slow < 0, (
            f"regime={regime_val!r} inconsistent with r_fast={fast}/r_slow={slow}"
        )
        return (fast + slow) / 2.0
    return _goulding_blend(regime, a_co, a_re, fast, slow)


def estimate_goulding_forecast_scalar(raw_forecasts, target_abs: float = GOULDING_FORECAST_TARGET_ABS,
                                       min_obs: int = GOULDING_FORECAST_MIN_OBS) -> Optional[float]:
    """Return the scalar making supplied raw forecasts mean-absolute `target_abs`.

    Causality is a caller responsibility: backtest/live callers supply only
    observations strictly prior to the forecast being scaled. Pooling the
    same rule across instruments follows Carver's forecast-scalar convention
    and is more stable than estimating one scalar per instrument.
    """
    if target_abs <= 0:
        raise ValueError("target_abs must be positive")
    if min_obs <= 0:
        raise ValueError("min_obs must be positive")
    finite = []
    for value in raw_forecasts:
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite.append(value)
    if len(finite) < min_obs:
        return None
    mean_abs = sum(abs(value) for value in finite) / len(finite)
    if mean_abs <= _DEGENERATE_EPS:
        return None
    return target_abs / mean_abs


def normalize_goulding_forecast(raw_forecast: Optional[float], scalar: Optional[float],
                                 cap: float = GOULDING_FORECAST_CAP) -> Optional[float]:
    """Scale a raw Goulding forecast and clip it to ``[-cap, cap]``."""
    if cap <= 0:
        raise ValueError("cap must be positive")
    if raw_forecast is None or scalar is None:
        return None
    raw_forecast = float(raw_forecast)
    scalar = float(scalar)
    if not math.isfinite(raw_forecast) or not math.isfinite(scalar) or scalar <= 0:
        return None
    return max(-cap, min(cap, raw_forecast * scalar))


def build_monthly_state_return_history(rebal_monthly: dict[str, pl.DataFrame],
                                        rebal_dates: list[date], cluster_by_symbol: dict[str, str]) -> pl.DataFrame:
    """One row per (symbol, consecutive rebalance-date pair) with the state
    DECIDED at `d` (rebal_monthly[sym] already forward-matches `d` to the
    Goulding bucket for the month starting right after it -- `d` is a
    month-END date, the bucket is labeled by month-START, so a caller
    building rebal_monthly must forward-match, not backward-match) paired
    with THAT SAME bucket's own 'ret' -- goulding_monthly's own simple
    month-end-to-month-end return for the month this state applies to --
    i.e. exactly the (state, subsequent-period return) pairs Appendix C's
    AVG[r|s]/AVG[r^2|s] are computed over. Reads 'ret' directly rather than
    recomputing a return from daily closes -- besides being redundant,
    that would also mix log and simple return conventions (this module
    uses simple returns throughout).

    cluster_by_symbol: caller-supplied {symbol: cluster} map (e.g.
    instruments.get_spec(sym)['cluster']) -- kept as a plain arg here
    rather than importing instruments.py directly, so this signal-
    estimation module doesn't need to know about instrument metadata.

    Two separate date columns, deliberately not collapsed into one:

    - 'date' = `d_next` (the NEXT rebal date after `d`) -- what
      estimate_mixing_params's own `date < as_of` filter uses. Kept at
      `d_next`, not `d`, on purpose: at as_of=`d` itself (this pair's own
      decision date), a row dated `d` would satisfy `d < d`... no, would
      NOT (False, correctly excluded either way) -- but at as_of=`d_next`
      (the very next rebalance), a row dated `d` WOULD satisfy `d <
      d_next` and be included, meaning every single as_of throughout the
      backtest would additionally see "the pair whose return concluded on
      this exact same day" -- fully known by then (no lookahead in the
      sense of using future information), but a materially more
      aggressive reading of "prior history" than this function's own
      docstring intends ("pairs STRICTLY before as_of"). Confirmed
      directly: switching this column to `d` changes the row COUNT visible
      at every as_of (k rows vs k-1 rows, in a sequence of n rebal dates)
      -- not just a relabeling, an actual behavior change, so it stays at
      `d_next` to preserve the original, more conservative estimation
      scope.
    - 'decided_at' = `d` -- audit/display only, never read by
      estimate_mixing_params. Exists purely so a human inspecting this
      table can see "July 31: state=X, return=Y" as a single row instead
      of that same pair only ever appearing under the FOLLOWING
      rebalance's date.

    Pooled across every symbol WITHIN A CLUSTER (not the whole universe,
    and not per-instrument either, unlike the paper's own single-asset
    design) -- a_Co/a_Re is meant to capture how a given asset class
    itself tends to behave in Correction/Rebound, which is exactly what
    pooling across unrelated clusters (e.g. grains together with equity
    index futures) destroys: it estimates one shared number that reflects
    whichever cluster happens to dominate the pooled sample, not any
    cluster's own real behavior. Per-instrument pooling was tried first and
    discarded -- this project's per-symbol history (~15 years) gives too
    few Correction/Rebound months on its own for a stable estimate; pooling
    within a `cluster` (instruments.py's own grain/metal/equity/rates/fx/
    energy grouping) is the middle ground: enough symbols to reach
    min_months, without conflating asset classes that plausibly behave
    differently in the same nominal regime. `cluster` is carried as its own
    column here (not resolved later from `symbol` at estimation time) so
    estimate_mixing_params can filter directly."""
    rows = []
    for sym, rm in rebal_monthly.items():
        cluster = cluster_by_symbol[sym]
        for d, d_next in zip(rebal_dates[:-1], rebal_dates[1:]):
            row = rm.filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            g_regime_val, monthly_return = row['g_regime'][0], row['ret'][0]
            state = g_regime_val.lower() if g_regime_val else None
            if state is not None and monthly_return is not None:
                rows.append({'date': d_next, 'decided_at': d, 'symbol': sym, 'cluster': cluster,
                             'state': state, 'monthly_return': monthly_return})
    if not rows:
        return pl.DataFrame(schema={'date': pl.Date, 'decided_at': pl.Date, 'symbol': pl.Utf8,
                                     'cluster': pl.Utf8, 'state': pl.Utf8, 'monthly_return': pl.Float64})
    return pl.DataFrame(rows)


def estimate_mixing_params(history: pl.DataFrame, as_of: date, cluster: Optional[str],
                            min_months: int = MIN_MONTHS_PER_PHASE) -> tuple[float, float]:
    """Proposition 9 / Appendix C -- a_Co/a_Re from every (state, monthly_return)
    pair strictly before `as_of`, restricted to `cluster` when given
    (expanding window, no lookahead; pooled within one instruments.py
    `cluster` -- grain/metal/equity/rates/fx/energy -- not across the
    whole universe: a_Co/a_Re is supposed to capture how THAT asset class
    behaves in Correction/Rebound, and pooling clusters together would
    estimate one number dominated by whichever cluster has the most
    history, not any of their real behavior). `cluster=None` disables the
    restriction and pools across every symbol in `history` regardless of
    cluster -- a caller's `mixing_pool='global'` option, kept for direct
    comparison against the cluster-scoped default and to reproduce this
    project's original (pre-cluster-split) behavior. Falls back to the
    uninformed (0.5, 0.5) -- equivalent to the flat regime_discount's
    no-op case -- whenever there isn't yet `min_months` of the selected
    pool's own history in EITHER the Correction or Rebound phase, or the
    Bull/Bear baseline D is non-positive, or either phase's mean-squared return
    is degenerate (zero); the paper's own rule for insufficient per-asset history is to
    exclude the asset for that month entirely, which does not map cleanly
    onto a pooled, always-in-the-portfolio backtest, so this is an
    explicit, flagged adaptation, not a literal reproduction. A cluster
    with too few symbols/too little history of its own simply stays at
    (0.5, 0.5) longer (or indefinitely) under cluster-scoped pooling --
    an intentional consequence of not borrowing another cluster's
    behavior, not a bug.

    Eq. 9's sign, as extracted from the scanned paper, appears identical in
    form to eq. 8's (both "1 - ...") -- which contradicts the paper's own
    prose ("if returns tend to be positive after rebounds... a_Re > 0.5")
    given a positive 1/C. Uses the "+" form here
    (a_Re = 1/2*(1 + (1/C)*AVG[r|Re]/AVG[r^2|Re])) as the only version
    self-consistent with that prose -- see
    research/research_trend_strength_crossover_signal.md Part 2 §6 for the
    full errata discussion; this is flagged, not confirmed against the
    primary source's actual typeset sign.

    Thin wrapper around estimate_mixing_params_diagnostics -- see that
    function if you need C/1/C/the per-state avg_r/avg_r2 values/the RAW
    pre-clamp a_co/a_re/which (if any) fallback fired, e.g. to audit why a
    particular cluster's a_co or a_re landed exactly at 0.0 or 1.0 (a
    clamp on this formula's own raw output, not a masking of r_fast/r_slow
    -- this function never reads either)."""
    diag = estimate_mixing_params_diagnostics(history, as_of, cluster, min_months)
    return diag['a_co'], diag['a_re']


def estimate_mixing_params_diagnostics(history: pl.DataFrame, as_of: date, cluster: Optional[str],
                                        min_months: int = MIN_MONTHS_PER_PHASE) -> dict:
    """Every intermediate value behind estimate_mixing_params's (a_co, a_re)
    -- per-state counts/means/mean-squares, the Proposition 9 normalizer C and
    inverse 1/C, the RAW pre-clamp a_co/a_re, and which (if any) fallback path
    fired -- so a cluster's estimate can be audited rather than trusted as
    an opaque pair of floats. estimate_mixing_params is a thin (a_co,
    a_re) = (result['a_co'], result['a_re']) wrapper around this function;
    the two share one implementation, not two. Deliberately NOT logged
    from in here (this is also called once per rebalance-month per
    cluster from the backtest's own monthly mixing-param loop -- see
    tsmom_backtester.py/tsmom_binary_vol_parity_backtest.py -- so logging
    on every call here would flood a multi-year backtest's log; the live
    path's own _mixing_params_for_instruments logs a per-cluster summary
    itself, once per rebalance, from this function's return value).

    'fallback_reason' is None when the full eq. 8-10 computation ran
    (a_co/a_re below came from the formula, possibly clamped -- see
    a_co_raw/a_re_raw), otherwise one of 'no_history' (prior.height == 0,
    no rows at all before as_of/within cluster), 'insufficient_months'
    (fewer than min_months of pooled Correction or Rebound history), or
    'degenerate' (the Bull-or-Bear union is empty or its pooled mean-
    squared return, or a Correction/Rebound mean-squared return, is near
    zero). A non-positive or near-zero D is reported as
    'nonpositive_baseline' because Proposition 9 no longer guarantees a
    maximizer. a_co/a_re are (0.5, 0.5) whenever fallback_reason is set;
    every other field is None/0 unless it was actually computed on the way
    to that fallback (e.g. n_bull/n_bear are still populated even when the fallback fires
    on n_correction/n_rebound).

    a_co_raw/a_re_raw are the UNCLAMPED eq. 8-10 values -- None whenever
    fallback_reason is set, otherwise possibly outside [0, 1] even though
    a_co/a_re (the clamped, actually-used values) never are. A raw value
    landing outside [0, 1] and getting silently pinned to the nearest
    boundary is the actual mechanism behind a_co/a_re == 0.0 or 1.0 in a
    saved report.

    kelly_bull/kelly_bear/kelly_correction/kelly_rebound are each state's
    own avg_r/avg_r2 -- the state-specific Kelly ratios and a_co_raw/a_re_raw
    are built from (mean-over-mean-square, i.e. mean/variance for a
    typically-small monthly mean -- the Kelly-optimal-fraction form, NOT
    mean/std/Sharpe), surfaced here as its own named field rather than
    making a reader manually divide avg_r_state/avg_r2_state. Sign always
    matches avg_r_state's own sign (avg_r2 >= 0 always) -- a HIGHER kelly_
    bear, say, means a MORE POSITIVE average return followed that state
    historically, not a more negative one. None whenever that state's own
    avg_r/avg_r2 is None (zero months in that state) -- populated
    independent of fallback_reason, same as avg_r_bull/etc. above."""
    diag = {
        'cluster': cluster, 'n_bull': 0, 'n_bear': 0, 'n_correction': 0, 'n_rebound': 0,
        'avg_r_bull': None, 'avg_r2_bull': None, 'avg_r_bear': None, 'avg_r2_bear': None,
        'avg_r_correction': None, 'avg_r2_correction': None, 'avg_r_rebound': None, 'avg_r2_rebound': None,
        'kelly_bull': None, 'kelly_bear': None, 'kelly_correction': None, 'kelly_rebound': None,
        'C': None, 'inv_C': None, 'a_co_raw': None, 'a_re_raw': None,
        'a_co': 0.5, 'a_re': 0.5, 'fallback_reason': None,
    }
    prior = history.filter(pl.col('date') < as_of)
    if cluster is not None:
        prior = prior.filter(pl.col('cluster') == cluster)
    if prior.height == 0:
        diag['fallback_reason'] = 'no_history'
        return diag

    def _stats(states: str | tuple[str, ...]) -> tuple[int, Optional[float], Optional[float]]:
        """Return count, mean return, and mean squared return for states."""
        state_values = [states] if isinstance(states, str) else list(states)
        sub = prior.filter(pl.col('state').is_in(state_values))
        if sub.height == 0:
            return 0, None, None
        r = sub['monthly_return']
        avg_r = cast(Optional[float], r.mean())
        avg_r2 = cast(Optional[float], (r * r).mean())
        return (sub.height, float(avg_r) if avg_r is not None else None,
                float(avg_r2) if avg_r2 is not None else None)

    def _kelly(avg_r: Optional[float], avg_r2: Optional[float]) -> Optional[float]:
        """Return the paper's mean-over-mean-square state statistic."""
        return avg_r / avg_r2 if avg_r is not None and avg_r2 else None

    n_bu, avg_r_bu, avg_r2_bu = _stats('bull')
    n_be, avg_r_be, avg_r2_be = _stats('bear')
    n_bu_be, _, avg_r2_bu_be = _stats(('bull', 'bear'))
    n_co, avg_r_co, avg_r2_co = _stats('correction')
    n_re, avg_r_re, avg_r2_re = _stats('rebound')
    k_bu = _kelly(avg_r_bu, avg_r2_bu)
    k_be = _kelly(avg_r_be, avg_r2_be)
    k_co = _kelly(avg_r_co, avg_r2_co)
    k_re = _kelly(avg_r_re, avg_r2_re)
    diag.update(n_bull=n_bu, n_bear=n_be, n_correction=n_co, n_rebound=n_re,
                avg_r_bull=avg_r_bu, avg_r2_bull=avg_r2_bu, avg_r_bear=avg_r_be, avg_r2_bear=avg_r2_be,
                avg_r_correction=avg_r_co, avg_r2_correction=avg_r2_co,
                avg_r_rebound=avg_r_re, avg_r2_rebound=avg_r2_re,
                kelly_bull=k_bu, kelly_bear=k_be,
                kelly_correction=k_co, kelly_rebound=k_re)

    if n_co < min_months or n_re < min_months:
        diag['fallback_reason'] = 'insufficient_months'
        return diag
    n_total = prior.height
    # Exact `== 0` float equality is fragile here -- these are means of
    # squared monthly returns, so a near-degenerate (but not exactly zero)
    # value like 1e-12 would sail past an exact-zero check and then blow
    # up the 1/x below. n_total is prior.height because Proposition 9 uses
    # unconditional state probabilities, not probabilities conditional on
    # being in the Bull/Bear union.
    if (n_bu_be == 0 or avg_r_bu is None or avg_r_be is None or
            avg_r2_bu_be is None or abs(avg_r2_bu_be) < _DEGENERATE_EPS):
        diag['fallback_reason'] = 'degenerate'
        return diag

    # Proposition 9 separates the calculation into a signed Bull/Bear
    # baseline and a Bull-or-Bear second-moment scale. The probabilities
    # below are over every prior state, as in the paper:
    #
    #   D = P(Bu) E[r|Bu] - P(Be) E[r|Be]
    #   C = D / (P(Bu or Be) E[r^2|Bu or Be])
    #
    # Bull contributes its forward return, while Bear is subtracted because
    # the trend strategy is short after Bear. The theorem requires D > 0.
    p_bu = n_bu / n_total
    p_be = n_be / n_total
    p_bu_be = n_bu_be / n_total
    D = p_bu * avg_r_bu - p_be * avg_r_be
    scale_bu_be = p_bu_be * avg_r2_bu_be
    if D <= _DEGENERATE_EPS:
        diag['fallback_reason'] = 'nonpositive_baseline'
        diag['C'] = D / scale_bu_be
        return diag

    # C is the paper's normalizer: the signed Bull/Bear return baseline D
    # divided by the probability-weighted second moment over their union.
    # Its inverse is the scale applied to each phase's Kelly-style ratio
    # K_s = E[r|s]/E[r^2|s].
    C = D / scale_bu_be
    if abs(C) < _DEGENERATE_EPS:
        diag['fallback_reason'] = 'degenerate'
        diag['C'] = C
        return diag
    inv_C = 1.0 / C
    if (avg_r_co is None or avg_r2_co is None or avg_r_re is None or avg_r2_re is None or
            abs(avg_r2_co) < _DEGENERATE_EPS or abs(avg_r2_re) < _DEGENERATE_EPS):
        diag['fallback_reason'] = 'degenerate'
        diag.update(C=C, inv_C=inv_C)
        return diag

    # The raw weights start at 0.5, meaning no preference between slow and
    # fast momentum. (1/C)*K_co and (1/C)*K_re compare phase evidence with
    # the Bull/Bear baseline. Specifically, a_co_raw = 0.5 * (1 - K_co/C)
    # and a_re_raw = 0.5 * (1 + K_re/C). With positive C, positive
    # Correction evidence lowers the fast weight, while positive Rebound
    # evidence raises it. Raw values are retained before clamping to [0, 1].
    a_co_raw = 0.5 * (1 - k_co / C)
    a_re_raw = 0.5 * (1 + k_re / C)
    diag.update(C=C, inv_C=inv_C, a_co_raw=a_co_raw, a_re_raw=a_re_raw,
                a_co=max(0.0, min(1.0, a_co_raw)), a_re=max(0.0, min(1.0, a_re_raw)))
    return diag

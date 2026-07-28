"""
Modular TSMOM feature/signal construction -- three independent, pure
functions, each computed straight from raw OHLCV bars so no model depends
on another model's intermediate columns:

    build_features(df)                 -- shared base features only
    continuous_momentum(df, ...)        -- daily, vol-normalized fast/slow model
    goulding_monthly(df, ...)           -- monthly, un-normalized arithmetic model

Design rationale (2026-07 rewrite, replacing an earlier version of this
module that wrapped tsmom_signal.calculate_trend_strength and dispatched on
a (SignalModel, WindowBasis) pair): that design tangled the monthly
Goulding signal with the continuous model's own intermediate columns and
only let a caller pick ONE model at a time. Here, both models take only
build_features' output (prev_close/peak/dd/r1d) and are run/saved/compared
independently -- see scripts/momentum_signal_comparison.py's --model
continuous/goulding/both for the comparison workflow this enables.

Simple (arithmetic) returns throughout -- close/prev_close - 1, never log
returns -- one convention, no mixing.

Column-naming convention, consistent across both models: fast_window/
slow_window (continuous, trading-day row counts) and fast_months/
slow_months (goulding, calendar months) control the RETURN horizon (the
numerator); vol_fast_window/vol_slow_window (continuous only) control ONLY
the volatility-normalization horizon (the denominator) and default to
fast_window/slow_window -- i.e. horizon-matched by default, never an
arbitrary fixed window reused regardless of what return horizon a caller
configured. annualization_days is a separate, per-instrument units-
conversion factor (avg_r_fast/avg_r_slow/hv_fast/hv_slow only) -- resolve
it from instrument config (instruments.resolve_annualization_days) and
pass it in; it never changes window length. goulding_monthly takes no
annualization_days/vol window at all: it is a pure arithmetic-average
momentum signal with NO volatility normalization anywhere in it, by design
(contrast continuous_momentum's ts_fast/ts_slow, which are vol-scaled).

SignalSpec bundles one consistent set of parameter names for both models
(a single object standing in for "the current config" -- the paper-
replication defaults via SignalSpec.goulding(), or ad hoc values to sweep
different horizons without touching either function's own code) --
continuous_kwargs()/goulding_kwargs() unpack the relevant subset for each
function's call, e.g. continuous_momentum(df, **spec.continuous_kwargs()).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.tsmom_signal import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
)

# ── Tunable defaults ─────────────────────────────────────────────────────
# Goulding et al.'s own horizons (calendar months) -- distinct from this
# project's canonical 3m/12m trading-day windows (DEFAULT_FAST_WINDOW/
# DEFAULT_SLOW_WINDOW, tsmom_signal.py), which is why SignalSpec carries
# separate *_months fields rather than reusing fast_window/slow_window.
GOULDING_FAST_MONTHS = 2
GOULDING_SLOW_MONTHS = 12


@dataclass
class SignalSpec:
    """Strategy/test-level signal configuration -- deliberately separate
    from instrument metadata (instruments.py's INSTRUMENTS dict): this
    describes HOW to compute a signal from a price series, not WHICH
    instrument it's for. annualization_days is the one exception carried
    here as a plain field rather than looked up internally -- the caller
    resolves it per-instrument (instruments.resolve_annualization_days) and
    passes the result in, keeping this dataclass itself instrument-
    agnostic and easily reusable/comparable across a parameter sweep.

    Defaults reproduce this project's original, long-standing continuous-
    model behavior (63/252-day windows, 0.4/0.6 weights, 0.5 discount,
    252-day annualization); use the goulding() factory below for Goulding
    et al.'s own 2/12-month parameterization instead of setting
    fast_months/slow_months by hand."""
    fast_window: int = DEFAULT_FAST_WINDOW
    slow_window: int = DEFAULT_SLOW_WINDOW
    vol_fast_window: Optional[int] = None  # None -> fast_window (horizon-matched)
    vol_slow_window: Optional[int] = None  # None -> slow_window (horizon-matched)
    annualization_days: int = DEFAULT_ANNUALIZATION_DAYS
    w_fast: float = 0.4
    w_slow: float = 0.6
    discount: float = 0.5

    fast_months: int = GOULDING_FAST_MONTHS
    slow_months: int = GOULDING_SLOW_MONTHS

    # Goulding's eq. 7 dynamic-reweight inputs -- plain pass-through values
    # for _goulding_weight(), NOT estimated in this module. Pooled,
    # expanding-window a_Co/a_Re ESTIMATION (eq. 8-10) is a multi-symbol
    # process that doesn't fit this module's per-symbol, stateless
    # functions -- see scripts/tsmom_binary_vol_parity_backtest.py's
    # _estimate_mixing_params, which already implements it and passes the
    # result into _goulding_weight directly. 0.5/0.5 is the paper's own
    # uninformed fallback and collapses eq. 7 to a flat, no-op reweight.
    a_co: float = 0.5
    a_re: float = 0.5

    def __post_init__(self):
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window/slow_window must be positive")
        if self.fast_window >= self.slow_window:
            # Not mathematically required by continuous_momentum itself,
            # but "fast" >= "slow" contradicts the field names and is
            # almost certainly a caller error (e.g. args swapped) rather
            # than an intentional config -- reject loudly instead of
            # silently computing a "fast" trend slower than its own "slow"
            # counterpart.
            raise ValueError(f"fast_window ({self.fast_window}) must be < slow_window ({self.slow_window})")
        if self.vol_fast_window is not None and self.vol_fast_window <= 0:
            raise ValueError("vol_fast_window must be positive when set")
        if self.vol_slow_window is not None and self.vol_slow_window <= 0:
            raise ValueError("vol_slow_window must be positive when set")
        if self.fast_months <= 0 or self.slow_months <= 0:
            raise ValueError("fast_months/slow_months must be positive")
        if self.fast_months >= self.slow_months:
            raise ValueError(f"fast_months ({self.fast_months}) must be < slow_months ({self.slow_months})")
        if self.w_fast + self.w_slow <= 0:
            # continuous_momentum's ts denominator clips at 1e-12 so this
            # wouldn't crash -- it would silently degenerate to ts=tanh(0)=0
            # every row instead. Catch the misconfiguration loudly instead.
            raise ValueError("w_fast + w_slow must be positive")
        if not (0.0 <= self.a_co <= 1.0) or not (0.0 <= self.a_re <= 1.0):
            raise ValueError("a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range)")

    @staticmethod
    def goulding(fast_months: int = GOULDING_FAST_MONTHS, slow_months: int = GOULDING_SLOW_MONTHS,
                 a_co: float = 0.5, a_re: float = 0.5) -> "SignalSpec":
        """Convenience factory for Goulding et al.'s own parameterization
        (2-month fast / 12-month slow, genuine calendar months via
        goulding_monthly's group_by_dynamic -- not a trading-day
        approximation)."""
        return SignalSpec(fast_months=fast_months, slow_months=slow_months, a_co=a_co, a_re=a_re)

    def continuous_kwargs(self) -> dict:
        """Unpack the subset of fields continuous_momentum() takes."""
        return dict(
            fast_window=self.fast_window, slow_window=self.slow_window,
            vol_fast_window=self.vol_fast_window, vol_slow_window=self.vol_slow_window,
            annualization_days=self.annualization_days,
            w_fast=self.w_fast, w_slow=self.w_slow, discount=self.discount,
        )

    def goulding_kwargs(self) -> dict:
        """Unpack the subset of fields goulding_monthly() takes."""
        return dict(fast_months=self.fast_months, slow_months=self.slow_months)


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    """Shared base features only -- computed once from raw OHLCV bars
    (needs 'ts_event', 'close'), so continuous_momentum/goulding_monthly
    can each derive their own signal independently from this same starting
    point without depending on each other's intermediate columns. No
    model-specific features (no fast/slow windows, no vol normalization)
    belong here.

    r1d is a SIMPLE daily return (close/prev_close - 1), not a log return
    -- this module uses simple returns throughout, never log returns.

    Sorts by ts_event first -- shift()/cum_max() are order-dependent, and
    every downstream function (continuous_momentum, goulding_monthly)
    inherits whatever order this leaves the frame in, so this is the one
    place that guarantee needs to be established."""
    df = df.sort('ts_event')
    df = df.with_columns(
        prev_close=pl.col('close').shift(1),
        peak=pl.col('close').cum_max(),
    )
    df = df.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d=pl.col('close') / pl.col('prev_close') - 1,
    )
    return df


def continuous_momentum(df: pl.DataFrame, fast_window: int = DEFAULT_FAST_WINDOW,
                         slow_window: int = DEFAULT_SLOW_WINDOW,
                         vol_fast_window: Optional[int] = None, vol_slow_window: Optional[int] = None,
                         annualization_days: int = DEFAULT_ANNUALIZATION_DAYS,
                         w_fast: float = 0.4, w_slow: float = 0.6, discount: float = 0.5) -> pl.DataFrame:
    """Continuous, daily, volatility-normalized fast/slow trend-strength
    model -- independent of goulding_monthly; takes only build_features'
    output (prev_close/dd/r1d), no shared intermediate state between
    models.

    fast_window/slow_window control the return horizon (the numerator):
        r_fast = close / close.shift(fast_window) - 1
        r_slow = close / close.shift(slow_window) - 1
    vol_fast_window/vol_slow_window control ONLY the volatility-
    normalization horizon (the denominator) and default to fast_window/
    slow_window -- i.e. each leg's vol estimate is horizon-matched to its
    own return by default, not an arbitrary fixed window (e.g. always 63
    days) reused for both regardless of what fast_window/slow_window a
    caller passed. Pass them explicitly only to deliberately decouple the
    two (e.g. testing a fixed-vol-window variant).

    ts_fast/ts_slow are horizon Sharpe-like statistics -- an n-day return
    divided by that SAME n-day horizon's own estimated return std
    (daily_std * sqrt(n)) -- NOT annualized Sharpe ratios; nothing here
    scales them by annualization_days.

    annualization_days is a separate, per-instrument units-conversion
    factor for the genuinely per-calendar-year REPORTING diagnostics only
    (avg_r_fast/avg_r_slow/hv_fast/hv_slow) -- resolve it from instrument
    config (instruments.resolve_annualization_days), don't hardcode it. It
    never changes window length and never touches ts_fast/ts_slow/ts/signal."""
    # Explicit None-check, not `vol_fast_window or fast_window` -- the
    # truthiness idiom would also replace an explicitly-passed 0/False
    # with the default, silently masking exactly the misconfiguration
    # SignalSpec.__post_init__ now rejects (a caller bypassing SignalSpec
    # and calling this function directly wouldn't get that guard).
    if vol_fast_window is None:
        vol_fast_window = fast_window
    if vol_slow_window is None:
        vol_slow_window = slow_window

    df = df.with_columns(
        r_fast=pl.col('close') / pl.col('close').shift(fast_window) - 1,
        r_slow=pl.col('close') / pl.col('close').shift(slow_window) - 1,
    )
    df = df.with_columns(
        avg_r_fast=pl.col('r1d').rolling_mean(fast_window) * annualization_days,
        avg_r_slow=pl.col('r1d').rolling_mean(slow_window) * annualization_days,
        std_fast=pl.col('r1d').rolling_std(vol_fast_window),
        std_slow=pl.col('r1d').rolling_std(vol_slow_window),
    )
    df = df.with_columns(
        hv_fast=pl.col('std_fast') * annualization_days ** 0.5,
        hv_slow=pl.col('std_slow') * annualization_days ** 0.5,
        # Guarded against std_fast/std_slow == 0 (a genuinely constant
        # price over the whole window -- rare for real futures, but not
        # impossible) explicitly, rather than dividing straight through:
        # an unguarded division produces +-inf there instead of NaN
        # (0/0 -> NaN, but any nonzero r_fast/0 -> inf), and
        # tanh(inf) == 1.0 -- ts's own tanh squash below would silently
        # read that as a genuine maximum-strength trend rather than an
        # undefined signal. Null (matching every other "undefined here"
        # case in this column, e.g. the pre-warmup nulls std_fast/std_slow
        # already carry) is the correct value instead.
        ts_fast=pl.when(pl.col('std_fast') > 0)
                  .then(pl.col('r_fast') / (pl.col('std_fast') * math.sqrt(fast_window)))
                  .otherwise(None),
        ts_slow=pl.when(pl.col('std_slow') > 0)
                  .then(pl.col('r_slow') / (pl.col('std_slow') * math.sqrt(slow_window)))
                  .otherwise(None),
    )
    df = df.with_columns(
        _w_fast=pl.col('ts_fast').is_not_null().cast(pl.Float64) * w_fast,
        _w_slow=pl.col('ts_slow').is_not_null().cast(pl.Float64) * w_slow,
    )
    df = df.with_columns(
        ts=(
            pl.when(pl.col('ts_slow').is_not_null())
            .then(
                ((pl.col('_w_fast') * pl.col('ts_fast').fill_null(0) +
                  pl.col('_w_slow') * pl.col('ts_slow').fill_null(0))
                 / (pl.col('_w_fast') + pl.col('_w_slow')).clip(lower_bound=1e-12)).tanh()
            )
            .otherwise(None)
        ),
        regime=(
            pl.when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') < 0)).then(pl.lit('bear'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') >= 0)).then(pl.lit('bull'))
            .when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') >= 0)).then(pl.lit('correction'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') < 0)).then(pl.lit('rebound'))
        ),
    )
    df = df.with_columns(
        signal=(
            pl.when(pl.col('regime').is_in(['correction', 'rebound']))
            .then(pl.col('ts') * discount)
            .otherwise(pl.col('ts'))
        )
    )
    return df.drop(['_w_fast', '_w_slow'], strict=False)


def goulding_monthly(df: pl.DataFrame, fast_months: int = GOULDING_FAST_MONTHS,
                      slow_months: int = GOULDING_SLOW_MONTHS) -> pl.DataFrame:
    """Goulding, Harvey & Mazzoleni's own monthly momentum construction --
    independent of continuous_momentum; takes only build_features' output
    and uses only 'ts_event'/'close' from it (ignores r1d/dd/prev_close
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


def _goulding_weight(regime_val: Optional[str], a_co: float, a_re: float,
                      r_fast: Optional[float] = None, r_slow: Optional[float] = None) -> Optional[float]:
    """Eq. 7's blend: (1-a_Co)*r_SLOW + a_Co*r_FAST in Correction,
    (1-a_Re)*r_SLOW + a_Re*r_FAST in Rebound -- blending the period's
    ACTUAL fast/slow return VALUES, not their signs. This module's own
    binary sign(signal) direction convention (matching Bull/Bear's
    unconditional +-1, and scripts/tsmom_binary_vol_parity_backtest.py's
    flat_discount mode's direction=sign(ts)) then takes sign(r_dyn) as the
    position weight -- always +1/-1/0, never a magnitude in between.

    CORRECTED from an earlier version that computed (1 - 2*a_co)/
    (2*a_re - 1) directly from a_co/a_re alone, with no r_fast/r_slow
    input at all. That formula is only reachable by substituting FIXED
    unit signs for r_slow/r_fast into eq. 7 BEFORE blending (r_slow=+1,
    r_fast=-1 in Correction; r_slow=-1, r_fast=+1 in Rebound) -- i.e. it
    took the sign of each leg first and blended signs, discarding the
    period's real relative fast/slow magnitudes entirely. Two different
    Correction months with the same a_co but very different actual
    r_fast/r_slow values produced the identical weight under the old
    formula; the paper's own eq. 7 blends the real return values and only
    takes the sign of the RESULT, not of each input beforehand. a_co=
    a_re=0.5 (the uninformed fallback) still makes eq. 7 degenerate to a
    flat 50/50 average of r_fast/r_slow -- no longer unconditionally zero,
    since a genuine (non-degenerate) blended return can still be
    nonzero -- but the flat-in-disagreement-states behavior at exactly
    a_co=a_re=0.5 no longer holds the way the old, sign-only formula
    guaranteed it would."""
    if regime_val is None:
        return None
    r = regime_val.lower()
    if r == 'bull':
        return 1.0
    if r == 'bear':
        return -1.0
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
    r_dyn = (1.0 - weight) * r_slow + weight * r_fast
    return 1.0 if r_dyn > 0 else (-1.0 if r_dyn < 0 else 0.0)

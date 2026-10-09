"""Daily continuous time-series momentum signal construction.

This module owns the source-neutral feature frame and the daily fast/slow
volatility-normalized momentum model.  It contains no monthly Goulding,
forecast-selection, portfolio-allocation, or execution logic.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

import polars as pl

DEFAULT_ANNUALIZATION_DAYS = 252
DEFAULT_FAST_WINDOW = 63
DEFAULT_SLOW_WINDOW = 252


class TrendRegime(str, Enum):
    """Sign agreement between the fast and slow momentum forecasts."""

    BULL = "bull"
    CORRECTION = "correction"
    BEAR = "bear"
    REBOUND = "rebound"
    UNKNOWN = "unknown"


def classify_regime(fast, slow) -> TrendRegime:
    """
    Classify into Bull/Correction/Bear/Rebound from the sign of the fast
    (~3mo, or whatever fast_window/fast_months a caller configured) and slow
    (~12mo) trend-strength scores.

        slow  fast  state        meaning
         +     +    Bull         strong trend, high-confidence long
         +     -    Correction   short-term dip in uptrend (61% revert to Bull)
         -     -    Bear         strong downtrend, high-confidence short/flat
         -     +    Rebound      short-term recovery in downtrend (55% up next)

    Exactly zero, None, or NaN on either input is ambiguous -> Unknown.
    """
    if fast is None or slow is None:
        return TrendRegime.UNKNOWN
    if (isinstance(fast, float) and math.isnan(fast)) or (isinstance(slow, float) and math.isnan(slow)):
        return TrendRegime.UNKNOWN
    if fast == 0 or slow == 0:
        return TrendRegime.UNKNOWN

    slow_up = slow > 0
    fast_up = fast > 0

    if slow_up and fast_up:
        return TrendRegime.BULL
    if slow_up and not fast_up:
        return TrendRegime.CORRECTION
    if not slow_up and not fast_up:
        return TrendRegime.BEAR
    return TrendRegime.REBOUND   # not slow_up and fast_up


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    """Build the shared drawdown and validated daily-return features.

    Source-neutral futures bars already carry ``ret_1d`` constructed from
    matched-contract price changes.  Preserve that column verbatim: deriving
    it again from ``close`` after an invalid session has been filtered can
    span the removed date and silently reintroduce a return that the history
    layer rejected.  Legacy/raw OHLCV callers have no ``ret_1d``, so only
    those callers fall back to the simple return ``close.pct_change()``.

    ``ret_1d`` is always a SIMPLE daily return, not a log return.  The old,
    retired ``legacy_signal.calculate_trend_strength`` function retains its
    separate ``r1d`` log-return convention.

    Sorts by ts_event first -- shift()/cum_max() are order-dependent, and
    every downstream function (continuous_momentum, the monthly Goulding model)
    inherits whatever order this leaves the frame in, so this is the one
    place that guarantee needs to be established."""
    df = df.sort('ts_event')
    return_expression = (
        pl.col('ret_1d')
        if 'ret_1d' in df.columns
        else pl.col('close').pct_change()
    )
    df = df.with_columns(
        peak=pl.col('close').cum_max(),
        ret_1d=return_expression,
    )
    df = df.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
    )
    return df


def continuous_momentum(df: pl.DataFrame, fast_window: int = DEFAULT_FAST_WINDOW,
                         slow_window: int = DEFAULT_SLOW_WINDOW,
                         vol_fast_window: Optional[int] = None, vol_slow_window: Optional[int] = None,
                         annualization_days: int = DEFAULT_ANNUALIZATION_DAYS,
                         w_fast: float = 0.4, w_slow: float = 0.6,
                         discount: float = 0.5) -> pl.DataFrame:
    """Continuous, daily, volatility-normalized fast/slow trend-strength
    model -- independent of the monthly Goulding model; takes only build_features'
    output (peak/dd/ret_1d), no shared intermediate state between
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
    scales them by annualization_days. std_fast/std_slow (their
    denominator) stay a plain, equal-weighted rolling_std deliberately
    horizon-matched to fast_window/slow_window -- see this project's own
    design discussion on why an EWM estimate would break that clean
    n-day-return-over-n-day-vol correspondence. avg_r_fast/avg_r_slow are
    reporting diagnostics only and never touch ts_fast/ts_slow/ts/signal.

    avg_r_fast/avg_r_slow: EXPONENTIALLY-weighted mean daily return
    (ret_1d.ewm_mean(half_life=fast_window/slow_window)), annualized --
    intentionally NOT the same equal-weighted rolling_mean convention
    std_fast/std_slow use; this is a pure reporting figure with no
    horizon-matching constraint to preserve, so EWM's smoother, more
    recency-weighted average is preferred here.

    annualization_days is a separate, per-instrument units-conversion
    factor for the genuinely per-calendar-year REPORTING diagnostics only
    (avg_r_fast/avg_r_slow/hv_fast/hv_slow) -- resolve it from instrument
    config (instruments.resolve_annualization_days), don't hardcode it. It
    never changes window length and never touches ts_fast/ts_slow/ts/signal."""
    # Explicit None-check, not `vol_fast_window or fast_window` -- the
    # truthiness idiom would also replace an explicitly-passed 0/False
    # with the default, silently masking exactly the misconfiguration
    # configuration validation rejects (a caller bypassing SignalSpec
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
        avg_r_fast=pl.col('ret_1d').ewm_mean(half_life=fast_window) * annualization_days,
        avg_r_slow=pl.col('ret_1d').ewm_mean(half_life=slow_window) * annualization_days,
        std_fast=pl.col('ret_1d').rolling_std(vol_fast_window),
        std_slow=pl.col('ret_1d').rolling_std(vol_slow_window),
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
        # A horizon contributes only when its score is available.  The
        # composite below intentionally remains null until the slow score has
        # completed its warm-up; after that point these availability weights
        # prevent a temporarily missing leg from diluting the other one.
        _w_fast=pl.col('ts_fast').is_not_null().cast(pl.Float64) * w_fast,
        _w_slow=pl.col('ts_slow').is_not_null().cast(pl.Float64) * w_slow,
    )
    df = df.with_columns(
        # ``ts`` is a bounded, unitless composite forecast.  tanh preserves
        # the sign of the weighted horizon score while preventing an extreme
        # volatility-normalized return from creating unbounded conviction.
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
        # The public ``signal`` is the composite forecast actually passed to
        # sizing.  Agreement regimes retain ``ts``; disagreement regimes are
        # deliberately reduced by ``discount``.  No volatility targeting,
        # capital allocation, or contract rounding occurs in this function.
        signal=(
            pl.when(pl.col('regime').is_in(['correction', 'rebound']))
            .then(pl.col('ts') * discount)
            .otherwise(pl.col('ts'))
        )
    )
    return df.drop(['_w_fast', '_w_slow'], strict=False)

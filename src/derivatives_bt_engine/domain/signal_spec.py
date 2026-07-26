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
        if self.fast_months <= 0 or self.slow_months <= 0:
            raise ValueError("fast_months/slow_months must be positive")
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
    -- this module uses simple returns throughout, never log returns."""
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

    annualization_days is a separate, per-instrument units-conversion
    factor (avg_r_fast/avg_r_slow/hv_fast/hv_slow only) -- resolve it from
    instrument config (instruments.resolve_annualization_days), don't
    hardcode it. It never changes window length."""
    vol_fast_window = vol_fast_window or fast_window
    vol_slow_window = vol_slow_window or slow_window

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
        ts_fast=pl.col('r_fast') / (pl.col('std_fast') * math.sqrt(fast_window)),
        ts_slow=pl.col('r_slow') / (pl.col('std_slow') * math.sqrt(slow_window)),
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

    Monthly return is a SIMPLE return between month-start and month-end
    close (p2/p1 - 1), aggregated via group_by_dynamic('1mo'). fast/slow
    are the trailing mean of the last fast_months/slow_months COMPLETED
    months -- shift(1) before rolling_mean, so month m's own (still-
    forming) return never leaks into its own signal; no lookahead."""
    monthly = df.group_by_dynamic('ts_event', every='1mo', closed='left').agg(
        pl.col('close').first().alias('p1'),
        pl.col('close').last().alias('p2'),
    )
    monthly = monthly.with_columns(
        ret=pl.col('p2') / pl.col('p1') - 1,
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


def _goulding_weight(regime_val: Optional[str], a_co: float, a_re: float) -> Optional[float]:
    """Eq. 7's blend, expressed as a position weight (equivalent to a
    return-blend for a single directional bet -- see research doc Part 2
    §6b): +1 Bull, -1 Bear, (1 - 2*a_co) Correction, (2*a_re - 1) Rebound.
    a_co=a_re=0.5 (the paper's own uninformed fallback) collapses this to
    exactly 0 -- fully flat -- in both disagreement states, which is the
    literature-backed reason 0.5/0.5 is this module's own default rather
    than an arbitrary placeholder."""
    if regime_val is None:
        return None
    r = regime_val.lower()
    if r == 'bull':
        return 1.0
    if r == 'bear':
        return -1.0
    if r == 'correction':
        return 1.0 - 2.0 * a_co
    if r == 'rebound':
        return 2.0 * a_re - 1.0
    return None

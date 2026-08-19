"""
TSMOM (time-series momentum) signal computation and estimation -- no IB
dependency. Consolidated (2026-07) from three previously separate modules
that had grown awkwardly split across similarly-named files:

    tsmom_signal.py  -- the old calculate_trend_strength() plus position-
                         sizing scalar math (compute_position_scalar) and
                         signal-confidence helpers
    signal_spec.py   -- the newer, canonical build_features/
                         continuous_momentum/goulding_monthly signal
                         construction (SignalSpec)
    (script-local)   -- Goulding et al.'s pooled, expanding-window a_Co/a_Re
                         ESTIMATION (build_monthly_state_return_history/
                         estimate_mixing_params), previously kept out of
                         signal_spec.py on purpose (a caller was expected to
                         estimate it separately and pass the result in) but
                         moved here once a second caller
                         (tsmom_backtester.py) needed the same estimation
                         logic tsmom_binary_vol_parity_backtest.py already
                         had -- one canonical implementation, not two.

Three signal-construction functions, each computed straight from raw OHLCV
bars so no model depends on another model's intermediate columns:

    build_features(df)                 -- shared base features only
    continuous_momentum(df, ...)        -- daily, vol-normalized fast/slow model
    goulding_monthly(df, ...)           -- monthly, un-normalized arithmetic model

Design rationale (2026-07 rewrite of the signal_spec.py half, replacing an
even earlier version that wrapped calculate_trend_strength and dispatched
on a (SignalModel, WindowBasis) pair): that design tangled the monthly
Goulding signal with the continuous model's own intermediate columns and
only let a caller pick ONE model at a time. Here, both models take only
build_features' output (peak/dd/r1d) and are run/saved/compared
independently -- see scripts/momentum_signal_comparison.py's --model
continuous/goulding/both for the comparison workflow this enables.

Simple (arithmetic) returns throughout -- close.pct_change(), never log
returns -- one convention, no mixing. (calculate_trend_strength, the older
function below, is the one exception -- it predates this convention and
uses log returns; kept as-is, not retrofitted, since it's already marked
retired.)

Column-naming convention, consistent across both newer models: fast_window/
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

Naming convention (applies wherever "fast"/"slow" columns are consumed,
e.g. Backtester.calculate_futures_mtm_drawdown's hv_fast/sharpe_fast):
"fast"/"slow" denote the rolling estimation window (fast_window/
slow_window, whatever those are configured to), not a fixed reporting
horizon.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.enums import SignalConfidenceRegime, TrendRegime

log = logging.getLogger(__name__)

# ── Tunable defaults ─────────────────────────────────────────────────────
# Fallback trading-days/year for callers that don't pass their own
# instrument-specific figure (see instruments.resolve_annualization_days) --
# kept as a plain local default (not imported from instruments.py) so this
# module stays usable standalone, per its own "no IB dependency" framing.
DEFAULT_ANNUALIZATION_DAYS = 252
# ts_fast/ts_slow's window lengths, in trading days -- "do not revisit":
#   - 3-month (63 bars) and 12-month (252 bars) only.
#   - 6-month is excluded: sits in the dead zone between the autocorrelation
#     and drift regimes, and is highly correlated with the 12-month signal —
#     low diversification value for the extra column.
#   - 1-month is excluded for monthly rebalancing: a single rebalance-period
#     observation is too noisy to trust; 3-month is the minimum reliable fast
#     signal at this cadence.
# Exposed as parameters (not hardcoded) purely so a caller can experiment/
# compare, not because either default is expected to change.
DEFAULT_FAST_WINDOW = 63
DEFAULT_SLOW_WINDOW = 252
# Goulding et al.'s own horizons (calendar months) -- distinct from
# DEFAULT_FAST_WINDOW/DEFAULT_SLOW_WINDOW's trading-day windows above, which
# is why SignalSpec carries separate *_months fields rather than reusing
# fast_window/slow_window.
GOULDING_FAST_MONTHS = 2
GOULDING_SLOW_MONTHS = 12
# continuous_momentum's MACD signal-line smoothing -- deliberately its own
# small, fixed halflife, NOT derived from fast_window/slow_window the way
# the MACD line itself is (see continuous_momentum's own docstring): the
# signal line's job is to be a fast-reacting smoother of the MACD line's
# own crossovers, not another multi-month trend estimate.
DEFAULT_MACD_SIGNAL_HALFLIFE = 10.0
# Paper's own warm-up requirement per Appendix C -- estimate_mixing_params
# falls back to the uninformed (0.5, 0.5) below this many months of pooled
# Correction/Rebound history.
MIN_MONTHS_PER_PHASE = 12
# Threshold below which a denominator in estimate_mixing_params' eq. 8-10
# arithmetic is treated as degenerate (fall back to (0.5, 0.5) rather than
# let 1/x explode) -- an exact `== 0` float comparison would let a
# near-zero-but-technically-nonzero mean-squared-return (e.g. 1e-12, from a
# run of near-identical monthly returns) sail through undetected.
_DEGENERATE_EPS = 1e-10


def calculate_trend_strength(contract, w3m=0.4, w1y=0.6, discount=0.5,
                              annualization_days=DEFAULT_ANNUALIZATION_DAYS,
                              fast_window=DEFAULT_FAST_WINDOW, slow_window=DEFAULT_SLOW_WINDOW):
    """OLD (now retired) canonical signal function, kept for
    backward-compatible callers/tests only -- continuous_momentum below is
    the current canonical continuous model. Uses log returns (this
    module's newer functions all use simple returns instead -- no mixing
    within a single function, but the two conventions differ across old
    vs. new here).

    See this module's own docstring for the annualization_days vs.
    fast_window/slow_window distinction -- annualization_days scales only
    the genuinely per-calendar-year terms (hv, avg_r_fast, avg_r_slow);
    fast_window/slow_window govern fast_return/slow_return/avg_fast/avg_slow/
    daily_std's rolling windows AND ts_fast/ts_slow's own same-horizon
    vol-scaling, independently of annualization_days. All three default to
    this project's long-standing values (252, 63, 252) -- passing nothing
    reproduces prior behavior exactly.

    Column names (fast/slow, not the old fixed-horizon 3m/1y labels) reflect
    fast_window/slow_window being genuinely configurable -- a caller passing
    fast_window=21 gets a column named ts_fast, not a column still called
    ts3m that's secretly a 21-day figure."""
    df = contract
    df = df.with_columns(
        log_price = pl.col('close').log(),
        peak      = pl.col('close').cum_max(),
    )
    df = df.with_columns(
        dd        = ((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d       = pl.col('log_price').diff(1),
        avg_fast  = pl.col('close').rolling_mean(fast_window).round(2),
        avg_slow  = pl.col('close').rolling_mean(slow_window).round(2),

        fast_return = pl.col('log_price').diff(fast_window),
        slow_return = pl.col('log_price').diff(slow_window),
    )

    df = df.with_columns(
        avg_r_fast = (pl.col('r1d').rolling_mean(fast_window) * annualization_days).round(2),
        avg_r_slow = (pl.col('r1d').rolling_mean(slow_window) * annualization_days).round(2),

        daily_std = pl.col('r1d').rolling_std(fast_window)
    )

    df = df.with_columns(
        hv = pl.col('daily_std') * annualization_days ** 0.5,
        ts_fast = pl.col('fast_return') / (pl.col('daily_std') * math.sqrt(fast_window)),
        ts_slow = pl.col('slow_return') / (pl.col('daily_std') * math.sqrt(slow_window)),
    )

    df = df.with_columns(
        w3 = pl.col('ts_fast').is_not_null().cast(pl.Float64) * w3m,
        w1 = pl.col('ts_slow').is_not_null().cast(pl.Float64) * w1y,
    )

    df = df.with_columns(
        ts = (
            pl.when(pl.col('ts_slow').is_not_null())
            .then(
                ((
                    pl.col('w3') * pl.col('ts_fast').fill_null(0) +
                    pl.col('w1') * pl.col('ts_slow').fill_null(0)
                ) / (pl.col('w3') + pl.col('w1')).clip(lower_bound=1e-12)).tanh()
            )
            .otherwise(None)
        ),
        regime = (
            pl.when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') < 0))
            .then(pl.lit('bear'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') >= 0))
            .then(pl.lit('bull'))
            .when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') >= 0))
            .then(pl.lit('correction'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') < 0))
            .then(pl.lit('rebound'))
        ),
        mom = (pl.col('ts_fast') - pl.col('ts_slow')).tanh().round(2)

    )
    df = df.with_columns(
            signal = (
                pl.when(pl.col('regime').is_in(['correction', 'rebound']))
                .then(pl.col('ts') * discount)
                .otherwise(pl.col('ts'))
                )
        )

    df = df.drop(['open', 'high', 'low', 'log_price',
                   'volume', 'average', 'w3', 'w1'], strict=False)

    # Round only the bounded/display-scale columns (tanh scores, price
    # averages, drawdown pct) to 2dp. r1d/daily_std/fast_return/slow_return/
    # hv are return-scale (typically well under 0.01 for a quiet instrument
    # -- a rates future like MTN, a quiet FX pair like BRE) and MUST stay
    # full precision: a blanket round(2) here used to floor them to exactly
    # 0.0, which propagated downstream into a silently-zeroed hv_fast
    # (Backtester.calculate_futures_mtm_drawdown) and, worse, into
    # tsmom_backtester._compute_target/live.tsmom_rebalance._compute_signal
    # treating `daily_std_last == 0.0` as falsy and silently defaulting
    # risk_scalar to 1.0 (vol-targeting disabled) instead of the
    # up-scaled size a genuinely low-vol instrument should get.
    _DISPLAY_SCALE_COLS = ['dd', 'avg_fast', 'avg_slow', 'ts_fast', 'ts_slow', 'ts', 'mom', 'signal']
    df = df.with_columns([pl.col(c).round(2) for c in _DISPLAY_SCALE_COLS if c in df.columns])
    return df


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


def compute_vol_ratio(df: pl.DataFrame, short_window: int = 21, long_window: int = 252) -> pl.DataFrame:
    """
    Per-instrument, asset-specific vol-regime ratio: this instrument's own
    short-window realized vol / long-window realized vol of its daily log
    returns (short_window ~= 1 trading month, long_window ~= 1 trading
    year). NOT VIX/VX-driven -- this is what catches an instrument-
    specific vol spike (a corn-harvest shock, a JPY intervention) with
    broad-market VX/VIX staying calm, since VX/VIX only reflects S&P-
    linked vol and has no visibility into corn's or JPY's own vol at all.

    Deliberately a separate function from calculate_trend_strength (which
    stays canonical/finalized, not to be touched) rather than adding
    columns there -- callers chain this on demand instead of paying for it
    on every signal computation. Annualization factors cancel in the
    ratio, so this works directly off raw rolling std of daily log returns.

    Expects a DataFrame that already has an 'r1d' column (daily log-return
    diff) -- i.e. chain this onto calculate_trend_strength's output, which
    retains 'r1d'/'log_price' for exactly this purpose, rather than on raw
    bars directly. Returns the same frame plus 'hv_short', 'hv_long',
    'vol_ratio' columns (vol_ratio is None/null wherever hv_long isn't yet
    defined or is zero), with 'log_price'/'r1d' dropped again afterward.
    """
    df = df.with_columns(
        hv_short=pl.col('r1d').rolling_std(short_window),
        hv_long=pl.col('r1d').rolling_std(long_window),
    )
    df = df.with_columns(
        vol_ratio=pl.when(pl.col('hv_long') > 0)
        .then(pl.col('hv_short') / pl.col('hv_long'))
        .otherwise(None)
    )
    return df.drop(['log_price', 'r1d'], strict=False)


def classify_signal_confidence(vol_ratio, low_threshold: float, high_threshold: float) -> SignalConfidenceRegime:
    """
    Low | Normal | High from vol_ratio (hv_short/hv_long, see
    compute_vol_ratio) against configurable thresholds -- deliberately not
    hardcoded, since the right threshold is asset- and regime-dependent
    and there's no settled, universal value.

    None/NaN (insufficient history) -> Normal, i.e. no discount -- a
    missing-data gap shouldn't read as "unusual," just as "unknown."
    """
    if vol_ratio is None or (isinstance(vol_ratio, float) and math.isnan(vol_ratio)):
        log.warning("Signal confidence couldn't be computed because of NaN component/s in vol_ratio")
        return SignalConfidenceRegime.NORMAL
    if vol_ratio >= high_threshold:
        return SignalConfidenceRegime.HIGH
    if vol_ratio <= low_threshold:
        return SignalConfidenceRegime.LOW
    return SignalConfidenceRegime.NORMAL


def compute_signal_confidence(vol_ratio, low_threshold: float, high_threshold: float,
                               high_vol_discount: float = 0.5, low_vol_discount: float = 1.0) -> float:
    """
    Per-instrument discount on trust in THIS instrument's trend signal,
    triggered when its own vol_ratio is unusual relative to its own
    history -- distinct from regime_discount (fast/slow sign
    disagreement) and from vix_scalar (portfolio-wide, VX-
    driven; applied by the caller, not in here).

    high_vol_discount and low_vol_discount are independent, free
    parameters -- deliberately NOT assumed symmetric. The literature
    reviewed in cta-vol-scalar-clamping.md treats high-vol momentum
    unreliability and low-vol mean-variance leverage opportunities
    (Bongaerts et al.'s low-vol response is to increase exposure for
    alpha reasons specific to equity factor timing, not to discount trend
    confidence) as different phenomena for different reasons -- there is
    no settled answer for whether low vol should discount this system's
    trend signal at all, hence low_vol_discount's no-op default of 1.0,
    vs high_vol_discount's suggested 0.5 (vol spikes specifically damage
    momentum reliability, per the Mozes-article finding already
    established in this project's research).
    """
    regime = classify_signal_confidence(vol_ratio, low_threshold, high_threshold)
    if regime == SignalConfidenceRegime.HIGH:
        return high_vol_discount
    if regime == SignalConfidenceRegime.LOW:
        return low_vol_discount
    return 1.0


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
    # for _goulding_direction(), NOT estimated here. Pooled, expanding-window
    # a_Co/a_Re ESTIMATION (eq. 8-10) is a multi-symbol process that doesn't
    # fit this dataclass's per-symbol, stateless fields -- see this same
    # module's estimate_mixing_params, which implements it and passes the
    # result into _goulding_direction directly. 0.5/0.5 is the paper's own
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

    r1d is a SIMPLE daily return (close.pct_change(), i.e. close/prev_close
    - 1), not a log return -- this module uses simple returns throughout
    (except calculate_trend_strength, the old retired function above,
    which predates this convention), never log returns. No caller needs
    prev_close itself (only r1d), so it's never materialized as its own
    column.

    Sorts by ts_event first -- shift()/cum_max() are order-dependent, and
    every downstream function (continuous_momentum, goulding_monthly)
    inherits whatever order this leaves the frame in, so this is the one
    place that guarantee needs to be established."""
    df = df.sort('ts_event')
    df = df.with_columns(
        peak=pl.col('close').cum_max(),
        r1d=pl.col('close').pct_change(),
    )
    df = df.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
    )
    return df


def continuous_momentum(df: pl.DataFrame, fast_window: int = DEFAULT_FAST_WINDOW,
                         slow_window: int = DEFAULT_SLOW_WINDOW,
                         vol_fast_window: Optional[int] = None, vol_slow_window: Optional[int] = None,
                         annualization_days: int = DEFAULT_ANNUALIZATION_DAYS,
                         w_fast: float = 0.4, w_slow: float = 0.6, discount: float = 0.5,
                         macd_signal_halflife: float = DEFAULT_MACD_SIGNAL_HALFLIFE) -> pl.DataFrame:
    """Continuous, daily, volatility-normalized fast/slow trend-strength
    model -- independent of goulding_monthly; takes only build_features'
    output (peak/dd/r1d), no shared intermediate state between
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
    n-day-return-over-n-day-vol correspondence. avg_r_fast/avg_r_slow and
    macd/macd_signal/macd_diff below are NOT part of this -- reporting/
    charting diagnostics only, never touching ts_fast/ts_slow/ts/signal.

    avg_r_fast/avg_r_slow: EXPONENTIALLY-weighted mean daily return
    (r1d.ewm_mean(half_life=fast_window/slow_window)), annualized --
    intentionally NOT the same equal-weighted rolling_mean convention
    std_fast/std_slow use; this is a pure reporting figure with no
    horizon-matching constraint to preserve, so EWM's smoother, more
    recency-weighted average is preferred here.

    ewm_fast/ewm_slow = close.ewm_mean(half_life=fast_window/slow_window)
    -- the underlying fast/slow EMA PRICE lines themselves, exposed as
    their own columns (e.g. for a standard price+EMA chart), not just
    embedded inside macd. macd = ewm_fast - ewm_slow. Reuses fast_window/
    slow_window as EWM half-lives rather than introducing a second,
    independent pair of MACD-specific windows, so "fast"/"slow" mean one
    consistent pair of numbers across every feature in this function.
    Flag this explicitly though: an EWM half-life of N behaves nothing
    like ts_fast/ts_slow's own N-day lookback (a half-life-N EWM's
    effective memory extends well past N days, unlike a hard N-day
    window) -- macd is a genuinely different kind of "fast"/"slow" than
    ts_fast/ts_slow, just sharing the same config numbers by deliberate
    choice, not because the underlying math is equivalent. macd_signal
    is macd's own EWM smoothing at a separate, much shorter half-life
    (macd_signal_halflife, default 10 days -- deliberately NOT derived
    from fast_window/slow_window,
    see DEFAULT_MACD_SIGNAL_HALFLIFE's own comment). macd_diff = macd -
    macd_signal, the standard MACD histogram.

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
        avg_r_fast=pl.col('r1d').ewm_mean(half_life=fast_window) * annualization_days,
        avg_r_slow=pl.col('r1d').ewm_mean(half_life=slow_window) * annualization_days,
        std_fast=pl.col('r1d').rolling_std(vol_fast_window),
        std_slow=pl.col('r1d').rolling_std(vol_slow_window),
        ewm_fast=pl.col('close').ewm_mean(half_life=fast_window),
        ewm_slow=pl.col('close').ewm_mean(half_life=slow_window),
    )
    df = df.with_columns(
        macd=pl.col('ewm_fast') - pl.col('ewm_slow'),
    )
    df = df.with_columns(
        macd_signal=pl.col('macd').ewm_mean(half_life=macd_signal_halflife),
    )
    df = df.with_columns(
        macd_diff=pl.col('macd') - pl.col('macd_signal'),
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


def resolve_trend_direction(signal_weighting: str, continuous_signal: Optional[float],
                             ts_fast: Optional[float], ts_slow: Optional[float],
                             regime_discount_cfg: float,
                             g_regime_val: Optional[str] = None, g_fast_val: Optional[float] = None,
                             g_slow_val: Optional[float] = None,
                             a_co: float = 0.5, a_re: float = 0.5,
                             ) -> Optional[tuple[float, TrendRegime, float, Optional[float]]]:
    """(trend_strength, regime, regime_discount, blend) for either
    signal_weighting mode -- "Goulding decides direction, vol-parity
    decides size" in 'goulding' mode, continuous_momentum's own signal +
    classify_regime in 'continuous' mode. Factored out of
    tsmom_backtester.py's _compute_signal_row so that module and
    live.tsmom_rebalance's own per-instrument signal computation share ONE
    implementation of this branch instead of two independently-maintained
    copies that could drift apart on exactly the subtlety
    _goulding_direction's own docstring warns about (g_fast_val/g_slow_val
    must be goulding_monthly's lagged fast/slow, not a same-period
    realized return).

    'goulding': g_regime_val is goulding_monthly's own `regime` column
    value for the bucket in question; g_fast_val/g_slow_val its `fast`/
    `slow`; a_co/a_re that cluster's (or global pool's) own estimated
    mixing weights (see estimate_mixing_params). regime_discount is always
    1.0 in this mode -- a_co/a_re IS the Correction/Rebound discount
    mechanism; applying regime_discount_cfg on top would double-discount a
    decision eq. 7 already made. `blend` is _goulding_direction's own raw,
    pre-sign eq. 7 value, returned alongside trend_strength from that same
    call -- (1-a_Co)*r_SLOW + a_Co*r_FAST in Correction, (1-a_Re)*r_SLOW +
    a_Re*r_FAST in Rebound -- for audit/display (e.g. a saved report
    showing *why* trend_strength came out +1/-1, not just that it did);
    always None in Bull/Bear (eq. 7 doesn't apply there -- trend_strength
    is unconditionally +-1, nothing to blend) even though trend_strength
    itself is resolved in that case. Returns None (the whole tuple) when
    g_regime_val is None or _goulding_direction itself can't resolve a
    direction (missing/invalid g_fast_val/g_slow_val).

    'continuous': continuous_signal is continuous_momentum's own `signal`
    column value; regime is classify_regime(ts_fast, ts_slow);
    regime_discount is regime_discount_cfg in Correction/Rebound, 1.0
    otherwise; blend is always None (not a goulding-mode concept). Returns
    None when continuous_signal is None (not yet enough history for a
    signal at all)."""
    if signal_weighting == 'goulding':
        if g_regime_val is None:
            return None
        regime = TrendRegime(g_regime_val.lower())
        resolved = _goulding_direction(g_regime_val, a_co, a_re, g_fast_val, g_slow_val)
        if resolved is None:
            return None
        trend_strength, blend = resolved
        return trend_strength, regime, 1.0, blend
    if continuous_signal is None:
        return None
    regime = classify_regime(ts_fast, ts_slow)
    regime_discount = regime_discount_cfg if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0
    return continuous_signal, regime, regime_discount, None


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
    """Appendix C, eq. 8-10 -- a_Co/a_Re from every (state, monthly_return)
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
    normalizer C / either phase's mean-squared return is degenerate
    (zero); the paper's own rule for insufficient per-asset history is to
    exclude the asset for that month entirely, which doesn't map cleanly
    onto a pooled, always-in-the-portfolio backtest, so this is an
    explicit, flagged adaptation, not a literal reproduction. A cluster
    with too few symbols/too little history of its own simply stays at
    (0.5, 0.5) longer (or indefinitely) under cluster-scoped pooling --
    an intentional consequence of not borrowing another cluster's
    behavior, not a bug.

    Eq. 9's sign, as extracted from the scanned paper, appears identical in
    form to eq. 8's (both "1 - ...") -- which contradicts the paper's own
    prose ("if returns tend to be positive after rebounds... a_Re > 0.5")
    given a single shared, "typically positive" C. Uses the "+" form here
    (a_Re = 1/2*(1 + (1/C)*AVG[r|Re]/AVG[r^2|Re])) as the only version
    self-consistent with that prose -- see
    research/research_trend_strength_crossover_signal.md Part 2 §6 for the
    full errata discussion; this is flagged, not confirmed against the
    primary source's actual typeset sign."""
    prior = history.filter(pl.col('date') < as_of)
    if cluster is not None:
        prior = prior.filter(pl.col('cluster') == cluster)
    if prior.height == 0:
        return 0.5, 0.5

    def _stats(state: str) -> tuple[int, float, float]:
        sub = prior.filter(pl.col('state') == state)
        if sub.height == 0:
            return 0, 0.0, 0.0
        r = sub['monthly_return']
        return sub.height, r.mean(), (r * r).mean()

    n_bu, avg_r_bu, avg_r2_bu = _stats('bull')
    n_be, avg_r_be, avg_r2_be = _stats('bear')
    n_co, avg_r_co, avg_r2_co = _stats('correction')
    n_re, avg_r_re, avg_r2_re = _stats('rebound')

    if n_co < min_months or n_re < min_months:
        return 0.5, 0.5
    freq_tot = n_bu + n_be
    # Exact `== 0` float equality is fragile here -- these are means of
    # squared monthly returns, so a near-degenerate (but not exactly zero)
    # value like 1e-12 would sail past an exact-zero check and then blow
    # up the 1/x below. freq_tot is a plain integer count (n_bu + n_be),
    # so exact-zero is fine and intentional for it specifically.
    if freq_tot == 0 or abs(avg_r2_bu) < _DEGENERATE_EPS or abs(avg_r2_be) < _DEGENERATE_EPS:
        return 0.5, 0.5

    C = (n_bu / freq_tot) * (avg_r_bu / avg_r2_bu) - (n_be / freq_tot) * (avg_r_be / avg_r2_be)
    if abs(C) < _DEGENERATE_EPS or abs(avg_r2_co) < _DEGENERATE_EPS or abs(avg_r2_re) < _DEGENERATE_EPS:
        return 0.5, 0.5

    a_co = 0.5 * (1 - (1 / C) * (avg_r_co / avg_r2_co))
    a_re = 0.5 * (1 + (1 / C) * (avg_r_re / avg_r2_re))
    log.info('Change in default params: n_bu %d | n_be %d | n_co %d (%s) | n_re %d (%s)',
             n_bu, n_be, n_co, a_co, n_re, a_re)
    log.info('avg_r_bu %s | avg_r_be %s | avg_r_co %s | avg_r_re %s', avg_r_bu, avg_r_be, avg_r_co, avg_r_re)

    return max(0.0, min(1.0, a_co)), max(0.0, min(1.0, a_re))

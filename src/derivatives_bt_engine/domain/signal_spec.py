"""
Modular, swappable TSMOM signal construction.

tsmom_signal.calculate_trend_strength() stays canonical and untouched (its
own docstring says not to redesign it, and nothing here does) -- this module
is the layer ABOVE it that lets a caller pick, per test/backtest run, which
economic formula and which window representation produced a given trend-
strength score, without touching any existing call site's default behavior.

Three genuinely independent axes, each already established separately
elsewhere in this project and deliberately NOT collapsed into one knob:

  1. instruments.annualization_days -- real trading-days/year for a given
     symbol (252 for the CBOT grains, 259 for the rest of this project's
     confirmed universe, post Sunday-session-merge fix). Pure units
     conversion for genuinely per-calendar-year quantities (hv, avg_r_fast/
     avg_r_slow, current_realized_vol) -- see tsmom_signal.py's own module
     docstring. Never derives window length.
  2. WindowBasis (enums.py) -- OBSERVATIONS (a fixed trading-day row count,
     this project's long-standing convention) vs. CALENDAR (a fixed
     calendar interval, e.g. "3 months ago" by date -- what a paper like
     Goulding, Harvey & Mazzoleni actually means by "N-month return").
     These are NOT the same thing once different assets/eras have
     different trading-days-per-calendar-period -- deriving one from the
     other (e.g. "63-day window = annualization_days/4") would silently
     change the signal itself between assets, not just how it's reported.
  3. SignalModel (enums.py) -- which economic formula: this project's
     original CLASSIC_TS fast/slow tanh-blend, or Goulding et al.'s
     bimonthly/annual construction with Bull/Bear/Correction/Rebound-
     conditioned reweighting.

compute_signal(df, spec) is the single dispatcher every (model, basis)
combination goes through, returning a common column contract (ts_event,
close, regime, signal, fast_return, slow_return, ts_fast, ts_slow,
daily_std, plus window_days_fast/window_days_slow when basis is CALENDAR)
so a caller can swap SignalSpecs and compare results without rewriting
downstream code for each combination.

On CALENDAR basis and continuous (e.g. daily) recomputation: a calendar-
month lookback has no fixed row count (holidays/weekends/this project's own
Sunday-session-merge history all shift it), so its vol-scaling denominator
is a genuinely per-row computed quantity here (window_days_fast/slow),
not a constant sqrt(N) the way OBSERVATIONS basis uses. This is handled
correctly either way, but CALENDAR basis only earns its extra complexity
at discrete (e.g. monthly) evaluation points matching how Goulding's own
paper is actually used -- recomputed daily, it converges to nearly the
same values as OBSERVATIONS basis, since both are smoothing out to the
same underlying trend at that frequency. Deciding the (window_basis,
rebalance cadence) pairing is the caller's responsibility -- this module
supports either, it doesn't enforce one.

Goulding's dynamic a_Co/a_Re reweighting (eq. 8-10) is a POOLED, multi-
symbol, expanding-window ESTIMATION process (see research/
research_trend_strength_crossover_signal.md Part 2 §6b and
scripts/tsmom_binary_vol_parity_backtest.py's _estimate_mixing_params,
which already implements it) -- it doesn't fit this module's per-symbol,
stateless compute_signal(df, spec) signature, and isn't reimplemented here.
SignalSpec.a_co/a_re are plain inputs (default 0.5/0.5, i.e. a flat, no-op
reweight -- see eq. 7's own derivation for why 0.5/0.5 collapses to
"fully flat" in Correction/Rebound); a caller doing the full paper
reproduction estimates them separately (pooled across symbols) and passes
the result in here, same separation of concerns as n_effective/
compute_desired_risk_budget already living outside tsmom_signal.py's own
per-symbol functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.enums import SignalModel, TrendRegime, WindowBasis
from derivatives_bt_engine.domain.tsmom_signal import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
    calculate_trend_strength,
    classify_regime,
)

# ── Tunable defaults ─────────────────────────────────────────────────────
# Goulding et al.'s own horizons (calendar months) -- distinct from this
# project's canonical 3m/12m (DEFAULT_FAST_WINDOW/DEFAULT_SLOW_WINDOW in
# tsmom_signal.py), which is why SignalSpec carries separate *_months
# fields rather than reusing fast_days/slow_days for CALENDAR basis.
DEFAULT_FAST_MONTHS = 3
DEFAULT_SLOW_MONTHS = 12
GOULDING_FAST_MONTHS = 2
GOULDING_SLOW_MONTHS = 12
# Observation-basis approximation of GOULDING_FAST_MONTHS, matching the
# already-validated standalone script (scripts/tsmom_binary_vol_parity_
# backtest.py's PAPER_FAST_DAYS) -- ~2 months at this project's usual
# ~21-trading-day/month rule of thumb.
GOULDING_FAST_DAYS = 42


@dataclass
class SignalSpec:
    """Strategy/test-level signal configuration -- deliberately separate
    from instrument metadata (instruments.py's INSTRUMENTS dict): this
    describes HOW to compute a signal from a price series, not WHICH
    instrument it's for. annualization_days is the one exception carried
    here as a plain field rather than looked up internally -- the caller
    resolves it per-instrument (instruments.resolve_annualization_days)
    and passes the result in, keeping this dataclass itself instrument-
    agnostic and easily reusable/comparable across a parameter sweep.

    Defaults reproduce this project's original, long-standing behavior
    exactly (CLASSIC_TS, OBSERVATIONS, 63/252-day windows, 0.4/0.6 weights,
    0.5 momentum_discount, 252-day annualization) -- constructing a bare
    SignalSpec() and calling compute_signal() is equivalent to calling
    calculate_trend_strength() directly with no extra arguments.
    """
    model: SignalModel = SignalModel.CLASSIC_TS
    window_basis: WindowBasis = WindowBasis.OBSERVATIONS

    # OBSERVATIONS-basis window lengths (trading-day row counts).
    fast_days: int = DEFAULT_FAST_WINDOW
    slow_days: int = DEFAULT_SLOW_WINDOW

    # CALENDAR-basis window lengths (calendar months). Defaults match this
    # project's classic 3m/12m labels -- use the goulding() factory below
    # for the paper's own 2m/12m instead of setting fast_months by hand.
    fast_months: int = DEFAULT_FAST_MONTHS
    slow_months: int = DEFAULT_SLOW_MONTHS

    w_fast: float = 0.4
    w_slow: float = 0.6
    momentum_discount: float = 0.5

    # Goulding's eq. 7 dynamic-reweight inputs -- plain pass-through values,
    # NOT estimated in this module (see module docstring). 0.5/0.5 is the
    # paper's own uninformed fallback and collapses eq. 7 to a flat
    # momentum_discount-equivalent no-op reweight.
    a_co: float = 0.5
    a_re: float = 0.5

    annualization_days: int = DEFAULT_ANNUALIZATION_DAYS

    def __post_init__(self):
        if not isinstance(self.model, SignalModel):
            raise ValueError(f"model must be a SignalModel, got {self.model!r}")
        if not isinstance(self.window_basis, WindowBasis):
            raise ValueError(f"window_basis must be a WindowBasis, got {self.window_basis!r}")
        if self.fast_days <= 0 or self.slow_days <= 0:
            raise ValueError("fast_days/slow_days must be positive")
        if self.fast_months <= 0 or self.slow_months <= 0:
            raise ValueError("fast_months/slow_months must be positive")
        if not (0.0 <= self.a_co <= 1.0) or not (0.0 <= self.a_re <= 1.0):
            raise ValueError("a_co/a_re must be in [0, 1] (eq. 7's own mixing-weight range)")

    @staticmethod
    def goulding(annualization_days: int = DEFAULT_ANNUALIZATION_DAYS,
                 window_basis: WindowBasis = WindowBasis.CALENDAR,
                 a_co: float = 0.5, a_re: float = 0.5) -> "SignalSpec":
        """Convenience factory for Goulding et al.'s own parameterization
        (2-month fast / 12-month slow), CALENDAR basis by default since
        that's what the paper's own "N-month return" literally means --
        pass window_basis=WindowBasis.OBSERVATIONS for the cheaper
        ~42-trading-day approximation instead (GOULDING_FAST_DAYS), which is
        what scripts/tsmom_binary_vol_parity_backtest.py's own dynamic mode
        calls directly."""
        return SignalSpec(
            model=SignalModel.GOULDING_DYNAMIC, window_basis=window_basis,
            fast_days=GOULDING_FAST_DAYS, slow_days=DEFAULT_SLOW_WINDOW,
            fast_months=GOULDING_FAST_MONTHS, slow_months=GOULDING_SLOW_MONTHS,
            a_co=a_co, a_re=a_re, annualization_days=annualization_days,
        )


def _calendar_log_return(df: pl.DataFrame, months: int, price_col: str = 'close') -> pl.DataFrame:
    """Adds `r{months}m_cal` (log return from the closest trading day at or
    before `months` calendar-months ago, to today) and `window_days_{months}m`
    (the ACTUAL number of trading-day rows that spans -- not a fixed count)
    to `df`, via a calendar-date join_asof (polars' dt.offset_by, which
    correctly handles variable month lengths) rather than a fixed row
    offset. `df` must be sorted by ts_event; returns a frame re-sorted to
    match the input's original row order."""
    d = df.sort('ts_event').with_row_index('_row_idx')
    d = d.with_columns(_target_date=pl.col('ts_event').dt.offset_by(f'-{months}mo'))

    lookup = d.select(
        pl.col('ts_event').alias('_lookup_date'),
        pl.col(price_col).alias('_price_ago'),
        pl.col('_row_idx').alias('_row_idx_ago'),
    ).sort('_lookup_date')

    d = d.sort('_target_date').join_asof(lookup, left_on='_target_date', right_on='_lookup_date', strategy='backward')
    d = d.sort('_row_idx')
    d = d.with_columns(
        (pl.col('_row_idx') - pl.col('_row_idx_ago')).alias(f'window_days_{months}m'),
        (pl.col(price_col).log() - pl.col('_price_ago').log()).alias(f'r{months}m_cal'),
    )
    return d.drop(['_row_idx', '_target_date', '_lookup_date', '_price_ago', '_row_idx_ago'])


def _finalize_common_columns(df: pl.DataFrame, ts_fast: str, ts_slow: str,
                              fast_ret: str, slow_ret: str) -> pl.DataFrame:
    """Renames a branch's own fast/slow-named columns to this module's
    common output contract (ts_fast/ts_slow/fast_return/slow_return),
    without dropping the originals (kept for anyone who wants the model's
    own native naming -- a no-op for CLASSIC_TS, whose native names already
    match; r2m_cal/r1y_cal for calendar-basis Goulding, for debugging/direct
    comparison)."""
    renames = {}
    if ts_fast != 'ts_fast' and 'ts_fast' not in df.columns:
        renames[ts_fast] = 'ts_fast'
    if ts_slow != 'ts_slow' and 'ts_slow' not in df.columns:
        renames[ts_slow] = 'ts_slow'
    if fast_ret != 'fast_return' and 'fast_return' not in df.columns:
        renames[fast_ret] = 'fast_return'
    if slow_ret != 'slow_return' and 'slow_return' not in df.columns:
        renames[slow_ret] = 'slow_return'
    return df.with_columns([pl.col(k).alias(v) for k, v in renames.items()])


def _classic_ts_observations(df: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """CLASSIC_TS + OBSERVATIONS: a thin wrapper around calculate_trend_
    strength -- byte-identical to calling it directly with the same
    arguments (verified in tests/domain/test_signal_spec.py).
    calculate_trend_strength's own columns are now natively named ts_fast/
    ts_slow/fast_return/slow_return (renamed from ts3m/ts1y/r3m/r1y, since
    fast_window/slow_window are genuinely configurable) -- already this
    module's common contract, so _finalize_common_columns below is a no-op
    for this branch, kept only so every branch goes through the same call
    shape."""
    out = calculate_trend_strength(
        df, w3m=spec.w_fast, w1y=spec.w_slow, discount=spec.momentum_discount,
        annualization_days=spec.annualization_days,
        fast_window=spec.fast_days, slow_window=spec.slow_days,
    )
    out = out.with_columns(regime=pl.col('regime').str.to_uppercase())
    return _finalize_common_columns(out, 'ts_fast', 'ts_slow', 'fast_return', 'slow_return')


def _classic_ts_calendar(df: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """CLASSIC_TS + CALENDAR: same tanh-blend construction as the
    OBSERVATIONS branch, but fast_return/slow_return are genuine calendar-
    month log returns (join_asof by date, see _calendar_log_return) and
    the vol-scaling denominator uses each row's own actual window_days
    (not a fixed sqrt(N)), since a calendar window's row count varies by
    date/instrument."""
    d = df.sort('ts_event').with_columns(
        log_price=pl.col('close').log(),
        peak=pl.col('close').cum_max(),
    )
    d = d.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d=pl.col('log_price').diff(1),
    )
    # daily_std stays OBSERVATION-based (fast_days rows) regardless of
    # window_basis -- vol ESTIMATION and window REPRESENTATION are
    # separate concerns, same reasoning as annualization_days vs. window
    # length in tsmom_signal.py's own module docstring.
    d = d.with_columns(daily_std=pl.col('r1d').rolling_std(spec.fast_days))
    d = d.with_columns(hv=pl.col('daily_std') * spec.annualization_days ** 0.5)

    d = _calendar_log_return(d, spec.fast_months)
    d = _calendar_log_return(d, spec.slow_months)
    fast_ret_col, slow_ret_col = f'r{spec.fast_months}m_cal', f'r{spec.slow_months}m_cal'
    fast_wd_col, slow_wd_col = f'window_days_{spec.fast_months}m', f'window_days_{spec.slow_months}m'

    d = d.with_columns(
        ts_fast=pl.col(fast_ret_col) / (pl.col('daily_std') * pl.col(fast_wd_col).cast(pl.Float64).sqrt()),
        ts_slow=pl.col(slow_ret_col) / (pl.col('daily_std') * pl.col(slow_wd_col).cast(pl.Float64).sqrt()),
    )
    d = d.with_columns(
        regime=pl.struct(['ts_fast', 'ts_slow']).map_elements(
            lambda s: classify_regime(s['ts_fast'], s['ts_slow']).value.upper(),
            return_dtype=pl.Utf8,
        ),
    )
    d = d.with_columns(
        _blend=pl.when(pl.col('ts_slow').is_not_null())
        .then((spec.w_fast * pl.col('ts_fast').fill_null(0) + spec.w_slow * pl.col('ts_slow').fill_null(0)).tanh())
        .otherwise(None),
    )
    d = d.with_columns(
        signal=pl.when(pl.col('regime').is_in(['CORRECTION', 'REBOUND']))
        .then(pl.col('_blend') * spec.momentum_discount)
        .otherwise(pl.col('_blend'))
    )
    d = d.drop(['log_price', '_blend'], strict=False)
    return _finalize_common_columns(d, 'ts_fast', 'ts_slow', fast_ret_col, slow_ret_col)


def _goulding_weight(regime_val: str, a_co: float, a_re: float) -> Optional[float]:
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


def _goulding_observations(df: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """GOULDING_DYNAMIC + OBSERVATIONS: r_fast/r_slow as fixed trading-day
    lookback returns (spec.fast_days ~= 2 months, spec.slow_days ~= 12
    months), matching the already-validated approximation in
    scripts/tsmom_binary_vol_parity_backtest.py."""
    d = df.sort('ts_event').with_columns(
        log_price=pl.col('close').log(),
        peak=pl.col('close').cum_max(),
    )
    d = d.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d=pl.col('log_price').diff(1),
        r_fast=pl.col('log_price').diff(spec.fast_days),
        r_slow=pl.col('log_price').diff(spec.slow_days),
    )
    d = d.with_columns(daily_std=pl.col('r1d').rolling_std(spec.fast_days))
    d = d.with_columns(
        hv=pl.col('daily_std') * spec.annualization_days ** 0.5,
        ts_fast=pl.col('r_fast') / (pl.col('daily_std') * math.sqrt(spec.fast_days)),
        ts_slow=pl.col('r_slow') / (pl.col('daily_std') * math.sqrt(spec.slow_days)),
    )
    return _goulding_finish(d, spec)


def _goulding_calendar(df: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """GOULDING_DYNAMIC + CALENDAR: r_fast/r_slow as genuine calendar-month
    returns (join_asof by date) -- the most literal reproduction of the
    paper's own "2-month"/"12-month return" construction."""
    d = df.sort('ts_event').with_columns(
        log_price=pl.col('close').log(),
        peak=pl.col('close').cum_max(),
    )
    d = d.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d=pl.col('log_price').diff(1),
    )
    d = d.with_columns(daily_std=pl.col('r1d').rolling_std(spec.fast_days))
    d = d.with_columns(hv=pl.col('daily_std') * spec.annualization_days ** 0.5)

    d = _calendar_log_return(d, spec.fast_months)
    d = _calendar_log_return(d, spec.slow_months)
    fast_ret_col, slow_ret_col = f'r{spec.fast_months}m_cal', f'r{spec.slow_months}m_cal'
    fast_wd_col, slow_wd_col = f'window_days_{spec.fast_months}m', f'window_days_{spec.slow_months}m'

    d = d.with_columns(
        r_fast=pl.col(fast_ret_col), r_slow=pl.col(slow_ret_col),
        ts_fast=pl.col(fast_ret_col) / (pl.col('daily_std') * pl.col(fast_wd_col).cast(pl.Float64).sqrt()),
        ts_slow=pl.col(slow_ret_col) / (pl.col('daily_std') * pl.col(slow_wd_col).cast(pl.Float64).sqrt()),
    )
    return _goulding_finish(d, spec)


def _goulding_finish(d: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """Shared tail for both Goulding branches: eq. 4's raw-sign state
    (from r_fast/r_slow directly, not the vol-normalized ts_fast/ts_slow --
    sign is identical either way since dividing by a positive vol term
    never flips it, so classify_regime works unchanged) and eq. 7's
    weight blend (see _goulding_weight)."""
    d = d.with_columns(
        regime=pl.struct(['r_fast', 'r_slow']).map_elements(
            lambda s: classify_regime(s['r_fast'], s['r_slow']).value.upper(),
            return_dtype=pl.Utf8,
        ),
    )
    d = d.with_columns(
        signal=pl.col('regime').map_elements(
            lambda r: _goulding_weight(r, spec.a_co, spec.a_re), return_dtype=pl.Float64,
        ),
    )
    d = d.drop(['log_price'], strict=False)
    return _finalize_common_columns(d, 'ts_fast', 'ts_slow', 'r_fast', 'r_slow')


_DISPATCH = {
    (SignalModel.CLASSIC_TS, WindowBasis.OBSERVATIONS): _classic_ts_observations,
    (SignalModel.CLASSIC_TS, WindowBasis.CALENDAR): _classic_ts_calendar,
    (SignalModel.GOULDING_DYNAMIC, WindowBasis.OBSERVATIONS): _goulding_observations,
    (SignalModel.GOULDING_DYNAMIC, WindowBasis.CALENDAR): _goulding_calendar,
}


def compute_signal(df: pl.DataFrame, spec: SignalSpec) -> pl.DataFrame:
    """Single dispatcher for every (SignalModel, WindowBasis) combination
    -- see this module's own docstring for the full design. `df` needs at
    least 'ts_event' (pl.Date) and 'close' columns, same contract as
    calculate_trend_strength. Returns a common column set (ts_event, close,
    regime, signal, ts_fast, ts_slow, fast_return, slow_return, daily_std,
    plus dd/hv and, for CALENDAR basis, window_days_{N}m) regardless of
    which branch actually ran, so callers can swap specs freely."""
    branch = _DISPATCH.get((spec.model, spec.window_basis))
    if branch is None:
        raise ValueError(f"Unsupported SignalSpec combination: {spec.model!r}/{spec.window_basis!r}")
    return branch(df, spec)

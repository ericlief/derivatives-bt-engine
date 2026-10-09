"""Per-instrument volatility confidence overlay for trend forecasts.

The helpers compare short- and long-horizon realized volatility, classify the
resulting regime, and return the optional confidence discount consumed by live
position sizing. Trend construction and portfolio risk allocation remain in
their own modules.
"""

from __future__ import annotations

import math
from enum import Enum

import polars as pl

from derivatives_bt_engine.logging_config import setup_logger


log = setup_logger()


class SignalConfidenceRegime(str, Enum):
    """Instrument-specific short-versus-long realized-volatility state."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


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

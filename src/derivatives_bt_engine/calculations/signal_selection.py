"""Cross-model trend-forecast selection and conviction scoring.

Continuous momentum and Goulding each construct forecasts in their own
modules.  This module is the explicit boundary that selects one configured
model for sizing and provides the signal-only score used to prioritize
instruments within a capped cluster.
"""

from __future__ import annotations

import math
from typing import Mapping, Optional

from derivatives_bt_engine.calculations.continuous_momentum import (
    TrendRegime,
    classify_regime,
)
from derivatives_bt_engine.calculations.goulding import (
    GOULDING_SIGNAL_MODES,
    _goulding_direction,
    goulding_continuous_raw,
    normalize_goulding_forecast,
)


def cluster_conviction_score(signal_weighting: str, signal: Mapping[str, object]) -> float:
    """Return the raw model evidence used to rank a capped cluster universe.

    This is deliberately signal-only: volatility scaling, VIX, confidence,
    and risk budgets decide *size*, not which underlying trend is strongest.
    Goulding's Bull/Bear direction is binary, so agreeing states use the
    absolute equal-weighted fast/slow monthly-return average; disagreement
    states use the absolute raw equation-7 blend.
    """
    def finite(value: object) -> Optional[float]:
        """Convert an optional audit value to a finite float or ``None``."""
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None

    if signal_weighting in ('continuous', 'carver_ewmac'):
        return abs(finite(signal.get('contin_signal')) or 0.0)

    regime_value = signal.get('g_regime')
    regime = (regime_value.value if isinstance(regime_value, TrendRegime)
              else str(regime_value or '')).lower()
    fast = finite(signal.get('g_fast'))
    slow = finite(signal.get('g_slow'))
    if fast is None or slow is None:
        return 0.0
    if regime in ('bull', 'bear'):
        return abs((fast + slow) / 2.0)
    if regime in ('correction', 'rebound'):
        blend = finite(signal.get('g_blend'))
        if blend is None:
            weight = finite(signal.get('a_co' if regime == 'correction' else 'a_re'))
            if weight is None:
                return 0.0
            blend = (1.0 - weight) * slow + weight * fast
        return abs(blend)
    return 0.0


def resolve_trend_direction(signal_weighting: str, continuous_signal: Optional[float],
                             ts_fast: Optional[float], ts_slow: Optional[float],
                             regime_discount_cfg: float,
                             g_regime_val: Optional[str] = None, g_fast_val: Optional[float] = None,
                             g_slow_val: Optional[float] = None,
                             a_co: float = 0.5, a_re: float = 0.5,
                             goulding_signal_mode: str = 'binary',
                             goulding_forecast_scalar: Optional[float] = None,
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
    always None in Bull/Bear (eq. 7 doesn't apply there). In `binary` mode
    trend_strength is +/-1/0. In `continuous` mode its raw value is the
    fast/slow mean in Bull/Bear or the equation-7 blend in disagreement
    states, then a caller-supplied causal forecast scalar targets average
    absolute 0.5 and caps at +/-1. Returns None when the required inputs or
    continuous-mode scalar are unavailable.

    'continuous': continuous_signal is continuous_momentum's own `signal`
    column value; regime is classify_regime(ts_fast, ts_slow);
    `continuous_signal` is continuous_momentum's already-discounted `signal`
    column, so the returned regime_discount is always 1.0. Returning
    regime_discount_cfg here used to apply the same Correction/Rebound
    discount a second time inside compute_position_scalar. `blend` is always
    None (not a continuous-momentum concept). Returns None when
    continuous_signal is None (not yet enough history for a signal at all)."""
    if signal_weighting == 'goulding':
        if goulding_signal_mode not in GOULDING_SIGNAL_MODES:
            raise ValueError(f"goulding_signal_mode must be one of {GOULDING_SIGNAL_MODES}, "
                             f"got {goulding_signal_mode!r}")
        if g_regime_val is None:
            return None
        regime = TrendRegime(g_regime_val.lower())
        if goulding_signal_mode == 'binary':
            resolved = _goulding_direction(g_regime_val, a_co, a_re, g_fast_val, g_slow_val)
            if resolved is None:
                return None
            trend_strength, blend = resolved
        else:
            raw_forecast = goulding_continuous_raw(
                g_regime_val, a_co, a_re, g_fast_val, g_slow_val,
            )
            trend_strength = normalize_goulding_forecast(raw_forecast, goulding_forecast_scalar)
            if trend_strength is None:
                return None
            blend = raw_forecast if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else None
        return trend_strength, regime, 1.0, blend
    if continuous_signal is None:
        return None
    regime = classify_regime(ts_fast, ts_slow)
    return continuous_signal, regime, 1.0, None

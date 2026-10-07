"""Compatibility imports for the former combined signal module.

New code should import trend models, legacy logic, and confidence overlays from
their dedicated domain modules. This file contains no calculations.
"""

from derivatives_bt_engine.domain.continuous_momentum import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
    build_features,
    classify_regime,
    continuous_momentum,
)
from derivatives_bt_engine.domain.goulding import (
    GOULDING_FAST_MONTHS,
    GOULDING_FORECAST_CAP,
    GOULDING_FORECAST_MIN_OBS,
    GOULDING_FORECAST_TARGET_ABS,
    GOULDING_SIGNAL_MODES,
    GOULDING_SLOW_MONTHS,
    MIN_MONTHS_PER_PHASE,
    _goulding_blend,
    _goulding_direction,
    build_monthly_state_return_history,
    estimate_goulding_forecast_scalar,
    estimate_mixing_params,
    estimate_mixing_params_diagnostics,
    goulding_continuous_raw,
    goulding_monthly,
    normalize_goulding_forecast,
)
from derivatives_bt_engine.domain.legacy_signal import calculate_trend_strength
from derivatives_bt_engine.domain.signal_confidence import (
    classify_signal_confidence,
    compute_signal_confidence,
    compute_vol_ratio,
)
from derivatives_bt_engine.domain.signal_config import SignalSpec
from derivatives_bt_engine.domain.signal_selection import (
    cluster_conviction_score,
    resolve_trend_direction,
)

__all__ = [
    "DEFAULT_ANNUALIZATION_DAYS",
    "DEFAULT_FAST_WINDOW",
    "DEFAULT_SLOW_WINDOW",
    "GOULDING_FAST_MONTHS",
    "GOULDING_FORECAST_CAP",
    "GOULDING_FORECAST_MIN_OBS",
    "GOULDING_FORECAST_TARGET_ABS",
    "GOULDING_SIGNAL_MODES",
    "GOULDING_SLOW_MONTHS",
    "MIN_MONTHS_PER_PHASE",
    "SignalSpec",
    "build_features",
    "build_monthly_state_return_history",
    "calculate_trend_strength",
    "classify_regime",
    "classify_signal_confidence",
    "cluster_conviction_score",
    "compute_signal_confidence",
    "compute_vol_ratio",
    "continuous_momentum",
    "estimate_goulding_forecast_scalar",
    "estimate_mixing_params",
    "estimate_mixing_params_diagnostics",
    "goulding_continuous_raw",
    "goulding_monthly",
    "normalize_goulding_forecast",
    "resolve_trend_direction",
]

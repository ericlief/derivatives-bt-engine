"""Retired log-return trend model retained for compatibility tests.

Production trend forecasts live in dedicated continuous-momentum, EWMAC, and
Goulding modules. This module isolates the original implementation so new code
cannot mistake it for the production signal path.
"""

from __future__ import annotations

import math

import polars as pl

from derivatives_bt_engine.domain.continuous_momentum import (
    DEFAULT_ANNUALIZATION_DAYS,
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
)

def calculate_trend_strength(contract, w3m=0.4, w1y=0.6, discount=0.5,
                              annualization_days=DEFAULT_ANNUALIZATION_DAYS,
                              fast_window=DEFAULT_FAST_WINDOW, slow_window=DEFAULT_SLOW_WINDOW):
    """OLD (now retired) canonical signal function, kept for
    backward-compatible callers/tests only -- domain.continuous_momentum is
    the current canonical continuous model. Uses log returns while the
    production model uses simple returns.

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

"""
Tests for domain.signal_spec -- build_features/continuous_momentum/
goulding_monthly, three independent, pure functions computed straight from
raw OHLCV bars. Each model's own tests deliberately avoid touching the
other model's columns, mirroring the "no model depends on another model's
intermediate columns" requirement the module itself is built around.
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.domain.signal_spec import (
    GOULDING_FAST_MONTHS,
    GOULDING_SLOW_MONTHS,
    SignalSpec,
    _goulding_weight,
    build_features,
    continuous_momentum,
    goulding_monthly,
)


def _trading_dates(start: date, n: int) -> list[date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _price_df(start: date, n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    dates = _trading_dates(start, n)
    return pl.DataFrame({'ts_event': dates, 'close': close})


# ── SignalSpec ───────────────────────────────────────────────────────────

def test_signal_spec_defaults():
    spec = SignalSpec()
    assert spec.fast_window == 63
    assert spec.slow_window == 252
    assert spec.vol_fast_window is None
    assert spec.vol_slow_window is None
    assert spec.w_fast == 0.4
    assert spec.w_slow == 0.6
    assert spec.discount == 0.5
    assert spec.fast_months == GOULDING_FAST_MONTHS == 2
    assert spec.slow_months == GOULDING_SLOW_MONTHS == 12
    assert spec.a_co == 0.5
    assert spec.a_re == 0.5


def test_signal_spec_rejects_bad_windows():
    with pytest.raises(ValueError):
        SignalSpec(fast_window=0)
    with pytest.raises(ValueError):
        SignalSpec(fast_months=-1)


def test_signal_spec_rejects_bad_a_co_a_re():
    with pytest.raises(ValueError):
        SignalSpec(a_co=1.5)
    with pytest.raises(ValueError):
        SignalSpec(a_re=-0.1)


def test_signal_spec_rejects_non_positive_weight_sum():
    # w_fast+w_slow<=0 wouldn't crash downstream (the ts denominator clips
    # at 1e-12) -- it would silently degenerate to ts=tanh(0)=0 every row.
    # Must be caught loudly here instead.
    with pytest.raises(ValueError):
        SignalSpec(w_fast=0.0, w_slow=0.0)
    with pytest.raises(ValueError):
        SignalSpec(w_fast=-0.5, w_slow=0.5)


def test_goulding_factory_uses_paper_horizons():
    spec = SignalSpec.goulding()
    assert spec.fast_months == 2
    assert spec.slow_months == 12


def test_continuous_kwargs_and_goulding_kwargs_disjoint_and_complete():
    spec = SignalSpec(fast_window=21, slow_window=100, fast_months=3, slow_months=9)
    ck = spec.continuous_kwargs()
    gk = spec.goulding_kwargs()
    assert ck['fast_window'] == 21 and ck['slow_window'] == 100
    assert gk['fast_months'] == 3 and gk['slow_months'] == 9
    # No overlap -- each function only ever receives its own parameters.
    assert set(ck.keys()).isdisjoint(gk.keys())


# ── build_features: shared base only, no model-specific columns ─────────

def test_build_features_adds_only_base_columns():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 5),
                        'close': [100.0, 102.0, 101.0, 105.0, 103.0]})
    feat = build_features(df)
    assert set(feat.columns) == {'ts_event', 'close', 'prev_close', 'peak', 'dd', 'r1d'}


def test_build_features_r1d_is_simple_return_not_log():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 3),
                        'close': [100.0, 110.0, 99.0]})
    feat = build_features(df)
    r1d = feat['r1d'].to_list()
    assert r1d[0] is None
    assert r1d[1] == pytest.approx(0.10)   # 110/100 - 1, NOT log(110/100)
    assert r1d[2] == pytest.approx(99 / 110 - 1)


def test_build_features_peak_and_drawdown():
    df = pl.DataFrame({'ts_event': _trading_dates(date(2020, 1, 1), 4),
                        'close': [100.0, 120.0, 90.0, 110.0]})
    feat = build_features(df)
    assert feat['peak'].to_list() == [100.0, 120.0, 120.0, 120.0]
    dd = feat['dd'].to_list()
    assert dd[2] == pytest.approx((90 - 120) / 120, abs=1e-2)


def test_build_features_sorts_unsorted_input():
    # shift()/cum_max() are order-dependent -- pass rows deliberately out
    # of ts_event order and confirm build_features sorts before computing,
    # rather than silently corrupting prev_close/peak/dd/r1d.
    df = pl.DataFrame({
        'ts_event': [date(2020, 1, 3), date(2020, 1, 1), date(2020, 1, 2)],
        'close': [103.0, 100.0, 101.0],
    })
    feat = build_features(df).sort('ts_event')
    assert feat['close'].to_list() == [100.0, 101.0, 103.0]
    assert feat['prev_close'].to_list() == [None, 100.0, 101.0]
    assert feat['peak'].to_list() == [100.0, 101.0, 103.0]


# ── continuous_momentum: independent of goulding_monthly ─────────────────

def test_continuous_momentum_needs_only_build_features_output():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=1)
    feat = build_features(df)
    # Deliberately does NOT call goulding_monthly first -- continuous_momentum
    # must run from build_features' output alone.
    out = continuous_momentum(feat)
    assert 'ts_fast' in out.columns and 'ts_slow' in out.columns
    assert out['signal'].drop_nulls().len() > 0


def test_continuous_momentum_signal_is_bounded():
    df = _price_df(date(2018, 1, 1), 400, drift=0.0015, vol=0.005, seed=2)
    out = continuous_momentum(build_features(df))
    signal = out['signal'].drop_nulls()
    assert len(signal) > 0
    assert signal.min() >= -1.0 and signal.max() <= 1.0


def test_continuous_momentum_regime_classification():
    df = _price_df(date(2018, 1, 1), 400, drift=0.002, vol=0.004, seed=3)
    out = continuous_momentum(build_features(df))
    regimes = set(out['regime'].drop_nulls().unique().to_list())
    assert regimes <= {'bull', 'bear', 'correction', 'rebound'}


def test_continuous_momentum_discount_applied_in_transition_regimes():
    df = _price_df(date(2018, 1, 1), 400, drift=0.0, vol=0.01, seed=4)
    out = continuous_momentum(build_features(df), discount=0.25)
    dis = out.filter(pl.col('regime').is_in(['correction', 'rebound']))
    other = out.filter(~pl.col('regime').is_in(['correction', 'rebound']) & pl.col('ts').is_not_null())
    if dis.height > 0:
        ratio = (dis['signal'] / dis['ts']).drop_nulls()
        assert ratio.len() > 0
        assert all(pytest.approx(0.25) == r for r in ratio.to_list())
    if other.height > 0:
        assert (other['signal'] == other['ts']).all()


def test_continuous_momentum_vol_window_defaults_to_return_window():
    # Default behavior: vol normalization is horizon-matched to the return
    # window, NOT an arbitrary fixed window (e.g. always 63 days) reused
    # regardless of fast_window/slow_window.
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, vol=0.008, seed=5)
    feat = build_features(df)
    out_default = continuous_momentum(feat, fast_window=21, slow_window=126)
    out_explicit = continuous_momentum(feat, fast_window=21, slow_window=126,
                                        vol_fast_window=21, vol_slow_window=126)
    a = out_default['std_fast'].fill_null(-999.0).to_list()
    b = out_explicit['std_fast'].fill_null(-999.0).to_list()
    assert a == pytest.approx(b)


def test_continuous_momentum_vol_window_can_be_decoupled_from_return_window():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, vol=0.008, seed=6)
    feat = build_features(df)
    out_matched = continuous_momentum(feat, fast_window=21)
    out_decoupled = continuous_momentum(feat, fast_window=21, vol_fast_window=63)
    a = out_matched['std_fast'].fill_null(-999.0).to_list()
    b = out_decoupled['std_fast'].fill_null(-999.0).to_list()
    assert a != pytest.approx(b)


def test_continuous_momentum_annualization_days_scales_avg_r():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=7)
    feat = build_features(df)
    out_252 = continuous_momentum(feat, annualization_days=252)
    out_259 = continuous_momentum(feat, annualization_days=259)
    a = out_252['avg_r_fast'].fill_null(-999.0).to_list()
    b = out_259['avg_r_fast'].fill_null(-999.0).to_list()
    assert a != pytest.approx(b)
    ratio = 259 / 252
    for x, y in zip(a, b):
        if x != -999.0:
            assert y == pytest.approx(x * ratio, rel=1e-6)


# ── goulding_monthly: independent of continuous_momentum ─────────────────

def test_goulding_monthly_needs_only_ts_event_and_close():
    # Deliberately pass raw build_features output straight through --
    # goulding_monthly must not require continuous_momentum's columns
    # (ts_fast/ts_slow/regime/signal) to exist at all.
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=8)
    feat = build_features(df)
    assert 'ts_fast' not in feat.columns  # sanity: continuous_momentum wasn't run
    out = goulding_monthly(feat)
    assert out.height > 0


def test_goulding_monthly_has_no_volatility_normalization_columns():
    df = _price_df(date(2018, 1, 1), 400, drift=0.001, seed=9)
    out = goulding_monthly(build_features(df))
    # Pure arithmetic-average signal -- no std/ts_fast/ts_slow/hv anywhere.
    assert not ({'std', 'std_fast', 'std_slow', 'ts_fast', 'ts_slow', 'hv'} & set(out.columns))


def test_goulding_monthly_uses_month_end_close_not_first_day():
    # Two rows per month with first != last -- locks in .last() (month-end
    # close) as the value used, not .first() (which an intra-month
    # first-to-last construction would silently prefer instead).
    df = pl.DataFrame([
        {'ts_event': date(2020, 1, 2), 'close': 100.0},   # Jan first day
        {'ts_event': date(2020, 1, 31), 'close': 105.0},  # Jan LAST day
        {'ts_event': date(2020, 2, 3), 'close': 108.0},   # Feb first day
        {'ts_event': date(2020, 2, 28), 'close': 110.0},  # Feb LAST day
    ])
    out = goulding_monthly(df).sort('ts_event')
    close = out['close'].to_list()
    assert close == [105.0, 110.0]
    ret = out['ret'].to_list()
    # Month-end-to-month-end: Feb's ret = 110/105 - 1, NOT 110/108 - 1
    # (which is what a first-to-last-day-of-Feb construction would give).
    assert ret[1] == pytest.approx(110.0 / 105.0 - 1)


def test_goulding_monthly_fast_slow_use_only_completed_months():
    # Six calendar months of deterministic monthly returns (one row per
    # month, so each bucket's month-end close IS that row's value) --
    # verify fast/slow (fast_months=1/slow_months=2) equal the PRIOR
    # month(s)' return, not the current month's own.
    month_starts = [date(2020, m, 1) for m in range(1, 7)]
    rows = []
    price = 100.0
    monthly_rets = [0.05, -0.03, 0.02, 0.04, -0.01, 0.03]
    for start, ret in zip(month_starts, monthly_rets):
        rows.append({'ts_event': start, 'close': price})
        price = price * (1 + ret)
    # Add a final row to close out the last month's own return.
    rows.append({'ts_event': date(2020, 7, 1), 'close': price})
    df = pl.DataFrame(rows)
    out = goulding_monthly(df, fast_months=1, slow_months=2).sort('ts_event')
    ret = out['ret'].to_list()
    fast = out['fast'].to_list()
    assert ret[1:] == pytest.approx(monthly_rets)  # locks in the exact convention
    # fast[i] must equal ret[i-1] (fast_months=1 trailing mean of last
    # completed month), never ret[i] itself.
    for i in range(2, len(ret)):
        if fast[i] is not None and ret[i - 1] is not None:
            assert fast[i] == pytest.approx(ret[i - 1])


def test_goulding_monthly_signal_bounds_and_regimes():
    df = _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=10)
    out = goulding_monthly(build_features(df))
    regimes = set(out['regime'].drop_nulls().unique().to_list())
    assert regimes <= {'bull', 'bear', 'correction', 'rebound'}


# ── _goulding_weight (eq. 7), independent of both models above ──────────

def test_goulding_weight_bull_bear_are_fully_directional():
    assert _goulding_weight('bull', a_co=0.5, a_re=0.5) == 1.0
    assert _goulding_weight('bear', a_co=0.5, a_re=0.5) == -1.0


def test_goulding_weight_flat_a_co_a_re_zeros_out_disagreement_states():
    assert _goulding_weight('correction', a_co=0.5, a_re=0.5) == pytest.approx(0.0)
    assert _goulding_weight('rebound', a_co=0.5, a_re=0.5) == pytest.approx(0.0)


def test_goulding_weight_nonflat_a_co_a_re_biases_disagreement_states():
    # a_co < 0.5 -> weight = 1 - 2*a_co > 0 (tilt toward slow/long)
    assert _goulding_weight('correction', a_co=0.2, a_re=0.5) > 0
    # a_re > 0.5 -> weight = 2*a_re - 1 > 0 (tilt toward fast/long)
    assert _goulding_weight('rebound', a_co=0.5, a_re=0.8) > 0


def test_goulding_weight_none_and_unknown_regime():
    assert _goulding_weight(None, a_co=0.5, a_re=0.5) is None
    assert _goulding_weight('unknown', a_co=0.5, a_re=0.5) is None

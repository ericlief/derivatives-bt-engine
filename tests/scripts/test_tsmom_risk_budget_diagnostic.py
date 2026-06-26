"""
Tests for scripts/tsmom_risk_budget_diagnostic.py -- covers only the parts
that don't require a live IB connection (signal_symbol resolution/
fallback logging, price-frame synchronization, and the weight-comparison/
report-formatting logic). fetch_continuous_bars/fetch_all_continuous_bars/
main are IB-dependent IO, exercised only by a manual run, per the brief.
"""

import numpy as np
import pandas as pd
import pytest

from scripts.tsmom_risk_budget_diagnostic import (
    build_comparison_report,
    compute_current_system_weights,
    compute_ewm_covariance,
    compute_erc_weights,
    compute_hrp_weights,
    compute_log_returns,
    correlation_view,
    log_signal_symbol_fallbacks,
    resolve_signal_symbol,
    summarize_divergence,
    synchronize_price_frames,
)


# ── resolve_signal_symbol / log_signal_symbol_fallbacks ─────────────────────

def test_resolve_signal_symbol_prefers_signal_symbol():
    instr = {'symbol': 'MZC', 'ib_symbol': 'MZC', 'signal_symbol': 'ZC'}
    assert resolve_signal_symbol(instr) == 'ZC'


def test_resolve_signal_symbol_falls_back_to_ib_symbol():
    instr = {'symbol': 'SIL', 'ib_symbol': 'SI'}
    assert resolve_signal_symbol(instr) == 'SI'


def test_resolve_signal_symbol_falls_back_to_symbol():
    instr = {'symbol': 'MES'}
    assert resolve_signal_symbol(instr) == 'MES'


def test_log_signal_symbol_fallbacks_only_returns_actual_substitutions():
    instruments = [
        {'symbol': 'MZC', 'ib_symbol': 'MZC', 'signal_symbol': 'ZC'},  # substituted: history comes from ZC, not MZC
        {'symbol': 'SIL', 'ib_symbol': 'SI'},     # NOT a history substitution -- SI is what's actually traded
        {'symbol': 'MES', 'ib_symbol': 'MES'},    # not substituted
        {'symbol': 'ES'},                          # not substituted
    ]
    fallbacks = log_signal_symbol_fallbacks(instruments)
    # SIL is excluded: its resolved symbol (SI) IS its traded ib_symbol --
    # that's a ticker-collision mapping, not a different security's
    # history being substituted in for a thin/short-history contract.
    assert fallbacks == {'MZC': 'ZC'}


# ── synchronize_price_frames ─────────────────────────────────────────────────

def _price_frame(dates: pd.DatetimeIndex, start: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({'date': dates, 'close': np.linspace(start, start + 10, len(dates))})


def test_synchronize_aligns_overlapping_mismatched_length_series():
    # A: 400 days from 2020-01-01. B: 400 days from 2020-06-01 (shifted,
    # deliberately mismatched start/end vs A) -- simulates one instrument
    # having a shorter/later-starting contract history than another, e.g.
    # a recently-listed micro vs its long-running full-size sibling.
    dates_a = pd.date_range('2020-01-01', periods=400, freq='D')
    dates_b = pd.date_range('2020-06-01', periods=400, freq='D')
    frames = {'A': _price_frame(dates_a), 'B': _price_frame(dates_b, start=50.0)}

    wide = synchronize_price_frames(frames, min_rows=100)

    assert list(wide.columns) == ['A', 'B']
    assert wide.index.is_monotonic_increasing
    # Identical date index across every column, by construction (inner join).
    assert wide['A'].notna().all() and wide['B'].notna().all()
    # Overlap is exactly the intersection of the two ranges.
    expected_overlap = len(pd.date_range('2020-06-01', dates_a[-1], freq='D'))
    assert len(wide) == expected_overlap


def test_synchronize_fallback_substituted_history_still_aligns():
    """The exact scenario the signal_symbol substitution exists for: a
    thin micro contract (short history) is replaced upstream by its full-
    size sibling's much longer history before this function ever sees it
    -- confirms that once substituted, a short series and a long series
    still align correctly via the same inner-join logic."""
    short_history = pd.date_range('2025-01-01', periods=50, freq='D')   # the micro's own (too short) history
    long_history = pd.date_range('2015-01-01', periods=3000, freq='D')  # the full-size substitute

    # Simulating the substitution having already happened upstream: the
    # "MZC" frame is actually built from ZC's long history, not its own.
    frames = {'MES': _price_frame(long_history), 'MZC': _price_frame(long_history, start=400.0)}
    wide = synchronize_price_frames(frames, min_rows=252)
    assert len(wide) == 3000

    # Without the substitution (using the micro's genuinely short history
    # directly), the same two instruments would fail to reach min_rows.
    frames_unsubstituted = {'MES': _price_frame(long_history), 'MZC': _price_frame(short_history)}
    with pytest.raises(ValueError):
        synchronize_price_frames(frames_unsubstituted, min_rows=252)


def test_synchronize_fails_loudly_on_no_overlap():
    frames = {
        'A': _price_frame(pd.date_range('2020-01-01', periods=10, freq='D')),
        'B': _price_frame(pd.date_range('2025-01-01', periods=10, freq='D')),
    }
    with pytest.raises(ValueError, match='has only 0 rows'):
        synchronize_price_frames(frames, min_rows=5)


def test_synchronize_fails_loudly_on_insufficient_window():
    # Plenty of individual history, but the OVERLAP is too short.
    frames = {
        'A': _price_frame(pd.date_range('2020-01-01', periods=400, freq='D')),
        'B': _price_frame(pd.date_range('2020-12-25', periods=400, freq='D')),  # only ~1wk overlap
    }
    with pytest.raises(ValueError) as exc_info:
        synchronize_price_frames(frames, min_rows=252)
    # Error names every instrument's own range, not just a generic message.
    assert 'A:' in str(exc_info.value) and 'B:' in str(exc_info.value)


def test_synchronize_fails_loudly_on_missing_instrument():
    frames = {'A': _price_frame(pd.date_range('2020-01-01', periods=100, freq='D')),
              'B': pd.DataFrame(columns=['date', 'close'])}
    with pytest.raises(ValueError, match='No bars available'):
        synchronize_price_frames(frames, min_rows=10)


def test_synchronize_empty_input_raises():
    with pytest.raises(ValueError):
        synchronize_price_frames({})


# ── compute_log_returns / compute_ewm_covariance ─────────────────────────────

def test_compute_log_returns_shape_and_values():
    prices = pd.DataFrame({'A': [100, 110, 121], 'B': [50, 50, 55]})
    returns = compute_log_returns(prices)
    assert len(returns) == 2  # one fewer row (diff)
    assert np.isclose(returns['A'].iloc[0], np.log(110 / 100))


def test_compute_ewm_covariance_symmetric_and_correct_shape():
    rng = np.random.default_rng(0)
    returns = pd.DataFrame(rng.normal(0, 0.01, (300, 3)), columns=['A', 'B', 'C'])
    cov = compute_ewm_covariance(returns, halflife=60)
    assert cov.shape == (3, 3)
    assert list(cov.columns) == ['A', 'B', 'C']
    np.testing.assert_allclose(cov.values, cov.values.T)  # symmetric
    assert (np.diag(cov.values) > 0).all()  # variances are positive


# ── compute_erc_weights ───────────────────────────────────────────────────────

def test_erc_weights_sum_to_one():
    cov = pd.DataFrame(np.diag([0.01, 0.02, 0.03]), index=['A', 'B', 'C'], columns=['A', 'B', 'C'])
    w = compute_erc_weights(cov)
    assert np.isclose(w.sum(), 1.0)
    assert (w >= 0).all()


def test_erc_equal_variance_uncorrelated_gives_equal_weights():
    cov = pd.DataFrame(np.diag([0.01, 0.01, 0.01]), index=['A', 'B', 'C'], columns=['A', 'B', 'C'])
    w = compute_erc_weights(cov)
    for symbol in ('A', 'B', 'C'):
        assert np.isclose(w[symbol], 1 / 3, atol=1e-4)


def test_erc_lower_variance_asset_gets_higher_weight():
    # Uncorrelated, unequal variance -- ERC should reduce to inverse-vol
    # weighting (the textbook special case), so the lowest-variance asset
    # (A) gets the highest weight.
    cov = pd.DataFrame(np.diag([0.01, 0.04, 0.09]), index=['A', 'B', 'C'], columns=['A', 'B', 'C'])
    w = compute_erc_weights(cov)
    assert w['A'] > w['B'] > w['C']
    inv_vol = pd.Series({'A': 1 / 0.1, 'B': 1 / 0.2, 'C': 1 / 0.3})
    expected = inv_vol / inv_vol.sum()
    pd.testing.assert_series_equal(w.sort_index(), expected.sort_index(), atol=1e-3, check_names=False)


# ── compute_hrp_weights ───────────────────────────────────────────────────────

def test_hrp_weights_sum_to_one_and_long_only():
    rng = np.random.default_rng(1)
    returns = pd.DataFrame(rng.normal(0, 0.01, (300, 4)), columns=['A', 'B', 'C', 'D'])
    w = compute_hrp_weights(returns)
    assert np.isclose(w.sum(), 1.0, atol=1e-6)
    assert (w >= 0).all()
    assert set(w.index) == {'A', 'B', 'C', 'D'}


# ── compute_current_system_weights ───────────────────────────────────────────

def test_current_system_weights_normalizes_abs_position_risk():
    targets = [
        {'symbol': 'A', 'position_risk': 100.0},
        {'symbol': 'B', 'position_risk': -300.0},  # sign shouldn't matter
        {'symbol': 'C', 'error': 'boom', 'position_risk': 999.0},  # excluded
    ]
    w = compute_current_system_weights(targets)
    assert np.isclose(w['A'], 0.25)
    assert np.isclose(w['B'], 0.75)
    assert 'C' not in w.index


def test_current_system_weights_all_zero_risk_is_no_op_not_division_error():
    targets = [{'symbol': 'A', 'position_risk': 0.0}, {'symbol': 'B', 'position_risk': 0.0}]
    w = compute_current_system_weights(targets)
    assert (w == 0.0).all()


# ── build_comparison_report / summarize_divergence ──────────────────────────

def test_build_comparison_report_columns_and_divergence_sign():
    instruments = [{'symbol': 'A', 'cluster': 'equity'}, {'symbol': 'B', 'cluster': 'grain'}]
    current_w = pd.Series({'A': 0.6, 'B': 0.4})
    erc_w = pd.Series({'A': 0.5, 'B': 0.5})
    hrp_w = pd.Series({'A': 0.55, 'B': 0.45})

    report = build_comparison_report(instruments, current_w, erc_w, hrp_w)

    assert list(report.columns) == [
        'cluster', 'current_weight', 'erc_weight', 'hrp_weight',
        'erc_minus_current', 'hrp_minus_current',
    ]
    assert report.loc['A', 'cluster'] == 'equity'
    # ERC wants LESS of A than the current system -> negative divergence.
    assert report.loc['A', 'erc_minus_current'] < 0
    assert report.loc['B', 'erc_minus_current'] > 0


def test_build_comparison_report_handles_symbol_missing_from_one_method():
    # An instrument ERC/HRP couldn't place a nonzero weight on at all
    # shouldn't crash the report -- just reads as 0.0 for that method.
    instruments = [{'symbol': 'A', 'cluster': 'equity'}]
    current_w = pd.Series({'A': 1.0})
    erc_w = pd.Series(dtype=float)  # empty -- A missing entirely
    hrp_w = pd.Series({'A': 1.0})
    report = build_comparison_report(instruments, current_w, erc_w, hrp_w)
    assert report.loc['A', 'erc_weight'] == 0.0


def test_summarize_divergence_aggregates_correctly():
    report = pd.DataFrame({
        'erc_minus_current': [-0.1, 0.1, 0.3],
        'hrp_minus_current': [0.0, 0.0, 0.0],
    })
    summary = summarize_divergence(report)
    assert np.isclose(summary['erc_mad'], (0.1 + 0.1 + 0.3) / 3)
    assert np.isclose(summary['erc_max_abs_dev'], 0.3)
    assert np.isclose(summary['hrp_mad'], 0.0)


# ── correlation_view ──────────────────────────────────────────────────────────

def test_correlation_view_diagonal_is_one_and_symmetric():
    rng = np.random.default_rng(2)
    returns = pd.DataFrame(rng.normal(0, 0.01, (300, 3)), columns=['A', 'B', 'C'])
    cov = compute_ewm_covariance(returns, halflife=60)
    corr = correlation_view(cov, cluster_by_symbol={'A': 'equity', 'B': 'grain', 'C': 'grain'})
    np.testing.assert_allclose(np.diag(corr.values), 1.0, atol=1e-9)
    np.testing.assert_allclose(corr.values, corr.values.T, atol=1e-9)


def test_correlation_view_sorted_by_cluster_then_symbol():
    cov = pd.DataFrame(np.eye(3) * 0.01, index=['Z', 'B', 'A'], columns=['Z', 'B', 'A'])
    cluster_by_symbol = {'Z': 'fx', 'B': 'grain', 'A': 'grain'}
    corr = correlation_view(cov, cluster_by_symbol)
    # Sorted by (cluster, symbol) -- 'fx' < 'grain' alphabetically, so Z's
    # cluster comes first; within 'grain', A before B.
    assert list(corr.index) == ['Z', 'A', 'B']

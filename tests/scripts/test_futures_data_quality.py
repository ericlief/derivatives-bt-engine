"""
Tests for scripts/futures_data_quality.py -- covers all pure (no-IB)
logic: stale-price detection, volume/liquidity gate, roll-date
contamination, listwise/pairwise deletion assessment, and the
recommendation builder. IO functions (fetch_ib_prices, load_db_prices,
main) require a live IB/DB connection and are exercised by a manual run.
"""

import numpy as np
import pandas as pd
import polars as pl
import pytest
from datetime import date, timedelta

from scripts.futures_data_quality import (
    _to_polars_wide,
    assess_deletion,
    build_recommendation,
    compute_log_returns,
    detect_low_volume,
    detect_roll_contamination,
    detect_stale_prices,
    ticker_cols,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dates(n: int, start: str = '2022-01-03') -> list[date]:
    d = date.fromisoformat(start)
    return [d + timedelta(days=i) for i in range(n)]


def _price_df(prices: dict[str, list[float]], n: int = None) -> pl.DataFrame:
    """Build a wide polars price DataFrame from per-ticker price lists."""
    dates = _dates(n or len(next(iter(prices.values()))))
    data = {'date': dates}
    data.update(prices)
    return pl.DataFrame(data).with_columns(pl.col('date').cast(pl.Date))


def _return_df(returns: dict[str, list[float]], n: int = None) -> pl.DataFrame:
    """Build a wide log-return DataFrame directly (first row is null per convention)."""
    dates = _dates(n or len(next(iter(returns.values()))))
    data = {'date': dates}
    data.update(returns)
    return pl.DataFrame(data).with_columns(pl.col('date').cast(pl.Date))


# ── _to_polars_wide ───────────────────────────────────────────────────────────

def test_to_polars_wide_accepts_polars():
    df = _price_df({'A': [100, 101, 102]})
    out = _to_polars_wide(df)
    assert isinstance(out, pl.DataFrame)
    assert 'date' in out.columns


def test_to_polars_wide_accepts_pandas():
    df = pd.DataFrame({'date': _dates(3), 'A': [100, 101, 102]})
    out = _to_polars_wide(df)
    assert isinstance(out, pl.DataFrame)
    assert out['date'].dtype == pl.Date


# ── compute_log_returns ───────────────────────────────────────────────────────

def test_compute_log_returns_first_row_null():
    prices = _price_df({'A': [100, 110, 121]})
    returns = compute_log_returns(prices)
    assert returns['A'][0] is None


def test_compute_log_returns_correct_value():
    prices = _price_df({'A': [100.0, 110.0]})
    returns = compute_log_returns(prices)
    assert np.isclose(returns['A'][1], np.log(110 / 100))


def test_compute_log_returns_preserves_date_col():
    prices = _price_df({'A': [100, 101, 102], 'B': [50, 51, 52]})
    returns = compute_log_returns(prices)
    assert 'date' in returns.columns
    assert ticker_cols(returns) == ['A', 'B']


# ── detect_stale_prices ───────────────────────────────────────────────────────

def test_stale_detection_no_stale():
    returns = _return_df({'A': [None, 0.01, -0.02, 0.03, 0.01]})
    result = detect_stale_prices(returns, run_threshold=3)
    assert result['total_flagged'] == 0
    assert result['flagged_tickers'] == {}


def test_stale_detection_run_below_threshold_not_flagged():
    # Two consecutive zeros, threshold is 3 -- should NOT be flagged.
    returns = _return_df({'A': [None, 0.01, 0.0, 0.0, 0.01]})
    result = detect_stale_prices(returns, run_threshold=3)
    assert result['total_flagged'] == 0


def test_stale_detection_run_exactly_at_threshold_is_flagged():
    returns = _return_df({'A': [None, 0.01, 0.0, 0.0, 0.0, 0.01]})
    result = detect_stale_prices(returns, run_threshold=3)
    assert result['flagged_tickers']['A'] == 3
    assert len(result['runs']['A']) == 1


def test_stale_detection_multiple_runs_per_instrument():
    returns = _return_df({'A': [None, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]})
    result = detect_stale_prices(returns, run_threshold=3)
    assert len(result['runs']['A']) == 2


def test_stale_detection_only_flags_the_correct_instrument():
    returns = _return_df({
        'A': [None, 0.0, 0.0, 0.0, 0.01],  # 3 zeros -> flagged
        'B': [None, 0.01, 0.02, -0.01, 0.01],  # clean
    })
    result = detect_stale_prices(returns, run_threshold=3)
    assert 'A' in result['flagged_tickers']
    assert 'B' not in result['flagged_tickers']


def test_stale_mask_shape_matches_returns():
    returns = _return_df({'A': [None, 0.0, 0.0, 0.0], 'B': [None, 0.01, 0.02, 0.03]})
    result = detect_stale_prices(returns, run_threshold=3)
    assert result['stale_mask'].shape == returns.shape


# ── detect_low_volume ─────────────────────────────────────────────────────────

def test_volume_check_no_volume_returns_limitation():
    prices = _price_df({'A': [100, 101]})
    result = detect_low_volume(prices, volume=None)
    assert result['limitation'] is not None
    assert result['low_vol_mask'] is None


def test_volume_check_flags_low_volume_rows():
    prices = _price_df({'A': [100.0, 101.0, 102.0, 103.0]})
    # Median volume = 50; threshold = 50 * 0.1 = 5. Volume of 2 on row 1 should be flagged.
    volume = _price_df({'A': [100.0, 2.0, 100.0, 100.0]})
    result = detect_low_volume(prices, volume=volume, volume_threshold_factor=0.1)
    assert result['low_vol_mask'] is not None
    flagged = result['low_vol_mask']['A'].to_list()
    assert flagged[1] is True
    assert all(not f for f in [flagged[0], flagged[2], flagged[3]])


def test_volume_check_computes_overlap_with_stale_mask():
    prices = _price_df({'A': [100.0, 101.0, 101.0, 101.0, 101.0]})
    returns = compute_log_returns(prices)
    stale = detect_stale_prices(returns, run_threshold=3)
    # Low volume on the same stale period.
    volume = _price_df({'A': [50.0, 1.0, 1.0, 1.0, 50.0]})
    result = detect_low_volume(prices, volume=volume, stale_result=stale, volume_threshold_factor=0.1)
    assert result['overlap_count'] is not None


# ── detect_roll_contamination ─────────────────────────────────────────────────

def test_roll_detection_clean_series_no_flags():
    # Small, normally-distributed returns -- nothing should be flagged.
    rng = np.random.default_rng(0)
    n = 200
    vals = rng.normal(0.0, 0.01, n).tolist()
    vals[0] = None
    returns = _return_df({'A': vals}, n=n)
    result = detect_roll_contamination(returns, outlier_sigma=5.0, trailing_window=60)
    # Allow a handful of false positives in a long series, but most should be clean.
    assert len(result['auto_detected']['A']) < 5


def test_roll_detection_flags_obvious_spike():
    n = 150
    vals = [0.001] * n
    vals[0] = None
    vals[100] = 0.5   # obvious outlier -- 50% return
    returns = _return_df({'A': vals}, n=n)
    result = detect_roll_contamination(returns, outlier_sigma=5.0, trailing_window=60)
    auto = result['auto_detected']['A']
    assert len(auto) >= 1


def test_roll_detection_provided_dates_also_flagged():
    n = 100
    vals = [0.001] * n
    vals[0] = None
    returns = _return_df({'A': vals}, n=n)
    roll_date = str(_dates(n)[50])
    result = detect_roll_contamination(returns, roll_dates={'A': [roll_date]},
                                        outlier_sigma=5.0, trailing_window=60)
    assert result['provided_rolls'] == {'A': [roll_date]}
    assert result['roll_mask']['A'][50] == True


def test_roll_detection_mask_has_correct_shape():
    n = 80
    vals = [0.001] * n
    vals[0] = None
    returns = _return_df({'A': vals, 'B': vals.copy()}, n=n)
    result = detect_roll_contamination(returns, trailing_window=60)
    assert result['roll_mask'].shape == returns.shape


# ── assess_deletion ───────────────────────────────────────────────────────────

def _clean_stale_roll(n: int = 200, tickers: list = None):
    """Return stale and roll results with no flags (all clean) for n rows."""
    tickers = tickers or ['A', 'B']
    dates = _dates(n)
    cols = {'date': dates}
    cols.update({t: [False] * n for t in tickers})
    clean_mask = pl.DataFrame(cols).with_columns(pl.col('date').cast(pl.Date))
    stale = {'stale_mask': clean_mask, 'runs': {t: [] for t in tickers},
             'flagged_tickers': {}, 'total_flagged': 0}
    roll = {'roll_mask': clean_mask.clone(), 'auto_detected': {t: [] for t in tickers},
            'provided_rolls': {}, 'outlier_returns': {}}
    return stale, roll


def test_assess_deletion_clean_data():
    rng = np.random.default_rng(1)
    n = 200
    r = rng.normal(0, 0.01, n)
    returns = _return_df({'A': r.tolist(), 'B': r.tolist()}, n=n)
    stale, roll = _clean_stale_roll(n=n)
    result = assess_deletion(returns, stale, roll)
    assert result['total_rows'] == n
    # Listwise rows = total - 1 null first row (from the diff).
    assert result['listwise_rows'] >= n - 2  # allow for NaN first row


def test_assess_deletion_consistent_pairwise_is_reliable():
    rng = np.random.default_rng(2)
    n = 200
    r = rng.normal(0, 0.01, n)
    returns = _return_df({'A': r.tolist(), 'B': r.tolist(), 'C': r.tolist()}, n=n)
    stale, roll = _clean_stale_roll(n=n, tickers=['A', 'B', 'C'])
    result = assess_deletion(returns, stale, roll, pairwise_n_tolerance=0.20)
    assert result['pairwise_reliable']


def test_assess_deletion_strategy_string():
    rng = np.random.default_rng(3)
    n = 100
    r = rng.normal(0, 0.01, n).tolist()
    returns = _return_df({'A': r, 'B': r}, n=n)
    stale, roll = _clean_stale_roll(n=n)
    result = assess_deletion(returns, stale, roll)
    assert 'listwise' in result['recommended_strategy']


# ── build_recommendation ──────────────────────────────────────────────────────

def _minimal_results():
    n = 100
    dates = _dates(n)
    mask = pl.DataFrame({'date': dates, 'A': [False]*n}).with_columns(pl.col('date').cast(pl.Date))
    stale = {'stale_mask': mask, 'runs': {'A': []}, 'flagged_tickers': {}, 'total_flagged': 0}
    volume = {'limitation': 'No volume data provided — Step 2 skipped; stale detection relies on zero-return runs only.',
              'low_vol_mask': None, 'overlap_count': None, 'genuine_zero_count': None}
    rolls = {'auto_detected': {'A': []}, 'provided_rolls': {}, 'outlier_returns': {}, 'roll_mask': mask}
    deletion = {'total_rows': n, 'listwise_rows': n - 1, 'listwise_pct': 99.0,
                'pairwise_n': {('A', 'B'): 99}, 'pairwise_n_cv': 0.0,
                'pairwise_reliable': True, 'recommended_strategy': 'listwise',
                'combined_bad_mask': pl.Series('bad', [False]*n)}
    return stale, volume, rolls, deletion


def test_build_recommendation_structure():
    stale, volume, rolls, deletion = _minimal_results()
    rec = build_recommendation(stale, volume, rolls, deletion, cov=None)
    assert 'DATA SOURCE RECOMMENDATION' in rec
    assert 'INSTRUMENTS TO REVIEW' in rec
    assert 'COVARIANCE MATRIX TO USE' in rec
    assert 'FLAGGED ISSUES' in rec


def test_build_recommendation_flags_stale_instrument():
    stale, volume, rolls, deletion = _minimal_results()
    stale['flagged_tickers'] = {'A': 5}
    stale['runs'] = {'A': [(_dates(10)[2], _dates(10)[6], 5)]}
    stale['total_flagged'] = 5
    rec = build_recommendation(stale, volume, rolls, deletion, cov=None)
    assert 'A' in rec
    assert 'zero returns' in rec.lower()


def test_build_recommendation_source_ib_when_no_db():
    stale, volume, rolls, deletion = _minimal_results()
    rec = build_recommendation(stale, volume, rolls, deletion, cov=None)
    assert 'Preferred source: IB' in rec


def test_build_recommendation_warns_on_unreliable_pairwise():
    stale, volume, rolls, deletion = _minimal_results()
    deletion['pairwise_reliable'] = False
    deletion['pairwise_n_cv'] = 0.35
    rec = build_recommendation(stale, volume, rolls, deletion, cov=None)
    assert 'WARNING' in rec or 'unreliable' in rec.lower() or 'UNRELIABLE' in rec

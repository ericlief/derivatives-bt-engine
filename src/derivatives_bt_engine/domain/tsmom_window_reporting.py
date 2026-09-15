"""Causal annual and windowed performance reports for one TSMOM backtest path.

The strategy is run once from its common training start through the final
date.  These helpers never rerun or reset it: every annual, expanding, and
capped-rolling row is calculated by slicing that same realised daily-return
stream.  Thus the signals, mixing estimates, positions, and transaction path
at a date are identical in every report that contains that date.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional

import polars as pl
from dateutil.relativedelta import relativedelta


WINDOW_COLUMNS = (
    'scheme', 'window_start', 'window_end', 'window_years', 'capital',
    'final_capital', 'rebalance_events', 'n_days', 'ann_ret_pct',
    'ann_vol_pct', 'sharpe', 'max_dd_pct', 'total_fees',
)


def _iso(value: date) -> str:
    return value.isoformat()


def _year_windows(oos_start: date, data_end: date) -> list[tuple[date, date]]:
    """Calendar-year OOS slices; the first/final slice may be partial."""
    windows = []
    start = oos_start
    while start <= data_end:
        end = min(date(start.year, 12, 31), data_end)
        windows.append((start, end))
        start = date(start.year + 1, 1, 1)
    return windows


def _expanding_windows(oos_start: date, data_end: date) -> list[tuple[date, date]]:
    return [(oos_start, end) for _, end in _year_windows(oos_start, data_end)]


def _capped_rolling_windows(oos_start: date, data_end: date,
                            max_width_years: int) -> list[tuple[date, date]]:
    """Expand to ``max_width_years``, then roll the return-report window.

    This is a reporting-window operation only.  In particular, the strategy's
    Goulding history remains its single causal, expanding history throughout.
    """
    if max_width_years < 1:
        raise ValueError('max_width_years must be at least one year')
    windows = []
    start = oos_start
    for width in range(1, max_width_years + 1):
        end = min(oos_start + relativedelta(years=width), data_end)
        windows.append((oos_start, end))
        if end == data_end:
            return windows
    start = oos_start + relativedelta(years=1)
    while start <= data_end:
        end = min(start + relativedelta(years=max_width_years), data_end)
        windows.append((start, end))
        if end == data_end:
            break
        start += relativedelta(years=1)
    return windows


def _daily_returns(daily_mtm: pl.DataFrame) -> pl.DataFrame:
    """Attach the realised one-day return before filtering any report window.

    Computing this on the full path first preserves the first day's realised
    return in a later rolling window, including the PnL of a position inherited
    from the preceding day.  Recomputing ``shift`` after a slice would lose it.
    """
    return daily_mtm.sort('date').with_columns(
        daily_return=pl.col('capital') / pl.col('capital').shift(1) - 1,
    )


def _fees_in_window(transactions: Optional[pl.DataFrame], start: date, end: date) -> float:
    if transactions is None or transactions.is_empty() or not {'date', 'fee'} <= set(transactions.columns):
        return 0.0
    return float(transactions.filter(
        pl.col('date').is_between(start, end, closed='both')
    )['fee'].sum() or 0.0)


def _events_in_window(events: Iterable[Mapping], start: date, end: date) -> int:
    return sum(1 for event in events if start <= event['date'] <= end)


def _score_window(returns: pl.DataFrame, events: Iterable[Mapping],
                  transactions: Optional[pl.DataFrame], *, scheme: str,
                  start: date, end: date, capital: float) -> dict:
    window = returns.filter(
        pl.col('date').is_between(start, end, closed='both') & pl.col('daily_return').is_not_null()
    )
    n_days = window.height
    if n_days:
        window = window.with_columns(growth=(1 + pl.col('daily_return')).cum_prod())
        window = window.with_columns(
            high_water=pl.max_horizontal(pl.lit(1.0), pl.col('growth').cum_max()),
        ).with_columns(drawdown_pct=(pl.col('growth') / pl.col('high_water') - 1) * 100)
        mean_ret, std_ret = window['daily_return'].mean(), window['daily_return'].std()
        ann_ret = (mean_ret or 0.0) * 252
        ann_vol = (std_ret or 0.0) * (252 ** .5)
        sharpe = ann_ret / ann_vol if ann_vol else None
        final_capital = capital * float(window['growth'][-1])
        max_dd_pct = float(window['drawdown_pct'].min())
    else:
        ann_ret = ann_vol = final_capital = max_dd_pct = None
        sharpe = None
    return {
        'scheme': scheme,
        'window_start': _iso(start),
        'window_end': _iso(end),
        'window_years': round((end - start).days / 365.25, 2),
        'capital': round(capital, 2),
        'final_capital': round(final_capital, 2) if final_capital is not None else None,
        'rebalance_events': _events_in_window(events, start, end),
        'n_days': n_days,
        'ann_ret_pct': round(ann_ret * 100, 2) if ann_ret is not None else None,
        'ann_vol_pct': round(ann_vol * 100, 2) if ann_vol is not None else None,
        'sharpe': round(sharpe, 2) if sharpe is not None else None,
        'max_dd_pct': round(max_dd_pct, 2) if max_dd_pct is not None else None,
        'total_fees': round(_fees_in_window(transactions, start, end), 2),
    }


def score_causal_windows(result: Mapping, *, initial_capital: float,
                         oos_start: date, max_width_years: int = 5) -> pl.DataFrame:
    """Score annual, B-expanding, and C-capped rolling slices of one run.

    ``oos_start`` is a shared evaluation boundary supplied by the caller, not
    an inferred first-trade date.  That makes all grid parameter combinations
    comparable even where a parameter changes the strategy's warmup/trading
    timing.
    """
    daily_mtm = result['daily_mtm']
    if daily_mtm.is_empty():
        return pl.DataFrame(schema={column: pl.Null for column in WINDOW_COLUMNS})
    data_end = daily_mtm['date'].max()
    if oos_start > data_end:
        raise ValueError(f'oos_start {oos_start} is after backtest data end {data_end}')
    returns = _daily_returns(daily_mtm)
    events = result.get('trend_signals', [])
    transactions = result.get('transactions')
    windows_by_scheme = (
        ('A_annual_oos', _year_windows(oos_start, data_end)),
        ('B_expanding', _expanding_windows(oos_start, data_end)),
        ('C_capped_rolling', _capped_rolling_windows(oos_start, data_end, max_width_years)),
    )
    rows = [
        _score_window(returns, events, transactions, scheme=scheme, start=start,
                      end=end, capital=initial_capital)
        for scheme, windows in windows_by_scheme
        for start, end in windows
    ]
    return pl.DataFrame(rows).select(WINDOW_COLUMNS)


def summarize_causal_windows(window_metrics: pl.DataFrame) -> pl.DataFrame:
    """Stability summary; overlapping B/C rows are diagnostics, not IID data."""
    return (
        window_metrics.filter(pl.col('sharpe').is_not_null())
        .group_by('scheme')
        .agg(
            pl.len().alias('n_windows'),
            pl.col('sharpe').mean().round(3).alias('sharpe_mean'),
            pl.col('sharpe').std().round(3).alias('sharpe_std'),
            pl.col('sharpe').min().round(3).alias('sharpe_min'),
            pl.col('sharpe').max().round(3).alias('sharpe_max'),
            pl.col('ann_ret_pct').mean().round(3).alias('ann_ret_pct_mean'),
            pl.col('ann_vol_pct').mean().round(3).alias('ann_vol_pct_mean'),
            pl.col('max_dd_pct').mean().round(3).alias('max_dd_pct_mean'),
        )
        .sort('scheme')
    )

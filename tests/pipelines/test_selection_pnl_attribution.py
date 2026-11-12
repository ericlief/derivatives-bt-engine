"""Tests for selected-book filtering in notebook P&L attribution charts."""

from datetime import date

import polars as pl

from derivatives_bt_engine.pipelines.ewmac_notebook import (
    _prepare_selection_pnl_attribution_year,
)


def test_attribution_year_contains_only_symbols_chosen_during_that_year():
    """The chart axis excludes screened and prior-book-only instruments."""
    frame = pl.DataFrame({
        "date": [date(2026, 1, 2)] * 4 + [date(2026, 2, 2)],
        "year": [2026] * 5,
        "symbol": ["NEW", "ZERO", "OLD", "SCREENED", "NEW"],
        "sel_active": [True, True, False, False, False],
        "net_pnl": [10.0, 0.0, -4.0, 0.0, 3.0],
    })

    result = _prepare_selection_pnl_attribution_year(frame, year=2026)

    assert result.get_column("symbol").unique().sort().to_list() == [
        "NEW",
        "ZERO",
    ]
    assert result.filter(pl.col("symbol") == "NEW").height == 2
    assert result.filter(pl.col("symbol") == "NEW")["net_pnl"].sum() == 13.0


def test_attribution_year_uses_union_of_in_year_books():
    """Monthly or other in-year book changes retain every chosen market."""
    frame = pl.DataFrame({
        "date": [date(2026, 1, 2), date(2026, 2, 2), date(2026, 2, 3)],
        "year": [2026, 2026, 2026],
        "symbol": ["JAN", "FEB", "JAN"],
        "sel_active": [True, True, False],
        "net_pnl": [1.0, 2.0, -0.5],
    })

    result = _prepare_selection_pnl_attribution_year(frame, year=2026)

    assert result.get_column("symbol").unique().sort().to_list() == [
        "FEB",
        "JAN",
    ]
    assert result.filter(pl.col("symbol") == "JAN")["net_pnl"].sum() == 0.5

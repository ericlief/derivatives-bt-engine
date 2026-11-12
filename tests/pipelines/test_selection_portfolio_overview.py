"""Tests for cap-aware selection portfolio notebook overview calculations."""

from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.ewmac_notebook import (
    _prepare_selection_portfolio_overview_frame,
    plot_selection_portfolio_overview,
)


def _portfolio_curve(cap_mode: str) -> pl.DataFrame:
    """Return a small portfolio curve with one invalid report observation."""
    return pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "port_gross_equity": [1_000.0, 1_010.0, 1_020.0, 1_020.0, 1_030.0],
        "port_net_equity": [1_000.0, 1_010.0, 1_020.0, 1_020.0, 1_030.0],
        "port_net": [100.0, 200.0, 300.0, 9_999.0, 400.0],
        "port_net_pct": [1.0, 2.0, 3.0, 99.0, 4.0],
        "ret_valid": [True, True, True, False, True],
        "cap_mode": [cap_mode] * 5,
    })


def test_fixed_return_is_additive_over_valid_observations():
    """Fixed-cap trailing return sums contributions and skips invalid rows."""
    frame, cap_mode = _prepare_selection_portfolio_overview_frame(
        _portfolio_curve("fixed"),
        rolling_days=3,
    )

    assert cap_mode == "fixed"
    assert frame["rolling_return_pct"][:2].to_list() == [None, None]
    assert frame["rolling_return_pct"][2] == pytest.approx(6.0)
    assert frame["rolling_return_pct"][3] == pytest.approx(6.0)
    assert frame["rolling_return_pct"][4] == pytest.approx(9.0)
    assert frame["rolling_realized_vol_pct"][2] == pytest.approx(16.0)
    assert frame["rolling_realized_vol_pct"][3] == pytest.approx(16.0)
    assert frame["rolling_realized_dvol"][2] == pytest.approx(1_600.0)


def test_compounded_return_uses_valid_lagged_equity_returns():
    """Compounded trailing return geometrically combines valid observations."""
    curve = _portfolio_curve("compounded").with_columns(
        pl.Series("port_net_pct", [10.0, -10.0, 5.0, 99.0, 20.0])
    )

    frame, cap_mode = _prepare_selection_portfolio_overview_frame(
        curve,
        rolling_days=3,
    )

    assert cap_mode == "compounded"
    assert frame["rolling_return_pct"][:2].to_list() == [None, None]
    assert frame["rolling_return_pct"][2] == pytest.approx(3.95)
    assert frame["rolling_return_pct"][3] == pytest.approx(3.95)
    assert frame["rolling_return_pct"][4] == pytest.approx(13.4)
    expected_vol = pl.Series([10.0, -10.0, 5.0]).std() * 16.0
    assert frame["rolling_realized_vol_pct"][2] == pytest.approx(expected_vol)


def test_mixed_cap_modes_are_rejected():
    """One plot cannot silently combine incompatible return denominators."""
    curve = _portfolio_curve("fixed").with_columns(
        pl.Series(
            "cap_mode",
            ["fixed", "fixed", "compounded", "compounded", "compounded"],
        )
    )

    with pytest.raises(ValueError, match="exactly one cap_mode"):
        _prepare_selection_portfolio_overview_frame(curve, rolling_days=3)


def test_legacy_curve_without_cap_mode_uses_historical_compounded_mode():
    """Reports predating fixed-cap support remain readable as compounded."""
    curve = _portfolio_curve("compounded").drop("cap_mode")

    _, cap_mode = _prepare_selection_portfolio_overview_frame(
        curve,
        rolling_days=3,
    )

    assert cap_mode == "compounded"


@pytest.mark.parametrize(
    ("cap_mode", "cap_label", "return_phrase", "volatility_phrase"),
    [
        (
            "fixed",
            "Fixed-cap",
            "additive net return contribution",
            "return-contribution volatility",
        ),
        (
            "compounded",
            "Compounded",
            "compounded net return",
            "lagged-equity return volatility",
        ),
    ],
)
def test_plot_titles_disclose_cap_mode_and_return_denominator(
    cap_mode: str,
    cap_label: str,
    return_phrase: str,
    volatility_phrase: str,
):
    """Return and volatility panels identify their cap-dependent semantics."""
    plot = plot_selection_portfolio_overview(
        _portfolio_curve(cap_mode),
        rolling_days=3,
    )
    panels = list(plot.values())
    return_title = panels[2].opts.get(backend="bokeh").kwargs["title"]
    volatility_title = panels[3].opts.get(backend="bokeh").kwargs["title"]

    assert cap_label in return_title
    assert cap_label in volatility_title
    assert return_phrase in return_title
    assert volatility_phrase in volatility_title

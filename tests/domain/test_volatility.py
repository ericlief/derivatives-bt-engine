import pandas as pd
import polars as pl
import pytest

from derivatives_bt_engine.domain.volatility import carver_mixed_point_volatility


def test_carver_mixed_point_volatility_matches_direct_polars_formula():
    frame = pl.DataFrame({"pt_change_1d": [None] + [(-1.0) ** i * (i % 7 + 1) for i in range(1, 90)]})

    result = carver_mixed_point_volatility(
        frame,
        annualization_days=252,
        fast_span=32,
        slow_years=10,
        slow_weight=0.3,
        min_samples=10,
    )
    expected_fast = frame["pt_change_1d"].ewm_std(span=32, adjust=True, min_samples=10)
    expected_slow = expected_fast.ewm_mean(span=2520, adjust=True, min_samples=1)
    expected_mixed = expected_fast * 0.7 + expected_slow * 0.3

    assert result["fast_point_vol"].to_list() == pytest.approx(expected_fast.to_list())
    assert result["slow_point_vol"].to_list() == pytest.approx(expected_slow.to_list())
    assert result["mixed_point_vol"].to_list() == pytest.approx(expected_mixed.to_list())
    assert result["fast_vol_span"][-1] == 32
    assert result["slow_vol_span"][-1] == 2520
    assert result["slow_vol_weight"][-1] == pytest.approx(0.3)


def test_carver_mixed_point_volatility_matches_pysystemtrade_pandas_mechanics():
    changes = [None] + [(-1.0) ** i * (i % 7 + 1) for i in range(1, 90)]
    result = carver_mixed_point_volatility(
        pl.DataFrame({"pt_change_1d": changes}),
        annualization_days=252,
        fast_span=32,
        slow_years=10,
        slow_weight=0.3,
        min_samples=10,
    )
    pandas_changes = pd.Series(changes, dtype=float)
    expected_fast = pandas_changes.ewm(
        span=32, adjust=True, min_periods=10
    ).std()
    expected_slow = expected_fast.ewm(
        span=2520, adjust=True, min_periods=1
    ).mean()

    assert result["fast_point_vol"].to_list()[10:] == pytest.approx(
        expected_fast.to_list()[10:]
    )
    assert result["slow_point_vol"].to_list()[10:] == pytest.approx(
        expected_slow.to_list()[10:]
    )


def test_carver_mixed_point_volatility_exposes_short_slow_history():
    result = carver_mixed_point_volatility(
        pl.DataFrame({"change": list(range(30))}),
        point_change_col="change",
        annualization_days=250,
        min_samples=10,
    )

    assert result["vol_observations"][-1] == 21
    assert result["slow_history_years"][-1] == pytest.approx(21 / 250)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"annualization_days": 0}, "annualization_days"),
        ({"fast_span": 1}, "fast_span"),
        ({"slow_years": 0}, "slow_years"),
        ({"slow_weight": 1.1}, "slow_weight"),
        ({"min_samples": 1}, "min_samples"),
    ],
)
def test_carver_mixed_point_volatility_validates_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        carver_mixed_point_volatility(pl.DataFrame({"pt_change_1d": [1.0, 2.0]}), **kwargs)

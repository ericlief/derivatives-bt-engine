"""Tests for annual subsystem cost composition."""

import pytest

from derivatives_bt_engine.calculations.costs import calculate_annual_sr_cost


def test_annual_sr_cost_keeps_trades_and_complete_rolls_separate():
    cost = calculate_annual_sr_cost(
        trade_sr=0.002,
        roll_sr=0.003,
        subsystem_turnover=12.0,
        ann_rolls=4.0,
    )

    assert cost.ann_trade_cost_sr == pytest.approx(0.024)
    assert cost.ann_roll_cost_sr == pytest.approx(0.012)
    assert cost.ann_cost_sr == pytest.approx(0.036)


@pytest.mark.parametrize("name", ["trade_sr", "roll_sr", "turnover", "rolls"])
def test_annual_sr_cost_rejects_negative_inputs(name):
    values = {
        "trade_sr": 0.002,
        "roll_sr": 0.003,
        "subsystem_turnover": 12.0,
        "ann_rolls": 4.0,
    }
    key = {
        "trade_sr": "trade_sr",
        "roll_sr": "roll_sr",
        "turnover": "subsystem_turnover",
        "rolls": "ann_rolls",
    }[name]
    values[key] = -1.0

    with pytest.raises(ValueError, match="nonnegative"):
        calculate_annual_sr_cost(**values)

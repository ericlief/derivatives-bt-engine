import numpy as np
import pytest

from derivatives_bt_engine.calculations.instrument_selection import (
    EqualPortfolioWeights,
    GreedyInstrumentSelector,
    GreedySelectionConfig,
    SelectionCandidate,
)


def _candidate(symbol, turnover, *, family=""):
    return SelectionCandidate(
        symbol=symbol,
        ann_dvol=100.0,
        trade_sr=0.01,
        combined_turnover=turnover,
        econ_family=family,
        mkt_risk_vol_day=1_000_000_000.0,
    )


def test_scoring_uses_combined_forecast_turnover_cost():
    candidate = _candidate("A", 12.5)
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["A"], {"A": candidate}, ["A"], np.eye(1), starting=True
    )

    detail = score.details[0]
    assert candidate.annual_trade_cost_sr == pytest.approx(0.125)
    assert detail["ann_trade_cost_sr"] == pytest.approx(0.125)
    assert detail["net_sr"] < 0.5


def test_greedy_selector_stops_when_best_addition_falls_below_threshold():
    candidates = [
        _candidate("A", 0.0),
        _candidate("B", 0.0),
        _candidate("C", 150.0),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    result = selector.select(candidates, np.eye(3))

    assert set(result.selected) == {"A", "B"}
    assert "C" not in result.selected
    c_final_trial = [
        row for row in result.trials
        if row["candidate"] == "C" and row["iteration"] == 2
    ][0]
    assert c_final_trial["accepted"] is False


def test_greedy_selector_allows_only_one_instrument_per_economic_family():
    candidates = [
        _candidate("CORN", 0.0, family="CORN_RELATED"),
        _candidate("MZC", 0.0, family="CORN_RELATED"),
        _candidate("SOY", 0.0, family="SOY_RELATED"),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
        weight_policy=EqualPortfolioWeights(),
    )

    result = selector.select(candidates, np.eye(3))

    assert len({"CORN", "MZC"}.intersection(result.selected)) == 1

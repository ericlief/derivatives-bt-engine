import numpy as np
import pytest

from derivatives_bt_engine.calculations.instrument_selection import (
    EqualPortfolioWeights,
    GreedyInstrumentSelector,
    GreedySelectionConfig,
    HandcraftedPortfolioWeights,
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


def test_handcrafted_weights_expose_recursive_cluster_paths():
    """Similar correlation profiles should share an auditable terminal path."""
    symbols = ["ES", "NQ", "GC", "SI"]
    correlation = np.array([
        [1.00, 0.90, 0.10, 0.05],
        [0.90, 1.00, 0.12, 0.08],
        [0.10, 0.12, 1.00, 0.80],
        [0.05, 0.08, 0.80, 1.00],
    ])

    allocation = HandcraftedPortfolioWeights().allocate(symbols, correlation)

    assert sum(allocation.weights.values()) == pytest.approx(1.0)
    assert allocation.cluster_paths["ES"] == allocation.cluster_paths["NQ"]
    assert allocation.cluster_paths["GC"] == allocation.cluster_paths["SI"]
    assert allocation.cluster_paths["ES"] != allocation.cluster_paths["GC"]


def test_greedy_selector_saves_member_weights_for_every_trial():
    """Every summary trial should have a complete long-form book allocation."""
    candidates = [
        _candidate("A", 0.0),
        _candidate("B", 0.0),
        _candidate("C", 0.0),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
        weight_policy=EqualPortfolioWeights(),
    )

    result = selector.select(candidates, np.eye(3))

    for trial in result.trials:
        member_rows = [
            row
            for row in result.trial_allocations
            if row["iteration"] == trial["iteration"]
            and row["candidate"] == trial["candidate"]
        ]
        assert {row["book_symbol"] for row in member_rows} == set(
            str(trial["book"]).split(",")
        )
        assert sum(float(row["weight"]) for row in member_rows) == pytest.approx(1.0)
        assert {row["cluster_path"] for row in member_rows} == {"1"}
        assert {row["accepted"] for row in member_rows} == {trial["accepted"]}

import numpy as np
import pytest

from derivatives_bt_engine.calculations.instrument_selection import (
    EqualPortfolioWeights,
    GreedyInstrumentSelector,
    GreedySelectionConfig,
    HandcraftedPortfolioWeights,
    SelectionCandidate,
    TopDownClassificationWeights,
)


def _candidate(
    symbol,
    turnover,
    *,
    family="",
    asset_class="equity",
    economic_subclass="us",
):
    return SelectionCandidate(
        symbol=symbol,
        ann_dvol=100.0,
        trade_sr=0.01,
        combined_turnover=turnover,
        asset_class=asset_class,
        economic_subclass=economic_subclass,
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
    assert c_final_trial["reason"] == "best_score_below_acceptance_threshold"
    assert c_final_trial["iter_best"] == "C"
    assert (
        c_final_trial["iter_best_score"]
        < c_final_trial["min_accept_score"]
    )


def test_singleton_losers_record_competitive_rejection_reason():
    """Feasible iteration-zero losers identify the selected seed and score."""
    candidates = [
        _candidate("CHEAP", 0.0),
        _candidate("COSTLY", 10.0),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
        weight_policy=EqualPortfolioWeights(),
    )

    result = selector.select(candidates, np.eye(2))

    seed_rows = [row for row in result.trials if row["iteration"] == 0]
    selected = next(row for row in seed_rows if row["accepted"])
    rejected = next(row for row in seed_rows if not row["accepted"])
    assert selected["candidate"] == "CHEAP"
    assert selected["reason"] == ""
    assert rejected["feasible"]
    assert rejected["reason"] == "lower_rank_than_selected_seed"
    assert rejected["iter_best"] == "CHEAP"
    assert rejected["iter_best_score"] == pytest.approx(selected["score"])
    rejected_allocations = [
        row for row in result.trial_allocations
        if row["iteration"] == 0 and row["candidate"] == "COSTLY"
    ]
    assert {row["reason"] for row in rejected_allocations} == {
        "lower_rank_than_selected_seed"
    }


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


def test_greedy_selector_caps_selected_instruments_per_economic_subclass():
    """A static cluster cap prevents an extra grain entering the book."""
    candidates = [
        _candidate("CORN", 0.0, economic_subclass="grains"),
        _candidate("WHEAT", 0.0, economic_subclass="grains"),
        _candidate("SOY", 0.0, economic_subclass="grains"),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.0,
            max_instruments_per_cluster=2,
        )
    )

    result = selector.select(candidates, np.eye(3))

    assert len(result.selected) == 2
    capped_trial = next(
        row for row in result.trials
        if row["iteration"] == 2 and row["candidate"] not in result.selected
    )
    assert not capped_trial["feasible"]
    assert capped_trial["score"] is None
    assert capped_trial["reason"] == (
        "cluster_instr_limit:grains n=3 max=2"
    )


def test_cluster_instrument_limit_must_be_positive():
    """Zero cannot express a useful per-cluster instrument limit."""
    with pytest.raises(ValueError, match="max_instruments_per_cluster"):
        GreedySelectionConfig(max_instruments_per_cluster=0)


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


def test_handcrafted_weights_match_pysystemtrade_nested_group_result():
    """Nested child weights must retain their raw scale at the parent node.

    These golden values reproduce pysystemtrade's ``handcraftPortfolio`` for
    the same four-instrument correlation matrix. One top-level branch contains
    three instruments, so normalizing its recursive weights inside the parent
    IDM would produce materially different answers and fail this test.
    """
    symbols = ["S0", "S1", "S2", "S3"]
    correlation = np.array([
        [1.0, 0.44019574, 0.62537716, 0.55906521],
        [0.44019574, 1.0, 0.54820557, 0.45648545],
        [0.62537716, 0.54820557, 1.0, 0.62784243],
        [0.55906521, 0.45648545, 0.62784243, 1.0],
    ])

    allocation = HandcraftedPortfolioWeights().allocate(symbols, correlation)

    assert allocation.weights == pytest.approx({
        "S0": 0.25454610712397285,
        "S1": 0.4633069978592112,
        "S2": 0.14107344750840803,
        "S3": 0.14107344750840803,
    })
    assert allocation.cluster_paths == {
        "S0": "1.2",
        "S1": "2",
        "S2": "1.1",
        "S3": "1.1",
    }


def test_top_down_classification_weights_equalize_each_tree_level():
    """Crowded subclasses must not consume extra top-level portfolio risk."""
    symbols = ["CORN", "WHEAT", "COCOA", "ES", "NIKKEI"]
    policy = TopDownClassificationWeights({
        "CORN": ("ags", "grains"),
        "WHEAT": ("ags", "grains"),
        "COCOA": ("ags", "softs"),
        "ES": ("equity", "us"),
        "NIKKEI": ("equity", "ex_us"),
    })

    allocation = policy.allocate(symbols, np.eye(len(symbols)))

    assert allocation.weights == pytest.approx({
        "CORN": 0.125,
        "WHEAT": 0.125,
        "COCOA": 0.25,
        "ES": 0.25,
        "NIKKEI": 0.25,
    })
    assert allocation.cluster_paths == {
        "CORN": "ags/grains",
        "WHEAT": "ags/grains",
        "COCOA": "ags/softs",
        "ES": "equity/us",
        "NIKKEI": "equity/ex_us",
    }


def test_selector_defaults_to_top_down_classification_weights():
    candidates = [
        _candidate("CORN", 0.0, asset_class="ags", economic_subclass="grains"),
        _candidate("WHEAT", 0.0, asset_class="ags", economic_subclass="grains"),
        _candidate("ES", 0.0, asset_class="equity", economic_subclass="us"),
    ]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0)
    )

    score = selector.score(
        [candidate.symbol for candidate in candidates],
        {candidate.symbol: candidate for candidate in candidates},
        [candidate.symbol for candidate in candidates],
        np.eye(3),
    )

    assert score.weights == pytest.approx({
        "CORN": 0.25,
        "WHEAT": 0.25,
        "ES": 0.50,
    })
    assert score.cluster_paths["CORN"] == "ags/grains"
    assert score.cluster_paths["ES"] == "equity/us"


def test_selection_idm_retains_signed_correlations():
    """AFTS final-book IDM uses signed H rather than the production zero floor."""
    candidates = [_candidate("A", 0.0), _candidate("B", 0.0)]
    correlation = np.array([
        [1.0, -0.5],
        [-0.5, 1.0],
    ])
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
    )

    score = selector.score(
        ["A", "B"],
        {candidate.symbol: candidate for candidate in candidates},
        ["A", "B"],
        correlation,
    )

    assert score.weights == pytest.approx({"A": 0.5, "B": 0.5})
    assert score.idm == pytest.approx(2.0)


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
        assert {
            row["cluster_members"] for row in member_rows
        } == {str(trial["book"])}
        assert all(
            float(row["cluster_weight"]) == pytest.approx(1.0)
            for row in member_rows
        )
        assert {row["accepted"] for row in member_rows} == {trial["accepted"]}

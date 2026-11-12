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
    initial_margin_usd=None,
    is_micro=False,
):
    return SelectionCandidate(
        symbol=symbol,
        ann_dvol=100.0,
        trade_sr=0.01,
        combined_turnover=turnover,
        asset_class=asset_class,
        economic_subclass=economic_subclass,
        econ_family=family,
        is_micro_contract=is_micro,
        mkt_risk_vol_day=1_000_000_000.0,
        initial_margin_usd=initial_margin_usd,
    )


def test_full_contract_preference_suppresses_feasible_micro_family_route():
    """A feasible full contract wins its family even with a lower raw score."""
    candidates = [
        _candidate("MICRO", 0.0, family="INDEX", is_micro=True),
        _candidate("FULL", 20.0, family="INDEX"),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            size_penalty_scale=0.0,
            prefer_full_contracts=True,
        )
    ).select(candidates, np.eye(2))

    assert result.selected == ("FULL",)
    micro_trial = next(
        row for row in result.trials if row["candidate"] == "MICRO"
    )
    assert micro_trial["reason"] == (
        "micro_suppressed_by_full_contract_preference:INDEX"
    )


def test_disabled_full_contract_preference_preserves_score_choice():
    """The default leaves full and micro family routes to ordinary scoring."""
    candidates = [
        _candidate("MICRO", 0.0, family="INDEX", is_micro=True),
        _candidate("FULL", 20.0, family="INDEX"),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            size_penalty_scale=0.0,
        )
    ).select(candidates, np.eye(2))

    assert result.selected == ("MICRO",)


def test_full_contract_preference_keeps_micro_when_full_is_infeasible():
    """The preference never replaces an executable micro with a failed full."""
    candidates = [
        _candidate(
            "MICRO",
            0.0,
            family="INDEX",
            is_micro=True,
            initial_margin_usd=100.0,
        ),
        _candidate(
            "FULL",
            0.0,
            family="INDEX",
            initial_margin_usd=None,
        ),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            size_penalty_scale=0.0,
            max_instrument_margin_pct=20.0,
            prefer_full_contracts=True,
        )
    ).select(candidates, np.eye(2))

    assert result.selected == ("MICRO",)


def test_score_hysteresis_can_retain_incumbent_micro_after_full_preference():
    """Full preference proposes the route; score hysteresis controls turnover."""
    candidates = [
        _candidate("MICRO", 0.0, family="INDEX", is_micro=True),
        _candidate("FULL", 20.0, family="INDEX"),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            size_penalty_scale=0.0,
            prefer_full_contracts=True,
            membership_hysteresis="score",
            book_score_buffer_percent=2.0,
        )
    ).select(candidates, np.eye(2), prior_selected=["MICRO"])

    assert result.unbuffered_selected == ("FULL",)
    assert result.selected == ("MICRO",)
    assert result.switch_reason == "net_score_gain_below_buffer"


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


def test_scoring_reports_contract_volume_and_roll_cash_cost_without_ranking_it():
    """Cash diagnostics expose scale while AFTS net SR remains trade-only."""
    candidate = SelectionCandidate(
        symbol="A",
        ann_dvol=100.0,
        trade_sr=0.01,
        combined_turnover=10.0,
        asset_class="equity",
        economic_subclass="us",
        mkt_risk_vol_day=1_000_000_000.0,
        roll_sr=0.02,
        ann_rolls=4.0,
    )
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=10_000.0,
            target_vol=0.20,
            size_penalty_scale=0.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["A"], {"A": candidate}, ["A"], np.eye(1), starting=True
    )

    detail = score.details[0]
    assert detail["average_position"] == pytest.approx(4.0)
    assert detail["ann_trade_cons"] == pytest.approx(40.0)
    assert detail["ann_roll_cons"] == pytest.approx(16.0)
    assert detail["ann_trade_cost_usd"] == pytest.approx(40.0)
    assert detail["ann_roll_cost_usd"] == pytest.approx(32.0)
    assert detail["ann_cost_usd"] == pytest.approx(72.0)
    assert detail["roll_cost_in_score"] is False
    assert detail["score_cost_sr"] == pytest.approx(0.10)
    assert detail["net_sr"] == pytest.approx(0.40)

    with_rolls = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=10_000.0,
            target_vol=0.20,
            size_penalty_scale=0.0,
            include_roll_cost_in_score=True,
        ),
        weight_policy=EqualPortfolioWeights(),
    ).score(["A"], {"A": candidate}, ["A"], np.eye(1), starting=True)

    roll_detail = with_rolls.details[0]
    assert roll_detail["roll_cost_in_score"] is True
    assert roll_detail["score_cost_sr"] == pytest.approx(0.18)
    assert roll_detail["net_sr"] == pytest.approx(0.32)

    # Persisted per-member trial rows carry the mode and all cost components.
    rows: list[dict[str, object]] = []
    GreedyInstrumentSelector._record_trial(
        [], rows, 0, "A", with_rolls, accepted=True
    )
    assert rows[0]["score_cost_sr"] == pytest.approx(0.18)
    assert rows[0]["ann_roll_cost_sr"] == pytest.approx(0.08)


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


def test_incumbent_receives_participation_eligibility_hysteresis():
    """A prior member may remain inside its hysteresis band unlike an entrant."""
    candidate = SelectionCandidate(
        symbol="A",
        ann_dvol=100.0,
        trade_sr=0.0,
        combined_turnover=1.0,
        asset_class="equity",
        economic_subclass="us",
        # The singleton uses 160 risk dollars per day, or 1.1% participation.
        mkt_risk_vol_day=160.0 / 0.011,
    )
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            max_pct_market_volume=1.0,
            eligibility_hysteresis_percent=25.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )
    candidates = {"A": candidate}

    entrant = selector.score(
        ["A"], candidates, ["A"], np.eye(1), starting=True
    )
    incumbent = selector.score(
        ["A"],
        candidates,
        ["A"],
        np.eye(1),
        starting=True,
        incumbent_symbols=frozenset({"A"}),
    )

    assert not entrant.feasible
    assert incumbent.feasible
    assert incumbent.details[0]["max_market_volume_percent"] == pytest.approx(
        1.25
    )


def test_reference_candidate_does_not_inherit_unknown_live_participation():
    """A reference-prefix candidate remains feasible with no dated volume."""
    candidate = SelectionCandidate(
        symbol="A",
        ann_dvol=100.0,
        trade_sr=0.0,
        combined_turnover=1.0,
        asset_class="equity",
        economic_subclass="us",
        mkt_risk_vol_day=None,
        enforce_market_participation=False,
    )
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(capital=1_000_000.0),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(["A"], {"A": candidate}, ["A"], np.eye(1))

    assert score.feasible
    assert score.details[0]["pct_mkt_volume"] is None
    assert score.details[0]["market_participation_enforced"] is False


@pytest.mark.parametrize(
    ("correlation", "switch_accepted"),
    [(0.99, False), (0.0, True)],
)
def test_book_hysteresis_requires_net_score_gain_above_buffer(
    correlation,
    switch_accepted,
):
    """A challenger pays transition cost and a score hurdle before replacing."""
    candidates = [_candidate("A", 0.0), _candidate("B", 0.0)]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.0,
            size_penalty_scale=0.0,
            membership_hysteresis="score",
            book_score_buffer_percent=2.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )
    correlation_matrix = np.array([
        [1.0, correlation],
        [correlation, 1.0],
    ])

    result = selector.select(
        candidates,
        correlation_matrix,
        prior_selected=["A"],
    )

    assert result.switch_accepted is switch_accepted
    assert result.switch_cost_usd is not None
    assert result.switch_cost_sr is not None
    assert result.net_score_gain == pytest.approx(
        result.gross_score_gain - result.switch_cost_sr
    )
    if switch_accepted:
        assert set(result.selected) == {"A", "B"}
        assert result.net_score_gain > result.score_buffer_sr
    else:
        assert result.selected == ("A",)
        assert result.net_score_gain <= result.score_buffer_sr


def test_rank_hysteresis_uses_lagged_count_anchor_without_forcing_book_size():
    """The rank band uses its supplied state but preserves the raw book count."""
    candidates = [_candidate(symbol, 0.0) for symbol in ("A", "B", "C")]
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.0,
            size_penalty_scale=0.0,
            membership_hysteresis="rank",
            rank_buffer_percent=20.0,
            rank_count_anchor=10,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    result = selector.select(
        candidates,
        np.eye(3),
        prior_selected=["A"],
    )

    assert result.rank_count_anchor == 10
    assert result.rank_buffer_count == 2
    assert result.rank_entry_cutoff == 8
    assert result.rank_exit_cutoff == 12
    assert len(result.selected) == len(result.unbuffered_selected) == 3


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
        "subclass_instr_limit:equity/grains n=3 max=2"
    )


def test_class_coverage_changes_conditional_score_competition():
    """A missing class champion competes before another crowded-class name."""
    candidates = [
        _candidate("FX1", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX2", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate(
            "BOND",
            30.0,
            asset_class="rates",
            economic_subclass="government",
        ),
    ]
    no_coverage = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
        )
    ).select(candidates, np.eye(3))
    with_coverage = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=3,
        )
    ).select(candidates, np.eye(3))

    assert set(no_coverage.selected) == {"FX1", "FX2"}
    assert set(with_coverage.selected) == {"FX1", "FX2", "BOND"}
    bond_trial = next(
        row for row in with_coverage.trials
        if row["iteration"] == 1 and row["candidate"] == "BOND"
    )
    assert bond_trial["coverage_phase"] == "class"
    assert bond_trial["coverage_eligible"] is True
    assert bond_trial["accepted"] is True
    assert bond_trial["class_rank"] == 2
    assert bond_trial["global_trial_rank"] == 2


def test_coverage_admits_missing_class_below_ordinary_score_floor():
    """A feasible required class is ranked by score but bypasses the stop floor."""
    candidates = [
        _candidate("FX", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate(
            "BOND", 10.0, asset_class="rates", economic_subclass="government"
        ),
        _candidate(
            "CRYPTO", 200.0, asset_class="crypto", economic_subclass="crypto"
        ),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=3,
        )
    ).select(candidates, np.eye(3))

    assert "CRYPTO" in result.selected
    assert result.unbuffered_class_coverage_deficit == 0
    assert result.coverage_satisfied is True
    admitted = next(
        row for row in result.trials
        if row["candidate"] == "CRYPTO" and row["iteration"] == 2
    )
    assert admitted["coverage_phase"] == "class"
    assert admitted["accepted"] is True
    assert admitted["reason"] == ""


def test_infeasible_missing_class_does_not_stop_feasible_breadth():
    """An unattainable class remains audited while other markets can enter."""
    candidates = [
        _candidate(
            "FX1",
            0.0,
            asset_class="fx",
            economic_subclass="g10",
            initial_margin_usd=100.0,
        ),
        _candidate(
            "FX2",
            1.0,
            asset_class="fx",
            economic_subclass="g10",
            initial_margin_usd=100.0,
        ),
        _candidate(
            "BOND",
            0.0,
            asset_class="rates",
            economic_subclass="government",
            initial_margin_usd=None,
        ),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=2,
            max_instrument_margin_pct=20.0,
        )
    ).select(candidates, np.eye(3))

    assert set(result.selected) == {"FX1", "FX2"}
    assert result.unbuffered_class_coverage_deficit == 1
    assert result.coverage_satisfied is False
    assert result.coverage_reason == "coverage_unmet_hard_feasibility_gate"
    breadth_trial = next(
        row for row in result.trials
        if row["candidate"] == "FX2" and row["iteration"] == 1
    )
    assert breadth_trial["coverage_phase"] == "breadth"
    assert breadth_trial["accepted"] is True


def test_coverage_minimum_book_count_extends_score_ranked_breadth():
    """Coverage reaches 15 feasible members before restoring the score stop."""
    candidates = [
        _candidate(
            f"FX{index:02d}",
            float(index * 20),
            asset_class="fx",
            economic_subclass="g10",
        )
        for index in range(16)
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=1.0,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=15,
        )
    ).select(candidates, np.eye(16))

    assert len(result.selected) == 15
    accepted_phases = {
        str(row["coverage_phase"])
        for row in result.trials
        if row["accepted"]
    }
    assert accepted_phases == {"class", "breadth"}


def test_score_hysteresis_cannot_retain_a_worse_coverage_book():
    """A score-valid coverage improvement dominates the switch buffer."""
    candidates = [
        _candidate("FX1", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX2", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate(
            "BOND", 30.0, asset_class="rates", economic_subclass="government"
        ),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            membership_hysteresis="score",
            book_score_buffer_percent=100.0,
        )
    ).select(candidates, np.eye(3), prior_selected=["FX1", "FX2"])

    assert "BOND" in result.selected
    assert result.switch_accepted is True
    assert result.switch_reason == "candidate_improves_coverage"
    assert result.final_class_coverage_deficit == 0


def test_score_hysteresis_cannot_undo_minimum_book_breadth():
    """The prior score hurdle cannot retain a feasible undersized book."""
    candidates = [
        _candidate("FX1", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX2", 1.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX3", 2.0, asset_class="fx", economic_subclass="g10"),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=3,
            membership_hysteresis="score",
            book_score_buffer_percent=100.0,
        )
    ).select(candidates, np.eye(3), prior_selected=["FX1", "FX2"])

    assert set(result.selected) == {"FX1", "FX2", "FX3"}
    assert result.switch_reason == "candidate_improves_coverage"
    assert result.final_book_count_deficit == 0
    assert result.coverage_satisfied is True


def test_rank_hysteresis_falls_back_when_retention_worsens_coverage():
    """Rank retention cannot replace a covered class with crowded incumbents."""
    candidates = [
        _candidate("FX1", 0.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX2", 50.0, asset_class="fx", economic_subclass="g10"),
        _candidate("FX3", 60.0, asset_class="fx", economic_subclass="g10"),
        _candidate(
            "BOND", 30.0, asset_class="rates", economic_subclass="government"
        ),
    ]
    result = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000_000.0,
            score_threshold=0.90,
            size_penalty_scale=0.0,
            min_instruments_per_class=1,
            minimum_book_count=2,
            membership_hysteresis="rank",
            rank_buffer_percent=100.0,
        )
    ).select(candidates, np.eye(4), prior_selected=["FX2", "FX3"])

    assert "BOND" in result.selected
    assert result.rank_buffer_reason == "rank_book_coverage_worse_fallback"
    assert result.switch_reason == "candidate_improves_coverage"
    assert result.final_class_coverage_deficit == 0


def test_cluster_instrument_limit_must_be_positive():
    """Zero cannot express a useful per-cluster instrument limit."""
    with pytest.raises(ValueError, match="max_instruments_per_cluster"):
        GreedySelectionConfig(max_instruments_per_cluster=0)


def test_coverage_minimum_cannot_exceed_matching_maximum():
    """Contradictory hierarchy count bounds fail before trial construction."""
    with pytest.raises(ValueError, match="minimum instruments per class"):
        GreedySelectionConfig(
            min_instruments_per_class=2,
            max_instruments_per_class=1,
        )
    with pytest.raises(ValueError, match="minimum instruments per subclass"):
        GreedySelectionConfig(
            min_instruments_per_subclass=2,
            max_instruments_per_subclass=1,
        )


def test_instrument_margin_limit_rejects_one_overfunded_position():
    """One instrument cannot consume more than its configured capital share."""
    candidate = _candidate("SARONA", 0.0, initial_margin_usd=4_000.0)
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=100_000.0,
            target_vol=0.20,
            max_weight_idm=0.20,
            max_instrument_margin_pct=10.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["SARONA"], {"SARONA": candidate}, ["SARONA"], np.eye(1),
        starting=True,
    )

    # Max forecast position is 80 contracts, requiring $320k of additive
    # one-lot initial margin against the configured $10k limit.
    assert not score.feasible
    assert "SARONA:instrument_margin_limit" in score.reason
    assert score.details[0]["portfolio_margin_at_max_usd"] == pytest.approx(
        320_000.0
    )


def test_margin_limit_uses_half_up_whole_contract_position():
    """The practical gate uses executable lots while retaining the baseline."""
    candidate = _candidate("MARGIN", 0.0, initial_margin_usd=100.0)
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_875.0,
            target_vol=0.20,
            max_weight_idm=0.20,
            max_instrument_margin_pct=9.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["MARGIN"], {"MARGIN": candidate}, ["MARGIN"], np.eye(1),
        starting=True,
    )

    detail = score.details[0]
    assert detail["max_position"] == pytest.approx(1.5)
    assert detail["max_cons"] == 2
    assert detail["cont_margin_at_max_usd"] == pytest.approx(150.0)
    assert detail["margin_at_max_usd"] == pytest.approx(200.0)
    assert not score.feasible
    assert "MARGIN:instrument_margin_limit" in score.reason


def test_book_margin_limit_tracks_cumulative_capital_usage():
    """Positions that pass individually can still exhaust total book margin."""
    candidates = {
        "A": _candidate("A", 0.0, initial_margin_usd=600.0),
        "B": _candidate("B", 0.0, initial_margin_usd=600.0),
    }
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=1_000.0,
            target_vol=0.20,
            max_weight_idm=0.20,
            max_instrument_margin_pct=80.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["A", "B"], candidates, ["A", "B"], np.eye(2)
    )

    assert all(
        detail["margin_at_max_usd"] == pytest.approx(600.0)
        for detail in score.details
    )
    assert score.details[0]["instr_margin_limit_usd"] == pytest.approx(800.0)
    assert score.details[0]["book_margin_limit_usd"] == pytest.approx(1_000.0)
    assert score.details[0]["book_margin_remaining_usd"] == pytest.approx(-200.0)
    assert not score.feasible
    assert "portfolio_margin_limit" in score.reason


def test_weight_idm_cap_applies_after_singleton_seed():
    """A two-market trial must not expand each market from 20% to 50% risk."""
    candidates = {
        "A": _candidate("A", 0.0, initial_margin_usd=100.0),
        "B": _candidate("B", 0.0, initial_margin_usd=100.0),
    }
    selector = GreedyInstrumentSelector(
        config=GreedySelectionConfig(
            capital=10_000.0,
            target_vol=0.20,
            max_weight_idm=0.20,
            max_instrument_margin_pct=100.0,
        ),
        weight_policy=EqualPortfolioWeights(),
    )

    score = selector.score(
        ["A", "B"], candidates, ["A", "B"], np.eye(2)
    )

    assert score.idm == pytest.approx(2**0.5)
    for detail in score.details:
        assert detail["weight"] == pytest.approx(0.5)
        assert detail["limit_weight"] * score.idm == pytest.approx(0.20)
        assert detail["max_position"] == pytest.approx(8.0)


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

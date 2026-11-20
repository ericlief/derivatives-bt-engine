from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.portfolio_selection import (
    CARRY_FORECAST_CACHE_VERSION,
    _cap_stitched_reference_to_snapshot,
    _carry_forecast_cache_spec,
    _carry_rule_smooths,
    _filter_backtest_period,
    _forecast_activity_at_review,
    _greedy_selector,
    _is_micro_contract,
    _direct_ib_handoff_date,
    _label_forecast_output_frame,
    _selection_history_content_identity,
    _selection_pysystemtrade_identity,
    _selection_rebalance_dates,
    _stitch_forecast_output_frame,
    _reference_return_coverage,
    _subsystem_reference_correlation,
    _WalkForwardSelectionProvider,
    parse_args,
    run,
    slow_tilt_ewmac_weights,
)


def test_carry_rule_smooths_parse_afts_family_in_stable_order():
    """Carry selection accepts only the named AFTS smoothing variants."""
    assert _carry_rule_smooths("carry120,carry5,carry60") == (5, 60, 120)
    with pytest.raises(ValueError, match="unsupported AFTS carry rule"):
        _carry_rule_smooths("carry10")


def test_carry_forecast_cache_identity_versions_forward_fill(tmp_path):
    """The v5 identity refreshes carry independently of the execution contract."""
    _, _, identity, _ = _carry_forecast_cache_spec(
        tmp_path,
        artifact="carry_reference_forecast",
        history_code="TEST",
        history_db_identity={"path": "reference.duckdb"},
        smooth_spans=(5, 20, 60, 120),
        min_valid_observations=256,
    )

    assert CARRY_FORECAST_CACHE_VERSION == 5
    assert identity["forecast_cache_version"] == CARRY_FORECAST_CACHE_VERSION
    assert identity["carry_smooth_spans"] == [5, 20, 60, 120]
    assert "carry_smooth_coms" not in identity
    assert "max_smoothing_gap_days" not in identity
    assert "max_stale_days" not in identity


def test_reference_return_coverage_uses_only_valid_forecast_rows():
    """Sparse carry gaps do not inflate the usable-return denominator."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "fcst_valid": [False, True, True, False, True],
        "ref_return_valid": [True, True, False, True, True],
    })

    valid, invalid, considered, coverage = _reference_return_coverage(
        frame,
        date(2024, 1, 2),
    )

    assert (valid, invalid, considered) == (2, 1, 3)
    assert coverage == pytest.approx(2 / 3)


def test_stitched_reference_is_capped_at_each_signal_snapshot_end():
    """A newer local DB cannot leak beyond the replayed market snapshot."""
    frame = pl.DataFrame({
        "date": [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)],
        "comb_fcst": [0.1, 0.2, 0.3],
    })

    capped = _cap_stitched_reference_to_snapshot(frame, date(2026, 11, 11))

    assert capped.get_column("date").to_list() == [
        date(2026, 11, 10),
        date(2026, 11, 11),
    ]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"pst_instr_code": "NASDAQ_micro", "desc": "NASDAQ"}, True),
        ({"pst_instr_code": "BITCOIN", "desc": "Micro Bitcoin"}, True),
        ({"pst_instr_code": "NASDAQ", "desc": "E-mini NASDAQ"}, False),
    ],
)
def test_micro_contract_identity_uses_persisted_code_and_description(
    row: dict[str, object], expected: bool
):
    """Nonstandard symbols are classified from explicit source metadata."""
    assert _is_micro_contract(row) is expected


def _mock_stitched_history(
    *,
    sources: list[str],
    source_priority: list[str],
) -> SimpleNamespace:
    """Return the minimal history shape used by cache and splice tests."""
    dates = [date(2024, 3, 28), date(2024, 4, 1)]
    stream = pl.DataFrame({
        "date": dates,
        "source_segment": sources,
        "value": [1.0, 2.0],
    })
    return SimpleNamespace(
        schema_version=12,
        signal=stream,
        marks=stream,
        panama=stream,
        metadata={
            "available_sources": source_priority,
            "source_priority": source_priority,
            "handoff_dates": [dates[1]],
            "reference_preserved_through": dates[0],
        },
    )


def test_direct_ib_handoff_preserves_every_pre_handoff_pst_row():
    """Selection accepts one PST prefix followed directly by the IB tail."""
    history = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "ib"],
    )

    handoff = _direct_ib_handoff_date(history, history_code="TEST")

    assert handoff == date(2024, 4, 1)


def test_direct_ib_handoff_rejects_globex_middle_segment():
    """Selection cannot silently replace the frozen PST prefix with Globex."""
    history = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "globex", "ib"],
    )

    with pytest.raises(ValueError, match="direct PST-to-IB"):
        _direct_ib_handoff_date(history, history_code="TEST")


def test_direct_ib_handoff_must_follow_frozen_pst_endpoint():
    """An overlapping IB segment cannot replace any PST-covered date."""
    history = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "ib"],
    )
    history.metadata["reference_preserved_through"] = date(2024, 4, 1)

    with pytest.raises(ValueError, match="must follow the frozen PST endpoint"):
        _direct_ib_handoff_date(history, history_code="TEST")


def test_stitched_content_identity_ignores_cache_metadata_but_tracks_rows():
    """Acquisition paths do not invalidate a tail whose actual rows match."""
    first = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "ib"],
    )
    second = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "ib"],
    )
    first.metadata["cache_directory"] = "/cache/2026-10-30"
    second.metadata["cache_directory"] = "/cache/2026-10-31"

    first_identity = _selection_history_content_identity(first)
    second_identity = _selection_history_content_identity(second)
    changed = _mock_stitched_history(
        sources=["pysystemtrade", "ib"],
        source_priority=["pysystemtrade", "ib"],
    )
    changed.signal = changed.signal.with_columns(
        pl.when(pl.col("date") == date(2024, 4, 1))
        .then(pl.lit(3.0))
        .otherwise(pl.col("value"))
        .alias("value")
    )

    assert first_identity == second_identity
    assert _selection_history_content_identity(changed) != first_identity


def test_pst_identity_uses_pinned_metadata_without_file_mtime():
    """Frozen PST/FX cache identity is its schema and source commit only."""
    provider = SimpleNamespace(
        _database_metadata=lambda: (5, "abc123")
    )

    identity = _selection_pysystemtrade_identity(provider)

    assert identity == {
        "sidecar_version": 5,
        "source_commit": "abc123",
    }


def test_forecast_output_places_symbol_immediately_after_date():
    """Persisted long forecasts lead with date and executable identity."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, 2)],
        "point_vol": [1.25],
        "combined_forecast": [0.5],
    })

    labeled = _label_forecast_output_frame(
        frame,
        symbol="MZC",
        hist_instr_code="CORN_mini",
    )

    assert labeled.columns == [
        "date",
        "symbol",
        "hist_instr_code",
        "point_vol",
        "combined_forecast",
    ]


def test_forecast_export_keeps_raw_ib_components_at_handoff(tmp_path):
    """The same Parquet keeps raw diagnostics from both sides of the handoff."""
    prefix = pl.LazyFrame({
        "date": [date(2024, 3, 28), date(2024, 4, 1)],
        "raw_fcst_8_32": [0.2, 9.0],
        "raw_carry": [0.3, 9.0],
        "comb_fcst": [0.4, 9.0],
    })
    tail = pl.LazyFrame({
        "date": [date(2024, 3, 28), date(2024, 4, 1), date(2024, 4, 2)],
        "raw_fcst_8_32": [8.0, -0.2, -0.3],
        "raw_carry": [8.0, 0.5, None],
        "comb_fcst": [8.0, 0.1, 0.2],
    })
    path = tmp_path / "forecasts.parquet"
    _stitch_forecast_output_frame(
        prefix, tail, live_tail_start=date(2024, 4, 1)
    ).sink_parquet(path)

    report = pl.read_parquet(path)
    assert report["date"].n_unique() == report.height == 3
    assert report["source_segment"].to_list() == ["pysystemtrade", "ib", "ib"]
    assert report["raw_fcst_8_32"].to_list() == [0.2, -0.2, -0.3]
    assert report["raw_carry"].to_list() == [0.3, 0.5, None]
    assert report["comb_fcst"].to_list() == [0.4, 0.1, 0.2]


def test_slow_tilt_ewmac_weights_use_sixty_forty_full_template():
    weights = slow_tilt_ewmac_weights(
        "4/16,8/32,16/64,32/128,64/256"
    )

    assert weights == pytest.approx({
        "4/16": 0.05,
        "8/32": 0.15,
        "16/64": 0.20,
        "32/128": 0.30,
        "64/256": 0.30,
    })
    assert weights["32/128"] + weights["64/256"] == pytest.approx(0.60)


def test_slow_tilt_ewmac_weights_renormalise_cost_eligible_subset():
    weights = slow_tilt_ewmac_weights(["64/256", "16/64", "32/128"])

    assert weights == pytest.approx({
        "16/64": 0.25,
        "32/128": 0.375,
        "64/256": 0.375,
    })
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "rules, message",
    [
        ("", "at least one"),
        ("4/16,4/16", "duplicates"),
        ("2/8,4/16", "unsupported"),
    ],
)
def test_slow_tilt_ewmac_weights_reject_invalid_rule_sets(rules, message):
    with pytest.raises(ValueError, match=message):
        slow_tilt_ewmac_weights(rules)


def test_selection_defaults_to_reviewed_classification_weights():
    args = parse_args(["--signal-model", "multi_ewmac"])

    assert args.weight_policy == "topdown_classif"
    assert args.max_weight_idm == pytest.approx(0.20)
    assert args.include_roll_cost_in_score is False
    assert args.buffer_size == pytest.approx(0.10)
    assert args.buffer_trade_to_edge is True
    assert args.rebalance_on_roll is True
    assert args.selection_rebalance == "static"
    assert args.selection_eligibility_hysteresis_pct == pytest.approx(0.0)
    assert args.selection_rank_count_ewma_halflife_reviews is None
    assert args.selection_book_score_buffer_pct == pytest.approx(2.0)
    assert args.selection_report_mode == "full"
    assert args.signal_model == "multi_ewmac"
    assert args.fast_window == 64
    assert args.slow_window == 256
    assert args.forecast_fdm == pytest.approx(1.0)
    assert args.selection_forecast_activity_filter is False
    assert args.selection_forecast_lookback_sessions == 256
    assert args.selection_forecast_probation_min == pytest.approx(0.50)
    assert args.selection_forecast_hard_min == pytest.approx(0.25)
    assert args.selection_forecast_probation_reviews == 2
    assert args.ib_prior_years == 3
    assert args.backtest_start_date is None
    assert args.backtest_end_date is None


def test_selection_min_max_position_reaches_greedy_config():
    """The CLI floor controls every member of each hypothetical trial book."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-min-max-position",
        "2",
    ])

    selector = _greedy_selector(
        capital=75_000.0,
        run_config={
            "target_vol": 0.20,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 250,
        },
        args=args,
    )

    assert selector.config.min_max_position == pytest.approx(2.0)


def test_selection_beam_width_is_explicit():
    """The CLI keeps exact greedy width one unless research opts into a beam."""
    baseline = parse_args(["--signal-model", "multi_ewmac"])
    beam = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-beam-width",
        "4",
    ])

    assert baseline.selection_beam_width == 1
    assert beam.selection_beam_width == 4


def test_forecast_activity_probates_then_hard_filters_causally():
    """Weak trailing forecasts survive once, then fail the next review."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 6)],
        "comb_fcst": [2.0, 0.4, -0.4, 0.4, 2.0],
        "fcst_valid": [True] * 5,
    })

    first = _forecast_activity_at_review(
        frame,
        date(2024, 1, 5),
        enabled=True,
        lookback_sessions=3,
        probation_min=0.50,
        hard_min=0.25,
        probation_reviews=2,
    )
    second = _forecast_activity_at_review(
        frame,
        date(2024, 1, 5),
        enabled=True,
        lookback_sessions=3,
        probation_min=0.50,
        hard_min=0.25,
        probation_reviews=2,
        prior_weak_reviews=1,
    )

    assert first["rolling_mean_abs_forecast"] == pytest.approx(0.4)
    assert first["forecast_activity_lookback_end"] == date(2024, 1, 4)
    assert first["forecast_activity_status"] == "probation"
    assert second["forecast_activity_status"] == "hard_filter"
    assert second["forecast_activity_reason"] == (
        "consecutive_weak_forecast_reviews"
    )


def test_forecast_activity_hard_min_excludes_immediately():
    """The lower hard floor does not wait for a second weak review."""
    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 4)],
        "combined_forecast": [0.1, -0.2, 0.15],
        "forecast_valid": [True] * 3,
    })

    activity = _forecast_activity_at_review(
        frame,
        date(2024, 1, 4),
        enabled=True,
        lookback_sessions=3,
        probation_min=0.50,
        hard_min=0.25,
        probation_reviews=2,
    )

    assert activity["forecast_activity_hard_filter"] is True
    assert activity["forecast_activity_reason"] == (
        "rolling_mean_abs_forecast_below_hard_min"
    )


def test_selection_rebalance_cadence_is_explicit():
    """Book review cadence is distinct from contract-roll rebalancing."""
    monthly = parse_args([
        "--signal-model", "multi_ewmac",
        "--selection-rebalance", "monthly",
    ])
    yearly = parse_args([
        "--signal-model", "multi_ewmac",
        "--selection-rebalance", "yearly",
    ])

    assert monthly.selection_rebalance == "monthly"
    assert yearly.selection_rebalance == "yearly"


def test_return_tsmom_signal_model_exposes_parameterized_horizons():
    """The reference baseline has explicit, reproducible signal settings."""
    args = parse_args([
        "--signal-model",
        "return_tsmom",
        "--fast-window",
        "32",
        "--slow-window",
        "128",
        "--regime-discount",
        "0.25",
    ])

    assert args.signal_model == "return_tsmom"
    assert args.fast_window == 32
    assert args.slow_window == 128
    assert args.regime_discount == pytest.approx(0.25)


def test_selection_compact_report_mode_is_explicit():
    """Both spellings enable reduced report detail."""
    args = parse_args([
        "--signal-model", "multi_ewmac",
        "--selection-report-mode", "compact",
    ])

    assert args.selection_report_mode == "compact"
    assert parse_args(["--signal-model", "ewmac_carry", "--compact"]).selection_report_mode == "compact"
    assert parse_args(["--signal-model", "ewmac_carry"]).selection_report_mode == "full"


def test_backtest_period_is_typed_and_inclusive():
    """A bounded cash replay keeps typed endpoints and resets outside rows."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--backtest-start-date",
        "2024-01-01",
        "--backtest-end-date",
        "2026-10-30",
        "--forecast-fdm",
        "1.25",
        "--ib-prior-years",
        "4",
    ])
    frame = pl.DataFrame({
        "date": [
            date(2023, 12, 29),
            date(2024, 1, 2),
            date(2026, 10, 30),
            date(2026, 11, 2),
        ],
        "value": [0, 1, 2, 3],
    })

    bounded = _filter_backtest_period(
        frame,
        start_date=args.backtest_start_date,
        end_date=args.backtest_end_date,
    )

    assert args.forecast_fdm == pytest.approx(1.25)
    assert args.ib_prior_years == 4
    assert bounded.get_column("date").to_list() == [
        date(2024, 1, 2),
        date(2026, 10, 30),
    ]


def test_selection_rebalance_dates_use_first_available_session():
    """Reviews occur on observed sessions and never invent calendar rows."""
    dates = [
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 2, 2),
        date(2027, 1, 4),
    ]

    assert _selection_rebalance_dates(dates, cadence="static") == [dates[0]]
    assert _selection_rebalance_dates(dates, cadence="yearly") == [
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2027, 1, 4),
    ]
    assert _selection_rebalance_dates(dates, cadence="monthly") == [
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2026, 2, 2),
        date(2027, 1, 4),
    ]


def test_walk_forward_selection_uses_uniform_reference_eligibility():
    """Stitched reviews use reference gates on both sides of the handoff."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-rebalance",
        "monthly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
        "--selection-membership-hysteresis",
        "rank",
    ])
    history_dates = [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 31),
    ]
    reference_frames = {
        symbol: pl.DataFrame({
            "date": history_dates,
            "ref_return_valid": [True] * 4,
            "ref_net_pct": returns,
            "daily_dvol": [10.0] * 4,
            "fx_to_usd": [1.0] * 4,
            # This intentionally wide field must be pruned by the lazy query.
            "unused_audit_value": ["x"] * 4,
        }).lazy()
        for symbol, returns in {
            "A": [0.1, 0.2, -0.1, 0.1],
            "B": [-0.1, 0.1, 0.2, -0.2],
            "C": [0.2, -0.1, 0.1, 0.2],
        }.items()
    }
    source_rows = {
        symbol: {
            "symbol": symbol,
            "asset_cls": "equity" if symbol == "A" else "bond",
            "econ_subcls": symbol.lower(),
            "econ_fam_id": symbol,
            "ann_dvol": 160.0,
            "trade_sr": 0.01,
            "roll_sr": 0.02,
            "avg_daily_volume": 1_000.0,
            "init_margin_usd": 10.0,
            "initial_margin_to_ann_dvol": 10.0 / 160.0,
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
            "volume_elig": symbol != "B",
            "exec_elig": True,
            "ib_avail": "contract_qualified",
        }
        for symbol in ("A", "B", "C")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[date(2024, 1, 4), date(2024, 2, 1)],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0} for symbol in ("A", "B", "C")
        },
        reference_frames=reference_frames,
        live_tail_start_by_symbol={
            symbol: date(2024, 2, 1) for symbol in ("A", "B", "C")
        },
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 0.01,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    historical = provider(date(2024, 1, 4), 1_000.0)
    latest = provider(date(2024, 2, 1), 1_000.0)

    assert historical
    assert latest
    assert provider.review_rows[0]["selection_candidate_count"] == 3
    assert provider.review_rows[1]["selection_candidate_count"] == 3
    assert all(
        row["selection_live_execution_overlay_count"] == 0
        for row in provider.review_rows
    )
    assert all(
        not row["live_execution_overlay_active"]
        for row in provider.review_instrument_rows
    )
    assert provider.review_rows[0]["rank_count_ewma_state"] is None
    assert provider.review_rows[1]["rank_count_ewma_state"] == pytest.approx(
        len(historical)
    )
    assert provider.review_rows[1]["rank_count_anchor"] == len(historical)
    assert all(
        row["trade_cost_per_con"] == pytest.approx(0.7)
        for row in historical.values()
    )
    assert all(
        row["roll_cost_per_con"] == pytest.approx(0.9)
        for row in historical.values()
    )
    assert all(
        row["trade_cost_per_con"] == pytest.approx(0.7)
        for row in latest.values()
    )
    assert all(
        row["roll_cost_per_con"] == pytest.approx(0.9)
        for row in latest.values()
    )
    assert all(
        row["initial_margin_usd"] == pytest.approx(10.0)
        for row in provider.review_instrument_rows
        if row["selection_active"]
    )
    first_review_rows = [
        row for row in provider.review_instrument_rows
        if row["selection_date"] == date(2024, 1, 4)
    ]
    second_review_rows = [
        row for row in provider.review_instrument_rows
        if row["selection_date"] == date(2024, 2, 1)
    ]
    assert sum(bool(row["selection_active"]) for row in first_review_rows) == len(
        historical
    )
    assert sum(bool(row["selection_active"]) for row in second_review_rows) == len(
        latest
    )
    assert {row["selection_status"] for row in first_review_rows} == {"added"}
    assert all(row["selection_year"] == 2024 for row in first_review_rows)
    assert all(row["hist_instr_code"] is None for row in first_review_rows)


def test_walk_forward_forecast_probation_rejects_weak_challenger():
    """First-weak challengers stay out while their causal streak advances."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-rebalance",
        "monthly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
        "--selection-forecast-activity-filter",
        "--selection-forecast-lookback-sessions",
        "2",
        "--selection-forecast-probation-min",
        "0.5",
        "--selection-forecast-hard-min",
        "0.25",
        "--selection-forecast-probation-reviews",
        "2",
        "--max-instr-margin-pct",
        "15",
    ])
    history_dates = [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 31),
        date(2024, 2, 1),
    ]
    reference_frames = {
        symbol: pl.DataFrame({
            "date": history_dates,
            "comb_fcst": forecasts,
            "fcst_valid": [True] * 5,
            "ref_return_valid": [True] * 5,
            "ref_net_pct": returns,
            "daily_dvol": [10.0] * 5,
            "fx_to_usd": [1.0] * 5,
        }).lazy()
        for symbol, forecasts, returns in (
            ("A", [1.0] * 5, [0.1, 0.2, -0.1, 0.1, 0.2]),
            ("B", [0.4] * 5, [-0.1, 0.1, 0.2, -0.2, 0.1]),
        )
    }
    source_rows = {
        symbol: {
            "symbol": symbol,
            "asset_cls": "equity",
            "econ_subcls": symbol.lower(),
            "econ_fam_id": symbol,
            "initial_margin_to_ann_dvol": 10.0 / 160.0,
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
        }
        for symbol in ("A", "B")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[date(2024, 1, 4), date(2024, 2, 2)],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0} for symbol in ("A", "B")
        },
        reference_frames=reference_frames,
        live_tail_start_by_symbol={},
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 0.01,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    first = provider(date(2024, 1, 4), 1_000.0)
    assert first is not None and "B" not in first
    assert "B" not in provider.latest_candidates

    second = provider(date(2024, 2, 2), 1_000.0)

    assert second is not None and "B" not in second
    assert provider.review_rows[0]["forecast_activity_probation_count"] == 1
    assert provider.review_rows[1]["forecast_activity_hard_filter_count"] == 1
    assert not any(
        row["symbol"] == "B" for row in provider.review_instrument_rows
    )


def test_walk_forward_forecast_probation_lets_incumbent_breathe_once():
    """A healthy entrant may survive its first weak review, but not its second."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-rebalance",
        "monthly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
        "--selection-forecast-activity-filter",
        "--selection-forecast-lookback-sessions",
        "2",
        "--selection-forecast-probation-min",
        "0.5",
        "--selection-forecast-hard-min",
        "0.25",
        "--selection-forecast-probation-reviews",
        "2",
        "--max-instr-margin-pct",
        "15",
    ])
    history_dates = [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 29),
        date(2024, 3, 1),
    ]
    reference_frames = {
        symbol: pl.DataFrame({
            "date": history_dates,
            "comb_fcst": forecasts,
            "fcst_valid": [True] * len(history_dates),
            "ref_return_valid": [True] * len(history_dates),
            "ref_net_pct": returns,
            "daily_dvol": [10.0] * len(history_dates),
            "fx_to_usd": [1.0] * len(history_dates),
        }).lazy()
        for symbol, forecasts, returns in (
            ("A", [1.0] * 7, [0.1, 0.2, -0.1, 0.1, 0.2, -0.2, 0.1]),
            ("B", [1.0, 1.0, 1.0, 0.4, 0.4, 0.4, 0.4],
             [-0.1, 0.1, 0.2, -0.2, 0.1, 0.2, -0.1]),
        )
    }
    source_rows = {
        symbol: {
            "symbol": symbol,
            "asset_cls": "equity",
            "econ_subcls": symbol.lower(),
            "econ_fam_id": symbol,
            "initial_margin_to_ann_dvol": 10.0 / 160.0,
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
        }
        for symbol in ("A", "B")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[
            date(2024, 1, 4),
            date(2024, 2, 2),
            date(2024, 3, 2),
        ],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0} for symbol in ("A", "B")
        },
        reference_frames=reference_frames,
        live_tail_start_by_symbol={},
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 0.01,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    first = provider(date(2024, 1, 4), 1_000.0)
    second = provider(date(2024, 2, 2), 1_000.0)
    third = provider(date(2024, 3, 2), 1_000.0)

    assert first is not None and "B" in first
    assert second is not None and "B" in second
    assert third is not None and "B" not in third
    b_second = next(
        row
        for row in provider.review_instrument_rows
        if row["selection_date"] == date(2024, 2, 2)
        and row["symbol"] == "B"
    )
    assert b_second["selection_status"] == "retained"
    assert b_second["forecast_activity_probation"] is True
    assert b_second["selection_note"].startswith("forecast_probation:")
    b_third = next(
        row
        for row in provider.review_instrument_rows
        if row["selection_date"] == date(2024, 3, 2)
        and row["symbol"] == "B"
    )
    assert b_third["selection_status"] == "ineligible"
    assert b_third["forecast_activity_weak_review_count"] == 2
    assert b_third["selection_note"].startswith(
        "forecast_activity_hard_filter:"
    )


def test_review_instruments_explain_prior_member_position_floor_exit():
    """A prior member rejected by the size floor keeps its selector reason."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-rebalance",
        "monthly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
        "--selection-min-max-position",
        "1",
    ])
    history_dates = [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 31),
        date(2024, 2, 1),
    ]
    reference_frames = {
        symbol: pl.DataFrame({
            "date": history_dates,
            "ref_return_valid": [True] * 5,
            "ref_net_pct": returns,
            "daily_dvol": daily_dvol,
            "fx_to_usd": [1.0] * 5,
        }).lazy()
        for symbol, returns, daily_dvol in (
            (
                "A",
                [0.1, 0.2, -0.1, 0.1, 0.2],
                [1.0, 1.0, 1.0, 10_000.0, 10_000.0],
            ),
            (
                "B",
                [-0.1, 0.1, 0.2, -0.2, 0.1],
                [1.0] * 5,
            ),
        )
    }
    source_rows = {
        symbol: {
            "symbol": symbol,
            "asset_cls": "equity",
            "econ_subcls": symbol.lower(),
            "econ_fam_id": symbol,
            "initial_margin_to_ann_dvol": 1.0 / 16.0,
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
        }
        for symbol in ("A", "B")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[date(2024, 1, 4), date(2024, 2, 2)],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0} for symbol in ("A", "B")
        },
        reference_frames=reference_frames,
        live_tail_start_by_symbol={},
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 1.0,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    first = provider(date(2024, 1, 4), 100_000.0)
    second = provider(date(2024, 2, 2), 100_000.0)

    assert first is not None and "A" in first
    assert second is not None and "A" not in second
    a_second = next(
        row
        for row in provider.review_instrument_rows
        if row["selection_date"] == date(2024, 2, 2)
        and row["symbol"] == "A"
    )
    assert a_second["selection_status"] == "ineligible"
    assert a_second["max_position"] < 1.0
    assert a_second["selection_note"].startswith(
        "hard_ineligible:A:max_position_below_minimum"
    )


def test_review_instruments_omit_never_selected_forecast_rejections():
    """Compact reviews do not turn rejected challengers into removals."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--selection-rebalance",
        "yearly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
        "--selection-forecast-activity-filter",
        "--selection-forecast-lookback-sessions",
        "2",
        "--selection-forecast-probation-min",
        "0.5",
        "--selection-forecast-hard-min",
        "0.25",
        "--selection-forecast-probation-reviews",
        "2",
    ])
    history_dates = [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
    reference_frames = {
        symbol: pl.DataFrame({
            "date": history_dates,
            "comb_fcst": forecasts,
            "fcst_valid": [True] * 3,
            "ref_return_valid": [True] * 3,
            "ref_net_pct": returns,
            "daily_dvol": [10.0] * 3,
            "fx_to_usd": [1.0] * 3,
        }).lazy()
        for symbol, forecasts, returns in (
            ("A", [1.0] * 3, [0.1, 0.2, -0.1]),
            ("OVERSIZED", [0.1] * 3, [-0.1, 0.1, 0.2]),
        )
    }
    source_rows = {
        symbol: {
            "symbol": symbol,
            "asset_cls": "equity",
            "econ_subcls": symbol.lower(),
            "econ_fam_id": symbol,
            "initial_margin_to_ann_dvol": (
                2.0 if symbol == "OVERSIZED" else 10.0 / 160.0
            ),
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
        }
        for symbol in ("A", "OVERSIZED")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[date(2024, 1, 4)],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0}
            for symbol in ("A", "OVERSIZED")
        },
        reference_frames=reference_frames,
        live_tail_start_by_symbol={},
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 0.01,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    selected = provider(date(2024, 1, 4), 1_000.0)

    assert selected is not None and "A" in selected
    assert "OVERSIZED" not in selected
    assert {
        row["symbol"] for row in provider.review_instrument_rows
    } == {"A"}
    assert provider.review_rows[0]["forecast_activity_hard_filter_count"] == 1


def test_build_selection_entrypoint_defaults_to_complete_pipeline():
    """The new command emits candidates and a book unless asked to stop."""
    args = parse_args(
        ["--signal-model", "multi_ewmac"], complete_default=True
    )
    candidates_only = parse_args(
        ["--signal-model", "multi_ewmac", "--candidates-only"],
        complete_default=True,
    )

    assert args.complete is True
    assert candidates_only.complete is False


def test_complete_selection_requires_explicit_cache_root():
    """Complete runs cannot silently write to the project cache directory."""
    assert parse_args([
        "--signal-model", "multi_ewmac",
        "--cache", "/tmp/selection-cache",
    ]).cache.as_posix() == (
        "/tmp/selection-cache"
    )
    with pytest.raises(ValueError, match="--cache is required"):
        run(["--signal-model", "multi_ewmac", "--complete"])


def test_selection_exposes_executable_buffer_sensitivities():
    """Cash execution can disable the band or trade directly to optimum."""
    args = parse_args([
        "--signal-model",
        "multi_ewmac",
        "--buffer-size",
        "0",
        "--no-buffer-trade-to-edge",
        "--no-rebalance-on-roll",
    ])

    assert args.buffer_size == pytest.approx(0.0)
    assert args.buffer_trade_to_edge is False
    assert args.rebalance_on_roll is False


def test_selection_exposes_offline_target_volatility_override():
    """A cached screen can be resized without another market-data request."""
    args = parse_args([
        "--signal-model", "multi_ewmac", "--target-vol", "0.15"
    ])

    assert args.target_vol == pytest.approx(0.15)


def test_selection_can_include_roll_cost_in_greedy_score():
    """The roll-cost sensitivity is opt-in and explicit at the CLI."""
    args = parse_args([
        "--signal-model", "multi_ewmac", "--include-roll-cost-in-score"
    ])

    assert args.include_roll_cost_in_score is True


def test_starting_weight_remains_alias_for_weight_idm_cap():
    """Existing run scripts keep working after the parameter is clarified."""
    args = parse_args([
        "--signal-model", "multi_ewmac", "--starting-weight", "0.25"
    ])

    assert args.max_weight_idm == pytest.approx(0.25)


def test_legacy_margin_option_is_per_instrument_cap():
    """The old option name maps to the clarified per-instrument constraint."""
    args = parse_args([
        "--signal-model", "multi_ewmac", "--max-margin-pct", "20"
    ])

    assert args.max_instr_margin_pct == pytest.approx(20.0)
    assert args.max_book_margin_pct is None


def test_run_loads_explicit_market_parquet_and_saves_typed_rows(tmp_path):
    source = tmp_path / "pysystemtrade_cost_phase1_ib_20261005_120000.parquet"
    output = tmp_path / "phase2_step1.parquet"
    pl.DataFrame({
        "symbol": ["MZC", "VIX"],
        "asset_cls": ["ags", "vol"],
        "econ_subcls": ["grains", "vol"],
        "ann_dvol": [600.126, 6_200.0],
        "avg_daily_volume": [2_000.0, None],
        "pct_mkt_volume": [0.2, None],
        "init_cap_usd": [100_000.0, 100_000.0],
        "target_vol": [0.2, 0.2],
        "cost_lim_sr": [0.01, 0.01],
        "rule_cost_lim_sr": [0.15, 0.15],
        "liq_ann_trades": [25.0, 25.0],
        "liq_days": [250, 250],
        "min_daily_volume": [100.0, 100.0],
        "max_pct_mkt_volume": [1.0, 1.0],
        "cost_elig": [True, True],
        "size_elig": [True, True],
        "liq_elig": [True, False],
        "data_elig": [True, False],
        "instr_has_elig_ewmac_rule": [True, True],
        "exec_elig": [True, True],
        "selection_elig": [True, False],
        "ib_avail": ["contract_qualified", "unavailable_or_unverified"],
    }).write_parquet(source)

    saved_path, selected = run([
        "--signal-model",
        "multi_ewmac",
        "--input",
        str(source),
        "--output",
        str(output),
    ])

    assert saved_path == output.resolve()
    assert selected.get_column("symbol").to_list() == ["MZC"]
    saved = pl.read_parquet(output)
    assert saved.get_column("symbol").to_list() == ["MZC"]
    assert saved.get_column("ann_dvol").to_list() == [600.13]
    assert selected.get_column("ann_dvol").to_list() == [600.13]
    assert saved.get_column("init_cap_usd").to_list() == [100_000.0]
    assert saved.get_column("target_vol").to_list() == [0.2]
    assert saved.columns == selected.columns
    screen_paths = list(
        tmp_path.glob("multi_speed_ewmac_selection_screen_*.parquet")
    )
    assert len(screen_paths) == 1
    screen = pl.read_parquet(screen_paths[0])
    assert screen.height == 2
    assert screen.get_column("symbol").to_list() == ["MZC", "VIX"]
    assert screen.get_column("selection_elig").to_list() == [True, False]
    assert screen.get_column("selection_excl")[1] is not None


def test_run_rejects_csv_output_path(tmp_path):
    source = tmp_path / "phase1.parquet"
    pl.DataFrame({
        "symbol": ["ES"],
        "selection_elig": [True],
    }).write_parquet(source)

    with pytest.raises(ValueError, match=r"\.parquet extension"):
        run([
            "--signal-model",
            "multi_ewmac",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "step1.csv"),
        ])


def test_subsystem_reference_correlation_uses_daily_date_key():
    """Reference-return correlation retains date in its persisted input panel."""
    dates = pl.date_range(
        pl.date(2024, 1, 1), pl.date(2024, 3, 20), eager=True
    )
    frames = {
        "A": pl.DataFrame({
            "date": dates,
            "ref_net_pct": [
                index * 0.001 for index in range(len(dates))
            ],
        }),
        "B": pl.DataFrame({
            "date": dates,
            "ref_net_pct": [
                index * -0.002 for index in range(len(dates))
            ],
        }),
    }

    correlation, covered, wide = _subsystem_reference_correlation(
        frames,
        ["A", "B"],
        window_years=1.0,
        halflife_days=20.0,
        min_rows=20,
    )

    assert wide.columns == ["date", "A", "B"]
    assert covered.tolist() == [True, True]
    assert correlation.shape == (2, 2)

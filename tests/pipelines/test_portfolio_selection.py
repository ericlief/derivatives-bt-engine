from datetime import date

import polars as pl
import pytest

from derivatives_bt_engine.pipelines.portfolio_selection import (
    _label_forecast_output_frame,
    _selection_rebalance_dates,
    _subsystem_reference_correlation,
    _WalkForwardSelectionProvider,
    parse_args,
    run,
    slow_tilt_ewmac_weights,
)


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
    args = parse_args([])

    assert args.weight_policy == "topdown_classif"
    assert args.max_weight_idm == pytest.approx(0.20)
    assert args.include_roll_cost_in_score is False
    assert args.buffer_size == pytest.approx(0.10)
    assert args.buffer_trade_to_edge is True
    assert args.rebalance_on_roll is True
    assert args.selection_rebalance == "static"


def test_selection_rebalance_cadence_is_explicit():
    """Book review cadence is distinct from contract-roll rebalancing."""
    monthly = parse_args(["--selection-rebalance", "monthly"])
    yearly = parse_args(["--selection-rebalance", "yearly"])

    assert monthly.selection_rebalance == "monthly"
    assert yearly.selection_rebalance == "yearly"


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


def test_walk_forward_selection_uses_carver_then_live_cost_calibration():
    """Completed segments use configured cost; the open segment uses live."""
    args = parse_args([
        "--selection-rebalance",
        "monthly",
        "--min-forecast-obs",
        "2",
        "--corr-min-rows",
        "2",
        "--weight-policy",
        "equal",
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
            "ref_ann_rolls": 4.0,
            "ref_spread_pts": 0.1,
            "ref_config_comm_usd": 0.2,
            "mult": 5.0,
        }
        for symbol in ("A", "B")
    }
    provider = _WalkForwardSelectionProvider(
        review_dates=[date(2024, 1, 4), date(2024, 2, 1)],
        source_rows=source_rows,
        audit_by_symbol={
            symbol: {"comb_turnover": 1.0} for symbol in ("A", "B")
        },
        reference_frames=reference_frames,
        run_config={
            "target_vol": 0.20,
            "cost_lim_sr": 0.01,
            "max_pct_mkt_volume": 1.0,
            "liq_days": 256,
        },
        args=args,
    )

    historical = provider(date(2024, 1, 4), 1_000.0)
    live = provider(date(2024, 2, 1), 1_000.0)

    assert historical
    assert live
    assert all(
        row["trade_cost_per_con"] == pytest.approx(0.7)
        for row in historical.values()
    )
    assert all(
        row["roll_cost_per_con"] == pytest.approx(0.9)
        for row in historical.values()
    )
    assert all(
        row["trade_cost_per_con"] == pytest.approx(1.6)
        for row in live.values()
    )
    assert all(
        row["roll_cost_per_con"] == pytest.approx(3.2)
        for row in live.values()
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
        live
    )
    assert {row["selection_status"] for row in first_review_rows} == {"added"}
    assert all(row["selection_year"] == 2024 for row in first_review_rows)
    assert all(row["hist_instr_code"] is None for row in first_review_rows)


def test_build_selection_entrypoint_defaults_to_complete_pipeline():
    """The new command emits candidates and a book unless asked to stop."""
    args = parse_args([], complete_default=True)
    candidates_only = parse_args(
        ["--candidates-only"], complete_default=True
    )

    assert args.complete is True
    assert candidates_only.complete is False


def test_selection_exposes_executable_buffer_sensitivities():
    """Cash execution can disable the band or trade directly to optimum."""
    args = parse_args([
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
    args = parse_args(["--target-vol", "0.15"])

    assert args.target_vol == pytest.approx(0.15)


def test_selection_can_include_roll_cost_in_greedy_score():
    """The roll-cost sensitivity is opt-in and explicit at the CLI."""
    args = parse_args(["--include-roll-cost-in-score"])

    assert args.include_roll_cost_in_score is True


def test_starting_weight_remains_alias_for_weight_idm_cap():
    """Existing run scripts keep working after the parameter is clarified."""
    args = parse_args(["--starting-weight", "0.25"])

    assert args.max_weight_idm == pytest.approx(0.25)


def test_legacy_margin_option_is_per_instrument_cap():
    """The old option name maps to the clarified per-instrument constraint."""
    args = parse_args(["--max-margin-pct", "20"])

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
        run(["--input", str(source), "--output", str(tmp_path / "step1.csv")])


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

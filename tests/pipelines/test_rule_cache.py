"""Tests for the scenario-neutral futures rule-cache command."""

from pathlib import Path

import polars as pl

import derivatives_bt_engine.pipelines.rule_cache as rule_cache


def test_rule_cache_parser_exposes_no_selection_scenario_parameters():
    """The cache API cannot accidentally bind itself to one account scenario."""
    parser = rule_cache.parse_args([
        "--snapshot-input",
        "snapshot.parquet",
    ])

    assert parser.snapshot_input == Path("snapshot.parquet")
    assert not hasattr(parser, "initial_capital_usd")
    assert not hasattr(parser, "target_vol")
    assert not hasattr(parser, "instrument_cost_limit_sr")


def test_rule_cache_forwards_only_historical_cache_inputs(monkeypatch):
    """The narrow CLI delegates calibration without selection policy flags."""
    captured: dict[str, object] = {}

    def fake_run(argv, *, rule_cache_only):
        captured["argv"] = argv
        captured["rule_cache_only"] = rule_cache_only
        return pl.DataFrame({"symbol": ["MZC"]})

    monkeypatch.setattr(rule_cache, "run_cost_pipeline", fake_run)

    result = rule_cache.run([
        "--snapshot-input",
        "snapshot.parquet",
        "--rule-cost-limit-sr",
        "0.10",
    ])

    forwarded = captured["argv"]
    assert captured["rule_cache_only"] is True
    assert "--snapshot-input" in forwarded
    assert "--rule-cost-limit-sr" in forwarded
    assert "--initial-capital-usd" not in forwarded
    assert "--target-vol" not in forwarded
    assert result.get_column("symbol").to_list() == ["MZC"]

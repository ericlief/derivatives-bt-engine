import argparse
from datetime import date
import importlib.util
from pathlib import Path

import polars as pl
import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "pysystemtrade_corn_roll_ablation.py"
)
SPEC = importlib.util.spec_from_file_location(
    "pysystemtrade_corn_roll_ablation",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

_parse_fast_spans = MODULE._parse_fast_spans
_strategy_label = MODULE._strategy_label
paired_comparison = MODULE.paired_comparison


def test_strategy_labels_distinguish_mini_roll_regimes():
    assert _strategy_label("CORN", "pre_split") == "annual_december"
    assert _strategy_label("CORN_mini", "pre_split") == (
        "mini_early_mostly_two_roll"
    )
    assert _strategy_label("CORN_mini", "post_split") == (
        "mini_five_roll_observed"
    )


def test_fast_span_parser_rejects_nonpositive_values():
    assert _parse_fast_spans("4,16,64") == (4, 16, 64)
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_fast_spans("4,0")


def test_paired_comparison_uses_common_dates_and_mini_minus_full():
    full = pl.DataFrame({
        "ts_event": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "CORN": [1.0, -1.0, 0.0],
    })
    mini = pl.DataFrame({
        "ts_event": [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
        "CORN_mini": [0.0, 2.0, -1.0],
    })

    result = paired_comparison(
        full,
        mini,
        rule="16/64",
        window="post_split",
        full_sharpe=0.4,
        mini_sharpe=0.5,
    )

    assert result["paired_observations"] == 2
    assert result["mini_minus_full_sharpe"] == pytest.approx(0.1)
    assert result["annual_mean_mini_minus_full"] == pytest.approx(384.0)
    assert result["pnl_correlation"] == pytest.approx(1.0)

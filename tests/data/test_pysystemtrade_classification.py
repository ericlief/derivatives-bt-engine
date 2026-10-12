"""Validate the repository-owned pysystemtrade economic hierarchy."""

from pathlib import Path

import polars as pl
import pytest

from derivatives_bt_engine.data.pysystemtrade_classification import (
    load_pysystemtrade_instrument_classification,
    validate_classification_coverage,
)


def test_packaged_classification_is_complete_and_uses_lowercase_values():
    classification = load_pysystemtrade_instrument_classification()

    assert classification.height == 252
    assert classification.get_column("instrument_code").n_unique() == 252
    assert classification.filter(
        (pl.col("instrument_code") == "CORN")
        & (pl.col("asset_class") == "ags")
        & (pl.col("economic_subclass") == "grains")
    ).height == 1
    assert classification.filter(
        (pl.col("instrument_code") == "LIVECOW")
        & (pl.col("economic_subclass") == "livestock")
    ).height == 1
    assert classification.filter(
        (pl.col("instrument_code") == "HOUSE-US")
        & (pl.col("asset_class") == "other")
        & (pl.col("economic_subclass") == "housing")
    ).height == 1
    assert classification.filter(
        (pl.col("instrument_code") == "EPRA-EUROPE")
        & (pl.col("asset_class") == "equity")
        & (pl.col("economic_subclass") == "ex_us")
    ).height == 1


def test_classification_rejects_noncanonical_capitalization(tmp_path: Path):
    path = tmp_path / "classification.csv"
    path.write_text(
        "instrument_code,asset_class,economic_subclass\n"
        "CORN,Ags,grains\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lowercase snake case"):
        load_pysystemtrade_instrument_classification(path)


def test_classification_coverage_rejects_unmapped_instruments():
    classification = pl.DataFrame({
        "instrument_code": ["CORN"],
        "asset_class": ["ags"],
        "economic_subclass": ["grains"],
    })

    with pytest.raises(ValueError, match="WHEAT"):
        validate_classification_coverage(classification, ["CORN", "WHEAT"])

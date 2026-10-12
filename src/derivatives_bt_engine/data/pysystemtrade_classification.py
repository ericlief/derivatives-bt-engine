"""Load and validate the explicit economic hierarchy for pysystemtrade.

The upstream instrument configuration supplies broad, presentation-oriented
asset classes but not the stable economic subclasses required by Phase 2's
top-down portfolio weighting.  This module owns the version-controlled
classification boundary.  The import pipeline materializes the mapping in the
reference DuckDB, while report and selection code consume the same canonical
lowercase values.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl


DEFAULT_PYSYSTEMTRADE_CLASSIFICATION_PATH = Path(__file__).with_name(
    "pysystemtrade_instrument_classification.csv"
)

CLASSIFICATION_COLUMNS = {
    "instrument_code",
    "asset_class",
    "economic_subclass",
}

# The small number of non-core branches are explicit rather than forcing
# commodity indexes or crypto into unrelated classes. Source ``Housing`` and
# ``Sector`` instruments are intentionally folded into the equity geography
# branches used by the Phase 2 top-down allocation.
ALLOWED_CLASSIFICATION_TREE: dict[str, frozenset[str]] = {
    "ags": frozenset({"grains", "softs", "livestock"}),
    "bond": frozenset({"bonds", "rates"}),
    "equity": frozenset({"us", "ex_us"}),
    "fx": frozenset({"fx"}),
    "metals": frozenset({"precious", "industrial"}),
    "energy": frozenset({"energy"}),
    "vol": frozenset({"vol"}),
    "crypto": frozenset({"crypto"}),
    "other": frozenset({"commodity_index"}),
}

_LOWER_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def load_pysystemtrade_instrument_classification(
    path: Path | str = DEFAULT_PYSYSTEMTRADE_CLASSIFICATION_PATH,
) -> pl.DataFrame:
    """Return the validated one-row-per-instrument economic classification.

    Parameters
    ----------
    path
        CSV containing ``instrument_code``, ``asset_class``, and
        ``economic_subclass``. Classification values must be lowercase snake
        case; uppercase is reserved for instrument and contract symbols.

    Returns
    -------
    polars.DataFrame
        Rows sorted by instrument code and restricted to the canonical schema.
    """
    mapping_path = Path(path)
    frame = pl.read_csv(mapping_path, infer_schema_length=10_000)
    missing = CLASSIFICATION_COLUMNS - set(frame.columns)
    extra = set(frame.columns) - CLASSIFICATION_COLUMNS
    if missing or extra:
        raise ValueError(
            "pysystemtrade classification columns do not match canonical "
            f"schema; missing={sorted(missing)} extra={sorted(extra)}"
        )
    frame = frame.select(
        pl.col("instrument_code").cast(pl.String),
        pl.col("asset_class").cast(pl.String),
        pl.col("economic_subclass").cast(pl.String),
    )
    for column in CLASSIFICATION_COLUMNS:
        if frame.get_column(column).null_count():
            raise ValueError(f"pysystemtrade classification contains null {column}")
    duplicates = frame.group_by("instrument_code").len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(
            "duplicate instrument_code in pysystemtrade classification: "
            f"{duplicates.get_column('instrument_code').to_list()}"
        )

    invalid_tokens: list[str] = []
    invalid_pairs: list[str] = []
    for row in frame.iter_rows(named=True):
        asset_class = row["asset_class"]
        subclass = row["economic_subclass"]
        if not _LOWER_SNAKE_CASE.fullmatch(asset_class):
            invalid_tokens.append(f"{row['instrument_code']}:asset_class={asset_class}")
        if not _LOWER_SNAKE_CASE.fullmatch(subclass):
            invalid_tokens.append(
                f"{row['instrument_code']}:economic_subclass={subclass}"
            )
        if subclass not in ALLOWED_CLASSIFICATION_TREE.get(asset_class, frozenset()):
            invalid_pairs.append(
                f"{row['instrument_code']}:{asset_class}/{subclass}"
            )
    if invalid_tokens:
        raise ValueError(
            "classification values must be lowercase snake case: "
            f"{invalid_tokens}"
        )
    if invalid_pairs:
        raise ValueError(
            "classification rows are outside the allowed hierarchy: "
            f"{invalid_pairs}"
        )
    return frame.sort("instrument_code")


def validate_classification_coverage(
    classification: pl.DataFrame,
    instrument_codes: pl.Series | list[str],
) -> pl.DataFrame:
    """Require exactly one classification for every requested instrument.

    Extra classification rows are allowed so import fixtures and older source
    snapshots can consume a subset of the current version-controlled table.
    The returned frame contains only the requested source universe.
    """
    codes = (
        instrument_codes.cast(pl.String).to_list()
        if isinstance(instrument_codes, pl.Series)
        else [str(code) for code in instrument_codes]
    )
    requested = set(codes)
    available = set(classification.get_column("instrument_code").to_list())
    missing = sorted(requested - available)
    if missing:
        raise ValueError(
            "pysystemtrade instruments missing economic classification: "
            f"{missing}"
        )
    return classification.filter(pl.col("instrument_code").is_in(codes)).sort(
        "instrument_code"
    )

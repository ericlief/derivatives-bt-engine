"""Audit and apply the pysystemtrade normalization-universe classification.

The imported DuckDB is an immutable source catalog: it intentionally contains
full, mini, micro, venue, and roll-policy variants.  This module supplies the
separate research decision layer used to prevent execution duplicates from
receiving repeated weight in pooled forecast calibration.

Candidate review has two stages:

1. symbol, description, broker identity, and instrument metadata identify
   plausible related histories; and
2. canonical roll-neutral returns, selected contract months, roll dates, and
   configured roll rules show whether the histories are execution duplicates
   or genuinely distinct contracts/roll policies.

The reviewed CSV is authoritative.  The heuristic assessment in the audit is
evidence for review, never an automatic deletion rule.

Representative coverage is ranked by chronological source span. Canonical
daily prices and usable returns diagnose gaps inside that span, but do not make
a same-span mini/micro history "longer." Raw ``multiple_rows`` and
``adjusted_rows`` counts include mixed-frequency observations and must never be
used to choose between size variants. Same effective spans prefer the
established larger contract unless a reviewed source-quality reason says
otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Optional

import duckdb
import polars as pl

from derivatives_bt_engine.data.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.logging_config import setup_logger


logger = setup_logger()

DEFAULT_POOLING_MAPPING_PATH = Path(__file__).with_name(
    "pysystemtrade_pooling_universe.csv"
)
DEFAULT_POOLING_AUDIT_DIR = (
    Path(__file__).resolve().parents[3] / "research" / "pysystemtrade-pooling-audit"
)

POOLING_MAPPING_COLUMNS = {
    "instrument_code",
    "economic_family_id",
    "roll_policy_id",
    "duplicate_group_id",
    "pooling_role",
    "representative_instrument",
    "include_default",
    "decision_basis",
    "notes",
}
POOLING_ROLES = {
    "primary",
    "execution_duplicate",
    "distinct_roll_policy",
    "distinct_contract",
    "excluded_mixed_history",
    "review_required",
}

_VARIANT_CODE_TOKENS = {"mini", "micro", "full", "ice", "sgx", "lme"}
_DESCRIPTION_STOPWORDS = {
    "contract",
    "contracts",
    "cme",
    "comex",
    "full",
    "future",
    "futures",
    "ice",
    "index",
    "lme",
    "micro",
    "mini",
    "nymex",
    "sized",
}


def load_pysystemtrade_pooling_mapping(
    path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> pl.DataFrame:
    """Load and validate explicit pooling decisions for related histories."""
    mapping_path = Path(path)
    mapping = pl.read_csv(
        mapping_path,
        infer_schema_length=10_000,
        null_values=[""],
    )
    missing = POOLING_MAPPING_COLUMNS - set(mapping.columns)
    if missing:
        raise ValueError(
            f"pysystemtrade pooling mapping missing columns: {sorted(missing)}"
        )
    mapping = mapping.select(sorted(POOLING_MAPPING_COLUMNS)).with_columns(
        pl.col("include_default").cast(pl.Boolean),
    )
    duplicates = (
        mapping.group_by("instrument_code").len().filter(pl.col("len") > 1)
    )
    if duplicates.height:
        raise ValueError(
            "duplicate instrument_code in pooling mapping: "
            f"{duplicates['instrument_code'].to_list()}"
        )
    invalid_roles = sorted(
        set(mapping["pooling_role"].drop_nulls().to_list()) - POOLING_ROLES
    )
    if invalid_roles:
        raise ValueError(f"invalid pooling roles: {invalid_roles}")
    required_values = [
        "instrument_code",
        "economic_family_id",
        "roll_policy_id",
        "pooling_role",
        "representative_instrument",
        "include_default",
        "decision_basis",
    ]
    for column in required_values:
        if mapping[column].null_count():
            raise ValueError(f"pooling mapping contains null {column}")
    return mapping.sort("economic_family_id", "roll_policy_id", "instrument_code")


def pooling_mapping_fingerprint(
    path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> str:
    """Return a short content hash suitable for derived-cache keys."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def load_pysystemtrade_pooling_catalog(
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
) -> pl.DataFrame:
    """Load source metadata needed by both audit stages."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        catalog = con.execute(
            """
            SELECT
                i.instrument_code,
                i.description,
                i.asset_class,
                i.currency,
                i.point_size,
                i.region,
                r.hold_roll_cycle,
                r.roll_offset_days,
                r.carry_offset,
                r.priced_roll_cycle,
                r.expiry_offset,
                c.multiple_rows,
                c.multiple_start,
                c.multiple_end,
                c.price_days,
                c.carry_day_coverage,
                c.adjusted_rows,
                c.adjusted_start,
                c.adjusted_end
            FROM raw.instrument_config i
            JOIN raw.roll_config r USING (instrument_code)
            JOIN qa.series_coverage c USING (instrument_code)
            WHERE c.multiple_rows > 0
              AND c.adjusted_rows > 0
            ORDER BY i.instrument_code
            """
        ).pl()
    finally:
        con.close()

    # Lazy import avoids a module cycle: the IB catalog also consumes the
    # pooling classifications for the all-variant execution-cost report.
    from derivatives_bt_engine.data.pysystemtrade_ib import (
        load_pysystemtrade_ib_mapping,
    )

    broker = load_pysystemtrade_ib_mapping().select(
        "instrument_code",
        "ib_symbol",
        "ib_exchange",
        "ib_currency",
        "ib_effective_point_value",
    )
    return catalog.join(broker, on="instrument_code", how="left", validate="1:1")


def apply_pysystemtrade_pooling_mapping(
    catalog: pl.DataFrame,
    *,
    mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> pl.DataFrame:
    """Attach reviewed decisions; unmapped instruments are included singletons."""
    if "instrument_code" not in catalog.columns:
        raise ValueError("pooling catalog requires instrument_code")
    mapping = load_pysystemtrade_pooling_mapping(mapping_path)
    catalog_codes = set(catalog["instrument_code"].to_list())
    unknown = sorted(set(mapping["instrument_code"].to_list()) - catalog_codes)
    if unknown:
        raise ValueError(f"pooling mapping references unknown instruments: {unknown}")
    representatives = set(mapping["representative_instrument"].to_list())
    missing_representatives = sorted(representatives - catalog_codes)
    if missing_representatives:
        raise ValueError(
            "pooling mapping references unknown representatives: "
            f"{missing_representatives}"
        )

    joined = catalog.join(mapping, on="instrument_code", how="left", validate="1:1")
    return joined.with_columns(
        pl.coalesce("economic_family_id", "instrument_code").alias(
            "economic_family_id"
        ),
        pl.coalesce("roll_policy_id", "instrument_code").alias("roll_policy_id"),
        pl.coalesce("pooling_role", pl.lit("singleton")).alias("pooling_role"),
        pl.coalesce("representative_instrument", "instrument_code").alias(
            "representative_instrument"
        ),
        pl.col("include_default").fill_null(True),
        pl.coalesce("decision_basis", pl.lit("no_duplicate_candidate")).alias(
            "decision_basis"
        ),
        pl.col("notes").fill_null(""),
        pl.lit(pooling_mapping_fingerprint(mapping_path)).alias(
            "pooling_mapping_hash"
        ),
    ).sort("instrument_code")


def default_pysystemtrade_pooling_universe(
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    *,
    mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> pl.DataFrame:
    """Return the reviewed default normalization members only."""
    return apply_pysystemtrade_pooling_mapping(
        load_pysystemtrade_pooling_catalog(db_path), mapping_path=mapping_path
    ).filter(pl.col("include_default"))


def _words(value: str) -> list[str]:
    """Tokenize a code or description into normalized alphanumeric words."""
    return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]


def _code_base(value: str) -> str:
    """Remove known size-variant tokens from an instrument code."""
    return "".join(
        token for token in _words(value) if token not in _VARIANT_CODE_TOKENS
    )


def _code_root(value: str) -> str:
    """Return the first normalized token used for conservative matching."""
    words = _words(value)
    return words[0] if words else ""


def _description_key(value: str) -> str:
    """Normalize a description after removing non-identifying stop words."""
    return " ".join(
        token
        for token in _words(value)
        if token not in _DESCRIPTION_STOPWORDS
    )


def _candidate_reasons(left: dict, right: dict) -> list[str]:
    """Return metadata reasons that justify an expensive history comparison."""
    if left["asset_class"] != right["asset_class"]:
        return []
    reasons: list[str] = []
    left_code = str(left["instrument_code"])
    right_code = str(right["instrument_code"])
    if _code_base(left_code) == _code_base(right_code):
        reasons.append("normalized_symbol")
    left_root = _code_root(left_code)
    if left_root == _code_root(right_code) and len(left_root) >= 3:
        reasons.append("symbol_root")
    if (
        left.get("ib_symbol")
        and left.get("ib_symbol") == right.get("ib_symbol")
    ):
        reasons.append("broker_symbol")
    left_description = _description_key(str(left.get("description") or ""))
    right_description = _description_key(str(right.get("description") or ""))
    if left_description and left_description == right_description:
        reasons.append("normalized_description")
    if (
        left.get("duplicate_group_id")
        and left.get("economic_family_id") == right.get("economic_family_id")
    ):
        reasons.append("reviewed_economic_family")
    if not reasons:
        return []
    if left.get("currency") == right.get("currency"):
        reasons.append("same_currency")
    if left.get("ib_exchange") == right.get("ib_exchange"):
        reasons.append("same_broker_exchange")
    left_point = float(left.get("point_size") or 0.0)
    right_point = float(right.get("point_size") or 0.0)
    if left_point > 0.0 and right_point > 0.0:
        ratio = max(left_point, right_point) / min(left_point, right_point)
        if ratio > 1.0 and math.isclose(ratio, round(ratio), abs_tol=1e-9):
            reasons.append("integer_scaled_point_value")
    return sorted(set(reasons))


def discover_pysystemtrade_duplicate_candidates(
    classified_catalog: pl.DataFrame,
) -> pl.DataFrame:
    """Produce conservative metadata candidate pairs for history review."""
    pairs: list[dict[str, object]] = []
    rows = classified_catalog.sort("instrument_code").to_dicts()
    for left, right in combinations(rows, 2):
        reasons = _candidate_reasons(left, right)
        if not reasons:
            continue
        pairs.append(
            {
                "instrument_a": left["instrument_code"],
                "instrument_b": right["instrument_code"],
                "candidate_reasons": ",".join(reasons),
                "asset_class": left["asset_class"],
                "economic_family_a": left["economic_family_id"],
                "economic_family_b": right["economic_family_id"],
                "description_a": left["description"],
                "description_b": right["description"],
                "currency_a": left["currency"],
                "currency_b": right["currency"],
                "point_size_a": left["point_size"],
                "point_size_b": right["point_size"],
                "ib_symbol_a": left.get("ib_symbol"),
                "ib_symbol_b": right.get("ib_symbol"),
                "ib_exchange_a": left.get("ib_exchange"),
                "ib_exchange_b": right.get("ib_exchange"),
                "history_start_a": left["adjusted_start"],
                "history_start_b": right["adjusted_start"],
                "history_days_a": left["price_days"],
                "history_days_b": right["price_days"],
                "roll_config_a": _roll_config_label(left),
                "roll_config_b": _roll_config_label(right),
                "pooling_role_a": left["pooling_role"],
                "pooling_role_b": right["pooling_role"],
                "include_default_a": left["include_default"],
                "include_default_b": right["include_default"],
                "representative_a": left["representative_instrument"],
                "representative_b": right["representative_instrument"],
            }
        )
    if not pairs:
        return pl.DataFrame()
    return pl.DataFrame(pairs, infer_schema_length=None).sort(
        "asset_class", "instrument_a", "instrument_b"
    )


def _roll_config_label(row: dict) -> str:
    """Serialize roll-cycle fields into a compact pair-comparison label."""
    return "/".join(
        str(row[column])
        for column in (
            "hold_roll_cycle",
            "roll_offset_days",
            "carry_offset",
            "priced_roll_cycle",
            "expiry_offset",
        )
    )


def _near_roll_fraction(
    source: list, target: list, days: int = 5
) -> Optional[float]:
    """Fraction of source roll dates within ``days`` of a target roll."""
    if not source:
        return None
    if not target:
        return 0.0
    matched = 0
    target_position = 0
    for source_date in source:
        while (
            target_position < len(target)
            and (target[target_position] - source_date).days < -days
        ):
            target_position += 1
        if target_position < len(target):
            delta = abs((target[target_position] - source_date).days)
            if delta <= days:
                matched += 1
    return matched / len(source)


def _finite_or_none(value) -> Optional[float]:
    """Convert an optional numeric diagnostic to a finite float."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _history_pair_metrics(left, right) -> dict[str, object]:
    """Compare common-date returns, contract identity, and roll timing.

    Both inputs are :class:`FuturesHistory` objects. Returns are reconstructed
    only after the two signal-index levels have been inner-joined on date, so
    missing sessions cannot compare unequal holding periods.
    """
    # Join levels before calculating returns. Joining independently calculated
    # daily returns can pair a one-session move with a multi-session move when
    # either source omits an intermediate date.
    returns = (
        left.signal.select(
            "trade_date", pl.col("signal_index").alias("index_a")
        )
        .join(
            right.signal.select(
                "trade_date", pl.col("signal_index").alias("index_b")
            ),
            on="trade_date",
            how="inner",
        )
        .sort("trade_date")
        .with_columns(
            pl.col("index_a").pct_change().alias("return_a"),
            pl.col("index_b").pct_change().alias("return_b"),
        )
        .drop_nulls(["return_a", "return_b"])
    )
    return_correlation = (
        _finite_or_none(returns.select(pl.corr("return_a", "return_b")).item())
        if returns.height > 1
        else None
    )
    contracts = left.marks.select(
        "trade_date",
        pl.col("contract_id").alias("contract_a"),
    ).join(
        right.marks.select(
            "trade_date",
            pl.col("contract_id").alias("contract_b"),
        ),
        on="trade_date",
        how="inner",
    )
    if contracts.height:
        agreements = contracts.select(
            (
                pl.col("contract_a").str.slice(0, 6)
                == pl.col("contract_b").str.slice(0, 6)
            ).mean().alias("contract_month_agreement"),
            (pl.col("contract_a") == pl.col("contract_b"))
            .mean()
            .alias("contract_id_agreement"),
        ).row(0, named=True)
    else:
        agreements = {
            "contract_month_agreement": None,
            "contract_id_agreement": None,
        }
    rolls_left = left.marks.filter(pl.col("is_roll"))["trade_date"].to_list()
    rolls_right = right.marks.filter(pl.col("is_roll"))["trade_date"].to_list()
    return {
        "overlap_return_days": returns.height,
        "return_correlation": return_correlation,
        "overlap_contract_days": contracts.height,
        **agreements,
        "rolls_a": len(rolls_left),
        "rolls_b": len(rolls_right),
        "roll_near_5d_a_to_b": _near_roll_fraction(rolls_left, rolls_right),
        "roll_near_5d_b_to_a": _near_roll_fraction(rolls_right, rolls_left),
    }


def _heuristic_assessment(row: dict) -> str:
    """Label pair evidence without overriding reviewed pooling decisions."""
    roles = {row["pooling_role_a"], row["pooling_role_b"]}
    if "excluded_mixed_history" in roles:
        return "reviewed_excluded_mixed_history"
    if "execution_duplicate" in roles:
        return "reviewed_execution_duplicate"
    if roles & {"distinct_roll_policy", "distinct_contract"}:
        return "reviewed_distinct_history"
    if "review_required" in roles:
        return "review_required"

    same_roll_config = row["roll_config_a"] == row["roll_config_b"]
    same_exchange = row["ib_exchange_a"] == row["ib_exchange_b"]
    correlation = row.get("return_correlation")
    month_agreement = row.get("contract_month_agreement")
    if (
        same_roll_config
        and same_exchange
        and correlation is not None
        and correlation >= 0.995
        and month_agreement is not None
        and month_agreement >= 0.94
    ):
        return "high_similarity_execution_duplicate_candidate"
    return "distinct_or_requires_review"


def build_pysystemtrade_pooling_audit(
    db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    *,
    mapping_path: Path | str = DEFAULT_POOLING_MAPPING_PATH,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return full classifications and two-stage candidate-pair evidence."""
    classified = apply_pysystemtrade_pooling_mapping(
        load_pysystemtrade_pooling_catalog(db_path), mapping_path=mapping_path
    )
    candidates = discover_pysystemtrade_duplicate_candidates(classified)
    if candidates.is_empty():
        return classified, candidates

    provider = PysystemtradeHistoryProvider(
        db_path=db_path,
        use_cache=True,
        save_cache=False,
    )
    codes = sorted(
        set(candidates["instrument_a"].to_list())
        | set(candidates["instrument_b"].to_list())
    )
    logger.info(
        "pooling_audit history_lookup_start candidates=%d instruments=%d",
        candidates.height,
        len(codes),
    )
    histories = {code: provider.load(code) for code in codes}
    metric_rows = []
    for position, pair in enumerate(candidates.iter_rows(named=True), start=1):
        metrics = _history_pair_metrics(
            histories[pair["instrument_a"]], histories[pair["instrument_b"]]
        )
        combined = {**pair, **metrics}
        combined["roll_config_match"] = (
            pair["roll_config_a"] == pair["roll_config_b"]
        )
        combined["same_broker_exchange"] = (
            pair["ib_exchange_a"] == pair["ib_exchange_b"]
        )
        combined["assessment"] = _heuristic_assessment(combined)
        metric_rows.append(combined)
        logger.debug(
            "pooling_audit candidate=%s/%s position=%d total=%d corr=%s "
            "contract_month_agreement=%s roll_config_match=%s assessment=%s",
            pair["instrument_a"],
            pair["instrument_b"],
            position,
            candidates.height,
            metrics["return_correlation"],
            metrics["contract_month_agreement"],
            combined["roll_config_match"],
            combined["assessment"],
        )
    audited = pl.DataFrame(metric_rows, infer_schema_length=None).sort(
        "asset_class", "instrument_a", "instrument_b"
    )
    logger.info(
        "pooling_audit history_lookup_complete candidates=%d instruments=%d",
        audited.height,
        len(codes),
    )
    return classified, audited


def _markdown_table(frame: pl.DataFrame, columns: list[str]) -> str:
    """Render selected Polars columns as a compact Markdown table."""
    if frame.is_empty():
        return "_None._"
    rows = frame.select(columns).to_dicts()
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row[column] or "") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def write_pysystemtrade_pooling_audit(
    output_dir: Path | str,
    classifications: pl.DataFrame,
    candidates: pl.DataFrame,
) -> None:
    """Persist pooling classifications, pair evidence, and narrative audit."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    classifications.write_csv(output / "instrument_classification.csv")
    candidates.write_csv(output / "candidate_pairs.csv")

    included = classifications.filter(pl.col("include_default"))
    excluded = classifications.filter(~pl.col("include_default"))
    representatives = classifications.filter(pl.col("pooling_role") == "primary")
    roles = (
        classifications.group_by("pooling_role")
        .agg(pl.len().alias("instruments"))
        .sort("pooling_role")
    )
    report = f"""# pysystemtrade pooling-universe audit

This audit leaves the source DuckDB unchanged. It first identifies related
histories from symbols, normalized descriptions, broker identity, and the
reviewed economic-family map. It then compares the same canonical
roll-neutral returns and selected contracts used by the backtester, together
with configured roll cycles, offsets, and carry relationships.

The reviewed mapping is authoritative; pairwise `assessment` is supporting
evidence and never deletes a history automatically.

Representative selection ranks chronological source span, not raw
mixed-frequency row counts. Daily-row counts expose gaps within a span; they do
not make a same-span mini/micro series longer. Same effective spans prefer the
established larger contract unless a reviewed source-quality reason says
otherwise.

## Summary

- Source histories: {classifications.height}
- Default normalization histories: {included.height}
- Excluded duplicate/mixed histories: {excluded.height}
- Candidate pairs inspected: {candidates.height}
- Mapping fingerprint: `{classifications['pooling_mapping_hash'][0]}`

{_markdown_table(roles, ['pooling_role', 'instruments'])}

## Selected representatives for size variants

This is the affirmative selection table. Carver's legacy instrument-code
suffixes are not reliable product-size labels, so the broker symbol and
effective point value are shown explicitly.

{_markdown_table(representatives, ['duplicate_group_id', 'instrument_code', 'description', 'ib_symbol', 'ib_effective_point_value', 'adjusted_start', 'adjusted_end', 'price_days', 'decision_basis'])}

## Excluded from the default normalization pool

{_markdown_table(excluded, ['instrument_code', 'economic_family_id', 'roll_policy_id', 'pooling_role', 'representative_instrument', 'decision_basis'])}

All execution variants remain in `instrument_classification.csv` and in the
futures cost/risk report. `candidate_pairs.csv` contains common-interval return
correlation, contract-month agreement, exact contract agreement, two-sided
five-day roll matching, both roll configurations, descriptions, broker symbols,
exchanges, history starts, and the reviewed decision for every first-pass
candidate. Common-interval returns are recomputed from each signal index after
joining common dates, so a missing session cannot pair a multi-session return
from one history with a one-session return from the other.

## Interpretation

- `execution_duplicate` is omitted only from pooled parameter estimation; it
  remains available as a tradable contract and cost diagnostic.
- `distinct_roll_policy` and `distinct_contract` remain in the research pool
  because their history differences are intentional rather than contract-size
  duplication.
- `excluded_mixed_history` is a reviewed data-quality/policy decision. The WTI
  mini/micro histories change regime inside one series, so the default pool
  instead retains the consistent NYMEX December and ICE monthly histories.
- Full and micro Ether have the same economic exposure and roll configuration,
  but Carver's stored EOD marks diverge materially. The full contract is the
  sole normalization representative; the micro remains available for execution
  and source-quality analysis.
- `RUSSELL_mini` is the larger IB `RTY` E-mini (50 dollars per point), while
  `RUSSELL` is the smaller IB `M2K` Micro E-mini (5 dollars per point). Likewise,
  `DOW_mini` is IB `YM`, while `DOW` is the smaller IB `MYM`. The selected table
  uses broker identity and effective point value to make these legacy-name
  inversions explicit.
- A long backfilled mini/micro history is research coverage, not evidence that
  the small contract itself traded throughout that period.
"""
    (output / "report.md").write_text(report, encoding="utf-8")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse source database, mapping, and audit-output paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_PYSYSTEMTRADE_DB_PATH))
    parser.add_argument(
        "--mapping", default=str(DEFAULT_POOLING_MAPPING_PATH)
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_POOLING_AUDIT_DIR))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """Build and write the reviewed normalization-pool audit artifacts."""
    args = parse_args(argv)
    classifications, candidates = build_pysystemtrade_pooling_audit(
        args.db, mapping_path=args.mapping
    )
    write_pysystemtrade_pooling_audit(
        args.output_dir, classifications, candidates
    )
    logger.info(
        "pooling_audit complete source_histories=%d included=%d candidates=%d "
        "output=%s",
        classifications.height,
        classifications.filter(pl.col("include_default")).height,
        candidates.height,
        args.output_dir,
    )


if __name__ == "__main__":
    main()

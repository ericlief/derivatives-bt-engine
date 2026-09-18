"""Source-neutral futures-history adapter for the TSMOM backtester."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import polars as pl

from derivatives_bt_engine.data.futures_overlap import load_mappings
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    FuturesHistory,
    GlobexHistoryProvider,
    HybridHistoryProvider,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.instruments import resolve_price_symbol


SOURCE_NEUTRAL_DATA_SOURCES = ("globex", "pysystemtrade", "hybrid")


def _contract_month_date(contract_id: object) -> Optional[date]:
    value = str(contract_id or "")
    if len(value) < 6 or not value[:6].isdigit():
        return None
    year, month = int(value[:4]), int(value[4:6])
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1)


def history_to_tsmom_bars(history: FuturesHistory) -> pl.DataFrame:
    """Expose separate signal, mark, and roll-neutral P&L columns."""
    signal_columns = [
        "trade_date",
        "source_timestamp",
        "signal_index",
        "normalized_return",
        "return_valid",
        "quality_flag",
    ]
    if "source_segment" in history.signal.columns:
        signal_columns.append("source_segment")
    signal = history.signal.select(signal_columns)
    marks = history.marks.select(
        "trade_date",
        pl.col("mark_price").alias("close"),
        "contract_id",
        "is_roll",
    )
    panama = history.panama.select(
        "trade_date",
        "panama_price",
        "contract_point_change",
    )
    bars = signal.join(marks, on="trade_date", how="inner").join(
        panama, on="trade_date", how="inner"
    ).sort("trade_date")
    if "source_segment" not in bars.columns:
        bars = bars.with_columns(pl.lit(history.source).alias("source_segment"))
    return bars.with_columns(
        pl.col("trade_date").alias("ts_event"),
        pl.col("panama_price").alias("pnl_close"),
        pl.when(pl.col("source_segment") == "pysystemtrade")
        .then(pl.lit("research_approximation"))
        .otherwise(pl.lit("primary_contract_marks"))
        .alias("pnl_quality"),
        pl.Series(
            "expiration",
            [_contract_month_date(value) for value in bars.get_column("contract_id")],
            dtype=pl.Date,
        ),
    ).select(
        "ts_event",
        "close",
        "pnl_close",
        "signal_index",
        "panama_price",
        "normalized_return",
        "return_valid",
        "quality_flag",
        "contract_point_change",
        "contract_id",
        "expiration",
        "is_roll",
        "source_segment",
        "pnl_quality",
    )


def _mapping_by_globex(
    mapping_path: Optional[Path | str],
    *,
    allow_candidate_mappings: bool,
) -> dict[str, object]:
    mappings = {mapping.globex_asset: mapping for mapping in load_mappings(mapping_path)}
    if allow_candidate_mappings:
        return mappings
    return {
        asset: mapping
        for asset, mapping in mappings.items()
        if mapping.mapping_status == "approved"
    }


def load_source_neutral_histories(
    symbols: list[str],
    *,
    data_source: str,
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH,
    pysystemtrade_db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH,
    mapping_path: Optional[Path | str] = None,
    handoff_date: Optional[date] = None,
    allow_candidate_mappings: bool = False,
) -> tuple[dict[str, pl.DataFrame], dict[str, object]]:
    """Load source-neutral bars plus reproducibility/quality metadata."""
    if data_source not in SOURCE_NEUTRAL_DATA_SOURCES:
        raise ValueError(f"unsupported source-neutral data source: {data_source}")
    globex = GlobexHistoryProvider(db_path=globex_db_path)
    carver = PysystemtradeHistoryProvider(db_path=pysystemtrade_db_path)
    mappings = _mapping_by_globex(
        mapping_path,
        allow_candidate_mappings=allow_candidate_mappings,
    )
    frames: dict[str, pl.DataFrame] = {}
    instruments: dict[str, object] = {}

    for traded_symbol in symbols:
        globex_symbol = resolve_price_symbol(traded_symbol)
        mapping = mappings.get(globex_symbol)
        if data_source in {"pysystemtrade", "hybrid"} and mapping is None:
            qualifier = "approved " if not allow_candidate_mappings else ""
            raise ValueError(
                f"no {qualifier}pysystemtrade mapping for {traded_symbol} "
                f"(resolved Globex symbol {globex_symbol}); use an approved mapping or "
                "explicitly enable candidate mappings for research"
            )
        if data_source == "globex":
            history = globex.load(globex_symbol)
        elif data_source == "pysystemtrade":
            history = carver.load(mapping.carver_instrument)
        else:
            alignment = (
                "previous_primary_session"
                if mapping.carver_date_alignment == "previous_globex_session"
                else ""
            )
            history = HybridHistoryProvider(
                historical=carver,
                primary=globex,
                historical_instrument_map={globex_symbol: mapping.carver_instrument},
                handoff_date=handoff_date,
                historical_date_alignment=alignment,
                historical_date_alignment_through=mapping.carver_date_alignment_through,
            ).load(globex_symbol)
        frames[traded_symbol] = history_to_tsmom_bars(history)
        invalid = history.signal.filter(
            ~pl.col("return_valid")
            & (pl.col("quality_flag") != "initial_observation")
        )
        instruments[traded_symbol] = {
            "history_instrument": history.instrument_code,
            "history_schema_version": history.schema_version,
            "resolved_globex_symbol": globex_symbol,
            "source": history.source,
            "start_date": history.signal.get_column("trade_date").min(),
            "end_date": history.signal.get_column("trade_date").max(),
            "invalid_return_rows": invalid.height,
            "quality_flags": invalid.group_by("quality_flag").len().to_dicts(),
            "crosswalk": (
                {
                    "canonical_market_id": mapping.canonical_market_id,
                    "carver_instrument": mapping.carver_instrument,
                    "globex_asset": mapping.globex_asset,
                    "mapping_status": mapping.mapping_status,
                    "usage_status": mapping.usage_status,
                    "carver_date_alignment": mapping.carver_date_alignment or None,
                    "carver_date_alignment_through": (
                        mapping.carver_date_alignment_through
                    ),
                }
                if mapping is not None else None
            ),
            "metadata": dict(history.metadata),
        }

    return frames, {
        "data_source": data_source,
        "globex_db_path": str(globex_db_path),
        "pysystemtrade_db_path": str(pysystemtrade_db_path),
        "mapping_path": str(mapping_path) if mapping_path is not None else "packaged_default",
        "allow_candidate_mappings": allow_candidate_mappings,
        "requested_handoff_date": handoff_date,
        "instruments": instruments,
    }

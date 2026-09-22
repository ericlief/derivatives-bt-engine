"""Contract-aware continuous futures transformations.

The functions in this module deliberately produce two different signal
representations from one immutable stream of selected-contract observations:

* an additive Panama price for point-price rules such as Carver EWMAC; and
* a positive chained index for rules defined on arithmetic returns.

Neither representation is an executable mark.  P&L must continue to use the
underlying contract prices and identities.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class ContinuousFuturesResult:
    """Derived observation-level streams, before optional EOD selection."""

    signal: pl.DataFrame
    panama: pl.DataFrame


def _quality(*flags: str) -> str:
    return ";".join(flag for flag in flags if flag)


def build_continuous_futures(
    observations: pl.DataFrame,
    *,
    direct_reference_column: str | None = None,
) -> ContinuousFuturesResult:
    """Build roll-neutral returns and an additive Panama series.

    Required columns are ``trade_date``, ``source_timestamp``,
    ``current_price`` and ``current_contract``.  Carver-style input also has
    ``forward_price`` and ``forward_contract``: on a roll, yesterday's
    forward quote must identify today's selected contract.  A provider that
    already has the prior same-contract quote (the Globex query does) may pass
    its name as ``direct_reference_column``.

    ``pt_change_1d`` is the matched-contract move in price points;
    ``ret_1d`` divides that move by the reference price. Neither is
    volatility-normalized. Percentage returns are deliberately unavailable
    when either side of the ratio is nonpositive, or when a roll cannot be
    matched. The positive
    index holds its last valid value on such a row, but ``return_valid`` is
    false and consumers must mask the row.  Point changes and the Panama path
    remain valid across negative prices when the contract match is valid.
    """
    required = {
        "trade_date",
        "source_timestamp",
        "current_price",
        "current_contract",
    }
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"continuous futures input missing columns: {sorted(missing)}")
    if direct_reference_column is not None and direct_reference_column not in observations:
        raise ValueError(f"missing direct reference column: {direct_reference_column}")

    frame = (
        observations.drop_nulls(["current_price", "current_contract"])
        .sort("source_timestamp")
    )
    if frame.is_empty():
        raise ValueError("continuous futures input has no complete price observations")

    rows = frame.to_dicts()
    point_changes: list[float | None] = []
    references: list[float | None] = []
    returns: list[float | None] = []
    indices: list[float] = []
    rolls: list[bool] = []
    roll_differentials: list[float | None] = []
    qualities: list[str] = []
    return_valid: list[bool] = []
    index_value = 100.0

    for position, row in enumerate(rows):
        current = float(row["current_price"])
        if position == 0:
            point_changes.append(None)
            references.append(None)
            returns.append(None)
            indices.append(index_value)
            rolls.append(False)
            roll_differentials.append(None)
            qualities.append("initial_observation")
            return_valid.append(False)
            continue

        previous = rows[position - 1]
        previous_price = float(previous["current_price"])
        is_roll = row["current_contract"] != previous["current_contract"]
        reference: float | None
        roll_differential: float | None = None
        contract_match = True

        if direct_reference_column is not None:
            value = row.get(direct_reference_column)
            reference = None if value is None else float(value)
            if is_roll and reference is not None:
                roll_differential = reference - previous_price
        elif not is_roll:
            reference = previous_price
        else:
            forward_contract = previous.get("forward_contract")
            forward_price = previous.get("forward_price")
            contract_match = (
                forward_contract is not None
                and str(forward_contract) == str(row["current_contract"])
                and forward_price is not None
            )
            reference = float(forward_price) if contract_match else None
            if reference is not None:
                roll_differential = reference - previous_price

        point_change = None if reference is None else current - reference
        ratio_valid = (
            reference is not None
            and reference > 0.0
            and current > 0.0
        )
        ret_1d = current / reference - 1.0 if ratio_valid else None
        if ret_1d is not None and ret_1d <= -1.0:
            ret_1d = None
            ratio_valid = False
        if ret_1d is not None:
            index_value *= 1.0 + ret_1d

        flag = _quality(
            "unmatched_roll" if is_roll and not contract_match else "",
            "missing_reference" if reference is None else "",
            "nonpositive_reference" if reference is not None and reference <= 0 else "",
            "nonpositive_current" if current <= 0 else "",
        )
        point_changes.append(point_change)
        references.append(reference)
        returns.append(ret_1d)
        indices.append(index_value)
        rolls.append(is_roll)
        roll_differentials.append(roll_differential)
        qualities.append(flag)
        return_valid.append(ratio_valid)

    # Carver's forward loop adds each new roll differential to every older
    # value.  The reverse pass is algebraically identical without O(n^2)
    # repeated mutation: latest segment remains raw, older segments receive
    # the sum of all subsequent roll differentials.
    adjustments = [0.0] * len(rows)
    cumulative_adjustment = 0.0
    for position in range(len(rows) - 1, 0, -1):
        adjustments[position] = cumulative_adjustment
        differential = roll_differentials[position]
        if differential is not None:
            cumulative_adjustment += differential
    adjustments[0] = cumulative_adjustment
    panama_prices = [
        float(row["current_price"]) + adjustment
        for row, adjustment in zip(rows, adjustments)
    ]

    enriched = frame.with_columns(
        pl.Series("reference_price", references, dtype=pl.Float64),
        pl.Series("pt_change_1d", point_changes, dtype=pl.Float64),
        pl.Series("ret_1d", returns, dtype=pl.Float64),
        pl.Series("signal_index", indices, dtype=pl.Float64),
        pl.Series("is_roll", rolls, dtype=pl.Boolean),
        pl.Series("roll_differential", roll_differentials, dtype=pl.Float64),
        pl.Series("return_valid", return_valid, dtype=pl.Boolean),
        pl.Series("quality_flag", qualities, dtype=pl.String),
    )
    signal = enriched.select(
        "trade_date",
        "source_timestamp",
        "current_price",
        pl.col("current_contract").alias("contract_id"),
        "reference_price",
        "pt_change_1d",
        "ret_1d",
        "signal_index",
        "is_roll",
        "return_valid",
        "quality_flag",
    )
    panama = enriched.with_columns(
        pl.Series("panama_price", panama_prices, dtype=pl.Float64)
    ).select(
        "trade_date",
        "source_timestamp",
        "panama_price",
        "pt_change_1d",
        "roll_differential",
        pl.col("current_price"),
        pl.col("current_contract").alias("contract_id"),
        "is_roll",
        "quality_flag",
    )
    return ContinuousFuturesResult(signal=signal, panama=panama)


def select_daily_last(frame: pl.DataFrame) -> pl.DataFrame:
    """Select the last derived observation per normalized trade session."""
    return (
        frame.sort("trade_date", "source_timestamp")
        .unique(subset="trade_date", keep="last", maintain_order=True)
        .sort("trade_date")
    )


def select_daily_continuous(
    result: ContinuousFuturesResult,
) -> ContinuousFuturesResult:
    """Collapse a full-frequency transformation without losing daily moves.

    The last observation's ``ret_1d`` is only its final intraday
    move.  The full-frequency ``signal_index`` already chains every valid move,
    so daily returns must be recomputed from consecutive EOD index levels.
    Panama point changes are similarly recomputed from consecutive EOD levels.
    Any invalid intraday ratio makes that whole session ineligible.
    """
    invalid_dates = (
        result.signal.filter(
            ~pl.col("return_valid")
            & (pl.col("quality_flag") != "initial_observation")
        )
        .get_column("trade_date")
        .unique()
        .to_list()
    )
    panama = select_daily_last(result.panama).with_columns(
        pl.col("panama_price").diff().alias("pt_change_1d")
    )
    signal = select_daily_last(result.signal).with_columns(
        pl.col("signal_index").pct_change().alias("ret_1d")
    ).with_columns(
        pl.when(pl.col("ret_1d").is_not_null())
        .then(pl.col("current_price") / (1.0 + pl.col("ret_1d")))
        .otherwise(None)
        .alias("reference_price"),
        (~pl.col("trade_date").is_in(invalid_dates)
         & pl.col("ret_1d").is_not_null()).alias("return_valid"),
        pl.when(pl.col("trade_date").is_in(invalid_dates))
        .then(pl.lit("invalid_intraday_return"))
        .when(pl.col("ret_1d").is_null())
        .then(pl.lit("initial_observation"))
        .otherwise(pl.col("quality_flag"))
        .alias("quality_flag"),
    ).with_columns(
        pl.when(pl.col("return_valid"))
        .then(pl.col("ret_1d"))
        .otherwise(None)
        .alias("ret_1d")
    ).with_columns(
        # Rebuild from the final validated daily returns.  The observation-
        # frequency index may contain valid intraday moves from a session
        # that was rejected as a whole; retaining that old index would let
        # index-based rules recover those excluded moves on a later date.
        (
            (1.0 + pl.col("ret_1d").fill_null(0.0)).cum_prod()
            * 100.0
        ).alias("signal_index")
    ).join(
        panama.select("trade_date", "pt_change_1d"),
        on="trade_date",
        how="left",
        suffix="_panama",
    ).drop("pt_change_1d").rename(
        {"pt_change_1d_panama": "pt_change_1d"}
    )
    return ContinuousFuturesResult(signal=signal, panama=panama)

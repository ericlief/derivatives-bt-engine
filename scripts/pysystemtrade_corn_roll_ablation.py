"""Ablate the historical CORN and CORN-mini roll-policy regimes.

The imported CORN history holds the annual December contract. CORN-mini used
mostly September/December contracts before 2016 and the configured HKNUZ cycle
from 2016 onward. This diagnostic calculates every EWMAC rule on its complete
history for warm-up, then evaluates performance in matched pre/post-2016
windows so a full-history difference is not mistaken for a roll-policy effect.

Run from the repository root::

    .venv/bin/python scripts/pysystemtrade_corn_roll_ablation.py
    .venv/bin/python scripts/pysystemtrade_corn_roll_ablation.py \
      --output-dir results/corn_roll_ablation
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import logging
import math
from pathlib import Path

import polars as pl

from derivatives_bt_engine.data.futures_cost_risk import (
    _ewmac_rule_performance,
)
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.signal import carver_ewmac
from derivatives_bt_engine.domain.tsmom_backtester import (
    TsmomBacktestConfig,
    load_pysystemtrade_ewmac_normalization,
)
from derivatives_bt_engine.utils.logger import setup_logger


log = logging.getLogger(
    "derivatives_bt_engine.scripts.pysystemtrade_corn_roll_ablation"
)
DEFAULT_FAST_SPANS = (4, 8, 16, 32, 64)
DEFAULT_SPLIT_DATE = date(2016, 1, 1)
ANNUALIZATION_DAYS = 256


def _parse_fast_spans(value: str) -> tuple[int, ...]:
    spans = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not spans or any(span <= 0 for span in spans):
        raise argparse.ArgumentTypeError("fast spans must be positive integers")
    return spans


def _strategy_label(instrument_code: str, window: str) -> str:
    if instrument_code == "CORN":
        return "annual_december"
    if window == "pre_split":
        return "mini_early_mostly_two_roll"
    if window == "post_split":
        return "mini_five_roll_observed"
    return "mini_mixed_full_history"


def _rule_frame(
    history,
    scalar: pl.DataFrame,
    *,
    fast_span: int,
) -> pl.DataFrame:
    slow_span = fast_span * 4
    return (
        carver_ewmac(
            history.panama_bars(),
            fast_span=fast_span,
            slow_span=slow_span,
            forecast_scalar=1.0,
        )
        .join_asof(scalar, on="ts_event", strategy="backward")
        .with_columns(
            (pl.col("raw_forecast") * pl.col("forecast_scalar"))
            .clip(-20.0, 20.0)
            .alias("ewmac_forecast")
        )
    )


def _evaluate_rule(
    rule: pl.DataFrame,
    *,
    instrument_code: str,
    fast_span: int,
    window: str,
    start: date | None,
    end: date | None,
) -> tuple[dict[str, object], pl.DataFrame]:
    evaluation = (
        rule
        if start is None or end is None
        else rule.filter(pl.col("ts_event").is_between(start, end))
    )
    metrics, pnl = _ewmac_rule_performance(evaluation)
    row = {
        "rule": f"{fast_span}/{fast_span * 4}",
        "fast_span": fast_span,
        "instrument_code": instrument_code,
        "strategy": _strategy_label(instrument_code, window),
        "window": window,
        "window_start": start or metrics["strategy_history_start"],
        "window_end": end or metrics["strategy_history_end"],
        "pnl_observations": metrics["strategy_pnl_observations"],
        "pre_cost_sharpe": metrics["ewmac_pre_cost_sharpe"],
        "forecast_turnover": metrics["ewmac_forecast_turnover"],
    }
    return row, pnl.rename({"risk_adjusted_pnl": instrument_code})


def paired_comparison(
    full_pnl: pl.DataFrame,
    mini_pnl: pl.DataFrame,
    *,
    rule: str,
    window: str,
    full_sharpe: float,
    mini_sharpe: float,
) -> dict[str, object]:
    """Return matched-date mini-minus-full performance diagnostics."""
    paired = (
        full_pnl.join(mini_pnl, on="ts_event", how="inner")
        .drop_nulls(["CORN", "CORN_mini"])
        .with_columns((pl.col("CORN_mini") - pl.col("CORN")).alias("difference"))
    )
    difference_std = paired.get_column("difference").std()
    difference_mean = paired.get_column("difference").mean()
    differential_sharpe = (
        difference_mean / difference_std * math.sqrt(ANNUALIZATION_DAYS)
        if difference_std is not None and difference_std > 0
        else None
    )
    effective_years = paired.height / ANNUALIZATION_DAYS
    differential_sharpe_se = (
        math.sqrt((1.0 + 0.5 * differential_sharpe**2) / effective_years)
        if differential_sharpe is not None and effective_years > 0
        else None
    )
    return {
        "rule": rule,
        "window": window,
        "paired_observations": paired.height,
        "effective_years": effective_years,
        "full_pre_cost_sharpe": full_sharpe,
        "mini_pre_cost_sharpe": mini_sharpe,
        "mini_minus_full_sharpe": mini_sharpe - full_sharpe,
        "pnl_correlation": paired.select(
            pl.corr("CORN", "CORN_mini")
        ).item(),
        "annual_mean_mini_minus_full": difference_mean * ANNUALIZATION_DAYS,
        "annual_tracking_error": difference_std * math.sqrt(ANNUALIZATION_DAYS),
        "differential_sharpe": differential_sharpe,
        "differential_sharpe_se_approx": differential_sharpe_se,
        "differential_sharpe_ci95_low_approx": (
            differential_sharpe - 1.96 * differential_sharpe_se
            if differential_sharpe_se is not None
            else None
        ),
        "differential_sharpe_ci95_high_approx": (
            differential_sharpe + 1.96 * differential_sharpe_se
            if differential_sharpe_se is not None
            else None
        ),
    }


def run_ablation(
    *,
    db_path: Path,
    split_date: date = DEFAULT_SPLIT_DATE,
    fast_spans: tuple[int, ...] = DEFAULT_FAST_SPANS,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    provider = PysystemtradeHistoryProvider(db_path=db_path)
    histories = {
        code: provider.load(code)
        for code in ("CORN", "CORN_mini")
    }
    common_start = max(
        history.marks.get_column("trade_date").min()
        for history in histories.values()
    )
    common_end = min(
        history.marks.get_column("trade_date").max()
        for history in histories.values()
    )
    windows = {
        "full_history": (None, None),
        "pre_split": (common_start, split_date - timedelta(days=1)),
        "post_split": (split_date, common_end),
    }

    metric_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    for fast_span in fast_spans:
        config = TsmomBacktestConfig(
            symbols=[],
            data_source="pysystemtrade",
            signal_weighting="carver_ewmac",
            pysystemtrade_db_path=db_path,
            ewmac_fast_span=fast_span,
            ewmac_slow_span=fast_span * 4,
        )
        scalar_history, _, _ = load_pysystemtrade_ewmac_normalization(config)
        scalar = (
            scalar_history.filter(pl.col("pool_key") == "global")
            .select("ts_event", "forecast_scalar")
            .sort("ts_event")
        )
        rules = {
            code: _rule_frame(history, scalar, fast_span=fast_span)
            for code, history in histories.items()
        }
        window_results: dict[
            tuple[str, str], tuple[dict[str, object], pl.DataFrame]
        ] = {}
        for window, (start, end) in windows.items():
            for code, rule in rules.items():
                result = _evaluate_rule(
                    rule,
                    instrument_code=code,
                    fast_span=fast_span,
                    window=window,
                    start=start,
                    end=end,
                )
                window_results[(window, code)] = result
                metric_rows.append(result[0])

            if window == "full_history":
                continue
            full_metrics, full_pnl = window_results[(window, "CORN")]
            mini_metrics, mini_pnl = window_results[(window, "CORN_mini")]
            comparison_rows.append(
                paired_comparison(
                    full_pnl,
                    mini_pnl,
                    rule=f"{fast_span}/{fast_span * 4}",
                    window=window,
                    full_sharpe=float(full_metrics["pre_cost_sharpe"]),
                    mini_sharpe=float(mini_metrics["pre_cost_sharpe"]),
                )
            )

    metrics = pl.DataFrame(metric_rows, infer_schema_length=None).sort(
        "fast_span", "window", "instrument_code"
    )
    comparisons = pl.DataFrame(
        comparison_rows,
        infer_schema_length=None,
    ).sort("rule", "window")
    log.info(
        "corn_roll_ablation complete rules=%d split_date=%s "
        "strategy_rows=%d comparison_rows=%d",
        len(fast_spans),
        split_date,
        metrics.height,
        comparisons.height,
    )
    return metrics, comparisons


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pysystemtrade-db",
        type=Path,
        default=DEFAULT_PYSYSTEMTRADE_DB_PATH,
    )
    parser.add_argument(
        "--split-date",
        type=date.fromisoformat,
        default=DEFAULT_SPLIT_DATE,
    )
    parser.add_argument(
        "--fast-spans",
        type=_parse_fast_spans,
        default=DEFAULT_FAST_SPANS,
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    setup_logger()
    metrics, comparisons = run_ablation(
        db_path=args.pysystemtrade_db,
        split_date=args.split_date,
        fast_spans=args.fast_spans,
    )
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=220):
        print(metrics)
        print(comparisons)
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        metrics.write_csv(args.output_dir / "strategy_metrics.csv")
        comparisons.write_csv(args.output_dir / "paired_comparisons.csv")


if __name__ == "__main__":
    main()

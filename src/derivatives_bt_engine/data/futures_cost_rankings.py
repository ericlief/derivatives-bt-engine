"""Load cached futures rule costs and construct selection candidates.

The rule-cost cache is the durable boundary between expensive historical
EWMAC calibration and cheap portfolio-scenario filtering. This module keeps
legacy report readers working, but new selection runs derive their
capital, cost, size, liquidity, data, and execution gates from the cached
market and rule inputs each time.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl

from derivatives_bt_engine.calculations.instruments import INSTRUMENTS
from derivatives_bt_engine.data.pysystemtrade_ib import (
    load_executable_symbol_mapping,
)


DEFAULT_REPORT_PATTERNS = (
    "multi_speed_ewmac_rule_cache_*.parquet",
    "futures_market_screen_*.parquet",
    "pysystemtrade_rule_cache_*.parquet",
    "pysystemtrade_selection_screen_*.parquet",
    "pysystemtrade_selection_screen_*.csv",
    "pysystemtrade_cost_phase1_ib_*.parquet",
    "pysystemtrade_cost_phase1_ib_*.csv",
    "pysystemtrade_cost_phase1_*.parquet",
    "pysystemtrade_cost_phase1_*.csv",
    "futures_cost_risk_*.parquet",
    "futures_cost_risk_*.csv",
)

DEFAULT_SELECTION_INITIAL_CAPITAL_USD = 100_000.0
DEFAULT_SELECTION_TARGET_VOL = 0.20
DEFAULT_SELECTION_INSTRUMENT_COST_LIMIT_SR = 0.01
DEFAULT_SELECTION_LIQUIDITY_ANNUAL_TRADES = 25.6
DEFAULT_SELECTION_LIQUIDITY_BUSINESS_DAYS = 256
DEFAULT_SELECTION_MIN_DAILY_VOLUME = 100.0
DEFAULT_SELECTION_MAX_MARKET_VOLUME_PCT = 1.0

RANK_COLUMNS = {
    "cost": "cost_rank_in_asset_cls",
    "affordability": "afford_rank_in_asset_cls",
    "notional": "notional_rank_in_asset_cls",
}

LEGACY_RANK_COLUMNS = {
    "cost": "cost_rank_in_asset_class",
    "affordability": "affordability_rank_in_asset_class",
    "notional": "notional_rank_in_asset_class",
}

DEFAULT_VIEW_COLUMNS = (
    "symbol",
    "pst_instr_code",
    "hist_instr_code",
    "desc",
    "asset_cls",
    "econ_subcls",
    "region",
    "exec_prof",
    "exec_elig",
    "exec_restrict_reason",
    "cost_rank_in_asset_cls",
    "afford_rank_in_asset_cls",
    "notional_rank_in_asset_cls",
    "trade_sr",
    "roll_sr",
    "ref_ann_rolls",
    "spread_pts",
    "spread_src",
    "comm",
    "comm_ccy",
    "comm_usd",
    "comm_src",
    "init_margin_usd",
    "maint_margin_usd",
    "margin_ccy",
    "whatif_attempted",
    "whatif_elig",
    "cur_px",
    "ccy",
    "ib_mult",
    "px_magnifier",
    "mult",
    "notional",
    "ann_dvol",
    "avg_daily_volume",
    "risk_traded_usd_day",
    "mkt_risk_vol_usd_day",
    "pct_mkt_volume",
    "volume_elig",
    "risk_volume_elig",
    "cost_elig",
    "size_elig",
    "data_elig",
    "sel_bucket",
    "selection_elig",
    "selection_excl",
    "afford_cluster_role",
    "counts_toward_main_instr_min",
    "afford_scen_cap",
    "afford_scen_idm",
    "afford_min_main_instrs",
    "afford_main_cluster_count",
    "equal_wt_dvol_budget",
    "equal_wt_avg_pos",
    "equal_wt_meets_min_cons",
    "main_instr_afford_for_scen",
    "min_cap_usd_full_wt_idm1",
    "min_cap_usd_equal_wt",
    "elig_ewmac_rule_count",
    "elig_ewmac_rules",
    "pool_role",
    "incl_default_pool",
    "ib_avail",
)

SELECTION_CANDIDATE_GATE_COLUMNS = (
    "cost_elig",
    "size_elig",
    "liq_elig",
    "data_elig",
    "instr_has_elig_ewmac_rule",
    "exec_elig",
    "selection_elig",
)

SELECTION_SCREEN_PARAM_COLUMNS = (
    "init_cap_usd",
    "target_vol",
    "cost_lim_sr",
    "rule_cost_lim_sr",
    "liq_ann_trades",
    "liq_days",
    "min_daily_volume",
    "max_pct_mkt_volume",
)

SELECTION_CANDIDATE_COLUMNS = (
    "symbol",
    "pst_instr_code",
    "signal_symbol",
    "hist_instr_code",
    "desc",
    "asset_cls",
    "econ_subcls",
    "region",
    "econ_fam_id",
    "roll_policy_id",
    "dup_grp_id",
    "pool_role",
    "rep_instr",
    "exec_prof",
    "con_id",
    "expiry",
    "ib_symbol",
    "ib_exch",
    "ib_avail",
    "cur_px",
    "ccy",
    "mult",
    "notional",
    "ann_dvol",
    "vol_src",
    "init_cap_usd",
    "target_vol",
    "cost_lim_sr",
    "rule_cost_lim_sr",
    "liq_ann_trades",
    "liq_days",
    "min_daily_volume",
    "max_pct_mkt_volume",
    "max_ann_dvol",
    "avg_daily_volume",
    "volume_n",
    "volume_start",
    "volume_end",
    "volume_src",
    "risk_traded_usd_day",
    "mkt_risk_vol_usd_day",
    "pct_mkt_volume",
    "volume_elig",
    "risk_volume_elig",
    "liq_elig",
    "trade_sr",
    "roll_sr",
    "comm",
    "comm_ccy",
    "comm_usd",
    "comm_src",
    "init_margin_usd",
    "maint_margin_usd",
    "margin_ccy",
    "whatif_attempted",
    "whatif_elig",
    "ref_ann_rolls",
    "cost_elig",
    "size_elig",
    "data_elig",
    "elig_ewmac_rule_count",
    "elig_ewmac_rules",
    "instr_has_elig_ewmac_rule",
    "exec_elig",
    "selection_elig",
    "report_ts_ct",
)


def selection_screen_config(report: pl.DataFrame) -> dict[str, object]:
    """Return one consistent run configuration from a legacy screen report."""
    missing = sorted(set(SELECTION_SCREEN_PARAM_COLUMNS).difference(report.columns))
    if missing:
        raise ValueError(
            "market-screen report is missing Step 1 parameter fields "
            f"{missing}; rerun the current futures-cost-risk audit"
        )

    config: dict[str, object] = {}
    for column in SELECTION_SCREEN_PARAM_COLUMNS:
        values = report.get_column(column).drop_nulls().unique().to_list()
        if len(values) != 1 or report.get_column(column).null_count():
            raise ValueError(
                f"market-screen report has inconsistent {column}; expected one "
                "non-null run-level value"
            )
        config[column] = values[0]
    return config


def _persisted_run_value(
    report: pl.DataFrame,
    column: str,
) -> object | None:
    """Return one consistent cached run value, or ``None`` when absent.

    A rule cache intentionally omits selection-scenario parameters, whereas a
    legacy selection screen persists them on every row.  If a field exists it
    must still be complete and constant; silently accepting mixed scenarios
    would make candidate reconstruction non-reproducible.
    """
    if column not in report.columns:
        return None
    series = report.get_column(column)
    values = series.drop_nulls().unique().to_list()
    if len(values) != 1 or series.null_count():
        raise ValueError(
            f"cached report has inconsistent {column}; expected one "
            "non-null run-level value"
        )
    return values[0]


def resolve_selection_config(
    report: pl.DataFrame,
    *,
    initial_capital_usd: float | None = None,
    target_vol: float | None = None,
    instrument_cost_limit_sr: float | None = None,
    liquidity_ann_trades: float | None = None,
    liquidity_business_days: int | None = None,
    min_daily_volume: float | None = None,
    max_market_volume_pct: float | None = None,
) -> dict[str, object]:
    """Resolve one selection scenario from CLI overrides, cache, and defaults.

    CLI values have precedence.  Legacy screens then provide backward-
    compatible persisted values.  A scenario-neutral rule cache falls through
    to the documented defaults.  ``rule_cost_lim_sr`` is always read from the
    cache because changing it requires rebuilding eligible-rule statistics.
    """
    requested = {
        "init_cap_usd": initial_capital_usd,
        "target_vol": target_vol,
        "cost_lim_sr": instrument_cost_limit_sr,
        "liq_ann_trades": liquidity_ann_trades,
        "liq_days": liquidity_business_days,
        "min_daily_volume": min_daily_volume,
        "max_pct_mkt_volume": max_market_volume_pct,
    }
    defaults: dict[str, object] = {
        "init_cap_usd": DEFAULT_SELECTION_INITIAL_CAPITAL_USD,
        "target_vol": DEFAULT_SELECTION_TARGET_VOL,
        "cost_lim_sr": DEFAULT_SELECTION_INSTRUMENT_COST_LIMIT_SR,
        "liq_ann_trades": DEFAULT_SELECTION_LIQUIDITY_ANNUAL_TRADES,
        "liq_days": DEFAULT_SELECTION_LIQUIDITY_BUSINESS_DAYS,
        "min_daily_volume": DEFAULT_SELECTION_MIN_DAILY_VOLUME,
        "max_pct_mkt_volume": DEFAULT_SELECTION_MAX_MARKET_VOLUME_PCT,
    }
    config: dict[str, object] = {}
    for column, cli_value in requested.items():
        cached_value = _persisted_run_value(report, column)
        config[column] = (
            cli_value
            if cli_value is not None
            else cached_value if cached_value is not None else defaults[column]
        )
    rule_limit = _persisted_run_value(report, "rule_cost_lim_sr")
    if rule_limit is None:
        raise ValueError(
            "cached report is missing rule_cost_lim_sr; rebuild it with "
            "futures-rule-cache"
        )
    config["rule_cost_lim_sr"] = rule_limit

    for column, value in config.items():
        if float(value) <= 0:
            raise ValueError(f"{column} must be positive")
    return config


def apply_selection_filters(
    report: pl.DataFrame,
    *,
    config: dict[str, object],
) -> pl.DataFrame:
    """Attach scenario-dependent hard gates to a cached rule-cost report.

    The input contains observed executable-contract data and cached rule
    eligibility.  This function performs no IB or historical-data request. It
    derives the account risk budget, market participation, cost, data, and
    execution gates for exactly one selection scenario and retains every row
    so rejection reasons can be written before candidate filtering.
    """
    required = {
        "symbol",
        "ann_dvol",
        "avg_daily_volume",
        "instr_has_elig_ewmac_rule",
        "exec_elig",
        "ib_avail",
    }
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "rule-cost cache cannot construct a selection scenario; missing "
            f"fields {missing}"
        )

    if "whatif_attempted" not in report.columns:
        report = report.with_columns(pl.lit(False).alias("whatif_attempted"))
    if "whatif_elig" not in report.columns:
        report = report.with_columns(pl.lit(False).alias("whatif_elig"))

    if "trade_sr" in report.columns:
        cost_eligible = (
            pl.col("trade_sr").is_not_null()
            & pl.col("trade_sr").is_finite()
            & (pl.col("trade_sr") <= float(config["cost_lim_sr"]))
        )
    elif "cost_elig" in report.columns:
        # Compatibility for compact historical test/screens that persisted
        # the decision but not its underlying trade-SR value.
        cost_eligible = pl.col("cost_elig").fill_null(False)
    else:
        raise ValueError("rule-cost cache is missing trade_sr")

    data_inputs = {"cur_px", "cur_fx_to_usd", "mult"}
    if data_inputs.issubset(report.columns):
        data_eligible = (
            pl.col("cur_px").is_not_null()
            & pl.col("cur_px").is_finite()
            & (pl.col("cur_px") > 0)
            & pl.col("cur_fx_to_usd").is_not_null()
            & pl.col("cur_fx_to_usd").is_finite()
            & (pl.col("cur_fx_to_usd") > 0)
            & pl.col("mult").is_not_null()
            & pl.col("mult").is_finite()
            & (pl.col("mult").abs() > 0)
            & pl.col("avg_daily_volume").is_not_null()
            & pl.col("ann_dvol").is_not_null()
            & (pl.col("ib_avail") == "contract_qualified")
            & (
                ~pl.col("whatif_attempted").fill_null(False)
                | pl.col("whatif_elig").fill_null(False)
            )
        )
    elif "data_elig" in report.columns:
        # Full current caches always take the branch above. This fallback only
        # keeps previously generated compact screens readable.
        data_eligible = pl.col("data_elig").fill_null(False)
    else:
        missing_data_inputs = sorted(data_inputs.difference(report.columns))
        raise ValueError(
            "rule-cost cache cannot reconstruct data eligibility; missing "
            f"fields {missing_data_inputs}"
        )

    initial_capital = float(config["init_cap_usd"])
    target_volatility = float(config["target_vol"])
    cost_limit = float(config["cost_lim_sr"])
    annual_trades = float(config["liq_ann_trades"])
    business_days = int(config["liq_days"])
    minimum_volume = float(config["min_daily_volume"])
    maximum_participation = float(config["max_pct_mkt_volume"])
    maximum_contract_dvol = initial_capital * target_volatility
    daily_risk_traded = (
        initial_capital * target_volatility * annual_trades / business_days
    )

    filtered = report.with_columns(
        pl.lit(initial_capital).alias("init_cap_usd"),
        pl.lit(target_volatility).alias("target_vol"),
        pl.lit(cost_limit).alias("cost_lim_sr"),
        pl.lit(float(config["rule_cost_lim_sr"])).alias("rule_cost_lim_sr"),
        pl.lit(annual_trades).alias("liq_ann_trades"),
        pl.lit(business_days).alias("liq_days"),
        pl.lit(minimum_volume).alias("min_daily_volume"),
        pl.lit(maximum_participation).alias("max_pct_mkt_volume"),
        pl.lit(maximum_contract_dvol).alias("max_ann_dvol"),
        pl.lit(daily_risk_traded).alias("risk_traded_usd_day"),
        (pl.col("avg_daily_volume") * pl.col("ann_dvol")).alias(
            "mkt_risk_vol_usd_day"
        ),
    ).with_columns(
        pl.when(pl.col("mkt_risk_vol_usd_day") > 0)
        .then(
            100.0
            * pl.col("risk_traded_usd_day")
            / pl.col("mkt_risk_vol_usd_day")
        )
        .otherwise(None)
        .alias("pct_mkt_volume"),
        cost_eligible.alias("cost_elig"),
        (
            pl.col("ann_dvol").is_not_null()
            & pl.col("ann_dvol").is_finite()
            & (pl.col("ann_dvol") <= maximum_contract_dvol)
        ).alias("size_elig"),
        (
            pl.col("avg_daily_volume").is_not_null()
            & pl.col("avg_daily_volume").is_finite()
            & (pl.col("avg_daily_volume") > minimum_volume)
        ).alias("volume_elig"),
        data_eligible.alias("data_elig"),
    ).with_columns(
        (
            pl.col("pct_mkt_volume").is_not_null()
            & pl.col("pct_mkt_volume").is_finite()
            & (pl.col("pct_mkt_volume") < maximum_participation)
        ).alias("risk_volume_elig"),
    ).with_columns(
        (
            pl.col("volume_elig").fill_null(False)
            & pl.col("risk_volume_elig").fill_null(False)
        ).alias("liq_elig"),
    ).with_columns(
        (
            pl.col("cost_elig").fill_null(False)
            & pl.col("size_elig").fill_null(False)
            & pl.col("liq_elig").fill_null(False)
            & pl.col("data_elig").fill_null(False)
            & pl.col("instr_has_elig_ewmac_rule").fill_null(False)
            & pl.col("exec_elig").fill_null(False)
            & (pl.col("ib_avail") == "contract_qualified")
        ).alias("selection_elig"),
        (
            pl.when(~pl.col("data_elig"))
            .then(pl.lit("dont_add_no_data"))
            .when(
                pl.col("cost_elig")
                & pl.col("size_elig")
                & pl.col("volume_elig")
                & pl.col("risk_volume_elig")
            )
            .then(pl.lit("add_first"))
            .otherwise(pl.lit("add_later"))
        ).alias("sel_bucket"),
    )

    exclusions: list[str] = []
    for row in filtered.iter_rows(named=True):
        reasons: list[str] = []
        if not row.get("data_elig"):
            reasons.append("data_unavailable")
        if not row.get("cost_elig"):
            reasons.append("cost")
        if not row.get("size_elig"):
            reasons.append("risk_size")
        if row.get("avg_daily_volume") is None:
            reasons.append("volume_unavailable")
        else:
            if not row.get("volume_elig"):
                reasons.append("contract_volume")
            if not row.get("risk_volume_elig"):
                reasons.append("risk_volume")
        if not row.get("instr_has_elig_ewmac_rule"):
            reasons.append("no_eligible_rule")
        if not row.get("exec_elig"):
            reasons.append("execution")
        if row.get("ib_avail") != "contract_qualified":
            reasons.append("ib_unavailable")
        exclusions.append(",".join(reasons))
    return filtered.with_columns(
        pl.Series("selection_excl", exclusions, dtype=pl.String)
    )


def override_selection_scenario(
    report: pl.DataFrame,
    *,
    initial_capital_usd: float | None = None,
    target_vol: float | None = None,
) -> pl.DataFrame:
    """Recalculate risk-budget-dependent gates from a market-screen report.

    The market-screen report already persists annual dollar volatility, market risk
    volume, and every non-capital gate.  This function changes only the run's
    initial capital and/or target volatility and derives the corresponding
    maximum contract dollar volatility, daily risk traded, market-volume
    percentage, size gate, relative-liquidity gate, and final selection
    eligibility. It performs no market-data or history request, which lets a
    robustness grid reuse one cached executable-market screen.

    Parameters
    ----------
    report
        Complete public market-screen cost report, before Step 1 filtering.
    initial_capital_usd
        Replacement account capital in USD. ``None`` retains the persisted
        value.
    target_vol
        Replacement annual portfolio volatility target as a decimal. ``None``
        retains the persisted value.

    Returns
    -------
    polars.DataFrame
        A new report with consistent run-level parameters and gate columns.
    """
    config = selection_screen_config(report)
    selected_capital = (
        float(config["init_cap_usd"])
        if initial_capital_usd is None
        else float(initial_capital_usd)
    )
    selected_target_vol = (
        float(config["target_vol"])
        if target_vol is None
        else float(target_vol)
    )
    if selected_capital <= 0:
        raise ValueError("initial_capital_usd must be positive")
    if selected_target_vol <= 0:
        raise ValueError("target_vol must be positive")
    override_config = {
        **config,
        "init_cap_usd": selected_capital,
        "target_vol": selected_target_vol,
    }
    return apply_selection_filters(report, config=override_config)


def override_selection_initial_capital(
    report: pl.DataFrame,
    initial_capital_usd: float,
) -> pl.DataFrame:
    """Recalculate selection gates for a replacement account capital.

    This compatibility wrapper preserves the original public helper while the
    more general scenario function also supports target-volatility sweeps.
    """
    return override_selection_scenario(
        report,
        initial_capital_usd=initial_capital_usd,
    )


def selection_candidates(report: pl.DataFrame) -> pl.DataFrame:
    """Select candidates using only gates saved by the market screen.

    This function deliberately performs no market-data request and no metric
    calculation. It filters the complete market-screen audit using its persisted
    cost, dollar-vol size, liquidity, data, rule, execution, and IB contract
    decisions, then returns the compact columns needed by the next stage.
    """
    # Older saved reports used phase-numbered gate names; normalize at this
    # input boundary so all new filtering and output uses function names.
    if "selection_elig" not in report.columns:
        legacy_gate = next(
            (
                column for column in ("phase2_elig", "phase2_eligible")
                if column in report.columns
            ),
            None,
        )
        if legacy_gate is not None:
            report = report.with_columns(
                pl.col(legacy_gate).alias("selection_elig")
            )
    if "selection_excl" not in report.columns and "phase2_excl" in report.columns:
        report = report.with_columns(pl.col("phase2_excl").alias("selection_excl"))

    required = {
        *SELECTION_CANDIDATE_GATE_COLUMNS,
        "ib_avail",
        "symbol",
        "asset_cls",
        "econ_subcls",
    }
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "market-screen report is missing Step 1 selection fields "
            f"{missing}; rerun the current futures-cost-risk audit"
        )
    selection_screen_config(report)

    gate = pl.lit(True)
    for column in SELECTION_CANDIDATE_GATE_COLUMNS:
        gate &= pl.col(column).fill_null(False)
    gate &= pl.col("ib_avail").fill_null("") == "contract_qualified"

    selected = report.filter(gate)
    columns = [name for name in SELECTION_CANDIDATE_COLUMNS if name in selected.columns]
    selected = selected.select(columns)
    sort_columns = [
        name for name in ("asset_cls", "symbol") if name in selected.columns
    ]
    return selected.sort(sort_columns) if sort_columns else selected


def selection_search_universe(
    report: pl.DataFrame,
    *,
    eligible_column: str = "selection_elig",
) -> pl.DataFrame:
    """Return only rows that passed the market-screen hard pre-selection gates."""
    if eligible_column not in report.columns and eligible_column == "selection_elig":
        eligible_column = next(
            (
                column
                for column in (
                    "selection_eligible",
                    "phase2_elig",
                    "phase2_eligible",
                )
                if column in report.columns
            ),
            eligible_column,
        )
    if eligible_column not in report.columns:
        raise ValueError(
            "Report has no selection-eligibility column; "
            "rerun the market-screen audit"
        )
    filtered = report.filter(pl.col(eligible_column).fill_null(False))
    sort_columns = [
        name for name in ("asset_cls", "asset_class", "symbol")
        if name in filtered.columns
    ]
    return filtered.sort(sort_columns) if sort_columns else filtered


def find_latest_market_report(
    report_dir: Path | str = ".",
    *,
    patterns: Sequence[str] = DEFAULT_REPORT_PATTERNS,
) -> Path:
    """Find the newest market-screen report in ``report_dir`` or its results folder.

    New reports are Parquet; CSV patterns remain available for legacy runs.
    Report-family precedence prefers the scenario-neutral rule cache, then the
    legacy selection screen and progressively broader market-screen report names.
    """
    root = Path(report_dir).expanduser()
    search_dirs = (root, root / "results")
    candidates: list[Path] = []
    for pattern in patterns:
        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue
            for path in search_dir.glob(pattern):
                if path.is_file():
                    candidates.append(path.resolve())
        if candidates:
            # Prefer the most-specific pattern, particularly the online IB
            # report, before falling back to broader report names.
            break
    if not candidates:
        pattern_text = ", ".join(patterns)
        raise FileNotFoundError(
            f"No market-screen cost report found under {root.resolve()} "
            f"(patterns: {pattern_text})"
        )
    return max(set(candidates), key=lambda path: path.stat().st_mtime_ns)


def load_latest_market_report(
    report_dir: Path | str = ".",
    *,
    patterns: Sequence[str] = DEFAULT_REPORT_PATTERNS,
) -> tuple[Path, pl.DataFrame]:
    """Load the newest market-screen report and return its resolved path.

    Parquet preserves the report's dates, booleans, and numeric schema. CSV is
    read only to maintain compatibility with previously saved reports.
    """
    path = find_latest_market_report(report_dir, patterns=patterns)
    return path, read_market_report(path)


def read_market_report(path: Path | str) -> pl.DataFrame:
    """Read one market report and normalize legacy instrument identities.

    Reports written before executable symbols were separated from history
    provider codes stored the provider code in ``symbol``. The reader upgrades
    those frames so every downstream selection output uses the current public
    contract: ``symbol`` is the executable key, ``pst_instr_code`` retains the
    provider identity, and ``signal_symbol`` identifies the signal market.
    """
    report_path = Path(path)
    if report_path.suffix.lower() == ".parquet":
        report = pl.read_parquet(report_path)
    elif report_path.suffix.lower() == ".csv":
        report = pl.read_csv(report_path, infer_schema_length=None)
    else:
        raise ValueError(
            f"Unsupported market report format {report_path.suffix!r}; "
            "expected .parquet or legacy .csv"
        )
    return normalize_report_instrument_identities(report)


def normalize_report_instrument_identities(
    report: pl.DataFrame,
) -> pl.DataFrame:
    """Upgrade legacy report identity columns to the public symbol contract.

    Parameters
    ----------
    report
        Public market or rule-cost report. Current reports already contain
        ``pst_instr_code`` and pass through unchanged.

    Returns
    -------
    polars.DataFrame
        The original frame for current schemas, or a legacy frame with its
        provider code moved to ``pst_instr_code`` and its executable and signal
        symbols resolved from the bundled mappings.
    """
    if "symbol" not in report.columns or "pst_instr_code" in report.columns:
        return report

    is_overlay = (
        pl.col("mapping_stat") == "local_execution_overlay"
        if "mapping_stat" in report.columns
        else pl.lit(False)
    )
    provider_code = (
        pl.when(is_overlay)
        .then(pl.col("hist_instr_code"))
        .otherwise(pl.col("symbol"))
        if "hist_instr_code" in report.columns
        else pl.col("symbol")
    )
    normalized = report.with_columns(
        provider_code.cast(pl.String).alias("pst_instr_code"),
        pl.col("symbol").alias("legacy_symbol"),
    )

    aliases = load_executable_symbol_mapping().select(
        "pst_instr_code",
        pl.col("symbol").alias("mapped_exec_symbol"),
    )
    normalized = normalized.join(
        aliases,
        on="pst_instr_code",
        how="left",
        validate="m:1",
        maintain_order="left",
    )
    executable_fallbacks = [pl.col("mapped_exec_symbol")]
    if "ib_symbol" in normalized.columns:
        executable_fallbacks.append(pl.col("ib_symbol"))
    executable_fallbacks.append(pl.col("legacy_symbol"))
    normalized = normalized.with_columns(
        pl.when(is_overlay)
        .then(pl.col("legacy_symbol"))
        .otherwise(pl.coalesce(executable_fallbacks))
        .alias("symbol")
    )

    signal_rows = [
        {
            "symbol": symbol,
            "mapped_signal_symbol": (
                spec.get("signal_symbol")
                or spec.get("ib_symbol")
                or symbol
            ),
        }
        for symbol, spec in INSTRUMENTS.items()
    ]
    if signal_rows:
        normalized = normalized.join(
            pl.DataFrame(signal_rows),
            on="symbol",
            how="left",
            validate="m:1",
            maintain_order="left",
        )
        signal_fallbacks = [pl.col("mapped_signal_symbol")]
        if "ib_symbol" in normalized.columns:
            signal_fallbacks.append(pl.col("ib_symbol"))
        if "signal_symbol" in normalized.columns:
            signal_fallbacks.append(pl.col("signal_symbol"))
        signal_fallbacks.append(pl.col("symbol"))
        normalized = normalized.with_columns(
            pl.coalesce(signal_fallbacks).alias("signal_symbol")
        ).drop("mapped_signal_symbol")

    normalized = normalized.drop("legacy_symbol", "mapped_exec_symbol")
    columns = normalized.columns
    columns.remove("pst_instr_code")
    columns.insert(columns.index("symbol") + 1, "pst_instr_code")
    return normalized.select(columns)


# Compatibility aliases for notebooks and scripts written before the pipeline
# was organized around market screening and portfolio selection functions.
PHASE2_STEP1_GATE_COLUMNS = SELECTION_CANDIDATE_GATE_COLUMNS
PHASE2_STEP1_PARAM_COLUMNS = SELECTION_SCREEN_PARAM_COLUMNS
PHASE2_STEP1_COLUMNS = SELECTION_CANDIDATE_COLUMNS
phase2_step1_config = selection_screen_config
resolve_phase2_selection_config = resolve_selection_config
apply_phase2_selection_filters = apply_selection_filters
override_phase2_selection_scenario = override_selection_scenario
override_phase2_initial_capital = override_selection_initial_capital
phase2_step1_candidates = selection_candidates
phase2_search_universe = selection_search_universe
find_latest_phase1_cost_report = find_latest_market_report
load_latest_phase1_cost_report = load_latest_market_report
read_phase1_cost_report = read_market_report


def _resolve_rank_column(report: pl.DataFrame, rank_by: str) -> str:
    rank_column = RANK_COLUMNS.get(rank_by, rank_by)
    if rank_column not in report.columns and rank_by in LEGACY_RANK_COLUMNS:
        rank_column = LEGACY_RANK_COLUMNS[rank_by]
    if rank_column not in report.columns:
        choices = ", ".join(RANK_COLUMNS)
        raise ValueError(
            f"Unknown or unavailable rank {rank_by!r}; use one of "
            f"{choices}, or an existing report column"
        )
    return rank_column


def top_n_by_asset_class(
    report: pl.DataFrame,
    *,
    n: int = 5,
    rank_by: str = "cost",
    asset_classes: Iterable[str] | None = None,
    columns: Sequence[str] | None = DEFAULT_VIEW_COLUMNS,
    eligible_only: bool = True,
) -> dict[str, pl.DataFrame]:
    """Return the top ``n`` rows in each asset class, sorted rank 1 first.

    ``rank_by`` accepts ``cost``, ``affordability``, ``notional``, or an
    explicit numeric column name. Pass ``columns=None`` to retain every source
    column rather than the compact notebook view. By default, rows explicitly
    restricted by our execution profile are excluded; older reports without
    the eligibility column remain readable. Rank 1 is the lowest value for
    cost, affordability, and notional.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    asset_class_column = (
        "asset_cls" if "asset_cls" in report.columns else "asset_class"
    )
    if asset_class_column not in report.columns:
        raise ValueError("Report has no asset class column")

    rank_column = _resolve_rank_column(report, rank_by)
    execution_eligible_column = (
        "exec_elig" if "exec_elig" in report.columns else "execution_eligible"
    )
    if eligible_only and execution_eligible_column in report.columns:
        report = report.filter(
            pl.col(execution_eligible_column).fill_null(False)
        )
    if asset_classes is None:
        classes = sorted(
            value
            for value in report.get_column(asset_class_column).unique().to_list()
            if value is not None
        )
    else:
        classes = list(dict.fromkeys(asset_classes))

    selected_columns = None
    if columns is not None:
        selected_columns = [name for name in columns if name in report.columns]
        if rank_column not in selected_columns:
            selected_columns.append(rank_column)

    rankings: dict[str, pl.DataFrame] = {}
    for asset_class in classes:
        ranked = (
            report
            .filter(pl.col(asset_class_column) == asset_class)
            .sort([rank_column, "symbol"], nulls_last=True)
            .head(n)
        )
        if selected_columns is not None:
            ranked = ranked.select(selected_columns)
        rankings[asset_class] = ranked
    return rankings


def display_asset_class_rankings(
    rankings: dict[str, pl.DataFrame],
) -> None:
    """Render each asset class as a separate table in a Jupyter notebook."""
    try:
        from IPython.display import Markdown, display
    except ImportError as exc:  # pragma: no cover - project includes ipykernel
        raise RuntimeError("Jupyter/IPython is required to display rankings") from exc

    for asset_class, frame in rankings.items():
        display(Markdown(f"### {asset_class}"))
        display(frame)

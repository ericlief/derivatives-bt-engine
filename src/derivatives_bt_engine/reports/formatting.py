"""Apply the shared numeric precision contract at public report boundaries.

Calculation dataframes retain full precision. Public report producers call
``round_public_report`` only on the copy being persisted or returned for
display, which prevents presentation rounding from feeding back into costs,
selection, or portfolio calculations.
"""

from __future__ import annotations

import polars as pl


# Internal calculations keep unit-explicit names. Public reports rely on the
# project convention that primary monetary values are USD and shorten only the
# boundary names here so every producer uses the same vocabulary.
PUBLIC_REPORT_RENAMES = {
    "pysystemtrade_instrument_code": "pst_instr_code",
    "target_volatility": "target_vol",
    "maximum_instrument_margin_percent": "max_instr_marg_pct",
    "maximum_book_margin_percent": "max_book_marg_pct",
    "maximum_weight_idm": "max_weight_idm",
    "maximum_instruments_per_cluster": "max_instr_per_cluster",
    "maximum_instruments_per_class": "max_instr_per_cls",
    "maximum_instruments_per_subclass": "max_instr_per_subcls",
    "minimum_instruments_per_class": "min_instr_per_cls",
    "minimum_instruments_per_subclass": "min_instr_per_subcls",
    "minimum_book_count": "min_book_n",
    "maximum_asset_class_weight_percent": "max_cls_weight_pct",
    "maximum_economic_subclass_weight_percent": "max_subcls_weight_pct",
    "asset_class_weight": "cls_weight",
    "limited_asset_class_weight": "cls_lim_weight",
    "economic_subclass_weight": "subcls_weight",
    "limited_economic_subclass_weight": "subcls_lim_weight",
    "group_limited_weight": "group_lim_weight",
    "unallocated_weight": "unalloc_weight",
    "include_roll_cost_in_score": "roll_cost_in_score",
    "elapsed_seconds": "elapsed_s",
    "number_instruments": "n_instr",
    "number_asset_classes": "n_asset_cls",
    "number_economic_subclasses": "n_econ_subcls",
    "selected_symbols": "symbols",
    "combined_forecast": "comb_fcst",
    "forecast_month": "fcst_month",
    "goulding_signal_mode": "g_mode",
    "goulding_fast_months": "g_fast_months",
    "goulding_slow_months": "g_slow_months",
    "goulding_cluster": "g_cluster",
    "point_change": "pt_change",
    "point_vol": "pt_vol",
    "combined_nulls": "comb_nulls",
    "combined_nonfinite": "comb_nonfinite",
    "forecast_valid": "fcst_valid",
    "source_gap_before": "src_gap_before",
    "quality_flag": "hist_quality",
    "source_gap_days": "src_gap_days",
    "forecast_valid_obs": "fcst_valid_obs",
    "forecast_invalid_obs": "fcst_inval_obs",
    "forecast_post_warmup_invalid_obs": "fcst_post_warmup_inval_n",
    "forecast_start": "fcst_start",
    "forecast_end": "fcst_end",
    "forecast_elig": "fcst_elig",
    "forecast_excl": "fcst_excl",
    "period_year_fraction": "yr_frac",
    "sizing_capital_usd": "sizing_cap",
    "capital_scalar": "cap_scalar",
    "backtest_capital_mode": "cap_mode",
    "selection_active": "sel_active",
    "selection_rebalanced": "sel_rebalanced",
    "selection_date": "sel_date",
    "selection_effective_date": "sel_date",
    "selection_year": "sel_year",
    "selection_status": "sel_status",
    "selection_incumbent": "sel_incumbent",
    "selection_rank": "sel_rank",
    "selection_rebalance": "sel_rebalance",
    "selection_max_history_gap_days": "sel_max_hist_gap_days",
    "selection_equity_usd": "sel_equity",
    "selection_candidate_count": "sel_cand_n",
    "selection_instrument_count": "sel_instr_n",
    "selection_score": "sel_score",
    "selection_idm": "sel_idm",
    "selection_symbols": "sel_symbols",
    "selection_unbuffered_symbols": "sel_unbuf_symbols",
    "selection_ranked_symbols": "sel_ranked_symbols",
    "selection_rank_buffered_symbols": "sel_rank_buf_symbols",
    "selection_hysteresis_candidate_symbols": "sel_hyst_cand_symbols",
    "selection_added_symbols": "sel_added",
    "selection_removed_symbols": "sel_removed",
    "unbuffered_class_coverage_deficit": "unbuf_cls_coverage_deficit",
    "unbuffered_subclass_coverage_deficit": "unbuf_subcls_coverage_deficit",
    "unbuffered_book_count_deficit": "unbuf_book_n_deficit",
    "final_class_coverage_deficit": "final_cls_coverage_deficit",
    "final_subclass_coverage_deficit": "final_subcls_coverage_deficit",
    "final_book_count_deficit": "final_book_n_deficit",
    "coverage_satisfied": "coverage_satisfied",
    "coverage_reason": "coverage_reason",
    "coverage_phase": "coverage_phase",
    "coverage_eligible": "coverage_elig",
    "instr_rank_in_subclass": "instr_rank_in_subcls",
    "subclass_rank_in_class": "subcls_rank_in_cls",
    "class_rank": "cls_rank",
    "global_trial_rank": "global_trial_rank",
    "selection_eligibility_hysteresis_percent": "sel_elig_hyst_pct",
    # Read legacy internal frames while emitting only the canonical public name.
    "selection_eligibility_exit_buffer_percent": "sel_elig_hyst_pct",
    "selection_membership_hysteresis": "sel_membership_hyst",
    "selection_rank_buffer_percent": "sel_rank_buf_pct",
    "selection_rank_count_ewma_halflife_reviews": "sel_rank_n_ewma_hl",
    "selection_book_score_buffer_percent": "sel_book_score_buf_pct",
    "selection_live_execution_overlay_count": "sel_live_exec_overlay_n",
    "preference_rank": "pref_rank",
    "selection_prefer_full_contracts": "sel_pref_full_cons",
    "is_micro_contract": "micro_con",
    "rank_buffer_count": "rank_buf_n",
    "rank_count_ewma_state": "rank_n_ewma_state",
    "rank_count_anchor": "rank_n_anchor",
    "rank_entry_cutoff": "rank_entry",
    "rank_exit_cutoff": "rank_exit",
    "rank_buffer_reason": "rank_buf_reason",
    "min_max_position": "min_max_pos",
    "max_market_volume_percent": "max_mkt_volume_pct",
    "market_participation_enforced": "mkt_participation_enforced",
    "unbuffered_book_score": "unbuf_book_score",
    "rank_buffered_book_score": "rank_buf_book_score",
    "hysteresis_candidate_book_score": "hyst_cand_book_score",
    "switch_cost_usd": "switch_cost",
    "score_buffer_sr": "score_buf_sr",
    "selection_cost_source": "sel_cost_src",
    "selection_margin_source": "sel_marg_src",
    "live_tail_start": "live_tail_start",
    "live_execution_overlay_active": "live_exec_overlay_active",
    "live_volume_eligible": "live_volume_elig",
    "live_execution_eligible": "live_exec_elig",
    "live_ib_availability": "live_ib_avail",
    "margin_calibration_timestamp": "marg_calib_ts",
    "margin_risk_calibration_source": "marg_risk_calib_src",
    "initial_capital_volatility_scalar": "init_vol_scalar",
    "maximum_instrument_margin_usd": "max_instr_marg",
    "book_margin_at_maximum_usd": "book_marg_at_max",
    "initial_margin_to_annual_dollar_vol": "init_marg_ann_dvol_ratio",
    "initial_margin_to_ann_dvol": "init_marg_ann_dvol_ratio",
    "carver_spread_pts": "ref_spread_pts",
    "trade_cost_usd_per_con": "trade_cost_per_con",
    "roll_cost_usd_per_con": "roll_cost_per_con",
    "ann_trade_cost_usd": "ann_trade_cost",
    "ann_roll_cost_usd": "ann_roll_cost",
    "ann_cost_usd": "ann_cost",
    "buffer_size": "buf_size",
    "buffer_trade_to_edge": "buf_trade_to_edge",
    "marginal_contribution_score": "marg_score",
    "scaled_contribution": "scaled_contrib",
    "low_contract_penalty_scale": "low_con_penalty_scale",
    "high_contract_penalty_scale": "high_con_penalty_scale",
    "contract_band_min": "con_band_min",
    "contract_band_max": "con_band_max",
    "score_tie_tolerance_percent": "score_tie_tol_pct",
    "score_tie_tolerance_pct": "score_tie_tol_pct",
    "tie_ref_book_count": "tie_ref_book_n",
    "low_contract_weight": "low_con_weight",
    "band_contract_weight": "band_con_weight",
    "high_contract_weight": "high_con_weight",
    "ref_weight": "ref_weight",
    "ref_book_count": "ref_book_n",
    "ref_idm": "ref_idm",
    "ref_weight_idm": "ref_weight_idm",
    "ref_cap_alloc_usd": "ref_cap_alloc",
    "port_ann_risk_target_usd": "port_ann_risk_target",
    "ref_ann_risk_usd": "ref_ann_risk",
    "ref_daily_risk_usd": "ref_daily_risk",
    "ref_gross_usd": "ref_gross",
    "ref_cost_usd": "ref_cost",
    "ref_net_usd": "ref_net",
    "cum_ref_gross_usd": "cum_ref_gross",
    "cum_ref_net_usd": "cum_ref_net",
    "ref_average_position": "vol_scalar",
    "smoothed_ref_average_position": "ref_avg_pos",
    "ref_smooth_avg_pos": "ref_avg_pos",
    "ref_target_position": "ref_tgt_pos",
    "ref_normalized_position": "ref_norm_pos",
    "held_ref_position": "ref_held_pos",
    "ref_abs_position_change": "ref_abs_pos_change",
    "ref_abs_position_changes": "ref_abs_pos_change",
    "ref_rolling_mean_abs_position_change": (
        "ref_roll_mean_abs_pos_change"
    ),
    "ref_rolling_ann_turnover": "ref_roll_ann_turn",
    # The reference-position calculation currently emits this compact
    # intermediate name; normalize it at the same public schema boundary.
    "ref_roll_ann_turnover": "ref_roll_ann_turn",
    "daily_dollar_vol_per_contract": "daily_dvol",
    "annual_dollar_vol_per_contract": "ann_dvol",
    "annual_percentage_vol": "ann_pct_vol",
    "contract_close": "con_px",
    "buffer_top_cons": "buffer_top_con",
    "buffer_bot_cons": "buffer_bot_con",
    "exec_cons": "exec_con",
    "held_cons": "held_con",
    "signed_trade_cons": "trade_con",
    "roll_cons": "roll_con",
    "ann_trade_cons": "ann_trade_con",
    "ann_roll_cons": "ann_roll_con",
    "max_cons": "max_con",
    "ref_return_valid": "ref_ret_valid",
    "ref_return_valid_obs": "ref_ret_valid_obs",
    "ref_return_post_start_invalid_obs": (
        "ref_ret_post_start_invalid_obs"
    ),
    "ref_return_start": "ref_ret_start",
    "ref_return_end": "ref_ret_end",
    # A per-instrument executable cash frame is already identified by its
    # position/cost fields and is commonly joined to forecast/reference data.
    # Keep the field names short there; portfolio aggregates use ``port_``
    # to distinguish the combined book from one instrument's cash curve.
    "cash_fx_valid": "pnl_fx_valid",
    "cash_pnl_valid": "pnl_valid",
    "cash_gross_pnl_usd": "gross_pnl",
    "cash_trade_cost_usd": "trade_cost",
    "cash_roll_cost_usd": "roll_cost",
    "cash_net_pnl_usd": "net_pnl",
    "cum_cash_gross_pnl_usd": "cum_gross_pnl",
    "cum_cash_net_pnl_usd": "cum_net_pnl",
    # Older compact cash schemas are accepted as inputs by audit/notebook tools.
    "gross_pnl_usd": "gross_pnl",
    "trade_cost_usd": "trade_cost",
    "roll_cost_usd": "roll_cost",
    "net_pnl_usd": "net_pnl",
    "cum_gross_pnl_usd": "cum_gross_pnl",
    "cum_net_pnl_usd": "cum_net_pnl",
    "cash_gross_pct": "gross_pct",
    "cash_trade_cost_pct": "trade_cost_pct",
    "cash_roll_cost_pct": "roll_cost_pct",
    "cash_total_cost_pct": "total_cost_pct",
    "cash_net_pct": "net_pct",
    "cum_cash_gross_pct": "cum_gross_pct",
    "cum_cash_trade_cost_pct": "cum_trade_cost_pct",
    "cum_cash_roll_cost_pct": "cum_roll_cost_pct",
    "cum_cash_total_cost_pct": "cum_total_cost_pct",
    "cum_cash_net_pct": "cum_net_pct",
    "ref_max_position": "ref_max_pos",
    "ref_contract_band_distance": "ref_con_band_distance",
    "candidate_ref_max_position": "cand_ref_max_pos",
    "candidate_ref_contract_band_distance": (
        "cand_ref_con_band_distance"
    ),
    "candidate_ref_con_band_distance": "cand_ref_con_band_distance",
    "low_contract_penalty_sr": "low_con_penalty_sr",
    "high_contract_penalty_sr": "high_con_penalty_sr",
    "contract_band_penalty_sr": "con_band_penalty_sr",
    "annual_contract_load": "ann_con_load",
    "init_margin": "init_marg_native",
    "init_margin_usd": "init_marg",
    "maint_margin": "maint_marg_native",
    "maint_margin_usd": "maint_marg",
    "cont_margin_at_max_usd": "cont_marg_at_max",
    "margin_at_max_usd": "marg_at_max",
    "margin_limit_usd": "marg_lim",
    "instr_margin_limit_usd": "instr_marg_lim",
    "book_margin_limit_usd": "book_marg_lim",
    "book_margin_remaining_usd": "book_marg_remaining",
    "average_position": "avg_pos",
    "target_position": "tgt_pos",
    "annual_tracking_error_usd": "track_err_ann",
    "track_err_ann_usd": "track_err_ann",
    "annual_tracking_error_pct_capital": "track_err_ann_pct_cap",
    "risk_budget_usd": "risk_budget",
    "buffer_width_position": "buf_width_pos",
    "initial_capital_usd": "init_cap",
    "init_capital_usd": "init_cap",
    "ib_whatif_initial_margin": "init_marg_native",
    "ib_whatif_initial_margin_usd": "init_marg",
    "ib_whatif_maintenance_margin": "maint_marg_native",
    "ib_whatif_maintenance_margin_usd": "maint_marg",
    "portfolio_cont_margin_at_max_usd": "port_cont_marg_at_max",
    "portfolio_margin_at_max_usd": "port_marg_at_max",
    "max_instr_margin_pct": "max_instr_marg_pct",
    "max_book_margin_pct": "max_book_marg_pct",
    "max_instr_margin_usd": "max_instr_marg",
    "book_margin_at_max_usd": "book_marg_at_max",
    "portfolio_instrument_count": "book_n_instr",
    "portfolio_instruments": "port_instr",
    "portfolio_valid_pnl_instruments": "pnl_valid_n_instr",
    "portfolio_invalid_pnl_instruments": "pnl_inval_n_instr",
    "portfolio_return_valid": "ret_valid",
    "portfolio_cash_gross_pnl_usd": "port_gross",
    "portfolio_cash_trade_cost_usd": "port_trade_cost",
    "portfolio_cash_roll_cost_usd": "port_roll_cost",
    "portfolio_cash_net_pnl_usd": "port_net",
    "portfolio_gross_pct": "port_gross_pct",
    "portfolio_net_pct": "port_net_pct",
    "portfolio_trade_cost_pct": "port_trade_cost_pct",
    "portfolio_roll_cost_pct": "port_roll_cost_pct",
    "portfolio_total_cost_pct": "port_total_cost_pct",
    "cumulative_portfolio_cash_gross_pnl_usd": "cum_port_gross",
    "cumulative_portfolio_cash_net_pnl_usd": "cum_port_net",
    "cumulative_portfolio_gross_pct": "cum_port_gross_pct",
    "cumulative_portfolio_net_pct": "cum_port_net_pct",
    "cumulative_portfolio_trade_cost_pct": "cum_port_trade_cost_pct",
    "cumulative_portfolio_roll_cost_pct": "cum_port_roll_cost_pct",
    "cumulative_portfolio_total_cost_pct": "cum_port_total_cost_pct",
    "historical_return_start": "hist_ret_start",
    "historical_return_end": "hist_ret_end",
    "historical_return_observations": "hist_ret_obs",
    "historical_return_source": "hist_src",
    "historical_instrument_code": "hist_instr_code",
    "historical_years": "hist_years",
    "historical_cumulative_net_percent": "hist_cum_net_pct",
    "historical_annual_mean_percent": "hist_ann_mean_pct",
    "historical_annual_vol_percent": "hist_ann_vol_pct",
    "historical_sharpe": "hist_sharpe",
    "historical_iid_t_stat": "hist_iid_t_stat",
    "portfolio_gross_equity_usd": "port_gross_equity",
    "portfolio_net_equity_usd": "port_net_equity",
}

# Token substitutions used by reports that derive a compact schema from a
# larger source frame. Keep these here so names stay consistent across report
# producers instead of accruing pipeline-local abbreviation tables.
PUBLIC_COLUMN_TOKEN_ABBREVIATIONS = {
    "affordability": "afford",
    "affordable": "afford",
    "average": "avg",
    "availability": "avail",
    "capital": "cap",
    "candidate": "cand",
    "class": "cls",
    "commission": "comm",
    "configured": "cfg",
    "contract": "con",
    "contracts": "cons",
    "currency": "ccy",
    "description": "desc",
    "duplicate": "dup",
    "duration": "dur",
    "economic": "econ",
    "error": "err",
    "eligible": "elig",
    "eligibility": "elig",
    "exclusion": "excl",
    "execution": "exec",
    "exchange": "exch",
    "expiration": "expiry",
    "historical": "hist",
    "history": "hist",
    "hysteresis": "hyst",
    "include": "incl",
    "invalid": "inval",
    "initial": "init",
    "instrument": "instr",
    "instruments": "instrs",
    "maintenance": "maint",
    "family": "fam",
    "fraction": "frac",
    "generated": "gen",
    "group": "grp",
    "liquidity": "liq",
    "market": "mkt",
    "margin": "marg",
    "minimum": "min",
    "limit": "lim",
    "multiplier": "mult",
    "observations": "n",
    "point": "pt",
    "pooling": "pool",
    "points": "pts",
    "position": "pos",
    "preference": "pref",
    "price": "px",
    "quality": "qual",
    "representative": "rep",
    "rebalance": "rebal",
    "restriction": "restrict",
    "profile": "prof",
    "return": "ret",
    "scenario": "scen",
    "selection": "sel",
    "status": "stat",
    "source": "src",
    "strategy": "strat",
    "transactions": "trades",
    "transaction": "trade",
    "timestamp": "ts",
    "turnover": "turn",
    "volatility": "vol",
    "weight": "wt",
    "years": "yrs",
}

# Time-series cash reports put fields used to read the position and its result
# before configuration and lineage. This is applied after public-name
# normalization, so the same compact vocabulary is used by every writer.
CASH_PNL_REPORT_PRIORITY = (
    "symbol",
    "date",
    "sel_active",
    "sel_rebalanced",
    "sel_date",
    "sel_rank",
    "close",
    "con_px",
    "pt_change",
    "pt_vol",
    "comb_fcst",
    "sizing_cap",
    "cap_scalar",
    "init_vol_scalar",
    "vol_scalar",
    "tgt_pos",
    "buf_width_pos",
    "buffer_top_con",
    "buffer_bot_con",
    "held_con",
    "exec_con",
    "trade_con",
    "gross_pnl",
    "gross_pct",
    "net_pnl",
    "net_pct",
    "cum_gross_pnl",
    "cum_net_pnl",
    "cum_gross_pct",
    "cum_net_pct",
    "trade_cost",
    "roll_cost",
    "trade_cost_pct",
    "roll_cost_pct",
    "total_cost_pct",
    "cum_trade_cost_pct",
    "cum_roll_cost_pct",
    "cum_total_cost_pct",
)
CASH_PNL_REPORT_PARAMS = (
    "cap_mode",
    "signal_model",
    "g_mode",
    "fast_window",
    "slow_window",
    "regime_discount",
    "weight",
    "limit_weight",
    "idm",
    "max_weight_idm",
    "buf_size",
    "buf_trade_to_edge",
    "rebalance_on_roll",
    "trade_cost_per_con",
    "roll_cost_per_con",
)
CASH_PNL_REPORT_DIAGNOSTICS = (
    "avg_pos",
    "roll_con",
    "risk_budget",
    "daily_dvol",
    "ann_dvol",
)


PORTFOLIO_CURVE_REPORT_PRIORITY = (
    "date",
    "port_gross",
    "port_net",
    "cum_port_gross",
    "cum_port_net",
    "port_gross_pct",
    "port_net_pct",
    "cum_port_gross_pct",
    "cum_port_net_pct",
    "book_n_instr",
    "port_instr",
    "pnl_valid_n_instr",
    "pnl_inval_n_instr",
    "port_trade_cost",
    "port_roll_cost",
    "port_trade_cost_pct",
    "port_roll_cost_pct",
    "port_total_cost_pct",
    "cum_port_trade_cost_pct",
    "cum_port_roll_cost_pct",
    "cum_port_total_cost_pct",
    "port_gross_equity",
    "port_net_equity",
    "ret_valid",
)
PORTFOLIO_CURVE_REPORT_PARAMS = ("cap_mode", "init_cap")


SELECTION_REVIEW_ALLOCATION_REPORT_PRIORITY = (
    "sel_date",
    "sel_equity",
    "iteration",
    "candidate",
    "accepted",
    "book",
    "book_symbol",
    "asset_cls",
    "econ_subcls",
    "weight",
    "cls_weight",
    "cls_lim_weight",
    "subcls_weight",
    "subcls_lim_weight",
    "group_lim_weight",
    "unalloc_weight",
    "limit_weight",
    "idm",
    "ann_dvol",
    "risk_budget",
)
SELECTION_REVIEW_ALLOCATION_REPORT_PARAMS = (
    "min_instr_per_cls",
    "min_instr_per_subcls",
    "min_book_n",
    "max_instr_per_cls",
    "max_instr_per_subcls",
    "max_cls_weight_pct",
    "max_subcls_weight_pct",
    "low_con_penalty_scale",
    "high_con_penalty_scale",
    "con_band_min",
    "con_band_max",
    "score_tie_tol_pct",
    "tie_ref_book_n",
    "sel_elig_hyst_pct",
    "sel_membership_hyst",
    "sel_rank_buf_pct",
    "sel_rank_n_ewma_hl",
    "sel_book_score_buf_pct",
)


def order_selection_review_allocation_report(
    report: pl.DataFrame,
) -> pl.DataFrame:
    """Lead scheduled allocation reports with review and sizing identity.

    Both the complete hypothetical-trial audit and the compact final-book
    audit require the dated selection capital, per-contract annual dollar
    volatility, and resulting risk budget. Failing when one disappears keeps
    the two public schemas aligned rather than silently producing an
    incomplete report.
    """
    required = {
        "sel_date",
        "sel_equity",
        "min_instr_per_cls",
        "min_instr_per_subcls",
        "min_book_n",
        "max_instr_per_cls",
        "max_instr_per_subcls",
        "max_cls_weight_pct",
        "max_subcls_weight_pct",
        "cls_weight",
        "cls_lim_weight",
        "subcls_weight",
        "subcls_lim_weight",
        "group_lim_weight",
        "unalloc_weight",
        "limit_weight",
        "idm",
        "ann_dvol",
        "risk_budget",
    }
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "selection review allocation report is missing required columns: "
            f"{missing}"
        )
    leading = [
        name
        for name in SELECTION_REVIEW_ALLOCATION_REPORT_PRIORITY
        if name in report.columns
    ]
    leading_set = set(leading)
    params = [
        name
        for name in SELECTION_REVIEW_ALLOCATION_REPORT_PARAMS
        if name in report.columns
    ]
    params_set = set(params)
    diagnostics = [
        name
        for name in report.columns
        if name not in leading_set and name not in params_set
    ]
    return report.select(*leading, *diagnostics, *params)


def order_selection_review_summary_report(report: pl.DataFrame) -> pl.DataFrame:
    """Keep review outcomes first and selection settings at the right edge."""
    priority = (
        "sel_date",
        "sel_equity",
        "sel_cand_n",
        "sel_instr_n",
        "sel_score",
        "sel_idm",
        "sel_symbols",
        "sel_unbuf_symbols",
        "sel_ranked_symbols",
        "sel_rank_buf_symbols",
        "sel_hyst_cand_symbols",
        "sel_added",
        "sel_removed",
    )
    params = (
        "cap_mode",
        "min_instr_per_cls",
        "min_instr_per_subcls",
        "min_book_n",
        "max_instr_per_cls",
        "max_instr_per_subcls",
        "max_cls_weight_pct",
        "max_subcls_weight_pct",
        "sel_elig_hyst_pct",
        "sel_membership_hyst",
        "sel_rank_buf_pct",
        "sel_rank_n_ewma_hl",
        "sel_book_score_buf_pct",
    )
    leading = [name for name in priority if name in report.columns]
    leading_set = set(leading)
    trailing_params = [name for name in params if name in report.columns]
    param_set = set(trailing_params)
    diagnostics = [
        name
        for name in report.columns
        if name not in leading_set and name not in param_set
    ]
    return report.select(*leading, *diagnostics, *trailing_params)


def order_selection_review_instrument_report(
    report: pl.DataFrame,
) -> pl.DataFrame:
    """Order the review instrument report from book identity through inputs.

    Review status and instrument identity lead, allocation weights and their
    limits follow, then score diagnostics. Remaining review diagnostics stay
    ahead of the run-wide parameters, which are kept together at the right edge
    for scanning and comparison across reports.
    """
    required = {"sel_instr_n", "symbol"}
    missing = sorted(required.difference(report.columns))
    if missing:
        raise ValueError(
            "selection review instrument report is missing required columns: "
            f"{missing}"
        )
    identity = [
        name
        for name in (
            "sel_date",
            "sel_year",
            "sel_equity",
            "sel_cand_n",
            "sel_instr_n",
            "sel_active",
            "symbol",
            "asset_cls",
            "econ_subcls",
            "sel_status",
            "rank",
            "pref_rank",
        )
        if name in report.columns
    ]
    allocation = [
        name
        for name in (
            "weight",
            "limit_weight",
            "cls_weight",
            "cls_lim_weight",
            "subcls_weight",
            "subcls_lim_weight",
            "group_lim_weight",
            "unalloc_weight",
        )
        if name in report.columns
    ]
    scores = [
        name
        for name in (
            "sel_score",
            "sel_idm",
            "trade_sr",
            "roll_sr",
            "ann_trade_cost_sr",
            "ann_roll_cost_sr",
            "ann_cost_sr",
        )
        if name in report.columns
    ]
    params = [
        name
        for name in (
            "min_instr_per_cls",
            "min_instr_per_subcls",
            "min_book_n",
            "max_instr_per_cls",
            "max_instr_per_subcls",
            "max_cls_weight_pct",
            "max_subcls_weight_pct",
            "sel_elig_hyst_pct",
            "sel_membership_hyst",
            "sel_rank_buf_pct",
            "sel_rank_n_ewma_hl",
            "sel_book_score_buf_pct",
        )
        if name in report.columns
    ]
    priority = identity + allocation + scores
    priority_set = set(priority)
    params_set = set(params)
    diagnostics = [
        name
        for name in report.columns
        if name not in priority_set and name not in params_set
    ]
    return report.select(*priority, *diagnostics, *params)


def order_cash_pnl_report(report: pl.DataFrame) -> pl.DataFrame:
    """Put key identity, position, P&L, and cost columns first.

    The volatility scalar and buffer boundaries sit beside executed trade
    quantities; other dynamic diagnostics follow costs, then immutable
    configuration inputs. Remaining source, validity, lineage, and extension
    fields trail in their original relative order.
    """
    leading = [name for name in CASH_PNL_REPORT_PRIORITY if name in report.columns]
    leading_set = set(leading)
    diagnostics = [
        name for name in CASH_PNL_REPORT_DIAGNOSTICS if name in report.columns
    ]
    diagnostics_set = set(diagnostics)
    params = [name for name in CASH_PNL_REPORT_PARAMS if name in report.columns]
    params_set = set(params)
    audit = [
        name for name in report.columns
        if name not in leading_set
        and name not in diagnostics_set
        and name not in params_set
    ]
    return report.select(
        *leading,
        *diagnostics,
        *params,
        *audit,
    )


def order_portfolio_curve_report(report: pl.DataFrame) -> pl.DataFrame:
    """Put portfolio P&L, membership, costs, and static settings in order.

    Daily and cumulative P&L lead the curve, followed by book membership and
    cost fields. Static account settings such as initial capital are placed at
    the far right; remaining audit fields retain their relative order.
    """
    leading = [
        name for name in PORTFOLIO_CURVE_REPORT_PRIORITY
        if name in report.columns
    ]
    leading_set = set(leading)
    params = [
        name for name in PORTFOLIO_CURVE_REPORT_PARAMS
        if name in report.columns
    ]
    params_set = set(params)
    audit = [
        name for name in report.columns
        if name not in leading_set and name not in params_set
    ]
    return report.select(*leading, *audit, *params)


# USD amounts generally use cents. The registry includes descriptive internal
# names and compact public-schema equivalents because reports can apply
# formatting immediately before or after their rename boundary.
TWO_DECIMAL_MONEY_COLUMNS = {
    "notional_native_per_contract",
    "notional_per_contract",
    "daily_dollar_vol_per_contract",
    "daily_dvol",
    "annual_dollar_vol_per_contract",
    "annual_tracking_error_usd",
    "track_err_ann_usd",
    "track_err_ann",
    "min_contract_notional",
    "min_contract_annual_dollar_vol",
    "min_capital_full_weight_idm1",
    "equal_weight_dvol_budget",
    "min_capital_equal_weight",
    "commission_per_side",
    "one_way_spread_cash",
    "one_way_total_cost",
    "configured_commission_native",
    "configured_commission",
    "configured_spread_cash_native",
    "configured_one_way_cost_native",
    "configured_one_way_cost",
    "notional",
    "notional_native",
    "daily_dvol",
    "ann_dvol",
    "comm_native",
    "comm_usd",
    "commission_native",
    "commission_usd",
    "comm",
    "init_marg_native",
    "init_marg",
    "maint_marg_native",
    "maint_marg",
    "ib_whatif_commission",
    "ib_whatif_commission_usd",
    "ib_whatif_initial_margin",
    "ib_whatif_initial_margin_usd",
    "ib_whatif_maintenance_margin",
    "ib_whatif_maintenance_margin_usd",
    "cont_marg_at_max",
    "marg_at_max",
    "port_cont_marg_at_max",
    "port_marg_at_max",
    "marg_lim",
    "instr_marg_lim",
    "book_marg_lim",
    "book_marg_remaining",
    "maximum_instrument_margin_usd",
    "max_instr_marg",
    "book_margin_at_maximum_usd",
    "book_marg_at_max",
    "init_cap",
    "spread_cash_native",
    "cost_native",
    "cost_usd",
    "snap_spread_cash_usd",
    "ref_config_commission_usd",
    "snap_ref_config_cost_usd",
    "min_con_notional",
    "min_con_ann_dvol",
    "min_cap_usd_full_wt_idm1",
    "afford_scen_cap",
    "min_cap_usd_equal_wt",
    "init_cap_usd",
    "max_ann_dvol",
    "min_capital_usd_full_weight_idm1",
    "min_capital_usd_equal_weight",
    "initial_capital_usd",
    "init_capital_usd",
    "sizing_capital_usd",
    "sizing_cap",
    "selection_equity_usd",
    "sel_equity",
    "switch_cost_usd",
    "switch_cost",
    "risk_traded_usd_day",
    "risk_traded_day",
    "min_mkt_risk_vol_usd_day",
    "mkt_risk_vol_usd_day",
    "gross_pnl_native",
    "cost_pnl_native",
    "cost_pnl_usd",
    "risk_budget_usd",
    "ref_cap_alloc_usd",
    "port_ann_risk_target_usd",
    "ref_ann_risk_usd",
    "ref_daily_risk_usd",
    "ref_gross_usd",
    "ref_cost_usd",
    "ref_net_usd",
    "cum_ref_gross_usd",
    "cum_ref_net_usd",
    "ref_cap_alloc",
    "port_ann_risk_target",
    "ref_ann_risk",
    "ref_daily_risk",
    "ref_gross",
    "ref_cost",
    "ref_net",
    "cum_ref_gross",
    "cum_ref_net",
    "trade_cost_usd_per_con",
    "roll_cost_usd_per_con",
    "ann_trade_cost_usd",
    "ann_roll_cost_usd",
    "ann_cost_usd",
    "trade_cost_per_con",
    "roll_cost_per_con",
    "ann_trade_cost",
    "ann_roll_cost",
    "ann_cost",
    "cash_trade_cost_usd",
    "cash_roll_cost_usd",
    "trade_cost_usd",
    "roll_cost_usd",
    "trade_cost",
    "roll_cost",
    "portfolio_cash_gross_pnl_usd",
    "portfolio_cash_trade_cost_usd",
    "portfolio_cash_roll_cost_usd",
    "portfolio_cash_net_pnl_usd",
    "cumulative_portfolio_cash_gross_pnl_usd",
    "cumulative_portfolio_cash_net_pnl_usd",
    "trade_cost",
    "roll_cost",
    "portfolio_gross_equity_usd",
    "portfolio_net_equity_usd",
    "gross_equity_usd",
    "net_equity_usd",
    "cash_gross_pnl",
    "cash_trade_cost",
    "cash_roll_cost",
    "cash_net_pnl",
    "cum_cash_gross_pnl",
    "cum_cash_net_pnl",
    "gross_equity",
    "net_equity",
    "port_gross",
    "port_trade_cost",
    "port_roll_cost",
    "port_net",
    "cum_port_gross",
    "cum_port_net",
    "port_gross_equity",
    "port_net_equity",
}

# Daily instrument P&L can be sub-cent when marks or FX are fractional. Keep
# extra precision for these audit series so a small nonzero contribution does
# not display as zero; transaction costs and account-level dollar totals remain
# at cents.
FOUR_DECIMAL_PNL_COLUMNS = {
    "gross_pnl_usd",
    "net_pnl_usd",
    "cum_gross_pnl_usd",
    "cum_net_pnl_usd",
    "cash_gross_pnl_usd",
    "cash_net_pnl_usd",
    "cum_cash_gross_pnl_usd",
    "cum_cash_net_pnl_usd",
    "gross_pnl",
    "net_pnl",
    "cum_gross_pnl",
    "cum_net_pnl",
}


def rename_public_report_columns(report: pl.DataFrame) -> pl.DataFrame:
    """Apply the centralized compact-name map to a public report copy.

    Only columns present in ``report`` are renamed. A report cannot contain
    both an internal source and its compact public target because that would
    make the boundary schema ambiguous.
    """
    renames = {
        source: target
        for source, target in PUBLIC_REPORT_RENAMES.items()
        if source in report.columns
    }
    duplicates = sorted(
        target for source, target in renames.items()
        if target in report.columns and target != source
    )
    if duplicates:
        raise ValueError(
            "public report already contains compact columns: "
            + ", ".join(duplicates)
        )
    return report.rename(renames)


# Contract prices, FX, return-volatility rates, and percentage-point returns
# need extra precision for reproducible sizing and audit work.
SIX_DECIMAL_COLUMNS = {
    "fx_to_usd",
    "con_px",
    "point_change",
    "pt_change",
    "daily_return_vol",
    "annual_return_vol",
    "cur_fx_to_usd",
    "ref_fx_to_usd",
    "ann_return_vol",
    "daily_ret_vol",
    "ann_ret_vol",
    "ann_pct_vol",
    "g_fast_rms_vol",
    "g_slow_rms_vol",
    "g_fast_sharpe",
    "g_slow_sharpe",
    "capital_scalar",
    "cap_scalar",
    "yr_frac",
    "period_year_fraction",
    "annual_tracking_error_pct_capital",
    "track_err_ann_pct_cap",
    "low_contract_penalty_scale",
    "high_contract_penalty_scale",
    "low_con_penalty_scale",
    "high_con_penalty_scale",
    "ref_gross_pct",
    "ref_ann_cost_pct",
    "ref_cost_pct",
    "ref_net_pct",
    "cum_ref_gross_pct",
    "cum_ref_net_pct",
    "portfolio_gross_pct",
    "portfolio_net_pct",
    "portfolio_trade_cost_pct",
    "portfolio_roll_cost_pct",
    "portfolio_total_cost_pct",
    "cumulative_portfolio_gross_pct",
    "cumulative_portfolio_net_pct",
    "cumulative_portfolio_trade_cost_pct",
    "cumulative_portfolio_roll_cost_pct",
    "cumulative_portfolio_total_cost_pct",
    "port_gross_pct",
    "port_net_pct",
    "port_trade_cost_pct",
    "port_roll_cost_pct",
    "port_total_cost_pct",
    "cum_port_gross_pct",
    "cum_port_net_pct",
    "cum_port_trade_cost_pct",
    "cum_port_roll_cost_pct",
    "cum_port_total_cost_pct",
    "gross_pct",
    "net_pct",
    "trade_cost_pct",
    "roll_cost_pct",
    "total_cost_pct",
    "cum_gross_pct",
    "cum_net_pct",
    "cum_trade_cost_pct",
    "cum_roll_cost_pct",
    "cum_total_cost_pct",
    "historical_cumulative_net_percent",
    "historical_annual_mean_percent",
    "historical_annual_vol_percent",
    "historical_sharpe",
    "historical_iid_t_stat",
    "hist_cum_net_pct",
    "hist_ann_mean_pct",
    "hist_ann_vol_pct",
    "hist_sharpe",
    "hist_iid_t_stat",
    "initial_margin_to_annual_dollar_vol",
    "initial_margin_to_ann_dvol",
    "init_marg_ann_dvol_ratio",
    "trade_sr",
    "roll_sr",
    "ref_trade_sr",
    "ref_roll_sr",
    "prior_book_score",
    "unbuffered_book_score",
    "unbuf_book_score",
    "rank_buffered_book_score",
    "rank_buf_book_score",
    "hysteresis_candidate_book_score",
    "hyst_cand_book_score",
    "gross_score_gain",
    "switch_cost_sr",
    "score_buffer_sr",
    "score_buf_sr",
    "net_score_gain",
}

SIX_DECIMAL_SUFFIXES = (
    "_ann_cost_sr",
    "_pre_cost_sr",
)


def round_public_report(report: pl.DataFrame) -> pl.DataFrame:
    """Return a presentation-rounded copy of a public report dataframe.

    Monetary values use two decimals except per-instrument gross/net P&L, which
    uses four to retain sub-cent audit values. Point changes, FX,
    return-volatility rates, and percentage-point returns use six; other
    floating-point diagnostics use four. Integer, boolean, temporal, and
    identifier columns are unchanged.
    The input frame is not mutated and should remain the calculation source.

    Parameters
    ----------
    report
        Polars dataframe ready to cross a public report boundary.

    Returns
    -------
    polars.DataFrame
        Same schema and column order, with floating-point values rounded by
        the centralized semantic column registry.
    """
    expressions: list[pl.Expr] = []
    for name, dtype in report.schema.items():
        if dtype not in (pl.Float32, pl.Float64):
            continue
        if name in TWO_DECIMAL_MONEY_COLUMNS:
            decimals = 2
        elif name in FOUR_DECIMAL_PNL_COLUMNS:
            decimals = 4
        elif name in SIX_DECIMAL_COLUMNS or name.endswith(
            SIX_DECIMAL_SUFFIXES
        ):
            decimals = 6
        else:
            decimals = 4
        expressions.append(pl.col(name).round(decimals))
    return report.with_columns(expressions)

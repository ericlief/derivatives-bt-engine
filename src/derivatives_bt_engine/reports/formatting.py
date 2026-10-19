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
    "target_volatility": "target_vol",
    "maximum_instrument_margin_percent": "max_instr_margin_pct",
    "maximum_book_margin_percent": "max_book_margin_pct",
    "maximum_weight_idm": "max_weight_idm",
    "maximum_instruments_per_cluster": "max_instr_per_cluster",
    "include_roll_cost_in_score": "roll_cost_in_score",
    "elapsed_seconds": "elapsed_s",
    "number_instruments": "n_instr",
    "number_asset_classes": "n_asset_cls",
    "number_economic_subclasses": "n_econ_subcls",
    "selected_symbols": "symbols",
    "maximum_instrument_margin_usd": "max_instr_margin_usd",
    "book_margin_at_maximum_usd": "book_margin_at_max_usd",
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
    "score_tie_tolerance_percent": "score_tie_tolerance_pct",
    "tie_ref_book_count": "tie_ref_book_n",
    "low_contract_weight": "low_con_weight",
    "band_contract_weight": "band_con_weight",
    "high_contract_weight": "high_con_weight",
    "ref_weight": "ref_weight",
    "ref_book_count": "ref_book_n",
    "ref_idm": "ref_idm",
    "ref_weight_idm": "ref_weight_idm",
    "ref_annual_risk_budget_usd": "ref_ann_risk_usd",
    "ref_daily_risk_budget_usd": "ref_daily_risk_usd",
    "ref_return_capital_usd": "ref_ret_cap_usd",
    "ref_average_position": "ref_avg_pos",
    "ref_target_position": "ref_targ_pos",
    "held_ref_position": "held_ref_pos",
    "ref_gross_return": "ref_gross_ret",
    "ref_annual_cost_return": "ref_ann_cost_ret",
    "ref_cost_return": "ref_cost_ret",
    "ref_net_return": "ref_net_ret",
    "ref_return_valid": "ref_ret_valid",
    "ref_return_valid_obs": "ref_ret_valid_obs",
    "ref_return_post_start_invalid_obs": (
        "ref_ret_post_start_invalid_obs"
    ),
    "ref_return_start": "ref_ret_start",
    "ref_return_end": "ref_ret_end",
    "ref_max_position": "ref_max_position",
    "ref_contract_band_distance": "ref_con_band_distance",
    "candidate_ref_max_position": "candidate_ref_max_position",
    "candidate_ref_contract_band_distance": (
        "candidate_ref_con_band_distance"
    ),
    "low_contract_penalty_sr": "low_con_penalty_sr",
    "high_contract_penalty_sr": "high_con_penalty_sr",
    "contract_band_penalty_sr": "con_band_penalty_sr",
    "annual_contract_load": "ann_con_load",
    "average_position": "avg_pos",
    "target_position": "targ_pos",
    "buffer_width_position": "buf_width_pos",
    "initial_capital_usd": "init_cap_usd",
    "portfolio_instrument_count": "book_n_instr",
    "portfolio_valid_pnl_instruments": "pnl_valid_n_instr",
    "portfolio_return_valid": "ret_valid",
    "portfolio_cash_gross_pnl_usd": "cash_gross_pnl_usd",
    "portfolio_cash_trade_cost_usd": "cash_trade_cost_usd",
    "portfolio_cash_realized_roll_cost_usd": (
        "cash_realized_roll_cost_usd"
    ),
    "portfolio_cash_expected_roll_cost_usd": (
        "cash_expected_roll_cost_usd"
    ),
    "portfolio_cash_realized_net_pnl_usd": (
        "cash_realized_net_pnl_usd"
    ),
    "portfolio_cash_expected_net_pnl_usd": (
        "cash_expected_net_pnl_usd"
    ),
    "portfolio_gross_return": "gross_ret",
    "portfolio_realized_net_return": "realized_net_ret",
    "portfolio_expected_net_return": "expected_net_ret",
    "cumulative_portfolio_cash_gross_pnl_usd": "cum_cash_gross_pnl_usd",
    "cumulative_portfolio_cash_realized_net_pnl_usd": (
        "cum_cash_realized_net_pnl_usd"
    ),
    "cumulative_portfolio_cash_expected_net_pnl_usd": (
        "cum_cash_expected_net_pnl_usd"
    ),
    "cumulative_portfolio_gross_return": "cum_gross_ret",
    "cumulative_portfolio_realized_net_return": "cum_realized_net_ret",
    "cumulative_portfolio_expected_net_return": "cum_expected_net_ret",
    "portfolio_gross_equity_usd": "gross_equity_usd",
    "portfolio_realized_equity_usd": "realized_equity_usd",
    "portfolio_expected_equity_usd": "expected_equity_usd",
    "cash_gross_return_contribution": "cash_gross_ret_contrib",
    "cash_realized_net_return_contribution": (
        "cash_realized_net_ret_contrib"
    ),
    "cash_expected_net_return_contribution": (
        "cash_expected_net_ret_contrib"
    ),
    "cumulative_cash_gross_return_contribution": (
        "cum_cash_gross_ret_contrib"
    ),
    "cumulative_cash_realized_net_return_contribution": (
        "cum_cash_realized_net_ret_contrib"
    ),
    "cumulative_cash_expected_net_return_contribution": (
        "cum_cash_expected_net_ret_contrib"
    ),
}


# USD amounts are reported to cents. The registry includes descriptive
# internal names and their compact public-schema equivalents because reports
# can apply formatting immediately before or after their rename boundary.
TWO_DECIMAL_MONEY_COLUMNS = {
    "notional_native_per_contract",
    "notional_per_contract",
    "daily_dollar_vol_per_contract",
    "annual_dollar_vol_per_contract",
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
    "init_margin",
    "init_margin_usd",
    "maint_margin",
    "maint_margin_usd",
    "ib_whatif_commission",
    "ib_whatif_commission_usd",
    "ib_whatif_initial_margin",
    "ib_whatif_initial_margin_usd",
    "ib_whatif_maintenance_margin",
    "ib_whatif_maintenance_margin_usd",
    "cont_margin_at_max_usd",
    "margin_at_max_usd",
    "portfolio_cont_margin_at_max_usd",
    "portfolio_margin_at_max_usd",
    "margin_limit_usd",
    "instr_margin_limit_usd",
    "book_margin_limit_usd",
    "book_margin_remaining_usd",
    "maximum_instrument_margin_usd",
    "max_instr_margin_usd",
    "book_margin_at_maximum_usd",
    "book_margin_at_max_usd",
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
    "risk_traded_usd_day",
    "risk_traded_day",
    "min_mkt_risk_vol_usd_day",
    "mkt_risk_vol_usd_day",
    "gross_pnl_native",
    "gross_pnl_usd",
    "cost_pnl_native",
    "cost_pnl_usd",
    "net_pnl_usd",
    "risk_budget_usd",
    "ref_annual_risk_budget_usd",
    "ref_daily_risk_budget_usd",
    "ref_return_capital_usd",
    "ref_ann_risk_usd",
    "ref_daily_risk_usd",
    "ref_ret_cap_usd",
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
    "cash_gross_pnl_usd",
    "cash_trade_cost_usd",
    "cash_realized_roll_cost_usd",
    "cash_expected_roll_cost_usd",
    "cash_realized_net_pnl_usd",
    "cash_expected_net_pnl_usd",
    "portfolio_cash_gross_pnl_usd",
    "portfolio_cash_trade_cost_usd",
    "portfolio_cash_realized_roll_cost_usd",
    "portfolio_cash_expected_roll_cost_usd",
    "portfolio_cash_realized_net_pnl_usd",
    "portfolio_cash_expected_net_pnl_usd",
    "cumulative_portfolio_cash_gross_pnl_usd",
    "cumulative_portfolio_cash_realized_net_pnl_usd",
    "cumulative_portfolio_cash_expected_net_pnl_usd",
    "cum_cash_gross_pnl_usd",
    "cum_cash_realized_net_pnl_usd",
    "cum_cash_expected_net_pnl_usd",
    "portfolio_gross_equity_usd",
    "portfolio_realized_equity_usd",
    "portfolio_expected_equity_usd",
    "gross_equity_usd",
    "realized_equity_usd",
    "expected_equity_usd",
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


# FX and return-volatility rates need more precision than general diagnostics
# so rounded inputs still reproduce reported USD notionals and dollar vol.
SIX_DECIMAL_RATE_COLUMNS = {
    "fx_to_usd",
    "daily_return_vol",
    "annual_return_vol",
    "cur_fx_to_usd",
    "ref_fx_to_usd",
    "ann_return_vol",
    "daily_ret_vol",
    "ann_ret_vol",
    "low_contract_penalty_scale",
    "high_contract_penalty_scale",
    "low_con_penalty_scale",
    "high_con_penalty_scale",
    "ref_gross_return",
    "ref_annual_cost_return",
    "ref_cost_return",
    "ref_net_return",
    "ref_gross_ret",
    "ref_ann_cost_ret",
    "ref_cost_ret",
    "ref_net_ret",
    "portfolio_gross_return",
    "portfolio_realized_net_return",
    "portfolio_expected_net_return",
    "cumulative_portfolio_gross_return",
    "cumulative_portfolio_realized_net_return",
    "cumulative_portfolio_expected_net_return",
    "gross_ret",
    "realized_net_ret",
    "expected_net_ret",
    "cum_gross_ret",
    "cum_realized_net_ret",
    "cum_expected_net_ret",
    "cash_gross_return_contribution",
    "cash_realized_net_return_contribution",
    "cash_expected_net_return_contribution",
    "cumulative_cash_gross_return_contribution",
    "cumulative_cash_realized_net_return_contribution",
    "cumulative_cash_expected_net_return_contribution",
    "cash_gross_ret_contrib",
    "cash_realized_net_ret_contrib",
    "cash_expected_net_ret_contrib",
    "cum_cash_gross_ret_contrib",
    "cum_cash_realized_net_ret_contrib",
    "cum_cash_expected_net_ret_contrib",
}


def round_public_report(report: pl.DataFrame) -> pl.DataFrame:
    """Return a presentation-rounded copy of a public report dataframe.

    Monetary values use two decimals, reproduction-sensitive FX and
    return-volatility rates use six, and all other floating-point diagnostics
    use four. Integer, boolean, temporal, and identifier columns are unchanged.
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
        elif name in SIX_DECIMAL_RATE_COLUMNS:
            decimals = 6
        else:
            decimals = 4
        expressions.append(pl.col(name).round(decimals))
    return report.with_columns(expressions)

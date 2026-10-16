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

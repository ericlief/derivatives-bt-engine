"""Stable, spreadsheet-oriented TSMOM reporting rows shared by live and backtest.

The raw target/event dictionaries remain deliberately rich internal audits.
This module exposes the smaller cross-mode reporting contract: one signal row
per symbol/rebalance and one portfolio row per rebalance. Static configuration
belongs in a separate run manifest, never duplicated on every signal row.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Mapping, Optional


SIGNAL_COLUMNS = (
    'run_id', 'as_of', 'symbol', 'cluster', 'close', 'mult',
    'g_regime', 'g_fast', 'g_slow', 'a_co', 'a_re', 'g_blend',
    'g_mode', 'g_raw', 'g_scale', 'g_sig',
    'ts_fast', 'ts_slow', 'ts', 'contin_sig', 'ts_reg',
    'day_std', 'hv', 'dd_pct', 'risk_sc', 'reg_disc', 'vx_sc', 'comb_sc',
    'cur_con', 'frac_con', 'tgt_con', 'max_con', 'rnd_gap', 'zero_why',
    'clust_rank', 'clust_score', 'clust_excl',
    'alloc_mode', 'not_weighting', 'pre_sc_not_bud', 'not_alloc_w',
    'one_con_not', 'frac_tgt_not', 'frac_tgt_dvol', 'pos_dvol', 'port_risk_con',
)

PORTFOLIO_COLUMNS = (
    'run_id', 'as_of', 'equity', 'n_act_symb', 'n_act_clus',
    'gross_pos_dvol', 'port_risk_tgt', 'idm_risk_tgt',
    'real_port_risk', 'idm_mult',
)


def _value(row: Mapping, *keys: str):
    for key in keys:
        if key in row:
            return row[key]
    return None


def _number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _as_of(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _dollars(value) -> Optional[float]:
    """Two-decimal USD/reporting amount; prices retain their quote precision."""
    value = _number(value)
    return round(value, 2) if value is not None else None


def _report_float(value, ndigits: int = 4) -> Optional[float]:
    """Bound non-dollar spreadsheet values to a stable, useful precision."""
    value = _number(value)
    return round(value, ndigits) if value is not None else None


def clean_signal_rows(rows: Iterable[Mapping], run_id: str, *, as_of=None) -> list[dict]:
    """Project raw live targets or backtest events into the common signal sheet."""
    output = []
    for source in rows:
        close = _number(_value(source, 'close'))
        mult = _number(_value(source, 'mult'))
        hv = _number(_value(source, 'hv'))
        fractional_contracts = _number(_value(source, 'fractional_target_contracts', 'frac_con'))
        target_contracts = _number(_value(source, 'final_target_contracts', 'target_contracts', 'tgt_con'))
        fractional_notional = _number(_value(source, 'fractional_target_notional'))
        if fractional_notional is None and fractional_contracts is not None and close is not None and mult is not None:
            fractional_notional = fractional_contracts * close * mult
        one_contract_notional = _number(_value(source, 'one_contract_notional', 'one_con_not'))
        if one_contract_notional is None and close is not None and mult is not None:
            one_contract_notional = abs(close * mult)
        fractional_dvol = _number(_value(source, 'fractional_target_dollar_vol'))
        if fractional_dvol is None and fractional_notional is not None and hv is not None:
            fractional_dvol = abs(fractional_notional) * hv
        position_dvol = _number(_value(source, 'standalone_position_dollar_vol'))
        if position_dvol is None and target_contracts is not None and close is not None and mult is not None and hv is not None:
            position_dvol = abs(target_contracts * close * mult * hv)
        rounding_gap = _number(_value(source, 'rounding_gap'))
        if rounding_gap is None and fractional_contracts is not None and target_contracts is not None:
            rounding_gap = abs(round(fractional_contracts - target_contracts, 2))

        row = {
            'run_id': run_id,
            'as_of': _as_of(_value(source, 'date') if 'date' in source else as_of),
            'symbol': _value(source, 'symbol'),
            'cluster': _value(source, 'cluster'),
            'close': _report_float(close),
            'mult': _report_float(mult),
            'g_regime': _value(source, 'g_regime'),
            'g_fast': _report_float(_value(source, 'g_fast')),
            'g_slow': _report_float(_value(source, 'g_slow')),
            'a_co': _report_float(_value(source, 'a_co')),
            'a_re': _report_float(_value(source, 'a_re')),
            'g_blend': _report_float(_value(source, 'g_blend')),
            'g_mode': _value(source, 'g_signal_mode', 'g_mode'),
            'g_raw': _report_float(_value(source, 'g_raw_forecast', 'g_raw')),
            'g_scale': _report_float(_value(source, 'g_forecast_scalar', 'g_scale')),
            'g_sig': _report_float(_value(source, 'signal', 'g_sig')),
            'ts_fast': _report_float(_value(source, 'ts_fast')),
            'ts_slow': _report_float(_value(source, 'ts_slow')),
            'ts': _report_float(_value(source, 'ts')),
            'contin_sig': _report_float(_value(source, 'contin_signal', 'contin_sig')),
            'ts_reg': _value(source, 'ts_regime', 'ts_reg'),
            'day_std': _report_float(_value(source, 'daily_std', 'day_std')),
            'hv': _report_float(hv),
            'dd_pct': _report_float(_value(source, 'dd_pct')),
            'risk_sc': _report_float(_value(source, 'risk_scalar', 'risk_sc')),
            'reg_disc': _report_float(_value(source, 'regime_discount', 'reg_discount', 'reg_disc')),
            'vx_sc': _report_float(_value(source, 'vix_scalar', 'vx_sc')),
            'comb_sc': _report_float(_value(source, 'combined_scalar', 'comb_sc', 'scalar')),
            'cur_con': _report_float(_value(source, 'current_contracts', 'prior_contracts', 'cur_con')),
            'frac_con': _report_float(fractional_contracts),
            'tgt_con': _report_float(target_contracts),
            'max_con': _report_float(_value(source, 'max_contracts', 'max_con')),
            'rnd_gap': _report_float(rounding_gap),
            'zero_why': _value(source, 'zero_reason', 'integer_zero_reason', 'gate_reason'),
            'clust_rank': _report_float(_value(source, 'cluster_universe_rank', 'clust_rank')),
            'clust_score': _report_float(_value(source, 'cluster_universe_score', 'clust_score')),
            'clust_excl': bool(_value(source, 'cluster_universe_excluded', 'clust_excl') or False),
            'alloc_mode': _value(source, 'allocation_mode', 'alloc_mode'),
            'not_weighting': _value(source, 'notional_weighting', 'not_weighting'),
            'pre_sc_not_bud': _dollars(_value(source, 'pre_scalar_notional_budget')),
            'not_alloc_w': _report_float(_value(source, 'notional_allocation_weight', 'not_alloc_w')),
            'one_con_not': _dollars(one_contract_notional),
            'frac_tgt_not': _dollars(fractional_notional),
            'frac_tgt_dvol': _dollars(fractional_dvol),
            'pos_dvol': _dollars(position_dvol),
            'port_risk_con': _dollars(_value(source, 'portfolio_risk_contribution')),
        }
        output.append({key: row[key] for key in SIGNAL_COLUMNS})
    return output


def portfolio_rows_from_signals(signal_rows: Iterable[Mapping], run_id: str, *, equity_by_as_of: Optional[Mapping] = None,
                                portfolio_fields_by_as_of: Optional[Mapping] = None) -> list[dict]:
    """Aggregate clean signal rows into one explicit portfolio-risk snapshot per date."""
    by_as_of: dict[str, list[Mapping]] = defaultdict(list)
    for row in signal_rows:
        by_as_of[row['as_of']].append(row)
    result = []
    for as_of, members in sorted(by_as_of.items(), key=lambda item: item[0] or ''):
        active = [row for row in members if _number(row.get('tgt_con')) not in (None, 0.0)]
        extra = (portfolio_fields_by_as_of or {}).get(as_of, {})
        result.append({
            'run_id': run_id,
            'as_of': as_of,
            'equity': _dollars((equity_by_as_of or {}).get(as_of)),
            'n_act_symb': len(active),
            'n_act_clus': len({row['cluster'] for row in active if row.get('cluster') is not None}),
            'gross_pos_dvol': _dollars(sum(_number(row.get('pos_dvol')) or 0.0 for row in active)),
            'port_risk_tgt': _dollars(extra.get('portfolio_risk_target')),
            'idm_risk_tgt': _dollars(extra.get('idm_risk_target')),
            'real_port_risk': _dollars(extra.get('realized_portfolio_risk')),
            'idm_mult': _number(extra.get('idm_multiplier')),
        })
    return result

use pyo3::prelude::*;
use pyo3_polars::PyLazyFrame;
use polars::prelude::*;
use crate::domain::enums::*;
use crate::domain::configs::*;
use crate::domain::schemas::*;

#[pyfunction]
pub fn generate_single_leg_signals_rs(
    option_chain: PyLazyFrame,
    config: &StrategyConfig,
    leg: &OptionLegConfig,
) -> PyResult<PyLazyFrame> {
    let mut lf = option_chain.0;

    let is_put = leg.option_type == OptionType::Put;
    let prefix = if is_put { "p_" } else { "c_" };
    let bid_col = format!("{}bid", prefix);
    let ask_col = format!("{}ask", prefix);
    let delta_col = format!("{}delta", prefix);

    // 1. Basic filtering: positive bids/asks and spread percent
    lf = lf
        .filter(
            col(&bid_col).gt(lit(0.0))
            .and(col(&ask_col).gt(lit(0.0)))
        )
        .with_column(
            ((col(&ask_col) - col(&bid_col)) / col(&bid_col) * lit(100.0)).alias("spread_percent")
        )
        .filter(col("spread_percent").lt_eq(lit(50.0)))
        .with_column(
            ((col(&bid_col) + col(&ask_col)) / lit(2.0)).alias("midpoint_price")
        );

    // 2. DTE filtering
    if let Some(range) = leg.dte_range {
        lf = lf.filter(col(COL_DTE).gt_eq(lit(range.0)).and(col(COL_DTE).lt_eq(lit(range.1))));
    } else if let Some(target) = leg.dte_target {
        let diff = col(COL_DTE).cast(DataType::Int32) - lit(target);
        lf = lf.filter(
            when(diff.clone().gt_eq(lit(0)))
            .then(diff.clone())
            .otherwise(diff * lit(-1))
            .lt_eq(lit(2))
        );
    } else {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>("Need either dte_target or dte_range"));
    }

    // 3. Delta filtering
    if let Some(range) = leg.delta_range { 
        let (min_delta, max_delta) = if is_put {
            (-range.1.abs(), -range.0.abs())
        } else {
            (range.0.abs(), range.1.abs())
        };
        lf = lf.filter(col(&delta_col).gt_eq(lit(min_delta)).and(col(&delta_col).lt_eq(lit(max_delta))));
    } else if let Some(target) = leg.delta_target {
        let target_val = if is_put { -target.abs() } else { target.abs() };
        let diff = col(&delta_col) - lit(target_val);
        let delta_diff_expr = when(diff.clone().gt_eq(lit(0.0)))
            .then(diff.clone())
            .otherwise(diff * lit(-1.0));
            
        lf = lf.with_column(delta_diff_expr.alias("delta_diff"));
        
        let max_delta_diff = target_val.abs() * 0.05;
        lf = lf.filter(col("delta_diff").lt_eq(lit(max_delta_diff)));
    } else {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>("Need either delta_target or delta_range"));
    }

    // 4. Sorting based on trade_selection_method
    let is_long = leg.position_side == PositionSide::Long;
    let midpoint_descending = !is_long;

    match config.trade_selection_method {
        TradeSelectionMethod::DeltaFirst => {
            if let Some(_) = leg.delta_target {
                lf = lf.sort(["index", "delta_diff", "midpoint_price"], SortMultipleOptions {
                    descending: vec![false, false, midpoint_descending],
                    nulls_last: vec![false, false, false],
                    multithreaded: true,
                    maintain_order: false,
                    limit: None,
                });
            } else {
                lf = lf.sort(["index", &delta_col, "midpoint_price"], SortMultipleOptions {
                    descending: vec![false, false, midpoint_descending],
                    nulls_last: vec![false, false, false],
                    multithreaded: true,
                    maintain_order: false,
                    limit: None,
                });
            }
        }
        TradeSelectionMethod::PremiumFirst => {
            if let Some(_) = leg.delta_target {
                lf = lf.sort(["index", "midpoint_price", "delta_diff"], SortMultipleOptions {
                    descending: vec![false, midpoint_descending, false],
                    nulls_last: vec![false, false, false],
                    multithreaded: true,
                    maintain_order: false,
                    limit: None,
                });
            } else {
                lf = lf.sort(["index", "midpoint_price", &delta_col], SortMultipleOptions {
                    descending: vec![false, midpoint_descending, false],
                    nulls_last: vec![false, false, false],
                    multithreaded: true,
                    maintain_order: false,
                    limit: None,
                });
            }
        }
        TradeSelectionMethod::Weighted => {}
    }

    Ok(PyLazyFrame(lf))
}

#[pyfunction]
pub fn generate_multi_leg_signals_rs(
    option_chain: PyLazyFrame,
    config: &StrategyConfig,
    spread_type: OptionSpreadType,
    legs: Vec<OptionLegConfig>,
) -> PyResult<PyLazyFrame> {
    if legs.len() < 2 {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>("Multi-leg strategy requires at least 2 legs"));
    }

    let mut leg_signals = Vec::with_capacity(legs.len());
    for (i, leg_config) in legs.iter().enumerate() {
        let py_lf = generate_single_leg_signals_rs(option_chain.clone(), config, leg_config)?;
        let mut lf = py_lf.0;
        
        // Add leg prefix to columns
        let schema = lf.collect_schema().map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))?;
        let cols: Vec<String> = schema.iter_names().map(|n| n.to_string()).collect();
        let prefix = format!("leg{}_", i + 1);
        for col_name in cols {
            if col_name != "index" && col_name != COL_EXPIRE_DATE {
                lf = lf.rename([col_name.clone()], [format!("{}{}", prefix, col_name)], false);
            }
        }
        
        leg_signals.push(lf);
    }

    // Join legs
    let mut combined = leg_signals[0].clone();
    for i in 1..leg_signals.len() {
        let join_cols = match spread_type {
            OptionSpreadType::Vertical => vec!["index", COL_EXPIRE_DATE],
            OptionSpreadType::Calendar => vec!["index"], 
            _ => vec!["index"],
        };
        
        combined = combined.join(
            leg_signals[i].clone(),
            join_cols.iter().map(|s| col(*s)).collect::<Vec<_>>(),
            join_cols.iter().map(|s| col(*s)).collect::<Vec<_>>(),
            JoinType::Inner.into(),
        );
    }

    // Post-join filtering and metrics
    match spread_type {
        OptionSpreadType::Vertical => {
            combined = combined.filter(col("leg1_strike").neq(col("leg2_strike")));
        }
        _ => {}
    }

    Ok(PyLazyFrame(combined))
}

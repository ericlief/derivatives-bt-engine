use pyo3::prelude::*;
use crate::domain::enums::*;

#[pyclass(get_all, set_all)]
#[derive(Debug, Clone)]
pub struct OptionLegConfig {
    pub option_type: OptionType,
    pub position_side: PositionSide,
    pub delta_target: Option<f64>,
    pub delta_range: Option<(f64, f64)>,
    pub dte_target: Option<i32>,
    pub dte_range: Option<(i32, i32)>,
    pub early_close_after_dit: Option<i32>,
    pub early_close_on_dte: Option<i32>,
}

#[pymethods]
impl OptionLegConfig {
    #[new]
    #[pyo3(signature = (option_type, position_side, delta_target=None, delta_range=None, dte_target=None, dte_range=None, early_close_after_dit=None, early_close_on_dte=None))]
    pub fn new(
        option_type: OptionType,
        position_side: PositionSide,
        delta_target: Option<f64>,
        delta_range: Option<(f64, f64)>,
        dte_target: Option<i32>,
        dte_range: Option<(i32, i32)>,
        early_close_after_dit: Option<i32>,
        early_close_on_dte: Option<i32>,
    ) -> Self {
        
        Self {
            option_type,
            position_side,
            delta_target,
            delta_range,
            dte_target,
            dte_range,
            early_close_after_dit,
            early_close_on_dte,
        }
    }
}

#[pyclass(get_all, set_all)]
#[derive(Debug, Clone)]
pub struct StrategyConfig {
    pub quantity: i32,
    pub max_positions: i32,
    pub initial_capital: f64,
    pub leverage: f64,
    pub max_margin_utilization: f64,
    pub option_strategy: OptionStrategy,
    pub trade_selection_method: TradeSelectionMethod,
    pub premium_weight: f64,
    pub delta_weight: f64,
    pub multiplier: f64,
}

#[pymethods]
impl StrategyConfig {
    #[new]
    #[pyo3(signature = (option_strategy, quantity=1, max_positions=1, initial_capital=100000.0, leverage=1.0, max_margin_utilization=0.8, trade_selection_method=TradeSelectionMethod::DeltaFirst, premium_weight=0.5, delta_weight=0.5, multiplier=100.0))]
    pub fn new(
        option_strategy: OptionStrategy,
        quantity: i32,
        max_positions: i32,
        initial_capital: f64,
        leverage: f64,
        max_margin_utilization: f64,
        trade_selection_method: TradeSelectionMethod,
        premium_weight: f64,
        delta_weight: f64,
        multiplier: f64,
    ) -> Self {
        Self {
            option_strategy,
            quantity,
            max_positions,
            initial_capital,
            leverage,
            max_margin_utilization,
            trade_selection_method,
            premium_weight,
            delta_weight,
            multiplier,
        }
    }
}

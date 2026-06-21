use pyo3::prelude::*;

pub mod domain;
pub mod signal_generator;

pub mod curl;


pub use domain::*;
pub use signal_generator::*;


#[pymodule]
fn options_engine(module: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register domain enums and classes
    module.add_class::<OptionType>()?;
    module.add_class::<PositionSide>()?;
    module.add_class::<OptionSpreadType>()?;
    module.add_class::<OptionStrategy>()?;
    module.add_class::<FuturesType>()?;
    module.add_class::<FuturesStrategy>()?;
    module.add_class::<TradeSelectionMethod>()?;
    
    module.add_class::<OptionLegConfig>()?;
    module.add_class::<StrategyConfig>()?;

    // Register signal generator functions
    module.add_function(wrap_pyfunction!(generate_single_leg_signals_rs, module)?)?;
    module.add_function(wrap_pyfunction!(generate_multi_leg_signals_rs, module)?)?;

    Ok(())
}

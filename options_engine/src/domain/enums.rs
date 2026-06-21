use pyo3::prelude::*;
use std::fmt::Display;

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OptionType {
    Call,
    Put,
}


impl Display for OptionType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OptionType::Call => write!(f, "call"),
            OptionType::Put => write!(f, "put"),
        }
    }
}


// impl OptionType {
    // pub fn as_str(&self) -> &'static str {
    //     match self {
    //         OptionType::Call => "call",
    //         OptionType::Put => "put",
    //     }
    // }
//     pub fn 
// }

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PositionSide {
    Long,
    Short,
}

impl Display for PositionSide {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PositionSide::Long => write!(f, "Long"),
            PositionSide::Short => write!(f, "Short"),
        }
    }
}

// impl PositionSide {
//     pub fn as_str(&self) -> &'static str {
//         match self {
//             PositionSide::Long => "long",
//             PositionSide::Short => "short",
//         }
//     }
// }

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OptionSpreadType {
    None,
    Vertical,
    Calendar,
    Diagonal,
    IronCondor,
    Butterfly,
}

impl OptionSpreadType {
    pub fn as_str(&self) -> &'static str {
        match self {
            OptionSpreadType::None => "none",
            OptionSpreadType::Vertical => "vertical",
            OptionSpreadType::Calendar => "calendar",
            OptionSpreadType::Diagonal => "diagonal",
            OptionSpreadType::IronCondor => "iron_condor",
            OptionSpreadType::Butterfly => "butterfly",
        }
    }
}

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OptionStrategy {
    ShortPut,
    LongPut,
    ShortCall,
    LongCall,
    BullPutCreditSpread,
    BearPutDebitSpread,
    BullCallDebitSpread,
    BearCallCreditSpread,
    CustomStrategy,
    IronCondor,
    Butterfly,
    Straddle,
    Strangle,
}

#[pyclass]
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FuturesType {
    MES, // Micro E-mini S&P 500
}

impl FuturesType {
    pub fn multiplier(&self) -> f64 {
        match self {
            FuturesType::MES => 5.0,
        }
    }

    pub fn initial_margin(&self) -> f64 {
        match self {
            FuturesType::MES => 2302.72,
        }
    }

    pub fn commission(&self) -> f64 {
        match self {
            FuturesType::MES => 1.42,
        }
    }
}

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FuturesStrategy {
    LongFutures,
    ShortFutures,
}

#[pyclass(eq, eq_int)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TradeSelectionMethod {
    PremiumFirst,
    DeltaFirst,
    Weighted,
}

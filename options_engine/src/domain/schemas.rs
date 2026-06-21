// Core DataFrame Columns

// Common Columns
pub const COL_DATE: &str = "date";
pub const COL_EXPIRE_DATE: &str = "expire_date";
pub const COL_STRIKE: &str = "strike";
pub const COL_UNDERLYING_LAST: &str = "underlying_last";
pub const COL_DTE: &str = "dte";
pub const COL_MARGIN_REQUIRED: &str = "margin_required";
pub const COL_POSITION_SIDE: &str = "position_side";
pub const COL_OPTION_TYPE: &str = "option_type";
pub const COL_BID: &str = "bid";
pub const COL_ASK: &str = "ask";
pub const COL_DELTA: &str = "delta";
pub const COL_QUANTITY: &str = "quantity";

// Options Chain DataFrame Columns
pub const COL_P_BID: &str = "p_bid";
pub const COL_P_ASK: &str = "p_ask";
pub const COL_C_BID: &str = "c_bid";
pub const COL_C_ASK: &str = "c_ask";
pub const COL_P_DELTA: &str = "p_delta";
pub const COL_C_DELTA: &str = "c_delta";
pub const COL_P_IV: &str = "p_iv";
pub const COL_C_IV: &str = "c_iv";
pub const COL_P_SIZE: &str = "p_size";
pub const COL_C_SIZE: &str = "c_size";

// Trade Signals DataFrame Columns (Extends common)
pub const COL_SPREAD_TYPE: &str = "spread_type";
pub const COL_SPREAD_ID: &str = "spread_id";
pub const COL_LEG_NUMBER: &str = "leg_number";
pub const COL_LEG_RATIO: &str = "leg_ratio";
pub const COL_SPREAD_PRICE: &str = "spread_price";

// Position DataFrame Columns (Extends common)
pub const COL_TRADE_ID: &str = "trade_id";
pub const COL_ENTRY_DATE: &str = "entry_date";
pub const COL_UNDERLYING_ENTRY: &str = "underlying_entry";
pub const COL_UNDERLYING_EXIT: &str = "underlying_exit";
pub const COL_ENTRY_PRICE: &str = "entry_price";
pub const COL_EXIT_PRICE: &str = "exit_price";
pub const COL_ENTRY_DELTA: &str = "entry_delta";
pub const COL_ENTRY_DTE: &str = "entry_dte";
pub const COL_CLOSE_DATE: &str = "close_date";

// Trade Results DataFrame Columns (Extends common)
pub const COL_EXIT_DATE: &str = "exit_date";
pub const COL_EXIT_DELTA: &str = "exit_delta";
pub const COL_DAYS_HELD: &str = "days_held";
pub const COL_CAPITAL_USED: &str = "capital_used";
pub const COL_OPTION_BP: &str = "option_bp";
pub const COL_RETURN_ON_MARGIN: &str = "return_on_margin";
pub const COL_CLOSE_REASON: &str = "close_reason";
pub const COL_PNL: &str = "pnl";

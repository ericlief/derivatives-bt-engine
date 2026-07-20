import json
import os
from pathlib import Path
from typing import Union
import gspread
from google.oauth2.service_account import Credentials
from datetime import date, datetime
import polars as pl
import numpy as np

from derivatives_bt_engine.utils.logger import setup_logger
from derivatives_bt_engine.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Create logger instance
logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_OPTIONS_SPREADSHEET = 'spx_options_bt_results'
DEFAULT_FUTURES_SPREADSHEET = 'futures_bt'

def google_auth():
    """Authenticate with Google Sheets API."""
    try:
        # Get the key file path from environment variable
        key_file_path = os.getenv('GSPREAD_KEY')
        if not key_file_path:
            raise ValueError("GSPREAD_KEY environment variable not set")
        key_file_path = Path(key_file_path).expanduser()
        # Define the scope
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        # Create credentials
        credentials = Credentials.from_service_account_file(str(key_file_path), scopes=scope)
        
        # Authorize and return the client
        gc = gspread.authorize(credentials)
        return gc
        
    except FileNotFoundError:
        logger.error(f"Service account key file not found at: {key_file_path}")
        raise
    except Exception as e:
        logger.error(f"Error authenticating with Google Sheets: {e}")
        raise


# Helper function to convert numpy types to Python types
def convert_numpy_types(obj):
    """Convert numpy types to Python types for JSON serialization."""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_numpy_types(item) for item in obj)
    else:
        return obj

def flatten_for_sheet(value):
    """Convert lists, tuples, numpy types into safe Google Sheets values."""
    value = convert_numpy_types(value)
    if isinstance(value, (list, tuple)):
        # return ", ".join(map(str, value)) if value else "N/A"
        return json.dumps(value)
    return value

def _json_or_blank(value):
        v = convert_numpy_types(value)

        def norm(x):
            x = convert_numpy_types(x)
            # Handle infinity and NaN values
            if isinstance(x, float) and (not np.isfinite(x)):
                return ''
            return None if x in (None, 'N/A') else x

        # If list/tuple container
        if isinstance(v, (list, tuple)):
            vals = []
            for x in v:
                x = norm(x)
                if isinstance(x, tuple):
                    x = list(x)
                vals.append(x)
            return '' if all(x is None for x in vals) else json.dumps(vals)

        # Scalar
        v = norm(v)
        return '' if v is None else v

def _get_leg_field_json(config, field_name):
    if hasattr(config, 'legs'):
        vals = [getattr(leg, field_name, None) for leg in config.legs]
        return _json_or_blank(vals)
    leg = getattr(config, 'leg', None)
    return _json_or_blank(getattr(leg, field_name, None)) if leg else ''

def log_to_google_sheets(results: dict,
                        config: Union['SingleLegOptionStrategyConfig', 'MultiLegOptionStrategyConfig', 'FuturesStrategyConfig'],
                        param_str: str,
                        spreadsheet_name=DEFAULT_OPTIONS_SPREADSHEET):
    """
    Log backtest results to Google Sheets as a single row.
    """
    logger.info(f"Starting Google Sheets logging for: {param_str}")
    
    results_df = results['trade_results']
    stats = results.get('stats')
    drawdown_analysis = results.get('drawdown_analysis', {})
    
    try:
        logger.info("Authenticating with Google Sheets...")
        gc = google_auth()
        logger.info("Authentication successful")
        
        logger.info(f"Opening spreadsheet {spreadsheet_name}...")
        spreadsheet = _get_or_create_spreadsheet(gc, spreadsheet_name=spreadsheet_name)
        logger.info("Spreadsheet opened successfully")
        
        # Try to get existing worksheet, create if doesn't exist
        try:
            strat_name = '_'.join(config.option_strategy.value.upper().split())
            # logger.info(f"{strat} worksheet found")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # strat_name = '_'.join(strategy_name.upper().split())
            worksheet_name = f'{strat_name}_{timestamp}'
            worksheet = spreadsheet.worksheet(worksheet_name)
            logger.info(f"Appended data to existing worksheet {worksheet_name}...")

        except Exception as e:
            logger.info(f"Could not find existing worksheet, creating new one: {e}")
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=40)
            logger.info(f"New worksheet {worksheet_name} created successfully!")
            # Add headers if new worksheet
            # headers = [a
            #     'Timestamp', 'Strategy', 'Start', 'End', 'Period', 'Quantity', 'DTE_Target', 'DTE_Range', 'Delta_Target', 'Delta_Range',
            #     'Total_PnL', 'Initial_Capital', 'Final_Capital', 'Return_Pct', 'Avg_Days_Held',
            #     'Avg_ROI', 'Max_Profit', 'Max_Loss', 'Win_Rate', 'Winning_Trades', 'Total_Trades', 
            #     'Max_Drawdown_USD', 'Max_Drawdown_Pct', 'Peak_Capital', 'Trough_Capital', 
            #     'Drawdown_Duration', 'Execution_Time', 'Max_Positions', 'Early_Close', 
            #     'Leverage', 'Max_Margin', 'Max_Spread_Width', 'Max_Trade_Loss', 
            #     'Param_String', 'Use_VIX', 'Use_IV', 'SL', 'TP', 'Average_Premium', "Trade_Selection"
            # ]
            headers = [
                        "timestamp", "strategy", "start", "end", "period", "quantity",
                        "dte_target", "dte_range", "delta_target", "delta_range",
                        "total_pnl", "initial_capital", "final_capital", "ret_yr", "roi", "avg_win", "max_win", "avg_loss", "max_loss", "win_rate", "winning_trades", "total_trades", "avg_days_held",
                        "max_dd_usd", "max_dd_pct", "peak_capital", "trough_capital",
                        "dd_duration", "execution_time", "max_positions", "early_close_after_dit", "early_close_on_dte",
                        "leverage", "max_margin", "max_spread_width", "max_trade_loss",
                        "param_string", "vix_range", "vix_max", "use_iv", "sl", "tp", "avg_premium", "trade_selection", "premium_ratio",
                        "short_delta_target", "long_delta_target"
            ]
            logger.info("Adding headers...")
            header_response = worksheet.append_row(headers)
            logger.info(f"Headers added, response: {header_response}")
        
        # Calculate drawdown stats
        # OLD (for negative drawdown): Use .min() instead of .max()
        max_dd_amount = "N/A"
        max_dd_pct = "N/A"
        if stats is not None and stats.height > 0:
            max_dd_amount = f"{stats['Drawdown ($)'].max():.2f}"  # Now using max since drawdown is positive
            max_dd_pct = f"{stats['Drawdown (%)'].max():.2f}"  # Now using max since drawdown is positive

        # Prepare data row
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        window_years = (date.fromisoformat(config.end_date) - date.fromisoformat(config.start_date)).days / 365.0
        gs_avg_margin = float(results_df['capital_used'].mean()) if results_df.height > 0 else float(config.initial_capital)
        gs_total_pnl = round(results_df['cumulative_pnl'][-1], 2)

        row_data = [
            timestamp,
            config.option_strategy.value,
            config.start_date,
            config.end_date,
            round(window_years, 2),
            config.quantity,
            _get_leg_field_json(config, 'dte_target'),
            _get_leg_field_json(config, 'dte_range'),
            _get_leg_field_json(config, 'delta_target'),
            _get_leg_field_json(config, 'delta_range'),
            gs_total_pnl,
            config.initial_capital,
            round(results_df['capital'][-1], 2),
            round(gs_total_pnl / gs_avg_margin / window_years * 100, 2) if window_years and gs_avg_margin else 0.0,
            round(results_df['roi'].mean(), 2) if results_df.height > 0 else 0.0,
            round(results_df.filter(pl.col('pnl') > 0)['pnl'].mean() or 0.0, 2),   # avg_profit
            round(results_df['pnl'].max(), 2),
            round(results_df.filter(pl.col('pnl') <= 0)['pnl'].mean() or 0.0, 2),   # avg_loss
            round(results_df['pnl'].min(), 2),
            round(((results_df['pnl'] > 0).sum() / results_df.height) * 100, 2),  # win_rate
            (results_df['pnl'] > 0).sum(),
            results_df.height,
            round(results_df['days_held'].mean(), 1),
            max_dd_amount,
            max_dd_pct,
            round(drawdown_analysis.get('peak_capital', 0), 2),
            round(drawdown_analysis.get('trough_capital', 0), 2),
            convert_numpy_types(drawdown_analysis.get('drawdown_duration', 0)),
            results.get('total_execution_time', ''),  # Execution_Time
            getattr(config, 'max_positions', ''),
            getattr(config, 'early_close_after_dit', ''),
            getattr(config, 'early_close_on_dte', ''),
            getattr(config, 'leverage', ''),
            getattr(config, 'max_margin_utilization', ''),
            getattr(config, 'max_spread_width', ''),
            getattr(config, 'max_trade_loss', ''),
            param_str,
            getattr(config, 'vix_range', ''),
            getattr(config, 'vix_max', ''),
            getattr(config, 'use_iv', ''),
            '',  # SL
            '',  # TP
            round(results_df['premium'].mean(), 2),
            config.trade_selection_method.value,
            getattr(config, 'premium_ratio', ''),
            _get_leg_field_json(config, 'short_delta_target'),
            _get_leg_field_json(config, 'long_delta_target'),
        ]
        
        row_data = [flatten_for_sheet(convert_numpy_types(obj)) for obj in row_data]
        logger.info(f"Prepared row data with {len(row_data)} columns")
        logger.info(f"Row data: {row_data}")
        
        # Sanity check: headers vs row length
        try:
            expected_cols = len(headers)  # if we just created the sheet in this run
        except NameError:
            # existing sheet: read header row to get the true column count
            expected_cols = len(worksheet.row_values(1))

        if len(row_data) != expected_cols:
            raise ValueError(f"Header/row length mismatch: expected {expected_cols}, got {len(row_data)}")

        # Append the row
        logger.info("Appending row to worksheet...")
        response = worksheet.append_row(row_data)
        logger.info(f"Row appended successfully!")
        logger.info(f"Response type: {type(response)}")
        logger.info(f"Response: {response}")
        logger.info(f"Response status code: {getattr(response, 'status_code', 'No status_code attribute')}")
        logger.info(f"Results logged to Google Sheets: {param_str}")
        
    except Exception as e:
        logger.error(f"An unexpected error occurred during Google Sheets upload: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

def _get_or_create_spreadsheet(gc, spreadsheet_name: str):
    try:
        spreadsheet = gc.open(spreadsheet_name)
        logger.info(f"Spreadsheet '{spreadsheet_name}' opened successfully.")
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error(f"Spreadsheet '{spreadsheet_name}' not found and cannot be created due to permissions. Please create it manually.")
        raise
    return spreadsheet

def _format_single_backtest_result_row(results: dict, 
                                      config: Union['SingleLegOptionStrategyConfig', 'MultiLegOptionStrategyConfig','FuturesStrategyConfig'],
                                      param_str: str,
                                      period: int) -> dict:
    """
    Formats a single backtest result into a dictionary suitable for a Google Sheet row.
    """
    tr = results['trade_results']
    stats = results.get('stats')
    drawdown_analysis = results.get('drawdown_analysis', {})

    # Calculate drawdown stats
    # OLD (for negative drawdown): Use .min() instead of .max()
    max_dd_amount = "N/A"
    max_dd_pct = "N/A"
    if stats is not None and stats.height > 0:
        max_dd_amount = f"{stats['Drawdown ($)'].max():.2f}"  # Now using max since drawdown is positive
        max_dd_pct = f"{stats['Drawdown (%)'].max():.2f}"  # Now using max since drawdown is positive

    # Prepare data row
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    has_trades = tr.height > 0
    avg_margin = float(tr['capital_used'].mean()) if has_trades else float(config.initial_capital)
    total_pnl_val = round(tr['cumulative_pnl'][-1], 2) if has_trades else 0.0

    row_data = {
        "timestamp": timestamp,
        "strategy": config.option_strategy.value,
        "start": config.start_date,
        "end": config.end_date,
        "period": period,
        "quantity": config.quantity,
        "dte_target": _get_leg_field_json(config, 'dte_target'),
        "dte_range": _get_leg_field_json(config, 'dte_range'),
        "delta_target": _get_leg_field_json(config, 'delta_target'),
        "delta_range": _get_leg_field_json(config, 'delta_range'),
        "total_pnl": total_pnl_val,
        "initial_capital": config.initial_capital,
        "final_capital": round(tr['capital'][-1], 2) if has_trades else config.initial_capital,
        "ret_yr": round(total_pnl_val / avg_margin / period * 100, 2) if (has_trades and period and avg_margin) else 0.0,
        "roi": round(tr['roi'].mean(), 2) if has_trades else 0.0,
        "avg_win": round(tr.filter(pl.col('pnl') > 0)['pnl'].mean() or 0.0, 2) if has_trades else 0.0,
        "max_win": round(tr['pnl'].max(), 2) if has_trades else 0.0,
        "avg_loss": round(tr.filter(pl.col('pnl') <= 0)['pnl'].mean() or 0.0, 2) if has_trades else 0.0,
        "max_loss": round(tr['pnl'].min(), 2) if has_trades else 0.0,
        "win_rate": round((tr['pnl'] > 0).sum() / tr.height, 2) if has_trades else 0.0,
        "winning_trades": (tr['pnl'] > 0).sum() if has_trades else 0,
        "total_trades": tr.height if has_trades else 0,
        "avg_days_held": round(tr['days_held'].mean(), 1) if has_trades else 0.0,
        "max_dd_usd": max_dd_amount,
        "max_dd_pct": max_dd_pct,
        "peak_capital": round(drawdown_analysis.get('peak_capital', 0), 2),
        "trough_capital": round(drawdown_analysis.get('trough_capital', 0), 2),
        "dd_duration": convert_numpy_types(drawdown_analysis.get('drawdown_duration', 0)),
        "execution_time": results.get('total_execution_time', ''),
        "max_positions": getattr(config, 'max_positions', ''),
        "early_close_after_dit": getattr(config, 'early_close_after_dit', ''),
        "early_close_on_dte": getattr(config, 'early_close_on_dte', ''),
        "leverage": getattr(config, 'leverage', ''),
        "max_margin": getattr(config, 'max_margin_utilization', ''),
        "max_spread_width": getattr(config, 'max_spread_width', ''),
        "max_trade_loss": getattr(config, 'max_trade_loss', ''),
        "param_string": param_str,
        "vix_range": getattr(config, 'vix_range', ''),
        "vix_max": getattr(config, 'vix_max', ''),
        "use_iv": getattr(config, 'use_iv', ''),
        "sl": '',
        "tp": '',
        "avg_premium": round(tr['premium'].mean(), 2) if has_trades else 0.0,
        "trade_selection": config.trade_selection_method.value,
        "premium_ratio": getattr(config, 'premium_ratio', ''),
        "short_delta_target": _get_leg_field_json(config, 'short_delta_target'),
        "long_delta_target": _get_leg_field_json(config, 'long_delta_target'),
    }

    # Apply flatten_for_sheet to all values
    for key, value in row_data.items():
        row_data[key] = flatten_for_sheet(value)
    
    return row_data

def _format_futures_backtest_result_row(results: dict,
                                        config: 'FuturesStrategyConfig',
                                        param_str: str,
                                        period: float) -> dict:
    """Futures analog of _format_single_backtest_result_row above, which
    assumes an options config (option_strategy, legs, dte/delta targets)
    and an options trade_results schema (a 'premium' column) -- both
    absent from FuturesStrategyConfig/the naked futures trade_results
    table, so that formatter raises AttributeError/ColumnNotFoundError the
    moment it's used on a futures backtest. This one reads only fields
    that actually exist on FuturesStrategyConfig and naked_futures.py's
    trade_results columns (symbol, quantity, opened/closed, entry_price/
    exit_price, days_held, futures_strategy, close_reason, capital,
    capital_used, margin_utilization, pnl, cumulative_pnl, fees, roi), and
    explicitly includes the signal entry/exit gate params
    (ts_exit_threshold/ts_entry_threshold/exit_on_ts_crossover) alongside
    quantity/leverage/fill_price -- the "gating params and other naked
    params" a futures sheet row needs that an options row has no concept
    of.

    Shared by naked_futures.py (single/multi-symbol runs, via Backtester.
    run()'s own log_to_sheets dispatch -- see FuturesStrategyConfig branch
    there), window_scheme_naked_futures.py (one row per window, batch-
    uploaded via upload_df_to_google_sheets), and any future naked-futures
    grid search -- same formatter/uploader split options already uses
    between log_to_google_sheets (one row per run) and
    upload_df_to_google_sheets (many rows in one shot)."""
    tr = results['trade_results']
    daily_mtm = results.get('daily_mtm')
    drawdown_analysis = results.get('drawdown_analysis', {})

    max_dd_usd = drawdown_analysis.get('max_drawdown')
    # dd_pct is negative (drawdown) in daily_mtm, worst = .min() -- same
    # convention as calculate_futures_mtm_drawdown/_save_results' own
    # "Maximum drawdown" log line in backtester.py.
    max_dd_pct = float(daily_mtm['dd_pct'].min()) if daily_mtm is not None and daily_mtm.height > 0 else None

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    has_trades = tr.height > 0
    avg_margin = float(tr['capital_used'].mean()) if has_trades else float(config.initial_capital)
    total_pnl_val = round(tr['cumulative_pnl'][-1], 2) if has_trades else 0.0

    row_data = {
        "timestamp": timestamp,
        "symbol": config.futures_type,
        "futures_strategy": config.futures_strategy.value,
        "start": config.start_date,
        "end": config.end_date,
        "period": period,
        "quantity": config.quantity,
        "fill_price": config.fill_price,
        "initial_capital": config.initial_capital,
        "final_capital": round(tr['capital'][-1], 2) if has_trades else config.initial_capital,
        "total_pnl": total_pnl_val,
        "ret_yr": round(total_pnl_val / avg_margin / period * 100, 2) if (has_trades and period and avg_margin) else 0.0,
        "sharpe": round(results['mtm_sharpe'], 2) if results.get('mtm_sharpe') is not None else 'N/A',
        "roi": round(tr['roi'].mean(), 2) if has_trades else 0.0,
        "avg_win": round(tr.filter(pl.col('pnl') > 0)['pnl'].mean() or 0.0, 2) if has_trades else 0.0,
        "max_win": round(tr['pnl'].max(), 2) if has_trades else 0.0,
        "avg_loss": round(tr.filter(pl.col('pnl') <= 0)['pnl'].mean() or 0.0, 2) if has_trades else 0.0,
        "max_loss": round(tr['pnl'].min(), 2) if has_trades else 0.0,
        "win_rate": round((tr['pnl'] > 0).sum() / tr.height, 2) if has_trades else 0.0,
        "winning_trades": int((tr['pnl'] > 0).sum()) if has_trades else 0,
        "total_trades": tr.height if has_trades else 0,
        "avg_days_held": round(tr['days_held'].mean(), 1) if has_trades else 0.0,
        "max_dd_usd": round(max_dd_usd, 2) if max_dd_usd is not None else 'N/A',
        "max_dd_pct": round(max_dd_pct, 2) if max_dd_pct is not None else 'N/A',
        "peak_capital": round(drawdown_analysis.get('peak_capital', 0) or 0, 2),
        "trough_capital": round(drawdown_analysis.get('trough_capital', 0) or 0, 2),
        "dd_duration": convert_numpy_types(drawdown_analysis.get('drawdown_duration', 0)),
        "execution_time": results.get('total_execution_time', ''),
        "leverage": getattr(config, 'leverage', ''),
        "avg_margin_utilization": round(tr['margin_utilization'].mean(), 4) if has_trades else 0.0,
        "total_fees": round(tr['fees'].sum(), 2) if has_trades else 0.0,
        "ts_exit_threshold": getattr(config, 'ts_exit_threshold', None),
        "ts_entry_threshold": getattr(config, 'ts_entry_threshold', None),
        "exit_on_ts_crossover": getattr(config, 'exit_on_ts_crossover', False),
        "param_string": param_str,
    }

    for key, value in row_data.items():
        row_data[key] = flatten_for_sheet(value)

    return row_data


def log_futures_to_google_sheets(results: dict,
                                 config: 'FuturesStrategyConfig',
                                 param_str: str,
                                 spreadsheet_name: str = DEFAULT_FUTURES_SPREADSHEET):
    """Futures analog of log_to_google_sheets above -- appends ONE row (this
    single backtest run) to a worksheet in `spreadsheet_name` (default:
    'futures_bt', a spreadsheet already created for this purpose --
    _get_or_create_spreadsheet can only open an existing spreadsheet with
    these credentials, not create a brand new one, same limitation the
    options path already has).

    Worksheet is named f'{symbol}_{futures_strategy}' (e.g.
    'MES_LONG_FUTURES') -- one persistent tab PER STRATEGY CONFIG, not per
    run: every run of the same symbol+direction appends another row to the
    same tab, so a tab's rows are directly comparable across runs (deliberately
    NOT log_to_google_sheets' options convention above, which timestamps a
    brand new tab every single call)."""
    logger.info(f"Starting Google Sheets logging for: {param_str}")

    try:
        logger.info("Authenticating with Google Sheets...")
        gc = google_auth()
        logger.info("Authentication successful")

        logger.info(f"Opening spreadsheet {spreadsheet_name}...")
        spreadsheet = _get_or_create_spreadsheet(gc, spreadsheet_name=spreadsheet_name)
        logger.info("Spreadsheet opened successfully")

        period = (date.fromisoformat(config.end_date) - date.fromisoformat(config.start_date)).days / 365.0
        row = _format_futures_backtest_result_row(results, config, param_str, period)

        worksheet_name = f"{config.futures_type}_{config.futures_strategy.value}".upper()

        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            logger.info(f"Appending data to existing worksheet {worksheet_name}...")

            # This tab is persistent across runs (see docstring above), so its
            # header row may predate a field that was later added to
            # _format_futures_backtest_result_row (e.g. "sharpe") -- appending
            # by position would silently shift every later column. Align by
            # header name instead, extending the header row in place for any
            # new keys so old rows keep their original columns (blank for the
            # new field) and new rows land under the right header.
            headers = worksheet.row_values(1)
            new_keys = [k for k in row.keys() if k not in headers]
            if new_keys:
                headers = headers + new_keys
                worksheet.update('A1', [headers])
                logger.info(f"Extended {worksheet_name} header row with new column(s): {new_keys}")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(row))
            headers = list(row.keys())
            worksheet.append_row(headers)
            logger.info(f"New worksheet {worksheet_name} created successfully!")

        worksheet.append_row([row.get(h, '') for h in headers])
        logger.info(f"Results logged to Google Sheets: {param_str}")

    except Exception as e:
        logger.error(f"An unexpected error occurred during Google Sheets upload: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")


def upload_df_to_google_sheets(df: pl.DataFrame, strategy_name: str, spreadsheet_name: str = DEFAULT_OPTIONS_SPREADSHEET):
    """
    Uploads a polars DataFrame to a specified Google Sheet worksheet.
    Creates the worksheet and adds headers if it doesn't exist.
    """
    logger.info(f"Starting Google Sheets upload for strategy: {strategy_name}")

    try:
        logger.info("Authenticating with Google Sheets...")
        gc = google_auth()
        logger.info("Authentication successful")

        spreadsheet = _get_or_create_spreadsheet(gc, spreadsheet_name)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        strat_name = '_'.join(strategy_name.upper().split())
        worksheet_name = f'{strat_name}_{timestamp}'
        try:
            logger.info(f"Getting worksheet: {worksheet_name}...")
            worksheet = spreadsheet.worksheet(worksheet_name)
            logger.info(f"{worksheet_name} worksheet found")
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"{worksheet_name} worksheet not found, creating new one.")
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=df.height + 1, cols=df.width)
            logger.info("New worksheet created")
            # Add headers
            headers = df.columns
            worksheet.append_row(headers)
            logger.info("Headers added to new worksheet.")

        # Prepare data for upload -- _json_or_blank already handles None/NaN
        # (via convert_numpy_types + its own non-finite-float check), so no
        # separate null-replacement pass is needed before it.
        data_to_upload = [[_json_or_blank(x) for x in row] for row in df.rows()]

        logger.info(f"Uploading {len(data_to_upload)} rows to worksheet...")
        worksheet.append_rows(data_to_upload)
        logger.info("Data uploaded successfully to Google Sheets.")

    except Exception as e: # Catch any other exceptions during the process
        logger.error(f"An unexpected error occurred during Google Sheets upload: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
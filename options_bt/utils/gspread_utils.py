import json
import os
from typing import Union
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pandas as pd
import numpy as np
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig, SingleLegOptionStrategyConfig
from options_bt.utils.logger import setup_logger

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Create logger instance
logger = setup_logger()
 
def google_auth():
    """Authenticate with Google Sheets API."""
    try:
        # Get the key file path from environment variable
        key_file_path = os.getenv('GSPREAD_KEY')
        if not key_file_path:
            raise ValueError("GSPREAD_KEY environment variable not set")
        
        # Define the scope
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        # Create credentials
        credentials = Credentials.from_service_account_file(key_file_path, scopes=scope)
        
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

def log_to_google_sheets(results: dict, 
                        config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig],
                        param_str: str):
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
        
        logger.info("Opening spreadsheet...")
        spreadsheet = gc.open('options_bt_results')
        logger.info("Spreadsheet opened successfully")
        
        # Try to get existing worksheet, create if doesn't exist
        try:
            logger.info("Getting SPX worksheet...")
            worksheet = spreadsheet.worksheet('SPX')
            logger.info("SPX worksheet found")
        except Exception as e:
            logger.info(f"SPX worksheet not found, creating new one: {e}")
            worksheet = spreadsheet.add_worksheet(title='SPX', rows=1000, cols=20)
            logger.info("New worksheet created")
            # Add headers if new worksheet
            # headers = [
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
                        "total_pnl", "initial_capital", "final_capital", "return_pct", "avg_days_held",
                        "avg_roi", "max_profit", "max_loss", "win_rate", "winning_trades", "total_trades",
                        "max_drawdown_usd", "max_drawdown_pct", "peak_capital", "trough_capital",
                        "drawdown_duration", "execution_time", "max_positions", "early_close",
                        "leverage", "max_margin", "max_spread_width", "max_trade_loss",
                        "param_string", "use_vix", "use_iv", "sl", "tp", "average_premium", "trade_selection"
            ]
            logger.info("Adding headers...")
            header_response = worksheet.append_row(headers)
            logger.info(f"Headers added, response: {header_response}")
        
        # Calculate drawdown stats
        max_dd_amount = "N/A"
        max_dd_pct = "N/A"
        if stats is not None and not stats.empty:
            max_dd_amount = f"{stats['Drawdown ($)'].min():.2f}"
            max_dd_pct = f"{stats['Drawdown (%)'].min():.2f}"
        
        # Prepare data row
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        def _json_or_blank(value):
            v = convert_numpy_types(value)

            def norm(x):
                x = convert_numpy_types(x)
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

        row_data = [
            timestamp,
            config.option_strategy.value,
            config.start_date,
            config.end_date,
            round((pd.to_datetime(config.end_date) - pd.to_datetime(config.start_date)).days, 2),
            config.quantity,
            _get_leg_field_json(config, 'dte_target'),
            _get_leg_field_json(config, 'dte_range'),
            _get_leg_field_json(config, 'delta_target'),
            _get_leg_field_json(config, 'delta_range'),
            round(results_df['cumulative_pnl'].iloc[-1], 2),
            config.initial_capital,
            round(results_df['capital'].iloc[-1], 2),
            round((results_df['capital'].iloc[-1] / config.initial_capital - 1) * 100, 2),  # Convert % to number
            round(results_df['days_held'].mean(), 1),
            round(results_df['roi'].mean(), 2),
            round(results_df['pnl'].max(), 2),
            round(results_df['pnl'].min(), 2),
            round(((results_df['pnl'] > 0).sum() / len(results_df)) * 100, 2),  # Convert % to number
            convert_numpy_types((results_df['pnl'] > 0).sum()),
            convert_numpy_types(len(results_df)),
            max_dd_amount,
            max_dd_pct,
            round(drawdown_analysis.get('peak_capital', 0), 2),
            round(drawdown_analysis.get('trough_capital', 0), 2),
            convert_numpy_types(drawdown_analysis.get('drawdown_duration', 0)),
            results.get('total_execution_time', ''),  # Execution_Time
            getattr(config, 'max_positions', ''),
            getattr(config, 'early_close_days', ''),
            getattr(config, 'leverage', ''),
            getattr(config, 'max_margin_utilization', ''),
            getattr(config, 'max_spread_width', ''),
            getattr(config, 'max_trade_loss', ''),
            param_str,
            getattr(config, 'vix_range', ''),
            getattr(config, 'use_iv', ''),
            '',  # SL
            '',  # TP
            round(results_df['premium'].mean(), 2),
            config.trade_selection_method.value
        ]
        
        row_data = [flatten_for_sheet(convert_numpy_types(obj)) for obj in row_data]
        logger.info(f"Prepared row data with {len(row_data)} columns")
        logger.info(f"Row data: {row_data}")
        
        # Append the row
        logger.info("Appending row to worksheet...")
        response = worksheet.append_row(row_data)
        logger.info(f"Row appended successfully!")
        logger.info(f"Response type: {type(response)}")
        logger.info(f"Response: {response}")
        logger.info(f"Response status code: {getattr(response, 'status_code', 'No status_code attribute')}")
        logger.info(f"Results logged to Google Sheets: {param_str}")
        
    except Exception as e:
        logger.error(f"Failed to log to Google Sheets: {e}")
        logger.error(f"Exception type: {type(e)}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
    

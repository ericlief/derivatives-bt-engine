import os
from typing import Union
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
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
        return ", ".join(map(str, value)) if value else "N/A"
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
            headers = [
                'Timestamp', 'Strategy', 'Start', 'End', 'Period', 'Quantity', 'DTE_Target', 'DTE_Range', 'Delta_Target', 'Delta_Range',
                'Total_PnL', 'Initial_Capital', 'Final_Capital', 'Return_Pct', 'Avg_Days_Held',
                'Avg_ROI', 'Max_Profit', 'Max_Loss', 'Win_Rate', 'Winning_Trades', 'Total_Trades', 
                'Max_Drawdown_$', 'Max_Drawdown_%', 'Peak_Capital', 'Trough_Capital', 
                'Drawdown_Duration', 'Execution_Time', 'Max_Positions', 'Early_Close', 
                'Leverage', 'Max_Margin', 'Max_Spread_Width', 'Max_Trade_Loss', 
                'Param_String', 'Use_VIX', 'Use_IV', 'SL', 'TP', 'Average_Premium', "Trade_Selection"
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
        
        row_data = [
            timestamp,
            config.option_strategy.value,
            config.start_date,
            config.end_date,
            round(np.datetime(config.end_date) - np.datetime(config.start_date), 2),
            config.quantity,
            [leg.dte_target if leg.dte_target is not None else 'N/A' for leg in config.legs] if hasattr(config, 'legs') else getattr(config.leg, 'dte_target', 'N/A'),
            [leg.dte_range if leg.dte_range is not None else 'N/A' for leg in config.legs] if hasattr(config, 'legs') else getattr(config.leg, 'dte_range', 'N/A'),
            [leg.delta_target if leg.delta_target is not None else 'N/A' for leg in config.legs] if hasattr(config, 'legs') else getattr(config.leg, 'delta_target', 'N/A'),
            [leg.delta_range if leg.delta_range is not None else 'N/A' for leg in config.legs] if hasattr(config, 'legs') else getattr(config.leg, 'delta_range', 'N/A'),
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
            "N/A",  # Execution_Time
            getattr(config, 'max_positions', 'N/A'),
            getattr(config, 'early_close_days', 'N/A'),
            getattr(config, 'leverage', 'N/A'),
            getattr(config, 'max_margin_utilization', 'N/A'),
            getattr(config, 'max_spread_width', 'N/A'),
            getattr(config, 'max_trade_loss', 'N/A'),
            param_str,
            getattr(config, 'use_vix', 'N/A'),
            getattr(config, 'use_iv', 'N/A'),
            'N/A',  # SL
            'N/A',  # TP
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
    

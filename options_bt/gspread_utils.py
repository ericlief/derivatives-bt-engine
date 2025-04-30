import json
import os
import gspread
from gspread import ServiceAccountCredentials
from options_bt.bt import run_multiple_backtests, OptionType, PositionSide, setup_logger
import datetime
import pandas as pd

# Create logger instance
logger = setup_logger()
 
def google_auth():

    # gc = gspread.service_account(key)

    try:
        # Set up credentials
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive']
        
        # Load credentials from environment variable or file
        creds_json = os.getenv('GOOGLE_CREDS_JSON')
        if creds_json:
            creds_dict = json.loads(creds_json)
        else:
            creds_path = os.path.join("/Users/liefe/.local/etc/share", creds_json)
            with open(creds_path, 'r') as f:
                creds_dict = json.load(f)
        
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(credentials)

        return gc
    
    except Exception as e:
        logger.error(f"Error logging to Google Sheets: {str(e)}")
        raise   

def log_to_google_sheets(strat: str,
                         results_df: pd.DataFrame, 
                         param_str: str, 
                         daily_df: pd.DataFrame = None
                         ):
    """
    Log backtest results to Google Sheets.
    
    Args:
        results_df: DataFrame containing trade results
        param_str: String describing the backtest parameters
        daily_df: Optional DataFrame containing daily MTM data
    """
    
        
    # Open the spreadsheet
    gc = google_auth()
    spreadsheet = gc.open('Options Backtest Results')
    
    # Create a new worksheet for this backtest
    worksheet_name = f"Backtest_{timestamp}"
    worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Write summary statistics
    # summary_data = [
    #     ['Backtest Summary', ''],
    #     ['Timestamp', timestamp],
    #     ['Parameters', param_str],
    #     ['Total Trades', len(results_df)],
    #     ['Win Rate', f"{(results_df['pnl'] > 0).mean():.2%}"],
    #     ['Average P&L', f"${results_df['pnl'].mean():.2f}"],
    #     ['Total P&L', f"${trade_results['cumulative_pnl'].iloc[-1]:.2f}"],
    #     ['Initial Capital', f"${results_df['capital'].iloc[0]:.2f}"],
    #     ['Final Capital', f"${results_df['capital'].iloc[-1]:.2f}"],
    #     ['Return on Capital', f"{(results_df['capital'].iloc[-1] / results_df['capital'].iloc[0] - 1):.2%}"],
    #     ['Average Days Held', f"{results_df['days_held'].mean():.1f}"],
    #     ['Average Return on Margin', f"{results_df['return_on_margin'].mean():.2f}%"],
    #     ['Maximum Drawdown', f"${results_df['drawdown'].min():.2f} ({results_df['drawdown_pct'].min():.2f}%)"],
    #     ['', ''],
    #     ['Trade Results', '']
    # ]

    option_type, position_side, delta_dte, start_date, end_date = param_str.split('_')
    if strat is None:
        strat = param_str.split('_')
        strat = strat[1] + strat[0]
    
     
    summary_data = [
        ['Timestamp', timestamp],
        ['Parameters', param_str],
        ['Total Trades', len(results_df)],
        ['Win Rate', f"{(results_df['pnl'] > 0).mean():.2%}"],
        ['Average P&L', f"${results_df['pnl'].mean():.2f}"],
        ['Total P&L', f"${trade_results['cumulative_pnl'].iloc[-1]:.2f}"],
        ['Initial Capital', f"${results_df['capital'].iloc[0]:.2f}"],
        ['Final Capital', f"${results_df['capital'].iloc[-1]:.2f}"],
        ['Return on Capital', f"{(results_df['capital'].iloc[-1] / results_df['capital'].iloc[0] - 1):.2%}"],
        ['Average Days Held', f"{results_df['days_held'].mean():.1f}"],
        ['Average Return on Margin', f"{results_df['return_on_margin'].mean():.2f}%"],
        ['Maximum Drawdown', f"${results_df['drawdown'].min():.2f} ({results_df['drawdown_pct'].min():.2f}%)"],
        ['', ''],
        ['Trade Results', '']
        ]
    
    # Write summary data
    worksheet.update('A1', summary_data)
    
    # Write trade results
    if not results_df.empty:
        # Prepare trade results data
        # trade_results = results_df[[
        #     'entry_date', 'exit_date', 'strike', 'option_type', 
        #     'position_side', 'entry_price', 'exit_price', 'pnl',
        #     'days_held', 'return_on_margin'
        # ]].copy()
        trade_results = results_df.copy()

        # Format dates
        trade_results['entry_date'] = trade_results['entry_date'].dt.strftime('%Y-%m-%d')
        trade_results['exit_date'] = trade_results['exit_date'].dt.strftime('%Y-%m-%d')
        
        # Write headers
        worksheet.update('A15', [trade_results.columns.tolist()])
        # Write data
        worksheet.update('A16', trade_results.values.tolist())
    
    # Write daily MTM data if available
    if daily_df is not None:
        # Add a separator
        worksheet.update(f'A{len(results_df) + 20}', [['', ''], ['Daily MTM Data', '']])
        
        # Prepare daily data
        daily_data = daily_df[[
            'Date', 'Net Liquidity', 'Position Value', 
            'Daily P&L', 'Cumulative P&L', 'Drawdown (%)'
        ]].copy()
        
        # Format dates
        daily_data['Date'] = daily_data['Date'].dt.strftime('%Y-%m-%d')
        
        # Write headers
        worksheet.update(f'A{len(results_df) + 22}', [daily_data.columns.tolist()])
        # Write data
        worksheet.update(f'A{len(results_df) + 23}', daily_data.values.tolist())
    
    logger.info(f"Results logged to Google Sheets in worksheet: {worksheet_name}")
    

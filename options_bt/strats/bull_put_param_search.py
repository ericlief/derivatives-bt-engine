from datetime import datetime
import os
import pandas as pd
from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *
from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import DataLoader
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig
import itertools
from typing import Dict, List, Callable, Any, Iterable, Optional
from options_bt.utils.gspread_log_util import upload_df_to_google_sheets, _format_single_backtest_result_row
import pickle


# Create logger instance
logger = setup_logger()

def product_dict(param_grid: Dict[str, Iterable[Any]]) -> List[Dict[str, Any]]:
    keys = list(param_grid.keys())
    vals = [param_grid[k] if isinstance(param_grid[k], Iterable) and not isinstance(param_grid[k], (str, bytes)) else [param_grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]


def backup_row_stream(row: dict, results_dir: str, filename: str):
    path = os.path.join(results_dir, filename)
    with open(path, "ab") as f:
        pickle.dump(row, f)


class GridSearchBacktester:
    def __init__(self, 
                backtester: Backtester,
                periods: Optional[List[int]] = [1],
                start_date: Optional[str] = "2020-01-01",
                end_date: Optional[str] = "2020-12-31"
    ):
        self.bt = backtester
        self.periods = periods
        self.start_date = start_date
        self.end_date = end_date

    def run(
        self,
        param_grid: Dict[str, Iterable[Any]],
        make_config: Callable[[Dict[str, Any]], Any],
        top_k: int = None,
        save_top_runs: int = 10,  # Save detailed results for top N runs
    ) -> pd.DataFrame:
        # Generate a single filename for this grid search run
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"bt_results_stream_{ts}.pkl"
        
        # Track top runs for detailed saving
        top_runs = []  # List of (score, combo, res, config, param_str) tuples
        
        combos = product_dict(param_grid)
        # print(f"Total combos (unfiltered): {len(combos)}")
        OFFSET = 0.05
        # combos = product_dict({'short_delta_target': [0.20, 0.30, 0.40, 0.50, 0.60, 0.70], 'dte_target': [30,35,40,45]})
        # combos = product_dict({'short_delta_target': [0.20, 0.30, 0.40, 0.50, 0.60, 0.70], 'dte_target': [30,35,40,45]})

        for c in combos:
            c['long_delta_target'] = max(0.05, round(c['short_delta_target'] - OFFSET, 2))

        logger.info(f"Total combos: {len(combos)}")
        logger.info(combos[:25])

        # For DEBUG
        # if True:
        #     return  
        rows = []
        for period in self.periods:  # ASSUMING YEAR PERIODS
            offset = 90  # 3 MONTHS
            start_date = self.start_date
            start_dt = pd.to_datetime(start_date)
            end_bound = pd.to_datetime(self.end_date) if self.end_date is not None else datetime.now()
            
            while True:
                # Calculate end date for this period
                end_dt = start_dt + pd.Timedelta(days=period * 365)
                if end_dt > end_bound:
                    break
                end_date = end_dt.strftime("%Y-%m-%d")
                logger.info(f"Testing slice: {start_date} to {end_date}")
                for i, combo in enumerate(combos, 1):
                    logger.info(f"Testing combo {i}: {combo}")
                    # Log early_close_days from the combo before creating config
                    logger.debug(f"Combo early_close_on_dte: {combo.get('early_close_on_dte', 'Not present')}")
                    logger.debug(f"Combo early_close_after_dit: {combo.get('early_close_after_dit', 'Not present')}")
                    config = make_config(combo, start_date, end_date)
                    # Log early_close_days from the created config
                    res = self.bt.run(config)
                    
                    # Generate param_str here as it's needed for _format_single_backtest_result_row
                    param_str = self.bt._generate_param_string(config)

                    # Use the new helper function to format the row data
                    formatted_row = _format_single_backtest_result_row(res, config, param_str, period)
                    
                    # Add combo parameters to the formatted row
                    formatted_row.update(combo)
                    rows.append(formatted_row)
                    # Backup (append row)
                    backup_row_stream(formatted_row, self.bt.results_dir, backup_filename)
                    
                    # Track top runs for detailed saving
                    if not res['trade_results'].empty and 'return_pct' in formatted_row:
                        score = formatted_row['return_pct']
                        logger.debug(f"Run score: {score}, combo: {combo}")
                        top_runs.append((score, combo, res, config, param_str))
                        # Keep only top N runs
                        top_runs.sort(key=lambda x: x[0], reverse=True)
                        if len(top_runs) > save_top_runs:
                            top_runs = top_runs[:save_top_runs]
                    else:
                        logger.debug(f"Skipping run - empty results or missing total_return. Results empty: {res['trade_results'].empty}, has total_return: {'total_return' in formatted_row}")

                # Advance start date for the next, overlapping slice
                start_dt = start_dt + pd.Timedelta(days=offset)
                start_date = start_dt.strftime("%Y-%m-%d")
                
        df = pd.DataFrame(rows)
        logger.info(f"Total runs completed: {len(rows)}")
        logger.info(f"Top runs collected: {len(top_runs)}")
        if top_runs:
            logger.info(f"Top run scores: {[run[0] for run in top_runs]}")

        # Save detailed results for top runs
        if top_runs:
             for i, (score, combo, res, config, param_str) in enumerate(top_runs):
                # Create a descriptive filename with combo parameters
                combo_str = "_".join([f"{k}{v}" for k, v in combo.items()])
                filename = f"{param_str}_TOP{i+1}_{combo_str}"
                # Save the detailed results directly
                self.bt._save_results(res, config, filename)

        #Upload the entire results_df to Google Sheets
        # Assuming all configs in a run_grid share the same option_strategy
        if not df.empty:
            # Get strategy name from the first row of results_df
            strategy_name = df['strategy'].iloc[0] 
            upload_df_to_google_sheets(df, strategy_name=strategy_name, spreadsheet_name='spx_options_bt_bull_put')


        if top_k is not None and 'total_pnl' in df.columns:
            df = df.sort_values(by='total_pnl', ascending=False).head(top_k).reset_index(drop=True)
        
        return df


def make_bull_put_config(combo, start_date, end_date):
    # Fixed dates and static pieces; vary others via combo
    return MultiLegOptionStrategyConfig(
        quantity=1,
        multiplier=100,
        option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        initial_capital=100000,
        leverage=1.0,
        start_date=start_date,
        end_date=end_date,
        use_underlying_close=False,
        early_close_on_dte=combo.get('early_close_on_dte', None),
        early_close_after_dit=combo.get('early_close_after_dit', None),
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=combo.get('max_spread_width', 100),
        max_trade_loss=combo.get('max_trade_loss', 7500),
        trade_selection_method=combo.get('trade_selection_method', TradeSelectionMethod.PREMIUM_FIRST),
        vix_range=combo.get('vix_range', None),
        vix_max=combo.get('vix_max', None),
        # premium_ratio=0.33,
        legs=[
            OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.SHORT,
                delta_target=combo['short_delta_target'],
                dte_target=combo.get('dte_target'),
                dte_range=combo.get('dte_range'),  # harmless if None
            ),
            OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.LONG,
                delta_target=combo['long_delta_target'],
                dte_target=combo.get('dte_target'),
                dte_range=combo.get('dte_range'),
            ),
        ],
    )

def run_grid():
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)

    DATA_PATH = "/Users/liefe/data/spx"
    dl = DataLoader(data_dir=DATA_PATH, options_file="options_chain_preprocessed.csv", vix_file="vix.csv", use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False) # Disable saving for grid search performance
    
    start_date="2010-01-01"
    # end_date="2011-01-01"
    # start_date = "2013-03-16"
    end_date = "2023-12-31" 
    periods = [10]
    # periods = [1, 3, 5, 10]

    runner = GridSearchBacktester(bt, periods=periods, start_date=start_date, end_date=end_date)

    param_grid = {
        # Original (commented to control explosion):
     
        'max_spread_width': [5, 10],
        # 'max_trade_loss': [2500, 5000, 7500],
        # 'trade_selection_method': [TradeSelectionMethod.DELTA_FIRST, TradeSelectionMethod.PREMIUM_FIRST],
        # 'vix_range': [(8, 22), (8, 26), (8, 30)],
        # 'vix_range': [(8, 30), None],

        # 'vix_max': [22],
        # 'vix_max': [22, 24, 28, 32],

        # 'dte_range': [(40, 45)],
        'early_close_on_dte': [20, 25, None],  # optional

        # Focused sweep
        # 'short_delta_target': [0.30, 0.40, 0.50, 0.60, 0.70],
        # 'short_delta_target': [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55],
        'short_delta_target': [0.45, 0.50, 0.55],

        # 'dte_target': [7, 15, 23, 30, 37, 44] ,   
        'dte_target': [44],            
    }

    
    results_df = runner.run(
        param_grid=param_grid, 
        make_config=make_bull_put_config, 
        save_top_runs=10,  # Save detailed results for top 5 runs
        # top_k=10
      )  # pass list of dicts too
    print(results_df.sort_values('total_pnl', ascending=False).head(20))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keys = list(param_grid.keys())
    values = ['_'.join(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v)
          for v in param_grid.values()]
    param_list = [f"{k}_{v}" for k, v in zip(keys, values)]
    param_str = "__".join(param_list)
    csv_path = os.path.join(bt.results_dir, f"backtest_summary_{timestamp}_{param_str}_{start_date}_{end_date}.csv")  
    results_df.to_csv(csv_path, index=False)
    print(param_list)



if __name__ == "__main__":
    run_grid()
    
 
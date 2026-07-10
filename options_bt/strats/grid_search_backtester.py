import itertools
import os
import pickle
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from options_bt.domain.backtester import Backtester
from options_bt.utils.gspread_log_util import upload_df_to_google_sheets, _format_single_backtest_result_row
from options_bt.utils.logger import setup_logger

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
        # combos = product_dict({'short_delta_target': [0.20, 0.30, 0.40, 0.50, 0.60, 0.70], 'dte_target': [30,35,40,45]})
        # combos = product_dict({'short_delta_target': [0.20, 0.30, 0.40, 0.50, 0.60, 0.70], 'dte_target': [30,35,40,45]})

        # Long-leg delta (when not using use_spread_width) is derived in
        # make_config itself now, not injected here -- this runner is shared
        # across strategies (bull put, iron condor) and shouldn't assume a
        # long_delta_target combo key applies to all of them.
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

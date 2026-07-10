import itertools
import multiprocessing
import os
import pickle
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from options_bt.domain.backtester import Backtester
from options_bt.utils.gspread_log_util import upload_df_to_google_sheets, _format_single_backtest_result_row
from options_bt.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────
_WINDOW_SLIDE_DAYS = 90  # rolling-window offset between overlapping test slices


def product_dict(param_grid: Dict[str, Iterable[Any]]) -> List[Dict[str, Any]]:
    keys = list(param_grid.keys())
    vals = [param_grid[k] if isinstance(param_grid[k], Iterable) and not isinstance(param_grid[k], (str, bytes)) else [param_grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]


def backup_row_stream(row: dict, results_dir: str, filename: str):
    path = os.path.join(results_dir, filename)
    with open(path, "ab") as f:
        pickle.dump(row, f)


def _generate_windows(periods: List[int], start_date: str, end_date: Optional[str]) -> List[Tuple[int, str, str]]:
    """Flatten the rolling, overlapping (period, start_date, end_date) windows
    -- period*365-day span, sliding by _WINDOW_SLIDE_DAYS each step -- into a
    list up front, so both the sequential and parallel run paths iterate
    exactly the same windows in exactly the same order as the original nested
    loop did."""
    windows = []
    for period in periods:  # ASSUMING YEAR PERIODS
        cur_start = start_date
        start_dt = datetime.strptime(cur_start, "%Y-%m-%d")
        end_bound = datetime.strptime(end_date, "%Y-%m-%d") if end_date is not None else datetime.now()
        while True:
            end_dt = start_dt + timedelta(days=period * 365)
            if end_dt > end_bound:
                break
            windows.append((period, cur_start, end_dt.strftime("%Y-%m-%d")))
            start_dt = start_dt + timedelta(days=_WINDOW_SLIDE_DAYS)
            cur_start = start_dt.strftime("%Y-%m-%d")
    return windows


# ── Parallel worker plumbing ────────────────────────────────────────────
# Each worker process holds its own copy of `data` (the option chain /
# underlying / VIX DataFrames), set once via the ProcessPoolExecutor
# initializer rather than passed with every submitted task -- it's the same,
# sizable dict needed by every job, so pickling it once per worker (at pool
# startup) instead of once per task is the difference between a few extra
# copies and thousands.
_worker_data: Optional[dict] = None


def _init_worker(data: dict) -> None:
    global _worker_data
    _worker_data = data


def _run_one_job(period: int, start_date: str, end_date: str, combo: Dict[str, Any], make_config: Callable,
                  save_trades: bool, log_to_sheets: bool) -> dict:
    """Run a single (window, combo) backtest to completion and return
    everything the caller needs to fold into results. Used directly in the
    sequential path (called in-process, `data` passed straight through) and
    as the unit of work submitted to worker processes in the parallel path
    (where `data` instead comes from the per-worker global set by
    _init_worker) -- same logic either way, so results are identical.

    save_trades/log_to_sheets are threaded through from the caller's
    Backtester rather than hardcoded, since Backtester.run() itself checks
    self.save_trades to decide whether to write this combo's results to disk
    immediately -- a caller-configured True must still take effect here.
    """
    bt_local = Backtester(data=_worker_data, save_trades=save_trades, log_to_sheets=log_to_sheets)
    logger.info(f"Testing combo: {combo} | window: {start_date} to {end_date}")
    config = make_config(combo, start_date, end_date)
    res = bt_local.run(config)
    param_str = bt_local._generate_param_string(config)
    formatted_row = _format_single_backtest_result_row(res, config, param_str, period)
    formatted_row.update(combo)
    return {
        'formatted_row': formatted_row,
        'combo': combo,
        'res': res,
        'config': config,
        'param_str': param_str,
    }


class GridSearchBacktester:
    def __init__(self,
                backtester: Backtester,
                periods: Optional[List[int]] = [1],
                start_date: Optional[str] = "2020-01-01",
                end_date: Optional[str] = "2020-12-31",
                max_workers: Optional[int] = None,
    ):
        """
        max_workers: None or 1 (default) runs sequentially in-process,
        reusing `backtester` across every combo/window -- identical to this
        class's original behavior. Set >1 to run every (rolling window,
        param combo) backtest as an independent task across a
        ProcessPoolExecutor of that many worker processes instead -- each
        worker builds its own throwaway Backtester per task (cheap: __init__
        just stores DataFrame references; the real work happens in .run()),
        so results are identical to the sequential path, just computed
        concurrently. This is the finest-grained parallelism available here
        (one task per window x combo pair), so it load-balances well
        regardless of how many windows vs. combos are in play.
        """
        self.bt = backtester
        self.periods = periods
        self.start_date = start_date
        self.end_date = end_date
        self.max_workers = max_workers

    def _shared_data(self) -> dict:
        return {
            'option_chain': self.bt.option_chain,
            'option_chain_multi_index': self.bt.option_chain_multi_index,
            'underlying': self.bt.underlying,
            'vix': self.bt.vix,
        }

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

        combos = product_dict(param_grid)
        logger.info(f"Total combos: {len(combos)}")
        logger.info(combos[:25])

        windows = _generate_windows(self.periods, self.start_date, self.end_date)
        logger.info(f"Total windows: {len(windows)} across {len(self.periods)} period(s)")
        logger.info(f"Total backtest runs: {len(windows) * len(combos)}")

        if self.max_workers and self.max_workers > 1:
            rows, top_runs = self._run_parallel(windows, combos, make_config, backup_filename, save_top_runs)
        else:
            rows, top_runs = self._run_sequential(windows, combos, make_config, backup_filename, save_top_runs)

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

    def _fold_result(self, result: dict, rows: list, top_runs: list, save_top_runs: int, backup_filename: str) -> None:
        """Apply one completed job's result the same way regardless of
        whether it ran sequentially or in a worker process: append to the
        running results list, stream a backup row to disk, and track it for
        top-N detailed saving. Always called from the main process (workers
        only compute and return -- concurrent processes appending to the
        same backup file would risk interleaved/corrupt writes)."""
        formatted_row, combo, res, config, param_str = (
            result['formatted_row'], result['combo'], result['res'], result['config'], result['param_str']
        )
        rows.append(formatted_row)
        backup_row_stream(formatted_row, self.bt.results_dir, backup_filename)

        if not res['trade_results'].empty and 'return_pct' in formatted_row:
            score = formatted_row['return_pct']
            logger.debug(f"Run score: {score}, combo: {combo}")
            top_runs.append((score, combo, res, config, param_str))
            top_runs.sort(key=lambda x: x[0], reverse=True)
            if len(top_runs) > save_top_runs:
                top_runs[:] = top_runs[:save_top_runs]
        else:
            logger.debug(f"Skipping run - empty results or missing total_return. combo: {combo}")

    def _run_sequential(self, windows, combos, make_config, backup_filename, save_top_runs):
        rows: list = []
        top_runs: list = []
        global _worker_data
        _worker_data = self._shared_data()  # _run_one_job reads this global either way

        for period, start_date, end_date in windows:
            logger.info(f"Testing slice: {start_date} to {end_date}")
            for i, combo in enumerate(combos, 1):
                logger.info(f"Testing combo {i}: {combo}")
                result = _run_one_job(period, start_date, end_date, combo, make_config,
                                      self.bt.save_trades, self.bt.log_to_sheets)
                self._fold_result(result, rows, top_runs, save_top_runs, backup_filename)

        return rows, top_runs

    def _run_parallel(self, windows, combos, make_config, backup_filename, save_top_runs):
        rows: list = []
        top_runs: list = []
        jobs = [(period, start_date, end_date, combo) for period, start_date, end_date in windows for combo in combos]
        total = len(jobs)
        logger.info(f"Running {total} backtests across {self.max_workers} worker processes")

        # spawn, not the Linux default 'fork': polars runs an internal thread
        # pool that's very likely already started in this process by the time
        # a grid search runs (OptionsDataLoader uses polars for the whole
        # load/preprocess path). Forking after those threads exist copies
        # whatever locks they held at that instant into each child, but the
        # threads themselves don't exist there to ever release them -- every
        # worker deadlocks at 0% CPU. spawn starts each worker as a fresh
        # interpreter instead, sidestepping the inherited-lock problem
        # entirely (same fix already applied in tsmom_grid_search.py).
        ctx = multiprocessing.get_context('spawn')

        with ProcessPoolExecutor(max_workers=self.max_workers, mp_context=ctx,
                                  initializer=_init_worker, initargs=(self._shared_data(),)) as pool:
            futures = {
                pool.submit(_run_one_job, period, start_date, end_date, combo, make_config,
                            self.bt.save_trades, self.bt.log_to_sheets): (start_date, end_date, combo)
                for period, start_date, end_date, combo in jobs
            }
            for i, future in enumerate(as_completed(futures), 1):
                start_date, end_date, combo = futures[future]
                try:
                    result = future.result()
                except Exception:
                    logger.exception(f"Job failed for window {start_date}-{end_date}, combo {combo}")
                    continue
                self._fold_result(result, rows, top_runs, save_top_runs, backup_filename)
                if i % 10 == 0 or i == total:
                    logger.info(f"Completed {i}/{total} backtest runs")

        return rows, top_runs

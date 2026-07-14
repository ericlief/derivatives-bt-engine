from datetime import datetime
import os
from derivatives_bt_engine.utils.logger import setup_logger
from derivatives_bt_engine.domain.enums import *
from derivatives_bt_engine.domain.backtester import Backtester
from derivatives_bt_engine.domain.dataloader import OptionsDataLoader
from derivatives_bt_engine.domain.strategy_config import MultiLegOptionStrategyConfig
from derivatives_bt_engine.domain.option_leg_config import OptionLegConfig
from derivatives_bt_engine.strats.grid_search_backtester import GridSearchBacktester
from dotenv import load_dotenv

# Create logger instance
logger = setup_logger()
load_dotenv()

def make_iron_condor_config(combo, start_date, end_date):
    """Build a symmetric iron condor: short legs at a target delta; long legs
    (the wings) are placed max_spread_width points further out-of-the-money
    (use_spread_width=True) rather than swept by their own delta_target --
    keeps the grid to one width dimension instead of two independent deltas.
    Fixed dates and static pieces; vary the rest via combo."""
    short_delta = combo['short_delta_target']
    dte_target = combo.get('dte_target')
    dte_range = combo.get('dte_range')  # harmless if None

    return MultiLegOptionStrategyConfig(
        quantity=1,
        multiplier=100,
        option_strategy=OptionsStrategy.IRON_CONDOR,
        spread_type=OptionSpreadType.IRON_CONDOR,
        initial_capital=100000,
        leverage=1.0,
        start_date=start_date,
        end_date=end_date,
        use_underlying_close=False,
        early_close_on_dte=combo.get('early_close_on_dte', None),
        early_close_after_dit=combo.get('early_close_after_dit', None),
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=combo.get('max_spread_width', 50),
        use_spread_width=combo.get('use_spread_width', True),
        max_trade_loss=combo.get('max_trade_loss', 7500),
        trade_selection_method=combo.get('trade_selection_method', TradeSelectionMethod.PREMIUM_FIRST),
        vix_range=combo.get('vix_range', None),
        vix_max=combo.get('vix_max', None),
        # _pair_iron_condor_spread_legs() pairs leg_signals positionally, not
        # by option_type/position_side, so this order is load-bearing: it
        # must be [long put (lower strike), short put (higher strike),
        # short call (lower strike), long call (higher strike)]. The long
        # legs omit delta_target/delta_range entirely -- use_spread_width
        # derives their strike from the matching short leg instead.
        legs=[
            OptionLegConfig(
                option_type=OptionsType.PUT,
                position_side=PositionSide.LONG,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionsType.PUT,
                position_side=PositionSide.SHORT,
                delta_target=short_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionsType.CALL,
                position_side=PositionSide.SHORT,
                delta_target=short_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionsType.CALL,
                position_side=PositionSide.LONG,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
        ],
    )


def run_grid():
    # Display width/precision for results (polars DataFrames) is configured
    # globally by backtester.py's module-level pl.Config calls.

    # Set up data paths. The options chain, SPX underlying, and VIX files
    # live in three different directories, each fully resolved to an
    # absolute path by its own env var -- OptionsDataLoader takes them as-is,
    # no shared base directory needed.
    #
    # .env example:
    #   SPX_OPTIONS_CHAIN_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_chain_eod.csv inside it)
    #   SPX_UNDERLYING_PATH=/Users/liefe/data/fin/market/index/SPX/eod  (looks for processed/spx_eod_preproc.csv inside it)
    #   VIX_PATH=/Users/liefe/data/fin/market/index/VIX/eod  (looks for processed/vix.csv inside it)
    OPTIONS_FILE = os.getenv('SPX_OPTIONS_CHAIN_PATH', 'options_chain_preprocessed.csv')
    SPX_FILE = os.getenv('SPX_UNDERLYING_PATH', 'spx.csv')
    VIX_FILE = os.getenv('VIX_PATH', 'vix.csv')

    dl = OptionsDataLoader(options_file=OPTIONS_FILE, spx_file=SPX_FILE, vix_file=VIX_FILE, use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False)  # Disable saving for grid search performance

    start_date = "2010-01-01"
    end_date = "2023-12-31"
    periods = [1]

    # Runs every (rolling window, param combo) backtest as an independent
    # task across this many worker processes -- set to 1 for the old
    # sequential behavior (e.g. if debugging a single combo).
    max_workers = os.cpu_count()

    runner = GridSearchBacktester(bt, periods=periods, start_date=start_date, end_date=end_date, max_workers=max_workers)

    param_grid = {
        'max_spread_width': [10, 20, 30],
        'early_close_on_dte': [20, 25, None],
        'short_delta_target': [0.20, 0.25, 0.30, 0.40],
        'dte_target': [30, 45],
    }

    results_df = runner.run(
        param_grid=param_grid,
        make_config=make_iron_condor_config,
        save_top_runs=10,
    )
    print(results_df.sort('total_pnl', descending=True).head(20))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keys = list(param_grid.keys())
    values = ['_'.join(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v)
              for v in param_grid.values()]
    param_list = [f"{k}_{v}" for k, v in zip(keys, values)]
    param_str = "__".join(param_list)
    csv_path = os.path.join(bt.results_dir, f"backtest_summary_{timestamp}_{param_str}_{start_date}_{end_date}.csv")
    results_df.write_csv(csv_path)
    print(param_list)


if __name__ == "__main__":
    run_grid()

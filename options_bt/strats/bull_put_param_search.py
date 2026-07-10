from datetime import datetime
import os
from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *
from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.strats.grid_search_backtester import GridSearchBacktester
from dotenv import load_dotenv

# Create logger instance
logger = setup_logger()
load_dotenv()

# ── Tunable defaults ────────────────────────────────────────────────
_LONG_LEG_DELTA_OFFSET = 0.05  # long-leg delta = short-leg delta - this, unless use_spread_width


def make_bull_put_config(combo, start_date, end_date):
    """Fixed dates and static pieces; vary others via combo.

    By default the long (wing) leg is picked by its own delta_target
    (short_delta_target - _LONG_LEG_DELTA_OFFSET, or an explicit
    'long_delta_target' combo override). Set combo['use_spread_width']=True
    to instead place the long leg max_spread_width points further
    out-of-the-money -- drops the long-leg delta as a grid dimension
    entirely, leaving max_spread_width as the only width knob.
    """
    use_spread_width = combo.get('use_spread_width', False)
    dte_target = combo.get('dte_target')
    dte_range = combo.get('dte_range')  # harmless if None

    if use_spread_width:
        long_leg_kwargs = {}
    else:
        long_delta = combo.get(
            'long_delta_target',
            max(0.05, round(combo['short_delta_target'] - _LONG_LEG_DELTA_OFFSET, 2))
        )
        long_leg_kwargs = {'delta_target': long_delta}

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
        use_spread_width=use_spread_width,
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
                dte_target=dte_target,
                dte_range=dte_range,  # harmless if None
            ),
            OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.LONG,
                dte_target=dte_target,
                dte_range=dte_range,
                **long_leg_kwargs,
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

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False) # Disable saving for grid search performance
    
    start_date="2010-01-01"
    # end_date="2011-01-01"
    # start_date = "2013-03-16"
    end_date = "2023-12-31" 
    periods = [10]
    # periods = [1, 3, 5, 10]

    # Runs every (rolling window, param combo) backtest as an independent
    # task across this many worker processes -- set to 1 for the old
    # sequential behavior (e.g. if debugging a single combo).
    max_workers = os.cpu_count()

    runner = GridSearchBacktester(bt, periods=periods, start_date=start_date, end_date=end_date, max_workers=max_workers)

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
    
 
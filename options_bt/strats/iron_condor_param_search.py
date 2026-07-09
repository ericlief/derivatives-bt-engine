from datetime import datetime
import os
import pandas as pd
from options_bt.utils.logger import setup_logger
from options_bt.domain.enums import *
from options_bt.domain.backtester import Backtester
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.strategy_config import MultiLegOptionStrategyConfig
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.strats.bull_put_param_search import GridSearchBacktester
from options_bt.utils.gspread_log_util import upload_df_to_google_sheets

# Create logger instance
logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────
WING_OFFSET = 0.05  # long-leg delta = short-leg delta - WING_OFFSET, per side


def make_iron_condor_config(combo, start_date, end_date):
    """Build a symmetric iron condor: same short/long delta targets on the
    put and call side. Fixed dates and static pieces; vary the rest via combo."""
    short_delta = combo['short_delta_target']
    long_delta = combo.get(
        'long_delta_target',
        max(0.05, round(short_delta - combo.get('wing_offset', WING_OFFSET), 2))
    )
    dte_target = combo.get('dte_target')
    dte_range = combo.get('dte_range')  # harmless if None

    return MultiLegOptionStrategyConfig(
        quantity=1,
        multiplier=100,
        option_strategy=OptionStrategy.IRON_CONDOR,
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
        max_trade_loss=combo.get('max_trade_loss', 7500),
        trade_selection_method=combo.get('trade_selection_method', TradeSelectionMethod.PREMIUM_FIRST),
        vix_range=combo.get('vix_range', None),
        vix_max=combo.get('vix_max', None),
        # _pair_iron_condor_spread_legs() pairs leg_signals positionally, not
        # by option_type/position_side, so this order is load-bearing: it
        # must be [long put (lower strike), short put (higher strike),
        # short call (lower strike), long call (higher strike)].
        legs=[
            OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.LONG,
                delta_target=long_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionType.PUT,
                position_side=PositionSide.SHORT,
                delta_target=short_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionType.CALL,
                position_side=PositionSide.SHORT,
                delta_target=short_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
            OptionLegConfig(
                option_type=OptionType.CALL,
                position_side=PositionSide.LONG,
                delta_target=long_delta,
                dte_target=dte_target,
                dte_range=dte_range,
            ),
        ],
    )


def run_grid():
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)

    DATA_PATH = "/Users/liefe/data/spx"
    dl = OptionsDataLoader(data_dir=DATA_PATH, options_file="options_chain_preprocessed.csv", vix_file="vix.csv", use_preprocessed=True, save_preprocessed=False)
    data = dl.load_data()

    bt = Backtester(data=data, save_trades=False, log_to_sheets=False)  # Disable saving for grid search performance

    start_date = "2010-01-01"
    end_date = "2023-12-31"
    periods = [1]

    runner = GridSearchBacktester(bt, periods=periods, start_date=start_date, end_date=end_date)

    param_grid = {
        'max_spread_width': [50, 75],
        'early_close_on_dte': [20, 25, None],
        'short_delta_target': [0.20, 0.25, 0.30],
        'dte_target': [30, 45],
    }

    results_df = runner.run(
        param_grid=param_grid,
        make_config=make_iron_condor_config,
        save_top_runs=10,
    )
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

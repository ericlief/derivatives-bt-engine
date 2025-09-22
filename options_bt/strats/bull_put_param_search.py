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
from typing import Dict, List, Callable, Any, Iterable
from itertools import islice

# Create logger instance
logger = setup_logger()

def product_dict(param_grid: Dict[str, Iterable[Any]]) -> List[Dict[str, Any]]:
    keys = list(param_grid.keys())
    vals = [param_grid[k] if isinstance(param_grid[k], Iterable) and not isinstance(param_grid[k], (str, bytes)) else [param_grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]

class GridSearchBacktester:
    def __init__(self, backtester, log_each_to_sheets: bool = False):
        self.bt = backtester
        self.log_each_to_sheets = log_each_to_sheets

    def run(
        self,
        param_grid: Dict[str, Iterable[Any]],
        make_config: Callable[[Dict[str, Any]], Any],
        top_k: int = None,
    ) -> pd.DataFrame:
        combos = product_dict(param_grid)
        # print(f"Total combos (unfiltered): {len(combos)}")
        OFFSET = 0.10
        combos = product_dict({'short_delta_target': [0.20, 0.30, 0.40, 0.50, 0.60, 0.70], 'dte_target': [30,35,40,45]})
        for c in combos:
            c['long_delta_target'] = max(0.05, round(c['short_delta_target'] - OFFSET, 2))

        print(f"Total combos: {len(combos)}")
        print(combos[:25])

        # if True:
        #     return  

        rows = []
        for i, combo in enumerate(combos, 1):
            config = make_config(combo)
            res = self.bt.run(config)
            tr = res['trade_results']
            stats = res.get('stats', pd.DataFrame())
            if tr is None or tr.empty:
                rows.append({**combo, 'total_pnl': 0.0, 'final_capital': config.initial_capital, 'win_rate_pct': 0.0, 'trades': 0, 'max_dd_pct': None})
                continue

            total_pnl = float(tr['cumulative_pnl'].iloc[-1])
            final_capital = float(tr['capital'].iloc[-1])
            win_rate = float(((tr['pnl'] > 0).sum() / len(tr)) * 100) if len(tr) else 0.0
            max_dd_pct = float(stats['Drawdown (%)'].min()) if not stats.empty else None

            rows.append({
                **combo,
                'total_pnl': round(total_pnl, 2),
                'final_capital': round(final_capital, 2),
                'win_rate_pct': round(win_rate, 2),
                'trades': int(len(tr)),
                'avg_days_held': round(float(tr['days_held'].mean()), 2),
                'avg_roi_pct': round(float(tr['roi'].mean()), 2),
                'max_profit': round(float(tr['pnl'].max()), 2),
                'max_loss': round(float(tr['pnl'].min()), 2),
                'max_dd_pct': round(max_dd_pct, 2) if max_dd_pct is not None else None,
            })

        df = pd.DataFrame(rows)
        if top_k is not None and 'total_pnl' in df.columns:
            df = df.sort_values(by='total_pnl', ascending=False).head(top_k).reset_index(drop=True)
        return df


def make_bull_put_config(combo):
    # Fixed dates and static pieces; vary others via combo
    return MultiLegOptionStrategyConfig(
        quantity=1,
        option_strategy=OptionStrategy.BULL_PUT_CREDIT_SPREAD,
        spread_type=OptionSpreadType.VERTICAL,
        initial_capital=100000,
        leverage=1.0,
        start_date="2020-01-01",
        end_date="2020-12-31",
        use_underlying_close=False,
        early_close_days=combo.get('early_close_days', None),
        max_margin_utilization=0.80,
        max_positions=1,
        max_spread_width=combo.get('max_spread_width', 100),
        max_trade_loss=combo.get('max_trade_loss', 7500),
        trade_selection_method=combo.get('trade_selection_method', TradeSelectionMethod.DELTA_FIRST),
        vix_range=combo.get('vix_range', None),
        vix_max=combo.get('vix_max', None),
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

    bt = Backtester(data=data, save_trades=True, log_to_sheets=False)
    runner = GridSearchBacktester(bt)

    param_grid = {
        # Original (commented to control explosion):
        # 'max_spread_width': [50, 75, 100],
        # 'max_trade_loss': [2500, 5000, 7500],
        # 'trade_selection_method': [TradeSelectionMethod.DELTA_FIRST, TradeSelectionMethod.PREMIUM_FIRST],
        # 'vix_range': [(8, 22), (8, 26), (8, 30)],
        # 'vix_max': [22, 24, 26, 28],
        # 'dte_range': [(40, 45)],
        # 'early_close_days': [20, 30],  # optional

        # Focused sweep
        'short_delta_target': [0.30, 0.40, 0.50, 0.60, 0.70],
        # 'long_delta_target': [0.45, 0.50],
        'dte_target': [30, 35, 40, 45],
    }

    
    # results_df = runner.run(param_grid=param_grid, make_config=make_bull_put_config, top_k=10)  # pass list of dicts too
    # print(results_df.sort_values('total_pnl', ascending=False).head(20))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keys = list(param_grid.keys())
    values = ['_'.join(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v)
          for v in param_grid.values()]
    param_list = [f"{k}_{v}" for k, v in zip(keys, values)]
    param_str = "__".join(param_list)
    # csv_path = os.path.join(bt.results_dir, f"backtest_summary_{timestamp}.csv")
    # results_df.to_csv(csv_path, index=False)
    print(param_list)
if __name__ == "__main__":
    run_grid()
    
 
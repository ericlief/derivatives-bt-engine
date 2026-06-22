import sys
from typing import Dict, List, NamedTuple, Optional, Tuple, Union
import numpy as np
import pandas as pd
import logging
import time
from datetime import datetime
import os
from enum import Enum

import polars as pl

from options_bt.domain.enums import *
from options_bt.domain.strategy_config import FuturesStrategyConfig, SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.futures_signal_generator import FuturesSignalGenerator
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.position import FuturesPosition, SingleLegOptionPosition     
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.position import MultiLegOptionPosition
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

# Create logger instance
logger = setup_logger()
pl.Config.set_tbl_rows(100)
pl.Config.set_tbl_cols(50)
pl.Config.set_fmt_str_lengths(1000)


class Backtester:
    """Class to manage backtest execution."""
    
    def __init__(self, 
                 data: Dict,
                 save_trades: bool = True,
                 log_to_sheets: bool = True):
        """
        Initialize backtester with configuration.
        
        Args:
            data: A dictionary containing option chain and underlying data.
            save_trades: Whether to save trade results.
            log_to_sheets: Whether to log results to Google Sheets.
        """
        self.option_chain = data['option_chain']
        self.option_chain_multi_index = data['option_chain_multi_index']
        self.underlying = data['underlying']
        self.vix = data['vix']
        self.save_trades = save_trades
        self.log_to_sheets = log_to_sheets

        # Track execution times
        self.execution_times = {}

        # Results (clear between runs?)
        self.results_dir = 'results'
        self.results: Dict[str, pd.DataFrame] = {}
        self.__post_init__()

    @staticmethod
    def _not_empty(df) -> bool:
        """Works for both pandas (.empty) and polars (.height) DataFrames."""
        return (df.height > 0) if isinstance(df, pl.DataFrame) else (not df.empty)

    def __post_init__(self):
        """Create the results directory if it does not exist."""

        os.makedirs(self.results_dir, exist_ok=True)
        logger.info(f'Instantiated Backtester with following data: ')
        logger.info(f'Options chain: {self._not_empty(self.option_chain)}')
        logger.info(f'Underlying: {self._not_empty(self.underlying)}')
        logger.info(f'VIX: {self._not_empty(self.vix)}')

    def run(
        self,
        config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig]
        
    ) -> dict:
        """Execute a backtest with the given parameters."""
        start_time = time.time()
        logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Logic for early close days
        # leg_early_close = leg.early_close_days if leg.early_close_days is not None else strategy.early_close_days
        
        # Futures signals/margin calc run entirely in polars (no spreads/legs,
        # a much simpler path than options); the option paths below are
        # untouched and stay on pandas.
        is_futures = isinstance(config, FuturesStrategyConfig)

        # Initialize trade manager
        trade_manager = TradeManager(config=config, vix=self.vix)
        if is_futures:
            signal_generator = FuturesSignalGenerator(config=config, underlying=self.underlying)
        else:
            signal_generator = OptionSignalGenerator(option_chain=self.option_chain.copy(), underlying=self.underlying.copy(), config=config)
        # Generate or validate signals
        signal_start = time.time()
        if isinstance(config, SingleLegOptionStrategyConfig):
            signals = signal_generator.generate_single_leg_signals(
                option_type=config.leg.option_type,
                position_side=config.leg.position_side,
                delta_target=config.leg.delta_target,
                delta_range=config.leg.delta_range,
                dte_target=config.leg.dte_target,
                dte_range=config.leg.dte_range,
                early_close_days=config.leg.early_close_days,
                start_date=config.start_date,
                end_date=config.end_date
            )
        elif isinstance(config, MultiLegOptionStrategyConfig):
            signals = signal_generator.generate_multi_leg_signals()
        elif is_futures:
            signals = signal_generator.generate_futures_signals(
                futures_type=config.futures_type,
                futures_strategy=config.futures_strategy,
                position_side=config.position_side,
                start_date=config.start_date,
                end_date=config.end_date
            )
        else:
            raise ValueError("Invalid config type")

        if (signals.height == 0) if is_futures else signals.empty:
            logger.warning("No valid signals generated")
            return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}

        max_allowed_margin = config.max_margin_utilization * config.initial_capital * config.leverage
        logger.info(f"Maximum allowed margin: ${max_allowed_margin:.2f} ({config.max_margin_utilization:.0%} of capital with {config.leverage}x leverage)")
        logger.info(f"Maximum simultaneous positions: {config.max_positions}")

        if is_futures:
            logger.info(f"Calculating margin requirements for futures {len(signals)} signals: {config.quantity} | {config.futures_strategy} | {config.futures_type}")
            signals = signals.with_columns(
                (pl.col('initial_margin') * config.quantity / config.leverage).alias('margin_required')
            )
            valid_signals = signals.filter(pl.col('margin_required') <= max_allowed_margin)
            self.execution_times['signal_generation'] = time.time() - signal_start

            filtered_count = signals.height - valid_signals.height
            if filtered_count > 0:
                logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
            if valid_signals.height > 0:
                logger.info(f"Average margin requirement for trades: ${valid_signals['margin_required'].mean():.2f}")
                logger.info(f"Maximum margin requirement for trades: ${valid_signals['margin_required'].max():.2f}")
            logger.info(f"Total valid signals: {valid_signals.height}")

            if valid_signals.height == 0:
                logger.info("No valid signals; skipping trade execution.")
                return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}

            backtest_start = time.time()
            results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(
                valid_signals,
                option_chain=self.option_chain,
                underlying_price_history=self.underlying,
            )
            self.execution_times['backtest_execution'] = time.time() - backtest_start
            trade_results = results_transactions_dict['trade_results']
            transactions = results_transactions_dict['transactions']
            if trade_results.empty:
                logger.warning("No trades were executed successfully")
                return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}

            return self._finalize_results(trade_results, transactions, config, start_time)

        # Pre-calculate margin requirements for all signals
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)

        # Handle all spread types
        if is_spread:
            # Calculate margins per spread group and ensure proper alignment
            # margins = signals.groupby('spread_id').apply(SingleLegOptionStrategyConfig.calculate_margin_for_spread)
            logger.info(f"Calculating margin requirements for multileg trade signals for {config.quantity} | {config.option_strategy} | {config.spread_type}")
            if 'spread_width' not in signals:
                logger.warning(f"Spread width not found in multileg signals. Calculating.")
                signals['spread_width'] = abs(signals["leg1_strike"] - signals["leg2_strike"])

            # Filter out trades with excessive spread width if max_spread_width is set
            if config.max_spread_width is not None:
                original_count = len(signals)
                logger.debug(f'Number of signals before filtering {original_count}')
                signals = signals[signals['spread_width'] <= config.max_spread_width]
                filtered_count = original_count - len(signals)
                if filtered_count > 0:
                    logger.warning(f"Filtered out {filtered_count} trades due to excessive spread width (> {config.max_spread_width} points)")
                logger.info(f"Maximum spread width in trades: {signals['spread_width'].max() if len(signals) > 0 else 'N/A'} points")

            # Filter out trades with excessive max trade loss if max_trade_loss is set
            if 'margin_required' not in signals.columns:
                if signals.empty:
                    logger.warning("No signals to filter for max_trade_loss after previous filters.")
                else:
                    logger.info('Calculating margin requirements for multileg position')
                    original_count = len(signals)
                    if config.option_strategy in [OptionStrategy.BULL_PUT_CREDIT_SPREAD, OptionStrategy.BEAR_CALL_CREDIT_SPREAD]:
                        # For credit spreads: max loss = (spread_width - credit) * 100 * qty
                        credit = signals['spread_price'].clip(lower=0)  # ensure non-negative credit
                        signals['margin_required'] = (signals['spread_width'] - credit) * config.quantity * config.multiplier
                    elif config.option_strategy in [OptionStrategy.BULL_CALL_DEBIT_SPREAD, OptionStrategy.BEAR_PUT_DEBIT_SPREAD]:
                        signals['margin_required'] = (signals['spread_price'].abs()) * config.quantity * config.multiplier
                    else:
                        signals['margin_required'] = signals['spread_width'] * config.quantity * config.multiplier  # fallback

                    if config.max_trade_loss is not None:
                        signals = signals[signals['margin_required'] <= config.max_trade_loss]
                        filtered_count = original_count - len(signals)
                        if filtered_count > 0:
                            logger.warning(f"Filtered out {filtered_count} trades due to max allowed trade loss (${config.max_trade_loss})")
                        logger.info(f"Maximum trade loss: ${signals['margin_required'].max() if len(signals) > 0 else 'N/A'}")
           

            if config.premium_ratio is not None:
                original_count = len(signals)
                premium = signals['spread_price'].clip(lower=0)
                signals['premium_ratio'] = round(premium / signals['spread_width'], 2)
                signals = signals[signals['premium_ratio'] >= config.premium_ratio]
                filtered_count = original_count - len(signals)
                if filtered_count > 0:
                    logger.warning(f"Filtered out {filtered_count} trades due to premium ratio ({config.premium_ratio})")
                logger.info(f"Minimum: {signals['premium_ratio'].min() if len(signals) > 0 else 'N/A'}")
                logger.info(f"Maximum: {signals['premium_ratio'].max() if len(signals) > 0 else 'N/A'}")

            # Ensure 'margin_required' is calculated for spreads before trade selection
            # if 'margin_required' not in signals.columns: # If not already calculated by MultiLegOptionPosition
            #     signals['margin_required'] = round(signals['spread_width'] * config.quantity * config.multiplier, 2)
            #     logger.debug(f'Calculated margins for {len(signals)} spread groups')    
            logger.debug(f'First few (filtered) signals: {signals.head()}')
        
        # Single leg
        elif isinstance(config, SingleLegOptionStrategyConfig):
            logger.info(f"Calculating margin requirements for single leg trade signals for {config.quantity} | {config.option_strategy} | {config.leg.option_type} | {config.leg.delta_target if config.leg.delta_target else config.leg.delta_range}")
            signals['margin_required'] = signals.apply(
                lambda row: SingleLegOptionPosition.calculate_margin(
                    quantity=config.quantity,
                    option_type=config.leg.option_type,
                    position_side=config.leg.position_side,
                    entry_price=row['midpoint_price'],
                    strike=row['strike'],
                    underlying_price=row['underlying_last'],
                    leverage=config.leverage
                    ), 
                axis=1
            )

        # Filter out trades that would exceed margin limits
        valid_signals = signals[signals['margin_required'] <= max_allowed_margin]
        self.execution_times['signal_generation'] = time.time() - signal_start

        filtered_count = len(signals) - len(valid_signals)
        if filtered_count > 0:
            logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
        logger.info(f"Average margin requirement for trades: ${valid_signals['margin_required'].mean():.2f}")
        logger.info(f"Maximum margin requirement for trades: ${valid_signals['margin_required'].max():.2f}")
        logger.info(f"Total valid signals: {len(valid_signals)}")
        logger.debug(valid_signals)
        # if config.trade_selection_method == TradeSelectionMethod.MARGIN_FIRST:
        #     valid_signals = valid_signals.sort_values(by=['margin_required', 'delta_difference'])

        
        
        
        
        logger.info("Minimum margin sample:")
        logger.info(valid_signals.sort_values(by="margin_required", ascending=True).head())
        logger.info("Maximum margin sample:")
        logger.info(valid_signals.sort_values(by="margin_required", ascending=True).tail())

        logger.info(f"Total signals: {len(valid_signals)} | Date Range: {valid_signals.index.min()} to {valid_signals.index.max()}")
        if valid_signals.empty:
            logger.info("No valid signals; skipping trade execution.")
            return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}

        # Execute trades
        backtest_start = time.time()
        results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(valid_signals, 
                                                                                option_chain=self.option_chain, 
                                                                                underlying_price_history=self.underlying)
        self.execution_times['backtest_execution'] = time.time() - backtest_start
        trade_results = results_transactions_dict['trade_results']
        transactions = results_transactions_dict['transactions']
        if trade_results.empty:
            logger.warning("No trades were executed successfully")
            return {'trade_results': pd.DataFrame(), 'transactions': pd.DataFrame()}

        return self._finalize_results(trade_results, transactions, config, start_time)

    def _finalize_results(self, trade_results: pd.DataFrame, transactions: pd.DataFrame,
                          config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig],
                          start_time: float) -> dict:
        """
        Shared post-processing for both the option and futures paths:
        cumulative PnL/capital tracking, Sharpe, column ordering, drawdown,
        and saving. trade_results/transactions are pandas here regardless of
        which path produced them (TradeManager converts futures results to
        pandas at its return boundary) since this math is unchanged from
        before the futures-polars migration.
        """
        # Calculate cumulative metrics based on PnL
        # Insert a new row at the start with the initial capital (pre-trade)
        # init_row = trade_results.iloc[0].copy()
        # for col in trade_results.columns:
        #     if col in init_row:
        #         init_row[col] = 0.0
        init_row = pd.DataFrame([{
            'pnl': 0.0,
            'cumulative_pnl': 0.0,
            'capital': config.initial_capital,
            'trade_id': 0
        }])

        # Preserve integer trade identifiers so they don't get upcast to float
        # if 'trade_id' in init_row:
        #     init_row['trade_id'] = 0
        # init_row['capital'] = config.initial_capital
        trade_results = pd.concat(
            [init_row, trade_results],
            ignore_index=True
        )

        if 'trade_id' in trade_results.columns:
            trade_results['trade_id'] = trade_results['trade_id'].astype('Int64')
        trade_results['cumulative_pnl'] = round(trade_results['pnl'].cumsum(), 2)
        trade_results['capital'] = round(config.initial_capital + trade_results['cumulative_pnl'], 2)  # Track actual capital based on cumulative PnL
        trade_results['peak_capital'] = round(trade_results['capital'].cummax(), 2)
        trade_results['ret'] = trade_results['pnl'] / trade_results['capital'].shift(1)

        # Remove init rowdfff
        trade_results = trade_results.iloc[1:]

        # Calculate margin utilization
        trade_results['margin_utilization'] = round(trade_results['capital_used'] / config.initial_capital, 4)
        # trade_results['avg_margin_util'] = round(trade_results['margin_utilization'].mean(), 2)
        # trade_results['max_margin_util'] = round(trade_results['margin_utilization'].max(), 2)
        # logger.info(f"Average margin utilization: {trade_results['avg_margin_util'].iloc[0]:.2%}")
        # logger.info(f"Maximum margin utilization: {trade_results['max_margin_util'].iloc[0]:.2%}")
        
        # Calculate Sharpe Ratio without risk-free rate
        sharpe = None
        if len(trade_results) > 1:
            # If you want to keep trade-to-trade returns:
            avg_trade_days = trade_results['days_held'].mean()  # Average days per trade
            annualization_factor = np.sqrt(252 / avg_trade_days)
            returns = np.diff(trade_results['capital'].values) / trade_results['capital'].values[:-1]
            if len(returns) > 0 and np.std(returns) > 0:
                sharpe = np.mean(returns) / np.std(returns) * annualization_factor
                logger.info(f"Sharpe Ratio: {sharpe:.2f}")

        # Save results if requested
        # Generate parameter string based on backtest type
        param_str = self._generate_param_string(config)

        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        self.execution_times['total'] = round(total_time, 2)

        # Order columns (futures trade results carry futures_strategy/roi
        # instead of option_strategy/premium/ret_per_unit_risk/ret_per_point)
        if isinstance(config, FuturesStrategyConfig):
            ordered_cols = [
                'trade_id',
                'quantity',
                'opened',
                'closed',
                'days_held',
                'futures_strategy',
                'close_reason',
                'bp',
                'margin_utilization',
                'capital',
                'peak_capital',
                'capital_used',
                'pnl',
                'cumulative_pnl',
                'fees',
                'ret',
                'roi',
            ]
        else:
            ordered_cols = [
                'trade_id',
                'quantity',
                'opened',
                'closed',
                'days_held',
                'option_strategy',
                'close_reason',
                'bp',
                'margin_utilization',
                'capital',
                'peak_capital',
                'capital_used',
                'pnl',
                'cumulative_pnl',
                'premium',
                'fees',
                'ret',
                'ret_per_unit_risk',
                'ret_per_point',
            ]

        trade_results = trade_results[[c for c in ordered_cols if c in trade_results.columns]]

        results = {
            'trade_results': trade_results,
            'transactions': transactions
        }

        print(results['trade_results'])

        if not results['trade_results'].empty:
            if isinstance(config, FuturesStrategyConfig):
                results = self.calculate_futures_mtm_drawdown(results, config)
            else:
                results = self.calculate_simple_drawdown(results, config)

        if self.save_trades:
            save_start = time.time()
            self._save_results(
                results,
                config,
                param_str=param_str
            )
            self.execution_times['saving'] = time.time() - save_start
            

    
        # NB: cumulative_pnl is the sum of realized profits/losses across all closed trades.
        # It starts from initial_capital and accumulates only closed P&L (not unrealized)
        # Thus (option_bp) matches the analytical P&L (cumulative_pnl + initial_capital):
        if not trade_results.empty:
            assert abs(trade_results['capital'].iloc[-1] - trade_results['bp'].iloc[-1]) < 1e-6, f'Final capital: {trade_results["capital"].iloc[-1]} | BP: {trade_results["bp"].iloc[-1]}'


        return results
    
    def run_multiple_backtests(
        self,
        configs: List[Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]]
  
    ) -> Dict:
        """Run multiple backtests with different parameters using the same data."""
  
        for i, config in enumerate(configs, 1):

            logger.info(f"Running backtest {i}/{len(configs)} ({i / len(config):.0%})")            
            start_time = time.time()
            # Prepare parameters for this backtest
            # params = prepare_backtest_params(
            #     params=params,
            #     spx_file_path=spx_file_path,
            #     options_chain_file_path=options_chain_file_path,
            #     options_chain=options_chain,
            #     spx_data=spx_data,
            #     preloaded_data=preloaded_data
            # )
            # Run backtest
            results = self.run_backtest(config)
            execution_time = time.time() - start_time
            
        #     results[f"backtest_{i}"] = {
        #         'params': params,
        #         'results': results,
        #         'execution_time': execution_time
        #     }
            
        #     logger.info(f"Backtest {i} completed in {execution_time:.2f} seconds")
        
        # total_time = time.time() - start_time     
        # logger.info(f"\nAll backtests completed in {total_time:.2f} seconds")
        # logger.info(f"Average time per backtest: {total_time/len(hyperparameter_sets):.2f} seconds")
        
        # return results


    
    def calculate_mtm(self, results: dict, config: Union[SingleLegOptionPosition, MultiLegOptionStrategyConfig]) -> pd.DataFrame:
        """
        Calculate MTM and log the results.
        
        Args:
            results: Dict of trade results and transations DataFrames  
            config: Union[SingleLegOptionPosition, MultiLegOptionStrategyConfig]
                The configuration of the option position
        Returns:
            DataFrame: The calculated MTM
        """

        logger.info("Running MTM calculation")
        mtm_start = time.time()
        
        # Generate parameter string based on backtest type
        param_str = self._generate_param_string(config)
        
        # Call the private method to perform the actual calculation
        daily_df = self._calculate_mtm(
            results,
            initial_capital=config.initial_capital,
            leverage=config.leverage
        )
        
        mtm_time = time.time() - mtm_start
        logger.info(f"MTM calculation completed in {mtm_time:.2f} seconds")

        # Save summary trade_results to CSV
        if self.save_trades:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Save MTM results with same timestamp
            mtm_csv_path = os.path.join(self.results_dir, f"mtm_{param_str}_{timestamp}.csv")
            daily_df.to_csv(mtm_csv_path, index=False)
            
                    # Create results file for logging summary
            results_file_path = os.path.join(self.results_dir, f"backtest_summary_{param_str}_{timestamp}.txt")
                   
                # 'Date': date,
                # 'Net Liquidity': round(net_liq, 2),
                # 'BP': round(option_bp, 2),
                # 'Cash': cash,
                # 'Position Value': round(daily_position_value, 2),
                # 'Margin Requirement': round(daily_margin_requirement, 2),
                # 'Daily P&L': round(daily_pnl, 2),
                # 'Cumulative P&L': round(cumulative_pnl, 2),
                # 'Drawdown ($)': round(drawdown_amount, 2),
                # 'Drawdown (%)': round(drawdown_pct, 2),
                # 'Daily ROI (%)': daily_roi,
                # 'Total ROI (%)': total_roi,
                # 'Active Positions': len(active_trades),
                # 'Peak Liquidity': round(peak_liquidity, 2),
                # 'Margin Utilization (%)': round(daily_margin_requirement / initial_capital * 100, 2)
 
            trade_results = results['trade_results']
            transactions = results['transactions']
            with open(results_file_path, 'w') as results_file:
                results_file.write("Backtest Results Summary:\n")
                results_file.write(f"Total trades executed: {len(trade_results)}\n")
                results_file.write(f"Winning trades: {(trade_results['pnl'] > 0).sum()}\n")
                results_file.write(f"Win rate: {((trade_results['pnl'] > 0).sum() / len(trade_results)):.2%}\n")
                results_file.write(f"Total P&L: ${trade_results['cumulative_pnl'].iloc[-1]:.2f}\n")
                results_file.write(f"Final capital: ${trade_results['capital'].iloc[-1]:.2f}\n")
                results_file.write(f"Return on initial capital: {(trade_results['capital'].iloc[-1] / config.initial_capital - 1):.2%}\n")
                results_file.write(f"Average days held: {trade_results['days_held'].mean():.1f}\n")
                results_file.write(f"Average return on margin: {trade_results['return_on_margin'].mean():.2f}%\n")
                if daily_df is not None and not daily_df.empty:
                    # OLD (for negative drawdown): daily_df['Drawdown ($)'].min() and daily_df['Drawdown (%)'].min()
                    max_drawdown_amount = daily_df['Drawdown ($)'].max()  # Now using max since drawdown is positive
                    max_drawdown_percentage = daily_df['Drawdown (%)'].max()  # Now using max since drawdown is positive
                    # results_file.write(f"Maximum drawdown: ${daily_df['max_drawdown'].iloc[-1]:,.2f} ({daily_df['max_drawdown_pct'].iloc[-1]:.2f}%)\n")
                    results_file.write(f"Maximum drawdown: ${max_drawdown_amount:.2f} ({max_drawdown_percentage:.2f}%)\n")
        
            # Final assertion
            assert abs(trade_results['bp'].iloc[-1] - daily_df['BP'].iloc[-1]) < 1e-6, f'Final trade BP: {trade_results["bp"].iloc[-1]} | MTM BP: {daily_df["BP"].iloc[-1]}'
            assert abs(daily_df['BP'].iloc[-1] - daily_df['Cash'].iloc[-1]) < 1e-6, f'MTM BP: {daily_df["BP"].iloc[-1]} | Cash: {daily_df["Cash"].iloc[-1]}'
            assert abs(daily_df['Net Liquidity'].iloc[-1] - daily_df['Cash'].iloc[-1]) < 1e-6, f'MTM Net Liquidity: {daily_df["Net Liquidity"].iloc[-1]} | Cash: {daily_df["Cash"].iloc[-1]}'
            
        return daily_df
    
    def _calculate_mtm(
        self, 
        results: Dict,  
        initial_capital: float, 
        use_underlying_close: bool = True, 
        leverage: float = 1.0
    ) -> Tuple[pd.DataFrame, float, float]:
        """
        Calculate and save mark-to-market (MTM) data for a backtest.

        Args:
            results: Dict of trade results and transations DataFrames  
            initial_capital (float): The initial capital for the backtest.
            use_spx_close (bool, optional): Flag to use S&P 500 close price. Defaults to True.
            leverage (float, optional): Leverage factor for the backtest. Defaults to 1.0.
        """
        # Convert string dates to timestamps if they're strings
        # start_date = pd.Timestamp(start_date).normalize() if start_date else self.option_chain_multi_index.index.get_level_values(0).min()
        # initial_end_date = pd.Timestamp(end_date).normalize() if end_date else self.option_chain_multi_index.index.get_level_values(0).max()
        
        # Find the latest exit date for trades that opened within our range
        trade_results = results['trade_results']
        transactions = results['transactions']

        # Date range for executed trades
        start_date = trade_results['opened'].iloc[0]
        end_date = trade_results['closed'].iloc[-1]
        # for trade in trade_results.itertuples():
        #     trade_start = pd.Timestamp(trade.entry_date).normalize()
        #     trade_end = pd.Timestamp(trade.exit_date).normalize()
        #     if start_date <= trade_start <= initial_end_date:  # Trade opened in our range
        #         latest_exit = max(latest_exit, trade_end)
        
        # Use the later of initial_end_date or latest_exit
        # end_date = latest_exit
        
        # if initial_end_date != end_date:
        #     logger.debug(f"Adjusting MTM end date from {initial_end_date} to {end_date} to include all trade exits")
        
        # Initialize tracking variables
        cash = initial_capital
        peak_liquidity = initial_capital  # Change from peak_capital to peak_liquidity
        net_liq = initial_capital  # Change from capital to net_liq
        option_bp = initial_capital     # This tracks available buying power for new trades
        cumulative_pnl = 0              # Track cumulative P&L
        daily_data = []
        
        # Dictionary to track active trades
        active_trades = {}  # (entry_date, strike, option_type) -> {'position_value': value, 'margin_requirement': margin}
        
        # Process each date in the backtest period
        for date in pd.date_range(start=start_date, end=end_date):
            date = pd.Timestamp(date).normalize()
            daily_pnl = 0
            daily_margin_requirement = 0
            daily_position_value = 0
            daily_cash_flow = 0  # Track daily cash flows from premiums
            
            logger.debug(f'Processing date: {date}')
            # First, check for any trades that start on this date
            for trade in trade_results.itertuples():
                trade_start = pd.Timestamp(trade.opened).normalize()
                trade_end = pd.Timestamp(trade.closed).normalize()
                trade_id = (trade.expire_date, trade.strike, trade.option_type)

                # Handle existing trades
                if trade_id in active_trades:
                    logger.debug(f'Processing active trade: {trade_id}')
        
                    # Calculate daily P&L for this trade
                    # daily_pnl += round(current_value - prev_value, 2) if current_value is not None else 0
                    # logger.debug(f'Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

                    # Close trade
                    if trade_end == date:
                        logger.debug(f'Closing trade: {trade_id}')

  
                        # Release margin back to BP for short positions
                        if PositionSide.is_short(trade):
                            logger.debug(f'BP before: {option_bp}')
                            option_bp += active_trades[trade_id]['margin_requirement']
                            logger.debug(f"BP after margin release of {active_trades[trade_id]['margin_requirement']}: {option_bp}")

                        # Validate closing/exit price sign 
                        exit_price = PriceUtils.get_signed_exit_price(trade.exit_price, trade.position_side)
                        #TODO: check if this is correct and quantity should be multiplied here
                        exit_premium = round(exit_price * 100 * trade.quantity, 2)  # Premium in dollars
                        logger.debug(f'Premium exit: {exit_premium}')
                        logger.debug(f'daily cash effect, before: {daily_cash_flow} | BP {option_bp}')
                        
                        prev_value = active_trades[trade_id]['position_value']
                        current_value = exit_premium  # ?
                        daily_pnl += round(current_value - prev_value, 2) if current_value is not None else 0
                        logger.debug(f'On close Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

                        
                        # Accumulate this to cash reserves
                        # daily_cash_flow += premium  # Already signed in the trade
                        # option_bp += premium  # Already signed in the trade

                        # TODO  add a func for closing
                        commission = 1.78
                        fees = 0
                        # ITM
                        # early_closure = False
                        exercise_fee = 0
                        if date < trade.expire_date:
                            # early_closure = True
                            exercise_fee += 5

                        fees += commission + exercise_fee
                        fees = round(fees * trade.quantity, 2)
                        # realized_pnl = exit_premium +- fees
                        daily_cash_flow = round(daily_cash_flow + exit_premium - fees, 2)
                        option_bp = round(option_bp + exit_premium - fees, 2)
                        logger.debug(f'daily cash effect, after: {daily_cash_flow} | BP {option_bp}')

                        del active_trades[trade_id]

                    # Update trade
                    else:
                        logger.debug(f'Updating existing trade {trade_id}')
                        current_value = self.calculate_daily_value(trade, date, use_underlying_close)
                        prev_value = active_trades[trade_id]['position_value']
                        daily_pnl += round(current_value - prev_value, 2) if current_value is not None else 0
                        logger.debug(f'Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

                        if current_value is not None:
                            active_trades[trade_id]['position_value'] = current_value
                            daily_position_value = round(daily_position_value + current_value, 2)  # Only add once
                            daily_margin_requirement += active_trades[trade_id]['margin_requirement']
                
                # Handle new trades
                elif trade_start == date:
                    logger.debug(f'Opening new trade: {trade_id}')
                    # position_value = calculate_daily_value(trade, date, self.option_chain_multi_index, spx_data, use_spx_close)
                    entry_price = PriceUtils.get_signed_entry_price(trade.entry_price, trade.position_side)
                    position_value = -round(entry_price * 100 * trade.quantity, 2)
                    if position_value is not None:
                        active_trades[trade_id] = {
                            'position_value': position_value,
                            'margin_requirement': trade.capital_used
                        }
                        
                        # entry_price = get_signed_entry_price(trade)
                        premium = round(entry_price * 100 * trade.quantity, 2)  # total premium dollars

                        # Accumulate entry price to cash flow (signed based on position side)
                        logger.debug(f'Premium at entry: {premium}')
                        logger.debug(f'Daily cash flow before: {daily_cash_flow}, BP before: {option_bp}')
                        # daily_cash_flow += premium  # Already signed in the trade
                        # option_bp += premium  # Already signed in the trade
                        daily_cash_flow = round(daily_cash_flow + premium, 2)
                        option_bp = round(option_bp + premium, 2)

                        req_margin = trade.capital_used
                        daily_margin_requirement += req_margin
                        
                        # For short positions, reduce BP
                        if PositionSide.is_short(trade):
                            option_bp = round(option_bp - req_margin / leverage, 2)  # Account for leverage in BP reduction
                            logger.debug(f'Margin reduced for short, BP now: {option_bp}')

                        # Update position value and margin
                        daily_position_value = round(daily_position_value + position_value, 2)
                        logger.debug(f'Daily Position Value: {position_value}')
    
            # Update cumulative P&L
            cumulative_pnl = round(cumulative_pnl + daily_pnl, 2)
            
            # Update cash with any daily premium flows
            # NB: there is no change in daily cash or BP due to unrealized pnl for equity and index options, only for certain futures
            cash  = round(cash + daily_cash_flow, 2)
            logger.debug(f'{date}: Cash: {cash}') 
            # Calculate net liquidation value
            net_liq = round(cash + daily_position_value, 2)
            logger.debug(f'{date}: Net Liquidity: {net_liq}')

            # daily drift persists but final seems ok
            drift = abs(net_liq - (initial_capital + cumulative_pnl))
            if drift < 1:
                pass  # No log, or maybe info-level for perfect match
            elif 1 <= drift < 5:
                logger.warning(f'FLOATING ERR DRIFT under $5: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')
            elif 5 <= drift < 10:
                logger.warning(f'FLOATING ERR DRIFT under $10: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')
            else:
                logger.error(f'FLOATING ERR DRIFT above $10: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')

            # Update peak liquidity if net liquidation value is higher
            if net_liq > peak_liquidity:
                peak_liquidity = net_liq
                
            # Calculate drawdown (as positive values)
            # OLD (for negative drawdown): drawdown_amount = - max(0, round(peak_liquidity - net_liq, 2))
            drawdown_amount = max(0, round(peak_liquidity - net_liq, 2))  # Positive dollar amount
            drawdown_pct = round(drawdown_amount / peak_liquidity * 100, 2) if peak_liquidity > 0 else 0  # Positive percentage

            # Calculate ROI metrics
            daily_roi = round(daily_pnl / daily_margin_requirement * 100, 2) if daily_margin_requirement > 0 else 0
            total_roi = round((net_liq - initial_capital) / initial_capital * 100, 2)
            
            # Store daily data with expanded metrics
            daily_data.append({
                'Date': date,
                'Net Liquidity': round(net_liq, 2),
                'BP': round(option_bp, 2),
                'Cash': cash,
                'Position Value': round(daily_position_value, 2),
                'Margin Requirement': round(daily_margin_requirement, 2),
                'Daily P&L': round(daily_pnl, 2),
                'Cumulative P&L': round(cumulative_pnl, 2),
                'Drawdown ($)': round(drawdown_amount, 2),
                'Drawdown (%)': round(drawdown_pct, 2),
                'Daily ROI (%)': daily_roi,
                'Total ROI (%)': total_roi,
                'Active Positions': len(active_trades),
                'Peak Liquidity': round(peak_liquidity, 2),
                'Margin Utilization (%)': round(daily_margin_requirement / initial_capital * 100, 2)
            })
            
            # Log daily summary
            logger.debug(f'Date: {date}')
            logger.debug(f'  Daily P&L: ${daily_pnl:.2f}')
            logger.debug(f'  Cumulative P&L: ${cumulative_pnl:.2f}')
            logger.debug(f'  Daily ROI: {daily_roi:.2f}%')
            logger.debug(f'  Cash: {cash:.2f}')
            logger.debug(f'  Net Liquidity: ${net_liq:.2f}')
            logger.debug(f'  BP: ${option_bp:.2f}')
            logger.debug(f'  Active Positions: {len(active_trades)}')
        
        # Create DataFrame and calculate metrics
        daily_df = pd.DataFrame(daily_data)
        
        return daily_df


    def calculate_daily_value(self, trade: NamedTuple, date: pd.Timestamp, use_underlying_close: bool = True):
        """
        Calculate the daily market value of open positions and margin requirements.
        
        Args:
            trade (pd.Series): Trade result containing trade results.
            date (pd.Timestamp): Date to calculate value for.
            options_chain_multi_index (pd.MultiIndex): MultiIndex DataFrame with option chain data.
            spx_data (pd.DataFrame): DataFrame containing SPX closing prices.
            use_spx_close (bool, optional): Whether to use SPX close price (True) or underlying_last from options data (False).
        
        Returns:
            Market value of the position
        """
        try:
            # Check if the date exists in the MultiIndex
            if date not in self.option_chain_multi_index.index.get_level_values(0):
                # Find the nearest date
                available_dates = self.option_chain_multi_index.index.get_level_values(0)
                nearest_date = available_dates[available_dates <= date][-1]
                # logger.debug(f"Found nearest date {nearest_date} before target date {date}.")
                date = nearest_date
            
            # Get the price data using MultiIndex
            price_data = self.option_chain_multi_index.loc[(date, trade.strike)]
            price_data = price_data.loc[price_data['expire_date']==trade.expire_date]

            # Expiration, so use intrinsic value
            if date == trade.expire_date:
                # Get underlying price based on source preference
                if use_underlying_close:
                    if date not in self.underlying.index:
                        logger.error(f"No underlying closing price available for {date}")
                        return None
                    underlying_price = self.underlying.loc[date, 'close']
                    # logger.debug(f"Using SPX close price: {underlying_price}")
                else:
                    underlying_price = price_data['underlying_last'].iloc[0]
                    # logger.debug(f"Using options chain underlying_last: {underlying_price}")

                close = PriceUtils.calculate_intrinsic_value(underlying_price=underlying_price, strike=trade.strike, option_type=trade.option_type)
                market_value = round(close * 100 * trade.quantity, 2)
                # logger.debug(f'Calculated intrinsic value on date={date} for strike={trade.strike} and value={market_value}')

            # Either MTM daily or early closure, so calculate mid point of bid/ask quote
            else:
                prefix = 'p_' if OptionType.is_put(trade) else "c_"
                bid_col = prefix + 'bid'
                ask_col = prefix + 'ask'
                bid = price_data[bid_col].iloc[0] 
                ask = price_data[ask_col].iloc[0] 
                mid = PriceUtils.calculate_midpoint_price(bid, ask)
                if mid is None:
                    logger.warning(f"Invalid bid/ask prices on {date} for strike {trade.strike}: bid={bid}, ask={ask}")
                    return None
                market_value = round(mid * 100 * trade.quantity, 2)
                # logger.debug(f'Calculated mid value on date={date} for strike={trade.strike}, bid={bid}, ask={ask}, mid={mid}, value={market_value}')
            
            # Validate sign of value according to PositionSide
            try:
                if PositionSide.is_long(trade):
                    assert market_value >= 0 
                else:
                    assert PositionSide.is_short(trade)
                    assert market_value <= 0      
            except AssertionError as e:
                if PositionSide.is_long(trade):
                    market_value = abs(market_value)
                else:
                    market_value = -abs(market_value)

            return market_value 
        
        except KeyError:
            logger.warning(f"No data for strike {trade.strike} on {date}")
        except Exception as e:
            logger.error(f"Error calculating daily value: {str(e)}")

        return None

    def _generate_param_string(self, config):
        """
        Generate a parameter string based on backtest configuration type.
        
        Creates a unique identifier string that captures the key parameters
        of the backtest configuration for use in file naming and result tracking.
        
        Args:
            config (Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]): 
                The configuration object containing strategy parameters
                
        Returns:
            str: A formatted parameter string containing strategy type, 
                 delta/DTE ranges or targets, and date range
                 
        Examples:
            For MultiLeg: "VERTICAL_spread_40:45_2020-01-01:2020-12-31"
            For SingleLeg: "PUT_SHORT_0.65:0.75_30:35_2020-01-01:2020-12-31"
        """

        if isinstance(config, MultiLegOptionStrategyConfig):
            param_str = f"{config.spread_type.value}_spread_{f'{config.legs[0].dte_range[0]}:{config.legs[0].dte_range[1]}' if config.legs[0].dte_range else config.legs[0].dte_target}_{config.start_date}:{config.end_date}"
        elif isinstance(config, FuturesStrategyConfig):
            param_str = f"{config.futures_type.name}_{config.futures_strategy.value}_{config.start_date}:{config.end_date}"
        else:
            param_str = f"{config.leg.option_type.value}_{config.leg.position_side.value}_{f'{config.leg.delta_range[0]}:{config.leg.delta_range[1]}' if config.leg.delta_range else config.leg.delta_target}_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"

        return param_str.upper()

 
    def _save_results(self, results: dict, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig], param_str: str="default"):
        """Save trade results and transactions to a CSV file."""

        trade_results = results['trade_results']
        transactions = results['transactions']
        stats = results['stats']

        # Save trades
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trades_csv_path = os.path.join(self.results_dir, f"trades_{param_str}_{timestamp}.csv")
        trade_results.to_csv(trades_csv_path, index=False)      

        # Save transactions if available
        if transactions is not None and not transactions.empty:
            transactions_csv_path = os.path.join(self.results_dir, f"transactions_{param_str}_{timestamp}.csv")
            transactions.to_csv(transactions_csv_path, index=False)

        # Save the full MTM/drawdown table (not just the text summary below)
        if stats is not None and not stats.empty:
            mtm_csv_path = os.path.join(self.results_dir, f"mtm_{param_str}_{timestamp}.csv")
            stats.to_csv(mtm_csv_path, index=False)

        is_futures = isinstance(config, FuturesStrategyConfig)
        dd_duration_unit = "trading days" if is_futures else "trades"

        stats_csv_path = os.path.join(self.results_dir, f"stats_{param_str}_{timestamp}.csv")

        with open(stats_csv_path, 'w') as results_file:
                # results_file.write("Backtest Results Summary:\n")
                results_file.write(f"Total trades executed: {len(trade_results)}\n")
                results_file.write(f"Winning trades: {(trade_results['pnl'] > 0).sum()}\n")
                results_file.write(f"Win rate: {((trade_results['pnl'] > 0).sum() / len(trade_results)):.2%}\n")
                results_file.write(f"Total raw P&L: ${trade_results['cumulative_pnl'].iloc[-1]:.2f}\n")
                results_file.write(f"Final capital: ${trade_results['capital'].iloc[-1]:.2f}\n")
                results_file.write(f"Return on initial capital: {(trade_results['capital'].iloc[-1] / config.initial_capital - 1):.2%}\n")
                if is_futures:
                    results_file.write(f"Average return on margin (roi) {trade_results['roi'].mean():.2f}%\n")
                else:
                    results_file.write(f"Average return per unit risk {trade_results['ret_per_unit_risk'].mean():.2%}\n")
                    average_return_per_point = trade_results['ret_per_point'].mean() if trade_results['ret_per_point'] is not None and not trade_results['ret_per_point'].empty else 0.0
                    results_file.write(f"Average return per point {average_return_per_point:.2%}\n")
                results_file.write(f"Max Profit {trade_results['pnl'].max():.2f}\n")
                results_file.write(f"Max Loss {trade_results['pnl'].min():.2f}\n")
                results_file.write(f"Average days held: {trade_results['days_held'].mean():.1f}\n")


                # Add execution times
                if hasattr(self, 'execution_times') and self.execution_times:
                    results_file.write("\nExecution Times:\n")
                    for phase, time_taken in self.execution_times.items():
                        results_file.write(f"{phase.replace('_', ' ').title()}: {time_taken:.2f}s\n")

                    total_execution_time = sum(self.execution_times.values())
                    results_file.write(f"Total execution time: {total_execution_time:.2f}s\n")

                if stats is not None and not stats.empty:
                    # Futures drawdown is negative (worst = .min()); the legacy
                    # option-path calculate_simple_drawdown is still positive
                    # (worst = .max()) — see that method's own comment.
                    if is_futures:
                        max_drawdown_amount = stats['Drawdown ($)'].min()
                        max_drawdown_percentage = stats['Drawdown (%)'].min()
                    else:
                        max_drawdown_amount = stats['Drawdown ($)'].max()
                        max_drawdown_percentage = stats['Drawdown (%)'].max()
                    results_file.write(f"Maximum drawdown: ${max_drawdown_amount:.2f} ({max_drawdown_percentage:.2f}%)\n")

                    # Add peak and duration stats
                    if 'drawdown_analysis' in results:
                        dd_analysis = results['drawdown_analysis']
                        results_file.write(f"Peak capital: ${dd_analysis['peak_capital']:.2f}\n")
                        results_file.write(f"Trough capital: ${dd_analysis['trough_capital']:.2f}\n")
                        results_file.write(f"Drawdown duration: {dd_analysis['drawdown_duration']} {dd_duration_unit}\n")

        results['execution_times'] = self.execution_times
        results['total_execution_time'] = round(sum(self.execution_times.values()), 2)
        if self.log_to_sheets:
            from options_bt.utils.gspread_log_util import log_to_google_sheets
            log_to_google_sheets(results, config=config, param_str=param_str)

        # Save MTM results with same timestamp
        # if mtm_df is not None and not mtm_df.empty:
        #     mtm_csv_path = os.path.join(results_dir, f"mtm_{param_str}_{timestamp}.csv")
        #     mtm_df.to_csv(mtm_csv_path, index=False)


    def _log_execution_summary(self, total_time: float) -> None:
        """
        Log a summary of execution times for different parts of the backtest.
        
        Args:
            total_time: Total execution time of the backtest in seconds
        """
        logger.info("\nExecution Time Summary:")
        logger.info("-" * 30)
        
        # Log individual components
        if 'signal_generation' in self.execution_times:
            logger.info(f"Signal Generation: {self.execution_times['signal_generation']:.2f} seconds")
        
        if 'backtest_execution' in self.execution_times:
            logger.info(f"Backtest Execution: {self.execution_times['backtest_execution']:.2f} seconds")
        
        if 'saving' in self.execution_times:
            logger.info(f"Saving Results: {self.execution_times['saving']:.2f} seconds")
        
        # Calculate other time (time not accounted for in tracked components)
        tracked_time = sum(self.execution_times.values())
        other_time = total_time - tracked_time
        
        logger.info(f"Other Operations: {other_time:.2f} seconds")
        logger.info("-" * 30)
        logger.info(f"Total Time: {total_time:.2f} seconds")


    def calculate_simple_drawdown(self, results, config):

            def get_dd_spans(drawdown_arr):
                spans = []
                current_span_start = None
                for i, dd_val in enumerate(drawdown_arr):
                    if dd_val > 0 and current_span_start is None:
                        current_span_start = i
                    elif dd_val <= 0 and current_span_start is not None:
                        spans.append((current_span_start, i))
                        current_span_start = None
                if current_span_start is not None:
                    spans.append((current_span_start, len(drawdown_arr) - 1))
                return spans

            def get_dd_spans_vectorized(drawdown):
                dd_active = drawdown > 0
                n = len(dd_active)
                
                # Start indices: where drawdown goes from False → True
                starts = np.where((~dd_active[:-1]) & dd_active[1:])[0] + 1  # tuple of arrays so [0] to get the array
                if dd_active[0]:
                    starts = np.insert(starts, 0, 0)  # handle drawdown starting at first element

                # End indices: where drawdown goes from True → False
                ends = np.where(dd_active[:-1] & (~dd_active[1:]))[0] + 1
                if dd_active[-1]:
                    ends = np.append(ends, n - 1)  # handle drawdown ending at last element

                spans = list(zip(starts, ends))
                return spans


            trade_results = results['trade_results']

            capital = trade_results['capital'].values
            logger.debug(f'Capital array:\n{capital}')

            capital_with_init = np.insert(capital, 0, config.initial_capital)
            logger.debug(f'Capital with prepended init:\n{capital_with_init}')

            running_max = np.maximum.accumulate(capital_with_init)
            logger.debug(f'Running max:\n{running_max}')

            drawdown = (running_max - capital_with_init)
            logger.debug(f'Drawdown:\n{drawdown}')

            max_drawdown_usd = np.max(drawdown)
            logger.debug(f'Max dd USD: {max_drawdown_usd}')

            trough_idx = np.argmax(drawdown)
            logger.debug(f'Trough index: {trough_idx}')

            if trough_idx > 0:
                peak_idx = np.argmax(capital_with_init[:trough_idx])
            else:
                peak_idx = 0

            logger.debug(f'Peak index: {peak_idx}')

            # spans = get_dd_spans(drawdown)
            spans = get_dd_spans_vectorized(drawdown)
            logger.debug(f'Drawdown spans: {spans}')

            max_dd_duration = max([(j - i) for i, j in spans]) if spans else 0

            logger.info(f"Maximum Drawdown (USD): {max_drawdown_usd:.2f}")
            logger.info(f"Maximum Drawdown (%)): {max_drawdown_usd / capital_with_init[peak_idx]:.2%}")
            logger.info(f"Peak Capital: ${capital_with_init[peak_idx]:.2f}")
            logger.info(f"Trough Capital: ${capital_with_init[trough_idx]:.2f}")
            logger.info(f"Drawdown Duration: {max_dd_duration} trades")

            stats = pd.DataFrame({
                'Drawdown ($)': drawdown,
                'Drawdown (%)': drawdown / running_max * 100,
                'Capital': capital_with_init,
                'Running Max': running_max
            })

            results['stats'] = stats
            results['drawdown_analysis'] = {
                'max_drawdown': max_drawdown_usd,
                'peak_capital': capital_with_init[peak_idx],
                'trough_capital': capital_with_init[trough_idx],
                'drawdown_duration': max_dd_duration
            }

            return results   # ← NECESSARY

    def calculate_futures_mtm_drawdown(self, results: dict, config: FuturesStrategyConfig) -> dict:
        """
        Daily mark-to-market drawdown for futures, built in polars from the
        continuous underlying price series.

        calculate_simple_drawdown only snapshots capital at trade-close
        events, so an intra-trade dip that fully recovers by the time the
        position closes/rolls is invisible — exactly the gap noted after a
        71/91-day single-position ES backtest reported zero drawdown. This
        marks the currently-open position to market every day instead,
        using entry_price/position_side/quantity/contract_multiplier from
        each trade's 'close' transaction record (trade_results no longer
        carries entry/exit price post-_finalize_results' column selection).

        Reports the same fields to the log as calculate_simple_drawdown
        (Maximum Drawdown $/%, Peak/Trough Capital), except Drawdown
        Duration is now in trading days rather than trade count, since the
        underlying series is daily resolution.
        """
        trade_results = results['trade_results']
        transactions = results['transactions']

        if trade_results.empty or transactions.empty:
            return results

        close_tx = transactions[transactions['type'] == 'close'][
            ['trade_id', 'entry_price', 'position_side', 'quantity', 'contract_multiplier']
        ]
        trades = trade_results[['trade_id', 'opened', 'closed', 'capital']].merge(close_tx, on='trade_id', how='left')
        trades = trades.sort_values('opened').reset_index(drop=True)

        trades['capital_before'] = trades['capital'].shift(1)
        trades['capital_before'] = trades['capital_before'].fillna(config.initial_capital)
        trades['direction'] = trades['position_side'].apply(lambda s: 1 if str(s).lower() == 'long' else -1)

        trades_pl = pl.from_pandas(trades).with_columns(
            pl.col('opened').cast(pl.Date),
            pl.col('closed').cast(pl.Date),
        )

        start = pd.Timestamp(config.start_date).date()
        end = pd.Timestamp(config.end_date).date()
        daily = (
            self.underlying
            .filter((pl.col('ts_event') >= start) & (pl.col('ts_event') <= end))
            .select(['ts_event', 'close'])
            .sort('ts_event')
        )

        # Match each day to the most recently opened trade as of that day;
        # is_open tells us whether that trade was still open (vs. already
        # closed, with no newer trade opened yet) on this particular day.
        daily = daily.join_asof(trades_pl, left_on='ts_event', right_on='opened', strategy='backward')
        daily = daily.with_columns(
            is_open=pl.col('closed').is_not_null() & (pl.col('ts_event') < pl.col('closed'))
        )

        daily = daily.with_columns(
            mtm_capital=pl.when(pl.col('is_open'))
            .then(
                pl.col('capital_before') +
                (pl.col('close') - pl.col('entry_price')) * pl.col('quantity') * pl.col('contract_multiplier') * pl.col('direction')
            )
            .when(pl.col('closed').is_not_null())
            .then(pl.col('capital'))  # already closed as of this day -> flat at realized capital
            .otherwise(pl.lit(config.initial_capital))  # before the first trade opened
        )

        # Day-over-day MTM PnL — mathematically identical to (close - entry_price)
        # for a single continuous holding period (the entry_price term cancels
        # telescoping day to day), but reported this way to match how a real
        # daily variation-margin settlement (e.g. IB's MTM report) is shown:
        # today's settlement price and today's P&L versus yesterday, not
        # "P&L since entry" recomputed fresh each row.
        daily = daily.with_columns(mtm_pnl=pl.col('mtm_capital').diff().fill_null(0.0))

        daily = daily.with_columns(running_max=pl.col('mtm_capital').cum_max())
        daily = daily.with_columns(drawdown_usd=pl.col('mtm_capital') - pl.col('running_max'))
        daily = daily.with_columns(
            drawdown_pct=pl.when(pl.col('running_max') > 0)
            .then(pl.col('drawdown_usd') / pl.col('running_max') * 100)
            .otherwise(0.0)
        )

        daily = daily.with_columns(
            pl.col('close', 'mtm_pnl', 'mtm_capital', 'running_max', 'drawdown_usd', 'drawdown_pct').round(2)
        )

        max_dd_row = daily.sort('drawdown_usd', descending=False).head(1)
        max_drawdown_usd = max_dd_row['drawdown_usd'][0]
        max_drawdown_pct = max_dd_row['drawdown_pct'][0]
        trough_capital = max_dd_row['mtm_capital'][0]
        peak_capital = max_dd_row['running_max'][0]

        # Drawdown duration in trading days: longest consecutive run with drawdown_usd < 0
        dd_active = (daily['drawdown_usd'] < 0).to_numpy()
        max_dd_duration = 0
        current_run = 0
        for active in dd_active:
            if active:
                current_run += 1
                max_dd_duration = max(max_dd_duration, current_run)
            else:
                current_run = 0

        logger.info(f"Maximum Drawdown (USD): {max_drawdown_usd:.2f}")
        logger.info(f"Maximum Drawdown (%)): {max_drawdown_pct:.2f}%")
        logger.info(f"Peak Capital: ${peak_capital:.2f}")
        logger.info(f"Trough Capital: ${trough_capital:.2f}")
        logger.info(f"Drawdown Duration: {max_dd_duration} trading days")

        stats = daily.select(['ts_event', 'close', 'mtm_pnl', 'mtm_capital', 'running_max', 'drawdown_usd', 'drawdown_pct']).rename({
            'close': 'Close',
            'mtm_pnl': 'MTM PnL',
            'mtm_capital': 'Capital',
            'running_max': 'Running Max',
            'drawdown_usd': 'Drawdown ($)',
            'drawdown_pct': 'Drawdown (%)',
        }).to_pandas()

        results['stats'] = stats
        results['drawdown_analysis'] = {
            'max_drawdown': max_drawdown_usd,
            'peak_capital': peak_capital,
            'trough_capital': trough_capital,
            'drawdown_duration': max_dd_duration,
        }

        return results




    # def calculate_simple_drawdown(self, results, config):

    #     def get_dd_spans(drawdown_arr):
    #         spans = []
    #         current_span_start = None
    #         for i, dd_val in enumerate(drawdown_arr):
    #             if dd_val > 0 and current_span_start is None: # Drawdown starts
    #                 current_span_start = i
    #             elif dd_val <= 0 and current_span_start is not None: # Drawdown ends
    #                 spans.append((current_span_start, i))
    #                 current_span_start = None
    #         if current_span_start is not None: # Handle drawdown extending to the end of the data
    #             spans.append((current_span_start, len(drawdown_arr) -1))
    #         return spans

    #     trade_results = results['trade_results']
        
    #     # Calculate running capital and drawdown
    #     capital = trade_results['capital'].values
    #     # Prepend initial capital for accurate drawdown
    #     logger.debug(f'Capital array:\n{capital}')
    #     capital_with_init = np.insert(capital, 0, config.initial_capital)
    #     logger.debug(f'Capital with prepended init:\n {capital_with_init}')

    #     # Drawdown as positive percentage: (peak - current) / peak
    #     # OLD (for negative drawdown): drawdown = (capital - running_max) / running_max
    #     running_max = np.maximum.accumulate(capital_with_init)
    #     logger.debug(f'Running max:\n {running_max}')

    #     drawdown = (running_max - capital_with_init) # / running_max
    #     logger.debug(f'Drawdown:\n {drawdown}')

    #     # Find peak-to-trough drawdown periods
    #     # OLD (for negative drawdown): max_drawdown = np.min(drawdown)
    #     max_drawdown_usd = np.max(drawdown) # Now using max since drawdown is positive
    #     logger.debug(f'Max dd USD: {max_drawdown_usd}')

    #     trough_idx = np.argmax(drawdown) 
    #     logger.debug(f'Trough index: {trough_idx}')

    #     # peak_idx = np.argmin(capital[:trough_idx])
    #     # Find the peak before the max drawdown 
    #     # Old calc only searches for the deepest dd and uses this duration
    #     if trough_idx > 0:
    #         peak_idx = np.argmax(capital_with_init[:trough_idx])
    #     else:
    #         peak_idx = 0  # If max drawdown is at the first trade, peak is also the first trade
        
    #     spans = get_dd_spans(drawdown)
    #     logger.debug(f'Drawdown spans: {spans}')

    #     max_dd_duration = max([(j - i) for i,j in spans]) if spans else 0

    #     logger.info(f"Maximum Drawdown USD: {max_drawdown_usd:.2f}")
    #     logger.info(f"Peak Capital: ${capital_with_init[peak_idx]:.2f}")
        # logger.info(f"Trough Capital: ${capital_with_init[trough_idx]:.2f}")
        # logger.info(f"Drawdown Duration: {max_dd_duration} trades")
        
        # Create stats DataFrame (similar to your existing format)
        # OLD (for negative drawdown):
        #   'Drawdown ($)': (capital - running_max),  # Negative dollar amount
        #   'Drawdown (%)': drawdown * 100,  # Negative percentage
        # stats = pd.DataFrame({
        #     'Drawdown ($)': (drawdown),  # Positive dollar amount
        #     'Drawdown (%)': drawdown / running_max * 100,  # Positive percentage
        #     'Capital': capital_with_init,
        #     'Running Max': running_max
        # })
        
        # Add drawdown to results
        # results['stats'] = stats
        # results['drawdown_analysis'] = {
        #     'max_drawdown': max_drawdown_usd,
        #     'peak_capital': capital_with_init[peak_idx],
        #     'trough_capital': capital_with_init[trough_idx],
        #     'drawdown_duration': max_dd_duration
        # }
        
        # return results

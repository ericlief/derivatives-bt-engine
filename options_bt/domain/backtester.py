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

            # Bound the price history passed to TradeManager to the backtest's
            # own date range: a forced close at backtest end uses this
            # series' max date as the closing price/date, and self.underlying
            # is the full multi-year continuous series, not the config's
            # window — without this filter, a position still open at
            # config.end_date would force-close years later than requested.
            backtest_start_ts = pd.Timestamp(config.start_date).date()
            backtest_end_ts = pd.Timestamp(config.end_date).date()
            bounded_underlying = self.underlying.filter(
                (pl.col('ts_event') >= backtest_start_ts) & (pl.col('ts_event') <= backtest_end_ts)
            )

            backtest_start = time.time()
            results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(
                valid_signals,
                option_chain=self.option_chain,
                underlying_price_history=bounded_underlying,
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
                if config.spread_type == OptionSpreadType.IRON_CONDOR:
                    # Iron condor signals carry put_width/call_width (no generic
                    # leg1_strike/leg2_strike columns) -- risk is bounded by
                    # whichever wing is wider, matching
                    # MultiLegOptionPosition.max_risk's convention.
                    signals['spread_width'] = signals[['put_width', 'call_width']].max(axis=1)
                else:
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
                    if config.option_strategy in [OptionStrategy.BULL_PUT_CREDIT_SPREAD, OptionStrategy.BEAR_CALL_CREDIT_SPREAD, OptionStrategy.IRON_CONDOR]:
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
        trade_results['ret'] = round(trade_results['pnl'] / trade_results['capital'].shift(1), 2)

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

        print(results['trade_results'].to_string())

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
                        max_drawdown_amount = stats['dd_usd'].min()
                        max_drawdown_percentage = stats['dd_pct'].min()
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
            """Peak-to-trough drawdown over the trade-by-trade capital curve,
            computed in polars. trade_results['capital'] is pandas (matches
            _finalize_results' pandas boundary); results['stats'] is
            converted back to pandas at the end since _save_results' CSV
            writing still expects pandas."""

            trade_results = results['trade_results']

            capital = pl.Series('capital', trade_results['capital'].to_numpy(), dtype=pl.Float64)
            capital_with_init = pl.concat([pl.Series('capital', [float(config.initial_capital)], dtype=pl.Float64), capital])
            logger.debug(f'Capital with prepended init:\n{capital_with_init}')

            running_max = capital_with_init.cum_max()
            logger.debug(f'Running max:\n{running_max}')

            drawdown = running_max - capital_with_init
            logger.debug(f'Drawdown:\n{drawdown}')

            max_dd_usd = drawdown.max()
            logger.debug(f'Max dd USD: {max_dd_usd}')

            trough_idx = drawdown.arg_max()
            logger.debug(f'Trough index: {trough_idx}')

            if trough_idx > 0:
                peak_idx = capital_with_init.slice(0, trough_idx).arg_max()
            else:
                peak_idx = 0

            logger.debug(f'Peak index: {peak_idx}')

            # Drawdown spans: contiguous runs where drawdown > 0, via
            # shift-based edge detection (True->False / False->True transitions).
            dd_active = (drawdown > 0).to_list()
            spans = []
            span_start = None
            for i, active in enumerate(dd_active):
                if active and span_start is None:
                    span_start = i
                elif not active and span_start is not None:
                    spans.append((span_start, i))
                    span_start = None
            if span_start is not None:
                spans.append((span_start, len(dd_active) - 1))
            logger.debug(f'Drawdown spans: {spans}')

            max_dd_duration = max((j - i) for i, j in spans) if spans else 0

            logger.info(f"Maximum Drawdown (USD): {max_dd_usd:.2f}")
            logger.info(f"Maximum Drawdown (%)): {max_dd_usd / capital_with_init[peak_idx]:.2%}")
            logger.info(f"Peak Capital: ${capital_with_init[peak_idx]:.2f}")
            logger.info(f"Trough Capital: ${capital_with_init[trough_idx]:.2f}")
            logger.info(f"Drawdown Duration: {max_dd_duration} trades")

            stats = pl.DataFrame({
                'Drawdown ($)': drawdown,
                'Drawdown (%)': drawdown / running_max * 100,
                'Capital': capital_with_init,
                'Running Max': running_max,
            })

            results['stats'] = stats.to_pandas()
            results['drawdown_analysis'] = {
                'max_drawdown': max_dd_usd,
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
        using entry_price/position_side/quantity/mult from
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
            ['trade_id', 'open', 'position_side', 'quantity', 'mult']
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
                (pl.col('close') - pl.col('open')) * pl.col('quantity') * pl.col('mult') * pl.col('direction')
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
        #
        # .diff() is null only for the very first row of the whole backtest
        # (no prior row to diff against) — fill that with
        # (mtm_capital - initial_capital), not a blind 0.0. Under
        # fill_price='close' those are the same thing (entry_price IS that
        # day's close, so day-1 PnL really is 0), but under fill_price='mid'
        # entry_price != that day's close mark, so day-1 PnL is genuinely
        # nonzero and a hardcoded 0.0 silently dropped it (cum_pnl already
        # showed the correct nonzero value, just not mtm_pnl).
        daily = daily.with_columns(
            mtm_pnl=pl.col('mtm_capital').diff().fill_null(pl.col('mtm_capital') - config.initial_capital)
        )

        # Cumulative PnL since backtest start (realized + unrealized as of
        # that day) — the daily-resolution equivalent of trade_results'
        # per-trade cumulative_pnl.
        daily = daily.with_columns(cum_pnl=pl.col('mtm_capital') - config.initial_capital)
        daily = daily.with_columns(cum_pnl_pct=pl.col('cum_pnl') / config.initial_capital * 100)

        daily = daily.with_columns(running_max=pl.col('mtm_capital').cum_max())
        daily = daily.with_columns(dd_usd=pl.col('mtm_capital') - pl.col('running_max'))
        daily = daily.with_columns(
            dd_pct=pl.when(pl.col('running_max') > 0)
            .then(pl.col('dd_usd') / pl.col('running_max') * 100)
            .otherwise(0.0)
        )

        daily = daily.with_columns(
            pl.col('close', 'mtm_pnl', 'cum_pnl', 'cum_pnl_pct', 'mtm_capital', 'running_max', 'dd_usd', 'dd_pct').round(2)
        )

        max_dd_row = daily.sort('dd_usd', descending=False).head(1)
        max_dd_usd = max_dd_row['dd_usd'][0]
        max_dd_pct = max_dd_row['dd_pct'][0]
        trough_capital = max_dd_row['mtm_capital'][0]
        peak_capital = max_dd_row['running_max'][0]

        # Drawdown duration in trading days: longest consecutive run with dd_usd < 0
        dd_active = (daily['dd_usd'] < 0).to_numpy()
        max_dd_duration = 0
        current_run = 0
        for active in dd_active:
            if active:
                current_run += 1
                max_dd_duration = max(max_dd_duration, current_run)
            else:
                current_run = 0

        logger.info(f"Maximum Drawdown (USD): {max_dd_usd:.2f}")
        logger.info(f"Maximum Drawdown (%)): {max_dd_pct:.2f}%")
        logger.info(f"Peak Capital: ${peak_capital:.2f}")
        logger.info(f"Trough Capital: ${trough_capital:.2f}")
        logger.info(f"Drawdown Duration: {max_dd_duration} trading days")

        # Whole-strategy summary: total PnL/return from trade_results (same
        # numbers as the per-trade table's last row), Sharpe from the daily
        # mtm_capital series (more accurate than a trade-to-trade Sharpe —
        # standard daily-return annualization via sqrt(252)), avg ROI/trade.
        total_pnl = trade_results['cumulative_pnl'].iloc[-1]
        total_return_pct = (trade_results['capital'].iloc[-1] / config.initial_capital - 1) * 100
        avg_roi = trade_results['roi'].mean()

        daily_ret = daily.with_columns(
            daily_ret=pl.col('mtm_capital') / pl.col('mtm_capital').shift(1) - 1
        )['daily_ret'].drop_nulls()
        sharpe = (
            (daily_ret.mean() / daily_ret.std() * (252 ** 0.5))
            if daily_ret.std() and daily_ret.std() > 0 else None
        )

        logger.info(f"Total PnL: ${round(total_pnl, 2):.2f}")
        logger.info(f"Total Return: {round(total_return_pct, 2):.2f}%")
        logger.info(f"Sharpe Ratio: {round(sharpe, 2) if sharpe is not None else 'N/A'}")
        logger.info(f"Average ROI per trade: {round(avg_roi, 2):.2f}%")

        # Lowercase snake_case column names throughout, matching the
        # convention used for Google Sheets headers elsewhere (e.g.
        # gspread_log_util.py's "total_pnl", "max_dd_usd", "peak_capital").
        stats = daily.select(['ts_event', 'close', 'mtm_pnl', 'cum_pnl', 'cum_pnl_pct', 'mtm_capital', 'running_max', 'dd_usd', 'dd_pct']).rename({
            'ts_event': 'date',
            'mtm_capital': 'capital',
        }).to_pandas()

        results['stats'] = stats
        results['drawdown_analysis'] = {
            'max_drawdown': max_dd_usd,
            'peak_capital': peak_capital,
            'trough_capital': trough_capital,
            'drawdown_duration': max_dd_duration,
        }

        return results



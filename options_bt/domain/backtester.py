import sys
from typing import Dict, List, NamedTuple, Optional, Tuple, Union
import numpy as np
import logging
import time
from datetime import date, datetime, timedelta
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
from options_bt.domain.tsmom_signal import calculate_trend_strength, classify_regime
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
        self.results: Dict[str, pl.DataFrame] = {}
        self.__post_init__()

    @staticmethod
    def _not_empty(df) -> bool:
        return df.height > 0

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

        # Both the futures and option signal/margin paths are polars-native
        # end to end now.
        is_futures = isinstance(config, FuturesStrategyConfig)

        # Initialize trade manager
        trade_manager = TradeManager(config=config, vix=self.vix)
        if is_futures:
            signal_generator = FuturesSignalGenerator(config=config, underlying=self.underlying)
        else:
            signal_generator = OptionSignalGenerator(option_chain=self.option_chain, underlying=self.underlying, config=config)
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

        if signals.height == 0:
            logger.warning("No valid signals generated")
            return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

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
                return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

            # Bound the price history passed to TradeManager to the backtest's
            # own date range: a forced close at backtest end uses this
            # series' max date as the closing price/date, and self.underlying
            # is the full multi-year continuous series, not the config's
            # window — without this filter, a position still open at
            # config.end_date would force-close years later than requested.
            backtest_start_ts = date.fromisoformat(config.start_date)
            backtest_end_ts = date.fromisoformat(config.end_date)
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
            if trade_results.height == 0:
                logger.warning("No trades were executed successfully")
                return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

            return self._finalize_results(trade_results, transactions, config, start_time)

        # Pre-calculate margin requirements for all signals
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)

        # Handle all spread types
        if is_spread:
            # Calculate margins per spread group and ensure proper alignment
            logger.info(f"Calculating margin requirements for multileg trade signals for {config.quantity} | {config.option_strategy} | {config.spread_type}")
            if 'spread_width' not in signals.columns:
                logger.warning(f"Spread width not found in multileg signals. Calculating.")
                if config.spread_type == OptionSpreadType.IRON_CONDOR:
                    # Iron condor signals carry put_width/call_width (no generic
                    # leg1_strike/leg2_strike columns) -- risk is bounded by
                    # whichever wing is wider, matching
                    # MultiLegOptionPosition.max_risk's convention.
                    signals = signals.with_columns(pl.max_horizontal('put_width', 'call_width').alias('spread_width'))
                else:
                    signals = signals.with_columns((pl.col('leg1_strike') - pl.col('leg2_strike')).abs().alias('spread_width'))

            # Filter out trades with excessive spread width if max_spread_width is set
            if config.max_spread_width is not None:
                original_count = signals.height
                logger.debug(f'Number of signals before filtering {original_count}')
                signals = signals.filter(pl.col('spread_width') <= config.max_spread_width)
                filtered_count = original_count - signals.height
                if filtered_count > 0:
                    logger.warning(f"Filtered out {filtered_count} trades due to excessive spread width (> {config.max_spread_width} points)")
                logger.info(f"Maximum spread width in trades: {signals['spread_width'].max() if signals.height > 0 else 'N/A'} points")

            # Filter out trades with excessive max trade loss if max_trade_loss is set
            if 'margin_required' not in signals.columns:
                if signals.height == 0:
                    logger.warning("No signals to filter for max_trade_loss after previous filters.")
                else:
                    logger.info('Calculating margin requirements for multileg position')
                    original_count = signals.height
                    if config.option_strategy in [OptionStrategy.BULL_PUT_CREDIT_SPREAD, OptionStrategy.BEAR_CALL_CREDIT_SPREAD, OptionStrategy.IRON_CONDOR]:
                        # For credit spreads: max loss = (spread_width - credit) * 100 * qty
                        signals = signals.with_columns(
                            ((pl.col('spread_width') - pl.col('spread_price').clip(lower_bound=0)) * config.quantity * config.multiplier).alias('margin_required')
                        )
                    elif config.option_strategy in [OptionStrategy.BULL_CALL_DEBIT_SPREAD, OptionStrategy.BEAR_PUT_DEBIT_SPREAD]:
                        signals = signals.with_columns(
                            (pl.col('spread_price').abs() * config.quantity * config.multiplier).alias('margin_required')
                        )
                    else:
                        signals = signals.with_columns(
                            (pl.col('spread_width') * config.quantity * config.multiplier).alias('margin_required')  # fallback
                        )

                    if config.max_trade_loss is not None:
                        signals = signals.filter(pl.col('margin_required') <= config.max_trade_loss)
                        filtered_count = original_count - signals.height
                        if filtered_count > 0:
                            logger.warning(f"Filtered out {filtered_count} trades due to max allowed trade loss (${config.max_trade_loss})")
                        logger.info(f"Maximum trade loss: ${signals['margin_required'].max() if signals.height > 0 else 'N/A'}")


            if config.premium_ratio is not None:
                original_count = signals.height
                signals = signals.with_columns(
                    (pl.col('spread_price').clip(lower_bound=0) / pl.col('spread_width')).round(2).alias('premium_ratio')
                )
                signals = signals.filter(pl.col('premium_ratio') >= config.premium_ratio)
                filtered_count = original_count - signals.height
                if filtered_count > 0:
                    logger.warning(f"Filtered out {filtered_count} trades due to premium ratio ({config.premium_ratio})")
                logger.info(f"Minimum: {signals['premium_ratio'].min() if signals.height > 0 else 'N/A'}")
                logger.info(f"Maximum: {signals['premium_ratio'].max() if signals.height > 0 else 'N/A'}")

            logger.debug(f'First few (filtered) signals: {signals.head()}')

        # Single leg
        elif isinstance(config, SingleLegOptionStrategyConfig):
            logger.info(f"Calculating margin requirements for single leg trade signals for {config.quantity} | {config.option_strategy} | {config.leg.option_type} | {config.leg.delta_target if config.leg.delta_target else config.leg.delta_range}")
            signals = signals.with_columns(
                pl.struct(['midpoint_price', 'strike', 'underlying_last']).map_elements(
                    lambda row: SingleLegOptionPosition.calculate_margin(
                        quantity=config.quantity,
                        option_type=config.leg.option_type,
                        position_side=config.leg.position_side,
                        entry_price=row['midpoint_price'],
                        strike=row['strike'],
                        underlying_price=row['underlying_last'],
                        leverage=config.leverage
                    ),
                    return_dtype=pl.Float64,
                ).alias('margin_required')
            )

        # Filter out trades that would exceed margin limits
        valid_signals = signals.filter(pl.col('margin_required') <= max_allowed_margin)
        self.execution_times['signal_generation'] = time.time() - signal_start

        filtered_count = signals.height - valid_signals.height
        if filtered_count > 0:
            logger.warning(f"Filtered out {filtered_count} trades due to margin requirements")
        logger.info(f"Average margin requirement for trades: ${valid_signals['margin_required'].mean():.2f}")
        logger.info(f"Maximum margin requirement for trades: ${valid_signals['margin_required'].max():.2f}")
        logger.info(f"Total valid signals: {valid_signals.height}")
        logger.debug(valid_signals)
        # if config.trade_selection_method == TradeSelectionMethod.MARGIN_FIRST:
        #     valid_signals = valid_signals.sort(['margin_required', 'delta_difference'])

        logger.info("Minimum margin sample:")
        logger.info(valid_signals.sort('margin_required').head())
        logger.info("Maximum margin sample:")
        logger.info(valid_signals.sort('margin_required').tail())

        logger.info(f"Total signals: {valid_signals.height} | Date Range: {valid_signals['date'].min()} to {valid_signals['date'].max()}")
        if valid_signals.height == 0:
            logger.info("No valid signals; skipping trade execution.")
            return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

        # Execute trades
        backtest_start = time.time()
        results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(valid_signals,
                                                                                option_chain=self.option_chain,
                                                                                underlying_price_history=self.underlying)
        self.execution_times['backtest_execution'] = time.time() - backtest_start
        trade_results = results_transactions_dict['trade_results']
        transactions = results_transactions_dict['transactions']
        if trade_results.height == 0:
            logger.warning("No trades were executed successfully")
            return {'trade_results': pl.DataFrame(), 'transactions': pl.DataFrame()}

        return self._finalize_results(trade_results, transactions, config, start_time)

    def _finalize_results(self, trade_results: pl.DataFrame, transactions: pl.DataFrame,
                          config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig, FuturesStrategyConfig],
                          start_time: float) -> dict:
        """
        Shared post-processing for both the option and futures paths:
        cumulative PnL/capital tracking, Sharpe, column ordering, drawdown,
        and saving.
        """
        # Calculate cumulative metrics based on PnL
        # Insert a new row at the start with the initial capital (pre-trade).
        # trade_results has many more columns than this row supplies (e.g.
        # option_strategy, opened, closed, ...) -- how='diagonal_relaxed'
        # fills those with null for this one row, matching pandas' pd.concat
        # NaN-fill-on-missing-column behavior.
        init_row = pl.DataFrame([{
            'pnl': 0.0,
            'cumulative_pnl': 0.0,
            'capital': float(config.initial_capital),
            'trade_id': 0,
        }])

        trade_results = pl.concat([init_row, trade_results], how='diagonal_relaxed')

        if 'trade_id' in trade_results.columns:
            trade_results = trade_results.with_columns(pl.col('trade_id').cast(pl.Int64))
        trade_results = trade_results.with_columns(pl.col('pnl').cum_sum().round(2).alias('cumulative_pnl'))
        trade_results = trade_results.with_columns((pl.lit(float(config.initial_capital)) + pl.col('cumulative_pnl')).round(2).alias('capital'))  # Track actual capital based on cumulative PnL
        trade_results = trade_results.with_columns(pl.col('capital').cum_max().round(2).alias('peak_capital'))
        trade_results = trade_results.with_columns((pl.col('pnl') / pl.col('capital').shift(1)).round(2).alias('ret'))

        # Remove init row
        trade_results = trade_results.slice(1)

        # Calculate margin utilization
        trade_results = trade_results.with_columns((pl.col('capital_used') / config.initial_capital).round(4).alias('margin_utilization'))

        # Trade-to-trade Sharpe: NOT a calendar-time Sharpe -- it treats each
        # closed trade as one "period" and annualizes by average trade
        # duration, implicitly assuming trades are evenly spaced and capital
        # is continuously at risk between them. See calculate_options_mtm_drawdown
        # (options) / calculate_futures_mtm_drawdown (futures) below for the
        # daily-return Sharpe that doesn't make that assumption.
        sharpe = None
        if trade_results.height > 1:
            avg_trade_days = trade_results['days_held'].mean()  # Average days per trade
            if avg_trade_days:
                annualization_factor = np.sqrt(252 / avg_trade_days)
                capital_vals = trade_results['capital'].to_numpy()
                returns = np.diff(capital_vals) / capital_vals[:-1]
                if len(returns) > 0 and np.std(returns) > 0:
                    sharpe = np.mean(returns) / np.std(returns) * annualization_factor
                    logger.info(f"Trade-to-trade Sharpe Ratio: {sharpe:.2f}")

        # Save results if requested
        # Generate parameter string based on backtest type
        param_str = self._generate_param_string(config)

        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        self.execution_times['total'] = round(total_time, 2)

        # Order columns (futures trade results carry futures_strategy,
        # options carry option_strategy/premium; both now carry roi)
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
                'roi',
            ]

        trade_results = trade_results.select([c for c in ordered_cols if c in trade_results.columns])

        results = {
            'trade_results': trade_results,
            'transactions': transactions,
            'sharpe_trade_to_trade': sharpe,
        }

        print(results['trade_results'])

        if results['trade_results'].height > 0:
            if isinstance(config, FuturesStrategyConfig):
                results = self.calculate_futures_mtm_drawdown(results, config)
            else:
                results = self.calculate_simple_drawdown(results, config)
                results = self.calculate_options_mtm_drawdown(results, config)

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
        if trade_results.height > 0:
            final_capital = trade_results['capital'][-1]
            final_bp = trade_results['bp'][-1]
            assert abs(final_capital - final_bp) < 1e-6, f'Final capital: {final_capital} | BP: {final_bp}'


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
        trade_results.write_csv(trades_csv_path)

        # Save transactions if available
        if transactions is not None and transactions.height > 0:
            transactions_csv_path = os.path.join(self.results_dir, f"transactions_{param_str}_{timestamp}.csv")
            transactions.write_csv(transactions_csv_path)

        # Save the full MTM/drawdown table (not just the text summary below)
        if stats is not None and stats.height > 0:
            mtm_csv_path = os.path.join(self.results_dir, f"mtm_{param_str}_{timestamp}.csv")
            stats.write_csv(mtm_csv_path)

        is_futures = isinstance(config, FuturesStrategyConfig)
        dd_duration_unit = "trading days" if is_futures else "trades"

        stats_csv_path = os.path.join(self.results_dir, f"stats_{param_str}_{timestamp}.csv")
        n_trades = trade_results.height
        win_count = (trade_results['pnl'] > 0).sum()

        with open(stats_csv_path, 'w') as results_file:
                # results_file.write("Backtest Results Summary:\n")
                results_file.write(f"Total trades executed: {n_trades}\n")
                results_file.write(f"Winning trades: {win_count}\n")
                results_file.write(f"Win rate: {(win_count / n_trades):.2%}\n")
                results_file.write(f"Total raw P&L: ${trade_results['cumulative_pnl'][-1]:.2f}\n")
                results_file.write(f"Final capital: ${trade_results['capital'][-1]:.2f}\n")
                results_file.write(f"Return on initial capital: {(trade_results['capital'][-1] / config.initial_capital - 1):.2%}\n")
                if 'roi' in trade_results.columns:
                    results_file.write(f"Average ROI per trade: {trade_results['roi'].mean():.2f}%\n")
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

                if stats is not None and stats.height > 0:
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
            """Peak-to-trough drawdown over the trade-by-trade capital curve, computed in polars."""

            trade_results = results['trade_results']

            capital = trade_results['capital'].cast(pl.Float64)
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

            results['stats'] = stats
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

        if trade_results.height == 0 or transactions.height == 0:
            return results

        close_tx = transactions.filter(pl.col('type') == 'close').select(
            ['trade_id', 'open', 'position_side', 'quantity', 'mult']
        )
        trades_pl = trade_results.select(['trade_id', 'opened', 'closed', 'capital']).join(close_tx, on='trade_id', how='left')
        trades_pl = trades_pl.sort('opened')

        trades_pl = trades_pl.with_columns(pl.col('capital').shift(1).fill_null(config.initial_capital).alias('capital_before'))
        trades_pl = trades_pl.with_columns(
            pl.when(pl.col('position_side').str.to_lowercase() == 'long').then(1).otherwise(-1).alias('direction')
        )

        start = date.fromisoformat(config.start_date)
        end = date.fromisoformat(config.end_date)
        # Signal/vol overlay -- hv3m, avg_r3m/avg_r1y ("mean"), ts3m/ts1y
        # (fast/slow), signal (weighted trend-strength score), regime
        # (Bull/Bear/Correction/Rebound), sharpe3m, and vix_close --
        # computed on the FULL underlying series before windowing to
        # [start, end]: trimming first would starve the 63/252-day rolling
        # windows of real prior history and show spurious nulls at the
        # start of the window, unlike what the strategy's own signal
        # actually saw at each date. Reuses tsmom_signal.py's canonical
        # calculate_trend_strength/classify_regime (same functions the
        # live TSMOM signal uses) rather than reimplementing rolling stats.
        #
        # Suffixes (3m, 1y, ...) denote the rolling estimation window,
        # not the reporting horizon. Volatility, Sharpe, and avg_r3m/
        # avg_r1y all remain annualized -- hv3m is annualized vol
        # estimated from the last 63d, avg_r3m is annualized mean return
        # estimated from the last 63d, sharpe3m is annualized Sharpe
        # estimated from the last 63d.
        overlay = calculate_trend_strength(self.underlying.select(['ts_event', 'close']).sort('ts_event'))
        overlay = overlay.with_columns(
            hv3m=(pl.col('daily_std') * (252 ** 0.5)).round(4),
            regime=pl.struct(['ts3m', 'ts1y']).map_elements(
                lambda s: classify_regime(s['ts3m'], s['ts1y']).value,
                return_dtype=pl.Utf8,
            ),
            **{c: pl.col(c).round(4) for c in ('ts3m', 'ts1y', 'signal', 'avg_r3m', 'avg_r1y')},
        )
        overlay = overlay.with_columns(
            # Rolling Sharpe on the same 3m (63-day) window as hv3m/ts3m/
            # regime, not whole-to-date -- keeps it on the same clock as
            # regime so a regime flip and a Sharpe/vol move are comparable
            # at a glance instead of drifting at different speeds. avg_r3m
            # is already annualized (see calculate_trend_strength), so no
            # extra *252 here -- both numerator and denominator are
            # annualized already.
            sharpe3m=pl.when(pl.col('hv3m') > 0)
            .then(pl.col('avg_r3m') / pl.col('hv3m'))
            .otherwise(None)
            .round(4)
        )

        if self.vix.height > 0 and 'ts_event' in self.vix.columns:
            vix_daily = self.vix.select(['ts_event', 'close']).rename({'close': 'vix_close'}).sort('ts_event')
            overlay = overlay.join(vix_daily, on='ts_event', how='left')
        else:
            overlay = overlay.with_columns(vix_close=pl.lit(None, dtype=pl.Float64))

        daily = overlay.filter((pl.col('ts_event') >= start) & (pl.col('ts_event') <= end))

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
        total_pnl = trade_results['cumulative_pnl'][-1]
        total_return_pct = (trade_results['capital'][-1] / config.initial_capital - 1) * 100
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
        stats = daily.select([
            'ts_event', 'close', 'mtm_pnl', 'cum_pnl', 'cum_pnl_pct', 'mtm_capital',
            'running_max', 'dd_usd', 'dd_pct', 'hv3m', 'sharpe3m',
            'avg_r3m', 'avg_r1y', 'ts3m', 'ts1y', 'signal', 'regime', 'vix_close',
        ]).rename({
            'ts_event': 'date',
            'mtm_capital': 'capital',
        })

        results['stats'] = stats
        results['drawdown_analysis'] = {
            'max_drawdown': max_dd_usd,
            'peak_capital': peak_capital,
            'trough_capital': trough_capital,
            'drawdown_duration': max_dd_duration,
        }

        return results

    def calculate_options_mtm_drawdown(self, results: dict,
                                       config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]) -> dict:
        """
        Daily mark-to-market Sharpe/drawdown for options, built in polars —
        the options analogue of calculate_futures_mtm_drawdown, added
        because trade-to-trade Sharpe (computed above in _finalize_results)
        implicitly assumes trades are evenly spaced and capital is
        continuously at risk, which understates real day-to-day volatility
        whenever the strategy sits in cash between trades. This method adds
        results['mtm_sharpe']/['daily_mtm'] alongside (not replacing) the
        existing trade-by-trade drawdown from calculate_simple_drawdown.

        Unlike futures (a single continuous instrument, marked via one
        join_asof against its own price series), an option spread can have
        several legs at different strikes/expirations, so each leg is
        marked to market separately against the option chain by
        (date, strike, expire_date) and summed per spread per day — no
        Python per-day loop, and no MultiIndex (this is exactly the
        approach the option_chain_multi_index removal commit recommended
        instead of resurrecting that structure). Per-leg contract terms
        (strike/expire_date/option_type/position_side/quantity/multiplier)
        come from 'open' transactions (BTO/STO rows) — trade_results is
        spread-level only and carries neither.
        """
        trade_results = results['trade_results']
        transactions = results['transactions']

        if trade_results.height == 0 or transactions.height == 0:
            return results

        leg_opens = transactions.filter(pl.col('type').is_in(['BTO', 'STO'])).select(
            ['trade_id', 'strike', 'expire_date', 'option_type', 'position_side', 'quantity', 'multiplier', 'price']
        )
        if leg_opens.height == 0:
            return results

        trades_meta = trade_results.select(['trade_id', 'opened', 'closed', 'capital'])
        legs = leg_opens.join(trades_meta, on='trade_id', how='inner')

        # One row per (leg, day it was open), day range [opened, closed) --
        # matches calculate_futures_mtm_drawdown's is_open convention (the
        # close day itself is already flat at realized capital, handled by
        # the join_asof branch below). Trades opened/closed same-day have no
        # is_open days at all -- their entire life is realized capital.
        legs = legs.filter(pl.col('opened') < pl.col('closed'))
        if legs.height == 0:
            return results

        legs = legs.with_row_index('leg_id').with_columns(
            pl.date_ranges(pl.col('opened'), pl.col('closed') - timedelta(days=1), interval='1d').alias('date')
        ).explode('date')

        # Polars expressions can't pick a column name per-row (p_bid vs
        # c_bid) -- split by option_type, join each half against its own
        # bid/ask pair aliased to a shared name, then recombine.
        option_chain = self.option_chain
        chain_puts = option_chain.select(['date', 'strike', 'expire_date',
                                           pl.col('p_bid').alias('bid'), pl.col('p_ask').alias('ask')])
        chain_calls = option_chain.select(['date', 'strike', 'expire_date',
                                            pl.col('c_bid').alias('bid'), pl.col('c_ask').alias('ask')])

        put_legs = legs.filter(pl.col('option_type') == OptionType.PUT.value).join(
            chain_puts, on=['date', 'strike', 'expire_date'], how='left')
        call_legs = legs.filter(pl.col('option_type') == OptionType.CALL.value).join(
            chain_calls, on=['date', 'strike', 'expire_date'], how='left')
        legs_priced = pl.concat([put_legs, call_legs], how='diagonal_relaxed').sort(['leg_id', 'date'])

        # Fill quote gaps (thin/no-quote days for a given strike) from the
        # nearest available day for that same leg -- forward first (use the
        # last known price), then backward for a gap at the very start of a
        # leg's life, mirroring the closing-price fallback philosophy
        # already used in position.py's _update_single_leg_closing_data.
        legs_priced = legs_priced.with_columns([
            pl.col('bid').fill_null(strategy='forward').over('leg_id'),
            pl.col('ask').fill_null(strategy='forward').over('leg_id'),
        ]).with_columns([
            pl.col('bid').fill_null(strategy='backward').over('leg_id'),
            pl.col('ask').fill_null(strategy='backward').over('leg_id'),
        ])
        legs_priced = legs_priced.with_columns(((pl.col('bid') + pl.col('ask')) / 2).alias('mid_price'))

        # Signed entry/current price, mirroring
        # BasePosition.signed_entry_price/signed_exit_price exactly (long:
        # entry is a debit/negative, current value is positive; short:
        # entry is a credit/positive, current cost-to-close is negative) --
        # summing the two gives per-share unrealized P&L, scaled by
        # multiplier/quantity to dollars, same as calculate_pnl's formula.
        is_long = pl.col('position_side') == PositionSide.LONG.value
        legs_priced = legs_priced.with_columns([
            pl.when(is_long).then(-pl.col('price').abs()).otherwise(pl.col('price').abs()).alias('signed_entry'),
            pl.when(is_long).then(pl.col('mid_price').abs()).otherwise(-pl.col('mid_price').abs()).alias('signed_current'),
        ])
        legs_priced = legs_priced.with_columns(
            ((pl.col('signed_current') + pl.col('signed_entry')) * pl.col('multiplier') * pl.col('quantity')).alias('leg_unrealized_pnl')
        )

        daily_unrealized = legs_priced.group_by(['trade_id', 'date']).agg(
            pl.col('leg_unrealized_pnl').sum().alias('unrealized_pnl')
        )

        start = date.fromisoformat(config.start_date)
        end = date.fromisoformat(config.end_date)
        daily = (
            self.underlying
            .filter((pl.col('date') >= start) & (pl.col('date') <= end))
            .select(['date'])
            .sort('date')
        )

        trades_for_join = trades_meta.sort('opened').with_columns(
            pl.col('capital').shift(1).fill_null(config.initial_capital).alias('capital_before')
        )

        daily = daily.join_asof(trades_for_join, left_on='date', right_on='opened', strategy='backward')
        daily = daily.with_columns(
            is_open=pl.col('closed').is_not_null() & (pl.col('date') < pl.col('closed'))
        )
        daily = daily.join(daily_unrealized, on=['trade_id', 'date'], how='left')

        daily = daily.with_columns(
            mtm_capital=pl.when(pl.col('is_open'))
            .then(pl.col('capital_before') + pl.col('unrealized_pnl').fill_null(0.0))
            .when(pl.col('closed').is_not_null())
            .then(pl.col('capital'))  # already closed as of this day -> flat at realized capital
            .otherwise(pl.lit(float(config.initial_capital)))  # before the first trade opened
        )

        daily = daily.with_columns(
            mtm_pnl=pl.col('mtm_capital').diff().fill_null(pl.col('mtm_capital') - config.initial_capital)
        )
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
            pl.col('mtm_pnl', 'cum_pnl', 'cum_pnl_pct', 'mtm_capital', 'running_max', 'dd_usd', 'dd_pct').round(2)
        )

        max_dd_row = daily.sort('dd_usd', descending=False).head(1)
        max_dd_usd = max_dd_row['dd_usd'][0]
        max_dd_pct = max_dd_row['dd_pct'][0]
        trough_capital = max_dd_row['mtm_capital'][0]
        peak_capital = max_dd_row['running_max'][0]

        dd_active = (daily['dd_usd'] < 0).to_numpy()
        max_dd_duration = 0
        current_run = 0
        for active in dd_active:
            if active:
                current_run += 1
                max_dd_duration = max(max_dd_duration, current_run)
            else:
                current_run = 0

        daily_ret = daily.with_columns(
            daily_ret=pl.col('mtm_capital') / pl.col('mtm_capital').shift(1) - 1
        )['daily_ret'].drop_nulls()
        mtm_sharpe = (
            (daily_ret.mean() / daily_ret.std() * (252 ** 0.5))
            if daily_ret.std() and daily_ret.std() > 0 else None
        )

        logger.info(f"[Daily MTM] Maximum Drawdown (USD): {max_dd_usd:.2f}")
        logger.info(f"[Daily MTM] Maximum Drawdown (%): {max_dd_pct:.2f}%")
        logger.info(f"[Daily MTM] Peak Capital: ${peak_capital:.2f}")
        logger.info(f"[Daily MTM] Trough Capital: ${trough_capital:.2f}")
        logger.info(f"[Daily MTM] Drawdown Duration: {max_dd_duration} trading days")
        logger.info(f"[Daily MTM] Sharpe Ratio: {round(mtm_sharpe, 2) if mtm_sharpe is not None else 'N/A'} "
                    f"(calendar-time, daily-return -- distinct from the trade-to-trade Sharpe above)")

        results['mtm_sharpe'] = mtm_sharpe
        results['daily_mtm'] = daily.select(
            ['date', 'mtm_capital', 'mtm_pnl', 'cum_pnl', 'cum_pnl_pct', 'running_max', 'dd_usd', 'dd_pct']
        )
        results['mtm_drawdown_analysis'] = {
            'max_drawdown': max_dd_usd,
            'peak_capital': peak_capital,
            'trough_capital': trough_capital,
            'drawdown_duration': max_dd_duration,
        }

        return results



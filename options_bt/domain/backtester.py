from typing import Dict, List, NamedTuple, Optional, Tuple, Union
import numpy as np
import pandas as pd
import logging
import time
from datetime import datetime
import os
from enum import Enum

from options_bt.domain.enums import *
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.position import SingleLegOptionPosition     
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.position import MultiLegOptionPosition
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

# Create logger instance
logger = setup_logger()



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

    def __post_init__(self):
        """Create the results directory if it does not exist."""
        
        os.makedirs(self.results_dir, exist_ok=True)

    def run(
        self,
        config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig]
        
    ) -> dict:
        """Execute a backtest with the given parameters."""
        start_time = time.time()
        logger.info(f"Starting backtest at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Logic for early close days
        # leg_early_close = leg.early_close_days if leg.early_close_days is not None else strategy.early_close_days
        
        # Initialize trade manager
        trade_manager = TradeManager(config=config)
        signal_generator = OptionSignalGenerator(option_chain=self.option_chain.copy(), underlying=self.underlying.copy(), config=config)    
        # Generate or validate signals
        signal_start = time.time()
        if isinstance(config, SingleLegOptionStrategyConfig):
            signals = signal_generator.generate_single_leg_signals()
        elif isinstance(config, MultiLegOptionStrategyConfig):
            signals = signal_generator.generate_multi_leg_signals()
        else:
            raise ValueError("Invalid config type")
        
        if signals.empty:
            logger.warning("No valid signals generated")
            return pd.DataFrame()

        # Pre-calculate margin requirements for all signals
        logger.info(f"Calculating margin requirements for trade signals for {config.quantity} | {config.leg.option_type if config.leg.option_type else config.spread_type} | {config.leg.delta_target if config.leg.delta_target else config.leg.delta_range}")
        is_spread = isinstance(config, MultiLegOptionStrategyConfig)
        max_allowed_margin = config.max_margin_utilization * config.initial_capital * config.leverage
        logger.info(f"Maximum allowed margin: ${max_allowed_margin:.2f} ({config.max_margin_utilization:.0%} of capital with {config.leverage}x leverage)")
        logger.info(f"Maximum simultaneous positions: {config.max_positions}")
        
        # Handle all spread types
        if is_spread:
            pass
            # TODO: Implement margin calculation for spreads
            # Calculate margins per spread group and ensure proper alignment
            # margins = trade_signals.groupby('spread_id').apply(SingleLegOptionStrategyConfig.calculate_margin_for_spread)
            # trade_signals['margin_required'] = trade_signals['spread_id'].map(margins)
            # logger.debug(f'Calculated margins for {len(margins)} spread groups')
            # logger.debug(f'First few margins: {margins.head()}')

        # Single leg
        else:
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
            logger.info(f"Average margin requirement for filtered trades: ${signals['margin_required'].mean():.2f}")
            logger.info(f"Maximum margin requirement for filtered trades: ${signals['margin_required'].max():.2f}")
        logger.info(f"Total valid signals: {len(valid_signals)}")

        # Execute trades
        backtest_start = time.time()
        results_transactions_dict = trade_manager.construct_and_execute_trades_from_signals(signals, 
                                                                                option_chain=self.option_chain, 
                                                                                underlying_price_history=self.underlying)
        self.execution_times['backtest_execution'] = time.time() - backtest_start
        trade_results = results_transactions_dict['trade_results']
        transactions = results_transactions_dict['transactions']
        if trade_results.empty:
            logger.warning("No trades were executed successfully")
            return pd.DataFrame()
        
        # Calculate margin utilization
        trade_results['margin_utilization'] = round(trade_results['capital_used'] / config.initial_capital, 2)
        trade_results['avg_margin_util'] = round(trade_results['margin_utilization'].mean(), 2)
        trade_results['max_margin_util'] = round(trade_results['margin_utilization'].max(), 2       )
        logger.info(f"Average margin utilization: {trade_results['avg_margin_util'].iloc[0]:.2%}")
        logger.info(f"Maximum margin utilization: {trade_results['max_margin_util'].iloc[0]:.2%}")
        # Calculate cumulative metrics based on PnL
        trade_results['cumulative_pnl'] = round(trade_results['pnl'].cumsum(), 2)
        trade_results['capital'] = round(config.initial_capital + trade_results['cumulative_pnl'], 2)  # Track actual capital based on cumulative PnL
        trade_results['peak_capital'] = round(trade_results['capital'].cummax(), 2)
        
        # Calculate Sharpe Ratio without risk-free rate
        sharpe = None
        if len(trade_results) > 1:
            returns = np.diff(trade_results['capital'].values) / trade_results['capital'].values[:-1]
            if len(returns) > 0 and np.std(returns) > 0:
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
                logger.info(f"Sharpe Ratio: {sharpe:.2f}")

        # Save results if requested
        # Generate parameter string based on backtest type
        if is_spread:
            param_str = f"{config.spread_type.value}_spread_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"
        else:
            param_str = f"{config.leg.option_type.value}_{config.leg.position_side.value}_{f'{config.leg.delta_range[0]}:{config.leg.delta_range[1]}' if config.leg.delta_range else config.leg.delta_target}_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"
        
        if self.save_trades:
            save_start = time.time()
            self._save_results(
                config=config,
                trade_results=trade_results,
                transactions=transactions,
                param_str=param_str
            )
            self.execution_times['saving'] = time.time() - save_start
            
        # Log execution times
        total_time = time.time() - start_time
        self._log_execution_summary(total_time)
        
        # NB: cumulative_pnl is the sum of realized profits/losses across all closed trades.
        # It starts from initial_capital and accumulates only closed P&L (not unrealized)
        # Thus (option_bp) matches the analytical P&L (cumulative_pnl + initial_capital):
        assert abs(trade_results['capital'].iloc[-1] - trade_results['bp'].iloc[-1]) < 1e-6, f'Final capital: {trade_results["capital"].iloc[-1]} | BP: {trade_results["bp"].iloc[-1]}'

        return {
            'trade_results': trade_results,
            'transactions': transactions
        }
    
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
    def calculate_mtm(self, results: dict, config: Union[SingleLegOptionPosition, MultiLegOptionPosition]) -> pd.DataFrame:
        """
        Calculate MTM and log the results.
        
        Args:
            results: Dict of trade results and transations DataFrames  
            config: Union[SingleLegOptionPosition, MultiLegOptionPosition]
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
                    max_drawdown_amount = daily_df['Drawdown ($)'].min()
                    max_drawdown_percentage = daily_df['Drawdown (%)'].min()    
                    # results_file.write(f"Maximum drawdown: ${daily_df['max_drawdown'].iloc[-1]:,.2f} ({daily_df['max_drawdown_pct'].iloc[-1]:.2f}%)\n")
                    results_file.write(f"Maximum drawdown: ${max_drawdown_amount:.2f} ({max_drawdown_percentage:.2f}%)\n")
        
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
        start_date = transactions['entry_date'].iloc[0]
        end_date = transactions['exit_date'].iloc[-1]
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
            for trade in transactions.itertuples():
                trade_start = pd.Timestamp(trade.entry_date).normalize()
                trade_end = pd.Timestamp(trade.exit_date).normalize()
                trade_id = (trade.expire_date, trade.strike, trade.option_type)

                # Handle existing trades
                if trade_id in active_trades:
                    logger.debug(f'Processing active trade: {trade_id}')
                    current_value = self.calculate_daily_value(trade, date, use_underlying_close)
                    prev_value = active_trades[trade_id]['position_value']
                    
                    # Calculate daily P&L for this trade
                    daily_pnl += round(current_value - prev_value, 2) if current_value is not None else 0
                    logger.debug(f'Daily PnL = Cur value - Prev value = {current_value} - {prev_value} = {daily_pnl}')

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
                        exit_premium = round(exit_price * 100 * trade.quantity, 2)  # Premium in dollars
                        logger.debug(f'Premium exit: {exit_premium}')
                        logger.debug(f'daily cash effect, before: {daily_cash_flow} | BP {option_bp}')
                        # Accumulate this to cash reserves
                        # daily_cash_flow += premium  # Already signed in the trade
                        # option_bp += premium  # Already signed in the trade

                        # TODO  add a func for closing
                        commission = 1.78
                        fees = 0
                        # ITM
                        early_closure = False
                        exercise_fee = 0
                        if trade.exit_date < trade.entry_date:
                            early_closure = True
                            exercise_fee += 5

                        fees += commission + exercise_fee
                        fees = round(fees * trade.quantity, 2)
                        daily_cash_flow = round(daily_cash_flow + exit_premium - fees, 2)
                        option_bp = round(option_bp + exit_premium - fees, 2)
                        logger.debug(f'daily cash effect, after: {daily_cash_flow} | BP {option_bp}')

                        del active_trades[trade_id]

                    # Update trade
                    else:
                        logger.debug(f'Updating existing trade {trade_id}')
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
                        logger.debug(f'Daily cash before: {daily_cash_flow}, BP before: {option_bp}')
                        # daily_cash_flow += premium  # Already signed in the trade
                        # option_bp += premium  # Already signed in the trade
                        daily_cash_flow = round(daily_cash_flow + premium, 2)
                        option_bp = round(option_bp + premium, 2)

                        req_margin = trade.capital_used
                        daily_margin_requirement += req_margin
                        
                        # For short positions, reduce BP
                        if PositionSide.is_short(trade):
                            option_bp = round(option_bp - req_margin / leverage, 2)  # Account for leverage in BP reductio
                            logger.debug(f'Margin reduced for short, BP now: {option_bp}')

                        # Update position value and margin
                        daily_position_value = round(daily_position_value + position_value, 2)
                        logger.debug(f'Daily Position Value: {position_value}')
    
            # Update cumulative P&L
            cumulative_pnl = round(cumulative_pnl + daily_pnl, 2)
            
            # Update cash with any daily premium flows
            # NB: there is no change in daily cash or BP due to unrealized pnl for equity and index options, only for certain futures
            cash  = round(cash + daily_cash_flow, 2)
            
            # Calculate net liquidation value
            net_liq = round(cash + daily_position_value, 2)

            # daily drift persists but final seems ok
            drift = abs(net_liq - (initial_capital + cumulative_pnl))
            if 1 < drift < 5:
                logger.warning(f'FLOATING ERR DRIFT under $5: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')
            else:
                logger.error(f'FLOATING ERR DRIFT above $10: Net Liq = {net_liq} != Initial Cap + Cum PnL = {initial_capital + cumulative_pnl}')

            # Update peak liquidity if net liquidation value is higher
            if net_liq > peak_liquidity:
                peak_liquidity = net_liq
                
            # Calculate drawdown
            drawdown_amount = - max(0, round(peak_liquidity - net_liq, 2))
            drawdown_pct = round(drawdown_amount / peak_liquidity * 100, 2) if peak_liquidity > 0 else 0

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
        """Generate parameter string based on backtest type."""

        if isinstance(config, MultiLegOptionStrategyConfig):
            spread_type = config.spread_type
            return f"{spread_type.value}_spread_{f'{config.dte_range[0]}:{config.dte_range[1]}' if config.dte_range else config.dte_target}_{config.start_date}:{config.end_date}"
        else:
            return f"{config.leg.option_type.value}_{config.leg.position_side.value}_{f'{config.leg.delta_range[0]}:{config.leg.delta_range[1]}' if config.leg.delta_range else config.leg.delta_target}_{f'{config.leg.dte_range[0]}:{config.leg.dte_range[1]}' if config.leg.dte_range else config.leg.dte_target}_{config.start_date}:{config.end_date}"

 
    def _save_results(self, config: Union[SingleLegOptionStrategyConfig, MultiLegOptionStrategyConfig], trade_results: pd.DataFrame, transactions: pd.DataFrame=None, mtm_df: pd.DataFrame=None, param_str: str="default"):
        """Save trade results and transactions to a CSV file."""

        # Save trades
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trades_csv_path = os.path.join(self.results_dir, f"trades_{param_str}_{timestamp}.csv")
        trade_results.to_csv(trades_csv_path, index=False)      

        # Save transactions if available
        if transactions is not None and not transactions.empty:
            transactions_csv_path = os.path.join(self.results_dir, f"transactions_{param_str}_{timestamp}.csv")
            transactions.to_csv(transactions_csv_path, index=False)

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

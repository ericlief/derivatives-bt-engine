# tests/domain/test_position.py
import pytest
import pandas as pd
import numpy as np
from options_bt.domain.option_leg_config import OptionLegConfig
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.domain.enums import *
from options_bt.domain.trade_manager import TradeManager

from options_bt.domain.dataloader import DataLoader
from options_bt.domain.strategy_config import SingleLegOptionStrategyConfig
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.trade_manager import TradeManager
from options_bt.domain.option_signal_generator import OptionSignalGenerator
from scipy.stats import norm
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils

logger = setup_logger()

@pytest.fixture(scope="module")
def mock_data():
    """Set up test data with mock data instead of real files."""

    # Create underlying chain data for all relevant dates
    start_date = pd.Timestamp('2023-01-01')
    end_date = pd.Timestamp('2023-01-31')  # Shortened to just January since all our tests use January dates
    dates = pd.date_range(start=start_date, end=end_date, freq='D')

    # Generate varying underlying prices (slight upward trend with noise)
    # np.random.seed(42)  # For reproducibility
    base_price = 95.0
    price_changes = np.random.normal(0.5, 0.5, len(dates)).cumsum()  # Random walk with upward drift
    underlying_prices = base_price + price_changes

    # Ensure we have specific price points for testing
    price_series = pd.Series(underlying_prices, index=dates)
    # price_series.loc['2023-01-31'] = 105.0  # Set expiration date price for ITM tests
    underlying_prices = price_series.values

    # Create option chain data with Black-Scholes prices and greeks
    option_chain_data = []
    puts = []
    calls = []

    # Parameters for Black-Scholes
    r = 0.05  # 5% risk-free rate
    sigma = 0.3  # 30% volatility
    strikes = [85, 90, 95, 100, 105, 110]
    for i, date in enumerate(dates):  # step through  dates
        for strike in strikes:
            current_price = underlying_prices[i]
            days_to_expiry = (end_date - date).days
            T = max(0, days_to_expiry / 365)  # Time to expiry in years
            
            # Calculate Black-Scholes prices and deltas
            call_price, call_delta = black_scholes(
                S=current_price, 
                K=strike, 
                T=T, 
                r=r, 
                sigma=sigma, 
                option_type='call'
            )
            put_price, put_delta = black_scholes( 
                S=current_price, 
                K=strike, 
                T=T, 
                r=r, 
                sigma=sigma, 
                option_type='put'
            )
            calls.append(call_price)
            puts.append(put_price)
            print('underly', underlying_prices[i])
            print('call', call_price, call_delta)
            print('put', put_price, put_delta)
            # Add bid-ask spread that widens for further dates and lower liquidity
            
            base_spread = 0.05 + (T * 0.1) + (abs(call_delta - 0.5) * 0.1)
            
            option_chain_data.append({
                'date': date,
                'underlying_last': round(current_price, 2),
                'p_bid': round(max(0.01, put_price - base_spread), 2),
                'p_ask': round(put_price + base_spread, 2),
                'c_bid': round(max(0.01, call_price - base_spread), 2),
                'c_ask': round(call_price + base_spread, 2),
                'p_delta': round(put_delta, 2),
                'c_delta': round(call_delta, 2),
                'strike': round(strike, 2),
                'expire_date': end_date,
                'dte': days_to_expiry
            })

    option_chain_data = pd.DataFrame(option_chain_data).set_index('date')
    data = {
            'option_chain': option_chain_data,
            'underlying_price_history': pd.DataFrame(underlying_prices, columns=['close'], index=dates)
            }
    return data

def black_scholes(S, K, T, r, sigma, option_type='call'):
    """Calculate Black-Scholes option price and delta."""
    if T <= 0:
        if option_type == 'call':
            return max(0, S - K), 1.0 if S > K else 0.0
        else:
            return max(0, K - S), -1.0 if S < K else 0.0
            
    d1 = (np.log(S/K) + (r + sigma**2/2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)
    
    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:
        price = K * np.exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)
        
    return price, delta

@pytest.fixture
def mock_backtester(mocker):
    return mocker.patch('options_bt.domain.backtester.Backtester', autospec=True)

@pytest.fixture
def mock_option_signal_generator(mocker):
    return mocker.patch('options_bt.domain.option_signal_generator.OptionSignalGenerator', autospec=True)

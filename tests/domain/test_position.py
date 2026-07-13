# tests/domain/test_position.py
import pytest
from datetime import date
import polars as pl
import numpy as np
from options_bt.domain.position import SingleLegOptionPosition, MultiLegOptionPosition
from options_bt.domain.enums import OptionsType, PositionSide, OptionsStrategy, OptionSpreadType
from options_bt.domain.dataloader import OptionsDataLoader
from options_bt.domain.trade_result import OptionTradeResult
from options_bt.domain.trade_manager import TradeManager
from scipy.stats import norm
from options_bt.utils.logger import setup_logger
from options_bt.utils.price_utils import PriceUtils
from tests.domain.conftest import *


logger = setup_logger()

# @pytest.fixture(scope="module")
# def mock_data():
#     """Fixture to create mock option chain data."""
#     start_date = date(2023, 1, 1)
#     end_date = date(2023, 1, 31)
#     dates = pd.date_range(start=start_date, end=end_date, freq='D')
    
#     r = 0.05  # 5% risk-free rate
#     sigma = 0.3  # 30% volatility
#     strike = 100.0
    
#     np.random.seed(42)
#     base_price = 95.0
#     price_changes = np.random.normal(0.1, 0.5, len(dates)).cumsum()
#     underlying_prices = base_price + price_changes
    
#     price_series = pd.Series(underlying_prices, index=dates)
#     price_series.loc['2023-01-31'] = 105.0
    
#     option_chain_data = []
#     for i, date in enumerate(dates):
#         current_price = underlying_prices[i]
#         days_to_expiry = (end_date - date).days
#         T = max(0, days_to_expiry / 365)
        
#         call_price, call_delta = black_scholes(current_price, strike, T, r, sigma, 'call')
#         put_price, put_delta = black_scholes(current_price, strike, T, r, sigma, 'put')
        
#         base_spread = 0.05 + (T * 0.1) + (abs(call_delta - 0.5) * 0.1)
        
#         option_chain_data.append({
#             'underlying_last': round(current_price, 2),
#             'p_bid': round(max(0.01, put_price - base_spread), 2),
#             'p_ask': round(put_price + base_spread, 2),
#             'c_bid': round(max(0.01, call_price - base_spread), 2),
#             'c_ask': round(call_price + base_spread, 2),
#             'p_delta': round(put_delta, 2),
#             'c_delta': round(call_delta, 2),
#             'strike': round(strike, 2),
#             'expire_date': end_date,
#             'dte': days_to_expiry
#         })

#     return {'option_chain': pd.DataFrame(option_chain_data, index=dates), 'underlying_price_history': pd.DataFrame({'close': price_series.to_list()}, index=dates)}

# def black_scholes(S, K, T, r, sigma, option_type='call'):
#     """Calculate Black-Scholes option price and delta."""
#     if T <= 0:
#         if option_type == 'call':
#             return max(0, S - K), 1.0 if S > K else 0.0
#         else:
#             return max(0, K - S), -1.0 if S < K else 0.0
            
#     d1 = (np.log(S/K) + (r + sigma**2/2)*T) / (sigma*np.sqrt(T))
#     d2 = d1 - sigma*np.sqrt(T)
    
#     if option_type == 'call':
#         price = S * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)
#         delta = norm.cdf(d1)
#     else:
#         price = K * np.exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)
#         delta = -norm.cdf(-d1)
        
#     return price, delta


def test_instance_vars(setup_test_data):
    """
    
    single_leg_long_call = SingleLegOptionPosition(
        trade_id=1,
        option_strategy=OptionsStrategy.LONG_CALL,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=round(float(c_bid + c_ask) / 2, 2),
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.34,
        entry_dte=30,
        underlying_entry=95.75
    )

    single_leg_short_put = SingleLegOptionPosition(
        trade_id=2,
        option_strategy=OptionsStrategy.SHORT_PUT,
        quantity=1,
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 1),
        entry_price=round(float(p_bid + p_ask) / 2, 2),
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=-0.66,
        entry_dte=30,
        underlying_entry=95.75
    )
    """
    data, single_leg_long_call, single_leg_short_put = setup_test_data

    # Opening prices
    p_bid, p_ask = data['option_chain']['p_bid'][0], data['option_chain']['p_ask'][0]
    c_bid, c_ask = data['option_chain']['c_bid'][0], data['option_chain']['c_ask'][0]
    long_call_entry_price = round(float(c_bid + c_ask) / 2, 2)
    short_put_entry_price = round(float(p_bid + p_ask) / 2, 2)
    print(f"long_call_entry_price: {long_call_entry_price}, short_put_entry_price: {short_put_entry_price}")
    # Closing prices
    p_bid, p_ask = data['option_chain']['p_bid'][-1], data['option_chain']['p_ask'][-1]
    c_bid, c_ask = data['option_chain']['c_bid'][-1], data['option_chain']['c_ask'][-1]
    long_call_exit_price = round(float(c_bid + c_ask) / 2, 2)   
    short_put_exit_price = round(float(p_bid + p_ask) / 2, 2)
    print(f"long_call_exit_price: {long_call_exit_price}, short_put_exit_price: {short_put_exit_price}")

    assert single_leg_long_call.entry_price == long_call_entry_price
    assert single_leg_short_put.entry_price == short_put_entry_price
    assert single_leg_long_call.position_side == PositionSide.LONG
    assert single_leg_short_put.position_side == PositionSide.SHORT
    assert single_leg_long_call.is_put == False        
    assert single_leg_short_put.is_put == True
    assert single_leg_long_call.is_call == True
    assert single_leg_short_put.is_call == False 
    assert single_leg_long_call.is_long == True
    assert single_leg_short_put.is_long == False
    assert single_leg_long_call.is_ITM(underlying_price=105) == True
    assert single_leg_long_call.is_ITM(underlying_price=95) == False
    assert single_leg_short_put.is_ITM(underlying_price=105) == False
    assert single_leg_short_put.is_ITM(underlying_price=95) == True
    assert single_leg_long_call.signed_entry_price == -abs(single_leg_long_call.entry_price)
    assert single_leg_short_put.signed_entry_price == abs(single_leg_short_put.entry_price)
    assert single_leg_long_call.premium == long_call_entry_price * 100 * 1
    assert single_leg_short_put.premium == short_put_entry_price * 100 * 1
    assert single_leg_long_call.signed_premium == -long_call_entry_price * 100 * 1
    assert single_leg_short_put.signed_premium == short_put_entry_price * 100 * 1
    assert single_leg_long_call.reset().calculate_pnl(underlying_exit=105) == round( (5 + single_leg_long_call.signed_entry_price) * 100 * 1 - (5 + 1.78) * 1, 2)  # ITM
    # single_leg_long_call.reset()
    assert single_leg_long_call.reset().calculate_pnl(underlying_exit=95) == round((0 + single_leg_long_call.signed_entry_price) * 100 * 1 - 1.78 * 1, 2) # OTM
    assert single_leg_short_put.reset().calculate_pnl(underlying_exit=105) == round((0 + single_leg_short_put.signed_entry_price) * 100 * 1 - 1.78 * 1, 2)  # OTM
    assert single_leg_short_put.reset().calculate_pnl(underlying_exit=95) == round((-5 + single_leg_short_put.signed_entry_price) * 100 * 1 - (5 + 1.78) * 1, 2) # ITM
    assert single_leg_long_call.reset().calculate_pnl(exit_price=long_call_exit_price, close_reason='early closure') == round((long_call_exit_price + single_leg_long_call.signed_entry_price) * 100 * 1 - 1.78 * 1, 2)
    assert single_leg_short_put.reset().calculate_pnl(exit_price=short_put_exit_price, close_reason='early closure') == round((-short_put_exit_price + single_leg_short_put.signed_entry_price) * 100 * 1 - 1.78 * 1, 2)



@pytest.mark.parametrize("underlying_price, strike, option_type, position_side", [
    (100.0, 90.0, OptionsType.CALL, PositionSide.SHORT),
    (100.0, 100.0, OptionsType.CALL, PositionSide.SHORT),
    (100.0, 110.0, OptionsType.CALL, PositionSide.SHORT),
])
def test_single_leg_margin(underlying_price, strike, option_type, position_side):
    """Test margin calculation for a single leg option position."""

    entry_price, _ = black_scholes(
        S=underlying_price,
        K=strike,
        T=30/365,
        r=0.05,
        sigma=0.3,
        option_type='call' if option_type == OptionsType.CALL else 'put'
    )
    
    margin = SingleLegOptionPosition.calculate_margin(
        quantity=1,
        option_type=option_type,
        position_side=position_side,
        underlying_price=underlying_price,
        entry_price=entry_price,
        strike=strike
    )
    
    otm_amount = max(0, underlying_price - strike) if option_type == OptionsType.PUT else max(0, strike - underlying_price)
    expected_margin = (entry_price + max(0.15 * underlying_price - otm_amount, 0.10 * underlying_price)) * 100
    
    assert margin == round(expected_margin, 2), f"Margin mismatch for {option_type} {position_side} with strike {strike}"

def test_single_leg_pnl():
    """Test P&L calculation for a single leg option position."""
    
    # Calculate entry and exit prices using Black-Scholes   
    underlying_price = 100.0
    strike = 100.0
    entry_price, entry_delta = black_scholes(
        S=underlying_price,
        K=strike,
        T=30/365,  # 30 days
        r=0.05,    # 5% risk-free rate
        sigma=0.3, # 30% volatility
        option_type='call'
    )
    position = SingleLegOptionPosition(
        trade_id=1,
        option_strategy=OptionsStrategy.LONG_CALL,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=entry_price,
        strike=strike,
        expire_date=date(2023, 1, 31),
        entry_delta=entry_delta,
        entry_dte=30,
        underlying_entry=underlying_price
    )
    # Calculate exit price with higher underlying
    exit_price, _ = black_scholes(
        S=underlying_price * 1.05,  # 5% increase in underlying
        K=strike,
        T=29/365,  # 29 days (1 day later)
        r=0.05,
        sigma=0.3,
        option_type='call'
    )
    position.exit_price
    # Test long call profit
    fees = 1.78 * 1
    pnl = position.calculate_pnl(exit_price=exit_price, close_reason='early closure')
    expected_profit = (exit_price - entry_price) * 100 * 1 - fees  # Assuming quantity is 1
    assert pnl == round(expected_profit, 2), f"PnL mismatch for long call: {pnl} != {expected_profit}, entry_price: {entry_price}, exit_price: {exit_price}, fees: {fees}"

    # Test short put loss
    entry_price, _ = black_scholes(
        S=underlying_price,
        K=strike,
        T=30/365,
        r=0.05,
        sigma=0.3,
        option_type='put'
    )
    
    # Calculate exit price with lower underlying
    exit_price, _ = black_scholes(
        S=underlying_price * 0.95,  # 5% decrease in underlying
        K=strike,
        T=29/365,
        r=0.05,
        sigma=0.3,
        option_type='put'
    )

    position = SingleLegOptionPosition(
        trade_id=2,
        option_strategy=OptionsStrategy.SHORT_PUT,
        quantity=10,
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 1),
        entry_price=entry_price,
        strike=strike,
        expire_date=date(2023, 1, 31),
        entry_delta=-0.66,
        entry_dte=30,
        underlying_entry=underlying_price
    )
    fees = 1.78 * 10
    pnl = position.calculate_pnl(exit_price=exit_price, close_reason='early closure')
    expected_loss = round((-exit_price + entry_price) * 100 * 10 - fees, 2)  # Assuming quantity is 10
    assert pnl == round(expected_loss, 2), f"PnL mismatch for short put: {pnl} != {expected_loss}, entry_price: {entry_price}, exit_price: {exit_price}, fees: {fees}"

def test_pnl_with_fees(setup_test_data):
    """Test P&L calculations including fees."""

    # data, single_leg_long, single_leg_short = setup_test_data
    test_cases = [
        # (entry_price, exit_price, quantity, position_side, option_type, expected_pnl)
        (5.0, 6.0, 1, PositionSide.LONG, OptionsType.CALL, (6 - 5) * 100 * 1 - 1.78),  # Long call profit
        (5.0, 4.0, 1, PositionSide.LONG, OptionsType.CALL, (4 - 5) * 100 * 1 - 1.78),  # Long call loss
        (5.0, 4.0, 1, PositionSide.SHORT, OptionsType.PUT, (-4 + 5) * 100 * 1 - 1.78),   # Short put profit
        (5.0, 6.0, 1, PositionSide.SHORT, OptionsType.PUT, (-6 + 5) * 100 * 1 - 1.78),  # Short put loss
        # Test with multiple contracts
        (5.0, 6.0, 2, PositionSide.LONG, OptionsType.CALL, (6 - 5) * 100 * 2 - 1.78 * 2),  # 2 contracts
        (5.0, 4.0, 3, PositionSide.SHORT, OptionsType.PUT, (-4 + 5) * 100 * 3 - 1.78 * 3),  # 3 contracts
    ]

    for entry_price, exit_price, quantity, position_side, option_type, expected_pnl in test_cases:
        # Create test position
        position = SingleLegOptionPosition(
            trade_id=1,
            option_strategy=OptionsStrategy.LONG_CALL if position_side == PositionSide.LONG else OptionsStrategy.SHORT_CALL,
            quantity=quantity,
            option_type=option_type,
            position_side=position_side,
            entry_date=date(2023, 1, 1),
            entry_price=entry_price,
            strike=100.0,
            expire_date=date(2023, 1, 31),
            entry_delta=0.5,
            entry_dte=30,
            underlying_entry=95.0
        )
        
        # Calculate fees (1.78 per contract)
        fees = 1.78  # * quantity (this should be in the func?)
        
        # Calculate PnL with fees
        pnl = position.calculate_pnl(exit_price=exit_price, commission=fees, close_reason='early closure')
        logger.info(f'Got pnl {pnl} for expected pnl {expected_pnl}')
        # Verify PnL calculation
        assert round(pnl, 2) == expected_pnl, f"PnL mismatch for {option_type.value} {position_side.value} with {quantity} contracts:\n" \
            f"Expected: {expected_pnl}, Got: {round(pnl, 2)}\n" \
            f"Entry: {entry_price}, Exit: {exit_price}, Fees: {fees * quantity}"
        

def test_calculate_intrinsic_value(setup_test_data):
    """Calculate intrinsic value at expiration.
     def calculate_intrinsic_value(self, underlying_price: float) -> float:
        if self.is_put:
            return max(0, self.strike - underlying_price)
        else:  # Call
            return max(0, underlying_price - self.strike)
            """
    data, single_leg_long_call, single_leg_short_put = setup_test_data
    assert single_leg_long_call.calculate_intrinsic_value(underlying_price=105) == 5  # strike 100, underlying 105, intrinsic 5 
    assert single_leg_long_call.calculate_intrinsic_value(underlying_price=95) == 0  # strike 100, underlying 95, intrinsic 0
    assert single_leg_short_put.calculate_intrinsic_value(underlying_price=105) == 0  # strike 100, underlying 105, intrinsic 0
    assert single_leg_short_put.calculate_intrinsic_value(underlying_price=95) == 5  # strike 100, underlying 95, intrinsic 5


def test_signed_prices(setup_test_data):
    """Test signed entry and exit prices."""
    data, single_leg_long, single_leg_short = setup_test_data
    # Long position tests
    entry_price = single_leg_long.entry_price
    assert single_leg_long.signed_entry_price == -abs(entry_price)  # BTO should be negative
    
    # Short position tests
    entry_price = single_leg_short.entry_price
    assert single_leg_short.signed_entry_price == abs(entry_price)  # STO should be positive

def test_premium_calculations(setup_test_data):
    """Test premium calculations."""
    data, single_leg_long, single_leg_short = setup_test_data
    # Test long call premium (quantity * price * 100)
    expected_premium = single_leg_long.entry_price * 100 * single_leg_long.quantity
    assert single_leg_long.premium == expected_premium
    assert single_leg_long.signed_premium == -expected_premium  # Long premium should be negative

    # Test short put premium
    expected_premium = single_leg_short.entry_price * 100 * single_leg_short.quantity
    assert single_leg_short.premium == expected_premium
    assert single_leg_short.signed_premium == expected_premium  # Short premium should be positive

def test_margin_requirements(setup_test_data):
    """Test margin requirement calculations using IB's formula."""

    # data, single_leg_long, single_leg_short = setup_test_data
    # Test cases with different moneyness levels
    test_cases = [
        # (underlying, strike, option_type, position_side, entry_price, expected_margin)
        (100.0, 100.0, OptionsType.CALL, PositionSide.SHORT, 5.0, 2000.0),  # ATM call
        (100.0, 90.0, OptionsType.CALL, PositionSide.SHORT, 12.0, 2700.0),  # ITM call
        (100.0, 110.0, OptionsType.CALL, PositionSide.SHORT, 2.0, 1200.0),  # OTM call
        (100.0, 100.0, OptionsType.PUT, PositionSide.SHORT, 5.0, 2000.0),   # ATM put
        (100.0, 110.0, OptionsType.PUT, PositionSide.SHORT, 12.0, 2700.0),  # ITM put
        (100.0, 90.0, OptionsType.PUT, PositionSide.SHORT, 2.0, 1200.0),    # OTM put
        # Test long positions (should have no margin requirement)
        (100.0, 100.0, OptionsType.CALL, PositionSide.LONG, 5.0, 0.0),
        (100.0, 100.0, OptionsType.PUT, PositionSide.LONG, 5.0, 0.0),
    ]

    for underlying, strike, option_type, position_side, entry_price, expected_margin in test_cases:
        # Calculate margin using class method
        margin = SingleLegOptionPosition.calculate_margin(
            quantity=1,
            option_type=option_type,
            position_side=position_side,
            underlying_price=underlying,
            entry_price=entry_price,
            strike=strike,
            leverage=1.0
        )
        
        # Verify margin calculation
        assert margin == expected_margin, f"Margin mismatch for {option_type.value} {position_side.value} with strike {strike}:\n" \
            f"Expected: {expected_margin}, Got: {margin}\n" \
            f"Underlying: {underlying}, Entry Price: {entry_price}"

        # Also test using instance method for consistency
        position = SingleLegOptionPosition(
            trade_id=1,
            option_strategy=OptionsStrategy.LONG_CALL if position_side == PositionSide.LONG else OptionsStrategy.SHORT_CALL,
            quantity=1,
            option_type=option_type,
            position_side=position_side,
            entry_date=date(2023, 1, 1),
            entry_price=entry_price,
            strike=strike,
            expire_date=date(2023, 1, 31),
            entry_delta=0.5,
            entry_dte=30,
            underlying_entry=underlying
        )
        
        instance_margin = position.calculate_position_margin(leverage=1.0)
        assert instance_margin == expected_margin, f"Instance margin mismatch for {option_type.value} {position_side.value} with strike {strike}:\n" \
            f"Expected: {expected_margin}, Got: {instance_margin}\n" \
            f"Underlying: {underlying}, Entry Price: {entry_price}"

def test_close_validation(setup_test_data):
    """Test position closing validation edge cases."""

    # data, single_leg_long, single_leg_short = setup_test_data

    # Test invalid entry date
    invalid_position = SingleLegOptionPosition(
        trade_id=1,
        option_strategy=OptionsStrategy.LONG_CALL,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(1970, 1, 1),  # Invalid date
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=95.0
    )
    
    result = invalid_position.close(
        option_chain=pl.DataFrame(),
        underlying_price_history=pl.DataFrame(),
        option_bp=10000
    )
    assert result is None

    # Test missing expiration and close dates
    no_dates_position = SingleLegOptionPosition(
        trade_id=1,
        option_strategy=OptionsStrategy.LONG_CALL,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=None,
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=95.0
    )
    
    result = no_dates_position.close(
        option_chain=pl.DataFrame(),
        underlying_price_history=pl.DataFrame(),
        option_bp=10000
    )
    assert result is None


def test_close_position(setup_test_data):
    """Test closing a position."""

    data, _ , _ = setup_test_data
    
    # Create test positions
    p_bid, p_ask = data['option_chain']['p_bid'][1], data['option_chain']['p_ask'][1]
    c_bid, c_ask = data['option_chain']['c_bid'][1], data['option_chain']['c_ask'][1]

    entry_price_call = round(float(c_bid + c_ask) / 2, 2)
    entry_price_put = round(float(p_bid + p_ask) / 2, 2)

    # Create a position with proper margin_required
    position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=10,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 2),
        entry_price=entry_price_call,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=29,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    trade_result, transaction = position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert isinstance(trade_result, dict)
    assert isinstance(transaction, dict)
    assert transaction['quantity'] == 10
    assert transaction['option_type'] == OptionsType.CALL.value
    assert transaction['position_side'] == PositionSide.LONG.value
    assert transaction['entry_date'] == date(2023, 1, 2)
    assert transaction['expire_date'] == date(2023, 1, 31)
    assert transaction['entry_delta'] == 0.5
    assert transaction['days_held'] == 29  # Full period
    assert transaction['underlying_entry'] == 95.0
    assert transaction['strike'] == 100.0
    assert transaction['capital_used'] == 1000.0
    assert trade_result['close_reason'] == 'expired'

  # Create a position with proper margin_required
    position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.SHORT_PUT,
        trade_id=1,
        quantity=10,
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 2),
        entry_price=entry_price_call,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=29,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    trade_result, transaction = position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert isinstance(trade_result, dict)
    assert isinstance(transaction, dict)
    assert transaction['quantity'] == 10
    assert transaction['option_type'] == OptionsType.PUT.value
    assert transaction['position_side'] == PositionSide.SHORT.value
    assert transaction['entry_date'] == date(2023, 1, 2)
    assert transaction['expire_date'] == date(2023, 1, 31)
    assert transaction['entry_delta'] == 0.5
    assert transaction['days_held'] == 29  # Full period
    assert transaction['underlying_entry'] == 95.0
    assert transaction['strike'] == 100.0
    assert transaction['capital_used'] == 1000.0
    assert trade_result['close_reason'] == 'expired'


def test_close_scenarios(setup_test_data):
    """Test different position closure scenarios."""

    data, single_leg_long, single_leg_short = setup_test_data
    logger.info(f"underlying close 1-31: {data['underlying_price_history'].filter(pl.col('date') == date(2023, 1, 31))['close'][0]}")
    # Test early closure
    early_close_position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=single_leg_long.entry_price,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=90,
        underlying_entry=95.0,
        close_date=date(2023, 1, 15),
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    result = early_close_position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is not None
    trade_result, transaction = result
    assert trade_result['close_reason'] == 'early closure'
    assert transaction['days_held'] == (date(2023, 1, 15) - date(2023, 1, 1)).days  # Jan 1 to Jan 15    

    # Test expiration closure for ITM option
    itm_expire_position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=2,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=single_leg_long.entry_price,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    result = itm_expire_position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is not None
    trade_result, transaction = result
    assert trade_result['close_reason'] == 'expired'
    assert transaction['days_held'] == 30  # Full 30 days to expiration

    # Test expiration closure for OTM option
    otm_expire_position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_PUT,
        trade_id=3,
        quantity=1,
        option_type=OptionsType.PUT,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=single_leg_long.entry_price,
        strike=100.0,  # OTM put
        expire_date=date(2023, 1, 31),
        entry_delta=-0.3,
        entry_dte=30,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    result = otm_expire_position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is not None
    trade_result, transaction = result
    assert trade_result['close_reason'] == 'expired'
    assert trade_result['days_held'] == 30
    assert trade_result['pnl'] == (otm_expire_position.signed_entry_price + 0) * 100 * 1 - 1.78 * 1      # OTM option should expire worthless

def test_invalid_close_dates(setup_test_data):
    """Test invalid closure date scenarios."""
    data, single_leg_long, single_leg_short = setup_test_data
    # Test close date before entry date
    invalid_close_position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 2, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 3, 31),
        entry_delta=0.5,
        entry_dte=60,
        underlying_entry=95.0,
        close_date=date(2023, 1, 1)  # Close date before entry
    )
    
    result = invalid_close_position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is None  # Should return None for invalid close date

    # Test expire date before entry date
    invalid_expire_position = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 2, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),  # Expire before entry
        entry_delta=0.5,
        entry_dte=60,
        underlying_entry=95.0
    )
    
    result = invalid_expire_position.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is None  # Should return None for invalid expire date

def test_exercise_fees(setup_test_data):
    """Test that exercise fees are correctly applied for ITM options at expiration."""
    data, single_leg_long, single_leg_short = setup_test_data

    # Test ITM call at expiration (should include exercise fee)
    itm_call = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=2,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=single_leg_long.entry_price,
        strike=100.0,  # ITM call (underlying at 105)
        expire_date=date(2023, 1, 31),
        entry_delta=0.7,
        entry_dte=30,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    result = itm_call.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is not None
    trade_result, transaction = result
    # Should include base fee (1.78 per contract) plus exercise fee (5.00 per contract)
    assert itm_call.is_ITM(105)
    expected_fees = (1.78 * 2) + (5.00 * 2)  # 2 contracts
    assert trade_result['fees'] == expected_fees

    # Test ITM put at expiration (should include exercise fee)
    otm_put = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_PUT,
        trade_id=2,
        quantity=2,
        option_type=OptionsType.PUT,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=single_leg_long.entry_price,
        strike=100.0,  # ITM put (underlying at 95)
        expire_date=date(2023, 1, 31),
        entry_delta=-0.7,
        entry_dte=30,
        underlying_entry=95.0,
        margin_required=1000.0  # Add margin required to avoid division by zero
    )
    
    result = otm_put.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=10000
    )
    assert result is not None
    trade_result, transaction = result
    # Should include base fee (1.78 per contract) and NO exercise fee (5.00 per contract)
    expected_fees = (1.78 * 2)    # 2 contracts, no exercise fee
    assert trade_result['fees'] == expected_fees

def test_buying_power_updates(setup_test_data):
    """Test that buying power is correctly updated during position closure."""

    data, _, _ = setup_test_data
    initial_bp = 100000
    
    # Test long position closure (should add exit premium to BP)
    long_pos = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=95.0
    )
    
    result = long_pos.close(
        option_chain=data['option_chain'],
        underlying_price_history=data['underlying_price_history'],
        option_bp=initial_bp
    )
    assert result is not None
    trade_result, transaction = result
    # BP should increase by exit premium and decrease by fees
    assert transaction['bp'] > initial_bp

    # # Test short position closure (should subtract exit premium from BP and return margin)
    # short_pos = SingleLegOptionPosition(
    #     option_strategy=OptionsStrategy.SHORT_CALL,
    #     trade_id=2,
    #     quantity=1,
    #     option_type=OptionsType.CALL,
    #     position_side=PositionSide.SHORT,
    #     entry_date=date(2023, 1, 1),
    #     entry_price=5.0,
    #     strike=100.0,
    #     expire_date=date(2023, 1, 31),
    #     entry_delta=0.5,
    #     entry_dte=30,
    #     underlying_entry=95.0
    # )
    
    # initial_bp = 100000
    # result = short_pos.close(
    #     option_chain=data['option_chain'],
    #     underlying_price_history=data['underlying_price_history'],
    #     option_bp=initial_bp
    # )
    # assert result is not None
    # logger.info(f'short call BP: {result}')
    # trade_result, transaction = result
    # # BP should reflet premium of $500 and loss of $500, plus margin return, and minus fees of $6.78
    # final_bp = transaction['bp']
    # assert final_bp != initial_bp
    # assert final_bp == initial_bp + abs(transaction['entry_price']) * 100 - (abs(transaction['exit_price']) * 100) - trade_result['fees']

def test_margin_required_property(setup_test_data):
    """Test the margin_required property calculation."""
    data, single_leg_long, single_leg_short = setup_test_data
    # Test long call - should have no margin requirement
    long_call = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.LONG_CALL,
        trade_id=1,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=100.0
    )
    assert long_call.margin_required == 0

    # Test short put - should use IB's formula
    short_put = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.SHORT_PUT,
        trade_id=2,
        quantity=1,
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=-0.5,
        entry_dte=30,
        underlying_entry=100.0
    )
    
    # Calculate expected margin using IB's formula
    otm_amount = max(0, short_put.underlying_entry - short_put.strike)
    expected_margin = (
        short_put.entry_price +
        max(
            (0.15 * short_put.underlying_entry - otm_amount),
            (0.10 * short_put.underlying_entry)
        )
    ) * 100 * short_put.quantity
    
    assert short_put.margin_required == round(expected_margin, 2)

    # Test short call - should use IB's formula
    short_call = SingleLegOptionPosition(
        option_strategy=OptionsStrategy.SHORT_CALL,
        trade_id=3,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 1),
        entry_price=5.0,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.5,
        entry_dte=30,
        underlying_entry=100.0
    )
    
    # Calculate expected margin using IB's formula
    otm_amount = max(0, short_call.strike - short_call.underlying_entry)
    expected_margin = (
        short_call.entry_price +
        max(
            (0.15 * short_call.underlying_entry - otm_amount),
            (0.10 * short_call.underlying_entry)
        )
    ) * 100 * short_call.quantity
    
    assert short_call.margin_required == round(expected_margin, 2)

    # Test margin for different moneyness levels
    test_cases = [
        # (underlying, strike, option_type, expected_otm)
        (100.0, 90.0, OptionsType.PUT, 10.0),   # OTM put
        (100.0, 110.0, OptionsType.PUT, 0.0),   # ITM put
        (100.0, 90.0, OptionsType.CALL, 0.0),   # ITM call
        (100.0, 110.0, OptionsType.CALL, 10.0), # OTM call
    ]

    for underlying, strike, option_type, expected_otm in test_cases:
        position = SingleLegOptionPosition(
            option_strategy=OptionsStrategy.SHORT_CALL if option_type == OptionsType.CALL else OptionsStrategy.SHORT_PUT,
            trade_id=4,
            quantity=1,
            option_type=option_type,
            position_side=PositionSide.SHORT,
            entry_date=date(2023, 1, 1),
            entry_price=5.0,
            strike=strike,
            expire_date=date(2023, 1, 31),
            entry_delta=0.5 if option_type == OptionsType.CALL else -0.5,
            entry_dte=30,
            underlying_entry=underlying
        )

        # Calculate expected margin
        expected_margin = (
            position.entry_price +
            max(
                (0.15 * underlying - expected_otm),
                (0.10 * underlying)
            )
        ) * 100 * position.quantity

        assert position.margin_required == round(expected_margin, 2), f"Margin mismatch for {option_type} with strike {strike} and underlying {underlying}"

 

@pytest.fixture(scope="module")
def setup_test_data(mock_data):
    """Fixture to set up test data including mock data and test positions."""
    data = mock_data

    # Create test positions
    p_bid, p_ask = data['option_chain']['p_bid'][0], data['option_chain']['p_ask'][0]
    c_bid, c_ask = data['option_chain']['c_bid'][0], data['option_chain']['c_ask'][0]

    entry_price_call = round(float(c_bid + c_ask) / 2, 2)
    entry_price_put = round(float(p_bid + p_ask) / 2, 2)

    single_leg_long_call = SingleLegOptionPosition(
        trade_id=1,
        option_strategy=OptionsStrategy.LONG_CALL,
        quantity=1,
        option_type=OptionsType.CALL,
        position_side=PositionSide.LONG,
        entry_date=date(2023, 1, 1),
        entry_price=entry_price_call,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=0.34,
        entry_dte=30,
        underlying_entry=95.75
    )

    single_leg_short_put = SingleLegOptionPosition(
        trade_id=2,
        option_strategy=OptionsStrategy.SHORT_PUT,
        quantity=1,
        option_type=OptionsType.PUT,
        position_side=PositionSide.SHORT,
        entry_date=date(2023, 1, 1),
        entry_price=entry_price_put,
        strike=100.0,
        expire_date=date(2023, 1, 31),
        entry_delta=-0.66,
        entry_dte=30,
        underlying_entry=95.75
    )

    return data, single_leg_long_call, single_leg_short_put
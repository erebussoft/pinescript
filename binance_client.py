# binance_client.py
import config
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
from binance.enums import *
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP

logger = logging.getLogger(__name__)

# Forward declaration for type hinting if Python < 3.9
# from typing import TYPE_CHECKING
# if TYPE_CHECKING:
#     from telegram_bot import TelegramNotifier

class BinanceFuturesClient:
    def __init__(self, api_key, api_secret, telegram_notifier_instance): # Added telegram_notifier_instance
        self.client = Client(api_key, api_secret)
        self.telegram_notifier = telegram_notifier_instance # Store it
        self.client.FUTURES_URL = 'https://fapi.binance.com' # Ensure we are using futures
        logger.info("Binance Futures Client initialized.")
        self.server_time_offset = self._get_server_time_offset()
        self.exchange_info = self.client.futures_exchange_info()

    def set_leverage(self, symbol, leverage):
        try:
            logger.info(f"Setting leverage for {symbol} to {leverage}x")
            response = self.client.futures_change_leverage(symbol=symbol, leverage=leverage, timestamp=self._get_timestamp())
            logger.info(f"Leverage set for {symbol}: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception setting leverage for {symbol} to {leverage}x: {e}")
            # Example: e.code == -4048 (Leverage not changed) - might not be an error if already set
            if e.code == -4048: # "Leverage not changed"
                logger.info(f"Leverage for {symbol} already set to {leverage}x or no change needed.")
                return True # Treat as success if it's already the desired leverage
            # Add more specific error code handling if needed
            # e.g. -4003: "Quantity is not valid" if leverage is too high for current position size/balance
            # e.g. -4028: "Leverage {leverage} is not valid for {symbol}" if leverage is out of allowed range
            self.telegram_notifier.notify_error(f"Leverage Error: {symbol}", f"Failed to set leverage to {leverage}x. Code: {e.code}, Msg: {e.message}")
            return False
        except Exception as e:
            logger.error(f"Generic error setting leverage for {symbol}: {e}")
            self.telegram_notifier.notify_error(f"Leverage Error: {symbol}", f"Generic error setting leverage to {leverage}x.")
            return False

    def set_margin_type(self, symbol, margin_type):
        # margin_type should be "ISOLATED" or "CROSSED"
        try:
            logger.info(f"Setting margin type for {symbol} to {margin_type}")
            response = self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type.upper(), timestamp=self._get_timestamp())
            logger.info(f"Margin type set for {symbol}: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception setting margin type for {symbol} to {margin_type}: {e}")
            # Example: e.code == -4046 (No need to change margin type)
            if e.code == -4046: # "No need to change margin type"
                logger.info(f"Margin type for {symbol} is already {margin_type} or no change needed.")
                return True # Treat as success
            # Other codes:
            # -4059: "Margin type cannot be changed if there are open orders or positions."
            # This is a critical one. If we hit this, we should not proceed with the trade.
            if e.code == -4059:
                 logger.error(f"CRITICAL: Cannot change margin type for {symbol} to {margin_type} due to existing open orders or positions. Manual intervention likely required if change is necessary.")
                 self.telegram_notifier.notify_error(f"Margin Type Error: {symbol}", f"Cannot change margin type to {margin_type} due to open orders/positions. Manual check needed.")
                 return False # This is a hard failure for this operation
            self.telegram_notifier.notify_error(f"Margin Type Error: {symbol}", f"Failed to set margin type to {margin_type}. Code: {e.code}, Msg: {e.message}")
            return False
        except Exception as e:
            logger.error(f"Generic error setting margin type for {symbol}: {e}")
            self.telegram_notifier.notify_error(f"Margin Type Error: {symbol}", f"Generic error setting margin type to {margin_type}.")
            return False

    def _get_server_time_offset(self):
        try:
            server_time = self.client.futures_time()['serverTime']
            local_time = int(time.time() * 1000)
            offset = server_time - local_time
            logger.info(f"Server time offset: {offset} ms")
            return offset
        except Exception as e:
            logger.error(f"Error getting server time: {e}")
            return 0

    def _get_timestamp(self):
        return int(time.time() * 1000 + self.server_time_offset)

    def get_symbol_info(self, symbol):
        for s_info in self.exchange_info['symbols']:
            if s_info['symbol'] == symbol:
                return s_info
        logger.warning(f"Symbol info not found for {symbol}")
        return None

    def _adjust_quantity_to_step(self, quantity, step_size):
        return (Decimal(str(quantity)).quantize(Decimal(str(step_size)), rounding=ROUND_DOWN))

    def _adjust_price_to_tick(self, price, tick_size):
        return (Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN)) # Or ROUND_NEAREST

    def get_usdt_balance(self):
        try:
            balances = self.client.futures_account_balance(timestamp=self._get_timestamp())
            for balance in balances:
                if balance['asset'] == 'USDT':
                    logger.info(f"USDT Balance: {balance['balance']}")
                    return float(balance['balance'])
            return 0.0
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception getting balance: {e}")
        except Exception as e:
            logger.error(f"Error getting USDT balance: {e}")
        return 0.0

    def get_open_positions_count(self):
        try:
            positions = self.client.futures_position_information(timestamp=self._get_timestamp())
            open_positions = [p for p in positions if float(p['positionAmt']) != 0]
            logger.info(f"Found {len(open_positions)} open positions.")
            return len(open_positions)
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception getting positions: {e}")
        except Exception as e:
            logger.error(f"Error getting open positions: {e}")
        return 0 # Or raise exception

    def calculate_position_size(self, symbol, usdt_balance, entry_price):
        if entry_price <= 0:
            logger.error("Entry price must be positive to calculate position size.")
            return None

        tradable_balance = usdt_balance * config.TRADABLE_BALANCE_RATIO
        amount_per_trade_usdt = tradable_balance / config.MAX_OPEN_TRADES

        quantity = amount_per_trade_usdt / entry_price

        symbol_info = self.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"Cannot calculate position size, symbol info not found for {symbol}")
            return None

        quantity_precision = None
        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
        if lot_size_filter:
            quantity_precision = lot_size_filter['stepSize']

        if quantity_precision:
            adjusted_quantity = self._adjust_quantity_to_step(quantity, quantity_precision)
            logger.info(f"Calculated position size for {symbol}: {quantity}, adjusted to: {adjusted_quantity} (step: {quantity_precision})")

            # Check minNotional
            min_notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
            if min_notional_filter:
                min_notional = float(min_notional_filter['notional'])
                if float(adjusted_quantity) * entry_price < min_notional:
                    logger.warning(f"Calculated notional ({float(adjusted_quantity) * entry_price}) for {symbol} is less than minNotional ({min_notional}). Cannot place order.")
                    return None # Or adjust to meet minNotional if desired and possible
            return float(adjusted_quantity)
        else:
            logger.warning(f"Could not determine quantity precision for {symbol}. Using unadjusted quantity: {quantity}")
            return quantity


    def place_futures_order(self, symbol, side, quantity, price=None, stop_price=None, order_type=None):
        symbol_info = self.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"Cannot place order, symbol info not found for {symbol}")
            return None

        price_precision = None
        tick_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
        if tick_size_filter:
            price_precision = tick_size_filter['tickSize']

        params = {
            'symbol': symbol,
            'side': side, # 'BUY' or 'SELL'
            'quantity': quantity,
            'timestamp': self._get_timestamp()
        }

        if order_type:
            params['type'] = order_type
        else: # Default to LIMIT if price provided, else MARKET
            params['type'] = FUTURE_ORDER_TYPE_LIMIT if price else FUTURE_ORDER_TYPE_MARKET

        if params['type'] == FUTURE_ORDER_TYPE_LIMIT:
            if not price:
                logger.error("Price is required for LIMIT order.")
                return None
            if price_precision:
                params['price'] = self._adjust_price_to_tick(price, price_precision)
            else:
                params['price'] = price
            params['timeInForce'] = TIME_IN_FORCE_GTC # Good Till Cancelled

        if params['type'] in [FUTURE_ORDER_TYPE_STOP_MARKET, FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET]:
            if not stop_price:
                logger.error("Stop price is required for STOP_MARKET or TAKE_PROFIT_MARKET orders.")
                return None
            if price_precision:
                 params['stopPrice'] = self._adjust_price_to_tick(stop_price, price_precision)
            else:
                params['stopPrice'] = stop_price
            params['reduceOnly'] = False # For initial SL it's not reduceOnly. For TP it might be.

        # For STOP or TAKE_PROFIT orders (non-market), price is also needed.
        # FUTURE_ORDER_TYPE_STOP, FUTURE_ORDER_TYPE_TAKE_PROFIT

        logger.info(f"Placing order with params: {params}")
        try:
            # Ensure leverage is set if needed (usually per symbol, once)
            # self.client.futures_change_leverage(symbol=symbol, leverage=config.LEVERAGE, timestamp=self._get_timestamp())
            # Ensure margin type is set if needed (ISOLATED or CROSSED)
            # self.client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED', timestamp=self._get_timestamp())

            order = self.client.futures_create_order(**params)
            logger.info(f"Order placed successfully: {order}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception placing order: {e.message} (Code: {e.code}) - Params: {params}")
            # Example: Handle margin errors, e.g. e.code == -2019 (Margin is insufficient.)
        except BinanceOrderException as e:
            logger.error(f"Binance Order Exception placing order: {e} - Params: {params}")
        except Exception as e:
            logger.error(f"Generic error placing order: {e} - Params: {params}")
        return None

    def create_entry_order(self, symbol, signal_type, entry_price, quantity):
        side = SIDE_BUY if signal_type == 'long' else SIDE_SELL
        order_type = config.ORDER_TYPES.get('entry', 'LIMIT').upper() # Default to LIMIT

        if order_type == 'LIMIT':
            return self.place_futures_order(symbol, side, quantity, price=entry_price, order_type=FUTURE_ORDER_TYPE_LIMIT)
        elif order_type == 'MARKET':
            # Market order doesn't use entry_price directly for placement, but useful for SL calc
            return self.place_futures_order(symbol, side, quantity, order_type=FUTURE_ORDER_TYPE_MARKET)
        else:
            logger.error(f"Unsupported entry order type: {order_type}")
            return None

    def create_stop_loss_order(self, symbol, signal_type, entry_price, quantity_for_sl):
        sl_pct = config.STOP_LOSS

        if signal_type == 'long':
            side = SIDE_SELL
            stop_price = entry_price * (1 - sl_pct)
        else: # short
            side = SIDE_BUY
            stop_price = entry_price * (1 + sl_pct)

        stop_order_type_str = config.ORDER_TYPES.get('stoploss', 'MARKET').upper() # Default to MARKET (STOP_MARKET)

        binance_stop_order_type = None
        if stop_order_type_str == 'MARKET': # This means STOP_MARKET for Binance
            binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET
        elif stop_order_type_str == 'LIMIT': # This means STOP (which is a limit order after stop price is hit)
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP
             # For a STOP LIMIT order, you'd also need a 'price' param (the limit price for the stop)
             # For simplicity, we'll use STOP_MARKET as per 'stoploss_on_exchange': true
             logger.warning("STOP LIMIT SL orders require a limit price. Defaulting to STOP_MARKET behavior if not provided.")
             # For now, only STOP_MARKET is fully implemented here.
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET


        if not binance_stop_order_type:
            logger.error(f"Unsupported stoploss order type: {stop_order_type_str}")
            return None

        logger.info(f"Creating SL for {symbol}: side={side}, stop_price={stop_price}, entry_price={entry_price}, quantity={quantity_for_sl}")

        # For STOP_MARKET, the 'price' param is not used. 'stopPrice' is the trigger.
        sl_order = self.place_futures_order(symbol, side, quantity_for_sl,
                                            stop_price=stop_price,
                                            order_type=binance_stop_order_type)
        if sl_order:
            logger.info(f"Stop loss order for {symbol} placed: {sl_order}")
        else:
            logger.error(f"Failed to place stop loss order for {symbol}")
        return sl_order

    def close_position_market(self, symbol, position_amt_str):
        position_amt = float(position_amt_str)
        if position_amt == 0:
            logger.info(f"No position to close for {symbol}")
            return None

        side = SIDE_SELL if position_amt > 0 else SIDE_BUY # If long, sell to close. If short, buy to close.
        quantity = abs(position_amt)

        logger.info(f"Attempting to close {quantity} of {symbol} with a MARKET order (side: {side})")
        return self.place_futures_order(symbol, side, quantity, order_type=FUTURE_ORDER_TYPE_MARKET)

    def get_open_position_for_symbol(self, symbol):
        try:
            positions = self.client.futures_position_information(symbol=symbol, timestamp=self._get_timestamp())
            for p in positions:
                if p['symbol'] == symbol and float(p['positionAmt']) != 0:
                    logger.info(f"Found open position for {symbol}: {p}")
                    return p
            logger.info(f"No open position found for {symbol}")
            return None
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception getting position for {symbol}: {e}")
        except Exception as e:
            logger.error(f"Error getting position for {symbol}: {e}")
        return None

# Example usage (for testing this module directly)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("Testing BinanceFuturesClient...")

    # Ensure config.py has dummy keys if you run this directly, or real (testnet) keys
    if config.BINANCE_API_KEY == "YOUR_BINANCE_API_KEY" or config.BINANCE_API_SECRET == "YOUR_BINANCE_API_SECRET":
        logger.warning("Using placeholder API keys. Live tests will fail.")
        # exit() # Uncomment to prevent running with placeholder keys

    # It's highly recommended to use Binance Testnet for development and testing.
    # client.API_URL = 'https://testnet.binance.vision/api'
    # client.FUTURES_URL = 'https://testnet.binancefuture.com'

    futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

    # Test connection and time sync
    logger.info(f"Server time offset: {futures_client.server_time_offset} ms")

    # Test get balance
    usdt_balance = futures_client.get_usdt_balance()
    logger.info(f"Current USDT balance: {usdt_balance}")

    # Test get open positions count
    open_positions_count = futures_client.get_open_positions_count()
    logger.info(f"Current open positions: {open_positions_count}")

    # Test symbol info and calculations (use a valid futures symbol)
    test_symbol = "BTCUSDT" # Make sure this is in config.TRADING_PAIRS
    if test_symbol not in config.TRADING_PAIRS:
        logger.warning(f"{test_symbol} not in TRADING_PAIRS, some tests might be misleading.")

    symbol_info = futures_client.get_symbol_info(test_symbol)
    if symbol_info:
        logger.info(f"Symbol info for {test_symbol}: Retrieved")
        # logger.info(f"Symbol info for {test_symbol}: {symbol_info}") # Very verbose

        # Test position size calculation
        # Ensure entry_price is realistic for the test_symbol
        # Example: current BTC price is $60000
        # test_entry_price = 60000
        # if usdt_balance > 0 and test_entry_price > 0:
        #     calculated_size = futures_client.calculate_position_size(test_symbol, usdt_balance, test_entry_price)
        #     logger.info(f"Calculated position size for {test_symbol} at ${test_entry_price}: {calculated_size}")
        # else:
        #    logger.warning("Cannot test position size calculation without balance or valid entry price.")
    else:
        logger.error(f"Could not get symbol info for {test_symbol}. Further tests involving this symbol might fail.")

    # --- Test order placement (CAUTION: USES REAL OR TESTNET FUNDS) ---
    # Ensure you are on TESTNET or using very small amounts if on live.
    # And that the symbol is correctly configured (leverage, margin type).
    #
    # Example: Place a small test order
    # test_entry_price = 60000 # A hypothetical price for BTCUSDT
    # test_quantity = 0.001 # A small quantity of BTC
    #
    # if symbol_info and calculated_size: # Use calculated_size if available and valid
    #    test_quantity = calculated_size
    #
    # if open_positions_count < config.MAX_OPEN_TRADES and test_quantity > 0:
    #     logger.info(f"Attempting to place a test LIMIT BUY order for {test_quantity} {test_symbol} at ${test_entry_price * 0.95}") # Buy 5% below hypothetical price
    #     # Ensure price is adjusted to tick size for limit orders
    #     adjusted_test_price = futures_client._adjust_price_to_tick(test_entry_price * 0.95, symbol_info['filters'][0]['tickSize']) # Assuming price filter is first
    #
    #     entry_order = futures_client.create_entry_order(test_symbol, 'long', float(adjusted_test_price), test_quantity)
    #     if entry_order:
    #         logger.info(f"Test entry order placed: {entry_order}")
    #         # Example: Place a stop loss for this test order
    #         # The quantity for SL should match the quantity of the position opened.
    #         # If order is FILLED immediately (e.g. market order or aggressive limit), you can get actual entry price from order response.
    #         # For a GTC LIMIT order, it might not fill immediately. SL setup needs filled position info.
    #         # For this direct test, we assume it fills at adjusted_test_price for SL calculation.
    #         sl_order = futures_client.create_stop_loss_order(test_symbol, 'long', float(adjusted_test_price), test_quantity)
    #         if sl_order:
    #             logger.info(f"Test SL order placed: {sl_order}")
    #     else:
    #         logger.error("Test entry order failed.")
    # else:
    #    logger.warning(f"Skipping test order placement. Open positions: {open_positions_count}, Max: {config.MAX_OPEN_TRADES}, Test Qty: {test_quantity}")

    # Test fetching a specific open position
    # open_pos_btc = futures_client.get_open_position_for_symbol(test_symbol)
    # if open_pos_btc:
    #    logger.info(f"Open position for {test_symbol}: Amount {open_pos_btc['positionAmt']}")
        # Test closing this position
        # close_order = futures_client.close_position_market(test_symbol, open_pos_btc['positionAmt'])
        # if close_order:
        #    logger.info(f"Market close order for {test_symbol} placed: {close_order}")
    # else:
    #    logger.info(f"No open position found for {test_symbol} to test closing.")

    logger.info("BinanceFuturesClient testing finished.")

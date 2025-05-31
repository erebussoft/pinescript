import config # Ensure config is imported first
import logging
from flask import Flask, request, jsonify
import json
import time
import threading # Added for TSL
# import copy # Not strictly needed if manage_trailing_stops iterates over list(keys)
from trailing_stop_manager import manage_trailing_stops # Added for TSL
from binance_client import BinanceFuturesClient
from telegram_bot import TelegramNotifier

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global variables
futures_client = None
telegram_notifier = None
active_bot_trades = {} # Stores active trades managed by this bot instance
initialized_symbols_settings = set() # Tracks symbols where leverage/margin have been set this session
# active_trades_lock = threading.Lock() # Optional: for more complex dict manipulations if needed

def initialize_services():
    global futures_client, telegram_notifier
    logger.info("Initializing services...")
    telegram_notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID) # Init this first for error reporting
    futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, telegram_notifier)

    logger.info("Checking Binance connection...")
    balance = futures_client.get_usdt_balance()
    if balance is None or (balance == 0.0 and config.BINANCE_API_KEY != "YOUR_BINANCE_API_KEY"):
        logger.error("Failed to connect to Binance or retrieve balance. Check API keys, permissions, or network.")
        if telegram_notifier.enabled:
             telegram_notifier.notify_error("Bot Service FATAL Error", "Failed to connect to Binance or retrieve balance. Bot cannot start trading.")
    else:
        logger.info(f"Binance connection successful. USDT Balance: {balance}")
        if telegram_notifier.enabled:
            telegram_notifier.send_message("🤖 Trading Bot Server Started Successfully\n🟢 Listening for webhook signals.")
    logger.info("Services initialized.")

def handle_trade_signal(data):
    global futures_client, telegram_notifier, active_bot_trades, initialized_symbols_settings
    if not futures_client or not telegram_notifier:
        logger.error("Services not initialized. Cannot handle trade signal.")
        return

    signal_type = data['signal_type']
    symbol = data['ticker']
    entry_price = float(data['close_price'])

    logger.info(f"Processing {signal_type} signal for {symbol} at {entry_price}")

    open_positions_count = futures_client.get_open_positions_count()
    if open_positions_count is not None and open_positions_count >= config.MAX_OPEN_TRADES:
        message = f"Max open trades ({config.MAX_OPEN_TRADES}) reached. Ignoring {signal_type} signal for {symbol}."
        logger.warning(message)
        if telegram_notifier.enabled: telegram_notifier.send_message(f"⚠️ {message}")
        return

    if symbol in active_bot_trades:
        message = f"A trade for {symbol} is already being managed by the bot. Ignoring new {signal_type} signal."
        logger.warning(message)
        return

    existing_position = futures_client.get_open_position_for_symbol(symbol)
    if existing_position and float(existing_position.get('positionAmt', 0)) != 0:
        message = f"An open position for {symbol} (Amt: {existing_position['positionAmt']}) already exists on Binance. Bot will not open a new trade."
        logger.warning(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error(f"Conflict Warning: {symbol}", message)
        return

    if symbol not in initialized_symbols_settings:
        logger.info(f"Configuring {symbol} for leverage {config.LEVERAGE}x and margin type {config.MARGIN_TYPE}...")
        leverage_ok = futures_client.set_leverage(symbol, config.LEVERAGE)
        if not leverage_ok:
            message = f"Failed to set leverage for {symbol}. Cannot proceed with trade."
            logger.error(message)
            return
        margin_type_ok = futures_client.set_margin_type(symbol, config.MARGIN_TYPE)
        if not margin_type_ok:
            message = f"Failed to set margin type for {symbol}. Cannot proceed with trade."
            logger.error(message)
            return
        logger.info(f"Successfully set leverage and margin type for {symbol}.")
        initialized_symbols_settings.add(symbol)
    else:
        logger.info(f"Leverage and margin type already configured for {symbol} in this session.")

    usdt_balance = futures_client.get_usdt_balance()
    if usdt_balance is None or usdt_balance == 0:
        message = f"Cannot calculate position size for {symbol}. USDT Balance is zero or unavailable."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Balance Error", message)
        return

    quantity = futures_client.calculate_position_size(symbol, usdt_balance, entry_price)
    if not quantity or quantity <= 0:
        message = f"Calculated quantity for {symbol} is zero or invalid ({quantity}). Cannot place trade."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Sizing Error", message)
        return

    logger.info(f"Attempting to place {signal_type} order for {quantity} of {symbol} at {entry_price}")
    entry_order = futures_client.create_entry_order(symbol, signal_type, entry_price, quantity)

    if not entry_order or 'orderId' not in entry_order:
        message = f"Failed to place entry order for {symbol} ({signal_type})."
        logger.error(message)
        # Notification is handled by create_entry_order or underlying methods if telegram_notifier is passed & used
        return

    logger.info(f"Entry order for {symbol} placed successfully: {entry_order}")

    # TODO: Query actual fill price of entry_order for more precise P&L and TSL calculations.
    # This is a CRITICAL TODO for accuracy. For now, using entry_price from webhook.
    actual_filled_entry_price = entry_price

    sl_order = futures_client.create_stop_loss_order(symbol, signal_type, actual_filled_entry_price, quantity)
    if not sl_order or 'orderId' not in sl_order:
        sl_failure_message = f"Entry order for {symbol} placed (ID: {entry_order['orderId']}), but FAILED to place stop-loss. MANUAL INTERVENTION REQUIRED."
        logger.error(sl_failure_message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("CRITICAL: SL Order Failed", sl_failure_message)
        return

    logger.info(f"Stop-loss order for {symbol} placed successfully: {sl_order}")
    initial_sl_price = float(sl_order.get('stopPrice', 0.0))
    if initial_sl_price == 0.0:
        logger.error(f"CRITICAL: Stop price not found in SL order response for {symbol}. SL might not be correctly placed or fetched.")
        if telegram_notifier.enabled: telegram_notifier.notify_error(f"SL Price Missing: {symbol}", "Initial SL price from order response is zero. Check order placement.")

    if telegram_notifier.enabled:
        telegram_notifier.notify_trade_entry(symbol, signal_type, actual_filled_entry_price, quantity, initial_sl_price,
                                             notes=f"Entry Order ID: {entry_order['orderId']}\nSL Order ID: {sl_order['orderId']}")

    active_bot_trades[symbol] = {
        'entry_order_id': entry_order['orderId'],
        'sl_order_id': sl_order['orderId'],
        'current_sl_price': initial_sl_price,
        'entry_price': actual_filled_entry_price,
        'quantity': quantity,
        'signal_type': signal_type,
        'status': "open",
        'trailing_active': False,
        'highest_price_since_trailing_activation': actual_filled_entry_price if signal_type == 'long' else 0.0,
        'lowest_price_since_trailing_activation': actual_filled_entry_price if signal_type == 'short' else float('inf'),
        'timestamp': time.time()
    }
    logger.info(f"Trade {symbol} added to active_bot_trades. Details: {active_bot_trades[symbol]}")


@app.route('/webhook', methods=['POST'])
def webhook():
    logger.info("Webhook received!")
    try:
        data_str = request.get_data(as_text=True)
        logger.debug(f"Raw webhook data: {data_str}")
        data = json.loads(data_str)
        logger.info(f"Parsed webhook data: {data}")

        required_fields = ["signal_type", "ticker", "close_price", "exchange", "interval"]
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing field: {field} in webhook data.")
                return jsonify({"status": "error", "message": f"Missing field: {field}"}), 400

        if data["signal_type"] not in ["long", "short"]:
            logger.warning(f"Invalid signal_type: {data['signal_type']}")
            return jsonify({"status": "error", "message": "Invalid signal_type"}), 400

        if str(data["interval"]) != config.EXPECTED_WEBHOOK_INTERVAL:
            logger.warning(f"Invalid interval: {data['interval']}. Expected {config.EXPECTED_WEBHOOK_INTERVAL}.")
            return jsonify({"status": "error", "message": f"Invalid interval. Expected {config.EXPECTED_WEBHOOK_INTERVAL}."}), 400

        if not data["exchange"] or not data["exchange"].upper().startswith("BINANCE"):
            logger.warning(f"Invalid exchange: {data['exchange']}. Expected to start with BINANCE.")
            return jsonify({"status": "error", "message": f"Invalid exchange. Expected BINANCE."}), 400

        if data["ticker"] not in config.TRADING_PAIRS:
            logger.warning(f"Ticker {data['ticker']} not in TRADING_PAIRS list.")
            return jsonify({"status": "error", "message": f"Ticker {data['ticker']} not configured."}), 400

        logger.info(f"Webhook validated for ticker: {data['ticker']}, signal: {data['signal_type']}")
        handle_trade_signal(data)
        return jsonify({"status": "success", "message": "Webhook received"}), 200

    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from data: {request.get_data(as_text=True)}")
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        if telegram_notifier and telegram_notifier.enabled:
             telegram_notifier.notify_error("Webhook Processing Error", str(e))
        return jsonify({"status": "error", "message": "Internal server error"}), 500

def trailing_stop_loop():
    global futures_client, telegram_notifier, active_bot_trades #, active_trades_lock
    logger.info("Trailing stop manager thread started.")
    while True:
        try:
            # Pass arguments to manage_trailing_stops
            manage_trailing_stops(futures_client, telegram_notifier, active_bot_trades) # Pass active_trades_lock if using
        except Exception as e:
            logger.error(f"Exception in trailing_stop_loop: {e}", exc_info=True)
            if telegram_notifier and telegram_notifier.enabled:
                 telegram_notifier.notify_error("TSL Loop Exception", str(e))

        sleep_duration = config.TRAILING_STOP_CHECK_INTERVAL_SECONDS
        if sleep_duration < 10:
            logger.warning(f"TRAILING_STOP_CHECK_INTERVAL_SECONDS ({sleep_duration}s) is very low. Setting to 10s minimum for safety.")
            sleep_duration = 10
        time.sleep(sleep_duration)

if __name__ == "__main__":
    initialize_services() # Initialize global clients

    if config.TRAILING_STOP:
        if futures_client and telegram_notifier: # Ensure clients are initialized before starting TSL
            ts_thread = threading.Thread(target=trailing_stop_loop, daemon=True)
            ts_thread.start()
            logger.info(f"Trailing stop manager thread initiated (check interval: {config.TRAILING_STOP_CHECK_INTERVAL_SECONDS}s).")
        else:
            logger.error("Cannot start Trailing Stop Manager: Binance client or Telegram notifier not initialized.")

    # Use Gunicorn or Waitress for production
    app.run(host='0.0.0.0', port=5000, debug=False) # debug=False for production

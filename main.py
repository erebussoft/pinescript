import config # Önce config dosyasının import edildiğinden emin olun
import logging
from flask import Flask, request, jsonify
import json
import time
import threading # TSL için eklendi
# import copy # Artık manage_trailing_stops anahtarların listesi üzerinde yinelendiği için kesinlikle gerekli değil
from trailing_stop_manager import manage_trailing_stops # TSL için eklendi
from redis_client import RedisClient
from binance_client import BinanceFuturesClient
from telegram_bot import TelegramNotifier

# Günlük kaydını yapılandır
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global değişkenler
futures_client = None
telegram_notifier = None
redis_client = None # Bunu ekle
# active_bot_trades = {} # Bu satırı kaldır
initialized_symbols_settings = set() # Bu oturumda kaldıraç/marjin ayarlanan sembolleri izler
# active_trades_lock = threading.Lock() # İsteğe bağlı: gerekirse daha karmaşık sözlük manipülasyonları için

def initialize_services():
    global futures_client, telegram_notifier, redis_client
    logger.info("Initializing services...")
    telegram_notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID) # Hata raporlama için bunu önce başlat
    futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, telegram_notifier)

    redis_client = RedisClient()
    if not redis_client.is_connected():
        # Bu kritik bir başarısızlıktır, bot Redis olmadan çalışamaz
        message = "KRİTİK: Redis'e bağlanılamadı. Bot işlemleri yönetemez."
        logger.error(message)
        if telegram_notifier and telegram_notifier.enabled:
            telegram_notifier.notify_error("Bot Servisi KRİTİK Hata", message)
        # İstenen davranışa bağlı olarak, çıkmak veya daha fazla işlemi engellemek isteyebilirsiniz
        # Şimdilik, günlük kaydı yapacak ve devam etmeye çalışacak, ancak Redis gerektiren işlemler başarısız olacaktır.
    else:
        logger.info("Redis client initialized and connected.")

    logger.info("Checking Binance connection...")
    balance = futures_client.get_usdt_balance()
    if balance is None or (balance == 0.0 and config.BINANCE_API_KEY != "YOUR_BINANCE_API_KEY"):
        logger.error("Failed to connect to Binance or retrieve balance. Check API keys, permissions, or network.")
        if telegram_notifier.enabled:
             telegram_notifier.notify_error("Bot Servisi KRİTİK Hata", "Binance'e bağlanılamadı veya bakiye alınamadı. Bot ticarete başlayamaz.")
    else:
        logger.info(f"Binance connection successful. USDT Balance: {balance}")
        if telegram_notifier.enabled:
            telegram_notifier.send_message("🤖 Trading Bot Sunucusu Başarıyla Başlatıldı\n🟢 Webhook sinyalleri dinleniyor.")
    logger.info("Services initialized.")

def handle_trade_signal(data):
    global futures_client, telegram_notifier, initialized_symbols_settings # active_bot_trades kaldırıldı
    if not futures_client or not telegram_notifier or not redis_client: # redis_client kontrolü eklendi
        logger.error("Services not initialized (or Redis not connected). Cannot handle trade signal.")
        return

    signal_type = data['signal_type']
    symbol = data['ticker']
    entry_price = float(data['close_price'])

    logger.info(f"Processing {signal_type} signal for {symbol} at {entry_price}")

    open_positions_count = futures_client.get_open_positions_count()
    if open_positions_count is not None and open_positions_count >= config.MAX_OPEN_TRADES:
        message = f"Maksimum açık işlem sayısına ({config.MAX_OPEN_TRADES}) ulaşıldı. {symbol} için {signal_type} sinyali yok sayılıyor."
        logger.warning(message)
        if telegram_notifier.enabled: telegram_notifier.send_message(f"⚠️ {message}")
        return

    if redis_client.get_trade(symbol):
        message = f"{symbol} için bir işlem zaten yönetiliyor (Redis'te bulundu). Yeni {signal_type} sinyali yok sayılıyor." # Log message, not sent to Telegram.
        logger.warning(message)
        return

    existing_position = futures_client.get_open_position_for_symbol(symbol)
    if existing_position and float(existing_position.get('positionAmt', 0)) != 0:
        message = f"{symbol} için (Miktar: {existing_position['positionAmt']}) açık bir pozisyon zaten Binance'te mevcut. Bot yeni bir işlem açmayacak."
        logger.warning(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error(f"Çakışma Uyarısı: {symbol}", message)
        return

    if symbol not in initialized_symbols_settings:
        logger.info(f"Configuring {symbol} for leverage {config.LEVERAGE}x and margin type {config.MARGIN_TYPE}...")
        leverage_ok = futures_client.set_leverage(symbol, config.LEVERAGE)
        if not leverage_ok:
            message = f"Failed to set leverage for {symbol}. Cannot proceed with trade." # Log message
            logger.error(message)
            return
        margin_type_ok = futures_client.set_margin_type(symbol, config.MARGIN_TYPE)
        if not margin_type_ok:
            message = f"Failed to set margin type for {symbol}. Cannot proceed with trade." # Log message
            logger.error(message)
            return
        logger.info(f"Successfully set leverage and margin type for {symbol}.")
        initialized_symbols_settings.add(symbol)
    else:
        logger.info(f"Leverage and margin type already configured for {symbol} in this session.")

    usdt_balance = futures_client.get_usdt_balance()
    if usdt_balance is None or usdt_balance == 0:
        message = f"{symbol} için pozisyon büyüklüğü hesaplanamıyor. USDT Bakiyesi sıfır veya kullanılamıyor."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Bakiye Hatası", message)
        return

    quantity = futures_client.calculate_position_size(symbol, usdt_balance, entry_price)
    if not quantity or quantity <= 0:
        message = f"{symbol} için hesaplanan miktar sıfır veya geçersiz ({quantity}). İşlem yapılamaz."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Boyutlandırma Hatası", message)
        return

    logger.info(f"Attempting to place {signal_type} order for {quantity} of {symbol} at {entry_price}")
    entry_order = futures_client.create_entry_order(symbol, signal_type, entry_price, quantity)

    if not entry_order or 'orderId' not in entry_order:
        message = f"Failed to place entry order for {symbol} ({signal_type})." # Log message
        logger.error(message)
        # Bildirim, telegram_notifier geçirilirse ve kullanılırsa create_entry_order veya temel yöntemler tarafından yönetilir
        return

    logger.info(f"Entry order for {symbol} placed successfully: {entry_order}")

    # TODO: Daha kesin P&L ve TSL hesaplamaları için entry_order'ın gerçek dolum fiyatını sorgulayın.
    # Bu, doğruluk için KRİTİK bir TODO'dur. Şimdilik webhook'tan gelen giriş fiyatı kullanılıyor.
    actual_filled_entry_price = entry_price

    sl_order = futures_client.create_stop_loss_order(symbol, signal_type, actual_filled_entry_price, quantity)
    if not sl_order or 'orderId' not in sl_order:
        sl_failure_message = f"{symbol} için giriş emri verildi (ID: {entry_order['orderId']}), ancak zarar durdurma emri VERİLEMEDİ. MANUEL MÜDAHALE GEREKLİ."
        logger.error(sl_failure_message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("KRİTİK: ZD Emir Hatası", sl_failure_message)
        return

    logger.info(f"Stop-loss order for {symbol} placed successfully: {sl_order}")
    initial_sl_price = float(sl_order.get('stopPrice', 0.0))
    if initial_sl_price == 0.0:
        logger.error(f"CRITICAL: Stop price not found in SL order response for {symbol}. SL might not be correctly placed or fetched.")
        if telegram_notifier.enabled: telegram_notifier.notify_error(f"ZD Fiyatı Eksik: {symbol}", "Emir yanıtından gelen başlangıç ZD fiyatı sıfır. Emir yerleşimini kontrol edin.")

    if telegram_notifier.enabled:
        telegram_notifier.notify_trade_entry(symbol, signal_type, actual_filled_entry_price, quantity, initial_sl_price,
                                             notes=f"Giriş Emir ID: {entry_order['orderId']}\nZD Emir ID: {sl_order['orderId']}")

    trade_details = {
        'entry_order_id': entry_order['orderId'],
        'sl_order_id': sl_order['orderId'],
        'current_sl_price': initial_sl_price,
        'entry_price': actual_filled_entry_price,
        'quantity': quantity,
        'signal_type': signal_type,
        'status': "open", # Başlangıç durumu
        'trailing_active': False,
        'highest_price_since_trailing_activation': actual_filled_entry_price if signal_type == 'long' else 0.0,
        'lowest_price_since_trailing_activation': actual_filled_entry_price if signal_type == 'short' else float('inf'),
        'timestamp': time.time()
    }
    if redis_client.set_trade(symbol, trade_details):
        logger.info(f"Trade {symbol} details stored in Redis. Details: {trade_details}")
    else:
        # Bu kritik bir sorundur, çünkü işlem açık ancak izlenmiyor.
        error_message = f"KRİTİK: Emirler verildikten sonra {symbol} işlemi Redis'e kaydedilemedi. Manuel izleme gerekli."
        logger.error(error_message)
        if telegram_notifier.enabled: # Kullanmadan önce bildirimcinin etkin olup olmadığını kontrol et
            telegram_notifier.notify_error("Redis Kayıt Hatası", error_message)
        # Bunun nasıl ele alınacağını düşünün: emirleri iptal etmeye çalışın? Şimdilik, günlük tutun ve bildirin.


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
             telegram_notifier.notify_error("Webhook İşleme Hatası", str(e))
        return jsonify({"status": "error", "message": "Internal server error"}), 500

def trailing_stop_loop():
    global futures_client, telegram_notifier, redis_client # active_bot_trades kaldırıldı, redis_client eklendi
    logger.info("Trailing stop manager thread started.")
    while True:
        try:
            # Argümanları manage_trailing_stops'a geçir
            manage_trailing_stops(futures_client, telegram_notifier, redis_client) # redis_client'ı geçir
        except Exception as e:
            logger.error(f"Exception in trailing_stop_loop: {e}", exc_info=True)
            if telegram_notifier and telegram_notifier.enabled:
                 telegram_notifier.notify_error("TSL Döngü İstisnası", str(e))

        sleep_duration = config.TRAILING_STOP_CHECK_INTERVAL_SECONDS
        if sleep_duration < 10:
            logger.warning(f"TRAILING_STOP_CHECK_INTERVAL_SECONDS ({sleep_duration}s) is very low. Setting to 10s minimum for safety.")
            sleep_duration = 10
        time.sleep(sleep_duration)

if __name__ == "__main__":
    initialize_services() # Global istemcileri başlat

    if config.TRAILING_STOP:
        if futures_client and telegram_notifier and redis_client and redis_client.is_connected(): # İstemcilerin başlatıldığından ve Redis'in bağlı olduğundan emin olun
            ts_thread = threading.Thread(target=trailing_stop_loop, daemon=True)
            ts_thread.start()
            logger.info(f"Trailing stop manager thread initiated (check interval: {config.TRAILING_STOP_CHECK_INTERVAL_SECONDS}s).")
        else:
            logger.error("Cannot start Trailing Stop Manager: Binance client, Telegram notifier, or Redis client not initialized/connected.")

    # Üretim için Gunicorn veya Waitress kullanın
    app.run(host='0.0.0.0', port=5000, debug=False) # üretim için debug=False

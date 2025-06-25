import config # Önce config dosyasının import edildiğinden emin olun
import logging
from flask import Flask, request, jsonify
import json
import time
import threading # TSL için eklendi
# import copy # Artık manage_trailing_stops anahtarların listesi üzerinde yinelendiği için kesinlikle gerekli değil
from trailing_stop_manager import manage_trailing_stops # TSL için eklendi
from database_handler import DatabaseHandler # RedisClient yerine DatabaseHandler import edildi
from binance_client import BinanceFuturesClient
from telegram_bot import TelegramNotifier

# Günlük kaydını yapılandır
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global değişkenler
futures_client = None
telegram_notifier = None
db_handler = None # redis_client -> db_handler olarak değiştirildi
synchronization_successful = False
initialized_symbols_settings = set()
app = Flask(__name__) # Flask app instance

def initialize_services():
    global futures_client, telegram_notifier, db_handler, synchronization_successful # redis_client -> db_handler
    synchronization_successful = False # Fonksiyon başında bayrağı başlat
    logger.info("Initializing services...")
    telegram_notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID) # Hata raporlama için bunu önce başlat
    futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, telegram_notifier)

    try:
        db_handler = DatabaseHandler(config.DATABASE_FILE)
        logger.info(f"Veritabanı yöneticisi '{config.DATABASE_FILE}' ile başarıyla başlatıldı.")
    except Exception as e:
        message = f"KRİTİK: Veritabanı başlatılamadı ({config.DATABASE_FILE}). Hata: {e}. Bot işlemleri yönetemez."
        logger.error(message, exc_info=True)
        if telegram_notifier and telegram_notifier.enabled:
            telegram_notifier.notify_error("Bot Servisi KRİTİK Hata", message)
        # synchronization_successful False kalacak ve TSL başlamayacak.
        # Botun burada tamamen durması da düşünülebilir.
        # Fonksiyonun geri kalanının çalışmaması için burada return edelim.
        return

    logger.info("Checking Binance connection...")
    balance = futures_client.get_usdt_balance() # İlk bakiye kontrolü kritik servislerin çalışıp çalışmadığını görmek için
    if balance is None: # Daha sıkı kontrol, API anahtarı demo olsa bile None dönmemeli
        logger.error("Failed to connect to Binance or retrieve balance. Check API keys, permissions, or network. Bot cannot start trading without Binance connection.")
        if telegram_notifier.enabled:
             telegram_notifier.notify_error("Bot Servisi KRİTİK Hata", "Binance'e bağlanılamadı veya bakiye alınamadı. Bot ticarete başlayamaz.")
        # synchronization_successful False kalır
    else:
        logger.info(f"Binance connection successful. USDT Balance: {balance if balance is not None else 'N/A'}")
        # Başlangıç senkronizasyonunu burada yap
        if db_handler and db_handler.conn and futures_client: # db_handler.conn bağlantının varlığını kontrol eder
            logger.info("Başlangıç: Binance ve Veritabanı arasında pozisyon senkronizasyonu başlatılıyor...")
            try:
                open_binance_positions_map = futures_client.get_all_open_positions_detailed()
                logger.info(f"Binance'te {len(open_binance_positions_map)} açık pozisyon bulundu: {list(open_binance_positions_map.keys())}")

                tracked_db_trades = db_handler.get_all_trades()
                logger.info(f"Veritabanında {len(tracked_db_trades)} takip edilen işlem bulundu: {list(tracked_db_trades.keys())}")

                # Senaryo 1: Binance'te pozisyon var, Veritabanında YOK (Yönetilmeyen Pozisyon)
                for symbol, binance_pos_details in open_binance_positions_map.items():
                    if symbol not in tracked_db_trades:
                        error_msg_detail = (f"Sembol: {symbol}, Miktar: {binance_pos_details['quantity']}, "
                                            f"Binance Giriş Fiyatı: {binance_pos_details['entry_price']}. "
                                            f"Bu pozisyon bot tarafından aktif olarak yönetilmiyor.")
                        logger.critical(f"KRİTİK: Binance'te yönetilmeyen açık pozisyon bulundu! {error_msg_detail}")
                        if telegram_notifier.enabled:
                            telegram_notifier.notify_unmanaged_position(
                                symbol,
                                binance_pos_details['quantity'],
                                binance_pos_details['entry_price'],
                                notes="Bu pozisyon bot tarafından İZLENMİYOR. Lütfen manuel olarak kontrol edin."
                            )

                # Senaryo 2: Veritabanında işlem var, Binance'te YOK (Eski Veritabanı Kaydı)
                for symbol, db_trade_details in list(tracked_db_trades.items()): # .items() kopyası üzerinde yineleme
                    if symbol not in open_binance_positions_map:
                        logger.warning(f"Veritabanında takip edilen {symbol} işlemi Binance'te açık değil. Muhtemelen bot kapalıyken kapatıldı. Veritabanından kaldırılıyor.")
                        db_handler.delete_trade(symbol)
                        if telegram_notifier.enabled:
                            telegram_notifier.notify_stale_trade_removed(
                                symbol,
                                notes=f"Pozisyon Binance'te bulunamadı. {symbol} takipten çıkarıldı."
                            )
                    elif symbol in open_binance_positions_map:
                         logger.info(f"Aktif işlem {symbol} hem Binance'te hem de Veritabanında bulundu ve senkronize. Takip devam ediyor.")
                         # İsteğe bağlı: Daha derin bir kontrol için miktarları karşılaştırın
                         # if abs(open_binance_positions_map[symbol]['quantity']) != abs(db_trade_details['quantity']):
                         #     logger.warning(f"{symbol} için Binance ve Veritabanı miktarları farklı! Binance: {open_binance_positions_map[symbol]['quantity']}, Veritabanı: {db_trade_details['quantity']}")
                         #     if telegram_notifier.enabled:
                         #         telegram_notifier.notify_error(f"Miktar Uyuşmazlığı: {symbol}", "Binance ve Veritabanı miktarları farklı. Manuel kontrol gerekli.")

                logger.info("Başlangıç pozisyon senkronizasyonu başarıyla tamamlandı.")
                synchronization_successful = True
                if telegram_notifier.enabled: # Sadece senkronizasyon başarılıysa başlangıç mesajı gönder
                    telegram_notifier.send_message("🤖 Trading Bot Sunucusu Başarıyla Başlatıldı\n🟢 Webhook sinyalleri dinleniyor.\n🔄 Pozisyonlar senkronize edildi.")

            except Exception as e:
                logger.error(f"Başlangıçta pozisyon senkronizasyonu sırasında bir hata oluştu: {e}", exc_info=True)
                if telegram_notifier.enabled:
                    telegram_notifier.notify_error(
                        "Senkronizasyon Hatası",
                        f"Bot başlarken pozisyonlar senkronize edilemedi: {str(e)}"
                    )
                # synchronization_successful False kalır (zaten başlangıçta False)
        else:
            logger.error("Veritabanı veya Futures istemcisi düzgün başlatılamadığı için başlangıç senkronizasyonu atlandı.")
            # synchronization_successful False kalır

    logger.info("Services initialized.") # Bu satırın yeri önemli, senkronizasyon sonrası olmalı

# Servisleri başlat ve TSL thread'ini ayarla (eğer etkinse)
initialize_services() # Servis başlatmayı buraya taşı

if config.TRAILING_STOP:
    # TSL thread'i yalnızca kritik servisler başlatıldıysa VE başlangıç senkronizasyonu başarılıysa başlat
    if futures_client and telegram_notifier and db_handler and db_handler.conn and synchronization_successful:
        ts_thread = threading.Thread(target=trailing_stop_loop, daemon=True)
        ts_thread.start()
        logger.info(f"Takip Eden Zarar Durdurma (TSL) yöneticisi iş parçacığı başlatıldı (kontrol aralığı: {config.TRAILING_STOP_CHECK_INTERVAL_SECONDS}s).")
    else:
        logger.error("Takip Eden Zarar Durdurma (TSL) Yöneticisi başlatılamıyor: Kritik servisler başlatılamadı VEYA başlangıç senkronizasyonu başarısız oldu.")

def handle_trade_signal(data):
    global futures_client, telegram_notifier, initialized_symbols_settings # active_bot_trades kaldırıldı
    # db_handler global olmasına rağmen, burada tekrar global olarak bildirmeye gerek yok çünkü initialize_services içinde zaten ayarlandı.
    # Ancak, None olup olmadığını kontrol etmek önemlidir.
    if not futures_client or not telegram_notifier or not db_handler or not db_handler.conn: # redis_client -> db_handler.conn
        logger.error("Servisler başlatılmadı (veya Veritabanı bağlı değil). İşlem sinyali işlenemiyor.")
        return

    signal_type = data['signal_type']
    symbol = data['ticker']
    entry_price = float(data['close_price'])

    logger.info(f"Processing {signal_type} signal for {symbol} at {entry_price}")
    existing_trade_details = db_handler.get_trade(symbol) # redis_client -> db_handler

    if existing_trade_details:
        logger.info(f"İşlemde olan bir pozisyon bulundu {symbol} Veritabanında: {existing_trade_details}") # Redis -> Veritabanında
        if existing_trade_details['signal_type'] == signal_type:
            # Same direction signal
            message = f"{symbol} için mevcut pozisyonla aynı yönde ({signal_type}) bir sinyal alındı. Sinyal yok sayılıyor."
            logger.warning(message)
            # Optional: Send to Telegram if desired
            # if telegram_notifier.enabled: telegram_notifier.send_message(f"ℹ️ {message}")
            return
        else:
            # Opposite direction signal - Reverse logic
            logger.info(f"{symbol} için mevcut pozisyona ters yönde ({signal_type}) bir sinyal alındı. Pozisyon tersine çevrilecek.")

            # 3a. Close existing position
            logger.info(f"Mevcut {existing_trade_details['signal_type']} pozisyonu kapatılıyor: {symbol}...")
            # We'll assume a function like close_trade_at_market(symbol, quantity, original_signal_type) exists or will be added to binance_client.py
            # It should return details like exit price. For now, placeholder:
            closure_details = futures_client.close_trade_at_market( # This function needs to be implemented in binance_client.py
                symbol,
                existing_trade_details['quantity'],
                existing_trade_details['signal_type']
            )

            if closure_details and closure_details.get('avgPrice'): # Check for avgPrice or other indicators of success
                logger.info(f"{symbol} pozisyonu başarıyla kapatıldı. Çıkış fiyatı: {closure_details.get('avgPrice')}")
                # 3b. Send Telegram notification for closure (using a new specific notifier method to be created)
                if telegram_notifier.enabled:
                    telegram_notifier.notify_trade_reverse_closure( # This function needs to be implemented in telegram_bot.py
                        symbol,
                        existing_trade_details['signal_type'], # original direction
                        float(closure_details.get('avgPrice')),
                        existing_trade_details['quantity'],
                        notes=f"Ters sinyal ({signal_type}) nedeniyle kapatıldı."
                    )

                # 3c. Remove old trade from Redis
                db_handler.delete_trade(symbol) # redis_client -> db_handler
                logger.info(f"{symbol} için eski işlem detayları Veritabanından silindi.") # Redis -> Veritabanından

                # IMPORTANT: Reset initialized_symbols_settings for the symbol to allow re-setting leverage/margin if needed for the new trade.
                if symbol in initialized_symbols_settings:
                    initialized_symbols_settings.remove(symbol)
                logger.info(f"{symbol} için kaldıraç/marjin ayarlarının yeniden doğrulanmasına izin verildi.")

                # 3d. Proceed to open new trade (logic continues below, as if no trade existed)
                logger.info(f"{symbol} için yeni {signal_type} pozisyonu açma işlemine devam ediliyor.")
                # Set existing_trade_details to None so the rest of the logic proceeds as a new trade
                existing_trade_details = None
            else:
                message = f"KRİTİK: {symbol} için mevcut pozisyon kapatılamadı. Yeni {signal_type} işlemi AÇILMAYACAK."
                logger.error(message)
                if telegram_notifier.enabled:
                    telegram_notifier.notify_error(f"Pozisyon Kapatma Hatası: {symbol}", message)
                return # Do not proceed to open new trade

    # If existing_trade_details was None OR if it was an opposite signal and successfully closed:
    # New placement for MAX_OPEN_TRADES check:
    if not existing_trade_details: # Only check if it's a truly new trade, not a reversal that just closed one.
        open_positions_count = futures_client.get_open_positions_count()
        if open_positions_count is not None and open_positions_count >= config.MAX_OPEN_TRADES:
            message = f"Maksimum açık işlem sayısına ({config.MAX_OPEN_TRADES}) ulaşıldı. {symbol} için {signal_type} sinyali yok sayılıyor."
            logger.warning(message)
            if telegram_notifier.enabled: telegram_notifier.send_message(f"⚠️ {message}")
            return

    # The original check for existing position on Binance (unmanaged by bot)
    if not existing_trade_details: # If we are not in a reversal flow (already handled or was not an existing bot trade)
        logger.debug(f"No prior bot-managed trade found for {symbol}. Checking Binance for unmanaged positions.")
        existing_position_on_binance = futures_client.get_open_position_for_symbol(symbol)
        if existing_position_on_binance and float(existing_position_on_binance.get('positionAmt', 0)) != 0:
            message = f"{symbol} için (Miktar: {existing_position_on_binance['positionAmt']}) açık bir pozisyon zaten Binance'te mevcut (bot tarafından yönetilmiyor). Bot yeni bir işlem açmayacak."
            logger.warning(message)
            if telegram_notifier.enabled: telegram_notifier.notify_error(f"Çakışma Uyarısı: {symbol}", message)
            return

    # Remainder of the original logic for opening a new trade starts here
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
    if db_handler.set_trade(symbol, trade_details): # redis_client -> db_handler
        logger.info(f"{symbol} işlem detayları Veritabanına kaydedildi. Detaylar: {trade_details}") # Redis -> Veritabanına
    else:
        # Bu kritik bir sorundur, çünkü işlem açık ancak izlenmiyor.
        error_message = f"KRİTİK: Emirler verildikten sonra {symbol} işlemi Veritabanına kaydedilemedi. Manuel izleme gerekli." # Redis -> Veritabanına
        logger.error(error_message)
        if telegram_notifier.enabled: # Kullanmadan önce bildirimcinin etkin olup olmadığını kontrol et
            telegram_notifier.notify_error("Veritabanı Kayıt Hatası", error_message) # Redis -> Veritabanı
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
    global futures_client, telegram_notifier, db_handler # redis_client -> db_handler
    logger.info("Trailing stop manager thread started.")
    while True:
        try:
            # Argümanları manage_trailing_stops'a geçir
            manage_trailing_stops(futures_client, telegram_notifier, db_handler) # redis_client -> db_handler
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
        # TSL thread'i yalnızca kritik servisler başlatıldıysa VE başlangıç senkronizasyonu başarılıysa başlat
        if futures_client and telegram_notifier and redis_client and redis_client.is_connected() and synchronization_successful:
            ts_thread = threading.Thread(target=trailing_stop_loop, daemon=True)
            ts_thread.start()
            logger.info(f"Takip Eden Zarar Durdurma (TSL) yöneticisi iş parçacığı başlatıldı (kontrol aralığı: {config.TRAILING_STOP_CHECK_INTERVAL_SECONDS}s).")
        else:
            logger.error("Takip Eden Zarar Durdurma (TSL) Yöneticisi başlatılamıyor: Kritik servisler başlatılamadı VEYA başlangıç senkronizasyonu başarısız oldu.")

    # Yerel geliştirme sunucusunu yalnızca doğrudan `python main.py` ile çalıştırıldığında başlat
    # Gunicorn bu bloğu çalıştırmayacak, bunun yerine 'app' Flask nesnesini kullanacaktır.
    logger.info("Flask geliştirme sunucusu başlatılıyor (yalnızca yerel test için)...")
    app.run(host='0.0.0.0', port=config.PORT, debug=False) # config.PORT kullanıldı

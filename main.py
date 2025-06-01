# main.py - Ana uygulama dosyası
import config # Yapılandırma dosyasını içe aktar (ilk sırada olmalı)
import logging
from flask import Flask, request, jsonify
import json
import time
import threading
from trailing_stop_manager import manage_trailing_stops # Takip eden zarar durdurma yöneticisi
from binance_client import BinanceFuturesClient # Binance Futures istemcisi
from telegram_bot import TelegramNotifier # Telegram bildirim gönderici

# Logging yapılandırması
# Log seviyesini config dosyasından al, belirtilmemişse veya geçersizse INFO olarak ayarla
log_level_str = getattr(config, 'LOG_LEVEL', 'INFO').upper()
numeric_level = getattr(logging, log_level_str, logging.INFO) # Sayısal log seviyesini al
logging.basicConfig(level=numeric_level, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # Logger örneği oluştur

app = Flask(__name__) # Flask uygulama örneği oluştur

# Global değişkenler
futures_client = None # Binance Futures istemcisi için global değişken
telegram_notifier = None # Telegram bildirim gönderici için global değişken
active_bot_trades = {} # Bu bot örneği tarafından yönetilen aktif işlemleri saklar. { sembol: {trade_detayları} }
initialized_symbols_settings = set() # Bu oturumda kaldıraç/marjin ayarları yapılmış sembolleri takip eder
# active_trades_lock = threading.Lock() # Karmaşık sözlük manipülasyonları için gerekirse kullanılabilir

# Servisleri başlatan fonksiyon
def initialize_services():
    """
    Gerekli servisleri (Binance istemcisi, Telegram bildirimcisi) başlatır.
    API bağlantısını kontrol eder ve başlangıç bakiyesini alır.
    """
    global futures_client, telegram_notifier
    logger.info("Servisler başlatılıyor...")
    # Hata raporlamada kullanılabilmesi için önce TelegramNotifier'ı başlat
    telegram_notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    try:
        # Binance Futures istemcisini API anahtarları ve Telegram bildirimcisi ile başlat
        futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, telegram_notifier)
        logger.info("BinanceFuturesClient başarıyla başlatıldı.")
    except Exception as e:
        logger.critical(f"BinanceFuturesClient başlatılamadı: {e}", exc_info=True)
        if telegram_notifier and telegram_notifier.enabled:
            telegram_notifier.notify_error("Bot Kritik Hata", f"Binance İstemcisi başlatılamadı: {e}. Bot başlatılamıyor.")
        raise # Uygulamanın hatalı durumda çalışmasını önlemek için hatayı yeniden yükselt

    logger.info("Binance API bağlantısı kontrol ediliyor ve başlangıç bakiyesi alınıyor...")
    balance = futures_client.get_usdt_balance() # Bu aynı zamanda bağlantıyı test eder

    # API anahtarlarının varsayılan yer tutucular olup olmadığını kontrol et
    is_default_api_key = config.BINANCE_API_KEY == "YOUR_BINANCE_API_KEY"

    if balance is None: # get_usdt_balance() fonksiyonunda bir hata olduğunu gösterir
        logger.error("Binance'ten bakiye alınamadı. API bağlantı sorunları veya izin problemleri olabilir.")
        if telegram_notifier.enabled:
            telegram_notifier.notify_error("Bot Başlangıç Hatası", "Binance'ten bakiye alınamadı. API anahtarlarını, izinleri veya ağı kontrol edin.")
    elif balance == 0.0 and not is_default_api_key:
        logger.warning("Binance bağlantısı başarılı, ancak USDT Bakiyesi 0.0.")
        if telegram_notifier.enabled:
            # Uyarı, kullanıcı daha sonra para yatırmayı planlıyorsa bu bir hata olmayabilir
            telegram_notifier.send_message("⚠️ Trading Bot Sunucusu Başlatıldı\nUSDT Bakiyesi 0.0. Lütfen işlem için fonların mevcut olduğundan emin olun.")
    elif is_default_api_key:
        logger.warning("Varsayılan yer tutucu API anahtarları kullanılıyor. Binance işlevselliği sınırlı olacak/başarısız olacaktır. Bakiye kontrolü atlandı.")
        if telegram_notifier.enabled:
            telegram_notifier.send_message("🤖 Trading Bot Sunucusu Başlatıldı (yer tutucu API anahtarlarıyla)\n🟢 Webhook sinyalleri dinleniyor.\n⚠️ API anahtarları yapılandırılana kadar Binance işlemleri başarısız olacaktır.")
    else: # Bakiye > 0 ve varsayılan anahtarlar değil
        logger.info(f"Binance bağlantısı başarılı. Başlangıç USDT Bakiyesi: {balance:.2f}")
        if telegram_notifier.enabled:
            telegram_notifier.send_message(f"🤖 Trading Bot Sunucusu Başarıyla Başlatıldı\n🟢 Webhook sinyalleri dinleniyor.\n💰 Başlangıç USDT Bakiyesi: {balance:.2f}")
    logger.info("Servislerin başlatılması tamamlandı.")

# Kar/Zarar (P&L) hesaplama fonksiyonu
def calculate_pnl(entry_price, exit_price, quantity, trade_type):
    """
    Bir işlem için Kar/Zarar (P&L) hesaplar.
    Args:
        entry_price (float): Giriş fiyatı.
        exit_price (float): Çıkış fiyatı.
        quantity (float): İşlem miktarı (pozitif olmalı).
        trade_type (str): İşlem türü ('long' veya 'short').
    Returns:
        float: Hesaplanan P&L.
    """
    abs_quantity = abs(float(quantity)) # Miktarın mutlak değerini al
    if trade_type.lower() == 'long': # Long pozisyon için P&L
        return (exit_price - entry_price) * abs_quantity
    elif trade_type.lower() == 'short': # Short pozisyon için P&L
        return (entry_price - exit_price) * abs_quantity
    logger.warning(f"P&L hesaplaması için bilinmeyen işlem türü: '{trade_type}'.")
    return 0

# Gelen işlem sinyallerini işleyen ana fonksiyon
def handle_trade_signal(data):
    """
    TradingView'den gelen webhook verilerini işler ve buna göre işlem kararları alır.
    Mantık akışı:
    1. Mevcut bot tarafından yönetilen bir işlem varsa onu yönetir (ters sinyalde kapatma vb.).
    2. Bot tarafından yönetilmeyen ama Binance'te açık bir pozisyon varsa onu yönetir.
    3. Maksimum açık işlem limitini kontrol eder.
    4. Gerekirse sembol için kaldıraç ve marjin tipini ayarlar.
    5. Pozisyon büyüklüğünü hesaplar.
    6. Giriş emrini verir.
    7. Zarar durdurma (SL) emrini verir.
    8. İşlemi aktif işlemler listesine kaydeder ve bildirim gönderir.
    """
    global futures_client, telegram_notifier, active_bot_trades, initialized_symbols_settings
    if not futures_client or not telegram_notifier:
        logger.error("Servisler başlatılmamış. İşlem sinyali işlenemiyor.")
        return

    new_signal_type = data['signal_type'].lower() # Gelen sinyal türü ('long' veya 'short')
    symbol = data['ticker'] # İşlem yapılacak sembol (örn: BTCUSDT)
    webhook_entry_price = float(data['close_price']) # Webhook'tan gelen fiyat (potansiyel giriş fiyatı)

    logger.info(f"{symbol} için {new_signal_type.upper()} sinyali işleniyor. Webhook fiyatı: {webhook_entry_price}")

    # --- Aşama 1: Sembol için Mevcut Bot Tarafından Yönetilen İşlemi Ele Al ---
    if symbol in active_bot_trades:
        current_trade = active_bot_trades[symbol]
        current_signal_type = current_trade['signal_type'] # Mevcut işlemin sinyal türü
        current_entry_price = current_trade['entry_price'] # Mevcut işlemin giriş fiyatı
        sl_order_id = current_trade.get('sl_order_id') # Mevcut işlemin SL emir ID'si
        original_quantity = current_trade['quantity'] # P&L hesaplamaları için orijinal miktar

        if new_signal_type == current_signal_type:
            logger.warning(f"{symbol} için sinyal ({new_signal_type.upper()}), mevcut bot tarafından yönetilen işlemle aynı yönde. Sinyal yok sayıldı.")
            return # Aynı yönde sinyal ise yok say

        # Aktif bir bot işlemi için ters sinyal alındı: Önce mevcut işlemi kapat.
        logger.info(f"{symbol} için ters sinyal ({new_signal_type.upper()}) alındı. Mevcut aktif bot işlemi: {current_signal_type.upper()}. Mevcut işlem kapatılmaya çalışılıyor.")

        # Kapatma için doğruluğu sağlamak amacıyla Binance'ten mevcut pozisyon miktarını al
        live_position_info = futures_client.get_open_position_for_symbol(symbol)

        if not live_position_info or float(live_position_info.get('positionAmt', 0)) == 0:
            # Botta aktif bir işlem kaydı vardı, ancak Binance'te canlı pozisyon bulunamadı veya pozisyon sıfır.
            logger.warning(f"Bot {symbol} için aktif bir işlem kaydına sahipti, ancak Binance'te canlı pozisyon bulunamadı veya pozisyon sıfır. Kayıt ve SL temizleniyor.")
            if sl_order_id: # Yetim kalmış SL emrini iptal etmeye çalış
                try:
                    logger.info(f"{symbol} için potansiyel olarak yetim kalmış SL emri {sl_order_id} iptal edilmeye çalışılıyor.")
                    futures_client.client.futures_cancel_order(symbol=symbol, orderId=sl_order_id, timestamp=futures_client._get_timestamp())
                    logger.info(f"{symbol} için yetim SL emri {sl_order_id} iptal edildi.")
                except Exception as e_cancel:
                    logger.error(f"{symbol} için yetim SL emri {sl_order_id} iptal edilirken hata: {e_cancel}")
            del active_bot_trades[symbol] # Aktif işlemlerden kaldır
            # Yeni sinyali yeni bir işlem açmak için bir açılış sinyali olarak değerlendir
        else:
            # Mevcut pozisyonu kapat
            position_amt_to_close = live_position_info['positionAmt'] # Binance'ten gelen işaretli string (örn: "-0.100")
            logger.info(f"{symbol} için mevcut {current_signal_type.upper()} pozisyonu kapatılıyor (Canlı Miktar: {position_amt_to_close}).")
            close_order = futures_client.close_position_market(symbol, position_amt_to_close) # İşaretli miktarı ilet

            if close_order and 'orderId' in close_order:
                logger.info(f"{symbol} için pozisyon {close_order['orderId']} emriyle başarıyla kapatıldı.")
                actual_exit_price = webhook_entry_price # Bildirim için varsayılan olarak webhook fiyatını kullan

                # Kapanış emrinin dolum fiyatını doğrulamaya çalış
                try:
                    # config dosyasında ORDER_FILL_WAIT_SECONDS varsa onu kullan, yoksa 2 saniye bekle
                    time.sleep(getattr(config, 'ORDER_FILL_WAIT_SECONDS', 2))
                    filled_close_order = futures_client.client.futures_get_order(symbol=symbol, orderId=close_order['orderId'], timestamp=futures_client._get_timestamp())
                    avg_price_str = filled_close_order.get('avgPrice', '0')
                    if float(avg_price_str) > 0: # Ortalama fiyat geçerliyse kullan
                        actual_exit_price = float(avg_price_str)
                        logger.info(f"{symbol} için kapanış emri {close_order['orderId']} ortalama fiyatla doldu: {actual_exit_price}")
                    else:
                        logger.warning(f"{symbol} için kapanış emri {close_order['orderId']} ortalama fiyatı 0 veya mevcut değil. P&L için webhook fiyatı {webhook_entry_price} kullanılıyor.")
                except Exception as e_fetch_order:
                    logger.error(f"{symbol} için kapanış emri dolum fiyatı alınamadı/doğrulanamadı (Emir ID: {close_order['orderId']}): {e_fetch_order}. P&L için webhook fiyatı kullanılıyor.")

                # P&L hesapla (orijinal miktarı kullanarak)
                pnl = calculate_pnl(current_entry_price, actual_exit_price, original_quantity, current_signal_type)
                logger.info(f"{symbol} için kapatılan işlemin P&L'i: {pnl:.2f} USDT")

                if telegram_notifier.enabled: # Telegram bildirimi gönder
                    telegram_notifier.notify_trade_close(
                        symbol, current_signal_type.upper(), actual_exit_price, current_entry_price,
                        abs(float(original_quantity)), pnl, # Mutlak miktarı gönder
                        notes=f"Ters sinyal ({new_signal_type.upper()}) nedeniyle kapatıldı. Kapanış Emir ID: {close_order['orderId']}"
                    )

                if sl_order_id: # Orijinal SL emrini iptal et
                    try:
                        logger.info(f"Pozisyon kapatıldıktan sonra {symbol} için orijinal SL emri {sl_order_id} iptal ediliyor.")
                        futures_client.client.futures_cancel_order(symbol=symbol, orderId=sl_order_id, timestamp=futures_client._get_timestamp())
                        logger.info(f"{symbol} için SL emri {sl_order_id} iptal edildi.")
                    except Exception as e_cancel:
                        logger.error(f"{symbol} için SL emri {sl_order_id} iptal edilirken hata: {e_cancel}")
                        if telegram_notifier.enabled:
                            telegram_notifier.notify_error(f"SL İptal Hatası: {symbol}", f"SL emri {sl_order_id} iptal edilemedi. Manuel kontrol gerekebilir. Hata: {e_cancel}")

                del active_bot_trades[symbol] # Aktif işlemlerden kaldır
                logger.info(f"{symbol} için bot işlem kaydı active_bot_trades'ten kaldırıldı.")
                # Devam et: Yeni sinyal şimdi yeni bir işlem açmak için işlenecek.
            else:
                # Mevcut pozisyon kapatılamadıysa, yeni işlemi açma
                err_msg = f"Ters sinyale yanıt olarak {symbol} için mevcut {current_signal_type.upper()} pozisyonu kapatılamadı. Yeni {new_signal_type.upper()} işlemi açılmayacak."
                logger.error(err_msg)
                if telegram_notifier.enabled:
                    telegram_notifier.notify_error(f"Pozisyon Kapatma Hatası: {symbol}", err_msg)
                return # Bu sinyali işlemeyi durdur; yeni işlemi açma

    # --- Aşama 2: Binance'te Yönetilmeyen Pozisyonları Kontrol Et (eğer bot işlemi aktif değilse/kapatıldıysa) ---
    # Bu bölüm, sembol active_bot_trades içinde değilse veya yukarıda başarıyla silindiyse yürütülür.
    # Amaç, kullanıcının botun devralmasını veya çakışan yönetilmeyen pozisyonları temizlemesini istemesidir.
    # Bu davranış ideal olarak yapılandırılabilir olmalıdır (örn: config.AUTO_CLOSE_UNMANAGED_POSITIONS).
    # Şimdilik, planın ima ettiği şekilde uygulanıyor.
    if symbol not in active_bot_trades: # Silinmiş olabileceği için yeniden kontrol et
        existing_binance_position = futures_client.get_open_position_for_symbol(symbol)
        if existing_binance_position and float(existing_binance_position.get('positionAmt', 0)) != 0:
            binance_pos_amt_val = float(existing_binance_position['positionAmt']) # Pozisyon miktarı (işaretli)
            unmanaged_direction = 'long' if binance_pos_amt_val > 0 else 'short' # Yönetilmeyen pozisyonun yönü

            if new_signal_type == unmanaged_direction:
                # Yeni sinyal, yönetilmeyen pozisyonla aynı yönde ise çakışma
                message = (f"{symbol} için sinyal ({new_signal_type.upper()}), Binance'te mevcut bir YÖNETİLMEYEN pozisyonla eşleşiyor "
                           f"(Yön: {unmanaged_direction.upper()}, Miktar: {binance_pos_amt_val}). "
                           f"Bot, bu yönetilmeyen pozisyona karşı yinelenen bir işlem açmayacak.")
                logger.warning(message)
                if telegram_notifier.enabled:
                    telegram_notifier.send_message(f"⚠️ Çakışma: {message}")
                return # Devam etme

            # Yönetilmeyen bir Binance pozisyonu için ters sinyal
            logger.info(f"{symbol} için ters sinyal ({new_signal_type.upper()}) alındı. "
                        f"Binance'te yönetilmeyen bir {unmanaged_direction.upper()} pozisyon (Miktar: {binance_pos_amt_val}) mevcut. "
                        f"Yeni işlem açmadan önce kapatılmaya çalışılıyor.")

            # Kapatmak için Binance'ten gelen gerçek işaretli positionAmt'yi kullan
            close_order = futures_client.close_position_market(symbol, existing_binance_position['positionAmt'])
            if close_order and 'orderId' in close_order:
                logger.info(f"{symbol} için yönetilmeyen Binance pozisyonu {close_order['orderId']} emriyle başarıyla kapatıldı.")
                # Bot giriş fiyatını bilmediği için yönetilmeyen pozisyonların P&L'i daha zordur.
                if telegram_notifier.enabled:
                    telegram_notifier.send_message(
                        f"✅ {symbol} için yönetilmeyen {unmanaged_direction.upper()} pozisyonu (Miktar: {binance_pos_amt_val}) "
                        f"yeni {new_signal_type.upper()} sinyali nedeniyle kapatıldı. Kapanış Emir ID: {close_order['orderId']}."
                    )
                # Sinyale göre yeni işlemi açmak için devam et
            else:
                # Yönetilmeyen pozisyon kapatılamadıysa, yeni işlemi açma
                err_msg = (f"Binance'te {symbol} için yönetilmeyen {unmanaged_direction.upper()} pozisyonu kapatılamadı. "
                           f"Yeni {new_signal_type.upper()} işlemi açılmayacak.")
                logger.error(err_msg)
                if telegram_notifier.enabled:
                    telegram_notifier.notify_error(f"Yönetilmeyen Pozisyon Kapatma Hatası: {symbol}", err_msg)
                return # Bu sinyali işlemeyi durdur

    # --- Aşama 3: Maksimum Açık İşlem Limitini Kontrol Et ---
    # Bu, BU bot örneği tarafından aktif olarak yönetilen işlemleri sayar.
    if len(active_bot_trades) >= config.MAX_OPEN_TRADES:
        message = f"Maksimum açık bot işlemi ({config.MAX_OPEN_TRADES}) limitine ulaşıldı. Mevcut: {len(active_bot_trades)}. {symbol} için {new_signal_type.upper()} sinyali yok sayılıyor."
        logger.warning(message)
        if telegram_notifier.enabled: telegram_notifier.send_message(f"⚠️ {message}")
        return

    # --- Aşama 4: Kaldıraç ve Marjin Türünü Yapılandır (bu oturumda bu sembol için yapılmadıysa) ---
    if symbol not in initialized_symbols_settings:
        logger.info(f"{symbol} yapılandırılıyor: Kaldıraç {config.LEVERAGE}x, Marjin Türü {config.MARGIN_TYPE.upper()}...")
        leverage_ok = futures_client.set_leverage(symbol, config.LEVERAGE) # Kaldıracı ayarla
        if not leverage_ok: # Bildirim set_leverage tarafından yapılır
            logger.error(f"Kaldıraç ayarlama hatası nedeniyle {symbol} için işleme devam edilemiyor.")
            return
        margin_type_ok = futures_client.set_margin_type(symbol, config.MARGIN_TYPE.upper()) # Marjin türünü ayarla
        if not margin_type_ok: # Bildirim set_margin_type tarafından yapılır
            logger.error(f"Marjin türü ayarlama hatası nedeniyle {symbol} için işleme devam edilemiyor.")
            return
        logger.info(f"{symbol} için kaldıraç ve marjin türü başarıyla ayarlandı.")
        initialized_symbols_settings.add(symbol) # Ayarlandığını kaydet
    else:
        logger.info(f"{symbol} için kaldıraç ve marjin türü bu oturumda zaten yapılandırılmış.")

    # --- Aşama 5: Pozisyon Büyüklüğünü Hesapla ---
    usdt_balance = futures_client.get_usdt_balance() # Güncel USDT bakiyesini al
    if usdt_balance is None: # Hata get_usdt_balance tarafından zaten loglandı
        message = f"{symbol} için pozisyon büyüklüğü hesaplanamıyor. USDT Bakiyesi mevcut değil."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Bakiye Hatası", message)
        return
    if usdt_balance == 0:
        message = f"{symbol} için pozisyon büyüklüğü hesaplanamıyor. USDT Bakiyesi sıfır."
        logger.error(message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("Bakiye Hatası", message)
        return

    # Pozisyon büyüklüğünü hesapla (örn: bakiye, kaldıraç, risk parametrelerine göre)
    quantity_to_open = futures_client.calculate_position_size(symbol, usdt_balance, webhook_entry_price)
    if not quantity_to_open or quantity_to_open <= 0:
        message = f"{symbol} için hesaplanan miktar sıfır veya geçersiz ({quantity_to_open}). İşlem yapılamaz."
        logger.error(message)
        # Bildirim calculate_position_size tarafından yapılır veya başarısızlığıyla ima edilir
        if telegram_notifier.enabled and quantity_to_open is not None : # sadece sembol bilgisi bulunamadı hatası değilse
             telegram_notifier.notify_error("Pozisyon Büyüklüğü Hatası", message)
        return

    # --- Aşama 6: Giriş Emrini Ver ---
    logger.info(f"{symbol} için {quantity_to_open} miktarlı {new_signal_type.upper()} emri verilmeye çalışılıyor (hedef giriş: {webhook_entry_price})")
    entry_order = futures_client.create_entry_order(symbol, new_signal_type, webhook_entry_price, quantity_to_open)

    if not entry_order or 'orderId' not in entry_order:
        # Hata mesajı create_entry_order tarafından zaten loglandı
        return

    logger.info(f"{symbol} için giriş emri {entry_order['orderId']} ({new_signal_type.upper()}) başarıyla verildi.")

    # Giriş emrinin gerçek dolum fiyatını almaya çalış.
    actual_filled_entry_price = webhook_entry_price # Varsayılan olarak webhook fiyatını kullan
    try:
        # Emir dolumu için bekle (config'den ORDER_FILL_WAIT_SECONDS al, yoksa 2sn)
        time.sleep(getattr(config, 'ORDER_FILL_WAIT_SECONDS', 2))
        filled_entry_order_details = futures_client.client.futures_get_order(symbol=symbol, orderId=entry_order['orderId'], timestamp=futures_client._get_timestamp())
        avg_price_str = filled_entry_order_details.get('avgPrice', '0')
        if float(avg_price_str) > 0: # avgPrice '0.00000' ise dolmamış veya piyasa emri henüz güncellenmemiş
            actual_filled_entry_price = float(avg_price_str)
            logger.info(f"{symbol} için giriş emri {entry_order['orderId']} ortalama fiyatla dolduğu onaylandı: {actual_filled_entry_price}")
        else: # LIMIT emirleri dolmadıysa veya MARKET emirlerinde avgPrice henüz güncellenmediyse
            logger.warning(f"{symbol} için giriş emri {entry_order['orderId']} - avgPrice: {avg_price_str}. SL için webhook fiyatı {webhook_entry_price} kullanılıyor. Durum: {filled_entry_order_details.get('status')}")
            if filled_entry_order_details.get('status') == 'NEW': # Limit emri henüz dolmadıysa
                 logger.warning(f"Giriş emri {entry_order['orderId']} LIMIT ve hala YENİ. SL, hedeflenen fiyat {webhook_entry_price} üzerinden hesaplanacak.")
    except Exception as e_fetch_entry:
        logger.error(f"{symbol} için giriş emri dolum fiyatı alınamadı/doğrulanamadı (Emir ID: {entry_order['orderId']}): {e_fetch_entry}. SL için webhook fiyatı {webhook_entry_price} kullanılıyor.")

    # --- Aşama 7: Zarar Durdurma (SL) Emrini Ver ---
    logger.info(f"{symbol} ({new_signal_type.upper()}) için SL emri veriliyor. Fiyat: {actual_filled_entry_price}, Miktar: {quantity_to_open}")
    sl_order = futures_client.create_stop_loss_order(symbol, new_signal_type, actual_filled_entry_price, quantity_to_open)

    if not sl_order or 'orderId' not in sl_order:
        # SL emri verilemediyse, riski azaltmak için açılan pozisyonu kapatmaya çalış
        sl_failure_message = (f"{symbol} için giriş emri verildi (ID: {entry_order['orderId']}), "
                              f"ancak zarar durdurma emri VERİLEMEDİ. RİSKİ AZALTMAK İÇİN POZİSYON KAPATILMAYA ÇALIŞILIYOR.")
        logger.error(sl_failure_message)
        if telegram_notifier.enabled: telegram_notifier.notify_error("KRİTİK: SL Emir Hatası", sl_failure_message)

        # Kritik: SL başarısız olursa yeni açılan pozisyonu kapatmaya çalış
        logger.warning(f"SL yerleştirme hatası nedeniyle {symbol} için pozisyon kapatılmaya çalışılıyor.")
        current_pos_after_entry = futures_client.get_open_position_for_symbol(symbol) # Kapatmak için mevcut miktarı al
        if current_pos_after_entry and float(current_pos_after_entry.get('positionAmt',0)) != 0:
            cleanup_close_order = futures_client.close_position_market(symbol, current_pos_after_entry['positionAmt'])
            if cleanup_close_order and 'orderId' in cleanup_close_order:
                logger.info(f"SL hatası nedeniyle {symbol} için pozisyon kapatıldı. Temizleme Emir ID: {cleanup_close_order.get('orderId')}")
                if telegram_notifier.enabled: telegram_notifier.send_message(f"ℹ️ {symbol} için pozisyon, SL yerleştirme hatası nedeniyle otomatik olarak kapatıldı (Temizleme Emri: {cleanup_close_order.get('orderId')}).")
            else:
                crit_msg = f"KRİTİK: SL hatasından sonra {symbol} için pozisyon kapatılamadı. MANUEL MÜDAHALE KESİNLİKLE GEREKLİDİR."
                logger.critical(crit_msg)
                if telegram_notifier.enabled: telegram_notifier.notify_error(f"KRİTİK: Pozisyon Aktif, SL Başarısız, Temizleme Başarısız: {symbol}", crit_msg)
        else: # Pozisyon dolmamış olabilir veya başka yollarla zaten kapatılmış olabilir
            logger.info(f"SL hatasından sonra {symbol} için temizlenecek açık pozisyon bulunamadı veya giriş emri {entry_order['orderId']} dolmadı.")
        return # Bu işlemi daha fazla işleme

    logger.info(f"{symbol} için zarar durdurma emri {sl_order['orderId']} başarıyla verildi.")
    initial_sl_price = float(sl_order.get('stopPrice', 0.0)) # SL tetikleme fiyatını al
    if initial_sl_price == 0.0 and sl_order.get('type') in ['STOP_MARKET', 'STOP']: # stopPrice bekleniyorsa kontrol et
        logger.error(f"KRİTİK: {symbol} için SL emir yanıtında stopPrice 0.0 (ID: {sl_order['orderId']}). SL doğru yerleştirilmemiş veya alınmamış olabilir. Tür: {sl_order.get('type')}")
        if telegram_notifier.enabled: telegram_notifier.notify_error(f"SL Fiyatı Eksik: {symbol}", f"Emir yanıtından alınan başlangıç SL fiyatı (ID: {sl_order['orderId']}) sıfır. Manuel kontrol gerekli.")
        # Bu çok kritik, işlemi kapatmayı da gerektirebilir. Şimdilik yoğun bir şekilde uyarılıyor.

    # --- Aşama 8: Bildir ve Bot İşlemini Kaydet ---
    if telegram_notifier.enabled: # Telegram bildirimi gönder
        telegram_notifier.notify_trade_entry(symbol, new_signal_type.upper(), actual_filled_entry_price, quantity_to_open, initial_sl_price,
                                             notes=f"Giriş Emir ID: {entry_order['orderId']}\nSL Emir ID: {sl_order['orderId']}")

    # Aktif işlemi kaydet
    active_bot_trades[symbol] = {
        'entry_order_id': entry_order['orderId'],
        'sl_order_id': sl_order['orderId'],
        'current_sl_price': initial_sl_price, # SL emrinin gerçek tetikleme fiyatı
        'entry_price': actual_filled_entry_price, # Giriş emrinin dolduğu fiyat
        'quantity': quantity_to_open, # Açılan miktar
        'signal_type': new_signal_type, # 'long' veya 'short'
        'status': "open", # İşlem durumu
        'trailing_active': False, # Takip eden zarar durdurma aktif mi?
        'highest_price_since_trailing_activation': actual_filled_entry_price if new_signal_type == 'long' else 0.0, # TSL için (long ise giriş fiyatı, değilse 0)
        'lowest_price_since_trailing_activation': actual_filled_entry_price if new_signal_type == 'short' else float('inf'), # TSL için (short ise giriş fiyatı, değilse sonsuz)
        'timestamp': time.time() # İşlem açılış zaman damgası
    }
    logger.info(f"{symbol} ({new_signal_type.upper()}) işlemi active_bot_trades'e eklendi. Detaylar: {active_bot_trades[symbol]}")


# Webhook endpoint'i
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    TradingView'den gelen POST isteklerini dinleyen webhook endpoint'i.
    Gelen veriyi doğrular, temizler ve handle_trade_signal fonksiyonuna iletir.
    """
    logger.info("Webhook alındı!")
    try:
        data_str = request.get_data(as_text=True) # Ham veriyi string olarak al
        logger.debug(f"Ham webhook verisi: {data_str}")
        data = json.loads(data_str) # JSON string'ini TradingView'den ayrıştır
        logger.info(f"Ayrıştırılmış webhook verisi: {data}")

        # --- Temel Doğrulama ---
        required_fields = ["signal_type", "ticker", "close_price", "exchange", "interval"] # Gerekli alanlar
        for field in required_fields: # Gerekli alanların varlığını kontrol et
            if field not in data:
                logger.warning(f"Webhook verisinde eksik alan: '{field}'. Veri: {data}")
                return jsonify({"status": "error", "message": f"Eksik alan: {field}"}), 400

        # signal_type (sinyal türü) doğrulaması
        if data["signal_type"].lower() not in ["long", "short"]:
            logger.warning(f"Geçersiz signal_type: {data['signal_type']}")
            return jsonify({"status": "error", "message": "Geçersiz signal_type. 'long' veya 'short' olmalı."}), 400

        # interval (zaman aralığı) doğrulaması
        if str(data["interval"]) != str(config.EXPECTED_WEBHOOK_INTERVAL):
            logger.warning(f"Geçersiz interval: {data['interval']}. Beklenen: {config.EXPECTED_WEBHOOK_INTERVAL}.")
            return jsonify({"status": "error", "message": f"Geçersiz interval. Beklenen: {config.EXPECTED_WEBHOOK_INTERVAL}."}), 400

        # exchange (borsa) doğrulaması (isteğe bağlı ama iyi bir pratik)
        if not data["exchange"] or not data["exchange"].upper().startswith("BINANCE"):
            logger.warning(f"Geçersiz exchange: {data['exchange']}. 'BINANCE' ile başlaması bekleniyor.")
            # Kullanıcı diğer borsaları işlemek istiyorsa izin ver, ancak logla.
            # return jsonify({"status": "error", "message": "Geçersiz exchange. Beklenen 'BINANCE'."}), 400

        # ticker (sembol) temizleme: varsa borsa önekini kaldır (örn: "BINANCE:BTCUSDT" -> "BTCUSDT")
        # TradingView'in ekleyebileceği ".P" veya diğer sonekleri de kaldır
        ticker_raw = data["ticker"]
        if ":" in ticker_raw: # İki nokta içeriyorsa, ayır ve ikinci kısmı al
            ticker_clean = ticker_raw.split(":")[1]
        else:
            ticker_clean = ticker_raw

        # .P, .PERP gibi yaygın sürekli sözleşme soneklerini kaldır (büyük/küçük harf duyarsız)
        if ticker_clean.upper().endswith((".P", ".PERP")):
            ticker_clean = ticker_clean[:-2] if ticker_clean.upper().endswith(".P") else ticker_clean[:-5]

        data["ticker"] = ticker_clean # Temizlenmiş ticker'ı handle_trade_signal için güncelle

        # ticker'ın yapılandırmadaki TRADING_PAIRS listesinde olup olmadığını kontrol et
        if data["ticker"] not in config.TRADING_PAIRS:
            logger.warning(f"Ticker {data['ticker']} (temizlenmiş hali: {ticker_raw}) config dosyasındaki TRADING_PAIRS listesinde değil.")
            return jsonify({"status": "error", "message": f"Ticker {data['ticker']} TRADING_PAIRS içinde yapılandırılmamış."}), 400

        # close_price (kapanış fiyatı) formatının float olup olmadığını kontrol et
        try:
            data['close_price'] = float(data['close_price'])
        except ValueError:
            logger.warning(f"Geçersiz close_price formatı: {data['close_price']}. Sayı olmalı.")
            return jsonify({"status": "error", "message": "Geçersiz close_price formatı."}), 400

        logger.info(f"Webhook doğrulandı: ticker: {data['ticker']}, sinyal: {data['signal_type'].lower()}, fiyat: {data['close_price']}")

        # Gerçek işlemeyi yeni bir thread'e yükleyerek webhook'a hızlı yanıt ver
        # Bu, handle_trade_signal zaman alabiliyorsa (API çağrıları) iyi bir pratiktir.
        # Ancak, aynı parite için sinyaller hızla gelirse yarış koşullarına veya yürütme sırasına dikkat edin.
        # Sembol başına sıralı işleme için, sembol başına bir kuyruk veya kilit gerekebilir.
        # Şimdilik basitlik için doğrudan çağrı, ancak üretim için thread'leri düşünün.
        # threading.Thread(target=handle_trade_signal, args=(data,)).start()
        handle_trade_signal(data) # Doğrudan çağrı

        return jsonify({"status": "success", "message": "Webhook alındı ve işleme başlatıldı"}), 200

    except json.JSONDecodeError: # JSON ayrıştırma hatası
        logger.error(f"Veriden JSON çözümlenemedi: {request.get_data(as_text=True)}")
        return jsonify({"status": "error", "message": "Geçersiz JSON yükü"}), 400
    except Exception as e: # Diğer genel hatalar
        logger.error(f"Webhook işlenirken hata: {e}", exc_info=True)
        if telegram_notifier and telegram_notifier.enabled:
             telegram_notifier.notify_error("Webhook İşleme Hatası", f"Detaylar: {str(e)[:500]}") # Kesilmiş hata gönder
        return jsonify({"status": "error", "message": "Webhook işlenirken dahili sunucu hatası"}), 500

# Takip eden zarar durdurma (Trailing Stop Loss - TSL) döngüsü
def trailing_stop_loop():
    """
    Aktif işlemler için periyodik olarak takip eden zarar durdurma (TSL) mantığını çalıştırır.
    Bu fonksiyon ayrı bir thread'de çalışır.
    """
    global futures_client, telegram_notifier, active_bot_trades
    logger.info("Takip eden zarar durdurma (TSL) yöneticisi thread'i başlatıldı.")
    time.sleep(10) # Servislerin tam olarak ayağa kalkması için biraz bekle (özellikle Docker/K8s'de)
    while True:
        try:
            # Sadece TSL config'de aktifse ve istemciler hazırsa çalıştır
            if futures_client and telegram_notifier and config.TRAILING_STOP:
                # Çalışma zamanı değişiklik sorunlarından kaçınmak için üzerinde yinelenecek öğelerin bir kopyasını oluştur
                symbols_to_check = list(active_bot_trades.keys()) # Kontrol edilecek sembollerin listesi
                if symbols_to_check: # Sadece aktif işlem varsa çalıştır
                    manage_trailing_stops(futures_client, telegram_notifier, active_bot_trades, symbols_to_check)
            else:
                if not config.TRAILING_STOP:
                    logger.debug("Takip eden zarar durdurma (TSL) config'de devre dışı bırakılmış. TSL döngüsü boşta.")
                else:
                    logger.warning("TSL döngüsü: futures_client veya telegram_notifier hazır değil. TSL döngüsü atlanıyor.")
        except Exception as e:
            logger.error(f"trailing_stop_loop içinde istisna: {e}", exc_info=True)
            if telegram_notifier and telegram_notifier.enabled:
                 telegram_notifier.notify_error("TSL Döngü İstisnası", str(e))

        try:
            # Uyku süresini config'den al
            sleep_duration = int(config.TRAILING_STOP_CHECK_INTERVAL_SECONDS)
            if sleep_duration < 10: # Güvenlik için minimum 10 saniye
                logger.warning(f"TRAILING_STOP_CHECK_INTERVAL_SECONDS ({sleep_duration}s) çok düşük. Güvenlik için minimum 10s olarak ayarlanıyor.")
                sleep_duration = 10
            time.sleep(sleep_duration)
        except AttributeError: # TRAILING_STOP_CHECK_INTERVAL_SECONDS config'de yoksa
            logger.error("TRAILING_STOP_CHECK_INTERVAL_SECONDS config'de bulunamadı. TSL uyku süresi varsayılan olarak 60s'ye ayarlanıyor.")
            time.sleep(60)
        except ValueError: # Geçerli bir tam sayı değilse
            logger.error("TRAILING_STOP_CHECK_INTERVAL_SECONDS geçerli bir tam sayı değil. TSL uyku süresi varsayılan olarak 60s'ye ayarlanıyor.")
            time.sleep(60)

# Ana program başlangıç noktası
if __name__ == "__main__":
    try:
        initialize_services() # Global istemcileri başlat

        if config.TRAILING_STOP: # TSL config'de aktifse
            if futures_client and telegram_notifier: # İstemciler başlatıldıysa TSL thread'ini başlat
                ts_thread = threading.Thread(target=trailing_stop_loop, daemon=True) # Arka plan thread'i
                ts_thread.start()
                logger.info(f"Takip eden zarar durdurma (TSL) yöneticisi thread'i başlatıldı (kontrol aralığı: {getattr(config, 'TRAILING_STOP_CHECK_INTERVAL_SECONDS', 60)}s).")
            else:
                logger.error("Takip Eden Zarar Durdurma Yöneticisi başlatılamıyor: Binance istemcisi veya Telegram bildirimcisi başlatılmamış veya TSL devre dışı.")
        else:
            logger.info("Takip Eden Zarar Durdurma (TSL) yapılandırmada devre dışı bırakılmış.")

        # Üretim için app.run(debug=True) yerine Gunicorn veya Waitress kullanın
        # Port numarasını config'den al veya varsayılan olarak 5000 kullan
        port = getattr(config, 'PORT', 5000)
        try:
            port = int(port)
        except ValueError:
            logger.warning(f"Config'deki PORT değeri '{port}' geçersiz. Varsayılan olarak 5000 kullanılıyor.")
            port = 5000

        logger.info(f"Flask uygulaması 0.0.0.0:{port} adresinde başlatılıyor")
        # Üretim için debug=False olmalı. Gunicorn worker'ları yönetecek.
        app.run(host='0.0.0.0', port=port, debug=False)

    except Exception as e_startup: # Uygulama başlangıcında kritik hata olursa
        logger.critical(f"Uygulama başlangıcında ölümcül hata: {e_startup}", exc_info=True)
        if telegram_notifier and telegram_notifier.enabled: # Mümkünse bildirim gönder
            telegram_notifier.notify_error("Bot Kritik Başlangıç Hatası", f"Bot başlatılamadı: {e_startup}. Logları kontrol edin.")
        import sys
        sys.exit(1) # Kritik başlangıç hatası durumunda çık

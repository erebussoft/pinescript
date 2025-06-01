# binance_client.py - Binance Futures API etkileşimlerini yöneten modül
import config # Yapılandırma ayarlarını içe aktar
import logging
from binance.client import Client # Binance API istemcisi
from binance.exceptions import BinanceAPIException, BinanceOrderException # Binance özel istisnaları
from binance.enums import * # Binance API sabitleri (örn: SIDE_BUY, FUTURE_ORDER_TYPE_MARKET)
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP # Hassas ondalık hesaplamalar için

logger = logging.getLogger(__name__) # Bu modül için logger örneği

# Python < 3.9 için tip ipucu (type hinting) amacıyla ileriye dönük bildirim
# from typing import TYPE_CHECKING
# if TYPE_CHECKING:
#     from telegram_bot import TelegramNotifier # TelegramNotifier'ı döngüsel bağımlılık olmadan içe aktarabilmek için

class BinanceFuturesClient:
    """
    Binance Futures API ile etkileşim kurmak için bir istemci sınıfı.
    Kaldıraç ayarlama, marjin türü değiştirme, emir verme, bakiye alma gibi işlemleri yönetir.
    """
    def __init__(self, api_key, api_secret, telegram_notifier_instance):
        """
        BinanceFuturesClient sınıfının başlatıcısı.
        Args:
            api_key (str): Binance API anahtarı.
            api_secret (str): Binance API gizli anahtarı.
            telegram_notifier_instance (TelegramNotifier): Telegram bildirimleri göndermek için örnek.
        """
        self.client = Client(api_key, api_secret) # Binance API istemcisini başlat
        self.telegram_notifier = telegram_notifier_instance # Telegram bildirimci örneğini sakla
        self.client.FUTURES_URL = 'https://fapi.binance.com' # Futures API URL'sini kullandığımızdan emin ol
        logger.info("Binance Futures İstemcisi başlatıldı.")
        self.server_time_offset = self._get_server_time_offset() # Sunucu zaman farkını al ve sakla
        self.exchange_info = self.client.futures_exchange_info() # Borsa bilgilerini (sembol detayları, filtreler vb.) al ve sakla

    def set_leverage(self, symbol, leverage):
        """
        Belirtilen sembol için kaldıracı ayarlar.
        Args:
            symbol (str): Kaldıracın ayarlanacağı sembol (örn: "BTCUSDT").
            leverage (int): Ayarlanacak kaldıraç değeri.
        Returns:
            bool: İşlem başarılıysa True, değilse False.
        """
        try:
            logger.info(f"{symbol} için kaldıraç {leverage}x olarak ayarlanıyor...")
            response = self.client.futures_change_leverage(symbol=symbol, leverage=leverage, timestamp=self._get_timestamp())
            logger.info(f"{symbol} için kaldıraç ayarlandı: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"{symbol} için kaldıraç {leverage}x olarak ayarlanırken Binance API İstisnası: {e}")
            # Örnek: e.code == -4048 (Kaldıraç değiştirilmedi) - zaten ayarlıysa hata olmayabilir
            if e.code == -4048: # "Leverage not changed"
                logger.info(f"{symbol} için kaldıraç zaten {leverage}x olarak ayarlı veya değişiklik gerekmiyor.")
                return True # Zaten istenen kaldıraçtaysa başarılı say
            # Gerekirse daha spesifik hata kodu yönetimi ekle
            self.telegram_notifier.notify_error(f"Kaldıraç Hatası: {symbol}", f"Kaldıraç {leverage}x olarak ayarlanamadı. Kod: {e.code}, Mesaj: {e.message}")
            return False
        except Exception as e:
            logger.error(f"{symbol} için kaldıraç ayarlanırken genel hata: {e}")
            self.telegram_notifier.notify_error(f"Kaldıraç Hatası: {symbol}", f"{leverage}x olarak kaldıraç ayarlanırken genel hata.")
            return False

    def set_margin_type(self, symbol, margin_type):
        """
        Belirtilen sembol için marjin türünü (ISOLATED veya CROSSED) ayarlar.
        Args:
            symbol (str): Marjin türünün ayarlanacağı sembol.
            margin_type (str): "ISOLATED" veya "CROSSED".
        Returns:
            bool: İşlem başarılıysa True, değilse False.
        """
        try:
            logger.info(f"{symbol} için marjin türü {margin_type} olarak ayarlanıyor...")
            response = self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type.upper(), timestamp=self._get_timestamp())
            logger.info(f"{symbol} için marjin türü ayarlandı: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"{symbol} için marjin türü {margin_type} olarak ayarlanırken Binance API İstisnası: {e}")
            if e.code == -4046: # "No need to change margin type"
                logger.info(f"{symbol} için marjin türü zaten {margin_type} veya değişiklik gerekmiyor.")
                return True # Başarılı say
            if e.code == -4059: # "Margin type cannot be changed if there are open orders or positions."
                 logger.error(f"KRİTİK: {symbol} için marjin türü {margin_type} olarak değiştirilemiyor çünkü açık emirler veya pozisyonlar var. Değişiklik gerekliyse manuel müdahale gerekebilir.")
                 self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"Açık emirler/pozisyonlar nedeniyle marjin türü {margin_type} olarak değiştirilemiyor. Manuel kontrol gerekli.")
                 return False # Bu işlem için kesin bir başarısızlık
            self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"Marjin türü {margin_type} olarak ayarlanamadı. Kod: {e.code}, Mesaj: {e.message}")
            return False
        except Exception as e:
            logger.error(f"{symbol} için marjin türü ayarlanırken genel hata: {e}")
            self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"{margin_type} olarak marjin türü ayarlanırken genel hata.")
            return False

    def _get_server_time_offset(self):
        """
        Yerel makine saati ile Binance sunucu saati arasındaki farkı milisaniye cinsinden hesaplar.
        Bu, API isteklerinin zaman damgalarının senkronize olmasını sağlamak için kullanılır.
        Returns:
            int: Milisaniye cinsinden zaman farkı. Hata durumunda 0.
        """
        try:
            server_time = self.client.futures_time()['serverTime'] # Sunucu zamanını al
            local_time = int(time.time() * 1000) # Yerel zamanı milisaniye cinsinden al
            offset = server_time - local_time # Farkı hesapla
            logger.info(f"Sunucu zaman farkı: {offset} ms")
            return offset
        except Exception as e:
            logger.error(f"Sunucu zamanı alınırken hata: {e}")
            return 0 # Hata durumunda farkı 0 olarak kabul et

    def _get_timestamp(self):
        """
        Binance API istekleri için geçerli bir zaman damgası (timestamp) oluşturur.
        Hesaplanan sunucu zaman farkını kullanarak yerel zamanı ayarlar.
        Returns:
            int: Milisaniye cinsinden ayarlanmış zaman damgası.
        """
        return int(time.time() * 1000 + self.server_time_offset)

    def get_symbol_info(self, symbol):
        """
        Belirtilen sembol için borsa bilgilerini (hassasiyetler, filtreler vb.) alır.
        Bu bilgiler `__init__` sırasında alınan `exchange_info` içinden aranır.
        Args:
            symbol (str): Bilgileri alınacak sembol (örn: "BTCUSDT").
        Returns:
            dict or None: Sembol bilgileri bulunursa bir sözlük, bulunamazsa None.
        """
        for s_info in self.exchange_info['symbols']: # Saklanan borsa bilgileri içinde ara
            if s_info['symbol'] == symbol:
                return s_info
        logger.warning(f"{symbol} için sembol bilgisi bulunamadı.")
        return None

    def _adjust_quantity_to_step(self, quantity, step_size):
        """
        Verilen miktarı, sembolün LOT_SIZE filtresindeki adım büyüklüğüne göre ayarlar.
        Miktarı her zaman aşağıya doğru yuvarlar (ROUND_DOWN).
        Args:
            quantity (float or str): Ayarlanacak miktar.
            step_size (str): Sembol için izin verilen miktar adımı (örn: "0.001").
        Returns:
            Decimal: Adım büyüklüğüne göre ayarlanmış miktar.
        """
        return (Decimal(str(quantity)).quantize(Decimal(str(step_size)), rounding=ROUND_DOWN))

    def _adjust_price_to_tick(self, price, tick_size):
        """
        Verilen fiyatı, sembolün PRICE_FILTER filtresindeki tick büyüklüğüne göre ayarlar.
        Fiyatı genellikle aşağıya doğru yuvarlar (ROUND_DOWN), ancak gereksinime göre değiştirilebilir.
        Args:
            price (float or str): Ayarlanacak fiyat.
            tick_size (str): Sembol için izin verilen fiyat adımı (örn: "0.01").
        Returns:
            Decimal: Tick büyüklüğüne göre ayarlanmış fiyat.
        """
        return (Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN)) # Veya ROUND_NEAREST

    def get_usdt_balance(self):
        """
        Binance Futures hesabındaki mevcut USDT bakiyesini alır.
        Returns:
            float: USDT bakiyesi. Hata durumunda 0.0.
        """
        try:
            balances = self.client.futures_account_balance(timestamp=self._get_timestamp()) # Hesap bakiyelerini al
            for balance in balances:
                if balance['asset'] == 'USDT': # USDT varlığını bul
                    logger.info(f"USDT Bakiyesi: {balance['balance']}")
                    return float(balance['balance'])
            return 0.0 # USDT bulunamazsa
        except BinanceAPIException as e:
            logger.error(f"Bakiye alınırken Binance API İstisnası: {e}")
        except Exception as e:
            logger.error(f"USDT bakiyesi alınırken hata: {e}")
        return 0.0

    def get_open_positions_count(self):
        """
        Binance Futures hesabındaki mevcut açık pozisyon sayısını alır.
        Returns:
            int: Açık pozisyon sayısı. Hata durumunda 0.
        """
        try:
            positions = self.client.futures_position_information(timestamp=self._get_timestamp()) # Tüm pozisyon bilgilerini al
            # Miktarı sıfır olmayan pozisyonları say
            open_positions = [p for p in positions if float(p['positionAmt']) != 0]
            logger.info(f"{len(open_positions)} açık pozisyon bulundu.")
            return len(open_positions)
        except BinanceAPIException as e:
            logger.error(f"Pozisyonlar alınırken Binance API İstisnası: {e}")
        except Exception as e:
            logger.error(f"Açık pozisyonlar alınırken hata: {e}")
        return 0 # Hata durumunda veya pozisyon yoksa

    def calculate_position_size(self, symbol, usdt_balance, entry_price):
        """
        Belirtilen sembol, bakiye ve giriş fiyatına göre işlem yapılacak pozisyon büyüklüğünü hesaplar.
        Yapılandırmadaki `TRADABLE_BALANCE_RATIO` ve `MAX_OPEN_TRADES` ayarlarını kullanır.
        Hesaplanan miktarı sembolün adım büyüklüğüne (stepSize) ve minimum nominal (minNotional) değerine göre ayarlar.
        Args:
            symbol (str): İşlem yapılacak sembol.
            usdt_balance (float): Kullanılabilir USDT bakiyesi.
            entry_price (float): Planlanan giriş fiyatı.
        Returns:
            float or None: Hesaplanan ve ayarlanan pozisyon büyüklüğü. Hata veya yetersizlik durumunda None.
        """
        if entry_price <= 0:
            logger.error("Pozisyon büyüklüğünü hesaplamak için giriş fiyatı pozitif olmalıdır.")
            return None

        if not hasattr(config, 'MAX_OPEN_TRADES') or config.MAX_OPEN_TRADES <= 0:
            logger.error("MAX_OPEN_TRADES in config must be greater than 0 to calculate position size.")
            return None

        # İşlem yapılabilecek toplam bakiye ve işlem başına düşen USDT miktarını hesapla
        tradable_balance = usdt_balance * config.TRADABLE_BALANCE_RATIO
        amount_per_trade_usdt = tradable_balance / config.MAX_OPEN_TRADES # config.MAX_OPEN_TRADES=0 ise ZeroDivisionError olabilir, dikkat!

        quantity = amount_per_trade_usdt / entry_price # Ham miktar (coin cinsinden)

        symbol_info = self.get_symbol_info(symbol) # Sembol bilgilerini al
        if not symbol_info:
            logger.error(f"Pozisyon büyüklüğü hesaplanamıyor, {symbol} için sembol bilgisi bulunamadı.")
            return None

        # Miktar hassasiyetini (stepSize) LOT_SIZE filtresinden al
        quantity_precision = None
        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
        if lot_size_filter:
            quantity_precision = lot_size_filter['stepSize']

        if quantity_precision:
            # Miktarı adım büyüklüğüne göre ayarla
            adjusted_quantity = self._adjust_quantity_to_step(quantity, quantity_precision)
            logger.info(f"{symbol} için hesaplanan pozisyon büyüklüğü: {quantity}, ayarlanan: {adjusted_quantity} (adım: {quantity_precision})")

            # Minimum nominal değeri (minNotional) kontrol et
            min_notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
            if min_notional_filter:
                min_notional = float(min_notional_filter['notional'])
                # Ayarlanan miktarın nominal değeri minimum nominalden küçükse, emir verilemez
                if float(adjusted_quantity) * entry_price < min_notional:
                    logger.warning(f"{symbol} için hesaplanan nominal ({float(adjusted_quantity) * entry_price}) minNotional'dan ({min_notional}) düşük. Emir verilemez.")
                    return None # Veya minNotional'ı karşılayacak şekilde ayarla (eğer istenirse ve mümkünse)
            return float(adjusted_quantity) # Ayarlanmış miktarı float olarak döndür
        else:
            logger.warning(f"{symbol} için miktar hassasiyeti belirlenemedi. Ayarlanmamış miktar kullanılıyor: {quantity}")
            return quantity # Hassasiyet belirlenemezse ham miktarı döndür


    def place_futures_order(self, symbol, side, quantity, price=None, stop_price=None, order_type=None):
        """
        Binance Futures üzerinde bir emir (LIMIT, MARKET, STOP_MARKET vb.) oluşturur ve verir.
        Bu, diğer emir oluşturma fonksiyonları (create_entry_order, create_stop_loss_order) tarafından kullanılan çekirdek fonksiyondur.
        Args:
            symbol (str): İşlem yapılacak sembol.
            side (str): Emir yönü ('BUY' veya 'SELL').
            quantity (float): İşlem miktarı.
            price (float, optional): LIMIT emirleri için fiyat.
            stop_price (float, optional): STOP_MARKET veya TAKE_PROFIT_MARKET emirleri için tetikleme fiyatı.
            order_type (str, optional): Emir türü (örn: FUTURE_ORDER_TYPE_LIMIT, FUTURE_ORDER_TYPE_MARKET). Belirtilmezse, fiyat varsa LIMIT, yoksa MARKET varsayılır.
        Returns:
            dict or None: Başarılı olursa emir yanıtı (sözlük), başarısız olursa None.
        """
        symbol_info = self.get_symbol_info(symbol) # Sembol bilgilerini al
        if not symbol_info:
            logger.error(f"Emir verilemiyor, {symbol} için sembol bilgisi bulunamadı.")
            return None

        # Fiyat hassasiyetini (tickSize) PRICE_FILTER filtresinden al
        price_precision = None
        tick_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
        if tick_size_filter:
            price_precision = tick_size_filter['tickSize']

        # Emir parametrelerini hazırla
        params = {
            'symbol': symbol,
            'side': side, # 'BUY' (Al) veya 'SELL' (Sat)
            'quantity': quantity, # Miktar
            'timestamp': self._get_timestamp() # Zaman damgası
        }

        if order_type: # Emir türü belirtilmişse kullan
            params['type'] = order_type
        else: # Belirtilmemişse, fiyat varsa LIMIT, yoksa MARKET varsay
            params['type'] = FUTURE_ORDER_TYPE_LIMIT if price else FUTURE_ORDER_TYPE_MARKET

        if params['type'] == FUTURE_ORDER_TYPE_LIMIT: # LIMIT emir ise
            if not price:
                logger.error("LIMIT emir için fiyat gereklidir.")
                return None
            if price_precision: # Fiyatı tick büyüklüğüne göre ayarla
                params['price'] = self._adjust_price_to_tick(price, price_precision)
            else:
                params['price'] = price
            params['timeInForce'] = TIME_IN_FORCE_GTC # Geçerlilik süresi: İptal Edilene Kadar Geçerli (GTC)

        # STOP_MARKET veya TAKE_PROFIT_MARKET emirleri için stopPrice gerekir
        if params['type'] in [FUTURE_ORDER_TYPE_STOP_MARKET, FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET]:
            if not stop_price:
                logger.error("STOP_MARKET veya TAKE_PROFIT_MARKET emirleri için stopPrice gereklidir.")
                return None
            if price_precision: # Stop fiyatını tick büyüklüğüne göre ayarla
                 params['stopPrice'] = self._adjust_price_to_tick(stop_price, price_precision)
            else:
                params['stopPrice'] = stop_price
            # params['reduceOnly'] = False # Başlangıç SL için reduceOnly False olmalı. TP için True olabilir.
                                        # Bu, pozisyonu sadece azaltmak için kullanılır, yeni pozisyon açmaz.

        logger.info(f"Emir veriliyor, parametreler: {params}")
        try:
            order = self.client.futures_create_order(**params) # Emri oluştur ve ver
            logger.info(f"Emir başarıyla verildi: {order}")
            return order
        except BinanceAPIException as e: # API'den kaynaklanan hatalar
            logger.error(f"Emir verilirken Binance API İstisnası: {e.message} (Kod: {e.code}) - Parametreler: {params}")
            if self.telegram_notifier and self.telegram_notifier.enabled:
                self.telegram_notifier.notify_error(
                    f"Emir Verme API Hatası: {params.get('symbol', 'N/A')}",
                    f"{params.get('type','N/A')} {params.get('side','N/A')} emri verilemedi. Kod: {e.code}, Mesaj: {e.message}. Parametreler: {params}"
                )
        except BinanceOrderException as e: # Emir mantığıyla ilgili hatalar (örn: yetersiz bakiye)
            logger.error(f"Emir verilirken Binance Emir İstisnası: {e} - Parametreler: {params}")
            if self.telegram_notifier and self.telegram_notifier.enabled:
                self.telegram_notifier.notify_error(
                    f"Emir Verme Mantık Hatası: {params.get('symbol', 'N/A')}",
                    f"{params.get('type','N/A')} {params.get('side','N/A')} emri verilemedi. Hata: {e}. Parametreler: {params}"
                )
        except Exception as e: # Diğer genel istisnalar
            logger.error(f"Emir verilirken genel hata: {e} - Parametreler: {params}", exc_info=True)
            if self.telegram_notifier and self.telegram_notifier.enabled:
                self.telegram_notifier.notify_error(
                    f"Emir Verme Sistem Hatası: {params.get('symbol', 'N/A')}",
                    f"{params.get('type','N/A')} {params.get('side','N/A')} emri verilirken genel bir istisna oluştu. Hata: {e}. Parametreler: {params}"
                )
        return None # Herhangi bir hata durumunda None döndür

    def create_entry_order(self, symbol, signal_type, entry_price, quantity):
        """
        Piyasaya giriş emri oluşturur (config'deki ORDER_TYPES['entry']'e göre LIMIT veya MARKET).
        Args:
            symbol (str): İşlem yapılacak sembol.
            signal_type (str): 'long' veya 'short'.
            entry_price (float): Hedeflenen giriş fiyatı (LIMIT emirler için kullanılır).
            quantity (float): İşlem miktarı.
        Returns:
            dict or None: Başarılı olursa emir yanıtı, değilse None.
        """
        side = SIDE_BUY if signal_type == 'long' else SIDE_SELL # Sinyal türüne göre AL veya SAT yönü belirle
        order_type = config.ORDER_TYPES.get('entry', 'LIMIT').upper() # config'den giriş emri türünü al, varsayılan LIMIT

        if order_type == 'LIMIT':
            return self.place_futures_order(symbol, side, quantity, price=entry_price, order_type=FUTURE_ORDER_TYPE_LIMIT)
        elif order_type == 'MARKET':
            # Piyasa emri giriş fiyatını doğrudan kullanmaz ama SL hesaplaması için faydalıdır
            return self.place_futures_order(symbol, side, quantity, order_type=FUTURE_ORDER_TYPE_MARKET)
        else:
            logger.error(f"Desteklenmeyen giriş emri türü: {order_type}")
            return None

    def create_stop_loss_order(self, symbol, signal_type, entry_price, quantity_for_sl):
        """
        Zarar durdurma (stop-loss) emri oluşturur.
        config'deki ORDER_TYPES['stoploss']'a göre genellikle STOP_MARKET emri kullanılır.
        Args:
            symbol (str): İşlem yapılacak sembol.
            signal_type (str): 'long' veya 'short'.
            entry_price (float): Pozisyonun giriş fiyatı (SL hesaplaması için temel).
            quantity_for_sl (float): SL emri için miktar (genellikle pozisyon miktarıyla aynı).
        Returns:
            dict or None: Başarılı olursa emir yanıtı, değilse None.
        """
        sl_pct = config.STOP_LOSS # Zarar durdurma yüzdesini config'den al

        if signal_type == 'long': # Long pozisyon için SL
            side = SIDE_SELL # Kapatmak için SAT
            stop_price = entry_price * (1 - sl_pct) # SL fiyatını hesapla
        else: # Short pozisyon için SL
            side = SIDE_BUY # Kapatmak için AL
            stop_price = entry_price * (1 + sl_pct) # SL fiyatını hesapla

        # config'den SL emir türünü al, varsayılan MARKET (Binance için STOP_MARKET)
        stop_order_type_str = config.ORDER_TYPES.get('stoploss', 'MARKET').upper()

        binance_stop_order_type = None
        if stop_order_type_str == 'MARKET': # Binance için STOP_MARKET anlamına gelir
            binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET
        elif stop_order_type_str == 'LIMIT': # Bu, stop fiyatı vurulduktan sonra bir limit emri anlamına gelir (STOP)
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP
             # STOP LIMIT emri için bir 'price' parametresi de (stop için limit fiyatı) gerekir.
             # Şimdilik sadece STOP_MARKET tam olarak uygulanmıştır.
             logger.warning("STOP LIMIT SL emirleri bir limit fiyatı gerektirir. Sağlanmazsa STOP_MARKET davranışı varsayılır.")
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET # Geçici olarak STOP_MARKET'e geri dön

        if not binance_stop_order_type:
            logger.error(f"Desteklenmeyen zarar durdurma emri türü: {stop_order_type_str}")
            return None

        logger.info(f"{symbol} için SL oluşturuluyor: taraf={side}, stop_fiyatı={stop_price}, giriş_fiyatı={entry_price}, miktar={quantity_for_sl}")

        # STOP_MARKET için 'price' parametresi kullanılmaz. 'stopPrice' tetikleyicidir.
        sl_order = self.place_futures_order(symbol, side, quantity_for_sl,
                                            stop_price=stop_price,
                                            order_type=binance_stop_order_type)
        if sl_order:
            logger.info(f"{symbol} için zarar durdurma emri verildi: {sl_order}")
        else:
            logger.error(f"{symbol} için zarar durdurma emri verilemedi.")
        return sl_order

    def close_position_market(self, symbol, position_amt_str):
        """
        Belirtilen sembol için mevcut bir pozisyonu piyasa emriyle kapatır.
        Args:
            symbol (str): Kapatılacak pozisyonun sembolü.
            position_amt_str (str): Pozisyon miktarı (Binance'ten geldiği gibi, işaretli bir string, örn: "0.1", "-0.2").
        Returns:
            dict or None: Başarılı olursa emir yanıtı, değilse None.
        """
        position_amt = float(position_amt_str) # String'i float'a çevir
        if position_amt == 0:
            logger.info(f"{symbol} için kapatılacak pozisyon yok.")
            return None

        # Pozisyon yönüne göre ters işlem yap (long ise sat, short ise al)
        side = SIDE_SELL if position_amt > 0 else SIDE_BUY
        quantity = abs(position_amt) # Miktarın mutlak değerini al

        logger.info(f"{symbol} için {quantity} miktarlı pozisyon MARKET emriyle (taraf: {side}) kapatılmaya çalışılıyor.")
        return self.place_futures_order(symbol, side, quantity, order_type=FUTURE_ORDER_TYPE_MARKET)

    def get_open_position_for_symbol(self, symbol):
        """
        Belirtilen sembol için mevcut açık pozisyon bilgilerini alır.
        Args:
            symbol (str): Pozisyon bilgisi alınacak sembol.
        Returns:
            dict or None: Açık pozisyon varsa pozisyon detaylarını içeren bir sözlük, yoksa None.
        """
        try:
            # Belirli bir sembol için pozisyon bilgilerini al
            positions = self.client.futures_position_information(symbol=symbol, timestamp=self._get_timestamp())
            for p in positions:
                # Sembol eşleşiyorsa ve pozisyon miktarı sıfır değilse, bu açık pozisyondur
                if p['symbol'] == symbol and float(p['positionAmt']) != 0:
                    logger.info(f"{symbol} için açık pozisyon bulundu: {p}")
                    return p
            logger.info(f"{symbol} için açık pozisyon bulunamadı.")
            return None
        except BinanceAPIException as e:
            logger.error(f"{symbol} için pozisyon alınırken Binance API İstisnası: {e}")
        except Exception as e:
            logger.error(f"{symbol} için pozisyon alınırken hata: {e}")
        return None

# Örnek kullanım (bu modülü doğrudan test etmek için)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO) # Test için temel log yapılandırması
    logger.info("BinanceFuturesClient test ediliyor...")

    # config.py'nin dummy anahtarlar içerdiğinden emin olun (veya gerçek testnet anahtarları)
    if config.BINANCE_API_KEY == "YOUR_BINANCE_API_KEY" or config.BINANCE_API_SECRET == "YOUR_BINANCE_API_SECRET":
        logger.warning("Yer tutucu API anahtarları kullanılıyor. Canlı testler başarısız olacaktır.")
        # exit() # Yer tutucu anahtarlarla çalışmayı önlemek için yorumu kaldırın

    # Geliştirme ve test için Binance Testnet kullanılması şiddetle tavsiye edilir.
    # client.API_URL = 'https://testnet.binance.vision/api'
    # client.FUTURES_URL = 'https://testnet.binancefuture.com' # Testnet için futures URL'si

    # futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, None) # Test için TelegramNotifier None olabilir

    # Bağlantı ve zaman senkronizasyonunu test et
    # logger.info(f"Sunucu zaman farkı: {futures_client.server_time_offset} ms")

    # Bakiye almayı test et
    # usdt_balance = futures_client.get_usdt_balance()
    # logger.info(f"Mevcut USDT bakiyesi: {usdt_balance}")

    # Açık pozisyon sayısını almayı test et
    # open_positions_count = futures_client.get_open_positions_count()
    # logger.info(f"Mevcut açık pozisyonlar: {open_positions_count}")

    # Sembol bilgisi ve hesaplamaları test et (geçerli bir futures sembolü kullanın)
    # test_symbol = "BTCUSDT" # Bunun config.TRADING_PAIRS içinde olduğundan emin olun
    # if test_symbol not in config.TRADING_PAIRS:
    #     logger.warning(f"{test_symbol} TRADING_PAIRS içinde değil, bazı testler yanıltıcı olabilir.")

    # symbol_info = futures_client.get_symbol_info(test_symbol)
    # if symbol_info:
    #     logger.info(f"{test_symbol} için sembol bilgisi alındı.")
        # Pozisyon büyüklüğü hesaplamasını test et
        # test_entry_price = 60000 # BTCUSDT için varsayımsal bir fiyat
        # if usdt_balance is not None and usdt_balance > 0 and test_entry_price > 0:
        #     calculated_size = futures_client.calculate_position_size(test_symbol, usdt_balance, test_entry_price)
        #     logger.info(f"{test_symbol} için ${test_entry_price} fiyatında hesaplanan pozisyon büyüklüğü: {calculated_size}")
        # else:
        #    logger.warning("Bakiye veya geçerli giriş fiyatı olmadan pozisyon büyüklüğü hesaplaması test edilemiyor.")
    # else:
    #     logger.error(f"{test_symbol} için sembol bilgisi alınamadı. Bu sembolü içeren diğer testler başarısız olabilir.")

    logger.info("BinanceFuturesClient testleri tamamlandı.")

# binance_client.py
import config
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
from binance.enums import *
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP

logger = logging.getLogger(__name__)

# Python < 3.9 için tür ipucu için ileriye dönük bildirim
# from typing import TYPE_CHECKING
# if TYPE_CHECKING:
#     from telegram_bot import TelegramNotifier

class BinanceFuturesClient:
    def __init__(self, api_key, api_secret, telegram_notifier_instance): # telegram_notifier_instance eklendi
        self.client = Client(api_key, api_secret)
        self.telegram_notifier = telegram_notifier_instance # Sakla
        self.client.FUTURES_URL = 'https://fapi.binance.com' # Vadeli işlemleri kullandığımızdan emin olun
        logger.info("Binance Futures İstemcisi başlatıldı.")
        self.server_time_offset = self._get_server_time_offset()
        self.exchange_info = self.client.futures_exchange_info()

    def set_leverage(self, symbol, leverage):
        try:
            logger.info(f"Setting leverage for {symbol} to {leverage}x")
            response = self.client.futures_change_leverage(symbol=symbol, leverage=leverage, timestamp=self._get_timestamp())
            logger.info(f"{symbol} için kaldıraç ayarlandı: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"{symbol} için kaldıraç {leverage}x olarak ayarlanırken Binance API İstisnası: {e}")
            # Örnek: e.code == -4048 (Kaldıraç değiştirilmedi) - zaten ayarlıysa bir hata olmayabilir
            if e.code == -4048: # "Leverage not changed"
                logger.info(f"{symbol} için kaldıraç zaten {leverage}x olarak ayarlanmış veya değişiklik gerekmiyor.")
                return True # Zaten istenen kaldıraçsa başarılı olarak kabul et
            # Gerekirse daha spesifik hata kodu yönetimi ekleyin
            # ör. -4003: Mevcut pozisyon büyüklüğü/bakiye için kaldıraç çok yüksekse "Miktar geçerli değil"
            # ör. -4028: Kaldıraç izin verilen aralığın dışındaysa "{symbol} için {leverage} kaldıracı geçerli değil"
            self.telegram_notifier.notify_error(f"Kaldıraç Hatası: {symbol}", f"Kaldıraç {leverage}x olarak ayarlanamadı. Kod: {e.code}, Mesaj: {e.message}")
            return False
        except Exception as e:
            logger.error(f"Generic error setting leverage for {symbol}: {e}")
            self.telegram_notifier.notify_error(f"Kaldıraç Hatası: {symbol}", f"Kaldıraç {leverage}x olarak ayarlanırken genel hata.")
            return False

    def set_margin_type(self, symbol, margin_type):
        # margin_type "ISOLATED" veya "CROSSED" olmalıdır
        try:
            logger.info(f"{symbol} için marjin türü {margin_type} olarak ayarlanıyor")
            response = self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type.upper(), timestamp=self._get_timestamp())
            logger.info(f"{symbol} için marjin türü ayarlandı: {response}")
            return True
        except BinanceAPIException as e:
            logger.error(f"{symbol} için marjin türü {margin_type} olarak ayarlanırken Binance API İstisnası: {e}")
            # Örnek: e.code == -4046 (Marjin türünü değiştirmeye gerek yok)
            if e.code == -4046: # "No need to change margin type"
                logger.info(f"{symbol} için marjin türü zaten {margin_type} veya değişiklik gerekmiyor.")
                return True # Başarılı olarak kabul et
            # Diğer kodlar:
            # -4059: "Açık emirler veya pozisyonlar varsa marjin türü değiştirilemez."
            # Bu kritik bir durumdur. Eğer bununla karşılaşırsak, işleme devam etmemeliyiz.
            if e.code == -4059:
                 logger.error(f"KRİTİK: Mevcut açık emirler veya pozisyonlar nedeniyle {symbol} için marjin türü {margin_type} olarak değiştirilemiyor. Değişiklik gerekliyse manuel müdahale gerekebilir.")
                 self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"Açık emirler/pozisyonlar nedeniyle marjin türü {margin_type} olarak değiştirilemiyor. Manuel kontrol gerekli.")
                 return False # Bu, bu işlem için kesin bir başarısızlıktır
            self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"Marjin türü {margin_type} olarak ayarlanamadı. Kod: {e.code}, Mesaj: {e.message}")
            return False
        except Exception as e:
            logger.error(f"Generic error setting margin type for {symbol}: {e}")
            self.telegram_notifier.notify_error(f"Marjin Türü Hatası: {symbol}", f"Marjin türü {margin_type} olarak ayarlanırken genel hata.")
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
        return (Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN)) # Veya ROUND_NEAREST

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
        return 0 # Veya istisna yükselt

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

            # minNotional kontrol et
            min_notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
            if min_notional_filter:
                min_notional = float(min_notional_filter['notional'])
                if float(adjusted_quantity) * entry_price < min_notional:
                    logger.warning(f"Calculated notional ({float(adjusted_quantity) * entry_price}) for {symbol} is less than minNotional ({min_notional}). Cannot place order.")
                    return None # Veya istenirse ve mümkünse minNotional'ı karşılamak için ayarla
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
            'side': side, # 'AL' veya 'SAT'
            'quantity': quantity,
            'timestamp': self._get_timestamp()
        }

        if order_type:
            params['type'] = order_type
        else: # Fiyat sağlanmışsa varsayılan olarak LIMIT, aksi takdirde MARKET
            params['type'] = FUTURE_ORDER_TYPE_LIMIT if price else FUTURE_ORDER_TYPE_MARKET

        if params['type'] == FUTURE_ORDER_TYPE_LIMIT:
            if not price:
                logger.error("Price is required for LIMIT order.")
                return None
            if price_precision:
                params['price'] = self._adjust_price_to_tick(price, price_precision)
            else:
                params['price'] = price
            params['timeInForce'] = TIME_IN_FORCE_GTC # İptal Edilene Kadar Geçerli

        if params['type'] in [FUTURE_ORDER_TYPE_STOP_MARKET, FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET]:
            if not stop_price:
                logger.error("Stop price is required for STOP_MARKET or TAKE_PROFIT_MARKET orders.")
                return None
            if price_precision:
                 params['stopPrice'] = self._adjust_price_to_tick(stop_price, price_precision)
            else:
                params['stopPrice'] = stop_price
            params['reduceOnly'] = False # Başlangıç SL için reduceOnly değildir. TP için olabilir.

        # STOP veya TAKE_PROFIT emirleri (piyasa dışı) için fiyat da gereklidir.
        # FUTURE_ORDER_TYPE_STOP, FUTURE_ORDER_TYPE_TAKE_PROFIT (Limit emirleri için)

        logger.info(f"Placing order with params: {params}")
        try:
            # Gerekirse kaldıracın ayarlandığından emin olun (genellikle sembol başına, bir kez)
            # self.client.futures_change_leverage(symbol=symbol, leverage=config.LEVERAGE, timestamp=self._get_timestamp())
            # Gerekirse marjin türünün ayarlandığından emin olun (İZOLÉ veya ÇAPRAZ)
            # self.client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED', timestamp=self._get_timestamp())

            order = self.client.futures_create_order(**params)
            logger.info(f"Order placed successfully: {order}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API Exception placing order: {e.message} (Code: {e.code}) - Params: {params}")
            # Örnek: Marjin hatalarını yönetin, ör. e.code == -2019 (Marjin yetersiz.)
        except BinanceOrderException as e:
            logger.error(f"Binance Order Exception placing order: {e} - Params: {params}")
        except Exception as e:
            logger.error(f"Generic error placing order: {e} - Params: {params}")
        return None

    def create_entry_order(self, symbol, signal_type, entry_price, quantity):
        side = SIDE_BUY if signal_type == 'long' else SIDE_SELL
        order_type = config.ORDER_TYPES.get('entry', 'LIMIT').upper() # Varsayılan olarak LIMIT

        if order_type == 'LIMIT':
            return self.place_futures_order(symbol, side, quantity, price=entry_price, order_type=FUTURE_ORDER_TYPE_LIMIT)
        elif order_type == 'MARKET':
            # Piyasa emri, yerleştirme için doğrudan entry_price kullanmaz, ancak SL hesaplaması için kullanışlıdır
            return self.place_futures_order(symbol, side, quantity, order_type=FUTURE_ORDER_TYPE_MARKET)
        else:
            logger.error(f"Unsupported entry order type: {order_type}")
            return None

    def create_stop_loss_order(self, symbol, signal_type, entry_price, quantity_for_sl):
        sl_pct = config.STOP_LOSS

        if signal_type == 'long':
            side = SIDE_SELL
            stop_price = entry_price * (1 - sl_pct)
        else: # short (kısa)
            side = SIDE_BUY
            stop_price = entry_price * (1 + sl_pct)

        stop_order_type_str = config.ORDER_TYPES.get('stoploss', 'MARKET').upper() # Varsayılan olarak MARKET (STOP_MARKET)

        binance_stop_order_type = None
        if stop_order_type_str == 'MARKET': # Bu, Binance için STOP_MARKET anlamına gelir
            binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET
        elif stop_order_type_str == 'LIMIT': # Bu, STOP anlamına gelir (stop fiyatına ulaşıldıktan sonra bir limit emri)
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP
             # Bir STOP LIMIT emri için, bir 'price' parametresine de ihtiyacınız olacaktır (stop için limit fiyatı)
             # Basitlik için, 'stoploss_on_exchange': true uyarınca STOP_MARKET kullanacağız
             logger.warning("STOP LIMIT SL orders require a limit price. Defaulting to STOP_MARKET behavior if not provided.")
             # Şimdilik burada yalnızca STOP_MARKET tam olarak uygulanmıştır.
             binance_stop_order_type = FUTURE_ORDER_TYPE_STOP_MARKET


        if not binance_stop_order_type:
            logger.error(f"Unsupported stoploss order type: {stop_order_type_str}")
            return None

        logger.info(f"Creating SL for {symbol}: side={side}, stop_price={stop_price}, entry_price={entry_price}, quantity={quantity_for_sl}")

        # STOP_MARKET için 'price' parametresi kullanılmaz. 'stopPrice' tetikleyicidir.
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

        side = SIDE_SELL if position_amt > 0 else SIDE_BUY # Uzun pozisyondaysa, kapatmak için sat. Kısa pozisyondaysa, kapatmak için al.
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

# Örnek kullanım (bu modülü doğrudan test etmek için)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("Testing BinanceFuturesClient...")

    # Bu dosyayı doğrudan çalıştırırsanız config.py dosyasında sahte anahtarların veya gerçek (testnet) anahtarların olduğundan emin olun
    if config.BINANCE_API_KEY == "YOUR_BINANCE_API_KEY" or config.BINANCE_API_SECRET == "YOUR_BINANCE_API_SECRET":
        logger.warning("Using placeholder API keys. Live tests will fail.")
        # exit() # Yer tutucu anahtarlarla çalışmasını önlemek için yorum satırını kaldırın

    # Geliştirme ve test için Binance Testnet kullanılması şiddetle tavsiye edilir.
    # client.API_URL = 'https://testnet.binance.vision/api'
    # client.FUTURES_URL = 'https://testnet.binancefuture.com'

    futures_client = BinanceFuturesClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

    # Bağlantıyı ve zaman senkronizasyonunu test et
    logger.info(f"Server time offset: {futures_client.server_time_offset} ms")

    # Bakiye almayı test et
    usdt_balance = futures_client.get_usdt_balance()
    logger.info(f"Current USDT balance: {usdt_balance}")

    # Açık pozisyon sayısını almayı test et
    open_positions_count = futures_client.get_open_positions_count()
    logger.info(f"Current open positions: {open_positions_count}")

    # Sembol bilgilerini ve hesaplamalarını test et (geçerli bir vadeli işlem sembolü kullanın)
    test_symbol = "BTCUSDT" # Bunun config.TRADING_PAIRS içinde olduğundan emin olun
    if test_symbol not in config.TRADING_PAIRS:
        logger.warning(f"{test_symbol} not in TRADING_PAIRS, some tests might be misleading.")

    symbol_info = futures_client.get_symbol_info(test_symbol)
    if symbol_info:
        logger.info(f"Symbol info for {test_symbol}: Retrieved")
        # logger.info(f"Symbol info for {test_symbol}: {symbol_info}") # Çok ayrıntılı

        # Pozisyon büyüklüğü hesaplamasını test et
        # entry_price'ın test_symbol için gerçekçi olduğundan emin olun
        # Örnek: mevcut BTC fiyatı 60000$
        # test_entry_price = 60000
        # if usdt_balance > 0 and test_entry_price > 0:
        #     calculated_size = futures_client.calculate_position_size(test_symbol, usdt_balance, test_entry_price)
        #     logger.info(f"Calculated position size for {test_symbol} at ${test_entry_price}: {calculated_size}")
        # else:
        #    logger.warning("Cannot test position size calculation without balance or valid entry price.")
    else:
        logger.error(f"Could not get symbol info for {test_symbol}. Further tests involving this symbol might fail.")

    # --- Emir verme testi (DİKKAT: GERÇEK VEYA TESTNET FONLARINI KULLANIR) ---
    # TESTNET'te olduğunuzdan veya canlı yayındaysanız çok küçük miktarlar kullandığınızdan emin olun.
    # Ve sembolün doğru şekilde yapılandırıldığından (kaldıraç, marjin türü).
    #
    # Örnek: Küçük bir test emri verin
    # test_entry_price = 60000 # BTCUSDT için varsayımsal bir fiyat
    # test_quantity = 0.001 # Küçük bir BTC miktarı
    #
    # if symbol_info and calculated_size: # Mevcut ve geçerliyse calculated_size kullanın
    #    test_quantity = calculated_size
    #
    # if open_positions_count < config.MAX_OPEN_TRADES and test_quantity > 0:
    #     logger.info(f"Attempting to place a test LIMIT BUY order for {test_quantity} {test_symbol} at ${test_entry_price * 0.95}") # Varsayımsal fiyatın %5 altında al
    #     # Limit emirleri için fiyatın tick boyutuna ayarlandığından emin olun
    #     adjusted_test_price = futures_client._adjust_price_to_tick(test_entry_price * 0.95, symbol_info['filters'][0]['tickSize']) # Fiyat filtresinin ilk olduğunu varsayıyoruz
    #
    #     entry_order = futures_client.create_entry_order(test_symbol, 'long', float(adjusted_test_price), test_quantity)
    #     if entry_order:
    #         logger.info(f"Test entry order placed: {entry_order}")
    #         # Örnek: Bu test emri için bir zarar durdurma emri verin
    #         # SL için miktar, açılan pozisyonun miktarıyla eşleşmelidir.
    #         # Emir hemen GERÇEKLEŞİRSE (ör. piyasa emri veya agresif limit), emir yanıtından gerçek giriş fiyatını alabilirsiniz.
    #         # Bir GTC LIMIT emri için hemen dolmayabilir. SL kurulumu, doldurulmuş pozisyon bilgisi gerektirir.
    #         # Bu doğrudan test için, SL hesaplaması için adjusted_test_price'ta dolduğunu varsayıyoruz.
    #         sl_order = futures_client.create_stop_loss_order(test_symbol, 'long', float(adjusted_test_price), test_quantity)
    #         if sl_order:
    #             logger.info(f"Test SL order placed: {sl_order}")
    #     else:
    #         logger.error("Test entry order failed.")
    # else:
    #    logger.warning(f"Test emri verme atlanıyor. Açık pozisyonlar: {open_positions_count}, Maks: {config.MAX_OPEN_TRADES}, Test Miktarı: {test_quantity}")

    # Belirli bir açık pozisyonu getirme testi
    # open_pos_btc = futures_client.get_open_position_for_symbol(test_symbol)
    # if open_pos_btc:
    #    logger.info(f"Open position for {test_symbol}: Amount {open_pos_btc['positionAmt']}")
        # Bu pozisyonu kapatma testi
        # close_order = futures_client.close_position_market(test_symbol, open_pos_btc['positionAmt'])
        # if close_order:
        #    logger.info(f"Market close order for {test_symbol} placed: {close_order}")
    # else:
    #    logger.info(f"No open position found for {test_symbol} to test closing.")

    logger.info("BinanceFuturesClient testing finished.")

import redis
import json
import logging
import config # Uygulamanın yapılandırma dosyasını içe aktar

logger = logging.getLogger(__name__)

class RedisClient:
    def __init__(self):
        """
        Redis istemcisini başlatır, ana yapılandırma dosyasındaki ayarları
        kullanarak Redis sunucusuna bağlanır.
        """
        self.redis_url = config.REDIS_URL
        self.db = getattr(config, 'REDIS_DB', 0) # Belirtilmemişse varsayılan olarak DB 0 kullanılır
        self.client = None
        self._connect()

    def _connect(self):
        """
        Redis sunucusuna bağlantı kurar.
        Bağlantı hatalarını yönetir.
        """
        if not self.redis_url:
            logger.error("REDIS_URL yapılandırılmamış. Redis istemcisi başlatılamıyor.")
            # Potansiyel olarak bir istisna yükseltilebilir veya bu durum uygun şekilde yönetilebilir
            # Şimdilik, istemci None ise işlemler başarısız olacaktır
            return

        try:
            logger.info(f"Redis'e {self.redis_url} adresinden, DB: {self.db} üzerinden bağlanılıyor")
            # `from_url` metodu, URL'yi ayrıştırmayı ve bağlantıyı kurmayı yönetir.
            # ssl_cert_reqs=None, özel bir CA kullanılmıyorsa Heroku Redis için genellikle gereklidir
            # decode_responses=True, tüm dize sonuçlarının baytlardan str'ye otomatik olarak çözülmesini sağlar
            self.client = redis.Redis.from_url(self.redis_url, db=self.db, ssl_cert_reqs=None, decode_responses=False)
            # Bağlantıyı test et
            self.client.ping()
            logger.info("Redis'e başarıyla bağlanıldı.")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis'e bağlanılamadı: {e}", exc_info=True)
            self.client = None # Bağlantı başarısız olursa istemcinin None olduğundan emin olun
        except Exception as e: # Redis istemcisi başlatılırken diğer olası hataları yakala
            logger.error(f"Redis başlatılırken beklenmeyen bir hata oluştu: {e}", exc_info=True)
            self.client = None

    def is_connected(self):
        """İstemcinin Redis'e bağlı olup olmadığını kontrol eder."""
        if self.client:
            try:
                self.client.ping()
                return True
            except redis.exceptions.ConnectionError:
                logger.warning("Redis bağlantısı kesildi. Yeniden bağlanmaya çalışılıyor...")
                self._connect() # Yeniden bağlanmayı dene
                if self.client and self.client.ping(): # Yeniden bağlanma denemesinden sonra tekrar kontrol et
                    logger.info("Redis'e başarıyla yeniden bağlanıldı.")
                    return True
                logger.error("Redis'e yeniden bağlanılamadı.")
                return False
        return False

    def set_trade(self, symbol: str, trade_data: dict):
        """
        Belirli bir sembol için işlem verilerini Redis'te saklar.
        trade_data sözlüğü bir JSON dizesine serileştirilir.
        Anahtar formatı: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Redis'e bağlı değil. {symbol} için işlem ayarlanamıyor.")
            return False
        try:
            key = f"active_trade:{symbol}"
            serialized_data = json.dumps(trade_data)
            self.client.set(key, serialized_data)
            logger.debug(f"{symbol} için işlem verileri Redis'te saklandı. Anahtar: {key}")
            return True
        except redis.exceptions.RedisError as e:
            logger.error(f"{symbol} için işlem ayarlanırken Redis hatası: {e}", exc_info=True)
            return False
        except json.JSONDecodeError as e:
            logger.error(f"{symbol} için işlem ayarlanırken JSON serileştirme hatası: {e}", exc_info=True)
            return False


    def get_trade(self, symbol: str) -> dict | None:
        """
        Bir sembol için işlem verilerini Redis'ten alır.
        Verileri JSON dizesinden bir sözlüğe deserileştirir.
        Sembol bulunamazsa veya bir hata oluşursa None döndürür.
        Anahtar formatı: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Redis'e bağlı değil. {symbol} için işlem alınamıyor.")
            return None
        try:
            key = f"active_trade:{symbol}"
            serialized_data = self.client.get(key)
            if serialized_data:
                trade_data = json.loads(serialized_data.decode('utf-8')) # Baytları str'ye çözdükten sonra json.loads
                logger.debug(f"{symbol} için işlem verileri Redis'ten alındı. Anahtar: {key}")
                return trade_data
            else:
                logger.debug(f"{symbol} için Redis'te işlem verisi bulunamadı. Anahtar: {key}")
                return None
        except redis.exceptions.RedisError as e:
            logger.error(f"{symbol} için işlem alınırken Redis hatası: {e}", exc_info=True)
            return None
        except json.JSONDecodeError as e:
            logger.error(f"{symbol} için işlem alınırken JSON deserileştirme hatası: {e}", exc_info=True)
            return None

    def delete_trade(self, symbol: str):
        """
        Bir sembol için işlem verilerini Redis'ten siler.
        Anahtar formatı: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Redis'e bağlı değil. {symbol} için işlem silinemiyor.")
            return False
        try:
            key = f"active_trade:{symbol}"
            result = self.client.delete(key)
            if result > 0:
                logger.info(f"{symbol} için işlem verileri Redis'ten silindi. Anahtar: {key}")
            else:
                logger.info(f"{symbol} için Redis'te silinecek işlem verisi bulunamadı (veya zaten silinmiş). Anahtar: {key}")
            return True # Anahtar mevcut olmasa bile True döndürür, Redis `del` davranışına göre
        except redis.exceptions.RedisError as e:
            logger.error(f"{symbol} için işlem silinirken Redis hatası: {e}", exc_info=True)
            return False

    def get_all_trade_symbols(self) -> list[str]:
        """
        Aktif işlemler için tüm sembolleri (önek olmadan anahtarlar) alır.
        "active_trade:*" kalıbıyla eşleşen anahtarları tarar.
        """
        if not self.is_connected():
            logger.error("Redis'e bağlı değil. Tüm işlem sembolleri alınamıyor.")
            return []
        symbols = []
        try:
            # Çok sayıda anahtarla bellek verimliliği için scan_iter kullanın
            for key_bytes in self.client.scan_iter(match="active_trade:*"):
                key_str = key_bytes.decode('utf-8')
                symbol = key_str.split(":", 1)[1] # "active_trade:SYMBOL" dan sembol kısmını çıkarın
                symbols.append(symbol)
            logger.debug(f"Redis'ten {len(symbols)} aktif işlem sembolü alındı.")
            return symbols
        except redis.exceptions.RedisError as e:
            logger.error(f"Tüm işlem sembolleri alınırken Redis hatası: {e}", exc_info=True)
            return []

    def get_all_trades(self) -> dict[str, dict]:
        """
        Tüm aktif işlemleri Redis'ten alır.
        Anahtarların semboller ve değerlerin trade_data sözlükleri olduğu bir sözlük döndürür.
        """
        if not self.is_connected():
            logger.error("Redis'e bağlı değil. Tüm işlemler alınamıyor.")
            return {}

        trades = {}
        trade_symbols = self.get_all_trade_symbols()
        for symbol in trade_symbols:
            trade_data = self.get_trade(symbol)
            if trade_data:
                trades[symbol] = trade_data
            else:
                # Bu, bir anahtar mevcutsa ancak verilerini getirme başarısız olursa veya tarama ile get arasında bir anahtarın süresi dolarsa olabilir
                logger.warning(f"Anahtarlarda listelenen {symbol} sembolü için işlem verileri alınamadı. Eş zamanlı olarak silinmiş olabilir.")
        logger.debug(f"Redis'ten {len(trades)} işlem için veri alındı.")
        return trades

# Örnek kullanım (isteğe bağlı, test amaçlı)
if __name__ == '__main__':
    # Bu bölüm yalnızca redis_client.py doğrudan çalıştırıldığında çalışacaktır.
    # config.py dosyasının, özellikle REDIS_URL'nin ayarlanmış olmasını gerektirir.
    # Heroku için REDIS_URL genellikle bir ortam değişkenidir.
    # Yerel test için, config.py'yi yüklemek üzere uyarlarsanız, config.REDIS_URL'yi manuel olarak veya bir .env dosyası aracılığıyla ayarlamanız gerekebilir.

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
    logger.info("RedisClient'ı test için bağımsız olarak çalıştırmaya çalışılıyor...")

    # --- ÖNEMLİ YEREL TEST NOTU ---
    # Bu testin çalışması için config.py dosyanızda geçerli bir REDIS_URL olduğundan emin olun.
    # Örneğin, config.py dosyasında geçici olarak ayarlayabilirsiniz:
    # config.REDIS_URL = "redis://localhost:6379/0"
    # Veya, config.py oradan yüklüyorsa ortam değişkeninin ayarlandığından emin olun.
    if not config.REDIS_URL:
        logger.warning("config.REDIS_URL ayarlanmadı. Ortam üzerinden kullanılabilir olmadıkça bağımsız test Redis'e bağlanamayabilir.")
        # Ayarlanmadıysa yerel test için varsayılan ayarlama denemesi - bu bir yedek çözümdür
        # Gerçek bir senaryoda, config.py bunu os.environ'dan yüklemeyi yönetmelidir
        # Bu alt görev için, config.py'nin yerel ortam değişkenleri için henüz tam olarak uyarlanmamış olabileceğini varsayıyoruz
        # Bu nedenle, yerel geliştirme için yaygın bir varsayılan sağlıyoruz:
        if 'REDIS_URL' not in dir(config) or not config.REDIS_URL:
             print("config.REDIS_URL ayarlanmadığı için yerel test için yama yapılıyor. 'redis://localhost:6379/0' kullanılıyor")
             config.REDIS_URL = "redis://localhost:6379/0"


    redis_client_instance = RedisClient()

    if redis_client_instance.is_connected():
        logger.info("Redis istemcisi test için bağlandı.")

        # Test verileri
        test_symbol_1 = "BTCUSDT_TEST"
        test_data_1 = {"entry_price": 40000, "quantity": 0.01, "side": "long", "sl_order_id": "12345"}
        test_symbol_2 = "ETHUSDT_TEST"
        test_data_2 = {"entry_price": 3000, "quantity": 0.1, "side": "short", "sl_order_id": "67890"}

        # Önceki test anahtarlarını temizle
        redis_client_instance.delete_trade(test_symbol_1)
        redis_client_instance.delete_trade(test_symbol_2)

        # set_trade test et
        logger.info(f"{test_symbol_1} için işlem ayarlanıyor...")
        redis_client_instance.set_trade(test_symbol_1, test_data_1)

        logger.info(f"{test_symbol_2} için işlem ayarlanıyor...")
        redis_client_instance.set_trade(test_symbol_2, test_data_2)

        # get_trade test et
        logger.info(f"{test_symbol_1} için işlem alınıyor...")
        retrieved_data_1 = redis_client_instance.get_trade(test_symbol_1)
        if retrieved_data_1 == test_data_1:
            logger.info(f"BAŞARILI: {test_symbol_1} için get_trade doğru veriyi döndürdü.")
        else:
            logger.error(f"BAŞARISIZ: {test_symbol_1} için get_trade {retrieved_data_1} döndürdü, beklenen {test_data_1}")

        # get_all_trade_symbols test et
        logger.info("Tüm işlem sembolleri alınıyor...")
        symbols = redis_client_instance.get_all_trade_symbols()
        expected_symbols = sorted([test_symbol_1, test_symbol_2])
        if sorted(symbols) == expected_symbols:
            logger.info(f"BAŞARILI: get_all_trade_symbols {symbols} döndürdü.")
        else:
            logger.error(f"BAŞARISIZ: get_all_trade_symbols {symbols} döndürdü, beklenen {expected_symbols}")


        # get_all_trades test et
        logger.info("Tüm işlemler alınıyor...")
        all_trades = redis_client_instance.get_all_trades()
        if test_symbol_1 in all_trades and all_trades[test_symbol_1] == test_data_1 and            test_symbol_2 in all_trades and all_trades[test_symbol_2] == test_data_2:
            logger.info(f"BAŞARILI: get_all_trades {len(all_trades)} işlem için doğru veriyi döndürdü.")
        else:
            logger.error(f"BAŞARISIZ: get_all_trades {all_trades} döndürdü, bireysel girişleri kontrol edin.")


        # delete_trade test et
        logger.info(f"{test_symbol_1} için işlem siliniyor...")
        redis_client_instance.delete_trade(test_symbol_1)
        if redis_client_instance.get_trade(test_symbol_1) is None:
            logger.info(f"BAŞARILI: {test_symbol_1} için delete_trade başarılı.")
        else:
            logger.error(f"BAŞARISIZ: {test_symbol_1} için delete_trade başarısız, işlem hala mevcut.")

        # Kalan test anahtarını temizle
        redis_client_instance.delete_trade(test_symbol_2)
        logger.info("Redis istemci testleri tamamlandı.")
    else:
        logger.error("Redis istemcisi bağlı DEĞİL. Testler çalıştırılamıyor.")
        logger.error("Lütfen Redis sunucusunun çalıştığından ve config.py dosyasındaki REDIS_URL'nin ortamınız için doğru şekilde ayarlandığından emin olun.")

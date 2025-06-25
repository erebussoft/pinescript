import sqlite3
import json # Sözlükleri string olarak saklamak yerine ayrı sütunlar kullanacağız
import logging
import time # Zaman damgaları için

logger = logging.getLogger(__name__)

class DatabaseHandler:
    def __init__(self, db_file):
        """
        Veritabanı yöneticisini başlatır ve SQLite veritabanına bağlanır.
        Gerekirse 'active_trades' tablosunu oluşturur.
        """
        self.db_file = db_file
        self.conn = None
        try:
            self.conn = sqlite3.connect(self.db_file, check_same_thread=False) # check_same_thread=False TSL thread'i için önemli
            self.conn.row_factory = sqlite3.Row # Sütun adlarıyla erişim için
            logger.info(f"'{self.db_file}' veritabanına başarıyla bağlanıldı.")
            self._create_tables()
        except sqlite3.Error as e:
            logger.error(f"'{self.db_file}' veritabanına bağlanırken veya tablo oluşturulurken hata: {e}", exc_info=True)
            # Bu durumda botun devam etmesi riskli olabilir, ana uygulamada kontrol edilmeli.
            raise  # Hatayı yeniden fırlat ki initialize_services bunu yakalayabilsin

    def _execute_query(self, query, params=(), fetch_one=False, fetch_all=False, commit=False):
        """
        Veritabanı sorgularını çalıştırmak için genel bir yardımcı metod.
        """
        if not self.conn:
            logger.error("Veritabanı bağlantısı yok. Sorgu çalıştırılamıyor.")
            return None # Veya istisna fırlat

        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)

            if commit:
                self.conn.commit()
                logger.debug(f"Sorgu başarıyla çalıştırıldı ve commit edildi: {query[:50]}...")
                return cursor.lastrowid # INSERT için faydalı olabilir

            if fetch_one:
                result = cursor.fetchone()
                logger.debug(f"Sorgu (fetch_one) başarıyla çalıştırıldı: {query[:50]}... Sonuç: {'Var' if result else 'Yok'}")
                return result

            if fetch_all:
                result = cursor.fetchall()
                logger.debug(f"Sorgu (fetch_all) başarıyla çalıştırıldı: {query[:50]}... {len(result)} satır döndü.")
                return result

            return True # execute başarılı olduysa (commit veya fetch olmadan)
        except sqlite3.Error as e:
            logger.error(f"Sorgu çalıştırılırken SQLite hatası: {query[:100]}... Parametreler: {params} Hata: {e}", exc_info=True)
            # Gerekirse self.conn.rollback() yapılabilir, ancak genellikle commit öncesi hatalarda gerekmez.
            return None # Veya istisna fırlat
        finally:
            if cursor:
                cursor.close()

    def _create_tables(self):
        """
        Gerekli veritabanı tablolarını oluşturur (eğer mevcut değillerse).
        """
        create_table_query = """
        CREATE TABLE IF NOT EXISTS active_trades (
            symbol TEXT PRIMARY KEY,
            entry_order_id TEXT,
            sl_order_id TEXT,
            current_sl_price REAL,
            entry_price REAL,
            quantity REAL,
            signal_type TEXT,
            status TEXT,
            trailing_active INTEGER,
            highest_price_since_trailing_activation REAL,
            lowest_price_since_trailing_activation REAL,
            timestamp REAL
        );
        """
        if self._execute_query(create_table_query, commit=False): # Tablo oluşturma sorgusu commit gerektirmez (IF NOT EXISTS)
                                                              # Aslında CREATE TABLE bir DDL olduğu için implicit commit yapar çoğu DB'de.
                                                              # Ancak sqlite3'te commit ile DDL sonrası değişiklikleri garantilemek iyi olabilir.
                                                              # Ama execute_query commit'i sadece commit=True ise yapıyor.
                                                              # Şimdilik commit=False ile bırakalım, testlerde sorun olursa düzeltiriz.
                                                              # Düzeltme: CREATE TABLE IF NOT EXISTS commit gerektirmez.
            logger.info("'active_trades' tablosu başarıyla oluşturuldu veya zaten mevcut.")
        else:
            logger.error("'active_trades' tablosu oluşturulamadı.")
            # Bu kritik bir hata, botun devam etmemesi gerekebilir. __init__ içinde raise ediliyor.

    def set_trade(self, symbol: str, trade_data: dict):
        """
        Verilen sembol için işlem verilerini 'active_trades' tablosuna ekler veya günceller.
        trade_data sözlüğü, tablo sütunlarıyla eşleşen anahtarlar içermelidir.
        """
        # Sütunların sırasının ve adlarının CREATE TABLE ile eşleştiğinden emin olun
        # `timestamp` anahtarının trade_data içinde olduğundan emin olun, yoksa time.time() kullanın
        trade_data.setdefault('timestamp', time.time())

        # trailing_active boolean ise INTEGER'a çevir (0 veya 1)
        if 'trailing_active' in trade_data and isinstance(trade_data['trailing_active'], bool):
            trade_data['trailing_active'] = int(trade_data['trailing_active'])

        query = """
        INSERT OR REPLACE INTO active_trades (
            symbol, entry_order_id, sl_order_id, current_sl_price, entry_price,
            quantity, signal_type, status, trailing_active,
            highest_price_since_trailing_activation, lowest_price_since_trailing_activation, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            symbol,
            trade_data.get('entry_order_id'),
            trade_data.get('sl_order_id'),
            trade_data.get('current_sl_price'),
            trade_data.get('entry_price'),
            trade_data.get('quantity'),
            trade_data.get('signal_type'),
            trade_data.get('status', 'open'), # Varsayılan durum 'open'
            trade_data.get('trailing_active', 0), # Varsayılan 0 (False)
            trade_data.get('highest_price_since_trailing_activation'),
            trade_data.get('lowest_price_since_trailing_activation'),
            trade_data.get('timestamp')
        )
        if self._execute_query(query, params, commit=True):
            logger.info(f"{symbol} için işlem verileri veritabanına kaydedildi/güncellendi.")
            return True
        else:
            logger.error(f"{symbol} için işlem verileri kaydedilirken/güncellenirken hata oluştu.")
            return False

    def get_trade(self, symbol: str) -> dict | None:
        """
        Belirli bir sembol için işlem detaylarını tablodan çeker.
        Bulunamazsa None döndürür.
        """
        query = "SELECT * FROM active_trades WHERE symbol = ?;"
        params = (symbol,)
        row = self._execute_query(query, params, fetch_one=True)
        if row:
            # sqlite3.Row nesnesini sözlüğe çevir
            trade_dict = dict(row)
            # trailing_active INTEGER ise boolean'a çevir
            if 'trailing_active' in trade_dict and trade_dict['trailing_active'] is not None:
                trade_dict['trailing_active'] = bool(trade_dict['trailing_active'])
            logger.debug(f"{symbol} için işlem detayları veritabanından alındı.")
            return trade_dict
        else:
            logger.debug(f"{symbol} için veritabanında işlem bulunamadı.")
            return None

    def delete_trade(self, symbol: str) -> bool:
        """
        Belirli bir sembol için işlemi tablodan siler.
        """
        query = "DELETE FROM active_trades WHERE symbol = ?;"
        params = (symbol,)
        # _execute_query commit=True ile çağrıldığında lastrowid veya True/None döner.
        # Silme işleminin başarılı olup olmadığını kontrol etmek için cursor.rowcount kullanılabilir,
        # ancak _execute_query bunu doğrudan döndürmüyor. Şimdilik True/None dönüşüne güvenelim.
        if self._execute_query(query, params, commit=True) is not None: # None değilse başarılı kabul edelim
            logger.info(f"{symbol} işlemi veritabanından silindi.")
            return True
        else:
            logger.error(f"{symbol} işlemi silinirken hata oluştu veya sembol bulunamadı.")
            return False

    def get_all_trades(self) -> dict[str, dict]:
        """
        'active_trades' tablosundaki tüm kayıtları çeker.
        {symbol: trade_details_dict} formatında bir sözlük döndürür.
        """
        query = "SELECT * FROM active_trades;"
        rows = self._execute_query(query, fetch_all=True)
        trades = {}
        if rows:
            for row in rows:
                trade_dict = dict(row)
                # trailing_active INTEGER ise boolean'a çevir
                if 'trailing_active' in trade_dict and trade_dict['trailing_active'] is not None:
                    trade_dict['trailing_active'] = bool(trade_dict['trailing_active'])
                trades[trade_dict['symbol']] = trade_dict
        logger.debug(f"Veritabanından {len(trades)} aktif işlem alındı.")
        return trades

    def close_connection(self):
        """
        Veritabanı bağlantısını kapatır.
        """
        if self.conn:
            try:
                self.conn.close()
                logger.info(f"'{self.db_file}' veritabanı bağlantısı kapatıldı.")
            except sqlite3.Error as e:
                logger.error(f"Veritabanı bağlantısı kapatılırken hata: {e}", exc_info=True)

# Örnek kullanım (bu modülü doğrudan test etmek için)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
    # Geçici bir test veritabanı dosyası kullan
    test_db_file = "test_trades.db"
    import os
    if os.path.exists(test_db_file):
        os.remove(test_db_file) # Her test öncesi temizle

    db_handler = None
    try:
        logger.info(f"DatabaseHandler'ı '{test_db_file}' ile test için başlatılıyor...")
        db_handler = DatabaseHandler(test_db_file)

        # Test verileri
        test_symbol_1 = "BTCUSDT_TEST"
        test_data_1 = {
            'entry_order_id': "order123", 'sl_order_id': "sl456", 'current_sl_price': 39000.0,
            'entry_price': 40000.0, 'quantity': 0.01, 'signal_type': "long",
            'status': "open", 'trailing_active': False,
            'highest_price_since_trailing_activation': 40000.0,
            'lowest_price_since_trailing_activation': float('inf'),
            'timestamp': time.time()
        }

        test_symbol_2 = "ETHUSDT_TEST"
        test_data_2 = {
            'entry_order_id': "order789", 'sl_order_id': "sl012", 'current_sl_price': 3100.0,
            'entry_price': 3000.0, 'quantity': 0.1, 'signal_type': "short",
            'status': "open", 'trailing_active': True, # Integer olarak 1 saklanacak
            'highest_price_since_trailing_activation': 0.0,
            'lowest_price_since_trailing_activation': 3000.0,
            'timestamp': time.time()
        }

        # set_trade test et
        logger.info(f"'{test_symbol_1}' için işlem ayarlanıyor...")
        db_handler.set_trade(test_symbol_1, test_data_1)

        logger.info(f"'{test_symbol_2}' için işlem ayarlanıyor...")
        db_handler.set_trade(test_symbol_2, test_data_2)

        # get_trade test et
        logger.info(f"'{test_symbol_1}' için işlem alınıyor...")
        retrieved_data_1 = db_handler.get_trade(test_symbol_1)

        # Karşılaştırma için test_data_1'deki boolean'ı integer'a çevir (eğer varsa)
        # ve None değerlerini kontrol et. SQLite'dan gelen dict ile eşleşmesi için.
        test_data_1_comp = test_data_1.copy()
        test_data_1_comp['trailing_active'] = bool(test_data_1_comp['trailing_active']) # get_trade bool'a çeviriyor

        if retrieved_data_1 and all(retrieved_data_1.get(k) == v for k, v in test_data_1_comp.items()):
             logger.info(f"BAŞARILI: get_trade ({test_symbol_1}) doğru veriyi döndürdü.")
        else:
            logger.error(f"BAŞARISIZ: get_trade ({test_symbol_1}) DÖNEN: {retrieved_data_1}, BEKLENEN (benzeri): {test_data_1_comp}")


        # get_all_trades test et
        logger.info("Tüm işlemler alınıyor...")
        all_trades = db_handler.get_all_trades()
        if len(all_trades) == 2 and test_symbol_1 in all_trades and test_symbol_2 in all_trades:
            logger.info(f"BAŞARILI: get_all_trades {len(all_trades)} işlem için doğru veriyi döndürdü.")
            # Daha detaylı karşılaştırma yapılabilir
        else:
            logger.error(f"BAŞARISIZ: get_all_trades {len(all_trades)} işlem döndürdü, beklenen 2. Alınan: {all_trades}")

        # delete_trade test et
        logger.info(f"'{test_symbol_1}' için işlem siliniyor...")
        db_handler.delete_trade(test_symbol_1)
        if db_handler.get_trade(test_symbol_1) is None:
            logger.info(f"BAŞARILI: delete_trade ({test_symbol_1}) başarılı.")
        else:
            logger.error(f"BAŞARISIZ: delete_trade ({test_symbol_1}) başarısız, işlem hala mevcut.")

        all_trades_after_delete = db_handler.get_all_trades()
        if len(all_trades_after_delete) == 1 and test_symbol_2 in all_trades_after_delete:
             logger.info(f"BAŞARILI: Silme sonrası kalan işlem sayısı doğru ({len(all_trades_after_delete)}).")
        else:
            logger.error(f"BAŞARISIZ: Silme sonrası kalan işlem sayısı yanlış ({len(all_trades_after_delete)}). Kalan: {all_trades_after_delete}")


    except Exception as e:
        logger.error(f"Test sırasında genel bir hata oluştu: {e}", exc_info=True)
    finally:
        if db_handler:
            db_handler.close_connection()
        # Test veritabanı dosyasını temizle
        # if os.path.exists(test_db_file):
        #     os.remove(test_db_file)
        # logger.info(f"'{test_db_file}' test veritabanı dosyası silindi (eğer varsa).")
        logger.info("DatabaseHandler testleri tamamlandı.")

import config
import logging
import time
# BinanceFuturesClient ve TelegramNotifier doğrudan içe aktarımları döngüsel bağımlılıkları önlemek için kaldırıldı
# Bunlar manage_trailing_stops fonksiyonuna argüman olarak geçirilecektir.
from binance.enums import * # FUTURE_ORDER_TYPE_STOP_MARKET, SIDE_SELL, SIDE_BUY için
from binance.exceptions import BinanceAPIException
# import copy # Redis'ten çektiğimiz için kaldırıldı

logger = logging.getLogger(__name__)

def manage_trailing_stops(futures_client, telegram_notifier, db_handler): # redis_client -> db_handler
    # Redis'in tek anahtarlar üzerindeki işlemleri genellikle atomik olduğundan active_trades_lock kaldırıldı.

    if not config.TRAILING_STOP or not futures_client:
        logger.debug("Trailing stop is disabled in config or futures_client not available.")
        return

    logger.debug("Veritabanından TSL yönetimi için aktif işlem verileri sorgulanıyor...") # Redis -> Veritabanından

    if not db_handler or not db_handler.conn: # redis_client -> db_handler.conn
        logger.error("Veritabanı işleyici mevcut değil veya bağlı değil. TSL döngüsü atlanıyor.") # Redis client -> Veritabanı işleyici
        return

    active_trades_map = db_handler.get_all_trades() # redis_client.get_all_trade_symbols() ve get_trade() yerine
    if not active_trades_map:
        logger.debug("Veritabanında yönetilecek aktif işlem bulunamadı.") # Redis -> Veritabanında
        return
    logger.info(f"Veritabanında {len(active_trades_map)} aktif işlem bulundu: {list(active_trades_map.keys())}") # Redis -> Veritabanında

    for symbol, trade_details in list(active_trades_map.items()): # .items() kopyası üzerinde yineleme
        # trade_details = db_handler.get_trade(symbol) # Bu artık gereksiz, döngü zaten veriyor
        # if not trade_details: # Bu kontrol de gereksiz
        #     logger.warning(f"Could not retrieve trade details for symbol {symbol} from DB, or it was deleted. Skipping.")
        #     continue

        if trade_details.get('status') != "open":
            logger.debug(f"Trade {symbol} is not open (status: {trade_details.get('status')}). Skipping TSL.")
            continue

        try:
            logger.debug(f"Managing TSL for {symbol}. Details: {trade_details}")
            position_info = futures_client.get_open_position_for_symbol(symbol)

            if not position_info or float(position_info.get('positionAmt', 0)) == 0:
                logger.info(f"Position for {symbol} (Entry: {trade_details['entry_price']}) appears closed on Binance. Removing from active_bot_trades.")

                # Mümkünse çıkış fiyatı için bilinen son gösterge fiyatını almaya çalışın
                last_mark_price_str = position_info.get('markPrice', str(trade_details['entry_price'])) if position_info else str(trade_details['entry_price'])

                try:
                    exit_price_estimate = float(last_mark_price_str)
                except ValueError:
                    exit_price_estimate = trade_details['entry_price'] # markPrice geçersizse giriş fiyatına geri dön

                unrealized_pnl_str = position_info.get('unRealizedProfit', '0') if position_info else '0'
                try:
                    closed_pnl_estimate = float(unrealized_pnl_str)
                except ValueError:
                    closed_pnl_estimate = 0.0


                telegram_notifier.notify_trade_close(
                    symbol,
                    trade_details['signal_type'],
                    exit_price_estimate,
                    trade_details['entry_price'],
                    trade_details['quantity'],
                    closed_pnl_estimate,
                    notes="Pozisyon Binance'te kapanmış görünüyor (TSL yöneticisi tarafından algılandı)."
                )
                # Anahtarı güvenle sil
                db_handler.delete_trade(symbol) # redis_client -> db_handler
                continue

            current_price = float(position_info.get('markPrice', 0))
            if current_price == 0:
                logger.warning(f"Could not get current mark price for {symbol} to manage TSL.")
                continue

            entry_price = trade_details['entry_price']
            signal_type = trade_details['signal_type']
            # Bu anahtarların mevcut olduğundan emin olun, güvenlik için yoksa varsayılanları sağlayın
            current_sl_price = trade_details.get('current_sl_price', 0.0)
            sl_order_id = trade_details.get('sl_order_id')

            pnl_ratio = 0
            if entry_price > 0: # Sıfıra bölmekten kaçının
                if signal_type == 'long':
                    pnl_ratio = (current_price - entry_price) / entry_price
                elif signal_type == 'short':
                    pnl_ratio = (entry_price - current_price) / entry_price

            if not trade_details.get('trailing_active', False) and config.TRAILING_ONLY_OFFSET_IS_REACHED:
                if pnl_ratio > config.TRAILING_STOP_POSITIVE_OFFSET:
                    trade_details['trailing_active'] = True
                    # direction_tr_tsl_act = "UZUN" if signal_type.lower() == "long" else "KISA" # For TSL message # Removed
                    if signal_type == 'long':
                        trade_details['highest_price_since_trailing_activation'] = current_price
                    elif signal_type == 'short':
                        trade_details['lowest_price_since_trailing_activation'] = current_price
                    else: # signal_type doğrulanmışsa olmamalıdır
                        trade_details['highest_price_since_trailing_activation'] = current_price # Varsayılan, ancak biri veya diğeri olmalı
                        trade_details['lowest_price_since_trailing_activation'] = current_price  # Varsayılan

                    logger.info(f"Trailing stop ACTIVATED for {symbol} at P&L ratio: {pnl_ratio:.4f}, Current Price: {current_price}")
                    if not db_handler.set_trade(symbol, trade_details): # redis_client -> db_handler
                        logger.error(f"TSL aktivasyonu sonrası {symbol} için işlem detayları Veritabanında güncellenemedi.") # Redis -> Veritabanında
                        # Veritabanındaki durum bu işlem için potansiyel olarak eski olduğundan bir sonraki sembole devam et.
                        continue # Bu döngüde bu sembol için daha fazla işlem yapmayı atla
                    telegram_notifier.send_message(f"🟢 Takip Eden Zarar Durdurma Aktifleşti ({symbol})\nSembol: {symbol}\nYön: {signal_type.upper()}\nGiriş: {entry_price:.4f}\nMevcut Fiyat: {current_price:.4f}\nKâr: {pnl_ratio*100:.2f}%")

            if trade_details.get('trailing_active', False):
                new_potential_sl_price = None
                if signal_type == 'long':
                    # Anahtar yoksa başlat
                    # Anahtar yoksa başlat veya güncelle
                    previous_highest = trade_details.get('highest_price_since_trailing_activation', current_price)
                    trade_details['highest_price_since_trailing_activation'] = max(current_price, previous_highest)

                    # SL henüz hareket etmese bile, değiştiyse güncellenmiş en yüksek fiyatı kalıcı hale getir
                    if trade_details['highest_price_since_trailing_activation'] != previous_highest:
                        if not db_handler.set_trade(symbol, trade_details): # redis_client -> db_handler
                            logger.warning(f"{symbol} için highest_price_since_trailing_activation Veritabanında güncellenemedi. Yeniden başlatılırsa TSL hesaplamaları eski veri kullanabilir.") # Redis -> Veritabanında
                            # Burada devam etmiyoruz, çünkü mantığın geri kalanı bu döngü için bellek içi güncellemeyle devam edebilir

                    calculated_sl = trade_details['highest_price_since_trailing_activation'] * (1 - config.TRAILING_STOP_POSITIVE)
                    if calculated_sl > current_sl_price and calculated_sl > entry_price : # SL'nin girişin de üzerinde olduğundan emin olun
                        new_potential_sl_price = calculated_sl

                elif signal_type == 'short':
                    previous_lowest = trade_details.get('lowest_price_since_trailing_activation', current_price)
                    trade_details['lowest_price_since_trailing_activation'] = min(current_price, previous_lowest)

                    # Değiştiyse güncellenmiş en düşük fiyatı kalıcı hale getir
                    if trade_details['lowest_price_since_trailing_activation'] != previous_lowest:
                        if not db_handler.set_trade(symbol, trade_details): # redis_client -> db_handler
                             logger.warning(f"{symbol} için lowest_price_since_trailing_activation Veritabanında güncellenemedi. Yeniden başlatılırsa TSL hesaplamaları eski veri kullanabilir.") # Redis -> Veritabanında

                    calculated_sl = trade_details['lowest_price_since_trailing_activation'] * (1 + config.TRAILING_STOP_POSITIVE)
                    if calculated_sl < current_sl_price and calculated_sl < entry_price: # SL'nin girişin de altında olduğundan emin olun
                        new_potential_sl_price = calculated_sl

                if new_potential_sl_price is not None and sl_order_id:
                    logger.info(f"Attempting to update SL for {symbol}. Old SL: {current_sl_price}, New Potential SL: {new_potential_sl_price}")

                    symbol_info_sl = futures_client.get_symbol_info(symbol)
                    tick_size_sl = "1e-8" # Bulunamazsa varsayılan olarak çok küçük bir değere ayarla
                    if symbol_info_sl:
                        price_filter = next((f for f in symbol_info_sl['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                        if price_filter: tick_size_sl = price_filter['tickSize']

                    adjusted_new_sl_price = float(futures_client._adjust_price_to_tick(new_potential_sl_price, tick_size_sl))
                    logger.info(f"New SL for {symbol} adjusted to tick size {tick_size_sl}: {adjusted_new_sl_price}")

                    if abs(adjusted_new_sl_price - current_sl_price) < float(tick_size_sl):
                        logger.debug(f"New SL {adjusted_new_sl_price} for {symbol} is not significantly different from current SL {current_sl_price} (tick: {tick_size_sl}). Skipping update.")
                        continue

                    # Aşırı oynaklık veya büyük izleme yüzdesi nedeniyle SL'nin mevcut fiyat "üzerinden" yerleştirilmediğinden emin olun
                    if signal_type == 'long' and adjusted_new_sl_price >= current_price:
                        logger.warning(f"Calculated new SL {adjusted_new_sl_price} for LONG {symbol} is at or above current price {current_price}. Skipping SL update to prevent immediate stop-out.")
                        continue
                    elif signal_type == 'short' and adjusted_new_sl_price <= current_price:
                        logger.warning(f"Calculated new SL {adjusted_new_sl_price} for SHORT {symbol} is at or below current price {current_price}. Skipping SL update to prevent immediate stop-out.")
                        continue


                    logger.info(f"Cancelling old SL order ID {sl_order_id} for {symbol} to update TSL.")
                    try:
                        cancel_success_details = futures_client.client.futures_cancel_order(symbol=symbol, orderId=sl_order_id, timestamp=futures_client._get_timestamp())
                        logger.info(f"Old SL order {sl_order_id} for {symbol} cancelled successfully: {cancel_success_details}")

                        sl_side = SIDE_SELL if signal_type == 'long' else SIDE_BUY
                        new_sl_order_direct = futures_client.place_futures_order(
                            symbol, sl_side, trade_details['quantity'],
                            stop_price=adjusted_new_sl_price,
                            order_type=FUTURE_ORDER_TYPE_STOP_MARKET
                        )

                        if new_sl_order_direct and 'orderId' in new_sl_order_direct:
                            trade_details['sl_order_id'] = new_sl_order_direct['orderId']
                            trade_details['current_sl_price'] = adjusted_new_sl_price
                            logger.info(f"New TSL order for {symbol} placed. ID: {new_sl_order_direct['orderId']}, Price: {adjusted_new_sl_price}")
                            if not db_handler.set_trade(symbol, trade_details): # redis_client -> db_handler
                                logger.error(f"KRİTİK: {symbol} işlemi yeni TSL emir ID {new_sl_order_direct['orderId']} ile Veritabanında güncellenemedi. Durum uyuşmazlığı olabilir.") # Redis -> Veritabanında
                            telegram_notifier.send_message(f"⚙️ Takip Eden ZD Güncellendi ({symbol})\nSembol: {symbol}\nYeni ZD Fiyatı: {adjusted_new_sl_price:.4f}")
                        else:
                            logger.error(f"CRITICAL: Old SL for {symbol} cancelled but FAILED to place new TSL order at {adjusted_new_sl_price}. POSITION IS UNPROTECTED.")
                            telegram_notifier.notify_error(f"KRİTİK TSL Hatası: {symbol}", f"Eski ZD iptal edildi, yeni TSL BAŞARISIZ. POZİSYON KORUMASIZ. Denenen ZD: {adjusted_new_sl_price:.4f}. Manuel müdahale gerekli!")
                            db_handler.delete_trade(symbol) # Aktif yönetimden kaldır # redis_client -> db_handler

                    except BinanceAPIException as cancel_e:
                        logger.error(f"Failed to cancel old SL order {sl_order_id} for {symbol} during TSL update: {cancel_e}")
                        if cancel_e.code == -2011: # Emir zaten doldurulmuş veya iptal edilmiş
                             logger.info(f"Old SL {sl_order_id} for {symbol} was already filled/cancelled. Removing from TSL management.")
                             db_handler.delete_trade(symbol) # redis_client -> db_handler
                        # aksi takdirde, birden fazla SL'den kaçınmak için yeni SL yerleştirmeyin. Bir sonraki döngüde yeniden denenecektir.

        except BinanceAPIException as e:
            logger.error(f"Binance API Error managing TSL for {symbol}: {e}", exc_info=False) # Yaygın API hataları için daha az ayrıntılı günlükler için exc_info=False olarak ayarlayın
            if e.code == -2011 and trade_details.get('sl_order_id'): # Bilinmeyen emir gönderildi. (ör. SL zaten iptal edilmiş / doldurulmuş)
                logger.warning(f"SL Order for {symbol} (ID: {trade_details['sl_order_id']}) likely filled or already cancelled. Removing from TSL management.")
                db_handler.delete_trade(symbol) # redis_client -> db_handler
            # Kritik olmayan API hataları için burada daha spesifik hata işleme veya daha az sık bildirimleri düşünün
        except Exception as e:
            logger.error(f"Generic Error managing TSL for {symbol}: {e}", exc_info=True)

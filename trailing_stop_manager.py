import config
import logging
import time
# Removed direct imports of BinanceFuturesClient and TelegramNotifier to avoid circular dependencies
# These will be passed as arguments to manage_trailing_stops function.
from binance.enums import * # For FUTURE_ORDER_TYPE_STOP_MARKET, SIDE_SELL, SIDE_BUY
from binance.exceptions import BinanceAPIException
import copy # For safely iterating over active_bot_trades

logger = logging.getLogger(__name__)

def manage_trailing_stops(futures_client, telegram_notifier, active_bot_trades, active_trades_lock=None):
    # active_trades_lock is optional, for more complex scenarios.
    # Python dict operations are largely atomic, but for multi-step read-modify-write, a lock is safer.
    # For iterating and simple checks/deletions, copy.deepcopy or list(dict.items()) is often sufficient.

    if not config.TRAILING_STOP or not futures_client:
        logger.debug("Trailing stop is disabled in config or futures_client not available.")
        return

    logger.debug(f"Checking trailing stops for {len(active_bot_trades)} active trades...")

    # Use a deep copy of items for safe iteration if modifications occur
    # Or iterate over keys and fetch/delete carefully.
    # active_trades_copy = copy.deepcopy(active_bot_trades) # Needs import copy; deepcopy might be overkill if objects are simple.

    # Iterate over a list of symbol keys to allow modification of the dict
    for symbol in list(active_bot_trades.keys()):
        if symbol not in active_bot_trades: # Check if trade was removed by another part of the logic or previous iteration
            continue

        trade_details = active_bot_trades[symbol]

        if trade_details.get('status') != "open":
            continue

        try:
            logger.debug(f"Managing TSL for {symbol}. Details: {trade_details}")
            position_info = futures_client.get_open_position_for_symbol(symbol)

            if not position_info or float(position_info.get('positionAmt', 0)) == 0:
                logger.info(f"Position for {symbol} (Entry: {trade_details['entry_price']}) appears closed on Binance. Removing from active_bot_trades.")

                # Attempt to get the last known mark price for exit price if available
                last_mark_price_str = position_info.get('markPrice', str(trade_details['entry_price'])) if position_info else str(trade_details['entry_price'])

                try:
                    exit_price_estimate = float(last_mark_price_str)
                except ValueError:
                    exit_price_estimate = trade_details['entry_price'] # Fallback to entry if markPrice is invalid

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
                    notes="Position appears closed on Binance (detected by TSL manager)."
                )
                # Safely delete the key
                if active_trades_lock:
                    with active_trades_lock:
                        if symbol in active_bot_trades: del active_bot_trades[symbol]
                else:
                    if symbol in active_bot_trades: del active_bot_trades[symbol]
                continue

            current_price = float(position_info.get('markPrice', 0))
            if current_price == 0:
                logger.warning(f"Could not get current mark price for {symbol} to manage TSL.")
                continue

            entry_price = trade_details['entry_price']
            signal_type = trade_details['signal_type']
            # Ensure these keys exist, provide defaults if not for safety
            current_sl_price = trade_details.get('current_sl_price', 0.0)
            sl_order_id = trade_details.get('sl_order_id')

            pnl_ratio = 0
            if entry_price > 0: # Avoid division by zero
                if signal_type == 'long':
                    pnl_ratio = (current_price - entry_price) / entry_price
                elif signal_type == 'short':
                    pnl_ratio = (entry_price - current_price) / entry_price

            # TSL aktivasyon mantığı:
            # Eğer TSL aktif değilse VE TRAILING_STOP_ACTIVATION_PERCENTAGE yapılandırmada mevcutsa VE PNL oranı bu eşiği aştıysa TSL'yi aktif et.
            if not trade_details.get('trailing_active', False) and \
               hasattr(config, 'TRAILING_STOP_ACTIVATION_PERCENTAGE') and \
               pnl_ratio > config.TRAILING_STOP_ACTIVATION_PERCENTAGE:

                trade_details['trailing_active'] = True
                # Aktivasyon anındaki fiyatı kaydet. Bu, TSL'nin karşılaştırma yapacağı başlangıç noktasıdır.
                # Long pozisyonlar için, bu andan itibaren daha yüksek fiyatlar bu değeri güncelleyecektir.
                # Short pozisyonlar için, bu andan itibaren daha düşük fiyatlar bu değeri güncelleyecektir.
                trade_details['highest_price_since_trailing_activation'] = current_price
                trade_details['lowest_price_since_trailing_activation'] = current_price

                activation_message = (f"✅ TSL Aktif: {symbol} için takip eden zarar durdurma etkinleştirildi. "
                                      f"Mevcut Fiyat: {current_price:.4f}, PNL Oranı: {pnl_ratio:.2%}, "
                                      f"Aktivasyon Eşiği: {config.TRAILING_STOP_ACTIVATION_PERCENTAGE:.2%}")
                logger.info(activation_message)
                if telegram_notifier.enabled:
                    telegram_notifier.send_message(activation_message)

            # TSL aktifse yeni SL fiyatını hesapla ve gerekirse güncelle
            if trade_details.get('trailing_active', False):
                new_potential_sl_price = None
                if signal_type == 'long':
                    # Long pozisyon: En yüksek fiyatı takip et ve SL'yi bunun belirli bir yüzde altında tut.
                    if current_price > trade_details.get('highest_price_since_trailing_activation', entry_price): # Aktivasyondan (veya girişten) beri yeni bir zirve varsa
                        trade_details['highest_price_since_trailing_activation'] = current_price # Zirveyi güncelle

                    # Yeni potansiyel SL fiyatını hesapla (zirveye göre)
                    calculated_sl = trade_details['highest_price_since_trailing_activation'] * (1 - config.TRAILING_STOP_DISTANCE_PERCENTAGE)
                    if calculated_sl > current_sl_price and calculated_sl > entry_price : # Sadece SL fiyatı yukarı hareket ediyorsa (kârı koruyorsa) ve giriş fiyatının üzerindeyse güncelle
                        new_potential_sl_price = calculated_sl

                elif signal_type == 'short':
                    # Short pozisyon: En düşük fiyatı takip et ve SL'yi bunun belirli bir yüzde üzerinde tut.
                    if current_price < trade_details.get('lowest_price_since_trailing_activation', entry_price): # Aktivasyondan (veya girişten) beri yeni bir dip varsa
                        trade_details['lowest_price_since_trailing_activation'] = current_price # Dibi güncelle

                    # Yeni potansiyel SL fiyatını hesapla (dibe göre)
                    calculated_sl = trade_details['lowest_price_since_trailing_activation'] * (1 + config.TRAILING_STOP_DISTANCE_PERCENTAGE)
                    if calculated_sl < current_sl_price and calculated_sl < entry_price: # Sadece SL fiyatı aşağı hareket ediyorsa (kârı koruyorsa) ve giriş fiyatının altındaysa güncelle
                        new_potential_sl_price = calculated_sl

                if new_potential_sl_price is not None and sl_order_id:
                    logger.info(f"Attempting to update SL for {symbol}. Old SL: {current_sl_price}, New Potential SL: {new_potential_sl_price}")

                    symbol_info_sl = futures_client.get_symbol_info(symbol)
                    tick_size_sl = "1e-8" # Default to very small if not found
                    if symbol_info_sl:
                        price_filter = next((f for f in symbol_info_sl['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                        if price_filter: tick_size_sl = price_filter['tickSize']

                    adjusted_new_sl_price = float(futures_client._adjust_price_to_tick(new_potential_sl_price, tick_size_sl))
                    logger.info(f"New SL for {symbol} adjusted to tick size {tick_size_sl}: {adjusted_new_sl_price}")

                    if abs(adjusted_new_sl_price - current_sl_price) < float(tick_size_sl):
                        logger.debug(f"New SL {adjusted_new_sl_price} for {symbol} is not significantly different from current SL {current_sl_price} (tick: {tick_size_sl}). Skipping update.")
                        continue

                    # Ensure SL is not placed "through" the current price due to extreme volatility or large trail %
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
                            telegram_notifier.send_message(f"⚙️ Trailing SL Updated for {symbol}\nSymbol: {symbol}\nNew SL Price: {adjusted_new_sl_price:.4f}")
                        else:
                            logger.error(f"CRITICAL: Old SL for {symbol} cancelled but FAILED to place new TSL order at {adjusted_new_sl_price}. POSITION IS UNPROTECTED.")
                            telegram_notifier.notify_error(f"CRITICAL TSL Error: {symbol}", f"Old SL cancelled, new TSL FAILED. POS UNPROTECTED. Attempted SL: {adjusted_new_sl_price:.4f}. Manual intervention required!")
                            if symbol in active_bot_trades: # Remove from active management
                                if active_trades_lock:
                                    with active_trades_lock: del active_bot_trades[symbol]
                                else:
                                    del active_bot_trades[symbol]

                    except BinanceAPIException as cancel_e:
                        logger.error(f"Failed to cancel old SL order {sl_order_id} for {symbol} during TSL update: {cancel_e}")
                        if cancel_e.code == -2011: # Order already filled or cancelled
                             logger.info(f"Old SL {sl_order_id} for {symbol} was already filled/cancelled. Removing from TSL management.")
                             if symbol in active_bot_trades:
                                 if active_trades_lock:
                                     with active_trades_lock: del active_bot_trades[symbol]
                                 else:
                                     del active_bot_trades[symbol]
                        # else, do not place new SL to avoid multiple SLs. Will retry next cycle.

        except BinanceAPIException as e:
            logger.error(f"Binance API Error managing TSL for {symbol}: {e}", exc_info=False) # Set exc_info=False for less verbose logs for common API errors
            if e.code == -2011 and trade_details.get('sl_order_id'): # Unknown order sent. (e.g. SL already cancelled / filled)
                logger.warning(f"SL Order for {symbol} (ID: {trade_details['sl_order_id']}) likely filled or already cancelled. Removing from TSL management.")
                if symbol in active_bot_trades:
                    if active_trades_lock:
                        with active_trades_lock: del active_bot_trades[symbol]
                    else:
                        del active_bot_trades[symbol]
            # Consider more specific error handling or less frequent notifications for non-critical API errors here
        except Exception as e:
            logger.error(f"Generic Error managing TSL for {symbol}: {e}", exc_info=True)

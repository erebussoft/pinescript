import config
import logging
import time
# Removed direct imports of BinanceFuturesClient and TelegramNotifier to avoid circular dependencies
# These will be passed as arguments to manage_trailing_stops function.
from binance.enums import * # For FUTURE_ORDER_TYPE_STOP_MARKET, SIDE_SELL, SIDE_BUY
from binance.exceptions import BinanceAPIException
# import copy # Removed as we fetch from Redis

logger = logging.getLogger(__name__)

def manage_trailing_stops(futures_client, telegram_notifier, redis_client):
    # active_trades_lock is removed as Redis operations on single keys are generally atomic.

    if not config.TRAILING_STOP or not futures_client:
        logger.debug("Trailing stop is disabled in config or futures_client not available.")
        return

    logger.debug("Querying active trade symbols from Redis for TSL management...")

    if not redis_client or not redis_client.is_connected():
        logger.error("Redis client not available or not connected in TSL manager. Skipping TSL cycle.")
        return

    active_trade_symbols = redis_client.get_all_trade_symbols()
    if not active_trade_symbols:
        logger.debug("No active trades found in Redis to manage.")
        return
    logger.info(f"Found {len(active_trade_symbols)} active trades in Redis to manage: {active_trade_symbols}")

    for symbol in active_trade_symbols:
        trade_details = redis_client.get_trade(symbol)
        if not trade_details:
            logger.warning(f"Could not retrieve trade details for symbol {symbol} from Redis, or it was deleted. Skipping.")
            continue

        if trade_details.get('status') != "open":
            logger.debug(f"Trade {symbol} is not open (status: {trade_details.get('status')}). Skipping TSL.")
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
                redis_client.delete_trade(symbol)
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

            if not trade_details.get('trailing_active', False) and config.TRAILING_ONLY_OFFSET_IS_REACHED:
                if pnl_ratio > config.TRAILING_STOP_POSITIVE_OFFSET:
                    trade_details['trailing_active'] = True
                    if signal_type == 'long':
                        trade_details['highest_price_since_trailing_activation'] = current_price
                    elif signal_type == 'short':
                        trade_details['lowest_price_since_trailing_activation'] = current_price
                    else: # Should not happen if signal_type is validated
                        trade_details['highest_price_since_trailing_activation'] = current_price # Defaulting, but should be one or the other
                        trade_details['lowest_price_since_trailing_activation'] = current_price  # Defaulting

                    logger.info(f"Trailing stop ACTIVATED for {symbol} at P&L ratio: {pnl_ratio:.4f}, Current Price: {current_price}")
                    if not redis_client.set_trade(symbol, trade_details):
                        logger.error(f"Failed to update trade details in Redis for {symbol} after TSL activation.")
                        # Continue to next symbol, as the state in Redis is now potentially stale for this trade.
                        continue # Skip further processing for this symbol in this cycle
                    telegram_notifier.send_message(f"🟢 Trailing Stop Activated for {symbol}\nSymbol: {symbol}\nDirection: {signal_type.upper()}\nEntry: {entry_price:.4f}\nCurrent Price: {current_price:.4f}\nProfit: {pnl_ratio*100:.2f}%")

            if trade_details.get('trailing_active', False):
                new_potential_sl_price = None
                if signal_type == 'long':
                    # Initialize if key doesn't exist
                    # Initialize if key doesn't exist, or update
                    previous_highest = trade_details.get('highest_price_since_trailing_activation', current_price)
                    trade_details['highest_price_since_trailing_activation'] = max(current_price, previous_highest)

                    # Persist updated highest price if it changed, even if SL doesn't move yet
                    if trade_details['highest_price_since_trailing_activation'] != previous_highest:
                        if not redis_client.set_trade(symbol, trade_details):
                            logger.warning(f"Failed to update highest_price_since_trailing_activation for {symbol} in Redis. TSL calculations might use stale data if restarted.")
                            # Not continuing here, as the rest of the logic can proceed with the in-memory update for this cycle

                    calculated_sl = trade_details['highest_price_since_trailing_activation'] * (1 - config.TRAILING_STOP_POSITIVE)
                    if calculated_sl > current_sl_price and calculated_sl > entry_price : # Ensure SL is also above entry
                        new_potential_sl_price = calculated_sl

                elif signal_type == 'short':
                    previous_lowest = trade_details.get('lowest_price_since_trailing_activation', current_price)
                    trade_details['lowest_price_since_trailing_activation'] = min(current_price, previous_lowest)

                    # Persist updated lowest price if it changed
                    if trade_details['lowest_price_since_trailing_activation'] != previous_lowest:
                        if not redis_client.set_trade(symbol, trade_details):
                             logger.warning(f"Failed to update lowest_price_since_trailing_activation for {symbol} in Redis. TSL calculations might use stale data if restarted.")

                    calculated_sl = trade_details['lowest_price_since_trailing_activation'] * (1 + config.TRAILING_STOP_POSITIVE)
                    if calculated_sl < current_sl_price and calculated_sl < entry_price: # Ensure SL is also below entry
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
                            if not redis_client.set_trade(symbol, trade_details):
                                logger.error(f"CRITICAL: Failed to update trade {symbol} in Redis with new TSL order ID {new_sl_order_direct['orderId']}. State mismatch possible.")
                            telegram_notifier.send_message(f"⚙️ Trailing SL Updated for {symbol}\nSymbol: {symbol}\nNew SL Price: {adjusted_new_sl_price:.4f}")
                        else:
                            logger.error(f"CRITICAL: Old SL for {symbol} cancelled but FAILED to place new TSL order at {adjusted_new_sl_price}. POSITION IS UNPROTECTED.")
                            telegram_notifier.notify_error(f"CRITICAL TSL Error: {symbol}", f"Old SL cancelled, new TSL FAILED. POS UNPROTECTED. Attempted SL: {adjusted_new_sl_price:.4f}. Manual intervention required!")
                            redis_client.delete_trade(symbol) # Remove from active management

                    except BinanceAPIException as cancel_e:
                        logger.error(f"Failed to cancel old SL order {sl_order_id} for {symbol} during TSL update: {cancel_e}")
                        if cancel_e.code == -2011: # Order already filled or cancelled
                             logger.info(f"Old SL {sl_order_id} for {symbol} was already filled/cancelled. Removing from TSL management.")
                             redis_client.delete_trade(symbol)
                        # else, do not place new SL to avoid multiple SLs. Will retry next cycle.

        except BinanceAPIException as e:
            logger.error(f"Binance API Error managing TSL for {symbol}: {e}", exc_info=False) # Set exc_info=False for less verbose logs for common API errors
            if e.code == -2011 and trade_details.get('sl_order_id'): # Unknown order sent. (e.g. SL already cancelled / filled)
                logger.warning(f"SL Order for {symbol} (ID: {trade_details['sl_order_id']}) likely filled or already cancelled. Removing from TSL management.")
                redis_client.delete_trade(symbol)
            # Consider more specific error handling or less frequent notifications for non-critical API errors here
        except Exception as e:
            logger.error(f"Generic Error managing TSL for {symbol}: {e}", exc_info=True)

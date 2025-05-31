# telegram_bot.py
import config
import logging
import httpx # Using httpx for simple synchronous POST requests

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/"
        if not bot_token or bot_token == "YOUR_TELEGRAM_BOT_TOKEN":
            logger.warning("Telegram bot token is not configured. Notifications will be disabled.")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Telegram Notifier initialized for chat ID: {self.chat_id}")

    def send_message(self, text, parse_mode="Markdown"):
        if not self.enabled:
            logger.info(f"Telegram disabled. Message not sent: {text}")
            return None

        url = self.base_url + "sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode  # Options: "Markdown" or "HTML"
        }
        try:
            with httpx.Client() as client:
                response = client.post(url, json=payload, timeout=10) # telegram API expects JSON payload
            response.raise_for_status()  # Raises an exception for 4XX/5XX responses
            logger.info(f"Telegram message sent successfully. Response: {response.json()}")
            return response.json()
        except httpx.RequestError as e:
            logger.error(f"Error sending Telegram message (RequestError): {e.request.url} - {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Error sending Telegram message (HTTPStatusError): {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.error(f"An unexpected error occurred when sending Telegram message: {e}")
        return None

    def notify_trade_entry(self, symbol, direction, entry_price, quantity, stop_loss_price, notes=""):
        direction_emoji = "🟢" if direction.lower() == "long" else "🔴"
        message = (
            f"{direction_emoji} **New Trade Entry** {direction_emoji}\n\n"
            f"**Symbol:** `{symbol}`\n"
            f"**Direction:** `{direction.upper()}`\n"
            f"**Entry Price:** `{entry_price:.4f}`\n" # Adjust precision as needed
            f"**Quantity:** `{quantity}`\n"
            f"**Stop Loss:** `{stop_loss_price:.4f}`\n"
        )
        if notes:
            message += f"\n**Notes:** {notes}"
        return self.send_message(message)

    def notify_trade_close(self, symbol, direction, exit_price, entry_price, quantity, pnl, notes=""):
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        message = (
            f"{pnl_emoji} **Trade Closed** {pnl_emoji}\n\n"
            f"**Symbol:** `{symbol}`\n"
            f"**Direction:** `{direction.upper()}`\n"
            f"**Entry Price:** `{entry_price:.4f}`\n"
            f"**Exit Price:** `{exit_price:.4f}`\n"
            f"**Quantity:** `{quantity}`\n"
            f"**P&L (USDT):** `{pnl:.2f}`\n" # Assuming PNL is in USDT
        )
        if notes:
            message += f"\n**Notes:** {notes}"
        return self.send_message(message)

    def notify_error(self, error_message, details=""):
        message = (
            f"⚠️ **Bot Error** ⚠️\n\n"
            f"**Message:** `{error_message}`\n"
        )
        if details:
            message += f"**Details:** `{details}`"
        return self.send_message(message)

    def notify_balance(self, balance, open_positions_count, total_pnl_session=None, notes=""):
        message = (
            f"💰 **Bot Status & Balance** 💰\n\n"
            f"**Current USDT Balance:** `{balance:.2f}`\n"
            f"**Open Positions:** `{open_positions_count}`\n"
        )
        if total_pnl_session is not None:
             message += f"**Session P&L:** `{total_pnl_session:.2f}` USDT\n"
        if notes:
            message += f"\n**Notes:** {notes}"
        return self.send_message(message)

# Example usage (for testing this module directly)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("Testing TelegramNotifier...")

    if config.TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or config.TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.warning("Telegram Bot Token or Chat ID is not configured in config.py. Cannot send test messages.")
    else:
        notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

        logger.info("Sending test entry notification...")
        notifier.notify_trade_entry("BTCUSDT", "long", 60000.0, 0.001, 58000.0, notes="Test entry from bot dev")

        logger.info("Sending test close notification...")
        notifier.notify_trade_close("ETHUSDT", "short", 3000.0, 3100.0, 0.05, -5.0, notes="Test close from bot dev")

        logger.info("Sending test error notification...")
        notifier.notify_error("Test error message", details="Simulated error during testing.")

        logger.info("Sending test balance notification...")
        notifier.notify_balance(10000.50, 2, 150.75, notes="End of day test report.")

        logger.info("Sending a simple message...")
        notifier.send_message("Hello from the bot! This is a *Markdown* test. And this is `code`.")

    logger.info("TelegramNotifier testing finished.")

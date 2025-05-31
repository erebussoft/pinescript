# TradingView Webhook Bot for Binance Futures

This Python-based bot listens for webhook signals from TradingView alerts and automatically places trades on Binance Futures. It includes features for position sizing, stop-loss orders, leverage and margin type setting, programmatic trailing stops, and Telegram notifications.

## Features

-   Receives trade signals (long/short) from TradingView via webhooks.
-   Integrates with Binance Futures API to place trades.
-   Sets specified leverage (e.g., 10x) and margin type (e.g., ISOLATED) for each symbol before trading.
-   Calculates position size based on tradable balance ratio and max open trades.
-   Automatically places entry orders (LIMIT by default) and corresponding STOP_MARKET stop-loss orders.
-   Programmatic Trailing Stop Loss (TSL):
    -   Activates after a defined profit offset is reached.
    -   Trails the price by a configured percentage, adjusting the stop-loss order on Binance.
    -   Runs in a background thread, periodically checking active trades.
-   Sends real-time notifications to a Telegram channel for:
    -   Bot startup
    -   Trade entries (symbol, direction, price, quantity, SL)
    -   Trailing stop activation and updates.
    -   Trade closures (detected by TSL manager if position disappears from Binance).
    -   Errors and critical warnings.
-   In-memory state management for active trades (note: volatile, lost on restart).
-   Configurable trading parameters via \`config.py\`.

## Setup and Configuration

1.  **Clone the Repository:**
    \`\`\`bash
    git clone <your_repository_url>
    cd <repository_directory>
    \`\`\`

2.  **Install Dependencies:**
    Create a Python virtual environment and install the required packages:
    \`\`\`bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    \`\`\`

3.  **Configure the Bot (\`config.py\`):**
    -   Edit \`config.py\` and fill in your details:
        -   \`BINANCE_API_KEY\`, \`BINANCE_API_SECRET\`: Your Binance API credentials.
            *   **Security Note:** Ensure API keys have Futures trading permissions enabled. Withdrawal permissions should be disabled for security.
        -   \`TELEGRAM_BOT_TOKEN\`, \`TELEGRAM_CHAT_ID\`: Your Telegram bot details.
        -   \`TRADING_PAIRS\`: Update with all Binance Futures symbols you intend to trade.
        -   **New/Updated Parameters**:
            -   \`LEVERAGE = 10\`: Set your desired leverage (e.g., 10 for 10x).
            -   \`MARGIN_TYPE = "ISOLATED"\`: Typically "ISOLATED" or "CROSSED".
            -   \`EXPECTED_WEBHOOK_INTERVAL = "15"\`: **Crucial.** This must match the chart interval of your TradingView alerts (e.g., "15" for 15-minute, "60" for 1-hour).
            -   \`TRAILING_STOP = True\`: Set to \`True\` to enable the programmatic trailing stop feature.
            -   \`TRAILING_STOP_POSITIVE_OFFSET = 0.009\`: Profit offset (e.g., 0.9%) to activate the trailing stop.
            -   \`TRAILING_STOP_POSITIVE = 0.008\`: Percentage (e.g., 0.8%) by which the stop loss will trail the peak price.
            -   \`TRAILING_STOP_CHECK_INTERVAL_SECONDS = 60\`: How often the bot checks to update trailing stops. See API Rate Limit warning below.
        -   Review and adjust other parameters like \`STOP_LOSS\` (initial stop), \`TRADABLE_BALANCE_RATIO\`, \`MAX_OPEN_TRADES\`, etc.

4.  **Configure TradingView Alerts:**
    -   Set up your alerts in TradingView on the chart interval specified in \`config.EXPECTED_WEBHOOK_INTERVAL\` (e.g., **15-minute chart** if \`EXPECTED_WEBHOOK_INTERVAL = "15"\`).
    -   The alert condition should be based on your PineScript indicator's specific signals (e.g., "4H Confirmed Long" or "4H Confirmed Short" signal names, even if the chart is 15-min). The indicator's internal logic using \`request.security\` for 4H data will still apply, but the alert itself triggers on the 15-min candle's close if the 4H conditions are met at that point.
    -   **Webhook URL**: Update this in each TradingView alert to point to your server's public address (e.g., \`http://<your_heroku_app_name>.herokuapp.com/webhook\` or \`http://<your_server_ip>:5000/webhook\`).
    -   Ensure the JSON payload in the TradingView alert's "Message" field is correctly formatted as previously specified.

## Running the Bot

### Locally (for development/testing)

1.  Ensure your virtual environment is activated.
2.  Run the bot:
    \`\`\`bash
    python main.py
    \`\`\`
    The bot will start, initialize services, start the TSL thread (if enabled), and listen for webhooks.

### Deployment (Example: Heroku)

1.  **Install Heroku CLI** and log in.
2.  **Create a Heroku app.**
3.  **Add your code to Git and deploy.** The \`Procfile\` (\`web: gunicorn main:app\`) is included.
4.  **Set Config Vars on Heroku:** For security, set sensitive information (API keys, tokens) as environment variables on Heroku. Modify \`config.py\` to read these from \`os.environ.get(...)\` if you use this method.
5.  **Check Logs:** Use \`heroku logs --tail\`.

## Important Notes

-   **Risk Management:** Trading futures involves significant risk. This bot is a tool, not a financial advisor. Understand the risks and the bot's logic before using real funds. **Always test thoroughly on Binance Testnet first.**
-   **Binance API Rate Limits:**
    -   Be extremely mindful of API rate limits, especially with the trailing stop feature.
    -   The \`TRAILING_STOP_CHECK_INTERVAL_SECONDS\` parameter determines how often the bot checks prices and potentially updates SL orders for **each active trade**.
    -   Setting this interval too low (e.g., 5-10 seconds) with multiple active trades can **quickly lead to IP bans or temporary API restrictions** from Binance.
    -   A safer range is typically 30-300 seconds, depending on the number of concurrent trades. Monitor bot logs and Binance API usage.
-   **Trailing Stops (TSL):**
    -   The programmatic TSL feature is now implemented. It activates after a profit offset and trails the price by a set percentage.
    -   **Critical Risk with TSL**: The process of cancelling an old stop-loss and placing a new one has a small window of risk. If placing the new SL fails after the old one is cancelled, the position could be momentarily unprotected. The bot has error handling for this, but it's a critical scenario to be aware of.
-   **State Management:** Active trades are stored in memory. If the bot restarts, this state (including TSL activation status and peak prices) is lost. For persistent state, a database would be required.
-   **Error Handling:** Monitor bot logs and Telegram notifications closely.
-   **Actual Fill Prices**: The bot currently uses the target entry price from the webhook for P&L calculations and initial TSL tracking. For higher accuracy, querying the actual fill price of entry orders is a recommended future enhancement (marked as TODO in code).

## Disclaimer

The developers of this bot are not responsible for any financial losses incurred through its use. Use at your own risk.

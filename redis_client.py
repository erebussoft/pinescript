import redis
import json
import logging
import config # Import the application's config file

logger = logging.getLogger(__name__)

class RedisClient:
    def __init__(self):
        """
        Initializes the Redis client, connecting to the Redis server
        using configuration from the main config file.
        """
        self.redis_url = config.REDIS_URL
        self.db = getattr(config, 'REDIS_DB', 0) # Default to DB 0 if not specified
        self.client = None
        self._connect()

    def _connect(self):
        """
        Establishes connection to the Redis server.
        Handles connection errors.
        """
        if not self.redis_url:
            logger.error("REDIS_URL not configured. Redis client cannot be initialized.")
            # Potentially raise an exception or handle this state as appropriate
            # For now, operations will fail if client is None
            return

        try:
            logger.info(f"Connecting to Redis at {self.redis_url}, DB: {self.db}")
            # The `from_url` method handles parsing the URL and setting up the connection.
            # ssl_cert_reqs=None is often needed for Heroku Redis if not using a custom CA
            # decode_responses=True would make all string results decoded from bytes to str automatically
            self.client = redis.Redis.from_url(self.redis_url, db=self.db, ssl_cert_reqs=None, decode_responses=False)
            # Test connection
            self.client.ping()
            logger.info("Successfully connected to Redis.")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}", exc_info=True)
            self.client = None # Ensure client is None if connection fails
        except Exception as e: # Catch other potential errors during Redis client initialization
            logger.error(f"An unexpected error occurred during Redis initialization: {e}", exc_info=True)
            self.client = None

    def is_connected(self):
        """Checks if the client is connected to Redis."""
        if self.client:
            try:
                self.client.ping()
                return True
            except redis.exceptions.ConnectionError:
                logger.warning("Redis connection lost. Attempting to reconnect...")
                self._connect() # Attempt to reconnect
                if self.client and self.client.ping(): # Check again after reconnect attempt
                    logger.info("Successfully reconnected to Redis.")
                    return True
                logger.error("Failed to reconnect to Redis.")
                return False
        return False

    def set_trade(self, symbol: str, trade_data: dict):
        """
        Stores trade data for a given symbol in Redis.
        The trade_data dictionary is serialized to a JSON string.
        Key format: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Not connected to Redis. Cannot set trade for {symbol}.")
            return False
        try:
            key = f"active_trade:{symbol}"
            serialized_data = json.dumps(trade_data)
            self.client.set(key, serialized_data)
            logger.debug(f"Trade data for {symbol} stored in Redis. Key: {key}")
            return True
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error while setting trade for {symbol}: {e}", exc_info=True)
            return False
        except json.JSONDecodeError as e:
            logger.error(f"JSON serialization error while setting trade for {symbol}: {e}", exc_info=True)
            return False


    def get_trade(self, symbol: str) -> dict | None:
        """
        Retrieves trade data for a symbol from Redis.
        Deserializes data from JSON string to a dictionary.
        Returns None if the symbol is not found or an error occurs.
        Key format: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Not connected to Redis. Cannot get trade for {symbol}.")
            return None
        try:
            key = f"active_trade:{symbol}"
            serialized_data = self.client.get(key)
            if serialized_data:
                trade_data = json.loads(serialized_data.decode('utf-8')) # Decode bytes to str before json.loads
                logger.debug(f"Trade data for {symbol} retrieved from Redis. Key: {key}")
                return trade_data
            else:
                logger.debug(f"No trade data found in Redis for {symbol}. Key: {key}")
                return None
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error while getting trade for {symbol}: {e}", exc_info=True)
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON deserialization error while getting trade for {symbol}: {e}", exc_info=True)
            return None

    def delete_trade(self, symbol: str):
        """
        Deletes trade data for a symbol from Redis.
        Key format: active_trade:<symbol>
        """
        if not self.is_connected():
            logger.error(f"Not connected to Redis. Cannot delete trade for {symbol}.")
            return False
        try:
            key = f"active_trade:{symbol}"
            result = self.client.delete(key)
            if result > 0:
                logger.info(f"Trade data for {symbol} deleted from Redis. Key: {key}")
            else:
                logger.info(f"No trade data found to delete in Redis for {symbol} (or already deleted). Key: {key}")
            return True # Returns True even if key didn't exist, as per Redis `del` behavior
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error while deleting trade for {symbol}: {e}", exc_info=True)
            return False

    def get_all_trade_symbols(self) -> list[str]:
        """
        Retrieves all symbols (keys without prefix) for active trades.
        Scans for keys matching "active_trade:*" pattern.
        """
        if not self.is_connected():
            logger.error("Not connected to Redis. Cannot get all trade symbols.")
            return []
        symbols = []
        try:
            # Use scan_iter for memory efficiency with large number of keys
            for key_bytes in self.client.scan_iter(match="active_trade:*"):
                key_str = key_bytes.decode('utf-8')
                symbol = key_str.split(":", 1)[1] # Extract symbol part from "active_trade:SYMBOL"
                symbols.append(symbol)
            logger.debug(f"Retrieved {len(symbols)} active trade symbols from Redis.")
            return symbols
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error while getting all trade symbols: {e}", exc_info=True)
            return []

    def get_all_trades(self) -> dict[str, dict]:
        """
        Retrieves all active trades from Redis.
        Returns a dictionary where keys are symbols and values are trade_data dictionaries.
        """
        if not self.is_connected():
            logger.error("Not connected to Redis. Cannot get all trades.")
            return {}

        trades = {}
        trade_symbols = self.get_all_trade_symbols()
        for symbol in trade_symbols:
            trade_data = self.get_trade(symbol)
            if trade_data:
                trades[symbol] = trade_data
            else:
                # This might happen if a key exists but fetching its data fails, or if a key expired between scan and get
                logger.warning(f"Could not retrieve trade data for symbol {symbol} listed in keys. It might have been deleted concurrently.")
        logger.debug(f"Retrieved data for {len(trades)} trades from Redis.")
        return trades

# Example usage (optional, for testing purposes)
if __name__ == '__main__':
    # This part will only run when redis_client.py is executed directly.
    # It requires config.py to be set up, especially REDIS_URL.
    # For Heroku, REDIS_URL is usually an environment variable.
    # For local testing, you might need to set config.REDIS_URL manually or via an .env file if you adapt config.py to load it.

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
    logger.info("Attempting to run RedisClient standalone for testing...")

    # --- IMPORTANT LOCAL TESTING NOTE ---
    # Ensure your config.py has a valid REDIS_URL for this test to work.
    # For example, you could temporarily set it in config.py:
    # config.REDIS_URL = "redis://localhost:6379/0"
    # Or, ensure the environment variable is set if config.py loads it from there.
    if not config.REDIS_URL:
        logger.warning("config.REDIS_URL is not set. Standalone test might not connect to Redis unless it's available via environment.")
        # Attempt to set a default for local testing if not set - this is a fallback
        # In a real scenario, config.py should handle loading this from os.environ
        # For this subtask, we assume config.py might not be fully adapted yet for local env vars
        # So, providing a common default for local dev:
        if 'REDIS_URL' not in dir(config) or not config.REDIS_URL:
             print("Patching config.REDIS_URL for local test as it's not set. Using 'redis://localhost:6379/0'")
             config.REDIS_URL = "redis://localhost:6379/0"


    redis_client_instance = RedisClient()

    if redis_client_instance.is_connected():
        logger.info("Redis client connected for testing.")

        # Test data
        test_symbol_1 = "BTCUSDT_TEST"
        test_data_1 = {"entry_price": 40000, "quantity": 0.01, "side": "long", "sl_order_id": "12345"}
        test_symbol_2 = "ETHUSDT_TEST"
        test_data_2 = {"entry_price": 3000, "quantity": 0.1, "side": "short", "sl_order_id": "67890"}

        # Clean up any previous test keys
        redis_client_instance.delete_trade(test_symbol_1)
        redis_client_instance.delete_trade(test_symbol_2)

        # Test set_trade
        logger.info(f"Setting trade for {test_symbol_1}...")
        redis_client_instance.set_trade(test_symbol_1, test_data_1)

        logger.info(f"Setting trade for {test_symbol_2}...")
        redis_client_instance.set_trade(test_symbol_2, test_data_2)

        # Test get_trade
        logger.info(f"Getting trade for {test_symbol_1}...")
        retrieved_data_1 = redis_client_instance.get_trade(test_symbol_1)
        if retrieved_data_1 == test_data_1:
            logger.info(f"SUCCESS: get_trade for {test_symbol_1} returned correct data.")
        else:
            logger.error(f"FAILURE: get_trade for {test_symbol_1} returned {retrieved_data_1}, expected {test_data_1}")

        # Test get_all_trade_symbols
        logger.info("Getting all trade symbols...")
        symbols = redis_client_instance.get_all_trade_symbols()
        expected_symbols = sorted([test_symbol_1, test_symbol_2])
        if sorted(symbols) == expected_symbols:
            logger.info(f"SUCCESS: get_all_trade_symbols returned {symbols}.")
        else:
            logger.error(f"FAILURE: get_all_trade_symbols returned {symbols}, expected {expected_symbols}")


        # Test get_all_trades
        logger.info("Getting all trades...")
        all_trades = redis_client_instance.get_all_trades()
        if test_symbol_1 in all_trades and all_trades[test_symbol_1] == test_data_1 and            test_symbol_2 in all_trades and all_trades[test_symbol_2] == test_data_2:
            logger.info(f"SUCCESS: get_all_trades returned correct data for {len(all_trades)} trades.")
        else:
            logger.error(f"FAILURE: get_all_trades returned {all_trades}, check individual entries.")


        # Test delete_trade
        logger.info(f"Deleting trade for {test_symbol_1}...")
        redis_client_instance.delete_trade(test_symbol_1)
        if redis_client_instance.get_trade(test_symbol_1) is None:
            logger.info(f"SUCCESS: delete_trade for {test_symbol_1} successful.")
        else:
            logger.error(f"FAILURE: delete_trade for {test_symbol_1} failed, trade still exists.")

        # Clean up remaining test key
        redis_client_instance.delete_trade(test_symbol_2)
        logger.info("Redis client tests finished.")
    else:
        logger.error("Redis client NOT connected. Cannot run tests.")
        logger.error("Please ensure Redis server is running and REDIS_URL in config.py is correctly set for your environment.")

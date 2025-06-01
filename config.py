# config.py - Yapılandırma dosyası

# --- Binance API Yapılandırması ---
# Gerçek API anahtarınız ve gizli anahtarınızla değiştirin
# Üretimde hassas veriler için ortam değişkenleri kullanılması önerilir
BINANCE_API_KEY = "YOUR_BINANCE_API_KEY" # BINANCE_API_KEY: Binance API anahtarınız.
BINANCE_API_SECRET = "YOUR_BINANCE_API_SECRET" # BINANCE_API_SECRET: Binance API gizli anahtarınız.

# --- Telegram Bot Yapılandırması ---
# Gerçek Telegram Bot Token'ınız ve Sohbet ID'nizle değiştirin
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN" # TELEGRAM_BOT_TOKEN: Telegram botunuzun token'ı.
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID" # TELEGRAM_CHAT_ID: Telegram'da mesajların gönderileceği sohbet ID'si.

# --- Ticaret Parametreleri ---
LEVERAGE = 10  # LEVERAGE: Kaldıraç oranı (örneğin, 10x).
MARGIN_TYPE = "ISOLATED"  # MARGIN_TYPE: Marjin türü ("ISOLATED" veya "CROSSED").
TRADABLE_BALANCE_RATIO = 0.8  # TRADABLE_BALANCE_RATIO: Ticaret için kullanılacak USDT bakiyesinin oranı (örneğin, %80).
# MAX_OPEN_TRADES: Aynı anda açılabilecek maksimum işlem sayısı. Pozisyon büyüklüğü hesaplaması için 0'dan büyük olmalıdır.
MAX_OPEN_TRADES = 5
STOP_LOSS = 0.02  # STOP_LOSS: Zarar durdurma yüzdesi (örneğin, %2 için 0.02).
TAKE_PROFIT = 0.05 # TAKE_PROFIT: Kar alma yüzdesi (örneğin, %5 için 0.05) - TSL aktivasyon noktası için kullanılır.

# --- Webhook Yapılandırması ---
# Bu, TradingView uyarılarınızda ayarladığınız zaman aralığı ile eşleşmelidir
EXPECTED_WEBHOOK_INTERVAL = "15m" # EXPECTED_WEBHOOK_INTERVAL: Örn: "1m", "5m", "15m", "1h", "4h"

# --- Emir Tipleri ---
# Seçenekler: "LIMIT", "MARKET"
# STOP_MARKET, 'stoploss_on_exchange': true ise borsada zarar durdurma için kullanılır.
ORDER_TYPES = {
    "entry": "MARKET",  # entry: Piyasa giriş emri türü.
    "stoploss": "MARKET" # stoploss: Zarar durdurma emri türü (Binance için STOP_MARKET).
}

# --- Takip Eden Zarar Durdurma (TSL) Yapılandırması ---
TRAILING_STOP = True  # TRAILING_STOP: Takip Eden Zarar Durdurma (TSL) ana şalteri.
# TRAILING_STOP_ACTIVATION_PERCENTAGE: TSL'nin devreye gireceği kar yüzdesi (örn: 0.02, %2 kar anlamına gelir).
TRAILING_STOP_ACTIVATION_PERCENTAGE = 0.02
# TRAILING_STOP_DISTANCE_PERCENTAGE: Zarar durdurmanın en yüksek/düşük fiyatın ne kadar geriden takip edeceği (örn: 0.01, %1 anlamına gelir).
TRAILING_STOP_DISTANCE_PERCENTAGE = 0.01

# Takip eden zararı durdurma kontrolleri için aralık (saniye cinsinden)
# API hız limitlerine dikkat edin. Daha sık kontroller daha fazla API çağrısı kullanır.
TRAILING_STOP_CHECK_INTERVAL_SECONDS = 60 # TRAILING_STOP_CHECK_INTERVAL_SECONDS: TSL kontrol aralığı (saniye).

# Emir dolumları için bekleme süresi (saniye). Piyasa emirlerinin dolması veya LIMIT emir detaylarının güncellenmesi için kısa bir bekleme.
ORDER_FILL_WAIT_SECONDS = 2

# --- İşlem Çiftleri ---
# İşlem yapmak istediğiniz döviz çiftlerini ekleyin
# Bu sembollerin Binance Futures'ta geçerli olduğundan ve USDT, BUSD vb. ile bittiğinden emin olun.
TRADING_PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "SOLUSDT",
    "DOTUSDT",
    "AVAXUSDT",
    "MATICUSDT",
    "LINKUSDT",
    "BNBUSDT",
    "XRPUSDT",
] # TRADING_PAIRS: İşlem yapılacak döviz çiftleri listesi.

# --- Kayıt (Logging) ---
LOG_LEVEL = "INFO" # LOG_LEVEL: Kayıt seviyesi ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").

# --- Flask Sunucu Portu ---
PORT = 5000 # PORT: Webhook sunucusunun çalışacağı port numarası.

# --- Diğer Ayarlar ---
# Örnek: Özellik bayrakları veya diğer parametreler
SOME_OTHER_SETTING = "some_value" # SOME_OTHER_SETTING: Diğer ayarlar için örnek bir parametre.
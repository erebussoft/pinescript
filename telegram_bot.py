# telegram_bot.py - Telegram bildirimlerini yöneten modül
import config # Yapılandırma ayarlarını içe aktar
import logging
import httpx # Basit senkron POST istekleri için httpx kullanılıyor

logger = logging.getLogger(__name__) # Bu modül için logger örneği

class TelegramNotifier:
    """
    Telegram API aracılığıyla mesaj göndermek için bir bildirimci sınıfı.
    Bot token'ı ve chat ID'si ile yapılandırılır.
    Mesaj gönderme, işlem giriş/çıkış bildirimleri ve hata bildirimleri gibi işlevleri içerir.
    """
    def __init__(self, bot_token, chat_id):
        """
        TelegramNotifier sınıfının başlatıcısı.
        Args:
            bot_token (str): Telegram botunun API token'ı.
            chat_id (str): Mesajların gönderileceği Telegram sohbet ID'si.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/" # Telegram API temel URL'si

        # Bot token'ı yapılandırılmamışsa veya varsayılan yer tutucu ise bildirimleri devre dışı bırak
        if not bot_token or bot_token == "YOUR_TELEGRAM_BOT_TOKEN":
            logger.warning("Telegram bot token'ı yapılandırılmamış. Bildirimler devre dışı bırakılacak.")
            self.enabled = False # Bildirimleri devre dışı bırak
        else:
            self.enabled = True # Bildirimleri etkinleştir
            logger.info(f"Telegram Bildirimcisi {self.chat_id} sohbet ID'si için başlatıldı.")

    def send_message(self, text, parse_mode="Markdown"):
        """
        Telegram'a belirtilen metinle bir mesaj gönderir.
        Bu, diğer tüm bildirim fonksiyonları tarafından kullanılan çekirdek mesaj gönderme fonksiyonudur.
        Args:
            text (str): Gönderilecek mesaj metni.
            parse_mode (str, optional): Mesajın nasıl biçimlendirileceği ('Markdown' veya 'HTML'). Varsayılan 'Markdown'.
        Returns:
            dict or None: Başarılı olursa Telegram API yanıtı (sözlük), başarısız olursa None.
        """
        if not self.enabled: # Bildirimler devre dışıysa gönderme
            logger.info(f"Telegram devre dışı. Mesaj gönderilmedi: {text}")
            return None

        url = self.base_url + "sendMessage" # Mesaj gönderme API endpoint'i
        payload = { # Gönderilecek veri yükü
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        try:
            # httpx istemcisi ile POST isteği gönder (Telegram API JSON yükü bekler)
            with httpx.Client() as client:
                response = client.post(url, json=payload, timeout=10) # 10 saniye zaman aşımı
            response.raise_for_status()  # 4XX/5XX yanıtları için bir istisna yükseltir
            logger.info(f"Telegram mesajı başarıyla gönderildi. Yanıt: {response.json()}")
            return response.json() # Başarılı yanıtı döndür
        except httpx.RequestError as e: # İstekle ilgili hatalar (örn: ağ sorunu)
            logger.error(f"Telegram mesajı gönderilirken hata (RequestError): {e.request.url} - {e}")
        except httpx.HTTPStatusError as e: # HTTP durum kodu hataları (örn: 400, 401, 500)
            logger.error(f"Telegram mesajı gönderilirken hata (HTTPStatusError): {e.response.status_code} - {e.response.text}")
        except Exception as e: # Diğer beklenmedik hatalar
            logger.error(f"Telegram mesajı gönderilirken beklenmedik bir hata oluştu: {e}")
        return None # Hata durumunda None döndür

    def notify_trade_entry(self, symbol, direction, entry_price, quantity, stop_loss_price, notes=""):
        """
        Yeni bir işlem girişi için Telegram bildirimi gönderir.
        Args:
            symbol (str): İşlem sembolü.
            direction (str): İşlem yönü ('long' veya 'short').
            entry_price (float): Giriş fiyatı.
            quantity (float): İşlem miktarı.
            stop_loss_price (float): Zarar durdurma fiyatı.
            notes (str, optional): Ek notlar.
        """
        direction_emoji = "🟢" if direction.lower() == "long" else "🔴" # Yöne göre emoji
        message = (
            f"{direction_emoji} **Yeni İşlem Girişi** {direction_emoji}\n\n"
            f"**Sembol:** `{symbol}`\n"
            f"**Yön:** `{direction.upper()}`\n"
            f"**Giriş Fiyatı:** `{entry_price:.4f}`\n" # Hassasiyeti gerektiği gibi ayarla
            f"**Miktar:** `{quantity}`\n"
            f"**Zarar Durdurma (SL):** `{stop_loss_price:.4f}`\n"
        )
        if notes: # Not varsa ekle
            message += f"\n**Notlar:** {notes}"
        return self.send_message(message)

    def notify_trade_close(self, symbol, direction, exit_price, entry_price, quantity, pnl, notes=""):
        """
        Kapatılan bir işlem için Telegram bildirimi gönderir.
        Args:
            symbol (str): İşlem sembolü.
            direction (str): İşlem yönü.
            exit_price (float): Çıkış fiyatı.
            entry_price (float): Giriş fiyatı.
            quantity (float): İşlem miktarı.
            pnl (float): Kar/Zarar miktarı.
            notes (str, optional): Ek notlar.
        """
        pnl_emoji = "✅" if pnl >= 0 else "❌" # P&L'e göre emoji
        message = (
            f"{pnl_emoji} **İşlem Kapatıldı** {pnl_emoji}\n\n"
            f"**Sembol:** `{symbol}`\n"
            f"**Yön:** `{direction.upper()}`\n"
            f"**Giriş Fiyatı:** `{entry_price:.4f}`\n"
            f"**Çıkış Fiyatı:** `{exit_price:.4f}`\n"
            f"**Miktar:** `{quantity}`\n"
            f"**P&L (USDT):** `{pnl:.2f}`\n" # P&L'in USDT cinsinden olduğunu varsay
        )
        if notes: # Not varsa ekle
            message += f"\n**Notlar:** {notes}"
        return self.send_message(message)

    def notify_error(self, error_message, details=""):
        """
        Bir bot hatası için Telegram bildirimi gönderir.
        Args:
            error_message (str): Ana hata mesajı.
            details (str, optional): Hata detayları.
        """
        message = (
            f"⚠️ **Bot Hatası** ⚠️\n\n"
            f"**Mesaj:** `{error_message}`\n"
        )
        if details: # Detay varsa ekle
            message += f"**Detaylar:** `{details}`"
        return self.send_message(message)

    def notify_balance(self, balance, open_positions_count, total_pnl_session=None, notes=""):
        """
        Bot durumu ve bakiye bilgisi için Telegram bildirimi gönderir.
        Args:
            balance (float): Mevcut USDT bakiyesi.
            open_positions_count (int): Açık pozisyon sayısı.
            total_pnl_session (float, optional): Oturum P&L'i.
            notes (str, optional): Ek notlar.
        """
        message = (
            f"💰 **Bot Durumu & Bakiye** 💰\n\n"
            f"**Mevcut USDT Bakiyesi:** `{balance:.2f}`\n"
            f"**Açık Pozisyonlar:** `{open_positions_count}`\n"
        )
        if total_pnl_session is not None: # Oturum P&L'i varsa ekle
             message += f"**Oturum P&L:** `{total_pnl_session:.2f}` USDT\n"
        if notes: # Not varsa ekle
            message += f"\n**Notlar:** {notes}"
        return self.send_message(message)

# Örnek kullanım (bu modülü doğrudan test etmek için)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO) # Test için temel log yapılandırması
    logger.info("TelegramNotifier test ediliyor...")

    # config.py'de Telegram bot token ve chat ID'sinin yapılandırıldığından emin olun
    if config.TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or config.TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.warning("Telegram Bot Token veya Chat ID config.py'de yapılandırılmamış. Test mesajları gönderilemiyor.")
    else:
        notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

        logger.info("Test giriş bildirimi gönderiliyor...")
        notifier.notify_trade_entry("BTCUSDT", "long", 60000.0, 0.001, 58000.0, notes="Bot geliştirme testi girişi")

        logger.info("Test kapanış bildirimi gönderiliyor...")
        notifier.notify_trade_close("ETHUSDT", "short", 3000.0, 3100.0, 0.05, -5.0, notes="Bot geliştirme testi kapanışı")

        logger.info("Test hata bildirimi gönderiliyor...")
        notifier.notify_error("Test hata mesajı", details="Test sırasında simüle edilmiş hata.")

        logger.info("Test bakiye bildirimi gönderiliyor...")
        notifier.notify_balance(10000.50, 2, 150.75, notes="Gün sonu test raporu.")

        logger.info("Basit bir mesaj gönderiliyor...")
        notifier.send_message("Bot'tan merhaba! Bu bir *Markdown* testidir. Ve bu da `kod`.")

    logger.info("TelegramNotifier testleri tamamlandı.")

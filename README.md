# Binance Futures için TradingView Webhook Botu

Bu Python tabanlı bot, TradingView uyarılarından gelen webhook sinyallerini dinler ve Binance Futures üzerinde otomatik olarak işlem yapar. Pozisyon boyutlandırma, zarar durdurma emirleri, kaldıraç ve marjin türü ayarı, programatik takip eden zarar durdurma (TSL) ve Telegram bildirimleri gibi özellikleri içerir.

## Özellikler

-   TradingView'den webhook aracılığıyla işlem sinyallerini (uzun/kısa) alır.
-   İşlem yapmak için Binance Futures API ile entegre olur.
-   İşlem yapmadan önce her sembol için belirtilen kaldıracı (örneğin, 10x) ve marjin türünü (örneğin, İZOLE) ayarlar.
-   Ticaret yapılabilir bakiye oranına ve maksimum açık işlem sayısına göre pozisyon büyüklüğünü hesaplar.
-   Giriş emirlerini (varsayılan olarak LIMIT) ve karşılık gelen STOP_MARKET zarar durdurma emirlerini otomatik olarak verir.
-   Programatik Takip Eden Zarar Durdurma (TSL):
    -   Belirlenen bir kâr ofsetine ulaşıldıktan sonra etkinleşir.
    -   Fiyatı yapılandırılmış bir yüzdeyle takip ederek Binance'teki zarar durdurma emrini ayarlar.
    -   Arka plan iş parçacığında çalışır, aktif işlemleri periyodik olarak kontrol eder.
-   Aşağıdakiler için bir Telegram kanalına gerçek zamanlı bildirimler gönderir:
    -   Bot başlatma
    -   İşlem girişleri (sembol, yön, fiyat, miktar, ZD)
    -   Takip eden zarar durdurma aktivasyonu ve güncellemeleri.
    -   İşlem kapanışları (pozisyon Binance'ten kaybolursa TSL yöneticisi tarafından algılanır).
    -   Hatalar ve kritik uyarılar.
-   Aktif işlemler için kalıcı durum yönetimi Redis kullanılarak sağlanır (TSL durumu, giriş fiyatları vb. verilerin bot yeniden başlasa bile kaybolmamasını sağlar).
-   `config.py` aracılığıyla yapılandırılabilir ticaret parametreleri.

## Kurulum ve Yapılandırma

1.  **Depoyu Klonlayın:**
    ```bash
    git clone <deponuzun_url_adresi>
    cd <depo_dizini>
    ```

2.  **Bağımlılıkları Yükleyin:**
    Bir Python sanal ortamı oluşturun ve gerekli paketleri yükleyin:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Windows'ta: venv\Scripts\activate
    pip install -r requirements.txt # requirements.txt dosyanızda redis olduğundan emin olun
    ```

3.  **Botu Yapılandırın (`config.py`):**
    -   `config.py` dosyasını düzenleyin ve bilgilerinizi girin:
        -   `BINANCE_API_KEY`, `BINANCE_API_SECRET`: Binance API kimlik bilgileriniz.
            *   **Güvenlik Notu:** API anahtarlarının Vadeli İşlemler ticaret izinlerinin etkinleştirildiğinden emin olun. Güvenlik nedeniyle para çekme izinleri devre dışı bırakılmalıdır.
        -   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Telegram bot bilgileriniz.
        -   `TRADING_PAIRS`: İşlem yapmayı düşündüğünüz tüm Binance Futures sembolleriyle güncelleyin.
        -   **Yeni/Güncellenmiş Parametreler**:
            -   `LEVERAGE = 10`: İstediğiniz kaldıracı ayarlayın (örneğin, 10x için 10).
            -   `MARGIN_TYPE = "ISOLATED"`: Genellikle "ISOLATED" (İZOLE) veya "CROSSED" (ÇAPRAZ).
            -   `EXPECTED_WEBHOOK_INTERVAL = "15m"`: **Çok Önemli.** Bu, TradingView uyarılarınızın grafik aralığıyla eşleşmelidir (örneğin, 15 dakikalık için "15m", 1 saatlik için "1h"). (Not: `config.py` dosyasındaki bu değer `15m` gibi bir zaman birimi içermelidir, sadece sayı değil.)
            -   `TRAILING_STOP = True`: Programatik takip eden zarar durdurma özelliğini etkinleştirmek için `True` olarak ayarlayın.
            -   `TRAILING_STOP_POSITIVE_OFFSET = 0.009`: Takip eden zarar durdurmayı etkinleştirmek için kâr ofseti (örneğin, %0.9).
            -   `TRAILING_STOP_POSITIVE = 0.008`: Zarar durdurmanın tepe fiyatını takip edeceği yüzde (örneğin, %0.8).
            -   `TRAILING_STOP_CHECK_INTERVAL_SECONDS = 60`: Botun takip eden zarar durdurmaları ne sıklıkta kontrol edip güncelleyeceği. Aşağıdaki API Hız Limiti uyarısına bakın.
        -   `STOP_LOSS` (başlangıçtaki zarar durdurma), `TRADABLE_BALANCE_RATIO`, `MAX_OPEN_TRADES` gibi diğer parametreleri gözden geçirin ve ayarlayın.

4.  **Redis Yapılandırması (Yeni Gereksinim):**
    -   Uygulama artık aktif işlem verilerinin kalıcı olarak saklanması için Redis kullanmaktadır. Bu, TSL aktivasyonu, mevcut ZD fiyatı vb. işlem durumlarının bot yeniden başlarsa kaybolmamasını sağlar.
    -   **`REDIS_URL` Ortam Değişkeni**: Uygulamanın Redis örneğinize bağlanması için `REDIS_URL` ortam değişkenini ayarlamanız gerekir.
        -   Örnek format: `redis://[:şifre@]sunucu:port/0`
        -   **Heroku için**: Bu genellikle bir Redis eklentisi (örneğin, Heroku Data for Redis veya Redis Cloud) eklenerek yapılandırılır. `REDIS_URL`, Heroku uygulamanızın yapılandırma değişkenlerinde otomatik olarak ayarlanacaktır.
        -   **Yerel geliştirme için**: Bu ortam değişkenini doğrudan ayarlayabilir (örneğin, `export REDIS_URL=redis://localhost:6379/0`) veya `config.py` dosyanız bir `.env` dosyası kullanacak şekilde uyarlanmışsa orada tanımlayabilirsiniz. Alternatif olarak, yerel bir Redis sunucusu çalışıyorsa yerel test için `config.py` dosyasında geçici olarak `config.REDIS_URL = "redis://localhost:6379/0"` ayarlayabilirsiniz.
    -   **`REDIS_DB` (İsteğe Bağlı)**: `config.py` dosyası, `REDIS_DB` belirtmenize olanak tanır (varsayılan olarak 0). Çoğu bulut Redis sağlayıcısı (Heroku Eklentileri gibi) için veritabanı numarası genellikle `REDIS_URL`'nin bir parçasıdır veya doğru olana varsayılan olarak ayarlanır, bu nedenle bunu değiştirmeniz gerekmeyebilir.

5.  **TradingView Uyarılarını Yapılandırın:**
    -   TradingView'de uyarılarınızı `config.EXPECTED_WEBHOOK_INTERVAL` içinde belirtilen grafik aralığında ayarlayın (örneğin, `EXPECTED_WEBHOOK_INTERVAL = "15m"` ise **15 dakikalık grafik**).
    -   Uyarı koşulu, PineScript göstergenizin belirli sinyallerine dayanmalıdır (örneğin, grafik 15 dakikalık olsa bile "4S Teyitli Uzun" veya "4S Teyitli Kısa" sinyal adları). Göstergenin 4S verileri için `request.security` kullanan dahili mantığı yine geçerli olacaktır, ancak uyarının kendisi, o noktada 4S koşulları karşılanırsa 15 dakikalık mumun kapanışında tetiklenir.
    -   **Webhook URL'si**: Her TradingView uyarısında bunu sunucunuzun genel adresine işaret edecek şekilde güncelleyin (örneğin, `http://<heroku_uygulama_adınız>.herokuapp.com/webhook` veya `http://<sunucu_ip_adresiniz>:5000/webhook`).
    -   TradingView uyarısının "Mesaj" alanındaki JSON yükünün daha önce belirtildiği gibi doğru biçimlendirildiğinden emin olun.

## Botu Çalıştırma

### Yerel Olarak (geliştirme/test için)

1.  Sanal ortamınızın etkinleştirildiğinden emin olun.
2.  Botu çalıştırın:
    ```bash
    python main.py
    ```
    Bot başlayacak, servisleri başlatacak, TSL iş parçacığını (etkinse) başlatacak ve webhook'ları dinleyecektir.

### Dağıtım (Örnek: Heroku)

1.  **Heroku CLI'yi yükleyin** ve giriş yapın.
2.  **Bir Heroku uygulaması oluşturun.**
3.  **Kodunuzu Git'e ekleyin ve dağıtın.** `Procfile` (`web: gunicorn main:app`) dahildir.
4.  **Heroku'da Yapılandırma Değişkenlerini Ayarlayın:** Güvenlik için hassas bilgileri (API anahtarları, jetonlar) Heroku'da ortam değişkenleri olarak ayarlayın. Bu yöntemi kullanıyorsanız `config.py` dosyasını bunları `os.environ.get(...)` üzerinden okuyacak şekilde değiştirin.
5.  **Günlükleri Kontrol Edin:** `heroku logs --tail` kullanın.

## Önemli Notlar

-   **Risk Yönetimi:** Vadeli işlemler önemli risk içerir. Bu bot bir araçtır, finansal danışman değildir. Gerçek fonları kullanmadan önce riskleri ve botun mantığını anlayın. **Her zaman önce Binance Testnet'te kapsamlı bir şekilde test edin.**
-   **Binance API Hız Limitleri:**
    -   Özellikle takip eden zarar durdurma özelliğiyle API hız limitlerine son derece dikkat edin.
    -   `TRAILING_STOP_CHECK_INTERVAL_SECONDS` parametresi, botun fiyatları ne sıklıkta kontrol ettiğini ve **her aktif işlem** için potansiyel olarak ZD emirlerini güncellediğini belirler.
    -   Bu aralığı çok düşük ayarlamak (örneğin, 5-10 saniye) birden fazla aktif işlemle birlikte Binance tarafından **hızla IP yasaklarına veya geçici API kısıtlamalarına** yol açabilir.
    -   Daha güvenli bir aralık genellikle eşzamanlı işlem sayısına bağlı olarak 30-300 saniyedir. Bot günlüklerini ve Binance API kullanımını izleyin.
-   **Takip Eden Zarar Durdurmalar (TSL):**
    -   Programatik TSL özelliği artık uygulanmıştır. Bir kâr ofsetinden sonra etkinleşir ve fiyatı belirli bir yüzdeyle takip eder.
    -   **TSL ile Kritik Risk**: Eski bir zarar durdurmayı iptal etme ve yenisini yerleştirme işlemi küçük bir risk penceresine sahiptir. Eskisi iptal edildikten sonra yeni ZD yerleştirme başarısız olursa, pozisyon anlık olarak korumasız kalabilir. Botun bunun için hata yönetimi vardır, ancak farkında olunması gereken kritik bir senaryodur.
-   **Durum Yönetimi:** Aktif işlem verileri (TSL durumu, giriş fiyatları, mevcut ZD fiyatları vb. dahil) artık Redis'te kalıcı olarak saklanmaktadır. Bu, bot yeniden başlarsa mevcut işlemleri doğru bir şekilde alıp yönetebileceği anlamına gelir.
-   **Hata Yönetimi:** Bot günlüklerini ve Telegram bildirimlerini yakından izleyin.
-   **Gerçekleşen Dolum Fiyatları**: Bot şu anda P&L hesaplamaları ve başlangıç TSL takibi için webhook'tan gelen hedef giriş fiyatını kullanmaktadır. Daha yüksek doğruluk için, giriş emirlerinin gerçek dolum fiyatını sorgulamak, önerilen bir gelecekteki geliştirmedir (kodda TODO olarak işaretlenmiştir).

## Sorumluluk Reddi

Bu botun geliştiricileri, kullanımından kaynaklanan herhangi bir mali kayıptan sorumlu değildir. Riski size ait olmak üzere kullanın.

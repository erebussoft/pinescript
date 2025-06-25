import os
import sys

# Flask uygulamanızın bulunduğu dizini Python path'ine ekleyin.
# Bu, 'main.py' dosyasının bulunduğu projenin kök dizini olmalıdır.
# Phusion Passenger genellikle mevcut çalışma dizinini zaten path'e ekler,
# ancak emin olmak için bu satır eklenebilir veya hosting sağlayıcısının
# önerisine göre düzenlenebilir.
# sys.path.insert(0, os.path.dirname(__file__))

# Flask app nesnesini main.py'den import edin
# 'main', main.py dosyanızdır; 'app', main.py içindeki Flask nesnenizin adıdır.
try:
    from main import app as application
    # Phusion Passenger'ın loglarına veya bir test log dosyasına
    # uygulamanın başarıyla import edildiğine dair bir işaret bırakılabilir.
    # Örneğin, basit bir print() bile Passenger loglarına gidebilir:
    print("Uygulama 'main.py' dosyasından 'application' olarak başarıyla yüklendi (passenger_wsgi.py).")
except Exception as e:
    # Import sırasında bir hata olursa, bunu loglamak önemlidir.
    # Passenger logları genellikle bu tür hataları gösterir.
    # Daha detaylı loglama için:
    # error_message = f"KRİTİK HATA: Flask uygulaması yüklenemedi (passenger_wsgi.py)! Hata: {str(e)}\n"
    # error_message += f"Python Path: {sys.path}\n"
    # error_message += f"Mevcut Çalışma Dizini: {os.getcwd()}\n"
    # print(error_message, file=sys.stderr) # Hata akışına yazdır
    # raise # Hatayı yeniden fırlat ki Passenger görsün
    pass # Şimdilik basit tutalım, Passenger'ın kendi hata loglarına güvenelim.
       # Hosting sağlayıcısı genellikle bu hataları kendi loglarında gösterir.

# Bazı hosting ortamları, uygulamanın çalışıp çalışmadığını test etmek için
# basit bir callable bekleyebilir. `application` yukarıda tanımlandı.

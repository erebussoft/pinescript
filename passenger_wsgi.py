import os
import sys

# Proje kök dizinini Python path'ine ekle (main.py ve passenger_wsgi.py aynı dizinde varsayılıyor)
sys.path.insert(0, os.path.dirname(__file__))

from main import app as application

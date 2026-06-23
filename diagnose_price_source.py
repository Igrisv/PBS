"""
diagnose_price_source.py — Busca donde sale el precio
"""
import sys, os
import re
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup
from core.scraper import get_session

URL = "https://www.amazon.com.mx/dp/B0G5FWJ8NL"

def diagnose_price():
    sess = get_session()
    resp = sess.get(URL, timeout=15)
    html = resp.text

    # Buscar el precio que reportó el usuario: 2,490
    print("\n-- Buscando '2,490' en el HTML --")
    matches = re.finditer(r'2[.,]490', html)
    found = False
    for m in matches:
        found = True
        start, end = m.start(), m.end()
        snippet = html[max(0, start-100):min(len(html), end+100)]
        print(f"\nMatch en pos {start}:")
        print(f"{snippet!r}")

    if not found:
        print("  ❌ No se encontró '2,490' en el HTML.")

if __name__ == "__main__":
    diagnose_price()

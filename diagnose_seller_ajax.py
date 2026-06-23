"""
diagnose_seller_ajax.py — Script de diagnóstico para detectar vendedor via AJAX
"""
import sys, os
import re
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup
from core.scraper import get_session, _is_captcha

ASIN = "B0G5FWJ8NL"
# URL de AJAX que Amazon usa para el panel "All Offers Display" (AOD)
AJAX_URL = f"https://www.amazon.com.mx/gp/product/ajax/ref=dp_aod_ALL_mbc?asin={ASIN}&experienceId=aodAjaxMain"

def diagnose_ajax():
    print(f"\n{'='*70}")
    print(f"  DIAGNOSTICO AJAX (AOD)")
    print(f"  URL: {AJAX_URL}")
    print(f"{'='*70}")

    sess = get_session()
    resp = sess.get(AJAX_URL, timeout=15)
    print(f"  Status: {resp.status_code}")

    soup = BeautifulSoup(resp.content, "lxml")

    if _is_captcha(soup):
        print("  CAPTCHA detectado.")
        return

    # Buscar enlaces de vendedor
    print("\n  -- Enlaces de vendedor (/gp/aag/main) --")
    links = soup.find_all("a", href=re.compile(r"/gp/aag/main"))
    if links:
        for i, link in enumerate(links[:10]):
            print(f"  {i+1}. Text: {link.get_text(strip=True)} | Href: {link.get('href')[:60]}...")
    else:
        print("  ❌ No se encontraron enlaces /gp/aag/main en el AJAX.")

    # Buscar bloques de oferta
    print("\n  -- Bloques de oferta (aod-offer) --")
    offers = soup.find_all(id=re.compile(r"aod-offer|aod-pinned-offer"))
    if offers:
        for i, off in enumerate(offers[:5]):
             print(f"  Offer {i+1} ID: {off.get('id')} | Text: {off.get_text(' ', strip=True)[:100]}...")
    else:
        print("  ❌ No se encontraron bloques aod-offer.")

if __name__ == "__main__":
    diagnose_ajax()

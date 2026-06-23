"""
diagnose_seller_deep.py — Búsqueda profunda de vendedor en HTML
"""
import sys, os
import re
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup
from core.scraper import get_session, _is_captcha

URL = "https://www.amazon.com.mx/dp/B0G5FWJ8NL"

def diagnose_deep():
    print(f"\n{'='*70}")
    print(f"  DIAGNOSTICO PROFUNDO")
    print(f"  URL: {URL}")
    print(f"{'='*70}")

    sess = get_session()
    resp = sess.get(URL, timeout=15)
    print(f"  Status: {resp.status_code}")

    soup = BeautifulSoup(resp.content, "lxml")
    html = resp.text

    # 1. Buscar cualquier enlace que contenga "seller="
    print("\n  -- Enlaces con 'seller=' en la URL --")
    seller_links = soup.find_all("a", href=re.compile(r"seller="))
    for i, link in enumerate(seller_links[:10]):
        print(f"  {i+1}. Text: {link.get_text(strip=True)} | Href: {link.get('href')[:100]}")

    # 2. Buscar el nombre que el usuario encontró: "Lia Toys"
    print("\n  -- Búsqueda de 'Lia Toys' en el HTML --")
    matches = re.findall(r'.{0,50}Lia Toys.{0,50}', html, re.I)
    for m in matches[:5]:
        print(f"  >>> {m.strip()}")

    # 3. Buscar "M-20 LLC"
    print("\n  -- Búsqueda de 'M-20 LLC' en el HTML --")
    matches2 = re.findall(r'.{0,50}M-20 LLC.{0,50}', html, re.I)
    for m in matches2[:5]:
        print(f"  >>> {m.strip()}")

    # 4. Buscar nombres de vendedores en bloques específicos (usando la info del usuario)
    print("\n  -- Inspección de divs con seller ratings --")
    ratings = soup.find_all(id=re.compile(r"aod-offer-seller-rating"))
    for i, r in enumerate(ratings):
        parent = r.parent
        print(f"  Rating {i+1} parent HTML: {str(parent)[:200]}...")

if __name__ == "__main__":
    diagnose_deep()

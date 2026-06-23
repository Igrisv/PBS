"""
diagnose_seller.py — Script de diagnóstico para detectar vendedor
Ejecutar: python diagnose_seller.py
Muestra exactamente qué HTML ve el bot en los campos clave de vendedor.
"""
import sys, os
import re
import json
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup
from core.scraper import get_session, _is_captcha

# Configurar salida a UTF-8 forzada para evitar errores en Windows
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

URL = "https://www.amazon.com.mx/dp/B0G5FWJ8NL"
OFFERS_URL = "https://www.amazon.com.mx/gp/offer-listing/B0G5FWJ8NL"

SELLER_IDS = [
    "tabular-buybox",
    "merchant-info",
    "apex_desktop",
    "coreBuyboxGroup",
    "desktop_buybox_feature_div",
    "buybox",
    "sellerProfileTriggerId",
    "aod-offer-sold-by",
    "aod-pinned-offer-sold-by",
]

def diagnose(label, url):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  URL: {url}")
    print(f"{'='*70}")

    sess = get_session()
    try:
        resp = sess.get(url, timeout=15)
        print(f"  Status: {resp.status_code}")
    except Exception as e:
        print(f"  Error: {e}")
        return

    soup = BeautifulSoup(resp.content, "lxml")

    if _is_captcha(soup):
        print("  CAPTCHA detectado - Amazon esta bloqueando.")
        return

    print("\n  -- Campos de Vendedor Encontrados --")
    found_any = False
    for sid in SELLER_IDS:
        el = soup.find(id=sid)
        if el:
            found_any = True
            raw_text = el.get_text(" ", strip=True)[:200]
            print(f"\n  [ID: {sid}]")
            print(f"  Tag: <{el.name}>")
            print(f"  Texto: {raw_text}")

    # Buscar enlaces de vendedor directamente
    print("\n  -- Enlaces de perfil de vendedor (/gp/aag/main) --")
    links = soup.find_all("a", href=re.compile(r"/gp/aag/main"))
    if links:
        for i, link in enumerate(links[:10]):
            print(f"  {i+1}. Text: {link.get_text(strip=True)} | Href: {link.get( 'href' )[:50]}...")
    else:
        print("  ❌ No se encontraron enlaces /gp/aag/main")

    if not found_any:
        print("  ❌ Ningun ID de vendedor conocido encontrado.")

    # Buscar "Amazon Mexico"
    print("\n  -- Busqueda de 'Amazon Mexico' --")
    matches2 = re.findall(r'.{0,30}Amazon M[eé]xico.{0,80}', resp.text)
    if matches2:
        for m in matches2[:3]:
            print(f"  >>> {m.strip()}")
    else:
        print("  ❌ 'Amazon Mexico' no aparece en el HTML.")

if __name__ == "__main__":
    diagnose("PAGINA PRINCIPAL", URL)
    diagnose("PAGINA DE OFERTAS", OFFERS_URL)
    print("\nDiagnostico completo.")

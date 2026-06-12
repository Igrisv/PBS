"""
scraper.py — Componente 1: El Recolector
Extrae título, disponibilidad y precio de una página de Amazon MX.
Ejecutar directamente para test: python scraper.py
"""

import re
import json
import time
import random
import logging
import unicodedata
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─── User-Agents reales para rotar y evitar bloqueos ─────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
]


@dataclass
class ProductSnapshot:
    name: str            # Nombre del producto en products.json
    url: str             # URL de Amazon MX
    title: str           # Título real extraído de Amazon
    in_stock: bool       # True = hay stock
    availability_text: str  # Texto crudo de disponibilidad
    price: str           # Precio formateado o "N/D"
    seller: str = "Desconocido" # Vendedor del producto
    release_date: Optional[str] = None  # Fecha de lanzamiento (YYYY-MM-DD o None)
    captcha_detected: bool = False
    error: Optional[str] = None
    normalized_title: str = "" # Título limpio para comparaciones
    amazon_present: bool = False  # True si Amazon MX aparece entre las ofertas
    image_url: Optional[str] = None # URL de la imagen del producto

# ─── Configuración de Proxies ────────────────────────────────────────────────
PROXIES_FILE = Path(__file__).resolve().parent.parent / "data" / "proxies.txt"
_all_proxies = []

def _load_proxies():
    global _all_proxies
    if PROXIES_FILE.exists():
        try:
            with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                _all_proxies = lines
                if _all_proxies:
                    logger.info(f"[SCRAPER] {len(_all_proxies)} proxies cargados.")
        except Exception as e:
            logger.error(f"[SCRAPER] Error cargando proxies: {e}")

# Carga inicial
_load_proxies()

# ─── Sesión Global para Anti-Bot ─────────────────────────────────────────────
_current_session = None
_current_ua = None
_current_proxy = None

def get_session() -> requests.Session:
    global _current_session, _current_ua, _current_proxy
    if _current_session is None:
        _current_session = requests.Session()
        _current_ua = random.choice(USER_AGENTS)
        _current_session.headers.update(_build_headers(_current_ua))
        
        if _all_proxies:
            _current_proxy = random.choice(_all_proxies)
            _current_session.proxies = {
                "http": _current_proxy,
                "https": _current_proxy
            }
            logger.info(f"[SCRAPER] Nueva sesión con proxy: {_current_proxy}")
        else:
            _current_proxy = None

    return _current_session

def rotate_session() -> None:
    global _current_session, _current_proxy
    logger.info("[SCRAPER] Rotando sesión HTTP, User-Agent y Proxy por prevención...")
    _current_session = None
    _current_proxy = None

def normalize_title(title: str) -> str:
    """Normaliza un título para evitar duplicados por tildes, mayúsculas o espacios."""
    if not title: return ""
    text = unicodedata.normalize('NFD', title).encode('ascii', 'ignore').decode("utf-8")
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return " ".join(text.split())



def _build_headers(ua: Optional[str] = None) -> dict:
    if not ua: ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }


def _is_captcha(soup: BeautifulSoup) -> bool:
    """Detecta si Amazon devolvió una página de CAPTCHA."""
    title = soup.find("title")
    if title and "robot" in title.text.lower():
        return True
    if soup.find("form", {"action": "/errors/validateCaptcha"}):
        return True
    return False


def _extract_availability(soup: BeautifulSoup) -> tuple[bool, str]:
    """Retorna (in_stock, availability_text)."""
    # Selector principal de disponibilidad
    avail_div = soup.find("div", {"id": "availability"})
    if avail_div:
        span = avail_div.find("span")
        text = span.get_text(strip=True) if span else avail_div.get_text(strip=True)
        text_lower = text.lower()

        out_of_stock_keywords = ["agotado", "no disponible", "currently unavailable",
                                  "out of stock", "no está disponible"]
        preorder_keywords = ["preventa", "coming soon", "próximamente", "expected", "available soon"]
        in_stock_keywords = ["en stock", "in stock", "disponible", "unidades",
                             "en existencia", "queda", "quedan"]

        if any(k in text_lower for k in preorder_keywords):
            return False, text

        if any(k in text_lower for k in out_of_stock_keywords):
            return False, text
        if any(k in text_lower for k in in_stock_keywords):
            return True, text

        # Si hay texto pero no matchea palabras clave, asumimos disponible
        return bool(text), text

    # Fallback: si hay botón "Agregar al carrito" hay stock
    add_to_cart = soup.find("input", {"id": "add-to-cart-button"})
    if add_to_cart:
        return True, "Disponible (botón de compra detectado)"

    # Si hay botón "Ver opciones de compra" => existen ofertas de vendedores
    see_options = soup.find("a", {"id": "buybox-see-all-buying-choices"}) or \
                  soup.find("a", string=re.compile(r"ver opciones de compra|see all buying options", re.I))
    if see_options:
        return False, "Multi-vendedor: revisar ofertas"

    return False, "Disponibilidad desconocida"


def _extract_price(soup: BeautifulSoup) -> str:
    """Extrae el precio del producto."""
    # Selector moderno de Amazon
    price_span = soup.find("span", {"class": re.compile(r"a-price-whole")})
    if price_span:
        fraction = soup.find("span", {"class": re.compile(r"a-price-fraction")})
        whole = price_span.get_text(strip=True).replace(",", "")
        frac = fraction.get_text(strip=True) if fraction else "00"
        return f"${whole}.{frac} MXN"

    # Fallback con id
    price_el = soup.find("span", {"id": "priceblock_ourprice"}) or \
               soup.find("span", {"id": "priceblock_dealprice"})
    if price_el:
        return price_el.get_text(strip=True)

    return "Precio no disponible"


def _extract_seller(soup: BeautifulSoup) -> str:
    """
    Extrae el nombre del vendedor principal de la página.
    Para Amazon MX busca texto literal 'Amazon México' en el buybox.
    """
    # Buscar en el tabular buybox (buybox estándar de Amazon MX)
    tabular_buybox = soup.find("div", {"id": "tabular-buybox"})
    if tabular_buybox:
        text = tabular_buybox.get_text(" ", strip=True)
        if "Amazon México" in text:
            return "Amazon México"

    # Buscar en merchant-info (versión compacta del buybox)
    merchant_info = soup.find("div", {"id": "merchant-info"})
    if merchant_info:
        text = merchant_info.get_text(strip=True)
        if "Amazon México" in text or "Amazon.com.mx" in text:
            return "Amazon México"
        # Enlace al perfil de vendedor externo
        seller_link = merchant_info.find("a", {"id": "sellerProfileTriggerId"})
        if seller_link:
            return seller_link.get_text(strip=True)
        return text

    # Buscar enlace de vendedor directo
    seller_link = soup.find("a", {"id": "sellerProfileTriggerId"})
    if seller_link:
        return seller_link.get_text(strip=True)

    # Fallback: si el texto de la página contiene "Amazon México" prominentemente
    shipped_by = soup.find("span", string=re.compile(r"Amazon\.com\.mx|Amazon México", re.I))
    if shipped_by:
        return "Amazon México"

    return "Desconocido"


def _extract_image_url(soup: BeautifulSoup) -> Optional[str]:
    """Extrae la URL de la imagen principal del producto."""
    img = soup.find("img", {"id": "landingImage"}) or \
          soup.find("img", {"id": "main-image"}) or \
          soup.find("img", {"class": "a-dynamic-image"})
    
    if img:
        # Amazon suele poner la imagen de alta resolución en data-old-hires o en el src
        return img.get("data-old-hires") or img.get("src")
    return None


def _extract_release_date(soup: BeautifulSoup) -> Optional[str]:
    """
    Extrae 'Fecha de lanzamiento' de la página de Amazon.
    Retorna una fecha en formato 'YYYY-MM-DD' o None si no se encuentra.
    """
    MESES_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }

    def parse_date(text: str) -> Optional[str]:
        text = text.strip()
        m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', text)
        if m:
            day, month_str, year = m.group(1), m.group(2).lower(), m.group(3)
            month = MESES_ES.get(month_str)
            if month:
                return f"{year}-{month:02d}-{int(day):02d}"
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
        if m:
            return m.group(0)
        return None

    keywords = ["fecha de lanzamiento", "fecha en que estuvo disponible", "available since"]

    bullets_div = soup.find("div", {"id": "detailBullets_feature_div"})
    if bullets_div:
        for li in bullets_div.find_all("li"):
            text = li.get_text(" ", strip=True).lower()
            if any(k in text for k in keywords):
                date = parse_date(li.get_text(" ", strip=True))
                if date:
                    return date

    for table_id in ["productDetails_techSpec_section_1", "productDetails_detailBullets_sections1"]:
        table = soup.find("table", {"id": table_id})
        if table:
            for row in table.find_all("tr"):
                header = row.find("th")
                value = row.find("td")
                if header and value:
                    if any(k in header.get_text(strip=True).lower() for k in keywords):
                        date = parse_date(value.get_text(strip=True))
                        if date:
                            return date

    return None


# ─── Detección de Amazon MX en página de ofertas ──────────────────────────────

def _check_amazon_mx_in_offers(asin: str) -> bool:
    """
    Verifica si Amazon México aparece como vendedor en la página
    de ofertas (/gp/offer-listing/ASIN).
    Busca patrones específicos del DOM que Amazon MX utiliza.
    """
    offers_url = f"https://www.amazon.com.mx/gp/offer-listing/{asin}"
    try:
        sess = get_session()
        resp = sess.get(offers_url, timeout=12)
        if resp.status_code != 200:
            return False

        text = resp.text
        soup = BeautifulSoup(text, "lxml")

        if _is_captcha(soup):
            logger.warning(f"[SCRAPER] CAPTCHA en offers page ASIN={asin}")
            return False

        # Método 1: aria-label con "vendedor Amazon México" (más confiable)
        # Ejemplo: aria-label="Agregar al carrito del vendedor Amazon México y el precio $929.00"
        if re.search(r'vendedor\s+Amazon\s+M[eé]xico', text, re.I):
            return True

        # Método 2: Buscar el bloque de la oferta anclada (#aod-pinned-offer) o
        # cualquier oferta (#aod-offer-list) con texto "Amazon México" sin enlace
        # (Amazon no tiene storefront propio, aparece como texto plano)
        for offer_block in soup.find_all(id=re.compile(r"aod-pinned-offer|aod-offer-\d+")):
            sold_by_div = offer_block.find(id=re.compile(r"aod-offer-sold-by|aod-pinned-offer-sold-by"))
            if sold_by_div:
                sold_text = sold_by_div.get_text(strip=True)
                # Amazon MX aparece como texto plano (sin enlace de storefront)
                if "Amazon México" in sold_text or "Amazon.com.mx" in sold_text:
                    return True
                # Fallback: coincidencia genérica "Amazon" sin ser un vendedor externo
                inner_link = sold_by_div.find("a")
                if not inner_link and re.search(r'\bAmazon\b', sold_text):
                    return True

        # Método 3: Buscar en el texto plano completo de la página
        # Patrón: "Amazon México" seguido de precio o botón de compra
        if "Amazon México" in text or "Amazon.com.mx" in text:
            # Verificar que no sea solo en el header/footer
            page_soup = soup.find("div", {"id": "aod-offer-list"}) or \
                        soup.find("div", {"id": "all-offers-display"})
            if page_soup and ("Amazon México" in page_soup.get_text() or
                              "Amazon.com.mx" in page_soup.get_text()):
                return True

        return False

    except Exception as e:
        logger.warning(f"[SCRAPER] Error verificando offers para {asin}: {e}")
        return False


def _has_multiple_sellers(soup: BeautifulSoup, text: str) -> bool:
    """Detecta si la página muestra que hay múltiples vendedores/ofertas."""
    multi_signals = [
        soup.find("a", {"id": "buybox-see-all-buying-choices"}),
        soup.find("a", string=re.compile(r"ver opciones de compra|see all buying options|ver \d+ ofertas", re.I)),
        soup.find("div", {"id": "aod-ingress-container"}),
    ]
    if any(multi_signals):
        return True

    # Señales en texto
    if re.search(r'otros vendedores|other sellers|opciones de compra|buying options', text, re.I):
        return True

    return False


def scrape(product_name: str, url: str) -> ProductSnapshot:
    """
    Scrape una URL de Amazon MX y retorna un ProductSnapshot.
    No lanza excepciones; los errores van dentro del snapshot.
    """
    try:
        session = get_session()
        response = session.get(url, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "lxml")

        if _is_captcha(soup):
            logger.warning(f"[CAPTCHA] {product_name} — se reintentará en el próximo ciclo.")
            rotate_session()
            return ProductSnapshot(
                name=product_name, url=url,
                title=product_name, in_stock=False,
                availability_text="CAPTCHA detectado",
                price="N/D", captcha_detected=True,
                normalized_title=normalize_title(product_name)
            )

        # Título del producto
        title_el = soup.find("span", {"id": "productTitle"})
        title = title_el.get_text(strip=True) if title_el else product_name

        in_stock, avail_text = _extract_availability(soup)
        price = _extract_price(soup)
        seller = _extract_seller(soup)
        release_date = _extract_release_date(soup)
        image_url = _extract_image_url(soup)

        full_text = response.text
        amazon_present = False

        # ── LÓGICA QUIRÚRGICA: VERIFICAR AMAZON MX ────────────────────────────
        # Caso 1: El vendedor principal ya ES Amazon México
        if seller == "Amazon México":
            amazon_present = True

        # Caso 2: Hay múltiples vendedores — revisar la página de ofertas
        elif _has_multiple_sellers(soup, full_text):
            logger.info(f"[SCRAPER] Multi-vendedor detectado en {product_name}. Verificando ofertas...")
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
            if asin_match:
                asin = asin_match.group(1)
                amazon_present = _check_amazon_mx_in_offers(asin)
                if amazon_present:
                    logger.info(f"[SCRAPER] ✅ Amazon MX está disponible en ofertas para {product_name}")
                    # Si Amazon está en ofertas y la página principal reporta 
                    # "ver opciones", el producto SÍ está disponible para comprar
                    if not in_stock:
                        in_stock = True
                        avail_text = "Disponible vía Amazon México (en ofertas)"
                else:
                    logger.info(f"[SCRAPER] ❌ Amazon MX NO aparece en las ofertas de {product_name}")
        # ─────────────────────────────────────────────────────────────────────

        return ProductSnapshot(
            name=product_name, url=url,
            title=title, in_stock=in_stock,
            availability_text=avail_text, price=price,
            seller=seller, release_date=release_date,
            normalized_title=normalize_title(title),
            amazon_present=amazon_present,
            image_url=image_url
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"[ERROR] {product_name}: {e}")
        return ProductSnapshot(
            name=product_name, url=url,
            title=product_name, in_stock=False,
            availability_text="Error de conexión",
            price="N/D", error=str(e),
            normalized_title=normalize_title(product_name)
        )


# ─── Test standalone ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.amazon.com.mx/dp/B0CW7BKSD8"
    print(f"\n[SEARCH] Scrapeando: {test_url}\n")
    snapshot = scrape("Test Product", test_url)
    print(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))

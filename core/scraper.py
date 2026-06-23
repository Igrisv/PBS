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
    is_preorder: bool = False     # True si el producto está en preventa
    sellers: list[str] = field(default_factory=list) # Lista de vendedores encontrados

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
            return True, text, True # En stock (para alerta), pero es preventa

        if any(k in text_lower for k in out_of_stock_keywords):
            return False, text, False
        if any(k in text_lower for k in in_stock_keywords):
            return True, text, False

        # Si hay texto pero no matchea palabras clave, asumimos disponible
        return bool(text), text, False

    # Fallback: si hay botón "Agregar al carrito" hay stock
    add_to_cart = soup.find("input", {"id": "add-to-cart-button"})
    if add_to_cart:
        return True, "Disponible", False

    # Preventa en botón
    preorder_btn = soup.find("input", {"id": "add-to-cart-button-preorder"}) or \
                   soup.find("span", {"id": "preorderButton"})
    if preorder_btn:
        return True, "Preventa detectada", True

    return False, "Desconocido", False


def _extract_price(soup: BeautifulSoup) -> str:
    """
    Extrae el precio del producto restringiéndolo al área principal
    para evitar capturar precios de recomendaciones laterales o patrocinados.
    """
    # Intentar buscar el precio dentro de los contenedores principales de Amazon
    main_containers = [
        soup.find("div", {"id": "centerCol"}),
        soup.find("div", {"id": "corePrice_feature_div"}),
        soup.find("div", {"id": "apex_desktop"}),
        soup.find("div", {"id": "buybox"}),
        soup.find("div", {"id": "mediaTab_content_landing_0"}),
        soup.find("div", {"id": "unifiedPrice_feature_div"})
    ]

    for container in main_containers:
        if not container:
            continue
        
        # Selectores específicos de precio dentro del contenedor
        # 1. Selector moderno con centavos
        price_whole = container.find("span", {"class": "a-price-whole"})
        if price_whole:
            whole_txt = price_whole.get_text(strip=True).replace(",", "").replace(".", "")
            price_fraction = container.find("span", {"class": "a-price-fraction"})
            frac_txt = price_fraction.get_text(strip=True) if price_fraction else "00"
            return f"${whole_txt}.{frac_txt} MXN"

        # 2. Selector offscreen (muy común)
        offscreen = container.find("span", {"class": "a-offscreen"})
        if offscreen:
             txt = offscreen.get_text(strip=True)
             if "$" in txt: return txt

        # 3. Ids clásicos
        classic = container.find("span", {"id": "priceblock_ourprice"}) or \
                  container.find("span", {"id": "priceblock_dealprice"})
        if classic:
            return classic.get_text(strip=True)

    return "No disponible"


def _extract_seller(soup: BeautifulSoup) -> str:
    """
    Extrae el nombre del vendedor principal de la página.
    Cubre múltiples layouts de Amazon MX: tabular buybox, merchant-info,
    desktop buybox, y el moderno apex_desktop.
    """
    # Normaliza texto de posibles vendedores para evitar strings vacíos o ruido
    def clean(txt: str) -> str:
        txt = txt.strip()
        # Eliminar prefijos comunes
        for prefix in ["Vendido por", "Sold by", "Ships from and sold by"]:
            if txt.lower().startswith(prefix.lower()):
                txt = txt[len(prefix):].strip()
        return txt if len(txt) > 1 else ""

    # 1. tabular-buybox (layout más común en Amazon MX)
    tabular = soup.find("div", {"id": "tabular-buybox"})
    if tabular:
        txt = tabular.get_text(" ", strip=True)
        if "Amazon México" in txt:
            return "Amazon México"

    # 2. merchant-info (buybox compacto)
    mi = soup.find("div", {"id": "merchant-info"})
    if mi:
        txt = mi.get_text(" ", strip=True)
        if "Amazon México" in txt or "Amazon.com.mx" in txt:
            return "Amazon México"
        link = mi.find("a", {"id": "sellerProfileTriggerId"})
        if link:
            c = clean(link.get_text(strip=True))
            if c: return c
        c = clean(txt)
        if c: return c

    # 3. desktop buybox nuevo (apex_desktop / coreBuyboxGroup)
    for box_id in ["apex_desktop", "coreBuyboxGroup", "desktop_buybox_feature_div"]:
        box = soup.find(id=box_id)
        if box:
            txt = box.get_text(" ", strip=True)
            if "Amazon México" in txt or "Amazon.com.mx" in txt:
                return "Amazon México"
            # Buscar enlace de vendedor dentro del box
            link = box.find("a", {"id": "sellerProfileTriggerId"})
            if link:
                c = clean(link.get_text(strip=True))
                if c: return c
            # Buscar spans con texto cercano a "Vendido por"
            for span in box.find_all("span"):
                span_txt = span.get_text(strip=True)
                if "Vendido por" in span_txt or "Sold by" in span_txt:
                    c = clean(span_txt)
                    if c: return c
            break

    # 4. Enlace directo de sellerProfileTriggerId
    link = soup.find("a", {"id": "sellerProfileTriggerId"})
    if link:
        c = clean(link.get_text(strip=True))
        if c: return c

    # 5. Búsqueda por aria-label (Basado en hallazgo del usuario)
    # Ejemplo: <a aria-label="Nombre. Abre una nueva página" ...>
    link = soup.find("a", aria_label=re.compile(r"Abre una nueva p[aá]gina|Opens a new page", re.I))
    if link:
        c = clean(link.get_text(strip=True))
        if c: return c

    # 6. Cualquier enlace con href de vendedor
    link = soup.find("a", href=re.compile(r"/gp/aag/main|seller="))
    if link:
        c = clean(link.get_text(strip=True))
        if c: return c

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

def _check_amazon_mx_in_offers(asin: str) -> tuple[bool, list[str], Optional[str]]:
    """
    Verifica vendedores en la página de ofertas.
    Retorna (amazon_mx_presente, lista_de_vendedores, mejor_precio).
    """
    offers_url = f"https://www.amazon.com.mx/gp/offer-listing/{asin}"
    all_sellers = []
    amazon_found = False
    best_price = None
    
    try:
        sess = get_session()
        resp = sess.get(offers_url, timeout=12)
        
        # Fallback: Si la página de ofertas estándar falla o está vacía, intentar con ?aod=1
        if resp.status_code != 200 or "aod-offer" not in resp.text:
            aod_url = f"https://www.amazon.com.mx/dp/{asin}/ref=olp-opf-redir?aod=1"
            resp = sess.get(aod_url, timeout=12)
            
        if resp.status_code != 200:
            return False, [], None

        text = resp.text
        soup = BeautifulSoup(text, "lxml")

        if _is_captcha(soup):
            logger.warning(f"[SCRAPER] CAPTCHA en offers page ASIN={asin}")
            return False, [], None

        # Extraer nombres y precios de bloques de oferta
        for offer_block in soup.find_all(id=re.compile(r"aod-pinned-offer|aod-offer-\d+")):
            # Extraer Precio
            price_el = offer_block.find("span", {"class": "a-price"})
            curr_price = None
            if price_el:
                offscreen = price_el.find("span", {"class": "a-offscreen"})
                if offscreen:
                    curr_price = offscreen.get_text(strip=True)
                if not best_price:
                    best_price = curr_price

            # Extraer Vendedor — El nombre está en un <a> que apunta a /gp/aag/main
            # Ejemplo: <a href="/gp/aag/main?...">Lia Toys Y Collectibles</a>
            seller_link = offer_block.find("a", href=re.compile(r"/gp/aag/main"))
            if seller_link:
                seller_name = seller_link.get_text(strip=True)
                
                if "Amazon México" in seller_name or "Amazon.com.mx" in seller_name:
                    amazon_found = True
                    if "Amazon México" not in all_sellers:
                        all_sellers.append("Amazon México")
                    if curr_price:
                        best_price = curr_price
                elif seller_name and seller_name not in all_sellers:
                    # Vendedor de terceros (Lia Toys, M-20 LLC, etc.)
                    all_sellers.append(seller_name)
            else:
                # Fallback: buscar por ID legacy por si acaso
                sold_by_div = offer_block.find(id=re.compile(r"aod-offer-sold-by|aod-pinned-offer-sold-by"))
                if sold_by_div:
                    sold_text = sold_by_div.get_text(" ", strip=True)
                    if "Amazon México" in sold_text or "Amazon.com.mx" in sold_text:
                        amazon_found = True
                        if "Amazon México" not in all_sellers:
                            all_sellers.append("Amazon México")
                        if curr_price:
                            best_price = curr_price
                    else:
                        name = sold_text.replace("Vendido por", "").replace("Sold by", "").strip()
                        if name and name not in all_sellers:
                            all_sellers.append(name)

        # Fallback amplio: si no se encontró ningún vendedor en los bloques,
        # escanear TODOS los enlaces /gp/aag/main en la página de ofertas
        if not all_sellers:
            for link in soup.find_all("a", href=re.compile(r"/gp/aag/main")):
                name = link.get_text(strip=True)
                if not name:
                    continue
                if "Amazon México" in name or "Amazon.com.mx" in name:
                    amazon_found = True
                    if "Amazon México" not in all_sellers:
                        all_sellers.append("Amazon México")
                elif name not in all_sellers:
                    all_sellers.append(name)

        # Fallback texto: buscar literal "Amazon México" en el HTML
        if not amazon_found and re.search(r'vendedor\s+Amazon\s+M[eé]xico', text, re.I):
            amazon_found = True
            if "Amazon México" not in all_sellers:
                all_sellers.insert(0, "Amazon México")

        return amazon_found, all_sellers, best_price

    except Exception as e:
        logger.warning(f"[SCRAPER] Error verificando offers para {asin}: {e}")
        return False, [], None


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


def scrape(product_name: str, url: str, amazon_only: bool = True) -> ProductSnapshot:
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

        in_stock, avail_text, is_preorder = _extract_availability(soup)
        price = _extract_price(soup)
        seller = _extract_seller(soup)
        release_date = _extract_release_date(soup)
        image_url = _extract_image_url(soup)

        full_text = response.text
        amazon_present = False
        all_sellers = [seller] if seller != "Desconocido" else []

        # ── LÓGICA QUIRÚRGICA: VERIFICAR AMAZON MX ────────────────────────────
        # Determinar si hay múltiples ofertas
        has_multi = _has_multiple_sellers(soup, full_text)
        
        if seller == "Amazon México":
            amazon_present = True

        # Consulta la página de ofertas si:
        # 1. Necesitamos explícitamente a Amazon (amazon_only) y aún no lo detectamos.
        # 2. O si el vendedor es desconocido y queremos intentar identificarlo.
        # 3. Y siempre que haya múltiples ofertas detectadas.
        should_check_offers = has_multi or seller == "Desconocido"
        
        # Optimización: si no necesitamos Amazon y ya hay stock, no es crítico ver ofertas
        if not amazon_only and in_stock:
             should_check_offers = False

        if should_check_offers:
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
            if asin_match:
                asin = asin_match.group(1)
                amazon_present_offers, sellers_list, offers_price = _check_amazon_mx_in_offers(asin)
                amazon_present = amazon_present or amazon_present_offers
                
                # Si no teníamos precio real, usar el de las ofertas
                if (price == "No disponible" or ".." in price) and offers_price:
                    price = offers_price

                # Combinar listas de vendedores y actualizar seller principal si era desconocido
                for s in sellers_list:
                    if s not in all_sellers: all_sellers.append(s)
                if seller == "Desconocido" and sellers_list:
                    seller = sellers_list[0]  # El primero es el más relevante
                
                if amazon_present and not in_stock:
                    in_stock = True
                    avail_text = "Disponible vía Amazon México (en ofertas)"

        # Consolidar nombre de vendedor para log
        if not all_sellers:
             final_seller = "Desconocido"
        elif len(all_sellers) == 1:
             final_seller = all_sellers[0]
        else:
             final_seller = f"Multi ({', '.join(all_sellers[:3])})"

        return ProductSnapshot(
            name=product_name, url=url,
            title=title, in_stock=in_stock,
            availability_text=avail_text, price=price,
            seller=final_seller, release_date=release_date,
            normalized_title=normalize_title(title),
            amazon_present=amazon_present,
            image_url=image_url,
            is_preorder=is_preorder,
            sellers=all_sellers
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

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
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
import os
import threading

from scrapling.fetchers import Fetcher, StealthyFetcher
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

# StealthyFetcher usa un navegador real (Camoufox/Firefox) con anti-fingerprinting
# Es más lento pero prácticamente indetectable por Amazon
_USE_STEALTH = True  # Cambiar a False para volver al modo HTTP simple

# Directorio base para guardar cookies y sesión de navegador
_BASE_USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "browser_profiles")
os.makedirs(_BASE_USER_DATA_DIR, exist_ok=True)

# Dominios de tracking/analytics que Amazon carga pero son innecesarios para el scraping
_BLOCKED_DOMAINS = {
    "amazon-adsystem.com", "doubleclick.net", "googlesyndication.com",
    "googletagmanager.com", "google-analytics.com", "adservice.google.com",
    "omtrdc.net", "assoc-amazon.com", "images-na.ssl-images-amazon.com",
    "images-amazon.com", "ssl-images-amazon.com"
}

logger = logging.getLogger(__name__)

def css_first(node, selector):
    res = node.css(selector)
    return res[0] if res else None



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
    bytes_downloaded: int = 0     # (Deprecado, mantener para compatibilidad)
    bytes_vps: int = 0            # Bytes descargados directo del VPS
    bytes_proxy: int = 0          # Bytes descargados a través del Proxy
    vps_blocked: bool = False     # True si Amazon bloqueó la IP del VPS en este ciclo

def get_page_stats(page) -> tuple[int, int, bool]:
    """Extrae el tamaño de la página y calcula las estadísticas de red."""
    size = get_page_size(page)
    proxy_used = getattr(page, 'proxy_used', False)
    vps_blocked = getattr(page, 'vps_blocked', False)
    bytes_vps = 0 if proxy_used else size
    bytes_proxy = size if proxy_used else 0
    return bytes_vps, bytes_proxy, vps_blocked

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
_proxy_rotator = None

def _get_proxy_rotator():
    """Crea o retorna el ProxyRotator de Scrapling para rotación nativa."""
    global _proxy_rotator
    if _all_proxies and _proxy_rotator is None:
        try:
            _proxy_rotator = ProxyRotator(_all_proxies)
            logger.info(f"[SCRAPER] ProxyRotator inicializado con {len(_all_proxies)} proxies")
        except Exception as e:
            logger.warning(f"[SCRAPER] ProxyRotator no disponible: {e}")
    return _proxy_rotator

# ─── Sesión Global para Anti-Bot ─────────────────────────────────────────────
_current_proxy = None

def clean_amazon_url(url: str) -> str:
    """
    Limpia una URL de Amazon conservando solo el ASIN.
    Elimina ref=, social_share=, y cualquier otro parámetro que delate automatización.
    """
    if not url:
        return url
    # Extraer ASIN directamente
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', url)
    if asin_match:
        asin = asin_match.group(1)
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/dp/{asin}"
    return url

def get_page(url: str):
    """
    Obtiene una página usando StealthyFetcher con enrutamiento inteligente y optimizaciones de caché:
    1. Intenta desde el VPS directo (Sin Proxy) usando un perfil persistente (cookies)
    2. Si salta CAPTCHA, cae al proxy residencial
    """
    url = clean_amazon_url(url)
    proxy = random.choice(_all_proxies) if _all_proxies else None
    
    def _check_captcha(p) -> bool:
        if not p or not p.body: return False
        return bool(p.css("form[action='/errors/validateCaptcha']"))

    if _USE_STEALTH:
        # Generar un perfil único por hilo para evitar que Chromium bloquee la carpeta (SingletonLock)
        thread_id = str(threading.get_ident())
        thread_data_dir = os.path.join(_BASE_USER_DATA_DIR, f"profile_{thread_id}")
        
        # Intento 1: Sin proxy (Internet del VPS = 0 costo) con persistencia de cookies
        try:
            page = StealthyFetcher.fetch(
                url,
                proxy=None,  # <-- ¡Directo desde el VPS!
                headless=True,
                network_idle=False,
                disable_resources=True,
                block_ads=True,
                blocked_domains=_BLOCKED_DOMAINS,
                user_data_dir=thread_data_dir, # <-- Persistencia de cookies por hilo
                timeout=30000,
            )
            
            if not _check_captcha(page):
                page.proxy_used = False
                page.vps_blocked = False
                return page
            
            logger.warning(f"[SCRAPER] CAPTCHA en VPS directo. Usando fallback proxy para {url}")
        except Exception as e:
            logger.warning(f"[SCRAPER] Error en VPS directo: {e}")

        # Intento 2: Fallback al Proxy Residencial
        try:
            page_fb = StealthyFetcher.fetch(
                url,
                proxy=proxy, # <-- Proxy residencial activado
                headless=True,
                network_idle=False,
                disable_resources=True,
                block_ads=True,
                blocked_domains=_BLOCKED_DOMAINS,
                # No usamos la misma carpeta de perfil para la IP del proxy para no cruzar huellas
                timeout=30000,
            )
            page_fb.proxy_used = True
            page_fb.vps_blocked = True
            return page_fb
        except Exception as e:
            logger.warning(f"[SCRAPER] StealthyFetcher fallback falló: {e}")
    
    # Fallback final: HTTP simple
    if proxy:
        logger.debug(f"[SCRAPER] Usando proxy HTTP: {proxy[:30]}...")
    page_http = Fetcher.get(url, proxy=proxy, impersonate="chrome", timeout=15)
    page_http.proxy_used = bool(proxy)
    page_http.vps_blocked = False
    return page_http

def get_page_size(page) -> int:
    """Calcula el tamaño en bytes de la página descargada."""
    try:
        if hasattr(page, 'body') and page.body:
            return len(page.body)
        elif hasattr(page, 'text') and page.text:
            return len(page.text.encode('utf-8', errors='ignore'))
    except:
        pass
    return 0

def rotate_session() -> None:
    # Con proxy rotativo, no hay nada que hacer manualmente — el proveedor rota el IP
    logger.info("[SCRAPER] Rotación de proxy delegada al proveedor.")

def normalize_title(title: str) -> str:
    """Normaliza un título para evitar duplicados por tildes, mayúsculas o espacios."""
    if not title: return ""
    text = unicodedata.normalize('NFD', title).encode('ascii', 'ignore').decode("utf-8")
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return " ".join(text.split())

def _is_captcha(page) -> bool:
    """Detecta si Amazon devolvió una página de CAPTCHA."""
    title = css_first(page, "title")
    if title and "robot" in title.text.lower():
        return True
    if css_first(page, "form[action='/errors/validateCaptcha']"):
        return True
    return False

def _extract_availability(page) -> tuple[bool, str]:
    """Retorna (in_stock, availability_text)."""
    # Selector principal de disponibilidad
    avail_div = css_first(page, "#availability")
    if avail_div:
        span = css_first(avail_div, "span")
        text = span.text.strip() if span else avail_div.text.strip()
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
    add_to_cart = css_first(page, "input#add-to-cart-button")
    if add_to_cart:
        return True, "Disponible", False

    # Preventa en botón
    preorder_btn = css_first(page, "input#add-to-cart-button-preorder") or \
                   css_first(page, "span#preorderButton")
    if preorder_btn:
        return True, "Preventa detectada", True

    return False, "Desconocido", False


def _extract_price(page) -> str:
    """
    Extrae el precio del producto restringiéndolo al área principal
    para evitar capturar precios de recomendaciones laterales o patrocinados.
    """
    main_containers = [
        css_first(page, "#centerCol"),
        css_first(page, "#corePrice_feature_div"),
        css_first(page, "#apex_desktop"),
        css_first(page, "#buybox"),
        css_first(page, "#mediaTab_content_landing_0"),
        css_first(page, "#unifiedPrice_feature_div")
    ]

    for container in main_containers:
        if not container:
            continue
        
        # 1. Selector moderno con centavos
        price_whole = css_first(container, "span.a-price-whole")
        if price_whole:
            whole_txt = price_whole.text.strip().replace(",", "").replace(".", "")
            price_fraction = css_first(container, "span.a-price-fraction")
            frac_txt = price_fraction.text.strip() if price_fraction else "00"
            return f"${whole_txt}.{frac_txt} MXN"

        # 2. Selector offscreen (muy común)
        offscreen = css_first(container, "span.a-offscreen")
        if offscreen:
             txt = offscreen.text.strip()
             if "$" in txt: return txt

        # 3. Ids clásicos
        classic = css_first(container, "span#priceblock_ourprice") or \
                  css_first(container, "span#priceblock_dealprice")
        if classic:
            return classic.text.strip()

    return "No disponible"


def _extract_seller(page) -> str:
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
    tabular = css_first(page, "#tabular-buybox")
    if tabular:
        txt = " ".join(tabular.text.split())
        if "Amazon México" in txt:
            return "Amazon México"

    # 2. merchant-info (buybox compacto)
    mi = css_first(page, "#merchant-info")
    if mi:
        txt = " ".join(mi.text.split())
        if "Amazon México" in txt or "Amazon.com.mx" in txt:
            return "Amazon México"
        link = css_first(mi, "a#sellerProfileTriggerId")
        if link:
            c = clean(link.text.strip())
            if c: return c
        c = clean(txt)
        if c: return c

    # 3. desktop buybox nuevo (apex_desktop / coreBuyboxGroup)
    for box_id in ["#apex_desktop", "#coreBuyboxGroup", "#desktop_buybox_feature_div"]:
        box = css_first(page, box_id)
        if box:
            txt = " ".join(box.text.split())
            if "Amazon México" in txt or "Amazon.com.mx" in txt:
                return "Amazon México"
            # Buscar enlace de vendedor dentro del box
            link = css_first(box, "a#sellerProfileTriggerId")
            if link:
                c = clean(link.text.strip())
                if c: return c
            # Buscar spans con texto cercano a "Vendido por"
            for span in box.css("span"):
                span_txt = span.text.strip()
                if "Vendido por" in span_txt or "Sold by" in span_txt:
                    c = clean(span_txt)
                    if c: return c
            break

    # 4. offerDisplayFeatures_desktop (nuevo layout ODF)
    for sel in ["div[data-csa-c-content-id='desktop-merchant-info']", 
                "span.offer-display-feature-text-message",
                "a[data-csa-c-content-id='odf-desktop-merchant-info']"]:
        odf = css_first(page, sel)
        if odf:
            txt = " ".join(odf.text.split())
            if "Amazon México" in txt or "Amazon.com.mx" in txt:
                return "Amazon México"
            # Tratar de aislar el nombre si hay "Remitente / Vendedor"
            lines = [x.strip() for x in txt.split("Remitente / Vendedor") if x.strip()]
            if lines:
                c = clean(lines[-1])
                if c: return c
            c = clean(txt)
            if c: return c

    # 4. Enlace directo de sellerProfileTriggerId
    link = css_first(page, "a#sellerProfileTriggerId")
    if link:
        c = clean(link.text.strip())
        if c: return c

    # 5. Búsqueda por aria-label (Basado en hallazgo del usuario)
    link = css_first(page, "a[aria-label~='Abre'], a[aria-label~='Opens']")
    if link:
        c = clean(link.text.strip())
        if c: return c

    # 6. Cualquier enlace con href de vendedor
    link = css_first(page, "a[href*='/gp/aag/main'], a[href*='seller=']")
    if link:
        c = clean(link.text.strip())
        if c: return c

    return "Desconocido"


def _extract_image_url(page) -> Optional[str]:
    """Extrae la URL de la imagen principal del producto."""
    img = css_first(page, "img#landingImage") or \
          css_first(page, "img#main-image") or \
          css_first(page, "img.a-dynamic-image")
    
    if img:
        return img.attrib.get("data-old-hires") or img.attrib.get("src")
    return None


def _extract_release_date(page) -> Optional[str]:
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

    bullets_div = css_first(page, "#detailBullets_feature_div")
    if bullets_div:
        for li in bullets_div.css("li"):
            text = " ".join(li.text.split()).lower()
            if any(k in text for k in keywords):
                date = parse_date(" ".join(li.text.split()))
                if date:
                    return date

    for table_id in ["#productDetails_techSpec_section_1", "#productDetails_detailBullets_sections1"]:
        table = css_first(page, table_id)
        if table:
            for row in table.css("tr"):
                header = css_first(row, "th")
                value = css_first(row, "td")
                if header and value:
                    if any(k in header.text.strip().lower() for k in keywords):
                        date = parse_date(value.text.strip())
                        if date:
                            return date

    return None


# ─── Detección de Amazon MX en página de ofertas ──────────────────────────────

def _check_amazon_mx_in_offers(asin: str) -> tuple[bool, list[str], Optional[str], int, int, bool]:
    """
    Verifica vendedores en la página de ofertas.
    Retorna (amazon_mx_presente, lista_de_vendedores, mejor_precio, bytes_vps, bytes_proxy, vps_blocked).
    """
    offers_url = f"https://www.amazon.com.mx/gp/offer-listing/{asin}"
    all_sellers = []
    amazon_found = False
    best_price = None
    bytes_vps = 0
    bytes_proxy = 0
    vps_blocked = False
    
    try:
        page = get_page(offers_url)
        v, p, b = get_page_stats(page)
        bytes_vps += v; bytes_proxy += p; vps_blocked = vps_blocked or b
        
        # Fallback: Si la página de ofertas estándar falla o está vacía, intentar con ?aod=1
        if page.status != 200 or "aod-offer" not in page.text:
            aod_url = f"https://www.amazon.com.mx/dp/{asin}/ref=olp-opf-redir?aod=1"
            page = get_page(aod_url)
            v, p, b = get_page_stats(page)
            bytes_vps += v; bytes_proxy += p; vps_blocked = vps_blocked or b
            
        if page.status != 200:
            return False, [], None, bytes_vps, bytes_proxy, vps_blocked

        if _is_captcha(page):
            logger.warning(f"[SCRAPER] CAPTCHA en offers page ASIN={asin}")
            return False, [], None, bytes_vps, bytes_proxy, vps_blocked

        # Extraer nombres y precios de bloques de oferta
        for offer_block in page.css("div[id^='aod-pinned-offer'], div[id^='aod-offer-']"):
            # Extraer Precio
            price_el = css_first(offer_block, "span.a-price")
            curr_price = None
            if price_el:
                offscreen = css_first(price_el, "span.a-offscreen")
                if offscreen:
                    curr_price = offscreen.text.strip()
                if not best_price:
                    best_price = curr_price

            # Extraer Vendedor — El nombre está en un <a> que apunta a /gp/aag/main
            seller_link = css_first(offer_block, "a[href*='/gp/aag/main']")
            if seller_link:
                seller_name = seller_link.text.strip()
                
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
                sold_by_div = css_first(offer_block, "div[id*='aod-offer-sold-by'], div[id*='aod-pinned-offer-sold-by']")
                if sold_by_div:
                    sold_text = " ".join(sold_by_div.text.split())
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

        # Fallback amplio: si no se encontró ningún vendedor en los bloques
        if not all_sellers:
            for link in page.css("a[href*='/gp/aag/main']"):
                name = link.text.strip()
                if not name:
                    continue
                if "Amazon México" in name or "Amazon.com.mx" in name:
                    amazon_found = True
                    if "Amazon México" not in all_sellers:
                        all_sellers.append("Amazon México")
                elif name not in all_sellers:
                    all_sellers.append(name)

        # Fallback texto: buscar literal "Amazon México" en el HTML
        if not amazon_found and re.search(r'vendedor\s+Amazon\s+M[eé]xico', page.text, re.I):
            amazon_found = True
            if "Amazon México" not in all_sellers:
                all_sellers.insert(0, "Amazon México")

        return amazon_found, all_sellers, best_price, bytes_vps, bytes_proxy, vps_blocked

    except Exception as e:
        logger.warning(f"[SCRAPER] Error verificando offers para {asin}: {e}")
        return False, [], None, bytes_vps, bytes_proxy, vps_blocked


def _has_multiple_sellers(page, text: str) -> bool:
    """Detecta si la página muestra que hay múltiples vendedores/ofertas."""
    multi_signals = [
        css_first(page, "a#buybox-see-all-buying-choices"),
        css_first(page, "a[href*='buying-choices']"),
        css_first(page, "div#aod-ingress-container"),
    ]
    if any(multi_signals):
        return True

    # Señales en texto
    if re.search(r'otros vendedores|other sellers|opciones de compra|buying options|ver \d+ ofertas|see all buying options', text, re.I):
        return True

    return False


def scrape(product_name: str, url: str, amazon_only: bool = True) -> ProductSnapshot:
    """
    Scrape una URL de Amazon MX y retorna un ProductSnapshot.
    No lanza excepciones; los errores van dentro del snapshot.
    """
    bytes_vps = 0
    bytes_proxy = 0
    vps_blocked = False
    
    try:
        page = get_page(url)
        v, p, b = get_page_stats(page)
        bytes_vps += v; bytes_proxy += p; vps_blocked = vps_blocked or b

        if page.status not in (200, 404, 503) and page.status >= 400:
            raise Exception(f"HTTP Error {page.status}")

        if _is_captcha(page):
            logger.warning(f"[CAPTCHA] {product_name} — se reintentará en el próximo ciclo.")
            
            # Guardar el HTML de la página bloqueada para revisión
            try:
                import os
                os.makedirs("/root/PBS/logs", exist_ok=True)
                with open("/root/PBS/logs/last_captcha.html", "wb") as f:
                    f.write(page.body if hasattr(page, 'body') else str(page).encode('utf-8'))
                logger.info("Guardado HTML del CAPTCHA en /root/PBS/logs/last_captcha.html")
            except Exception as e:
                logger.error(f"No se pudo guardar HTML del CAPTCHA: {e}")

            rotate_session()
            return ProductSnapshot(
                name=product_name, url=url,
                title=product_name, in_stock=False,
                availability_text="CAPTCHA detectado",
                price="N/D", captcha_detected=True,
                normalized_title=normalize_title(product_name),
                bytes_vps=bytes_vps,
                bytes_proxy=bytes_proxy,
                vps_blocked=vps_blocked
            )

        # Título del producto
        title_el = css_first(page, "span#productTitle")
        title = title_el.text.strip() if title_el else product_name

        in_stock, avail_text, is_preorder = _extract_availability(page)
        price = _extract_price(page)
        seller = _extract_seller(page)
        release_date = _extract_release_date(page)
        image_url = _extract_image_url(page)

        full_text = page.text
        amazon_present = False
        all_sellers = [seller] if seller != "Desconocido" else []

        # ── LÓGICA QUIRÚRGICA: VERIFICAR AMAZON MX ────────────────────────────
        has_multi = _has_multiple_sellers(page, full_text)
        
        if seller == "Amazon México":
            amazon_present = True
            # FIX: si la página principal ya muestra Amazon MX como vendedor pero
            # #availability dice "Agotado", el producto SÍ tiene stock real.
            # Esto ocurre cuando Amazon publica el precio en el buybox pero el
            # widget de disponibilidad aún no se ha actualizado.
            if not in_stock:
                in_stock = True
                avail_text = "Disponible vía Amazon México"

        should_check_offers = has_multi or seller == "Desconocido"
        
        if not amazon_only and in_stock:
             should_check_offers = False

        if should_check_offers:
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
            if asin_match:
                asin = asin_match.group(1)
                amazon_present_offers, sellers_list, offers_price, v2, p2, b2 = _check_amazon_mx_in_offers(asin)
                amazon_present = amazon_present or amazon_present_offers
                bytes_vps += v2
                bytes_proxy += p2
                vps_blocked = vps_blocked or b2
                
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
            sellers=all_sellers,
            bytes_vps=bytes_vps,
            bytes_proxy=bytes_proxy,
            vps_blocked=vps_blocked
        )

    except Exception as e:
        logger.error(f"[ERROR] {product_name}: {e}")
        return ProductSnapshot(
            name=product_name, url=url,
            title=product_name, in_stock=False,
            availability_text="Error interno", price="N/D",
            error=str(e),
            normalized_title=normalize_title(product_name),
            bytes_vps=bytes_vps,
            bytes_proxy=bytes_proxy,
            vps_blocked=vps_blocked
        )


# ─── Test standalone ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.amazon.com.mx/dp/B0CW7BKSD8"
    print(f"\n[SEARCH] Scrapeando: {test_url}\n")
    snapshot = scrape("Test Product", test_url)
    print(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))


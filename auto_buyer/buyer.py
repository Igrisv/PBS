"""
CONFIGURACIÓN:
  1. Copia .env.example a .env y rellena tus credenciales de Amazon.
  2. Edita config.json y agrega las URLs de los productos (enabled: true).
  3. Pon "confirm_order": false para modo DRY-RUN (sin comprar de verdad).
  4. Ejecutar: python buyer.py
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)


BASE_DIR = Path(__file__).resolve().parent
if BASE_DIR.name != "auto_buyer":

    _sub = BASE_DIR / "auto_buyer"
    BASE_DIR = _sub if _sub.is_dir() else BASE_DIR

if not load_dotenv(BASE_DIR / ".env"):
    load_dotenv(BASE_DIR.parent / ".env")

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "auto_buyer.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("auto_buyer")

# Credenciales
AMAZON_EMAIL    = os.getenv("AMAZON_EMAIL", "").strip()
AMAZON_PASSWORD = os.getenv("AMAZON_PASSWORD", "").strip()
CHECK_INTERVAL  = int(os.getenv("CHECK_INTERVAL_SECONDS", "45"))

# Archivos de estado persistente
SESSION_FILE = BASE_DIR / "amazon_session.json"
BOUGHT_FILE  = BASE_DIR / "bought_products.json"  # Persiste entre reinicios

# ---------------------------------------------------------------------------
# Selectores de Amazon MX — con fallbacks múltiples
# ---------------------------------------------------------------------------
SEL = {
    # Login
    "email_field":          "input[name='email'], #ap_email, input[type='email']",
    "continue_btn":         "input#continue, #continue, .a-button-input, input[type='submit']",
    "password_field":       "input[name='password'], #ap_password, input[type='password']",
    "signin_btn":           "input#signInSubmit, #signInSubmit, .a-button-input, input[type='submit']",
    "login_nav_link":       "#nav-link-accountList-nav-line-1, #nav-link-accountList",

    # Producto
    "add_to_cart_btn":      "#add-to-cart-button",
    "buy_now_btn":          "#buy-now-button",

    # Popups post-agregar al carrito (garantías, protección, etc.)
    # Probamos varios IDs conocidos de Amazon MX
    "warranty_decline":     "#attachSiNoCoverage, #siNoCoverage-announce, [id*='NoCoverage']",

    # Carrito → Checkout
    "proceed_to_checkout":  "[name='proceedToRetailCheckout']",
    "checkout_alt":         "#sc-buy-box-ptc-button, .checkout-button",

    # Dirección
    "deliver_here_btn":     "[name='shipToThisAddress']",

    # Pago OXXO / Efectivo — múltiples estrategias
    "oxxo_radio_value":     "input[value*='OXXO'], input[value*='oxxo'], input[value*='CASH'], input[value*='Cash'], input[value*='CashInstrument']",
    "oxxo_label_text":      "label:has-text('OXXO'), span:has-text('OXXO'), span:has-text('Paga en efectivo'), div:has-text('Pago en OXXO'), div:has-text('Paga en efectivo en tienda'), label:has-text('efectivo en tienda'), .a-label:has-text('efectivo')",
    "use_payment_btn":      "[name='useThisPayment'], #payment-submit-btn, .pmts-portal-component input[type='submit'], [name*='SetPaymentPlan'], .a-button-input[name*='payment']",

    # Revisión y confirmación
    "place_order_btn":      "#submitOrderButtonId, [name='placeYourOrder1'], input[value*='Realizar pedido']",
}

# User-Agents realistas para Linux en 2026 (Chrome 125-126)
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Script de inyección para evadir detección de Playwright/headless
STEALTH_SCRIPT = """
// Ocultar webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Simular plugins del navegador (navegadores reales los tienen)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ],
});

// Simular lenguajes
Object.defineProperty(navigator, 'languages', { get: () => ['es-MX', 'es', 'en-US', 'en'] });

// Ocultar que el contexto Chrome tiene Playwright
if (window.chrome) {
    window.chrome.runtime = {};
}

// Parchear permissions API (detectado en algunos tests de bots)
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
"""


# ---------------------------------------------------------------------------
# Helpers de comportamiento humano
# ---------------------------------------------------------------------------

async def human_delay(min_ms: float = 800, max_ms: float = 2500) -> None:
    """Pausa aleatoria para simular tiempo de reacción humano."""
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Escribe texto carácter por carácter con delay aleatorio entre teclas,
    similar a un usuario real. Mucho más difícil de detectar que page.fill().
    Tiene varios fallbacks en caso de que el selector falle.
    """
    selectors = [s.strip() for s in selector.split(",")]
    el = None
    
    for sel in selectors:
        try:
            candidate = page.locator(sel).first
            await candidate.wait_for(state="visible", timeout=3000)
            el = candidate
            break
        except Exception:
            continue

    if el is None:
        # Fallback: intentar fill genérico
        logger.debug(f"[human_type] Selectores no visibles. Forzando fill en: {selectors[0]}")
        try:
            await page.fill(selectors[0], text, timeout=3000, force=True)
        except Exception as e:
            logger.warning(f"[human_type] fill fallback falló: {e}")
        return

    # Usar .focus() junto con click forzado si es necesario, ya que 
    # a veces Amazon intercepta eventos y click() normal se queda pegado.
    try:
        await el.focus(timeout=2000)
        await el.click(timeout=2000, force=True)
    except Exception as e:
        logger.warning(f"[human_type] Focus/click explícito falló: {e}")

    await human_delay(200, 600)
    
    # Asegurar que el campo se limpie
    await el.fill("") 
    await human_delay(100, 300)

    try:
        # Escribir con locator.type que es más seguro que page.keyboard
        # (keyboard asume foco, locator envía eventos directos)
        for char in text:
            await el.type(char, delay=random.randint(60, 150))
    except Exception as e:
        logger.error(f"[human_type] Error al intentar tipear '{text}': {e}")

    await human_delay(300, 700)


async def click_safely(page: Page, selector: str, timeout: int = 8000) -> bool:
    """
    Intenta hacer click en un selector, con scroll suave al elemento primero.
    Retorna True si tuvo éxito, False si no encontró el elemento.
    """
    try:
        el = page.locator(selector).first
        if await el.count() == 0:
            return False
        await el.scroll_into_view_if_needed(timeout=timeout)
        await human_delay(300, 800)
        await el.click(timeout=timeout)
        return True
    except Exception:
        return False


async def detect_captcha(page: Page) -> bool:
    """Detecta si Amazon está mostrando un CAPTCHA."""
    if "captcha" in page.url.lower():
        return True
    signs = [
        "form[action='/errors/validateCaptcha']",
        "input#captchacharacters",
        "div.a-box:has-text('Escribe los caracteres')",
    ]
    for sign in signs:
        if await page.locator(sign).count() > 0:
            return True
    return False


# ---------------------------------------------------------------------------
# Gestión de sesión
# ---------------------------------------------------------------------------

async def load_session(context: BrowserContext) -> bool:
    """Carga cookies de sesión guardadas."""
    if not SESSION_FILE.exists():
        return False
    try:
        cookies = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        logger.info("[SESSION] Cookies cargadas desde archivo.")
        return True
    except Exception as e:
        logger.warning(f"[SESSION] Error cargando cookies: {e}")
        return False


async def save_session(context: BrowserContext) -> None:
    """Guarda cookies actuales a disco."""
    try:
        cookies = await context.cookies()
        SESSION_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        logger.info("[SESSION] Sesión guardada en disco.")
    except Exception as e:
        logger.warning(f"[SESSION] Error guardando sesión: {e}")


async def is_logged_in(page: Page) -> bool:
    """
    Verifica sesión navegando a la home de Amazon MX y buscando el nombre del usuario.
    """
    try:
        # Usamos la home que es más fiable para ver el estado de la cuenta
        await page.goto("https://www.amazon.com.mx", timeout=25000, wait_until="domcontentloaded")
        await human_delay(1000, 2000)

        # Si nos redirigió al login, no hay duda
        if "ap/signin" in page.url or "ap/register" in page.url:
            logger.info("[SESSION] Redirigido a login. Sesión inactiva.")
            return False

        # Verificar el texto de la navbar
        try:
            # Selector específico del nombre en Amazon MX
            account_line = page.locator("#nav-link-accountList-nav-line-1").first
            if await account_line.count() > 0:
                text = (await account_line.text_content(timeout=3000) or "").lower()
                # Si dice "identifícate" o "sign in" o no contiene "hola", no estamos logueados
                if "identif" in text or "sign in" in text or "ingresa" in text:
                    logger.info("[SESSION] Navbar dice 'identifícate'. Sesión inactiva.")
                    return False
                if "hola" in text:
                    logger.info(f"[SESSION] Sesión confirmada activa (vía navbar: '{text.strip()}')")
                    return True
        except Exception:
            pass

        # Fallback: si no estamos en login y la URL es de amazon, asumimos que sí (riesgo bajo)
        if "amazon.com.mx" in page.url and "signin" not in page.url:
            return True

    except Exception as e:
        logger.warning(f"[SESSION] Error verificando sesión: {e}")
    return False


async def do_login(page: Page) -> bool:
    """Realiza el flujo de login en Amazon con comportamiento humano."""
    if not AMAZON_EMAIL or not AMAZON_PASSWORD:
        logger.error("[LOGIN] ❌ AMAZON_EMAIL o AMAZON_PASSWORD no configurados en .env")
        return False

    logger.info("[LOGIN] Iniciando sesión en Amazon MX...")
    try:
        # Navegar a la página de inicio primero (más natural que ir directo al signin)
        await page.goto("https://www.amazon.com.mx", timeout=30000, wait_until="domcontentloaded")
        await human_delay(1500, 3000)

        # Hacer click en "Iniciar sesión" desde la navbar
        try:
            await page.locator("#nav-link-accountList").click(timeout=5000)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await human_delay(1000, 2000)
        except Exception:
            # Fallback: ir directamente
            await page.goto(
                "https://www.amazon.com.mx/ap/signin?openid.pape.max_auth_age=0"
                "&openid.return_to=https%3A%2F%2Fwww.amazon.com.mx%2F&openid.identity=http%3A%2F%2F"
                "specs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=mxflex"
                "&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2F"
                "specs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2F"
                "specs.openid.net%2Fauth%2F2.0",
                timeout=30000,
                wait_until="domcontentloaded",
            )
            await human_delay(1200, 2500)

        # Paso 1: Email (escritura humana carácter por carácter)
        await human_type(page, SEL["email_field"], AMAZON_EMAIL)
        await human_delay(600, 1200)
        await click_safely(page, SEL["continue_btn"])
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await human_delay(1000, 2000)

        # Manejar posible CAPTCHA después del email
        if await detect_captcha(page):
            logger.error("[LOGIN] ❌ CAPTCHA en el login. Imposible continuar automáticamente.")
            await _take_debug_screenshot(page, "login_captcha")
            return False

        # Paso 2: Contraseña (escritura humana)
        await human_type(page, SEL["password_field"], AMAZON_PASSWORD)
        await human_delay(500, 1000)
        await click_safely(page, SEL["signin_btn"])
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await human_delay(2000, 3500)

        # Verificar resultado del login
        if "ap/mfa" in page.url or "ap/cvf" in page.url:
            logger.error(
                "[LOGIN] ❌ Verificación en dos pasos requerida (2FA/OTP/CVF). "
                "Desactiva 2FA en tu cuenta de Amazon para poder usar el bot."
            )
            await _take_debug_screenshot(page, "login_2fa")
            return False

        if "ap/signin" in page.url or "ap/register" in page.url:
            # Credenciales incorrectas
            logger.error(f"[LOGIN] ❌ Login fallido. Verifica tu email y contraseña. URL: {page.url}")
            await _take_debug_screenshot(page, "login_failed")
            return False

        logger.info("[LOGIN] ✅ Login exitoso.")
        return True

    except Exception as e:
        logger.error(f"[LOGIN] ❌ Error inesperado durante login: {e}")
        await _take_debug_screenshot(page, "login_error")
        return False


# ---------------------------------------------------------------------------
# Gestión de productos comprados (persistencia entre reinicios)
# ---------------------------------------------------------------------------

def load_bought() -> set:
    """Carga el set de URLs ya compradas desde disco."""
    if BOUGHT_FILE.exists():
        try:
            data = json.loads(BOUGHT_FILE.read_text(encoding="utf-8"))
            urls = set(data.get("bought_urls", []))
            if urls:
                logger.info(f"[STATE] {len(urls)} URL(s) ya compradas cargadas desde disco.")
            return urls
        except Exception as e:
            logger.warning(f"[STATE] Error leyendo {BOUGHT_FILE.name}: {e}")
    return set()


def save_bought(bought: set) -> None:
    """Guarda el set de URLs compradas a disco."""
    try:
        data = {
            "bought_urls": list(bought),
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        BOUGHT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[STATE] Error guardando estado de compras: {e}")


# ---------------------------------------------------------------------------
# Verificación de disponibilidad
# ---------------------------------------------------------------------------

async def is_product_available(page: Page, url: str) -> bool:
    """
    Verifica si el botón 'Agregar al carrito' está presente y habilitado.
    Retorna False si hay CAPTCHA o el producto está agotado.
    """
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await human_delay(1500, 3000)  # Esperar renderizado dinámico

        # CAPTCHA check
        if await detect_captcha(page):
            logger.warning("[CHECK] ⚠️ CAPTCHA detectado al verificar producto.")
            await _take_debug_screenshot(page, "check_captcha")
            # Espera larga para que Amazon se calme
            await asyncio.sleep(random.uniform(90, 180))
            return False

        # Buscar botón principal
        add_btn = page.locator(SEL["add_to_cart_btn"])
        if await add_btn.count() > 0:
            is_enabled = await add_btn.is_enabled()
            if is_enabled:
                return True

        # Algunos listados usan "Comprar ahora"; si existe también hay stock
        buy_now = page.locator(SEL["buy_now_btn"])
        if await buy_now.count() > 0 and await buy_now.is_enabled():
            return True

        return False

    except PlaywrightTimeoutError:
        logger.warning(f"[CHECK] Timeout al cargar producto.")
        return False
    except Exception as e:
        logger.error(f"[CHECK] Error verificando disponibilidad: {e}")
        return False


# ---------------------------------------------------------------------------
# Flujo de compra
# ---------------------------------------------------------------------------

async def _clear_cart(page: Page) -> None:
    """
    Vacía el carrito antes de agregar el nuevo producto para evitar
    checkouts con artículos no deseados o fallos por carrito lleno.
    """
    try:
        await page.goto(
            "https://www.amazon.com.mx/gp/cart/view.html",
            timeout=20000,
            wait_until="domcontentloaded",
        )
        await human_delay(800, 1500)

        # Si no hay artículos, listo
        empty_msg = page.locator("h1:has-text('El carrito está vacío'), .sc-your-amazon-cart-is-empty")
        if await empty_msg.count() > 0:
            return

        # Seleccionar todos y eliminar
        select_all = page.locator("[name='selectAllItems']")
        if await select_all.count() > 0:
            await select_all.check()
            await human_delay(400, 800)
            delete_btn = page.locator("[value*='delete'], input.sc-action-delete, [data-action='delete']").first
            if await delete_btn.count() > 0:
                await delete_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                await human_delay(600, 1200)
                logger.info("[CART] Carrito limpiado correctamente.")
    except Exception as e:
        logger.debug(f"[CART] No se pudo limpiar el carrito (puede que ya estaba vacío): {e}")


async def buy_product(page: Page, product: dict, confirm: bool) -> bool:
    """
    Flujo completo de compra:
    Limpiar carrito → Agregar al carrito → Checkout → Seleccionar OXXO
    → (Confirmar si confirm=True)

    Retorna True si el flujo llegó a la revisión final (o se confirmó el pedido).
    """
    name = product["name"]
    url  = product["url"]
    logger.info(f"[BUYER] 🛒 Iniciando flujo de compra: {name}")

    try:
        # --- Paso 0: Limpiar carrito ---
        await _clear_cart(page)

        # --- Paso 1: Agregar al carrito ---
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await human_delay(1500, 2800)

        if await detect_captcha(page):
            logger.error("[BUYER] CAPTCHA al ir al producto. Abortando ciclo.")
            await _take_debug_screenshot(page, "buy_captcha")
            return False

        add_btn = page.locator(SEL["add_to_cart_btn"])
        if not await add_btn.count():
            logger.warning("[BUYER] Botón 'Agregar al carrito' no encontrado.")
            await _take_debug_screenshot(page, "no_add_btn")
            return False

        await add_btn.scroll_into_view_if_needed()
        await human_delay(400, 900)
        await add_btn.click()
        logger.info("[BUYER]   ✅ Click en 'Agregar al carrito'.")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await human_delay(1500, 2500)

        # --- Paso 2: Descartar popups (garantías, protección, etc.) ---
        # Intentamos varios selectores conocidos de Amazon MX
        warranty_selectors = [
            "#attachSiNoCoverage",
            "#siNoCoverage-announce",
            "[id*='NoCoverage']",
            "button:has-text('No, gracias')",
            "a:has-text('No, gracias')",
            "span:has-text('Sin protección'):visible",
        ]
        for ws in warranty_selectors:
            try:
                btn = page.locator(ws).first
                if await btn.count() > 0 and await btn.is_visible(timeout=2000):
                    await btn.click()
                    await human_delay(600, 1200)
                    logger.info(f"[BUYER]   Popup de garantía descartado ({ws}).")
                    break
            except Exception:
                continue

        # Navegamos directamente al carrito
        await page.goto(
            "https://www.amazon.com.mx/gp/cart/view.html",
            timeout=25000,
            wait_until="domcontentloaded",
        )
        await human_delay(1500, 3000)

        # --- VALIDACIÓN: ¿Realmente hay algo en el carrito? ---
        try:
            cart_count_el = page.locator("#nav-cart-count").first
            count_text = await cart_count_el.text_content(timeout=5000)
            if count_text and count_text.strip() == "0":
                logger.warning("[BUYER] ⚠️ El carrito reporta 0 productos. Intentando recargar...")
                await page.reload()
                await human_delay(2000, 4000)
                count_text = await cart_count_el.text_content(timeout=5000)
                if count_text and count_text.strip() == "0":
                    logger.error("[BUYER] ❌ El carrito sigue vacío. Abortando checkout.")
                    await _take_debug_screenshot(page, "empty_cart_error")
                    return False
        except Exception:
            pass

        # Buscar botón de checkout con múltiples fallbacks
        checkout_found = False
        for sel in [SEL["proceed_to_checkout"], SEL["checkout_alt"]]:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.scroll_into_view_if_needed()
                await human_delay(600, 1200)
                await btn.click()
                checkout_found = True
                break

        if not checkout_found:
            logger.error("[BUYER] ❌ No se encontró el botón para proceder al checkout.")
            await _take_debug_screenshot(page, "no_checkout_btn")
            return False

        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await human_delay(2000, 3500)
        logger.info("[BUYER]   ✅ En el checkout.")

        # Verificar si nos mandó al login (sesión expiró en mitad del flujo)
        if "ap/signin" in page.url:
            logger.warning("[BUYER] Sesión expirada durante checkout. Re-logueando...")
            if not await do_login(page):
                return False
            # Volver al carrito
            await page.goto(
                "https://www.amazon.com.mx/gp/cart/view.html",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            await human_delay(1500, 2500)

        # --- Paso 4: Dirección de entrega ---
        # Amazon puede mostrar esta pantalla o saltarla si ya hay una predeterminada
        try:
            deliver_btn = page.locator(SEL["deliver_here_btn"]).first
            if await deliver_btn.count() > 0 and await deliver_btn.is_visible(timeout=4000):
                await deliver_btn.scroll_into_view_if_needed()
                await human_delay(600, 1200)
                await deliver_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await human_delay(1500, 2500)
                logger.info("[BUYER]   ✅ Dirección de entrega seleccionada.")
        except Exception:
            logger.debug("[BUYER]   Pantalla de dirección no apareció (se usó la predeterminada).")

        # --- Paso 5: Seleccionar método de pago OXXO / Paga en efectivo ---
        logger.info("[BUYER]   Buscando opción OXXO / Efectivo en los métodos de pago...")
        oxxo_found = False

        try:
            all_radios = page.locator("input[type='radio']")
            total = await all_radios.count()
            logger.debug(f"[BUYER]   Radios encontrados en la página: {total}")

            for i in range(total):
                radio = all_radios.nth(i)
                # Subir hasta 5 niveles en el DOM para capturar el texto del bloque completo
                section_text = await radio.evaluate("""
                    node => {
                        let el = node;
                        for (let j = 0; j < 5; j++) {
                            if (!el.parentElement) break;
                            el = el.parentElement;
                        }
                        return el.innerText.toLowerCase();
                    }
                """)
                logger.debug(f"[BUYER]   Radio #{i+1} section: {section_text[:80]}")
                if "oxxo" in section_text or "efectivo" in section_text:
                    await radio.scroll_into_view_if_needed()
                    await human_delay(300, 700)
                    await radio.check(force=True)
                    oxxo_found = True
                    logger.info(f"[BUYER]   ✅ Efectivo/OXXO seleccionado via .check() (radio #{i+1}).")
                    break
        except Exception as e:
            logger.debug(f"[BUYER]   Error en búsqueda de radio: {e}")

        if not oxxo_found:
            logger.error("[BUYER] ❌ Método 'Paga en efectivo' no encontrado.")
            await _take_debug_screenshot(page, "oxxo_not_found")
            return False

        await human_delay(800, 1500)

        # --- Paso 5.5: Manejar Modal de 'Oxxo | Pay' (si aparece) ---
        try:
            await asyncio.sleep(3)
            popover = page.locator(".a-popover-modal, .a-popover-wrapper").first
            if await popover.count() > 0 and await popover.is_visible():
                radios = popover.locator("input[type='radio']")
                for i in range(await radios.count()):
                    radio = radios.nth(i)
                    row_text = await radio.evaluate("node => node.closest('div, li, tr').innerText.toLowerCase()")
                    if "oxxo" in row_text:
                        await radio.check(force=True)
                        logger.info("[BUYER]   ✅ OXXO seleccionado en modal.")
                        break
                
                confirm_modal = popover.locator("button:has-text('Continuar'), button:has-text('Seleccionar')").first
                if await confirm_modal.count() > 0:
                    await confirm_modal.click()
                    await human_delay(1000, 2000)
        except Exception as e:
            logger.debug(f"[BUYER] Error manejando modal: {e}")

        # Confirmar método de pago con 'Usar este método de pago'
        payment_confirmed = False
        try:
            # Estrategia 1: buscar por texto (más robusto)
            for btn_text in ["Usar este método de pago", "Usar este m\u00e9todo", "Use this payment"]:
                btn = page.get_by_role("button", name=btn_text, exact=False).first
                if await btn.count() == 0:
                    btn = page.get_by_text(btn_text, exact=False).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.scroll_into_view_if_needed()
                    await human_delay(500, 1000)
                    await btn.click()
                    payment_confirmed = True
                    logger.info(f"[BUYER]   ✅ Click en '{btn_text}'.")
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await human_delay(2000, 4000)
                    break
        except Exception as e:
            logger.debug(f"[BUYER] get_by_text pay btn: {e}")
        
        # Estrategia 2: selectores CSS
        if not payment_confirmed:
            try:
                pay_btn = page.locator(SEL["use_payment_btn"]).first
                if await pay_btn.count() > 0 and await pay_btn.is_visible(timeout=5000):
                    await pay_btn.scroll_into_view_if_needed()
                    await human_delay(500, 1000)
                    await pay_btn.click()
                    payment_confirmed = True
                    logger.info("[BUYER]   ✅ Click en botón de pago (CSS selector).")
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await human_delay(2000, 4000)
            except Exception:
                pass

        if not payment_confirmed:
            logger.warning("[BUYER]   ⚠️ Botón 'Usar este método de pago' no encontrado. Continuando de todas formas...")
            await _take_debug_screenshot(page, "pay_btn_not_found")

        # --- Paso 5.8: Rechazar oferta de Amazon Prime si aparece ---
        try:
            await human_delay(1500, 2500)
            for no_thanks_text in ["No gracias", "No, gracias", "Ahora no", "Skip"]:
                no_btn = page.get_by_role("button", name=no_thanks_text, exact=False).first
                if await no_btn.count() == 0:
                    no_btn = page.get_by_text(no_thanks_text, exact=False).first
                if await no_btn.count() > 0 and await no_btn.is_visible():
                    await no_btn.click()
                    logger.info(f"[BUYER]   ✅ Oferta Prime rechazada ('{no_thanks_text}').")
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await human_delay(1500, 2500)
                    break
        except Exception as e:
            logger.debug(f"[BUYER] Prime dismiss: {e}")

        # --- Paso 6: Revisar pedido ---
        logger.info("[BUYER]   En la pantalla de revisión del pedido...")
        await human_delay(2000, 3500)

        # Esperar a que el botón de confirmar esté disponible
        place_order = page.locator(SEL["place_order_btn"]).first
        try:
            await place_order.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeoutError:
            logger.warning("[BUYER] Botón 'Realizar pedido' no visible. Guardando screenshot...")
            await _take_debug_screenshot(page, "review_order")
            # Intentar continuar de todas formas
            if await place_order.count() == 0:
                logger.error("[BUYER] ❌ No se encontró el botón de confirmación final.")
                return False

        # --- Paso 7: Confirmar (o DRY-RUN) ---
        if confirm:
            logger.info("[BUYER] 🚨🚨🚨 MODO REAL — CONFIRMANDO PEDIDO CON OXXO...")
            await place_order.scroll_into_view_if_needed()
            await human_delay(1000, 2000)
            await place_order.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await human_delay(2000, 4000)
            logger.info("[BUYER] ✅✅✅ PEDIDO REALIZADO EXITOSAMENTE.")
            logger.info("[BUYER]    Revisa tu email para el código OXXO (válido por 3 días).")
            await _take_debug_screenshot(page, "order_confirmed")
            return True
        else:
            logger.info("[BUYER] 🟡 DRY-RUN activo — Bot llegó a la pantalla de confirmación.")
            logger.info("[BUYER]    Para comprar de verdad: cambia 'confirm_order': true en config.json")
            await _take_debug_screenshot(page, "dry_run_review")
            return True

    except PlaywrightTimeoutError as e:
        logger.error(f"[BUYER] ❌ Timeout durante el flujo de compra: {e}")
        await _take_debug_screenshot(page, "buy_timeout")
        return False
    except Exception as e:
        logger.error(f"[BUYER] ❌ Error inesperado durante la compra: {e}")
        await _take_debug_screenshot(page, "buy_unexpected_error")
        return False


# ---------------------------------------------------------------------------
# Screenshots de debug
# ---------------------------------------------------------------------------

async def _take_debug_screenshot(page: Page, name: str) -> None:
    """Guarda un screenshot completo para depuración."""
    try:
        path = BASE_DIR / f"debug_{name}_{int(time.time())}.png"
        await page.screenshot(path=str(path), full_page=True)
        logger.info(f"[DEBUG] Screenshot: {path.name}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Carga y valida config.json. Falla rápido si algo es inválido."""
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        logger.error(f"❌ No se encontró config.json en: {BASE_DIR}")
        sys.exit(1)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"❌ config.json tiene un error de formato JSON: {e}")
        sys.exit(1)

    # Validar estructura
    all_products = config.get("target_products", [])
    logger.info(f"[CONFIG] Productos totales encontrados en JSON: {len(all_products)}")
    
    enabled = [p for p in all_products if p.get("enabled", False) is True]
    logger.info(f"[CONFIG] Productos con 'enabled': true: {len(enabled)}")

    for p in enabled:
        url = p.get("url", "")
        # Amazon MX tiene formatos variados: /dp/ASIN o /nombre/dp/ASIN
        if "amazon.com.mx" not in url or "/dp/" not in url:
            logger.warning(
                f"[CONFIG] URL sospechosa (debe contener amazon.com.mx y /dp/): {url}"
            )
        if "XXXXXXXXXX" in url:
            logger.error(
                f"[CONFIG] ❌ '{p.get('name')}' tiene una URL de ejemplo. Reemplázala con la URL real."
            )
            sys.exit(1)

    config["target_products"] = enabled
    return config


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    global CHECK_INTERVAL
    config   = load_config()
    confirm  = config.get("checkout", {}).get("confirm_order", False)
    max_qty  = config.get("checkout", {}).get("max_quantity_per_product", 1)

    if not config.get("target_products"):
        logger.error("❌ No hay productos habilitados en config.json.")
        logger.error("   Agrega una URL real y pon 'enabled': true.")
        # sys.exit(1) - Removed to allow starting empty and adding via web dashboard

    logger.info("=" * 62)
    logger.info("  🤖 AMAZON AUTO-BUYER v2 — ASCEND HEROES")
    logger.info(f"  📦 Productos objetivo inicial: {len(config.get('target_products', []))}")
    logger.info(f"  ⏱️  Intervalo de chequeo base: ~{CHECK_INTERVAL}s (± jitter)")
    logger.info(f"  🔢 Máx. cantidad/prod.:  {max_qty}")
    logger.info(f"  🛒 Modo: {'🚨 COMPRA REAL (confirm_order=true)' if confirm else '🟡 DRY-RUN (sin confirmar)'}")
    logger.info("=" * 62)

    if confirm:
        logger.warning("⚠️  ATENCIÓN: El bot CONFIRMARÁ pedidos reales. Tienes 5 segundos para cancelar (Ctrl+C)...")
        await asyncio.sleep(5)

    # Cargar estado persistente de compras
    bought: set = load_bought()

    # User-Agent aleatorio por sesión
    user_agent = random.choice(USER_AGENTS)

    # Viewport con variación mínima (más humano)
    viewport = {
        "width":  random.choice([1280, 1366, 1440]),
        "height": random.choice([720, 768, 800]),
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-infobars",
                "--window-size=1280,720",
            ],
        )

        context = await browser.new_context(
            user_agent=user_agent,
            viewport=viewport,
            locale="es-MX",
            timezone_id="America/Mexico_City",  # Consistente con locale es-MX
            color_scheme="light",
            extra_http_headers={
                "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        # Aplicar stealth script completo a todas las páginas del contexto
        await context.add_init_script(STEALTH_SCRIPT)

        page = await context.new_page()

        # Intentar sesión guardada; si expiró, re-loguearse
        session_loaded = await load_session(context)
        if not session_loaded or not await is_logged_in(page):
            logger.info("[INIT] Se necesita login fresco...")
            if not await do_login(page):
                logger.error("❌ Login fallido. Abortando.")
                await browser.close()
                return
            await save_session(context)

        logger.info("\n✅ Sesión activa. Iniciando monitoreo...\n")

        cycle = 0
        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 5

        while True:
            cycle += 1
            logger.info(f"┌{'─'*56}┐")
            logger.info(f"│  CICLO #{cycle:04d} | {time.strftime('%H:%M:%S')}                            │")
            logger.info(f"└{'─'*56}┘")
            
            # --- HOT RELOAD CONFIG ---
            try:
                config = load_config()
                products = config.get("target_products", [])
                
                # Check interval from env dynamically
                if (BASE_DIR / ".env").exists():
                    env_lines = (BASE_DIR / ".env").read_text(encoding="utf-8").splitlines()
                    for line in env_lines:
                        if line.startswith("CHECK_INTERVAL_SECONDS="):
                            try:
                                CHECK_INTERVAL = int(line.split("=")[1].strip())
                            except:
                                pass
            except Exception as e:
                logger.error(f"[CONFIG] Error recargando configuración en caliente: {e}")
                products = []

            for product in products:
                name = product["name"]
                url  = product["url"]

                if url in bought:
                    logger.info(f"  ✅ Ya comprado (persistente): {name}")
                    continue

                logger.info(f"  🔍 Verificando stock: {name}")
                available = await is_product_available(page, url)

                if available:
                    logger.info(f"  🚨 ¡STOCK DETECTADO! → {name}")
                    consecutive_failures = 0

                    success = await buy_product(page, product, confirm=confirm)

                    if success:
                        bought.add(url)
                        save_bought(bought)  # Persistir a disco inmediatamente
                        logger.info(f"  🎉 Proceso completado para: {name}")
                    else:
                        consecutive_failures += 1
                        logger.error(f"  ❌ Falló el flujo de compra ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}).")

                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            logger.critical("🚨 Demasiados fallos consecutivos. Verificando sesión...")
                            if not await is_logged_in(page):
                                logger.warning("[RECOVERY] Re-logueando...")
                                if await do_login(page):
                                    await save_session(context)
                                    consecutive_failures = 0
                            else:
                                consecutive_failures = 0
                else:
                    logger.info(f"  ⏩ Sin stock: {name}")

                # Pausa humana entre productos
                await human_delay(2000, 5000)

            # Actualizar qué productos siguen activos
            active_products = [p for p in products if p["url"] not in bought]
            if not active_products:
                logger.info("\n⏳ Todos los productos han sido procesados o no hay productos configurados.")
                logger.info("El bot seguirá vivo esperando a que cambies algo en el Web Dashboard.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # Intervalo entre ciclos con jitter (evita patrón fijo detectable)
            jitter = random.randint(-10, 15)
            next_interval = max(20, CHECK_INTERVAL + jitter)
            logger.info(f"\n  ⏳ Próximo ciclo en {next_interval}s...\n")
            await asyncio.sleep(next_interval)

        await context.close()
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⛔ Bot detenido manualmente.")
        sys.exit(0)

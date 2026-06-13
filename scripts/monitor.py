"""
monitor.py — Componente 2: El Vigilante (Versión Stateful)
Gestiona la watchlist con persistencia de estado para evitar alertas duplicadas.
"""

import json
import logging
import time
import os
import sys
import random
import threading
from pathlib import Path
from typing import Optional, Callable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.scraper import ProductSnapshot, scrape
from core.discovery import discover_new_products

logger = logging.getLogger(__name__)

# Configuración
STATE_FILE = os.path.join(BASE_DIR, "data", "monitor_state.json")
DISCOVERY_EVERY_N_CYCLES = 3


def _is_amazon_mx_url(url: str) -> bool:
    """True si la URL apunta a Amazon MX o Amazon.com.mx."""
    url = (url or "").lower()
    return "amazon.com.mx" in url or "amazon.mx" in url


def load_products(products_path: str) -> list[dict]:
    try:
        with open(products_path, "r", encoding="utf-8") as f:
            all_products = json.load(f)

        active = [p for p in all_products if p.get("active", True)]
        return active
    except Exception as e:
        logger.error(f"[MONITOR] Error leyendo products.json: {e}")
        return []


def _parse_price(price_text: str) -> Optional[float]:
    """Convierte '$349.99 MXN' en un número flotante."""
    if not price_text:
        return None
    digits = ''.join(ch for ch in price_text if ch.isdigit() or ch in '.-')
    try:
        return float(digits)
    except ValueError:
        return None


def should_alert_for_product(product: dict, snapshot: ProductSnapshot) -> tuple[bool, str]:
    """Aplica filtros de precio y vendedor antes de disparar alertas.
    
    REGLA CLAVE: Solo se alerta si Amazon México está disponible como vendedor.
    - Si el vendedor principal ES Amazon México → OK.
    - Si hay múltiples vendedores pero Amazon MX está en ofertas → OK (amazon_present=True).
    - Si el vendedor es tercero y Amazon MX no aparece en ofertas → FILTRADO.
    """
    max_price = product.get("max_price")
    if max_price is not None:
        current_price = _parse_price(snapshot.price)
        if current_price is not None and current_price > float(max_price):
            return False, f"max_price={max_price} (precio actual {snapshot.price})"

    # Verificar que Amazon México esté disponible como opción de compra
    seller = (snapshot.seller or "").strip()
    is_amazon_mx = seller == "Amazon México" or snapshot.amazon_present

    if not is_amazon_mx:
        return False, f"Amazon MX no disponible (vendedor: {seller or 'Desconocido'})"

    return True, "ok"


def load_state() -> dict:
    """Carga el último estado guardado de los productos."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Reconstruir objetos ProductSnapshot desde dicts
                snapshots = {}
                for url, s in data.items():
                    snapshots[url] = ProductSnapshot(**s)
                return snapshots
        except Exception as e:
            logger.warning(f"[MONITOR] No se pudo cargar el estado previo: {e}")
            return {}
    return {}


def save_state(snapshots: dict[str, ProductSnapshot]):
    """Guarda el estado actual de los productos."""
    try:
        # Convertir objetos a dicts para serializar
        data = {url: vars(s) for url, s in snapshots.items() if s}
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MONITOR] Error guardando estado: {e}")


def detect_change(
    prev: Optional[ProductSnapshot],
    curr: ProductSnapshot,
) -> Optional[str]:
    """
    Compara estados y retorna el tipo de alerta si hay un cambio relevante.
    """
    if curr.captcha_detected or curr.error:
        return None

    # Caso 1: Primera vez que vemos este producto
    if prev is None:
        if curr.in_stock:
            return "stock_available"
        return None

    # Caso 2: Se AGOTÓ (In Stock -> Out of Stock)
    # Se desactiva para evitar alertas de agotado cuando Amazon MX no publica la oferta real.
    if prev.in_stock and not curr.in_stock:
        return None

    # Caso 3: REABASTECIMIENTO (Out of Stock -> In Stock)
    if not prev.in_stock and curr.in_stock:
        return "restock"

    # Caso 4: CAMBIO DE PRECIO (Si ambos tienen precio válido)
    def clean_price(p):
        try:
            return float(''.join(c for c in p if c.isdigit() or c == '.'))
        except:
            return None

    p_prev = clean_price(prev.price)
    p_curr = clean_price(curr.price)

    if p_prev and p_curr and abs(p_prev - p_curr) > 1.0: # Cambio > $1 MXN
        return "price_change"

    # Caso 5: PREVENTA (Detectada ahora)
    if curr.is_preorder and (prev is None or not prev.is_preorder):
        return "preorder"

    return None


def run_monitor(
    products_path: str,
    interval_seconds: int,
    on_change_callback: Callable,
    file_lock: threading.Lock = None,
    run_discovery: bool = True
) -> None:
    """Bucle principal stateful con logs refinados."""
    # Cargar datos iniciales para el resumen
    from core.discovery import DISCOVERED_FILE
    def get_discovery_count():
        if os.path.exists(DISCOVERED_FILE):
            try:
                with open(DISCOVERED_FILE, "r") as f: return len(json.load(f))
            except: return 0
        return 0

    products = load_products(products_path)
    snapshots = load_state()
    
    logger.info(f"{'='*60}")
    logger.info(f"🚀 POKÉMON MONITOR PROFESIONAL")
    logger.info(f"{'='*60}")
    logger.info(f"📋 Watchlist:     {len(products)} productos activos")
    logger.info(f"🧠 Memoria:       {len(snapshots)} estados guardados")
    logger.info(f"🔎 Historial:     {get_discovery_count()} ASINs descubiertos")
    logger.info(f"⏱️  Intervalo:     {interval_seconds}s")
    logger.info(f"📡 Discovery:     Cada {DISCOVERY_EVERY_N_CYCLES} ciclos")
    logger.info(f"{'='*60}\n")
    
    cycle = 0
    captcha_cooldown_until = 0

    while True:
        cycle += 1
        now_ts = time.time()
        now_str = time.strftime('%H:%M:%S')
        
        # Enfriamiento si Amazon bloqueó
        if now_ts < captcha_cooldown_until:
            wait_rem = int(captcha_cooldown_until - now_ts)
            logger.warning(f"🛑 [PAUSA CAPTCHA] Amazon bloqueado. Enfriando por {wait_rem}s...")
            time.sleep(60)
            continue

        discovery_in = DISCOVERY_EVERY_N_CYCLES - (cycle % DISCOVERY_EVERY_N_CYCLES)
        discovery_status = "✨ DISCOVERY ACTIVO" if cycle % DISCOVERY_EVERY_N_CYCLES == 0 else f"📡 {discovery_in} ciclo(s) p/ Discovery"

        logger.info(f"┌{'─'*58}┐")
        logger.info(f"│ CICLO #{cycle:03} | {now_str} | {discovery_status.center(28)} │")
        logger.info(f"└{'─'*58}┘")

        # Cargar productos con seguridad
        if file_lock:
            with file_lock: current_products = load_products(products_path)
        else:
            current_products = load_products(products_path)
        
        for product in current_products:
            name = product.get("name", "Desconocido")
            url = product.get("url", "")
            if not url: continue
            
            logger.info(f"  🔍 Watchlist: {name}")
            curr = scrape(name, url)
            
            if curr.captcha_detected:
                logger.warning(f"⚠️  CAPTCHA detectado en '{name}'. Pausando todo el monitor 5 min.")
                captcha_cooldown_until = time.time() + 300 # 5 min
                break # Salir de la lista de productos para este ciclo

            if curr.error:
                logger.error(f"  ❌ Error en '{name}': {curr.error}")
                continue
            
            status_icon = "✅" if curr.in_stock else "❌"
            preorder_tag = " [PREVENTA]" if curr.is_preorder else ""
            logger.info(f"  {status_icon}{preorder_tag} {curr.availability_text} | {curr.price} | Vendedor: {curr.seller}")
            if len(curr.sellers) > 1:
                logger.info(f"    👥 Otros vendedores: {', '.join(curr.sellers[1:5])}")

            should_alert, filter_reason = should_alert_for_product(product, curr)
            if not should_alert:
                logger.info(f"  ⏭️  Filtro de control: {filter_reason}")
            else:
                prev = snapshots.get(url)
                change = detect_change(prev, curr)
                if change:
                    logger.info(f"  🚨 CAMBIO DETECTADO: {change}")
                    on_change_callback(curr, change)

            # Guardar con seguridad
            snapshots[url] = curr
            if file_lock:
                with file_lock: save_state(snapshots)
            else:
                save_state(snapshots)

            time.sleep(random.uniform(3, 6))

        # Fase Discovery (sólo si no está deshabilitada)
        if run_discovery and cycle % DISCOVERY_EVERY_N_CYCLES == 0:
            logger.info(f"\n  🕵️  Iniciando Discovery (ciclo {cycle})...")
            try:
                new_items = discover_new_products()
                for snap, change_type in new_items:
                    on_change_callback(snap, change_type)
            except Exception as e:
                logger.error(f"  [DISCOVERY] Error: {e}")

        # Determinar intervalo (soporta int o callable)
        current_sleep = interval_seconds() if callable(interval_seconds) else interval_seconds
        logger.info(f"\n  ⏳ Próxima revisión en {current_sleep}s...\n")
        time.sleep(current_sleep)

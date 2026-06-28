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
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.scraper import ProductSnapshot, scrape
from core.discovery import discover_new_products

logger = logging.getLogger(__name__)

# Configuración
STATE_FILE = os.path.join(BASE_DIR, "data", "monitor_state.json")
STATS_FILE = os.path.join(BASE_DIR, "data", "stats.json")
DISCOVERY_EVERY_N_CYCLES = 3

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {"total_bytes_vps": 0, "total_bytes_proxy": 0, "captchas_hit": 0, "start_time": time.time()}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
            if "start_time" not in st: st["start_time"] = time.time()
            return st
    except:
        return {"total_bytes_vps": 0, "total_bytes_proxy": 0, "captchas_hit": 0, "start_time": time.time()}

def save_stats(stats):
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
    except:
        pass

global_stats = load_stats()

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


def should_alert_for_product(product: dict, snapshot: ProductSnapshot, amazon_only: bool = True) -> tuple[bool, str]:
    """Aplica filtros de precio y vendedor antes de disparar alertas."""
    max_price = product.get("max_price")
    price_ok = False
    
    if max_price is not None:
        current_price = _parse_price(snapshot.price)
        if current_price is not None:
            if current_price > float(max_price):
                return False, f"precio excedido ({current_price} > {max_price})"
            else:
                price_ok = True

    if amazon_only:
        # Verificar que Amazon México esté disponible como opción de compra
        seller = (snapshot.seller or "").strip()
        is_amazon_mx = seller == "Amazon México" or snapshot.amazon_present

        # Si no es Amazon MX, validar si el precio es bueno como fallback (doble verificación)
        if not is_amazon_mx and not price_ok:
            return False, f"Amazon MX no disponible y precio no validado (vendedor: {seller or 'Desconocido'})"

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

    # Caso 3: SALIDA DE PREVENTA A STOCK (tiene prioridad sobre restock genérico)
    if prev.is_preorder and curr.in_stock and not curr.is_preorder:
        return "released"

    # Caso 4: REABASTECIMIENTO (Out of Stock -> In Stock)
    if not prev.in_stock and curr.in_stock:
        return "restock"

    # Caso 5: CAMBIO DE PRECIO (Si ambos tienen precio válido)
    def clean_price(p):
        try:
            return float(''.join(c for c in p if c.isdigit() or c == '.'))
        except:
            return None

    p_prev = clean_price(prev.price)
    p_curr = clean_price(curr.price)

    if p_prev and p_curr and abs(p_prev - p_curr) > 1.0: # Cambio > $1 MXN
        return "price_change"

    # Caso 6: PREVENTA (Detectada ahora por primera vez)
    if curr.is_preorder and not prev.is_preorder:
        return "preorder"

def run_monitor(
    products_path: str,
    interval_seconds: int,
    on_change_callback: Callable,
    file_lock: threading.Lock = None,
    run_discovery: bool = True
) -> None:
    """Bucle principal paralelo con ThreadPoolExecutor."""
    from core.discovery import DISCOVERED_FILE
    def get_discovery_count():
        if os.path.exists(DISCOVERED_FILE):
            try:
                with open(DISCOVERED_FILE, "r") as f: return len(json.load(f))
            except: return 0
        return 0

    products = load_products(products_path)
    snapshots = load_state()
    state_lock = threading.Lock() # Bloqueo para snapshots y save_state
    
    logger.info(f"{'='*60}")
    logger.info(f"🚀 POKÉMON MONITOR PARALELO (Turbo)")
    logger.info(f"{'='*60}")
    logger.info(f"📋 Watchlist:     {len(products)} productos activos")
    logger.info(f"🧠 Memoria:       {len(snapshots)} estados guardados")
    logger.info(f"🔎 Historial:     {get_discovery_count()} ASINs descubiertos")
    logger.info(f"⏱️  Intervalo:     {interval_seconds if not callable(interval_seconds) else interval_seconds()}s")
    logger.info(f"📡 Discovery:     Cada {DISCOVERY_EVERY_N_CYCLES} ciclos")
    logger.info(f"{'='*60}\n")
    
    cycle = 0
    captcha_cooldown_until = 0
    cycle_bytes = [0]

    def process_product(product, cycle_snapshots, config):
        nonlocal captcha_cooldown_until
        if time.time() < captcha_cooldown_until:
             return

        # Aplicar Jitter para evitar ráfagas de peticiones desde el mismo IP
        min_j = config.get("min_jitter", 1)
        max_j = config.get("max_jitter", 3)
        time.sleep(random.uniform(min_j, max_j))

        name = product.get("name", "Desconocido")
        url = product.get("url", "")
        if not url: return
        
        # Pasar preferencia de Amazon Only al scraper para optimizar peticiones
        amazon_only = config.get("amazon_only", False)
        curr = scrape(name, url, amazon_only=amazon_only)
        
        if curr.captcha_detected:
            with state_lock:
                logger.warning(f"⚠️  CAPTCHA detectado en '{name}'. Pausando monitor 1 min.")
                captcha_cooldown_until = time.time() + 60 
            return

        if curr.error:
            logger.error(f"  ❌ Error en '{name}': {curr.error}")
            return
        
        # Logs de estado
        status_icon = "✅" if curr.in_stock else "❌"
        preorder_tag = " [PREVENTA]" if curr.is_preorder else ""
        logger.info(f"  {status_icon}{preorder_tag} {curr.availability_text} | {curr.price} | Vendedor: {curr.seller} | {name}")
        
        if len(curr.sellers) > 1:
            logger.info(f"    👥 Otros vendedores ({name}): {', '.join(curr.sellers[1:5])}")

        should_alert, filter_reason = should_alert_for_product(product, curr, amazon_only=amazon_only)
        
        with state_lock:
            prev = cycle_snapshots.get(url)
            vps_b = getattr(curr, 'bytes_vps', 0)
            prox_b = getattr(curr, 'bytes_proxy', 0)
            blocked = getattr(curr, 'vps_blocked', False)
            
            # (Deprecado backward compat)
            cycle_bytes[0] += vps_b + prox_b + getattr(curr, 'bytes_downloaded', 0)
            
            global_stats["total_bytes_vps"] = global_stats.get("total_bytes_vps", 0) + vps_b
            global_stats["total_bytes_proxy"] = global_stats.get("total_bytes_proxy", 0) + prox_b
            if blocked:
                global_stats["captchas_hit"] = global_stats.get("captchas_hit", 0) + 1
            if should_alert:
                change = detect_change(prev, curr)
                if change:
                    logger.info(f"  🚨 ALERTA INSTANTÁNEA ({name}): {change}")
                    on_change_callback(curr, change)
            else:
                # Opcional: log de por qué se filtró si estaba en stock
                if curr.in_stock:
                    logger.info(f"    [FILTRADO] {name}: {filter_reason}")
            
            # Guardar estado hilos-seguro
            cycle_snapshots[url] = curr
            if file_lock:
                with file_lock: 
                    save_state(cycle_snapshots)
                    save_stats(global_stats)
            else:
                save_state(cycle_snapshots)
                save_stats(global_stats)

    while True:
        cycle += 1
        cycle_bytes[0] = 0
        now_ts = time.time()
        now_str = time.strftime('%H:%M:%S')
        
        if now_ts < captcha_cooldown_until:
            wait_rem = int(captcha_cooldown_until - now_ts)
            logger.warning(f"🛑 [CAPTCHA] Amazon bloqueado. Esperando {wait_rem}s...")
            time.sleep(30)
            continue

        discovery_in = DISCOVERY_EVERY_N_CYCLES - (cycle % DISCOVERY_EVERY_N_CYCLES)
        discovery_status = "✨ DISCOVERY ACTIVO" if cycle % DISCOVERY_EVERY_N_CYCLES == 0 else f"📡 {discovery_in} ciclo(s)"

        logger.info(f"┌{'─'*58}┐")
        logger.info(f"│ CICLO #{cycle:03} | {now_str} | {discovery_status.center(28)} │")
        logger.info(f"└{'─'*58}┘")

        # Recargar productos
        if file_lock:
            with file_lock: products = load_products(products_path)
        else:
            products = load_products(products_path)
        
        # Cargar configuración operativa dinámicamente
        from core.admin_hub import get_notif_config
        OP_CONFIG_PATH = os.path.join(BASE_DIR, "data", "operational_config.json")
        op_cfg = get_notif_config(OP_CONFIG_PATH)

        # DISPARAR HILOS EN PARALELO
        # Usamos un pool reducido (2 hilos) para no saturar a Amazon desde un mismo IP
        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.map(lambda p: process_product(p, snapshots, op_cfg), products)

        # Fase Discovery (secuencial al final del ciclo)
        if run_discovery and cycle % DISCOVERY_EVERY_N_CYCLES == 0:
            logger.info(f"\n  🕵️  Iniciando Discovery (ciclo {cycle})...")
            try:
                new_items = discover_new_products()
                for snap, change_type in new_items:
                    on_change_callback(snap, change_type)
            except Exception as e:
                logger.error(f"  [DISCOVERY] Error: {e}")

        current_sleep = interval_seconds() if callable(interval_seconds) else interval_seconds
        
        total_kb = cycle_bytes[0] / 1024
        if total_kb > 1024:
            bw_str = f"{total_kb / 1024:.2f} MB"
        else:
            bw_str = f"{total_kb:.2f} KB"
            
        logger.info(f"\n  📊 Ancho de banda del ciclo: {bw_str}")
        logger.info(f"  ⏳ Ciclo completado. Próxima revisión en {current_sleep}s...\n")
        time.sleep(current_sleep)

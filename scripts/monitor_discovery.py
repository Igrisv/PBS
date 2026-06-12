"""
monitor_discovery.py — Ejecutable: Escáner de Nuevas Publicaciones
Escanea categorías de Amazon MX buscando productos recién publicados (< 20 minutos).
Ejecutar con: python monitor_discovery.py
"""

import logging
import os
import sys
import time
import random
from pathlib import Path

# --- Encoding UTF-8 en Windows ---
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from web.auth import require_license
from core.notifier_telegram import send_telegram_alert
from core.notifier import send_whatsapp_alert
from core.scraper import ProductSnapshot
from core.utils import get_file_lock

# --- Inter-process Lock ---
GLOBAL_LOCK_FILE = os.path.join(BASE_DIR, "data", "app.lock")
file_lock = get_file_lock(GLOBAL_LOCK_FILE)

# Validar licencia
require_license(feature="discovery")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "monitor_discovery.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# --- Config ---
# --- Config operativa (intervalos) sigue de .env ---
DISCOVERY_INTERVAL   = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "20"))
FRESH_WINDOW_MINUTES = int(os.getenv("FRESH_WINDOW_MINUTES", "20"))
WARMUP_PERIOD_MINUTES = int(os.getenv("WARMUP_PERIOD_MINUTES", "0"))

from core.admin_hub import get_notif_config
NOTIF_CONFIG = os.path.join(BASE_DIR, "data", "notif_config.json")

BOT_START_TIME = time.time()
ALERT_CACHE: set = set()


def send_new_item_alert(snapshot: ProductSnapshot, change_type: str = "new_launch") -> None:
    global ALERT_CACHE
    alert_key = f"{snapshot.url}_{change_type}"
    if alert_key in ALERT_CACHE:
        logger.info(f"  [DUP] Alerta ya enviada ({change_type}): {snapshot.name}")
        return
    ALERT_CACHE.add(alert_key)
    if len(ALERT_CACHE) > 500:
        ALERT_CACHE.clear()

    # ── Cargar config dinámica desde JSON (permite cambios sin reiniciar) ──
    cfg = get_notif_config(NOTIF_CONFIG)

    # ── Telegram ──
    tg = cfg.get("telegram", {})
    if tg.get("bot_token") and tg.get("chat_id"):
        try:
            send_telegram_alert(
                bot_token=tg["bot_token"], chat_id=tg["chat_id"],
                product_name=snapshot.title, product_url=snapshot.url,
                availability_text=snapshot.availability_text,
                price=snapshot.price, change_type=change_type
            )
        except Exception as e:
            logger.error(f"  [TELEGRAM] {e}")

    # ── WhatsApp Bridge (lee del JSON, no del .env) ──
    wb = cfg.get("whatsapp_bridge", {})
    bridge_url       = wb.get("bridge_url", "")
    community_id     = wb.get("community_id", "")

    if bridge_url and community_id:
        # Warmup check
        elapsed_min = (time.time() - BOT_START_TIME) / 60
        if elapsed_min < WARMUP_PERIOD_MINUTES:
            logger.info(f"  [WARMUP] Periodo activo ({elapsed_min:.1f}/{WARMUP_PERIOD_MINUTES} min). Silenciando Bridge.")
            return

        from core.notifier import send_whatsapp_bridge_alert
        try:
            send_whatsapp_bridge_alert(
                bridge_url=bridge_url,
                chat_id=community_id,
                product_name=snapshot.title,
                product_url=snapshot.url,
                availability_text=snapshot.availability_text,
                price=snapshot.price,
                change_type=change_type,
                image_url=snapshot.image_url
            )
        except Exception as e:
            logger.error(f"  [BRIDGE] {e}")

    # ── WhatsApp Meta Cloud API ──
    wm = cfg.get("whatsapp_meta", {})
    if wm.get("access_token") and wm.get("phone_number_id") and wm.get("to_number"):
        try:
            send_whatsapp_alert(
                access_token=wm["access_token"], phone_number_id=wm["phone_number_id"],
                to_number=wm["to_number"], product_name=snapshot.title,
                product_url=snapshot.url, availability_text=snapshot.availability_text,
                price=snapshot.price, change_type=change_type,
                template_name=wm.get("template_name", "inventory_update"),
                language_code=wm.get("language_code", "es_MX"),
            )
        except Exception as e:
            logger.error(f"  [META] {e}")


def main():
    # Importar discovery aquí para separación total de contexto de ejecución
    from core.discovery import discover_new_products, _load_discovered, DISCOVERED_FILE

    logger.info("=" * 60)
    logger.info("  ✨ POKÉMON MONITOR — MODO DESCUBRIMIENTO")
    logger.info(f"  🔎 Ventana frescos: {FRESH_WINDOW_MINUTES} minutos")
    logger.info(f"  ⏱️  Intervalo ciclo: {DISCOVERY_INTERVAL}s")
    logger.info("=" * 60)

    cycle = 0
    captcha_cooldown_until = 0

    while True:
        # ── Cargar config operativa en cada ciclo para permitir cambios en caliente ──
        op_cfg = get_notif_config(os.path.join(BASE_DIR, "data", "operational_config.json"))
        interval = op_cfg.get("discovery_interval", 20)
        fresh_win = op_cfg.get("fresh_window_minutes", 20)
        warmup_min = op_cfg.get("warmup_period_minutes", 0)

        cycle += 1
        now_ts = time.time()

        # Cooldown de CAPTCHA
        if now_ts < captcha_cooldown_until:
            wait = int(captcha_cooldown_until - now_ts)
            logger.warning(f"🛑 [PAUSA CAPTCHA] Enfriando {wait}s...")
            time.sleep(60)
            continue

        logger.info(f"┌{'─'*56}┐")
        logger.info(f"│ DISCOVERY #{cycle:03} | {time.strftime('%H:%M:%S')}                           │")
        logger.info(f"└{'─'*56}┘")

        try:
            with file_lock:
                new_items = discover_new_products()  # Lista de (snapshot, change_type)
                discovered_data = _load_discovered()
                fresh_items = []
                cutoff = now_ts - (FRESH_WINDOW_MINUTES * 60)

                for item_tuple in new_items:
                    # discover_new_products() retorna (snapshot, change_type)
                    if isinstance(item_tuple, tuple):
                        item, change_type = item_tuple
                    else:
                        item, change_type = item_tuple, "new_launch"  # fallback

                    asin = item.url.split("/dp/")[-1].split("?")[0].strip()
                    entry = discovered_data.get(asin, {})
                    first_seen = entry.get("first_seen", 0)

                    # Pre-orders siempre pasan la ventana de tiempo
                    if first_seen >= cutoff or change_type == "preorder":
                        age_min = max(0, (now_ts - first_seen) / 60)
                        icon = "🔔" if change_type == "preorder" else "🆕"
                        logger.info(f"  {icon} [{change_type}] [{age_min:.1f} min] {item.title[:55]}")
                        fresh_items.append((item, change_type))
                    else:
                        logger.info(f"  ⏩ Fuera de ventana: {item.title[:55]}")

                for item, change_type in fresh_items:
                    send_new_item_alert(item, change_type=change_type)

                if not fresh_items:
                    logger.info("  ℹ️  Sin lanzamientos frescos en este ciclo.")

            # Detectar CAPTCHA generalizado
            all_snaps = [t[0] if isinstance(t, tuple) else t for t in new_items]
            if any(i.captcha_detected for i in all_snaps):
                logger.warning("⚠️  CAPTCHA detectado en discovery. Pausando 5 min...")
                captcha_cooldown_until = now_ts + 300

        except Exception as e:
            logger.error(f"  [DISCOVERY ERROR] {e}")


        logger.info(f"\n  ⏳ Próximo ciclo en {interval}s...\n")
        time.sleep(interval)


if __name__ == "__main__":
    main()

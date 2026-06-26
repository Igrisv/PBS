"""
monitor_restock.py — Ejecutable: Vigilante de Stock y Precios
Monitorea los productos de products.json en busca de restock, sold_out y cambios de precio.
Ejecutar con: python monitor_restock.py
"""

import logging
import os
import sys
import threading
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
from web.web_server import start_web_server
from core.utils import get_file_lock

from core.admin_hub import get_notif_config
NOTIF_CONFIG = os.path.join(BASE_DIR, "data", "notif_config.json")

# Validar licencia ANTES de iniciar
require_license(feature="restock")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "monitor_restock.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# --- Config ---
PRODUCTS_PATH   = os.path.join(BASE_DIR, "data", "products.json")
CHECK_INTERVAL  = int(os.getenv("CHECK_INTERVAL_SECONDS", "20"))
TG_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")
WHATSAPP_TOKEN  = os.getenv("META_WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("META_PHONE_NUMBER_ID", "")
WHATSAPP_TO     = os.getenv("WHATSAPP_TO_NUMBER", "")
TEMPLATE_NAME   = os.getenv("META_TEMPLATE_NAME", "inventory_update")
LANGUAGE_CODE   = os.getenv("META_LANGUAGE_CODE", "es_MX")

# WhatsApp Bridge (Comunidad)
WHATSAPP_BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "")
WHATSAPP_COMMUNITY  = os.getenv("WHATSAPP_COMMUNITY_ID", "")
WHATSAPP_ASCENDED_HEROES_CHANNEL = os.getenv("WHATSAPP_ASCENDED_HEROES_CHANNEL", "")
WEB_HOST        = os.getenv("DASHBOARD_HOST", "65.75.203.137")
WEB_PORT        = int(os.getenv("DASHBOARD_PORT", "5000"))

GLOBAL_LOCK_FILE = os.path.join(BASE_DIR, "data", "app.lock")
file_lock = get_file_lock(GLOBAL_LOCK_FILE)
ALERT_CACHE: dict = {}
ALERT_TTL_SECONDS = 86400 * 3 # 3 días


def on_change(snapshot: ProductSnapshot, change_type: str) -> None:
    global ALERT_CACHE
    import time
    now = time.time()
    
    # Limpieza basada en TTL
    keys_to_delete = [k for k, ts in ALERT_CACHE.items() if now - ts > ALERT_TTL_SECONDS]
    for k in keys_to_delete:
        del ALERT_CACHE[k]

    alert_key = f"{snapshot.url}_{change_type}_{snapshot.price}"
    if alert_key in ALERT_CACHE:
        logger.info(f"  [DUP] Alerta ya enviada: {snapshot.name}")
        return
    ALERT_CACHE[alert_key] = now

    logger.info(f"[ALERTA] {change_type.upper()} → {snapshot.name}")

    # Cargar config dinámica cada vez para permitir cambios sin reiniciar
    cfg = get_notif_config(NOTIF_CONFIG)
    
    # Telegram
    tg = cfg.get("telegram", {})
    if tg.get("bot_token") and tg.get("chat_id"):
        try:
            send_telegram_alert(
                bot_token=tg["bot_token"], chat_id=tg["chat_id"],
                product_name=snapshot.name, product_url=snapshot.url,
                availability_text=snapshot.availability_text,
                price=snapshot.price, change_type=change_type,
                seller=snapshot.seller
            )
        except Exception as e:
            logger.error(f"  [TELEGRAM] {e}")

    # WhatsApp Bridge (Comunidad)
    wb = cfg.get("whatsapp_bridge", {})
    if wb.get("bridge_url") and wb.get("community_id"):
        from core.notifier import send_whatsapp_bridge_alert
        try:
            send_whatsapp_bridge_alert(
                bridge_url=wb["bridge_url"],
                chat_id=wb["community_id"],
                product_name=snapshot.name,
                product_url=snapshot.url,
                availability_text=snapshot.availability_text,
                price=snapshot.price,
                change_type=change_type,
                image_url=snapshot.image_url,
                seller=snapshot.seller
            )
        except Exception as e:
            logger.error(f"  [BRIDGE] {e}")

    # WhatsApp Meta Cloud API
    wm = cfg.get("whatsapp_meta", {})
    if wm.get("access_token") and wm.get("phone_number_id") and wm.get("to_number"):
        try:
            send_whatsapp_alert(
                access_token=wm["access_token"], phone_number_id=wm["phone_number_id"],
                to_number=wm["to_number"], product_name=snapshot.name,
                product_url=snapshot.url, availability_text=snapshot.availability_text,
                price=snapshot.price, change_type=change_type,
                template_name=wm.get("template_name", "inventory_update"),
                language_code=wm.get("language_code", "es_MX"),
                seller=snapshot.seller
            )
        except Exception as e:
            logger.error(f"  [WHATSAPP] {e}")


def main():
    logger.info("=" * 60)
    logger.info("  🔄 POKÉMON MONITOR — MODO RESTOCK/PRECIOS")
    logger.info(f"  📦 Watchlist: {PRODUCTS_PATH}")
    logger.info(f"  ⏱️  Intervalo: {CHECK_INTERVAL}s")
    logger.info("=" * 60)

    # El dashboard web ahora corre en un proceso independiente (pbs-web) vía PM2.
    logger.info("🌐 Dashboard: Gestionado por pm2 (pbs-web)")

    # Importar AQUÍ para que solo este ejecutable cargue el monitor de restock
    from scripts.monitor import run_monitor
    try:
        # Wrapper para recargar el intervalo dinámicamente
        def get_current_interval():
            op_cfg = get_notif_config(os.path.join(BASE_DIR, "data", "operational_config.json"))
            return op_cfg.get("restock_interval", 20)

        run_monitor(
            products_path=PRODUCTS_PATH,
            interval_seconds=get_current_interval, # Pasar función para permitir lectura dinámica si el monitor lo soporta
            on_change_callback=on_change,
            file_lock=file_lock,
            run_discovery=False,
        )
    except KeyboardInterrupt:
        logger.info("\n👋 Monitor Restock detenido (Ctrl+C). ¡Hasta pronto!")


if __name__ == "__main__":
    main()

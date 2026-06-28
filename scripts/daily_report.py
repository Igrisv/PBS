#!/usr/bin/env python3
import json
import os
import sys
import logging
from pathlib import Path
import time

# Agregar directorio base a sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from core.admin_hub import broadcast_admin_message

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger("daily_report")

def get_json_data(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def main():
    logger.info("Generando reporte diario...")

def generate_report_message():
    products_path = BASE_DIR / "data" / "products.json"
    stats_path = BASE_DIR / "data" / "stats.json"

    # 1. Analizar productos
    products = get_json_data(products_path)
    if isinstance(products, dict):
        products = []
        
    total_products = len(products)
    active_products = sum(1 for p in products if p.get("active", True))

    # 2. Analizar métricas de red
    stats = get_json_data(stats_path)
    vps_bytes = stats.get("total_bytes_vps", 0)
    proxy_bytes = stats.get("total_bytes_proxy", 0)
    captchas = stats.get("captchas_hit", 0)
    start_time = stats.get("start_time", time.time())

    vps_mb = vps_bytes / (1024 * 1024)
    proxy_mb = proxy_bytes / (1024 * 1024)
    
    # Calcular Uptime aproximado (desde que se creó/reinició stats.json)
    uptime_seconds = time.time() - start_time
    uptime_hours = uptime_seconds / 3600

    # 3. Formatear mensaje
    return (
        "✅ <b>El servidor está en línea y trabajando.</b>\n\n"
        f"📦 <b>Productos en Watchlist:</b> {total_products} ({active_products} activos)\n"
        f"⏱️ <b>Uptime:</b> {uptime_hours:.1f} horas sin interrupción\n\n"
        "🌐 <b>Rendimiento de Red:</b>:\n"
        f"  • Datos Servidor: <b>{vps_mb:.2f} MB</b>\n"
        f"  • Datos Proxy: <b>{proxy_mb:.2f} MB</b>\n"
        f"  • Bloqueos / CAPTCHAs evadidos: <b>{captchas}</b>\n\n"
        "Bot operativo."
    )

def main():
    logger.info("Generando reporte diario...")
    
    notif_config_path = BASE_DIR / "data" / "notif_config.json"
    msg = generate_report_message()

    # 4. Enviar mediante Admin Hub
    results = broadcast_admin_message(str(notif_config_path), msg)
    
    if results:
        logger.info(f"Reporte enviado exitosamente. Resultados: {results}")
    else:
        logger.warning("No se pudo enviar el reporte. Revisa la configuración de notificaciones.")

if __name__ == "__main__":
    main()

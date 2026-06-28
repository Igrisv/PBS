#!/usr/bin/env python3
"""
notice_scheduler.py
Script en segundo plano que revisa periódicamente los avisos programados
y los envía a la hora correcta según el huso horario de México.
"""
import json
import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
try:
    import zoneinfo
except ImportError:
    # Si usamos < 3.9
    pass

# Agregar directorio base a sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from core.admin_hub import broadcast_admin_message
from scripts.daily_report import generate_report_message

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("notice_scheduler")

NOTICES_FILE = BASE_DIR / "data" / "scheduled_notices.json"
NOTIF_CONFIG = BASE_DIR / "data" / "notif_config.json"

try:
    TZ = zoneinfo.ZoneInfo("America/Mexico_City")
except Exception:
    # Fallback si no hay zoneinfo, asume que el server está en hora local correcta o usa pytz si se instala
    import pytz
    TZ = pytz.timezone("America/Mexico_City")

# Estado en memoria para no repetir envíos en el mismo día
# Formato: {"14:30_message_hash": "2023-10-25"}
sent_history = {}

def get_notices():
    if not NOTICES_FILE.exists():
        return []
    try:
        with open(NOTICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Error reading notices: {e}")
        return []

def main():
    logger.info("Notice Scheduler iniciado. Verificando cada minuto...")
    
    while True:
        try:
            now = datetime.now(TZ)
            current_time_str = now.strftime("%H:%M")
            current_date_str = now.strftime("%Y-%m-%d")
            
            notices = get_notices()
            
            for notice in notices:
                if not notice.get("enabled", True):
                    continue
                
                target_time = notice.get("time")
                if target_time == current_time_str:
                    msg = notice.get("message", "")
                    channels = notice.get("channels", [])
                    
                    if "{REPORTE_DIARIO}" in msg:
                        try:
                            msg = msg.replace("{REPORTE_DIARIO}", generate_report_message())
                        except Exception as e:
                            logger.error(f"Error generating daily report: {e}")
                    
                    # Generar un hash o id único para el mensaje original y la hora
                    notice_id = f"{target_time}_{hash(msg)}"
                    
                    if sent_history.get(notice_id) != current_date_str:
                        logger.info(f"Enviando aviso programado para las {target_time}...")
                        results = broadcast_admin_message(str(NOTIF_CONFIG), msg, channels)
                        logger.info(f"Resultados del aviso ({target_time}): {results}")
                        sent_history[notice_id] = current_date_str
            
        except Exception as e:
            logger.error(f"Error en el ciclo del scheduler: {e}")
            
        # Dormir hasta el próximo inicio de minuto para ser más precisos
        now = datetime.now()
        sleep_seconds = 60 - now.second
        if sleep_seconds < 1:
            sleep_seconds = 60
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    main()

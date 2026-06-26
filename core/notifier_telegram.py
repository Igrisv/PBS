"""
notifier_telegram.py — Notificador para Telegram
Envía alertas enriquecidas sin las restricciones de plantillas de WhatsApp.
"""

import logging
import requests
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_FILE = BASE_DIR / "data" / "templates.json"

def get_templates():
    if not TEMPLATES_FILE.exists():
        return {}
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"[TELEGRAM] Error: {e}")
        return False

def send_telegram_alert(
    bot_token: str,
    chat_id: str,
    product_name: str,
    product_url: str,
    availability_text: str,
    price: str,
    change_type: str,
    seller: str = None
) -> bool:
    """Envía una alerta de producto usando plantillas personalizables."""
    def h_esc(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    tpls = get_templates().get("product_alert", {})
    
    # Headers dinámicos
    header_map = {
        "stock_available": tpls.get("header_stock"),
        "restock": tpls.get("header_restock"),
        "new_launch": tpls.get("header_new"),
        "price_change": tpls.get("header_price"),
        "preorder": tpls.get("header_preorder"),
        "released": "🎉 <b>¡YA DISPONIBLE! (Fuera de Preventa)</b>"
    }
    header = header_map.get(change_type) or tpls.get("header_default") or "🚨 <b>ALERTA DE POKÉMON</b> 🚨"

    # Etiquetas
    l_prod = tpls.get("label_product", "📦 <b>Producto:</b>")
    l_stat = tpls.get("label_status", "🔍 <b>Estado:</b>")
    l_pric = tpls.get("label_price", "💰 <b>Precio:</b>")
    f_text = tpls.get("footer_text", "🛒 Comprar en Amazon")

    msg = (
        f"{header}\n\n"
        f"{l_prod} {h_esc(product_name)}\n"
        f"{l_stat} {h_esc(availability_text)}\n"
        f"{l_pric} {h_esc(price)}\n"
    )
    if seller and seller.lower() != "amazon méxico":
        msg += f"🤝 <b>Vendedor:</b> {h_esc(seller)}\n"
    msg += "\n"
    
    if tpls.get("show_footer", True):
        msg += f"<a href='{product_url}'>{f_text}</a>"

    return send_telegram_message(bot_token, chat_id, msg)

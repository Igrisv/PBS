import json
import os
import logging
from core.notifier_telegram import send_telegram_alert
from core.notifier import send_whatsapp_alert, send_whatsapp_bridge_alert

logger = logging.getLogger("admin_hub")

def get_notif_config(file_path: str):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def broadcast_admin_message(config_path: str, message: str, channels: list[str] = None):
    """
    Sends a generic admin message (maintenance, custom alert) to selected channels.
    Uses the 'broadcast' template for formatting.
    """
    from core.notifier_telegram import get_templates, send_telegram_message
    from core.notifier import send_whatsapp_bridge_message
    
    cfg = get_notif_config(config_path)
    if not cfg: return False
    
    tpls = get_templates().get("broadcast", {})
    header = tpls.get("header", "📢 <b>ANUNCIO</b> 📢")
    footer = tpls.get("footer", "")
    body_format = tpls.get("body_format", "{message}")
    
    # Format message
    full_msg_html = f"{header}\n\n{body_format.replace('{message}', message)}"
    if footer:
        full_msg_html += f"\n\n{footer}"
    
    # Format for Bridge (Markdown)
    def to_md(txt):
        return txt.replace("<b>", "*").replace("</b>", "*").replace("<i>", "_").replace("</i>", "_")
    full_msg_md = to_md(full_msg_html)

    results = {}
    
    # Telegram
    if not channels or "telegram" in channels:
        t = cfg.get("telegram", {})
        results["telegram"] = send_telegram_message(t.get("bot_token"), t.get("chat_id"), full_msg_html)

    # WhatsApp Bridge
    if not channels or "whatsapp_bridge" in channels:
        b = cfg.get("whatsapp_bridge", {})
        results["bridge"] = send_whatsapp_bridge_message(b.get("bridge_url"), [b.get("community_id")], full_msg_md)
        
    return results

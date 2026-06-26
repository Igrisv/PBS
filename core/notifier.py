"""
notifier.py — Componente 3: El Mensajero (Meta WhatsApp Cloud API)
Envía alertas de stock a WhatsApp usando la API oficial de Meta.
"""

import logging
import re
import requests

logger = logging.getLogger(__name__)




def _extract_asin(url: str) -> str:
    """Extrae el ASIN (ID del producto) de una URL de Amazon."""
    import re
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    return match.group(1) if match else "B000000000"


def send_whatsapp_alert(
    access_token: str,
    phone_number_id: str,
    to_number: str,
    product_name: str,
    product_url: str,
    availability_text: str,
    price: str,
    change_type: str,  # "stock_available" | "price_change" | "test"
    template_name: str = "inventory_update",  # Nombre de la plantilla aprobada en Meta
    language_code: str = "es_MX",        # Idioma exacto de la plantilla
    use_button: bool = True,             # Desactivar si la plantilla NO tiene botón
    seller: str = None
) -> bool:
    """
    Envía un mensaje de WhatsApp via Meta Cloud API usando una plantilla.
    """
    
    # Limpieza de número
    clean_to_number = to_number.replace("whatsapp:", "").replace("+", "").strip()

    url = f"https://graph.facebook.com/v25.0/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Parámetros del Body ({{1}}, {{2}}, {{3}}, {{4}})
    price_str = price
    if seller and seller.lower() != "amazon méxico":
        price_str = f"{price} (por {seller[:15]})"

    body_params = [
        {"type": "text", "text": product_name[:30]},       # Nombre
        {"type": "text", "text": availability_text[:15]}, # Estado
        {"type": "text", "text": price_str[:30]},         # Precio + Vendedor
        {"type": "text", "text": product_url}             # URL
    ]

    components = [
        {
            "type": "body",
            "parameters": body_params
        }
    ]

    # Añadir botón si está activo
    if use_button:
        asin = _extract_asin(product_url)
        components.append({
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [{"type": "text", "text": asin}]
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_to_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components
        }
    }

    try:
        logger.info(f"[WHATSAPP] Enviando plantilla '{template_name}' (lang: {language_code}) a {clean_to_number}")
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response_json = response.json()
        
        if response.status_code in (200, 201):
            msg_id = response_json.get("messages", [{}])[0].get("id", "N/A")
            logger.info(f"[WHATSAPP] ✅ Mensaje ACEPADO por Meta. ID: {msg_id}")
            # Log de debug detallado
            logger.debug(f"[WHATSAPP] Payload enviado: {payload}")
            logger.debug(f"[WHATSAPP] Respuesta completa: {response_json}")
            return True
        else:
            logger.error(f"[WHATSAPP] ❌ Meta rechazó el mensaje (Status {response.status_code})")
            logger.error(f"[WHATSAPP] Detalles: {response_json}")
            return False
            
    except Exception as e:
        logger.error(f"[WHATSAPP] ❌ Error de conexión con Meta: {e}")
        return False


def send_whatsapp_bridge_message(
    bridge_url: str,
    chat_id: str | list[str],
    text: str,
    image_url: str = None
) -> bool:
    """Envía un mensaje de texto plano a través del Bridge."""
    chat_ids = [chat_id] if isinstance(chat_id, str) else list(chat_id or [])
    chat_ids = [c for c in dict.fromkeys(chat_ids) if c]

    if not bridge_url or not chat_ids:
        return False

    success = True
    for target_chat_id in chat_ids:
        payload = {"chatId": target_chat_id, "message": text, "mediaUrl": image_url}
        try:
            url = f"{bridge_url.rstrip('/')}/send-alert"
            import requests
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code != 200:
                success = False
                logger.error(f"[BRIDGE] Error {response.status_code}: {response.text}")
        except Exception as e:
            success = False
            logger.error(f"[BRIDGE] Conexión fallida: {e}")
    return success

def send_whatsapp_bridge_alert(
    bridge_url: str,
    chat_id: str | list[str],
    product_name: str,
    product_url: str,
    availability_text: str,
    price: str,
    change_type: str,
    image_url: str = None,
    seller: str = None
) -> bool:
    """Envía una alerta de producto usando el Bridge con plantillas configurables."""
    from core.notifier_telegram import get_templates
    tpls = get_templates().get("product_alert", {})

    header_map = {
        "stock_available": tpls.get("header_stock"),
        "restock": tpls.get("header_restock"),
        "new_launch": tpls.get("header_new"),
        "price_change": tpls.get("header_price"),
        "preorder": tpls.get("header_preorder"),
        "released": "🎉 *¡YA DISPONIBLE! (Fuera de Preventa)*"
    }
    
    def to_md(txt):
        if not txt: return ""
        return txt.replace("<b>", "*").replace("</b>", "*").replace("<i>", "_").replace("</i>", "_")

    header = to_md(header_map.get(change_type) or tpls.get("header_default") or "🚨 *ALERTA DE POKÉMON* 🚨")
    l_prod = to_md(tpls.get("label_product", "*📦 Producto:*"))
    l_stat = to_md(tpls.get("label_status", "*🔍 Estado:*"))
    l_pric = to_md(tpls.get("label_price", "*💰 Precio:*"))
    f_text = to_md(tpls.get("footer_text", "Compra aquí"))

    msg = (
        f"{header}\n\n"
        f"{l_prod} {product_name}\n"
        f"{l_stat} {availability_text}\n"
        f"{l_pric} {price}\n"
    )
    if seller and seller.lower() != "amazon méxico":
        msg += f"🤝 *Vendedor:* {seller}\n"
    msg += "\n"

    if tpls.get("show_footer", True):
        msg += f"🛒 *{f_text}:* {product_url}"

    return send_whatsapp_bridge_message(bridge_url, chat_id, msg, image_url)

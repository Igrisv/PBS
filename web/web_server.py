"""
web_server.py — Dashboard con soporte de suscripción y API mejorada
"""

import json
import os
import logging
import threading
from flask import Flask, jsonify, request, render_template
from pathlib import Path
import sys
import subprocess

BASE_DIR_STR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR_STR not in sys.path:
    sys.path.append(BASE_DIR_STR)

from web.auth import get_client_id_from_request, validate_client_access
from core.utils import get_file_lock
from core.admin_hub import broadcast_admin_message

file_lock = threading.Lock()
logger = logging.getLogger("web_server")
app = Flask(__name__)


@app.before_request
def enforce_client_access():
    """Bloquea rutas de cliente si no tienen licencia asignada."""
    client_id = get_client_id_from_request()
    if not client_id:
        return None

    allowed, message = validate_client_access(client_id, required_feature="restock")
    if not allowed:
        return jsonify({"error": "Acceso denegado", "message": message, "client": client_id}), 403
    return None

BASE_DIR            = Path(__file__).resolve().parent.parent
PRODUCTS_FILE       = BASE_DIR / "data" / "products.json"
STATE_FILE          = BASE_DIR / "data" / "monitor_state.json"
DISCOVERED_FILE     = BASE_DIR / "data" / "discovered.json"
DISCOVERY_CONFIG    = BASE_DIR / "data" / "discovery_config.json"
NOTIF_CONFIG        = BASE_DIR / "data" / "notif_config.json"
STATS_FILE          = BASE_DIR / "data" / "stats.json"

# Lock mechanism: use a file-based lock for multi-process safety
GLOBAL_LOCK_FILE = BASE_DIR / "data" / "app.lock"

def get_shared_lock():
    return get_file_lock(GLOBAL_LOCK_FILE)

DASHBOARD_BASE_PATH = os.getenv("DASHBOARD_BASE_PATH", "").strip("/")


def get_base_path():
    forwarded_prefix = (request.headers.get("X-Forwarded-Prefix", "") or "").strip("/")
    if forwarded_prefix:
        return forwarded_prefix
    return DASHBOARD_BASE_PATH

# Token de acceso simple para proteger ediciones (se lee del .env)
from dotenv import load_dotenv
load_dotenv()
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
DISCOVERY_CONFIG = Path(BASE_DIR) / "data" / "discovery_config.json"
NOTIF_CONFIG     = Path(BASE_DIR) / "data" / "notif_config.json"
OPERATIONAL_CONFIG = Path(BASE_DIR) / "data" / "operational_config.json"
SCHEDULED_NOTICES_FILE = Path(BASE_DIR) / "data" / "scheduled_notices.json"

# --- JSON Helpers ---
def get_json_data(file_path):
    if not file_path.exists():
        # Defaults
        if "operational" in str(file_path):
            return {"restock_interval": 20, "discovery_interval": 20, "fresh_window_minutes": 20, "warmup_period_minutes": 0}
        return [] if ("products" in str(file_path) or "discovered" in str(file_path)) else {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return [] if ("products" in str(file_path) or "discovered" in str(file_path)) else {}

def save_json_data(file_path, data):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving {file_path}: {e}")
        return False


def check_token():
    """Si DASHBOARD_TOKEN está configurado en .env, valida Bearer token en la request."""
    if not DASHBOARD_TOKEN:
        return True  # Sin token configurado = acceso libre (modo desarrollo)
    auth_header = request.headers.get("Authorization", "")
    return auth_header == f"Bearer {DASHBOARD_TOKEN}"


# ─── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", base_path=get_base_path())


@app.route("/admin-hub")
def admin_hub():
    """Panel de administración separado."""
    # Opcional: Validar token si se desea protección extra en el cargado de la página
    # if not check_token(): ...
    return render_template("admin.html", base_path=get_base_path())


@app.route("/api/status")
def get_status():
    products   = get_json_data(PRODUCTS_FILE)
    snapshots  = get_json_data(STATE_FILE)
    discovered = get_json_data(DISCOVERED_FILE)
    stats      = get_json_data(STATS_FILE)
    active_count = len([p for p in products if p.get("active", True)]) if isinstance(products, list) else 0
    disc_count   = len(discovered) if isinstance(discovered, (list, dict)) else 0
    return jsonify({
        "watchlist_count": len(products) if isinstance(products, list) else 0,
        "active_count":    active_count,
        "memory_count":    len(snapshots),
        "discovered_count": disc_count,
        "status": "Running",
        "network_stats": stats
    })


@app.route("/api/subscription")
def get_subscription():
    """Retorna el estado de la suscripción para el dashboard."""
    try:
        from auth import get_subscription_status
        return jsonify(get_subscription_status())
    except Exception as e:
        return jsonify({"valid": False, "message": str(e), "plan": "error", "days_left": 0})


@app.route("/api/products", methods=["GET", "POST"])
def manage_products():
    if request.method == "GET":
        products = get_json_data(PRODUCTS_FILE)
        if isinstance(products, list):
            # Enriquecer cada producto con datos del último snapshot (imagen, precio, vendedor)
            snapshots = get_json_data(STATE_FILE)
            for p in products:
                snap = snapshots.get(p.get("url", ""), {}) if isinstance(snapshots, dict) else {}
                p.setdefault("image_url", snap.get("image_url"))
                p.setdefault("last_price", snap.get("price"))
                p.setdefault("last_seller", snap.get("seller"))
                p.setdefault("last_in_stock", snap.get("in_stock"))
        return jsonify(products)

    if request.method == "POST":
        if not check_token():
            return jsonify({"error": "Unauthorized"}), 401
        new_prod = request.json
        if not new_prod or not new_prod.get("name") or not new_prod.get("url"):
            return jsonify({"error": "Faltan datos: name y url son obligatorios"}), 400
        with file_lock:
            products = get_json_data(PRODUCTS_FILE)
            if not isinstance(products, list):
                products = []
            # Integrar nuevos campos de producto: amazon_only, max_price
            new_prod["active"] = new_prod.get("active", True)
            new_prod["amazon_only"] = bool(new_prod.get("amazon_only", True))
            # Aceptar max_price como número si se envía, o None
            try:
                new_prod["max_price"] = float(new_prod["max_price"]) if "max_price" in new_prod and new_prod["max_price"] not in (None, "") else None
            except Exception:
                new_prod["max_price"] = None
            products.append(new_prod)
            with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=2, ensure_ascii=False)
        return jsonify({"success": True}), 201


@app.route("/api/products/<int:index>", methods=["DELETE", "PUT"])
def product_by_index(index):
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401

    with file_lock:
        products = get_json_data(PRODUCTS_FILE)
        if not isinstance(products, list) or not (0 <= index < len(products)):
            return jsonify({"error": "Índice inválido"}), 404

        if request.method == "DELETE":
            removed = products.pop(index)
            with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=2, ensure_ascii=False)
            return jsonify({"success": True, "removed": removed.get("name")})

        if request.method == "PUT":
            updates = request.json or {}
            if "name" in updates:
                products[index]["name"] = updates["name"]
            if "url" in updates:
                products[index]["url"] = updates["url"]
            if "active" in updates:
                products[index]["active"] = bool(updates["active"])
            # Nuevos campos editables desde dashboard
            if "amazon_only" in updates:
                products[index]["amazon_only"] = bool(updates["amazon_only"])
            if "max_price" in updates:
                try:
                    products[index]["max_price"] = float(updates["max_price"]) if updates["max_price"] not in (None, "") else None
                except Exception:
                    products[index]["max_price"] = None
            with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=2, ensure_ascii=False)
            return jsonify({"success": True, "updated": products[index]})


@app.route("/api/snapshots")
def get_snapshots():
    state = get_json_data(STATE_FILE)
    products = get_json_data(PRODUCTS_FILE)
    
    # Crear un set con las URLs activas en la watchlist
    active_urls = {p.get("url") for p in products if isinstance(p, dict) and "url" in p}
    
    # Filtrar el estado para devolver solo los snapshots de las URLs activas
    filtered_state = {url: data for url, data in state.items() if url in active_urls}
    
    return jsonify(filtered_state)


@app.route("/api/discovered")
def get_discovered():
    """Retorna los productos descubiertos con su metadata."""
    data = get_json_data(DISCOVERED_FILE)
    # Si es el formato nuevo (dict), enriquecemos la respuesta
    if isinstance(data, dict):
        result = []
        for asin, meta in data.items():
            result.append({
                "asin": asin,
                "name": meta.get("name", asin),
                "price": meta.get("price", "N/D"),
                "first_seen": meta.get("first_seen", 0),
                "url": f"https://www.amazon.com.mx/dp/{asin}",
                "image_url": meta.get("image_url", None),
            })
        result.sort(key=lambda x: x["first_seen"], reverse=True)
        return jsonify(result)
    return jsonify([])


@app.route("/api/discovered/add-to-watchlist", methods=["POST"])
def add_discovered_to_watchlist():
    """Mueve un ASIN descubierto directamente a la watchlist."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}
    asin = payload.get("asin", "")
    name = payload.get("name", asin)

    if not asin:
        return jsonify({"error": "ASIN requerido"}), 400

    url = f"https://www.amazon.com.mx/dp/{asin}"

    with file_lock:
        products = get_json_data(PRODUCTS_FILE)
        if not isinstance(products, list):
            products = []
        if any(p.get("url") == url for p in products):
            return jsonify({"error": "Producto ya en watchlist"}), 409
        # Añadir campos por defecto para filtros
        products.append({"name": name, "url": url, "active": True, "amazon_only": True, "max_price": None})
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, ensure_ascii=False)

    return jsonify({"success": True, "name": name, "url": url}), 201


@app.route("/api/discovered", methods=["DELETE"])
def clear_discovered():
    """Limpia el archivo de productos descubiertos."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401

    with get_shared_lock():
        with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2, ensure_ascii=False)
    
    logger.info("[WEB] Se han limpiado todos los productos descubiertos.")
    return jsonify({"success": True, "message": "Lista de descubrimientos limpiada."})


# ─── Discovery Config API ──────────────────────────────────────────────────────

def _read_discovery_config() -> dict:
    """Lee discovery_config.json desde data/, con defaults si no existe."""
    defaults = {
        "searches": [],
        "excluded_keywords": [],
        "preorder_keywords": ["reservar", "reservar ahora", "preventa", "próximamente"],
        "max_product_age_days": 365,
        "max_asins_per_search": 15,
    }
    if DISCOVERY_CONFIG.exists():
        try:
            with open(DISCOVERY_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for k, v in defaults.items():
                    if k not in cfg:
                        cfg[k] = v
                return cfg
        except Exception:
            pass
    return defaults


def _write_discovery_config(cfg: dict) -> None:
    DISCOVERY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(DISCOVERY_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


@app.route("/api/discovery/config", methods=["GET"])
def get_discovery_config():
    """Retorna la configuración completa del Discovery."""
    return jsonify(_read_discovery_config())


@app.route("/api/discovery/config", methods=["POST"])
def update_discovery_config():
    """Guarda la configuración completa del Discovery (reemplaza)."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    with get_shared_lock():
        _write_discovery_config(data)
    logger.info("[WEB] Discovery config actualizado.")
    return jsonify({"success": True})


@app.route("/api/discovery/search/<int:index>", methods=["PUT"])
def toggle_discovery_search(index):
    """Habilita o deshabilita una búsqueda individual."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    payload = request.json or {}
    with get_shared_lock():
        cfg = _read_discovery_config()
        searches = cfg.get("searches", [])
        if not (0 <= index < len(searches)):
            return jsonify({"error": "Índice inválido"}), 404
        if "enabled" in payload:
            searches[index]["enabled"] = bool(payload["enabled"])
        if "name" in payload:
            searches[index]["name"] = payload["name"]
        if "url" in payload:
            searches[index]["url"] = payload["url"]
        cfg["searches"] = searches
        _write_discovery_config(cfg)
    return jsonify({"success": True, "search": searches[index]})


@app.route("/api/discovery/search", methods=["POST"])
def add_discovery_search():
    """Agrega una nueva búsqueda a la lista."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    payload = request.json or {}
    name = payload.get("name", "").strip()
    url  = payload.get("url", "").strip()
    if not name or not url:
        return jsonify({"error": "name y url son requeridos"}), 400
    with get_shared_lock():
        cfg = _read_discovery_config()
        cfg["searches"].append({"name": name, "url": url, "enabled": True})
        _write_discovery_config(cfg)
    return jsonify({"success": True}), 201


@app.route("/api/discovery/search/<int:index>", methods=["DELETE"])
def delete_discovery_search(index):
    """Elimina una búsqueda de la lista."""
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    with get_shared_lock():
        cfg = _read_discovery_config()
        searches = cfg.get("searches", [])
        if not (0 <= index < len(searches)):
            return jsonify({"error": "Índice inválido"}), 404
        removed = searches.pop(index)
        cfg["searches"] = searches
        _write_discovery_config(cfg)
    return jsonify({"success": True, "removed": removed.get("name")})


# --- Admin Hub: Custom Alerts & Config ---

@app.route("/api/admin/notif-config", methods=["GET"])
def get_notif_config_api():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_json_data(NOTIF_CONFIG))


@app.route("/api/admin/notif-config", methods=["POST"])
def update_notif_config_api():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    with get_shared_lock():
        with open(NOTIF_CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({"success": True})


@app.route("/api/admin/broadcast", methods=["POST"])
def admin_broadcast():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    payload = request.json or {}
    msg = payload.get("message", "").strip()
    channels = payload.get("channels", ["telegram", "whatsapp_bridge"])
    
    if not msg:
        return jsonify({"error": "Mensaje vacío"}), 400
        
    # broadcast_admin_message usa locks internamente si los implementamos, o simplemente lee.
    # Usaremos el lock para leer la config de forma segura.
    with get_shared_lock():
        results = broadcast_admin_message(str(NOTIF_CONFIG), msg, channels)
    
    return jsonify({"success": True, "results": results})


@app.route("/api/admin/scheduled-notices", methods=["GET"])
def get_scheduled_notices():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    notices = get_json_data(SCHEDULED_NOTICES_FILE)
    if isinstance(notices, dict): # Handle empty file default
        notices = []
    return jsonify(notices)


@app.route("/api/admin/scheduled-notices", methods=["POST"])
def update_scheduled_notices():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or []
    if not isinstance(data, list):
        return jsonify({"error": "Data must be a list"}), 400
    with get_shared_lock():
        with open(SCHEDULED_NOTICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({"success": True})


def start_web_server(host="0.0.0.0", port=5000, shared_lock=None):
    if shared_lock:
        global file_lock
        file_lock = shared_lock
    app.run(host=host, port=port, debug=False, use_reloader=False)

@app.route("/api/system/status")
def system_status():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    
    # Run pm2 jlist
    try:
        result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, check=True)
        pm2_data = json.loads(result.stdout)
    except Exception as e:
        pm2_data = []
        logger.error(f"Error running pm2 jlist: {e}")

    # Process statuses
    processes = []
    for p in pm2_data:
        name = p.get("name")
        if name in ["restock", "pbs-bridge", "pbs-web"]:
            processes.append({
                "name": name,
                "status": p.get("pm2_env", {}).get("status", "unknown"),
                "memory": p.get("monit", {}).get("memory", 0),
                "cpu": p.get("monit", {}).get("cpu", 0)
            })
            
    # Check global CAPTCHA state
    snapshots = get_json_data(STATE_FILE)
    captcha_active = any(s.get("captcha_detected", False) for s in snapshots.values() if isinstance(s, dict))
    
    return jsonify({
        "processes": processes,
        "captcha_active": captcha_active
    })

@app.route("/api/system/process/<name>/<action>", methods=["POST"])
def system_process_action(name, action):
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
        
    valid_actions = ["start", "stop", "restart"]
    if action not in valid_actions:
        return jsonify({"error": "Acción inválida"}), 400
        
    try:
        subprocess.run(["pm2", action, name], capture_output=True, text=True, check=True)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error executing pm2 {action} {name}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/operational", methods=["GET"])
def get_operational_config():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_json_data(OPERATIONAL_CONFIG))

@app.route("/api/admin/operational", methods=["POST"])
def update_operational_config():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if save_json_data(OPERATIONAL_CONFIG, data):
        return jsonify({"success": True})
    return jsonify({"error": "Failed to save"}), 500

TEMPLATES_CONFIG = Path(BASE_DIR) / "data" / "templates.json"

@app.route("/api/admin/templates", methods=["GET"])
def get_templates_api():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_json_data(TEMPLATES_CONFIG))

@app.route("/api/admin/templates", methods=["POST"])
def update_templates_api():
    if not check_token():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if save_json_data(TEMPLATES_CONFIG, data):
        return jsonify({"success": True})
    return jsonify({"error": "Failed to save"}), 500

if __name__ == "__main__":
    start_web_server()


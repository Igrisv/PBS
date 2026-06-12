import json
import logging
import os
import time
import sys
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from markupsafe import escape

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from core.scraper import scrape

# Configure logging
LOG_FILE = os.path.join(ROOT_DIR, "logs", "autobuyer.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("autobuyer_web")

# Paths and Config
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
BOUGHT_FILE = BASE_DIR / "bought_products.json"
SESSION_FILE = BASE_DIR / "amazon_session.json"
ENV_FILE = BASE_DIR / ".env"

app = Flask(__name__)

# --- Helper functions ---

def read_config():
    if not CONFIG_FILE.exists():
        return {"target_products": []}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"target_products": []}

def write_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def read_bought():
    if not BOUGHT_FILE.exists():
        return {"bought_urls": []}
    try:
        return json.loads(BOUGHT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"bought_urls": []}

def write_bought(data):
    BOUGHT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def get_check_interval():
    interval = 45 # Default
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.startswith("CHECK_INTERVAL_SECONDS="):
                try:
                    interval = int(line.split("=")[1].strip())
                except:
                    pass
    return interval

def update_check_interval(new_interval):
    # This edits the .env file cleanly
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"CHECK_INTERVAL_SECONDS={new_interval}\n", encoding="utf-8")
        return
    
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith("CHECK_INTERVAL_SECONDS="):
            new_lines.append(f"CHECK_INTERVAL_SECONDS={new_interval}")
            found = True
        else:
            new_lines.append(line)
    
    if not found:
        new_lines.append(f"CHECK_INTERVAL_SECONDS={new_interval}")
    
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

def is_session_active():
    # Helper to check if file exists and has been modified recently
    # In reality, session validation is done by the buyer, but here we just show if cookies exist
    if SESSION_FILE.exists():
        mtime = SESSION_FILE.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        # If older than 72 hours, it's likely expired
        if age_hours > 72:
            return "expired"
        return "active"
    return "missing"

# --- Web Endpoints ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config", methods=["GET"])
def api_get_config():
    config = read_config()
    bought_data = read_bought()
    bought_urls = set(bought_data.get("bought_urls", []))
    
    products = config.get("target_products", [])
    
    # Enriquecer data con info de estado "comprado"
    for p in products:
        p["is_bought"] = p.get("url") in bought_urls
        if "quantity" not in p:
            p["quantity"] = 1 # default
            
    return jsonify({
        "products": products,
        "check_interval": get_check_interval(),
        "session_status": is_session_active()
    })

@app.route("/api/product", methods=["POST"])
def api_add_product():
    data = request.json
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    quantity = int(data.get("quantity", 1))
    
    if not name or not url:
        return jsonify({"error": "Faltan datos"}), 400
        
    config = read_config()
    products = config.get("target_products", [])
    
    # Comprobar si ya existe la URL
    for p in products:
        if p.get("url") == url:
            return jsonify({"error": "El producto ya existe"}), 400
            
    # Obtener la imagen
    image_url = None
    try:
        snapshot = scrape(name, url)
        image_url = snapshot.image_url
        name = snapshot.title if snapshot.title != name else name # Refinar el nombre si es posible
    except Exception as e:
        logger.error(f"Error extrayendo imagen para {url}: {e}")
        
    products.append({
        "name": name,
        "url": url,
        "enabled": True,
        "quantity": quantity,
        "image_url": image_url
    })
    
    config["target_products"] = products
    write_config(config)
    return jsonify({"success": True})

@app.route("/api/product/update", methods=["POST"])
def api_update_product():
    data = request.json
    url = data.get("url")
    field = data.get("field")
    value = data.get("value")
    
    if not url or not field:
        return jsonify({"error": "Faltan datos"}), 400
        
    config = read_config()
    for p in config.get("target_products", []):
        if p.get("url") == url:
            if field == "enabled":
                p["enabled"] = bool(value)
            elif field == "quantity":
                p["quantity"] = int(value)
            break
            
    write_config(config)
    return jsonify({"success": True})

@app.route("/api/product/delete", methods=["POST"])
def api_delete_product():
    data = request.json
    url = data.get("url")
    if not url:
         return jsonify({"error": "Faltan datos"}), 400
         
    config = read_config()
    products = config.get("target_products", [])
    config["target_products"] = [p for p in products if p.get("url") != url]
    
    write_config(config)
    return jsonify({"success": True})

@app.route("/api/product/reset_bought", methods=["POST"])
def api_reset_bought():
    # Permite al usuario "des-comprar" un artículo para que el bot vuelva a considerarlo
    data = request.json
    url = data.get("url")
    if not url:
        return jsonify({"error": "Faltan datos"}), 400
        
    bought_data = read_bought()
    bought_urls = bought_data.get("bought_urls", [])
    if url in bought_urls:
        bought_urls.remove(url)
        bought_data["bought_urls"] = bought_urls
        write_bought(bought_data)
        
    return jsonify({"success": True})

@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json
    interval = data.get("check_interval")
    if interval:
        try:
            val = int(interval)
            if val >= 5:
                update_check_interval(val)
        except ValueError:
            pass
            
    return jsonify({"success": True})

if __name__ == "__main__":
    logger.info("Iniciando servidor del Dashboard de Auto-Buyer en http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)

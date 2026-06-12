"""
discovery.py — Módulo de Descubrimiento de Nuevos Lanzamientos
Escanea resultados de Amazon MX ordenados por fecha y filtra por vendedor oficial.
Lee su configuración desde data/discovery_config.json para ser gestionable por el panel web.
"""

import logging
import requests
import json
import os
import re
import sys
import time
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup

# ─── Path Setup ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.scraper import _build_headers, _is_captcha, scrape, normalize_title

logger = logging.getLogger(__name__)

# ─── Rutas absolutas ─────────────────────────────────────────────────────────────
DISCOVERED_FILE     = str(BASE_DIR / "data" / "discovered.json")
DISCOVERY_CONFIG    = BASE_DIR / "data" / "discovery_config.json"

# ─── Defaults (si falla la lectura del config) ───────────────────────────────────
_DEFAULT_CONFIG = {
    "searches": [
        {"name": "Pokemon TCG",  "url": "https://www.amazon.com.mx/s?k=Pokemon+TCG&s=date-desc-rank",  "enabled": True},
        {"name": "Pokemon ETB",  "url": "https://www.amazon.com.mx/s?k=Pokemon+Elite+Trainer+Box&s=date-desc-rank", "enabled": True},
    ],
    "excluded_keywords": ["peluche", "plush", "sleeve", "protector"],
    "preorder_keywords": [
        "reservar", "reservar ahora", "preventa", "pre-venta", "próximamente",
        "proximamente", "coming soon", "pre-order", "preorder",
        "disponible el", "available on", "expected", "aniversario", "anniversary"
    ],
    "max_product_age_days": 365,
    "max_asins_per_search": 15,
}


def _load_discovery_config() -> dict:
    """Carga la configuración del Discovery desde data/discovery_config.json."""
    if DISCOVERY_CONFIG.exists():
        try:
            with open(DISCOVERY_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                # Asegurar que tengamos todos los campos necesarios
                for key, val in _DEFAULT_CONFIG.items():
                    if key not in cfg:
                        cfg[key] = val
                return cfg
        except Exception as e:
            logger.error(f"[DISCOVERY] Error leyendo discovery_config.json: {e}. Usando defaults.")
    return _DEFAULT_CONFIG.copy()


# ─── Helpers de datos persistentes ───────────────────────────────────────────────

def _load_discovered() -> dict:
    if os.path.exists(DISCOVERED_FILE):
        try:
            with open(DISCOVERED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Migrar formato viejo (lista) a dict
                    return {a: {"name": f"Migrated {a}", "normalized_name": "", "price": "N/D", "first_seen": 0} for a in data}
                return data
        except Exception:
            return {}
    return {}


def _save_discovered(data: dict) -> None:
    os.makedirs(os.path.dirname(DISCOVERED_FILE), exist_ok=True)
    with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Helpers de filtrado ──────────────────────────────────────────────────────────

def _is_duplicate_name(title: str, discovered: dict) -> bool:
    norm_title = normalize_title(title)
    if not norm_title:
        return False
    for data in discovered.values():
        if data.get("normalized_name") == norm_title:
            return True
    return False


def _is_recent(release_date: str | None, max_days: int) -> bool:
    """True si no hay fecha (posible preventa/anuncio) o si el lanzamiento está dentro de la ventana."""
    if release_date is None:
        return True
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
        dt = datetime.fromisoformat(release_date).replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except ValueError:
        return True


def _is_official(seller: str, amazon_present: bool = False) -> bool:
    """Retorna True SOLO si Amazon MX es el vendedor o está en las ofertas."""
    if amazon_present:
        return True
    s = seller.lower().strip()
    if not s or s == "desconocido":
        return False
    return "amazon" in s


def _is_pokemon(title: str) -> bool:
    t = title.lower()
    return "pokemon" in t or "pokémon" in t


def _should_include_product(title: str, excluded_keywords: list) -> bool:
    """Filtra productos basados en keywords excluidas."""
    if not excluded_keywords:
        return True
    normalized = normalize_title(title).lower()
    return not any(term.lower() in normalized for term in excluded_keywords)


def _is_preorder(snap, preorder_keywords: list) -> bool:
    """
    Detecta si un producto está en preventa/pre-orden.
    Busca en: availability_text, título, y el HTML de la página de Amazon.
    Amazon MX usa "Reservar ahora" como botón principal de preventa.
    """
    texts_to_check = [
        (snap.availability_text or "").lower(),
        (snap.title or "").lower(),
    ]
    combined = " ".join(texts_to_check)
    return any(kw.lower() in combined for kw in preorder_keywords)


def _extract_asins_from_page(html: bytes) -> list[str]:
    """Extrae ASINs únicos de una página de resultados de Amazon."""
    soup = BeautifulSoup(html, "lxml")

    if _is_captcha(soup):
        return []  # Señal de CAPTCHA

    asins = []
    # Método principal: atributo data-asin en contenedores de producto
    for tag in soup.find_all(attrs={"data-asin": True}):
        a = tag.get("data-asin", "").strip()
        if a and len(a) == 10 and a not in asins:
            asins.append(a)

    # Fallback: links con /dp/ASIN
    if not asins:
        for link in soup.find_all("a", href=re.compile(r"/dp/([A-Z0-9]{10})")):
            m = re.search(r"/dp/([A-Z0-9]{10})", link["href"])
            if m and m.group(1) not in asins:
                asins.append(m.group(1))

    return asins


# ─── Función principal ────────────────────────────────────────────────────────────

def discover_new_products() -> list:
    """
    Escanea las páginas de búsqueda en Amazon MX buscando Pokémon nuevos.
    Lee la configuración desde data/discovery_config.json.
    Retorna lista de tuplas (ProductSnapshot, change_type) donde change_type
    es 'new_launch' o 'preorder'.
    """
    cfg               = _load_discovery_config()
    searches          = cfg.get("searches", [])
    excluded_keywords = cfg.get("excluded_keywords", [])
    preorder_keywords = cfg.get("preorder_keywords", [])
    max_age_days      = int(cfg.get("max_product_age_days", 365))
    max_asins         = int(cfg.get("max_asins_per_search", 15))

    discovered = _load_discovered()
    new_products = []  # Lista de (snapshot, change_type)

    for search in searches:
        if not search.get("enabled", True):
            logger.info(f"[DISCOVERY] ⏭️  Se omite '{search['name']}' (deshabilitado).")
            continue

        search_name = search["name"]
        url = search["url"]
        logger.info(f"[DISCOVERY] 🔎 Escaneando '{search_name}'...")
        time.sleep(random.uniform(2, 4))

        try:
            resp = requests.get(url, headers=_build_headers(), timeout=15)
        except Exception as e:
            logger.error(f"[DISCOVERY] Error de red en '{search_name}': {e}")
            continue

        if resp.status_code != 200:
            logger.warning(f"[DISCOVERY] HTTP {resp.status_code} en '{search_name}', saltando.")
            continue

        all_asins = _extract_asins_from_page(resp.content)
        if not all_asins:
            logger.warning(f"[DISCOVERY] CAPTCHA o sin ASINs en '{search_name}'.")
            continue

        # Filtrar solo ASINs nuevos
        unique_asins = [a for a in all_asins if a not in discovered]
        new_asins = unique_asins[:max_asins]
        logger.info(f"[DISCOVERY]   → {len(all_asins)} ASINs | {len(new_asins)} nuevos a revisar")

        for asin in new_asins:
            if asin in discovered:
                continue

            # Marcar como visto ANTES de scrapear (evita race conditions)
            discovered[asin] = {
                "name": f"Pending {asin}",
                "normalized_name": "",
                "price": "N/D",
                "first_seen": time.time(),
                "image_url": None,
            }
            _save_discovered(discovered)

            time.sleep(random.uniform(3, 6))

            prod_url = f"https://www.amazon.com.mx/dp/{asin}"
            snap = scrape(f"ASIN:{asin}", prod_url)

            if snap.captcha_detected or snap.error:
                logger.warning(f"[DISCOVERY]   ⚠️  CAPTCHA/Error en {asin}, guardado para no repetir.")
                continue

            # Actualizar datos con lo scrapeado
            discovered[asin].update({
                "name": snap.title,
                "normalized_name": snap.normalized_title,
                "price": snap.price,
                "image_url": snap.image_url,
            })
            _save_discovered(discovered)

            # ─── Evaluación de filtros ────────────────────────────────────────
            ok_pokemon  = _is_pokemon(snap.title)
            ok_official = _is_official(snap.seller, amazon_present=snap.amazon_present)
            ok_recent   = _is_recent(snap.release_date, max_age_days)
            ok_allowed  = _should_include_product(snap.title, excluded_keywords)
            is_dup      = _is_duplicate_name(snap.title, {k: v for k, v in discovered.items() if k != asin})

            is_preorder = _is_preorder(snap, preorder_keywords)

            # Un producto pasa si: es Pokémon, Amazon MX es el vendedor (u oficial),
            # no es demasiado viejo, no está excluido por keywords y no es duplicado.
            # Para preventa: in_stock puede ser False, pero is_preorder debe ser True.
            ok_available = snap.in_stock or is_preorder

            if ok_pokemon and ok_official and ok_recent and ok_allowed and ok_available and not is_dup:
                change_type = "preorder" if is_preorder else "new_launch"
                icon = "🔔" if is_preorder else "✅"
                logger.info(
                    f"[DISCOVERY]   {icon} {change_type.upper()} (Amazon MX): "
                    f"{snap.title} | {snap.seller} | {snap.release_date or 'sin fecha'}"
                )
                new_products.append((snap, change_type))
            else:
                reasons = []
                if not ok_pokemon:   reasons.append("No es Pokémon")
                if not ok_official:  reasons.append(f"Revendedor: {snap.seller}")
                if not ok_recent:    reasons.append(f"Antiguo: {snap.release_date}")
                if not ok_allowed:   reasons.append("Excluido por filtros")
                if not ok_available: reasons.append("Sin stock ni preventa")
                if is_dup:           reasons.append("Nombre duplicado")
                logger.info(f"[DISCOVERY]   ➡️  Ignorado ({', '.join(reasons)}): {snap.title[:55]}")

    return new_products

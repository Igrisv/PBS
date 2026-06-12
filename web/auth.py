"""
auth.py — Sistema de Autenticación por Suscripción
Valida la clave de licencia mensual antes de permitir el uso del bot.
"""

import json
import os
import logging
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import request

logger = logging.getLogger(__name__)

LICENSE_FILE = Path(__file__).resolve().parent.parent / "data" / "license.json"

# ─── Estructura del archivo license.json ──────────────────────────────────────
# {
#   "key": "POKEMON-XXXX-XXXX-XXXX",   <- Clave única del cliente
#   "owner": "Juan Perez",              <- Nombre del titular
#   "plan": "pro",                      <- "basic" | "pro"
#   "expires_at": "2026-06-30",         <- Fecha de expiración ISO YYYY-MM-DD
#   "features": ["restock", "discovery"] <- Módulos habilitados
# }


def load_license() -> dict | None:
    """Carga el archivo de licencia si existe."""
    if not LICENSE_FILE.exists():
        return None
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[AUTH] No se pudo leer la licencia: {e}")
        return None


def _validate_license_entry(lic: dict, required_feature: str = None) -> tuple[bool, str]:
    """Valida un dict de licencia ya sea global o por cliente."""
    if lic is None:
        return False, "❌ No se encontró archivo de licencia (license.json)."

    required_fields = ["key", "owner", "expires_at", "features"]
    if not all(f in lic for f in required_fields):
        return False, "❌ Licencia corrupta o incompleta."

    try:
        exp = datetime.fromisoformat(lic["expires_at"]).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > exp:
            days_expired = (now - exp).days
            return False, f"❌ Licencia expirada hace {days_expired} día(s). Renueva en @tu_bot_telegram."
    except ValueError:
        return False, "❌ Fecha de expiración inválida en la licencia."

    if required_feature and required_feature not in lic.get("features", []):
        return False, f"❌ Tu plan '{lic.get('plan', 'basic')}' no incluye el módulo '{required_feature}'."

    days_left = (exp - now).days
    owner = lic.get("owner", "Usuario")
    plan = lic.get("plan", "basic").upper()
    return True, f"✅ Licencia válida | {owner} | Plan {plan} | {days_left} día(s) restante(s)"


def validate_license(required_feature: str = None) -> tuple[bool, str]:
    """
    Valida la licencia global del usuario.
    Retorna (is_valid: bool, message: str)
    """
    lic = load_license()
    return _validate_license_entry(lic, required_feature)


def validate_client_access(client_id: str, required_feature: str = None) -> tuple[bool, str]:
    """Valida si un cliente concreto tiene acceso al dashboard."""
    if not client_id:
        return True, ""

    lic = load_license()
    if lic is None:
        return False, "❌ No se encontró archivo de licencia (license.json)."

    clients = lic.get("clients", {}) or {}
    if isinstance(clients, dict) and client_id in clients:
        return _validate_license_entry(clients[client_id], required_feature)

    if isinstance(clients, dict) and clients:
        return False, f"❌ El cliente '{client_id}' no tiene una licencia asignada."

    return _validate_license_entry(lic, required_feature)


def get_client_id_from_request() -> str:
    """Obtiene el identificador del cliente desde el prefijo forwardeado."""
    forwarded_prefix = (request.headers.get("X-Forwarded-Prefix", "") or "").strip("/")
    if forwarded_prefix:
        return forwarded_prefix

    base_path = (os.getenv("DASHBOARD_BASE_PATH", "") or "").strip("/")
    return base_path


def get_subscription_status() -> dict:
    """Retorna un dict con el estado de la suscripción para la API web."""
    lic = load_license()
    if lic is None:
        return {
            "valid": False,
            "owner": "Sin licencia",
            "plan": "none",
            "expires_at": None,
            "days_left": 0,
            "features": [],
            "message": "No se encontró archivo de licencia."
        }
    try:
        exp = datetime.fromisoformat(lic["expires_at"]).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = max(0, (exp - now).days)
        valid = now <= exp
    except Exception:
        return {"valid": False, "owner": lic.get("owner", "?"), "plan": "error",
                "expires_at": None, "days_left": 0, "features": [], "message": "Fecha inv\u00e1lida"}

    return {
        "valid": valid,
        "owner": lic.get("owner", "Desconocido"),
        "plan": lic.get("plan", "basic"),
        "expires_at": lic.get("expires_at"),
        "days_left": days_left,
        "features": lic.get("features", []),
        "message": f"{days_left} d\u00eda(s) restante(s)" if valid else "Suscripci\u00f3n expirada"
    }


def require_license(feature: str = None):
    """
    Decorador / guard de ejecución.
    Llama a esto al inicio de cualquier script para bloquear si no hay licencia válida.
    """
    valid, msg = validate_license(feature)
    print("=" * 60)
    if valid:
        logger.info(f"[AUTH] {msg}")
    else:
        logger.critical(f"[AUTH] ACCESO DENEGADO: {msg}")
        print(f"\n[AUTH] ACCESO DENEGADO\n{msg}\n")
        import sys
        sys.exit(1)
    print("=" * 60)

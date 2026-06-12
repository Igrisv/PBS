"""
license_gen.py — Generador de Licencias para el Administrador
Usa este script para crear archivos license.json para tus clientes.
"""

import json
import uuid
from datetime import datetime, timedelta

def generate_license(owner, months=1, plan="pro", features=None):
    if features is None:
        features = ["restock", "discovery"]
    
    # Generar una clave única basada en UUID
    key = f"POKEMON-{str(uuid.uuid4())[:13].upper()}"
    
    # Calcular fecha de expiración
    expires_at = (datetime.now() + timedelta(days=30 * months)).strftime("%Y-%m-%d")
    
    license_data = {
        "key": key,
        "owner": owner,
        "plan": plan,
        "expires_at": expires_at,
        "features": features
    }
    
    filename = f"license_{owner.lower().replace(' ', '_')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(license_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Licencia generada exitosamente para: {owner}")
    print(f"🔑 Clave: {key}")
    print(f"📅 Expira: {expires_at}")
    print(f"📦 Módulos: {', '.join(features)}")
    print(f"📄 Archivo creado: {filename}")
    print("-" * 40)
    print("Instrucciones: Renombra este archivo a 'license.json' y colócalo")
    print("en la carpeta raíz del bot del cliente.")

if __name__ == "__main__":
    print("--- Generador de Licencias Pokémon Monitor ---")
    name = input("Nombre del Cliente: ")
    dur = int(input("Duración en meses (ej: 1): ") or 1)
    
    # Por defecto damos todo en el plan pro
    generate_license(name, months=dur)

#!/bin/bash
# deploy_vps.sh — Instalación rápida en Linux VPS (Ubuntu/Debian)

echo "--- 🔄 Iniciando Instalación de Pokémon Bot Server ---"

# 1. Actualizar sistema
sudo apt update && sudo apt install -y python3-pip python3-venv git chromium-browser

# 2. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias de Python
pip install --upgrade pip
pip install -r requirements.txt

# 4. Instalar dependencias de Playwright (indispensable para Linux)
echo "--- 🎭 Instalando dependencias de Playwright ---"
playwright install chromium
sudo playwright install-deps

# 5. Crear carpetas necesarias
mkdir -p logs data

# 6. Configuración inicial
if [ ! -f .env ]; then
    cp .env.example .env
    echo "--- ⚠️  Se ha creado un .env de ejemplo. Edítalo con 'nano .env' antes de empezar. ---"
fi

echo "--- ✅ Instalación completa ---"
echo "Para arrancar el monitor: source venv/bin/activate && python scripts/monitor_restock.py"
echo "Para arrancar el discovery: source venv/bin/activate && python scripts/monitor_discovery.py"

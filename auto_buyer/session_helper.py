"""
auto_buyer/session_helper.py — Extractor de Sesión de Amazon
===========================================================
Este script abre un navegador VISIBLE para que puedas:
  1. Iniciar sesión manualmente en Amazon MX.
  2. Resolver cualquier Puzzle, CAPTCHA o 2FA.
  3. Una vez logueado, los cookies se guardarán en amazon_session.json.

El bot principal (buyer.py) usará ese archivo para trabajar en segundo plano.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from playwright.async_api import async_playwright

# Setup
BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / "amazon_session.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("session_helper")


async def main():
    logger.info("=" * 60)
    logger.info("  🔑 EXTRACTOR DE SESIÓN — AMAZON MX")
    logger.info("  Este navegador es VISIBLE. Por favor, logueate manualmente.")
    logger.info("=" * 60)

    async with async_playwright() as pw:
        # Abrir navegador visible (headless=False)
        browser = await pw.chromium.launch(headless=False)
        
        # Crear contexto con un User-Agent realista
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )

        page = await context.new_page()
        
        # Ir a la página de login
        logger.info("Navegando a Amazon Login...")
        await page.goto("https://www.amazon.com.mx/ap/signin?openid.mode=checkid_setup&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0&openid.return_to=https%3A%2F%2Fwww.amazon.com.mx%2F", timeout=60000)

        logger.info("ESPERANDO LOGIN MANUAL...")
        logger.info("Por favor, introduce tu correo, contraseña y resuelve los CAPTCHAs.")
        
        # Esperar hasta que veamos indicios de que la sesión está activa
        # Buscamos el selector del menú de cuenta que dice "Hola, [Nombre]"
        logged_in = False
        while not logged_in:
            try:
                # Si el selector de login ya no está y aparece el de cuenta
                account_nav = page.locator("#nav-link-accountList-nav-line-1")
                if await account_nav.count() > 0:
                    text = await account_nav.text_content()
                    if text and "Hola" in text:
                        logger.info(f"✅ ¡Sesión detectada!: {text.strip()}")
                        logged_in = True
                        break
                
                # También verificamos si cerramos la ventana por error
                if page.is_closed():
                    logger.error("❌ La ventana se cerró antes de completar el login.")
                    return

            except Exception:
                pass
            await asyncio.sleep(2)

        # Guardar cookies
        logger.info("Guardando cookies de sesión...")
        cookies = await context.cookies()
        SESSION_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
        
        logger.info("-" * 60)
        logger.info(f"✅ ÉXITO: Sesión guardada en {SESSION_FILE.name}")
        logger.info("Ya puedes cerrar esta ventana y ejecutar 'python buyer.py'.")
        logger.info("-" * 60)
        
        # Darle un momento al usuario para leer antes de cerrar
        await asyncio.sleep(5)
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

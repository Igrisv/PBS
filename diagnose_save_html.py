"""
diagnose_save_html.py — Guarda el HTML completo para inspección
"""
import sys, os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.scraper import get_session

URL = "https://www.amazon.com.mx/dp/B0G5FWJ8NL"

def save_html():
    sess = get_session()
    resp = sess.get(URL, timeout=15)
    
    with open("debug_page.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    
    print(f"HTML guardado en debug_page.html (Status: {resp.status_code})")
    print(f"Tamaño: {len(resp.text)} bytes")
    
    # Búsqueda simple de Lia Toys
    if "Lia Toys" in resp.text:
        print("✅ Encontrado 'Lia Toys' en el HTML crudo.")
    else:
        print("❌ No se encontró 'Lia Toys' en el HTML crudo.")

if __name__ == "__main__":
    save_html()

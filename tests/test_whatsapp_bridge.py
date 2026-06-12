import os
import requests
from dotenv import load_dotenv

def test_bridge():
    load_dotenv()
    
    bridge_url = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3000")
    chat_id = os.getenv("WHATSAPP_COMMUNITY_ID")
    
    if not chat_id:
        print("❌ Error: WHATSAPP_COMMUNITY_ID no está configurado en el archivo .env")
        print("Corre el bridge primero para ver los IDs de tus grupos.")
        return

    print(f"🚀 Enviando mensaje de prueba a: {chat_id}...")
    
    payload = {
        "chatId": chat_id,
        "message": "🧪 *Mensaje de Prueba - Pokémon Monitor*\n\nSi ves esto, el Bridge de WhatsApp está funcionando correctamente. ✅"
    }
    
    try:
        response = requests.post(f"{bridge_url.rstrip('/')}/send-alert", json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ ¡Éxito! El mensaje ha sido enviado.")
        else:
            print(f"❌ Error ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")

if __name__ == "__main__":
    test_bridge()

# 🤖 Amazon Auto-Buyer — Ascend Heroes (OXXO)

Bot autónomo para comprar productos de Ascend Heroes en Amazon MX usando el método de pago OXXO.
**Completamente independiente** del resto del monitor de Pokémon.

## ⚙️ Instalación

```bash
# Instalar dependencias
pip install -r requirements.txt

# Instalar el navegador Chromium (solo la primera vez)
playwright install chromium
```

## 🔑 Configuración

### 1. Credenciales (`.env`)
Copia `.env.example` a `.env` y rellena con tus datos:
```
AMAZON_EMAIL=tu_correo@example.com
AMAZON_PASSWORD=tu_contraseña
```
> ⚠️ Nunca subas el archivo `.env` a GitHub.

### 2. Productos a comprar (`config.json`)
Edita `config.json` para agregar los productos de Ascend Heroes que quieres comprar:
```json
{
  "target_products": [
    {
      "name": "Ascend Heroes Booster Box",
      "url": "https://www.amazon.com.mx/dp/TU_ASIN_AQUI",
      "enabled": true
    }
  ],
  "checkout": {
    "confirm_order": false
  }
}
```
- Cambia `"enabled": true` para activar un producto.
- `"confirm_order": false` = modo **DRY-RUN** (llega a la última pantalla pero NO confirma el pedido).
- `"confirm_order": true` = **compra de verdad**.

## 🚀 Ejecución

```bash
# Desde el directorio auto_buyer/
python buyer.py
```

El bot:
1. Inicia sesión en Amazon (guarda la sesión para no repetirlo).
2. Revisa cada `CHECK_INTERVAL_SECONDS` segundos si el producto está disponible.
3. Al detectar stock: agrega al carrito → checkout → selecciona OXXO → revisa pedido.
4. Si `confirm_order=true`, confirma el pedido. Recibirás el código OXXO por email (válido 3 días).

## 📁 Archivos generados

| Archivo | Descripción |
|---|---|
| `amazon_session.json` | Cookies de sesión (generado automáticamente) |
| `auto_buyer.log` | Log de todas las acciones |
| `debug_*.png` | Screenshots de depuración ante errores |

## ⚠️ Notas importantes

- **2FA**: Si tu cuenta de Amazon tiene verificación en dos pasos, debes desactivarla o usar una cuenta secundaria para el bot.
- **CAPTCHA**: El bot detecta CAPTCHAs y espera 60s antes de reintentar.
- **ToS**: El uso de bots puede violar los términos de servicio de Amazon.
- **OXXO**: Tienes 3 días para pagar en OXXO después de confirmado el pedido.

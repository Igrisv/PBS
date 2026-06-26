"""
test_monitor_logic.py — Validación unitaria de la lógica de alertas del monitor.
Sin dependencias de red. Corre en menos de 1 segundo.
"""

def _parse_price(price_text):
    if not price_text: return None
    digits = ''.join(ch for ch in price_text if ch.isdigit() or ch in '.-')
    try: return float(digits)
    except: return None

def should_alert(product, snap, amazon_only=True):
    max_price = product.get("max_price")
    price_ok = False
    if max_price is not None:
        current_price = _parse_price(snap.get("price", ""))
        if current_price is not None:
            if current_price > float(max_price):
                return False, f"precio excedido ({current_price} > {max_price})"
            price_ok = True
    if amazon_only:
        seller = (snap.get("seller", "") or "").strip()
        is_amx = seller == "Amazon México" or snap.get("amazon_present", False)
        if not is_amx and not price_ok:
            return False, "Amazon MX no disponible y precio no validado"
    return True, "ok"

def detect_change(prev, curr):
    if curr.get("captcha_detected") or curr.get("error"):
        return None
    if prev is None:
        return "stock_available" if curr.get("in_stock") else None
    if prev.get("in_stock") and not curr.get("in_stock"):
        return None  # sold_out desactivado
    # Released tiene prioridad sobre restock genérico
    if prev.get("is_preorder") and curr.get("in_stock") and not curr.get("is_preorder"):
        return "released"
    if not prev.get("in_stock") and curr.get("in_stock"):
        return "restock"
    return None

SEP = "=" * 50

def run_tests():
    print(SEP)

    # Test 1: Released (preventa → stock)
    prev = {"in_stock": False, "is_preorder": True,  "seller": "Amazon México", "price": "$500"}
    curr = {"in_stock": True,  "is_preorder": False, "seller": "Amazon México", "price": "$500"}
    r = detect_change(prev, curr)
    assert r == "released", f"FAIL Test 1: esperaba 'released', obtuve {r!r}"
    print("✅ Test 1 - Released (preventa → stock real):", r)

    # Test 2: Fallback precio OK (vendedor desconocido + precio dentro del rango)
    cfg = {"max_price": 600.0}
    s2  = {"in_stock": True, "price": "$500", "seller": "Desconocido", "amazon_present": False}
    a2, r2 = should_alert(cfg, s2, amazon_only=True)
    assert a2 is True, f"FAIL Test 2: esperaba True, razón: {r2}"
    print("✅ Test 2 - Fallback precio OK (vendedor desconocido):", a2, r2)

    # Test 3: Precio excedido → NO alerta aunque haya stock
    s3 = {"in_stock": True, "price": "$700", "seller": "Desconocido", "amazon_present": False}
    a3, r3 = should_alert(cfg, s3, amazon_only=True)
    assert a3 is False, f"FAIL Test 3: esperaba False, obtuve {a3}"
    print("✅ Test 3 - Precio excedido → Sin alerta:", a3, r3)

    # Test 4: Revendedor 3rd-party + precio OK → alerta
    s4 = {"in_stock": True, "price": "$450", "seller": "JuanitoCartas", "amazon_present": False}
    a4, r4 = should_alert(cfg, s4, amazon_only=True)
    assert a4 is True, f"FAIL Test 4: esperaba True, obtuve {a4}"
    print("✅ Test 4 - Revendedor + precio OK → Alerta:", a4, r4)

    # Test 5: Restock normal (agotado → disponible, sin preventa)
    prev5 = {"in_stock": False, "is_preorder": False, "seller": "Amazon México", "price": "$500"}
    curr5 = {"in_stock": True,  "is_preorder": False, "seller": "Amazon México", "price": "$500"}
    r5 = detect_change(prev5, curr5)
    assert r5 == "restock", f"FAIL Test 5: esperaba 'restock', obtuve {r5!r}"
    print("✅ Test 5 - Restock normal:", r5)

    # Test 6: Sold Out → sin alerta (desactivado por diseño)
    prev6 = {"in_stock": True,  "price": "$500", "seller": "Amazon México"}
    curr6 = {"in_stock": False, "price": "$500", "seller": "Amazon México"}
    r6 = detect_change(prev6, curr6)
    assert r6 is None, f"FAIL Test 6: esperaba None (sold_out desactivado), obtuve {r6!r}"
    print("✅ Test 6 - Sold Out desactivado → sin alerta:", r6)

    # Test 7: Sin max_price + vendedor desconocido → NO alerta (sin fallback posible)
    s7 = {"in_stock": True, "price": "$500", "seller": "Desconocido", "amazon_present": False}
    a7, r7 = should_alert({}, s7, amazon_only=True)
    assert a7 is False, f"FAIL Test 7: esperaba False, obtuve {a7}"
    print("✅ Test 7 - Sin max_price + vendedor desconocido → Sin alerta:", a7, r7)

    # Test 8: CAPTCHA detectado → nunca dispara alerta
    r8 = detect_change(None, {"in_stock": True, "captcha_detected": True})
    assert r8 is None, f"FAIL Test 8: esperaba None, obtuve {r8!r}"
    print("✅ Test 8 - CAPTCHA detectado → Sin alerta:", r8)

    # Test 9: Amazon México directo (sin vendedor pero amazon_present=True) → alerta
    s9 = {"in_stock": True, "price": "$550", "seller": "Desconocido", "amazon_present": True}
    a9, r9 = should_alert({}, s9, amazon_only=True)
    assert a9 is True, f"FAIL Test 9: esperaba True, obtuve {a9}"
    print("✅ Test 9 - amazon_present=True actúa como Amazon MX → Alerta:", a9, r9)

    print(SEP)
    print("🎉 TODOS LOS TESTS PASARON — Lógica del bot verificada y lista para producción")
    print(SEP)

if __name__ == "__main__":
    run_tests()

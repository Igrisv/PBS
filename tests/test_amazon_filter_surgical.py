import json
import logging
import sys
from scraper import ProductSnapshot
from monitor import should_alert_for_product

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

def run_tests():
    print("=== TEST: Surgical Amazon MX Detection ===")
    
    # Mock product configuration (from products.json)
    product_config = {
        "name": "Test Product",
        "url": "https://www.amazon.com.mx/dp/TEST12345",
        "amazon_only": True,
        "max_price": None
    }
    
    # Case 1: Amazon MX is the direct seller
    snap1 = ProductSnapshot(
        name="Test", url="url", title="Title", in_stock=True,
        availability_text="En stock.", price="$100.00 MXN",
        seller="Amazon México", amazon_present=False
    )
    res1, reason1 = should_alert_for_product(product_config, snap1)
    print(f"CASE 1 (Direct Seller = Amazon MX): Expected=True, Got={res1} (Reason: {reason1})")
    assert res1 == True, "Failed Case 1"

    # Case 2: Third party seller, but Amazon MX is in the offers (amazon_present=True)
    snap2 = ProductSnapshot(
        name="Test", url="url", title="Title", in_stock=True,
        availability_text="Opciones de compra disponibles", price="$100.00 MXN",
        seller="Tercer Vendedor", amazon_present=True
    )
    res2, reason2 = should_alert_for_product(product_config, snap2)
    print(f"CASE 2 (Multiple Sellers, Amazon MX Present): Expected=True, Got={res2} (Reason: {reason2})")
    assert res2 == True, "Failed Case 2"

    # Case 3: Third party seller, Amazon MX NOT in offers
    snap3 = ProductSnapshot(
        name="Test", url="url", title="Title", in_stock=True,
        availability_text="Opciones de compra disponibles", price="$100.00 MXN",
        seller="Tercer Vendedor", amazon_present=False
    )
    res3, reason3 = should_alert_for_product(product_config, snap3)
    print(f"CASE 3 (Third Party Only): Expected=False, Got={res3} (Reason: {reason3})")
    assert res3 == False, "Failed Case 3"

    print("ALL TESTS PASSED: Surgical Amazon MX Filtering is working as expected.")

if __name__ == "__main__":
    run_tests()

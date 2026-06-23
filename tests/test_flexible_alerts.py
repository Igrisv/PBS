import sys
import os

# Add core and scripts to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, "scripts"))
sys.path.append(os.path.join(BASE_DIR, "core"))

import monitor
from scraper import ProductSnapshot

def test_flexible_alerts():
    # Mock product and snapshot
    product = {"name": "Test Product", "max_price": 1000}
    
    # Snapshot from Third Party
    snap_3p = ProductSnapshot(
        name="Test Product",
        url="http://amazon.mx/dp/TEST",
        title="Test Product Title",
        in_stock=True,
        availability_text="In Stock",
        price="$500",
        seller="Some Third Party",
        amazon_present=False
    )
    
    # Snapshot from Amazon
    snap_amz = ProductSnapshot(
        name="Test Product",
        url="http://amazon.mx/dp/TEST",
        title="Test Product Title",
        in_stock=True,
        availability_text="In Stock",
        price="$500",
        seller="Amazon México",
        amazon_present=True
    )

    print("Checking amazon_only=True with 3P seller...")
    should, reason = monitor.should_alert_for_product(product, snap_3p, amazon_only=True)
    assert should is False
    print(f"  Result: Filtered as expected ({reason})")

    print("Checking amazon_only=False with 3P seller...")
    should, reason = monitor.should_alert_for_product(product, snap_3p, amazon_only=False)
    assert should is True
    print(f"  Result: Alerted as expected ({reason})")

    print("Checking amazon_only=True with Amazon seller...")
    should, reason = monitor.should_alert_for_product(product, snap_amz, amazon_only=True)
    assert should is True
    print(f"  Result: Alerted as expected ({reason})")

if __name__ == "__main__":
    try:
        test_flexible_alerts()
        print("\nALL TESTS PASSED!")
    except AssertionError as e:
        print(f"\nTEST FAILED!")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

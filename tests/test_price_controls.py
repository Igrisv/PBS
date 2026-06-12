from scraper import ProductSnapshot
import monitor


def test_should_alert_skips_products_above_max_price():
    product = {"name": "Test", "url": "https://www.amazon.com.mx/dp/B0TEST0001", "active": True, "max_price": 299.99}
    snapshot = ProductSnapshot(name="Test", url=product["url"], title="Test", in_stock=True, availability_text="En stock", price="$349.99 MXN", seller="Amazon México")
    allowed, reason = monitor.should_alert_for_product(product, snapshot)
    assert allowed is False
    assert "max_price" in reason


def test_should_alert_keeps_amazon_mx_official_seller_only():
    product = {"name": "Test", "url": "https://www.amazon.com.mx/dp/B0TEST0001", "active": True, "amazon_only": True}
    snapshot = ProductSnapshot(name="Test", url=product["url"], title="Test", in_stock=True, availability_text="En stock", price="$199.00 MXN", seller="Mercado Libre")
    allowed, reason = monitor.should_alert_for_product(product, snapshot)
    assert allowed is False
    assert "seller" in reason


def test_should_alert_allows_amazon_mx_offer_within_threshold():
    product = {"name": "Test", "url": "https://www.amazon.com.mx/dp/B0TEST0001", "active": True, "max_price": 399.99, "amazon_only": True}
    snapshot = ProductSnapshot(name="Test", url=product["url"], title="Test", in_stock=True, availability_text="En stock", price="$299.00 MXN", seller="Amazon México")
    allowed, reason = monitor.should_alert_for_product(product, snapshot)
    assert allowed is True
    assert reason == "ok"


def test_should_alert_blocks_multi_seller_pages():
    product = {"name": "Test", "url": "https://www.amazon.com.mx/dp/B0TEST0001", "active": True, "amazon_only": True}
    snapshot = ProductSnapshot(name="Test", url=product["url"], title="Test", in_stock=True, availability_text="Múltiples vendedores disponibles — se filtrará hasta Amazon MX", price="$299.00 MXN", seller="Amazon México")
    allowed, reason = monitor.should_alert_for_product(product, snapshot)
    assert allowed is False
    assert "multi_seller_offer" in reason or "seller" in reason


def test_detect_change_does_not_report_sold_out():
    prev = ProductSnapshot(name="Test", url="https://www.amazon.com.mx/dp/B0TEST0001", title="Test", in_stock=True, availability_text="En stock", price="$299.00 MXN", seller="Amazon México")
    curr = ProductSnapshot(name="Test", url="https://www.amazon.com.mx/dp/B0TEST0001", title="Test", in_stock=False, availability_text="Agotado", price="$299.00 MXN", seller="Amazon México")
    assert monitor.detect_change(prev, curr) is None

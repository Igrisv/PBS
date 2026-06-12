import json
from unittest.mock import mock_open, patch

import monitor


def test_load_products_keeps_only_amazon_mx_urls():
    fake_products = [
        {"name": "Amazon MX", "url": "https://www.amazon.com.mx/dp/B0ABC12345", "active": True},
        {"name": "Mercado Libre", "url": "https://www.mercadolibre.com.mx/", "active": True},
        {"name": "Amazon US", "url": "https://www.amazon.com/dp/B0ABC12345", "active": True},
        {"name": "Inactive", "url": "https://www.amazon.com.mx/dp/B0ZZZ99999", "active": False},
    ]

    with patch("builtins.open", mock_open(read_data=json.dumps(fake_products))):
        products = monitor.load_products("products.json")

    assert [p["name"] for p in products] == ["Amazon MX"]

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_BASE_PATH", "cliente-demo")

import web_server


class WebServerBasePathTests(unittest.TestCase):
    def test_index_uses_forwarded_prefix_for_proxy_paths(self):
        fake_license = {
            "key": "LIC-TEST",
            "owner": "Demo",
            "plan": "pro",
            "expires_at": "2099-12-31",
            "features": ["restock"]
        }

        with patch("auth.load_license", return_value=fake_license):
            client = web_server.app.test_client()
            response = client.get("/", headers={"X-Forwarded-Prefix": "/cliente-demo"})

        self.assertEqual(response.status_code, 200)
        self.assertIn('<base href="/cliente-demo/">', response.text)

    def test_client_access_is_denied_for_unknown_client_when_clients_map_exists(self):
        fake_license = {
            "clients": {
                "cliente1": {
                    "key": "LIC-1",
                    "owner": "Cliente 1",
                    "plan": "pro",
                    "expires_at": "2099-12-31",
                    "features": ["restock"]
                }
            }
        }

        with patch("auth.load_license", return_value=fake_license):
            client = web_server.app.test_client()
            response = client.get("/", headers={"X-Forwarded-Prefix": "/cliente2"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("cliente2", response.text)


if __name__ == "__main__":
    unittest.main()

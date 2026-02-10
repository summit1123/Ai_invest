from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class FastApiAppTests(unittest.TestCase):
    def test_healthz(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["status"], "ok")

    def test_today_overview_shape(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/v1/ui/today-overview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertIn("latest_safe_decision", body["data"])

    def test_timeline_ok(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/v1/ui/timeline?limit=5")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_finance_endpoints_ok(self) -> None:
        with TestClient(app) as client:
            resp1 = client.get("/api/v1/ui/tax-exports?limit=5")
            resp2 = client.get("/api/v1/ui/ledger?limit=5")
            resp3 = client.get("/api/v1/ui/outcomes?limit=5")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp3.status_code, 200)
        self.assertTrue(resp1.json()["ok"])
        self.assertTrue(resp2.json()["ok"])
        self.assertTrue(resp3.json()["ok"])

    def test_research_and_ops_endpoints_ok(self) -> None:
        with TestClient(app) as client:
            resp1 = client.get("/api/v1/ui/research/daily?limit=5")
            resp1b = client.get("/api/v1/ui/agent-opinions?limit=5")
            resp2 = client.get("/api/v1/ui/collaboration/rooms?limit=5")
            resp3 = client.get("/api/v1/ui/meetings?limit=5")
            resp4 = client.get("/api/v1/ui/strategy-reviews?limit=5")
            resp5 = client.get("/api/v1/ui/trade-plan/latest")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1b.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp4.status_code, 200)
        self.assertEqual(resp5.status_code, 200)
        self.assertTrue(resp1.json()["ok"])
        self.assertTrue(resp1b.json()["ok"])
        self.assertTrue(resp2.json()["ok"])
        self.assertTrue(resp3.json()["ok"])
        self.assertTrue(resp4.json()["ok"])
        self.assertTrue(resp5.json()["ok"])


if __name__ == "__main__":
    unittest.main()

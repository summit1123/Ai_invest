from __future__ import annotations

import base64
import json
import unittest

from ai_invest.execution.upbit_private import UpbitPrivateClient, _to_query_text


class _FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, payload: object | None = None) -> None:
        self.ok = ok
        self.status_code = status_code
        self._payload = payload if payload is not None else {"uuid": "order-1"}
        self.text = json.dumps(self._payload)

    def json(self):  # type: ignore[no-untyped-def]
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse()


class UpbitPrivateTests(unittest.TestCase):
    def test_query_text_formats_small_float_without_scientific_notation(self) -> None:
        query = _to_query_text(
            {
                "market": "KRW-BTC",
                "side": "bid",
                "ord_type": "limit",
                "price": 104068000.0,
                "volume": 4.8045508705846176e-05,
            }
        )
        self.assertIn("volume=0.000048045508705846176", query)
        self.assertNotIn("e-05", query.lower())

    def test_place_order_serializes_decimal_strings_for_jwt_and_body(self) -> None:
        session = _FakeSession()
        client = UpbitPrivateClient(
            access_key="access",
            secret_key="secret",
            session=session,  # type: ignore[arg-type]
        )
        client.place_order(
            market="KRW-BTC",
            side="bid",
            ord_type="limit",
            price=104068000.0,
            volume=4.8045508705846176e-05,
            time_in_force="post_only",
        )
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        body = dict(call.get("json") or {})
        self.assertEqual(body["price"], "104068000")
        self.assertEqual(body["volume"], "0.000048045508705846176")
        auth = str((call.get("headers") or {}).get("Authorization") or "")
        self.assertTrue(auth.startswith("Bearer "))
        token = auth.removeprefix("Bearer ").strip()
        header_raw = token.split(".", 1)[0]
        padded = header_raw + ("=" * (-len(header_raw) % 4))
        header = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        self.assertEqual(header["alg"], "HS512")


if __name__ == "__main__":
    unittest.main()

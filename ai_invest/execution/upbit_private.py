from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode, unquote

import requests


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _to_query_text(params: Mapping[str, Any]) -> str:
    items: list[tuple[str, str]] = []
    for key, raw in params.items():
        if raw is None:
            continue
        if isinstance(raw, bool):
            items.append((str(key), "true" if raw else "false"))
        elif isinstance(raw, (list, tuple)):
            for v in raw:
                if v is None:
                    continue
                items.append((str(key), str(v)))
        else:
            items.append((str(key), str(raw)))
    return unquote(urlencode(items, doseq=True))


class UpbitPrivateApiError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class UpbitOrderSnapshot:
    order_id: str
    market: str
    side: str
    ord_type: str
    state: str
    price: float | None
    volume: float | None
    remaining_volume: float | None
    executed_volume: float
    paid_fee: float
    raw: Mapping[str, Any]


class UpbitPrivateClient:
    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        base_url: str = "https://api.upbit.com",
        timeout_sec: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self._access_key = str(access_key).strip()
        self._secret_key = str(secret_key).strip()
        self._base_url = str(base_url).rstrip("/")
        self._timeout_sec = max(1.0, float(timeout_sec))
        self._session = session or requests.Session()
        if not self._access_key or not self._secret_key:
            raise UpbitPrivateApiError(message="Missing UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY")

    @classmethod
    def from_env(cls) -> UpbitPrivateClient:
        return cls(
            access_key=str(os.environ.get("UPBIT_ACCESS_KEY") or ""),
            secret_key=str(os.environ.get("UPBIT_SECRET_KEY") or ""),
            base_url=str(os.environ.get("UPBIT_API_BASE_URL") or "https://api.upbit.com"),
            timeout_sec=_as_float(os.environ.get("UPBIT_PRIVATE_TIMEOUT_SEC"), default=12.0),
        )

    def _jwt(self, *, params: Mapping[str, Any] | None = None) -> str:
        header = {"alg": "HS512", "typ": "JWT"}
        payload: dict[str, Any] = {"access_key": self._access_key, "nonce": str(uuid.uuid4())}
        params_map = dict(params or {})
        if params_map:
            query_text = _to_query_text(params_map)
            if query_text:
                payload["query_hash"] = hashlib.sha512(query_text.encode("utf-8")).hexdigest()
                payload["query_hash_alg"] = "SHA512"
        h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(self._secret_key.encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha512).digest()
        return f"{h}.{p}.{_b64url(sig)}"

    def _request(self, *, method: str, path: str, params: Mapping[str, Any] | None = None) -> Any:
        payload = dict(params or {})
        headers = {
            "Authorization": f"Bearer {self._jwt(params=payload)}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}{path}"
        try:
            if method in {"GET", "DELETE"}:
                resp = self._session.request(method, url, params=payload, headers=headers, timeout=self._timeout_sec)
            else:
                resp = self._session.request(method, url, json=payload, headers=headers, timeout=self._timeout_sec)
        except requests.RequestException as exc:
            raise UpbitPrivateApiError(message=f"Upbit private request error: {exc}") from exc

        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}

        if not resp.ok:
            msg = ""
            if isinstance(body, Mapping):
                err = body.get("error")
                if isinstance(err, Mapping):
                    msg = str(err.get("message") or err.get("name") or "")
            if not msg:
                msg = str(body)
            raise UpbitPrivateApiError(
                message=f"Upbit private API error ({resp.status_code}): {msg}",
                status_code=int(resp.status_code),
                payload=body,
            )
        return body

    def get_accounts(self) -> list[dict[str, Any]]:
        res = self._request(method="GET", path="/v1/accounts")
        if not isinstance(res, list):
            raise UpbitPrivateApiError(message="Unexpected /v1/accounts response shape", payload=res)
        return [row for row in res if isinstance(row, Mapping)]

    def get_order(self, *, order_id: str | None = None, identifier: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if order_id:
            params["uuid"] = str(order_id)
        if identifier:
            params["identifier"] = str(identifier)
        if not params:
            raise UpbitPrivateApiError(message="get_order requires order_id or identifier")
        res = self._request(method="GET", path="/v1/order", params=params)
        if not isinstance(res, Mapping):
            raise UpbitPrivateApiError(message="Unexpected /v1/order response shape", payload=res)
        return dict(res)

    def cancel_order(self, *, order_id: str | None = None, identifier: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if order_id:
            params["uuid"] = str(order_id)
        if identifier:
            params["identifier"] = str(identifier)
        if not params:
            raise UpbitPrivateApiError(message="cancel_order requires order_id or identifier")
        res = self._request(method="DELETE", path="/v1/order", params=params)
        if not isinstance(res, Mapping):
            raise UpbitPrivateApiError(message="Unexpected DELETE /v1/order response shape", payload=res)
        return dict(res)

    def place_order(
        self,
        *,
        market: str,
        side: str,
        ord_type: str,
        volume: float | None = None,
        price: float | None = None,
        time_in_force: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        side_n = str(side).strip().lower()
        if side_n not in {"bid", "ask"}:
            raise UpbitPrivateApiError(message=f"Unsupported side: {side}")
        ord_type_n = str(ord_type).strip().lower()
        if ord_type_n not in {"limit", "price", "market"}:
            raise UpbitPrivateApiError(message=f"Unsupported ord_type: {ord_type}")

        payload: dict[str, Any] = {"market": str(market).strip().upper(), "side": side_n, "ord_type": ord_type_n}
        if volume is not None:
            payload["volume"] = str(max(0.0, float(volume)))
        if price is not None:
            payload["price"] = str(max(0.0, float(price)))
        if time_in_force:
            payload["time_in_force"] = str(time_in_force).strip().lower()
        if identifier:
            payload["identifier"] = str(identifier)

        res = self._request(method="POST", path="/v1/orders", params=payload)
        if not isinstance(res, Mapping):
            raise UpbitPrivateApiError(message="Unexpected POST /v1/orders response shape", payload=res)
        return dict(res)

    @staticmethod
    def normalize_order(snapshot: Mapping[str, Any]) -> UpbitOrderSnapshot:
        return UpbitOrderSnapshot(
            order_id=str(snapshot.get("uuid") or ""),
            market=str(snapshot.get("market") or ""),
            side=str(snapshot.get("side") or "").upper(),
            ord_type=str(snapshot.get("ord_type") or "").lower(),
            state=str(snapshot.get("state") or "").lower(),
            price=_as_float(snapshot.get("price"), default=0.0) if snapshot.get("price") is not None else None,
            volume=_as_float(snapshot.get("volume"), default=0.0) if snapshot.get("volume") is not None else None,
            remaining_volume=(
                _as_float(snapshot.get("remaining_volume"), default=0.0)
                if snapshot.get("remaining_volume") is not None
                else None
            ),
            executed_volume=_as_float(snapshot.get("executed_volume"), default=0.0),
            paid_fee=_as_float(snapshot.get("paid_fee"), default=0.0),
            raw=dict(snapshot),
        )

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _parse_unix_ts_to_utc_iso(value: Any) -> str | None:
    try:
        ts = int(str(value).strip())
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _http_get_json(*, url: str, timeout_sec: int) -> dict[str, Any]:
    headers = {"User-Agent": "ai-invest/1.0 (macro-context)"}
    resp = requests.get(url, headers=headers, timeout=max(2, int(timeout_sec)))
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("response is not a JSON object")
    return data


def fetch_macro_context(*, timeout_sec: int = 6) -> dict[str, Any]:
    """Fetch light-weight macro context for prework/governance.

    Sources:
    - CoinGecko global market snapshot
    - Alternative.me Fear & Greed index
    """

    out: dict[str, Any] = {
        "as_of_utc": _utcnow_iso(),
        "status": "PARTIAL",
        "risk_mode": "UNKNOWN",
        "fear_greed_index": {
            "value": None,
            "classification": None,
            "timestamp_utc": None,
            "source": "alternative_me",
        },
        "crypto_market": {
            "btc_dominance_pct": None,
            "eth_dominance_pct": None,
            "total_market_cap_usd": None,
            "total_volume_usd": None,
            "market_cap_change_24h_usd_pct": None,
            "source": "coingecko_global",
        },
        "errors": [],
    }

    errors: list[str] = []

    # Fear & Greed index
    try:
        fng = _http_get_json(url="https://api.alternative.me/fng/?limit=1", timeout_sec=int(timeout_sec))
        rows = fng.get("data") if isinstance(fng.get("data"), list) else []
        row0 = rows[0] if rows else {}
        if isinstance(row0, dict):
            fg_value = _as_int(row0.get("value"), default=-1)
            if fg_value >= 0:
                out["fear_greed_index"]["value"] = int(fg_value)
            out["fear_greed_index"]["classification"] = str(row0.get("value_classification") or "").strip() or None
            out["fear_greed_index"]["timestamp_utc"] = _parse_unix_ts_to_utc_iso(row0.get("timestamp"))
    except Exception as exc:
        errors.append(f"fear_greed_fetch_failed:{str(exc)[:120]}")

    # Crypto global market snapshot
    try:
        cg = _http_get_json(url="https://api.coingecko.com/api/v3/global", timeout_sec=int(timeout_sec))
        data = cg.get("data") if isinstance(cg.get("data"), dict) else {}
        mcp = data.get("market_cap_percentage") if isinstance(data.get("market_cap_percentage"), dict) else {}
        total_cap = data.get("total_market_cap") if isinstance(data.get("total_market_cap"), dict) else {}
        total_vol = data.get("total_volume") if isinstance(data.get("total_volume"), dict) else {}
        out["crypto_market"]["btc_dominance_pct"] = _as_float(mcp.get("btc"), default=0.0) or None
        out["crypto_market"]["eth_dominance_pct"] = _as_float(mcp.get("eth"), default=0.0) or None
        out["crypto_market"]["total_market_cap_usd"] = _as_float(total_cap.get("usd"), default=0.0) or None
        out["crypto_market"]["total_volume_usd"] = _as_float(total_vol.get("usd"), default=0.0) or None
        out["crypto_market"]["market_cap_change_24h_usd_pct"] = (
            _as_float(data.get("market_cap_change_percentage_24h_usd"), default=0.0) or None
        )
    except Exception as exc:
        errors.append(f"crypto_global_fetch_failed:{str(exc)[:120]}")

    fg_v = out["fear_greed_index"].get("value")
    btc_dom = out["crypto_market"].get("btc_dominance_pct")
    try:
        fg_i = int(fg_v) if fg_v is not None else None
    except Exception:
        fg_i = None
    try:
        btc_dom_f = float(btc_dom) if btc_dom is not None else None
    except Exception:
        btc_dom_f = None

    if fg_i is not None and btc_dom_f is not None:
        if fg_i <= 30 or btc_dom_f >= 57.0:
            out["risk_mode"] = "RISK_OFF"
        elif fg_i >= 60 and btc_dom_f <= 53.0:
            out["risk_mode"] = "RISK_ON"
        else:
            out["risk_mode"] = "NEUTRAL"
    elif fg_i is not None:
        if fg_i <= 30:
            out["risk_mode"] = "RISK_OFF"
        elif fg_i >= 60:
            out["risk_mode"] = "RISK_ON"
        else:
            out["risk_mode"] = "NEUTRAL"
    elif btc_dom_f is not None:
        out["risk_mode"] = "RISK_OFF" if btc_dom_f >= 57.0 else "NEUTRAL"

    out["errors"] = list(errors)
    if not errors:
        out["status"] = "OK"
    elif len(errors) >= 2:
        out["status"] = "FAIL"
    else:
        out["status"] = "PARTIAL"
    return out


from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_invest.market_data.upbit_public import fetch_markets_all, fetch_tickers


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


def _as_int(value: Any, *, default: int) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, int):
            return int(value)
        return int(float(str(value).strip()))
    except Exception:
        return int(default)


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return bool(value)
    s = str(value or "").strip().lower()
    if not s:
        return bool(default)
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _chunks(items: Sequence[str], n: int) -> list[list[str]]:
    size = max(1, int(n))
    out: list[list[str]] = []
    cur: list[str] = []
    for s in items:
        cur.append(str(s))
        if len(cur) >= size:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


@dataclass(frozen=True)
class DynamicUniverseResult:
    symbols: list[str]
    source: str
    ranked_count: int
    total_krw_markets: int
    top24h_turnover: list[dict[str, float | str]]


def resolve_dynamic_universe(
    *,
    rules_raw: Mapping[str, Any],
    fallback_symbols: Sequence[str],
) -> DynamicUniverseResult:
    """Resolve candidate symbols for governance/prework.

    - default: returns static fallback symbols from rules.yaml
    - dynamic enabled: scans KRW universe and ranks by acc_trade_price_24h
    """

    fallback = [str(s).strip().upper() for s in list(fallback_symbols or []) if str(s).strip()]
    uv = rules_raw.get("universe") if isinstance(rules_raw, Mapping) else None
    dyn = (uv or {}).get("dynamic") if isinstance(uv, Mapping) else None

    if not isinstance(dyn, Mapping) or not _as_bool(dyn.get("enabled"), default=False):
        return DynamicUniverseResult(
            symbols=fallback,
            source="static",
            ranked_count=0,
            total_krw_markets=0,
            top24h_turnover=[],
        )

    quote = str(dyn.get("quote_currency") or "KRW").strip().upper() or "KRW"
    top_n = max(1, _as_int(dyn.get("top_n_by_24h_turnover"), default=40))
    max_candidates = max(1, _as_int(dyn.get("max_candidates"), default=12))
    min_turnover = _as_float(dyn.get("min_24h_turnover_krw"), default=0.0)
    exclude_set = {str(x).strip().upper() for x in list(dyn.get("exclude_symbols") or []) if str(x).strip()}
    include_set = {str(x).strip().upper() for x in list(dyn.get("include_symbols") or []) if str(x).strip()}

    try:
        markets_all = fetch_markets_all(is_details=False)
        all_symbols: list[str] = []
        for row in markets_all:
            market = str(row.get("market") or "").strip().upper()
            if not market.startswith(f"{quote}-"):
                continue
            if market in exclude_set:
                continue
            all_symbols.append(market)

        if include_set:
            all_symbols = [s for s in all_symbols if s in include_set]

        if not all_symbols:
            return DynamicUniverseResult(
                symbols=fallback,
                source="static_fallback(no_krw_markets)",
                ranked_count=0,
                total_krw_markets=0,
                top24h_turnover=[],
            )

        rows: list[dict[str, Any]] = []
        for batch in _chunks(all_symbols, 100):
            rows.extend(fetch_tickers(batch))

        scored: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row.get("market") or "").strip().upper()
            if not symbol:
                continue
            turnover = _as_float(row.get("acc_trade_price_24h"), default=0.0)
            if turnover < min_turnover:
                continue
            scored.append({"symbol": symbol, "turnover_24h_krw": turnover})
    except Exception as exc:
        return DynamicUniverseResult(
            symbols=fallback,
            source=f"static_fallback(api_error:{str(exc)[:80]})",
            ranked_count=0,
            total_krw_markets=0,
            top24h_turnover=[],
        )
    if not scored:
        return DynamicUniverseResult(
            symbols=fallback,
            source="static_fallback(no_ranked)",
            ranked_count=0,
            total_krw_markets=len(all_symbols),
            top24h_turnover=[],
        )

    scored.sort(key=lambda x: float(x.get("turnover_24h_krw") or 0.0), reverse=True)
    ranked = scored[:top_n]
    selected = [str(x.get("symbol")) for x in ranked[:max_candidates] if str(x.get("symbol") or "").strip()]

    # Keep deterministic uniqueness and guarantee fallback union.
    merged: list[str] = []
    seen: set[str] = set()
    for s in selected + fallback:
        sym = str(s).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(sym)

    return DynamicUniverseResult(
        symbols=merged if merged else fallback,
        source="dynamic_24h_turnover",
        ranked_count=len(ranked),
        total_krw_markets=len(all_symbols),
        top24h_turnover=[
            {"symbol": str(x.get("symbol")), "turnover_24h_krw": float(x.get("turnover_24h_krw") or 0.0)}
            for x in ranked[:20]
        ],
    )

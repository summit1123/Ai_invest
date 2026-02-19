from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote_plus

import requests
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class NewsHeadline:
    source: str
    title: str
    url: str
    published_at: str | None  # keep raw string (provider-dependent)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_name(tag: str) -> str:
    # "{namespace}tag" -> "tag"
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _find_first_text(elem: ET.Element, local_tag: str) -> str:
    for ch in list(elem):
        if _local_name(ch.tag) == local_tag and (ch.text or "").strip():
            return str(ch.text).strip()
    # fallback: nested search
    for ch in elem.iter():
        if _local_name(ch.tag) == local_tag and (ch.text or "").strip():
            return str(ch.text).strip()
    return ""


def _find_atom_link(elem: ET.Element) -> str:
    # Atom: <link rel="alternate" href="..."/>
    candidates: list[str] = []
    for ch in list(elem):
        if _local_name(ch.tag) != "link":
            continue
        href = (ch.attrib or {}).get("href") or ""
        if not href:
            continue
        rel = (ch.attrib or {}).get("rel") or ""
        if rel == "alternate":
            return href
        candidates.append(href)
    return candidates[0] if candidates else ""


def _parse_rss(xml_text: str, *, source: str, limit: int) -> list[NewsHeadline]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    root_name = _local_name(root.tag).lower()
    if root_name == "feed":
        return _parse_atom(xml_text, source=source, limit=limit)

    # RSS 2.0
    channel = None
    for ch in list(root):
        if _local_name(ch.tag).lower() == "channel":
            channel = ch
            break
    if channel is None:
        channel = root

    out: list[NewsHeadline] = []
    for item in channel.iter():
        if _local_name(item.tag).lower() != "item":
            continue
        title = _find_first_text(item, "title")
        link = _find_first_text(item, "link")
        pub = _find_first_text(item, "pubDate") or _find_first_text(item, "date") or _find_first_text(item, "updated")
        if not title or not link:
            continue
        out.append(NewsHeadline(source=source, title=title, url=link, published_at=pub or None))
        if len(out) >= limit:
            break
    return out


def _parse_atom(xml_text: str, *, source: str, limit: int) -> list[NewsHeadline]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    out: list[NewsHeadline] = []
    for entry in root.iter():
        if _local_name(entry.tag).lower() != "entry":
            continue
        title = _find_first_text(entry, "title")
        link = _find_atom_link(entry)
        pub = _find_first_text(entry, "published") or _find_first_text(entry, "updated")
        if not title or not link:
            continue
        out.append(NewsHeadline(source=source, title=title, url=link, published_at=pub or None))
        if len(out) >= limit:
            break
    return out


def _dedupe(headlines: list[NewsHeadline], *, limit: int) -> list[NewsHeadline]:
    seen: set[str] = set()
    out: list[NewsHeadline] = []
    for h in headlines:
        key = (h.url or "").strip() or (h.title or "").strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _merge_headline_groups(
    *,
    rss_items: list[NewsHeadline],
    web_items: list[NewsHeadline],
    limit: int,
    web_slot: int,
) -> list[NewsHeadline]:
    total_lim = max(1, int(limit))
    web_cap = max(0, min(int(web_slot), total_lim))
    rss_cap = max(0, total_lim - web_cap)

    picked_rss = _dedupe(rss_items, limit=max(rss_cap, total_lim))
    picked_web = _dedupe(web_items, limit=max(web_cap, total_lim))

    out: list[NewsHeadline] = []
    seen: set[str] = set()

    def _push(item: NewsHeadline) -> None:
        if len(out) >= total_lim:
            return
        key = (item.url or "").strip() or (item.title or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(item)

    for item in picked_rss[:rss_cap]:
        _push(item)
    for item in picked_web[:web_cap]:
        _push(item)

    if len(out) < total_lim:
        for item in picked_rss[rss_cap:]:
            _push(item)
            if len(out) >= total_lim:
                break
    if len(out) < total_lim:
        for item in picked_web[web_cap:]:
            _push(item)
            if len(out) >= total_lim:
                break

    return out


def fetch_rss_headlines(
    *,
    url: str,
    source: str,
    limit: int = 10,
    timeout_sec: int = 15,
) -> list[NewsHeadline]:
    headers = {"User-Agent": "Mozilla/5.0 (ai-invest research; +https://example.invalid)"}
    try:
        resp = requests.get(url, timeout=timeout_sec, headers=headers)
        if not resp.ok:
            return []
        text = resp.text or ""
    except Exception:
        return []

    items = _parse_rss(text, source=source, limit=int(limit))
    return _dedupe(items, limit=int(limit))


def google_news_rss_url(*, query: str, hl: str = "ko", gl: str = "KR", ceid: str = "KR:ko") -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def bing_news_rss_url(*, query: str, mkt: str = "ko-KR") -> str:
    q = quote_plus(query)
    market = quote_plus(str(mkt or "ko-KR"))
    return f"https://www.bing.com/news/search?q={q}&format=rss&mkt={market}"


def symbol_to_news_query(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    # Upbit spot symbol: KRW-BTC / BTC-ETH etc.
    base = sym.split("-", 1)[1] if "-" in sym else sym

    if base == "BTC":
        return "비트코인 OR bitcoin OR BTC"
    if base == "ETH":
        return "이더리움 OR ethereum OR ETH"
    if base:
        # Best-effort for altcoins.
        return f"{base} OR {base.lower()}"
    return "bitcoin OR BTC"


def _fetch_bing_news_headlines(*, query: str, limit: int, timeout_sec: int) -> list[NewsHeadline]:
    q = re.sub(r"\s+OR\s+", " ", str(query or ""), flags=re.IGNORECASE)
    q = re.sub(r"\s+", " ", q).strip()
    if q and "news" not in q.lower():
        q = f"{q} news"
    lim = max(1, int(limit))
    lim_kr = max(1, int(round(lim * 0.7)))
    lim_en = max(0, lim - lim_kr)
    urls: list[tuple[str, str, int]] = [
        ("Bing News KR", bing_news_rss_url(query=q, mkt="ko-KR"), lim_kr),
    ]
    if lim_en > 0:
        urls.append(("Bing News Global", bing_news_rss_url(query=q, mkt="en-US"), lim_en))

    out: list[NewsHeadline] = []
    for source, url, per_lim in urls:
        out.extend(fetch_rss_headlines(url=url, source=source, limit=per_lim, timeout_sec=int(timeout_sec)))
    return _dedupe(out, limit=lim)


def fetch_wqb_headlines(
    *,
    query: str,
    limit: int = 8,
    timeout_sec: int = 10,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> list[NewsHeadline]:
    """Fetch headlines from optional WQB search endpoint.

    Expected response shape (flexible):
    - {"results": [{title,url,source,published_at}, ...]}
    - {"items": [...]}
    - [...]
    """

    ep = str(endpoint or os.environ.get("WQB_SEARCH_ENDPOINT", "")).strip()
    if not ep:
        return []

    key = str(api_key or os.environ.get("WQB_SEARCH_API_KEY", "")).strip()
    headers = {"User-Agent": "Mozilla/5.0 (ai-invest research; +https://example.invalid)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        resp = requests.get(
            ep,
            params={"q": str(query), "query": str(query), "limit": int(limit)},
            timeout=int(timeout_sec),
            headers=headers,
        )
        if not resp.ok:
            return []
        payload = resp.json()
    except Exception:
        return []

    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = []
        for k in ("results", "items", "data", "documents"):
            v = payload.get(k)
            if isinstance(v, list):
                rows = v
                break
    else:
        rows = []

    out: list[NewsHeadline] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or row.get("name") or row.get("headline") or "").strip()
        url = str(row.get("url") or row.get("link") or row.get("href") or "").strip()
        if not title or not url:
            continue
        source = str(row.get("source") or row.get("site") or row.get("domain") or "WQB").strip() or "WQB"
        published_at = (
            str(
                row.get("published_at")
                or row.get("publishedAt")
                or row.get("pubDate")
                or row.get("date")
                or ""
            ).strip()
            or None
        )
        out.append(NewsHeadline(source=source, title=title, url=url, published_at=published_at))
        if len(out) >= int(limit):
            break
    return _dedupe(out, limit=int(limit))


def fetch_web_search_headlines(
    *,
    query: str,
    provider: str = "auto",
    limit: int = 8,
    timeout_sec: int = 10,
) -> list[NewsHeadline]:
    p = str(provider or "auto").strip().lower()
    lim = max(1, int(limit))
    to = max(3, int(timeout_sec))

    if p == "wqb":
        return fetch_wqb_headlines(query=query, limit=lim, timeout_sec=to)
    if p in {"bing", "bing_news", "bing_news_rss"}:
        return _fetch_bing_news_headlines(query=query, limit=lim, timeout_sec=to)

    # auto: prefer WQB if configured; otherwise Bing News RSS.
    wqb = fetch_wqb_headlines(query=query, limit=lim, timeout_sec=to)
    if wqb:
        return wqb
    return _fetch_bing_news_headlines(query=query, limit=lim, timeout_sec=to)


def _headline_channel(source: str) -> str:
    s = str(source or "").strip().lower()
    if "bing news" in s:
        return "web_search"
    if s == "wqb":
        return "web_search"
    return "rss"


def fetch_crypto_headlines(
    *,
    symbol: str,
    limit: int = 12,
    include_web_search: bool = False,
    web_search_provider: str = "auto",
    web_search_limit: int = 8,
    web_search_timeout_sec: int = 10,
    rss_timeout_sec: int = 15,
) -> list[dict[str, Any]]:
    """Fetch a small set of crypto headlines.

    Sources:
    - RSS feeds (Google News query + major crypto media RSS)
    - Optional web-search headlines (Bing News RSS or WQB endpoint)
    """
    q = symbol_to_news_query(symbol)
    urls: list[tuple[str, str, int]] = [
        ("Google News", google_news_rss_url(query=q), 8),
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 5),
        ("Cointelegraph", "https://cointelegraph.com/rss", 5),
    ]

    all_items: list[NewsHeadline] = []
    for source, url, per_lim in urls:
        all_items.extend(
            fetch_rss_headlines(
                url=url,
                source=source,
                limit=per_lim,
                timeout_sec=max(3, int(rss_timeout_sec)),
            )
        )

    web_items: list[NewsHeadline] = []
    if include_web_search:
        web_items = fetch_web_search_headlines(
            query=q,
            provider=str(web_search_provider or "auto"),
            limit=max(1, int(web_search_limit)),
            timeout_sec=max(3, int(web_search_timeout_sec)),
        )

    if include_web_search:
        total_limit = max(1, int(limit))
        desired_web = min(max(1, int(web_search_limit)), total_limit)
        # Keep at least half for RSS to avoid losing core crypto feeds.
        web_slot = min(desired_web, max(1, total_limit // 2))
        out = _merge_headline_groups(
            rss_items=all_items,
            web_items=web_items,
            limit=total_limit,
            web_slot=web_slot,
        )
    else:
        out = _dedupe(all_items, limit=int(limit))

    return [
        {
            "source": h.source,
            "channel": _headline_channel(h.source),
            "title": h.title,
            "url": h.url,
            "published_at": h.published_at,
            "fetched_at_utc": _utc_now_iso(),
        }
        for h in out
    ]


def normalize_headline_title(title: str) -> str:
    # Light cleanup for UI/Telegram (keep meaning, avoid over-normalization).
    t = str(title or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def summarize_headlines_text(headlines: list[Mapping[str, Any]], *, max_items: int = 8) -> str:
    lines: list[str] = []
    for h in list(headlines)[:max_items]:
        title = normalize_headline_title(str(h.get("title") or ""))
        src = str(h.get("source") or "")
        if not title:
            continue
        if src:
            lines.append(f"- [{src}] {title}")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines).strip()

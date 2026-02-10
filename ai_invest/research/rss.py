from __future__ import annotations

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


def fetch_crypto_headlines(
    *,
    symbol: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Fetch a small set of crypto headlines (RSS only; no full article content)."""
    q = symbol_to_news_query(symbol)
    urls: list[tuple[str, str, int]] = [
        ("Google News", google_news_rss_url(query=q), 8),
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 5),
        ("Cointelegraph", "https://cointelegraph.com/rss", 5),
    ]

    all_items: list[NewsHeadline] = []
    for source, url, per_lim in urls:
        all_items.extend(fetch_rss_headlines(url=url, source=source, limit=per_lim))

    out = _dedupe(all_items, limit=int(limit))
    return [
        {
            "source": h.source,
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


from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


_KEYWORD_WEIGHTS: dict[str, float] = {
    "hack": 0.95,
    "exploit": 0.95,
    "breach": 0.90,
    "liquidation": 0.85,
    "liquidations": 0.85,
    "outage": 0.85,
    "halt": 0.80,
    "ban": 0.80,
    "lawsuit": 0.70,
    "sec": 0.55,
    "fraud": 0.90,
    "default": 0.90,
    "bankruptcy": 0.95,
    "war": 0.80,
    "sanction": 0.70,
    "tariff": 0.60,
    "cpi": 0.45,
    "fomc": 0.45,
    "rate cut": 0.35,
    "rate hike": 0.55,
    "etf": 0.35,
    "regulation": 0.60,
    "regulatory": 0.60,
    "exchange": 0.35,
    "volatility": 0.30,
    "recession": 0.55,
}

_WATCHLIST_WEIGHTS: dict[str, float] = {
    "pause": 0.90,
    "recon": 0.75,
    "fail": 0.85,
    "risk": 0.45,
    "volatility": 0.35,
    "latency": 0.35,
    "spread": 0.30,
    "outage": 0.80,
}


def build_news_signal(
    *,
    headlines: Sequence[Mapping[str, Any]] | None,
    risk_watchlist: Sequence[str] | None = None,
) -> dict[str, Any]:
    compact_titles: list[str] = []
    keyword_hits: Counter[str] = Counter()
    per_title_scores: list[float] = []

    for row in list(headlines or [])[:12]:
        if not isinstance(row, Mapping):
            continue
        title = _as_text(row.get("title"))
        if not title:
            continue
        compact_titles.append(title)
        lower_title = title.lower()
        title_score = 0.0
        for keyword, weight in _KEYWORD_WEIGHTS.items():
            if keyword in lower_title:
                keyword_hits[keyword] += 1
                title_score = max(float(title_score), float(weight))
        per_title_scores.append(float(title_score))

    watch_hits: Counter[str] = Counter()
    watchlist_score = 0.0
    compact_watchlist: list[str] = []
    for item in list(risk_watchlist or [])[:8]:
        text = _as_text(item)
        if not text:
            continue
        compact_watchlist.append(text)
        lower_text = text.lower()
        row_score = 0.0
        for keyword, weight in _WATCHLIST_WEIGHTS.items():
            if keyword in lower_text:
                watch_hits[keyword] += 1
                row_score = max(float(row_score), float(weight))
        watchlist_score = max(float(watchlist_score), float(row_score))

    headline_count = len(compact_titles)
    high_impact_count = sum(1 for score in per_title_scores if float(score) >= 0.70)
    avg_title_score = (
        float(sum(per_title_scores)) / float(len(per_title_scores))
        if per_title_scores
        else 0.0
    )
    density_score = _clamp(float(headline_count) / 8.0, 0.0, 1.0)
    impact_ratio = (
        float(high_impact_count) / float(max(1, headline_count))
        if headline_count > 0
        else 0.0
    )

    shock_score = (
        (0.50 * float(avg_title_score))
        + (0.20 * float(impact_ratio))
        + (0.15 * float(density_score))
        + (0.15 * float(watchlist_score))
    )
    shock_score = _clamp(float(shock_score), 0.0, 1.0)

    if shock_score >= 0.75:
        severity = "HIGH"
    elif shock_score >= 0.45:
        severity = "ELEVATED"
    else:
        severity = "NORMAL"

    return {
        "enabled": bool(compact_titles or compact_watchlist),
        "headline_count": int(headline_count),
        "high_impact_count": int(high_impact_count),
        "severity": str(severity),
        "shock_score": float(shock_score),
        "avg_title_score": float(avg_title_score),
        "density_score": float(density_score),
        "watchlist_score": float(watchlist_score),
        "keyword_hits": dict(keyword_hits),
        "watchlist_hits": dict(watch_hits),
        "top_titles": compact_titles[:5],
        "risk_watchlist": compact_watchlist[:5],
    }

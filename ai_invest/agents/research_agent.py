from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_invest.agents.prompt_contract import research_daily_system_prompt
from ai_invest.config.llm_router import LLMRoute
from ai_invest.llm.openai_http import (
    OpenAIConfigError,
    OpenAIRequestError,
    OpenAITextResult,
    openai_generate_text,
)
from ai_invest.research.news_signal import build_news_signal
from ai_invest.research.rss import summarize_headlines_text


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = str(value or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _clip(text: str, n: int) -> str:
    text = str(text or "")
    if len(text) <= n:
        return text
    if n <= 3:
        return text[:n]
    return text[: n - 3] + "..."


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def _json_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text)
        if len(out) >= max_items:
            break
    return out


@dataclass(frozen=True)
class ResearchBrief:
    title: str
    summary: str
    key_findings: list[str]
    risk_watchlist: list[str]
    next_actions: list[str]
    used_llm: bool
    llm_meta: Mapping[str, Any] | None


def _deterministic_brief(
    *,
    symbol: str,
    snapshot: Mapping[str, Any],
    features: Mapping[str, Any],
    ops: Mapping[str, Any],
    headlines: Sequence[Mapping[str, Any]],
) -> ResearchBrief:
    last_price = snapshot.get("last_price")
    spread_bps = snapshot.get("spread_bps")
    rsi_14 = features.get("rsi_14")
    atr_pct = features.get("atr_pct")
    vol_z = features.get("vol_zscore")

    pause_state = bool((ops.get("pause") or {}).get("paused") or ops.get("pause_state") or False)
    recon_status = str(
        ((ops.get("latest_reconciliation") or {}).get("status"))
        or ops.get("reconciliation_status")
        or "OK"
    ).upper()
    risk_watchlist_seed: list[str] = []
    if pause_state:
        risk_watchlist_seed.append("ops pause active")
    if recon_status == "FAIL":
        risk_watchlist_seed.append("reconciliation fail")
    news_signal = build_news_signal(headlines=headlines, risk_watchlist=risk_watchlist_seed)

    summary = (
        f"{symbol} daily research brief "
        f"price={last_price} spread_bps={spread_bps} "
        f"RSI14={rsi_14} ATR%={atr_pct} VolZ={vol_z} "
        f"news_severity={news_signal.get('severity')} "
        f"shock={float(news_signal.get('shock_score') or 0.0):.2f}"
    )

    findings: list[str] = []
    if isinstance(spread_bps, (int, float)) and float(spread_bps) >= 10.0:
        findings.append(f"Liquidity warning: spread {float(spread_bps):.2f}bps")
    if isinstance(atr_pct, (int, float)) and float(atr_pct) >= 2.5:
        findings.append(f"Volatility warning: ATR% {float(atr_pct):.2f}")

    headline_text = summarize_headlines_text(list(headlines), max_items=6)
    if headline_text:
        findings.append("Top headlines:")
        findings.extend([line for line in headline_text.splitlines() if line.strip()])

    if str(news_signal.get("severity") or "NORMAL").upper() != "NORMAL":
        findings.append(
            "News risk elevated: "
            f"severity={news_signal.get('severity')} shock={float(news_signal.get('shock_score') or 0.0):.2f}"
        )

    risk_watchlist: list[str] = []
    if pause_state:
        risk_watchlist.append("System pause is active")
    if recon_status == "FAIL":
        risk_watchlist.append("Reconciliation failed")
    if float(news_signal.get("shock_score") or 0.0) >= 0.45:
        risk_watchlist.append("News shock risk elevated")
    if not risk_watchlist:
        risk_watchlist.append("No critical operating risk detected")

    next_actions: list[str] = []
    if pause_state or recon_status == "FAIL":
        next_actions.append("ops: inspect pause log and latest reconciliation before resuming")
    next_actions.append("research: monitor follow-up headlines and separate facts from rumors")
    next_actions.append("quant: review spread and ATR jump before relaxing entry conditions")

    return ResearchBrief(
        title="Daily research brief",
        summary=summary,
        key_findings=findings[:14] if findings else [],
        risk_watchlist=risk_watchlist[:8],
        next_actions=next_actions[:8],
        used_llm=False,
        llm_meta=None,
    )


def research_agent_daily_brief(
    *,
    symbol: str,
    snapshot: Mapping[str, Any],
    features: Mapping[str, Any],
    ops: Mapping[str, Any],
    headlines: Sequence[Mapping[str, Any]] | None = None,
    llm_route: LLMRoute | None = None,
) -> ResearchBrief:
    """Research Agent: qualitative context + news (LLM optional).

    - Output is stored as an asset (`agent_daily_reports` + `events(RESEARCH_DAILY_BRIEF)`).
    - Must not be required for real-time execution.
    """

    headlines = list(headlines or [])
    fallback = _deterministic_brief(
        symbol=symbol,
        snapshot=snapshot,
        features=features,
        ops=ops,
        headlines=headlines,
    )

    if llm_route is not None:
        use_llm = bool(llm_route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    else:
        llm_enabled = _parse_bool(os.environ.get("RESEARCH_LLM_ENABLED", ""), default=True)
        use_llm = llm_enabled and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if not use_llm:
        return fallback

    ctx: dict[str, Any] = {
        "symbol": symbol,
        "snapshot": dict(snapshot),
        "features": dict(features),
        "ops": dict(ops),
        "headlines": [
            {
                "source": item.get("source"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
            }
            for item in headlines[:20]
            if isinstance(item, Mapping)
        ],
        "fallback": {
            "summary": fallback.summary,
            "risk_watchlist": fallback.risk_watchlist,
            "key_findings": fallback.key_findings,
        },
    }

    system_prompt = research_daily_system_prompt()
    user_prompt = "Input JSON:\n" + _safe_json(ctx)

    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=(llm_route.model if llm_route else None),
            api_style=(llm_route.api_style if llm_route else None),
            reasoning_effort=(llm_route.reasoning_effort if llm_route else None),
            temperature=(float(llm_route.temperature) if (llm_route and llm_route.temperature is not None) else 0.2),
            timeout_sec=(int(llm_route.timeout_sec) if (llm_route and llm_route.timeout_sec) else 40),
        )
        raw = res.text.strip()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("non-object json")

        summary = str(data.get("summary") or "").strip() or fallback.summary
        key_findings = _json_list(data.get("key_findings"), max_items=8) or fallback.key_findings
        risk_watchlist = _json_list(data.get("risk_watchlist"), max_items=8) or fallback.risk_watchlist
        next_actions = _json_list(data.get("next_actions"), max_items=8) or fallback.next_actions

        return ResearchBrief(
            title=fallback.title,
            summary=_clip(summary, 800),
            key_findings=key_findings[:8],
            risk_watchlist=risk_watchlist[:8],
            next_actions=next_actions[:8],
            used_llm=True,
            llm_meta={
                "model": res.model,
                "endpoint": res.endpoint,
                "usage": {
                    "input_tokens": getattr(res.usage, "input_tokens", None) if res.usage else None,
                    "output_tokens": getattr(res.usage, "output_tokens", None) if res.usage else None,
                    "total_tokens": getattr(res.usage, "total_tokens", None) if res.usage else None,
                    "response_id": res.response_id,
                },
            },
        )
    except (OpenAIConfigError, OpenAIRequestError, Exception):
        return fallback

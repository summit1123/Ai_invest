from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, OpenAITextResult, openai_generate_text
from ai_invest.research.rss import summarize_headlines_text


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = str(value or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def _json_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for x in value:
        s = str(x or "").strip()
        if not s:
            continue
        out.append(s)
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
    recon_status = str(((ops.get("latest_reconciliation") or {}).get("status")) or ops.get("reconciliation_status") or "OK").upper()

    summary = (
        f"{symbol} 시장 브리프: 현재가={last_price} spread_bps={spread_bps} "
        f"RSI14={rsi_14} ATR%={atr_pct} VolZ={vol_z}"
    )

    findings: list[str] = []
    if isinstance(spread_bps, (int, float)) and float(spread_bps) >= 10:
        findings.append(f"유동성 경고: 스프레드 {float(spread_bps):.2f}bps")
    if isinstance(atr_pct, (int, float)) and float(atr_pct) >= 2.5:
        findings.append(f"변동성 경고: ATR% {float(atr_pct):.2f}")

    # Headlines as findings (titles only; raw list stored elsewhere).
    hl_text = summarize_headlines_text(list(headlines), max_items=6)
    if hl_text:
        findings.append("주요 뉴스:")
        findings.extend([line for line in hl_text.splitlines() if line.strip()])

    risk_watchlist: list[str] = []
    if pause_state:
        risk_watchlist.append("시스템 PAUSE 상태(실행 차단)")
    if recon_status == "FAIL":
        risk_watchlist.append("정합성 FAIL(운영 리스크)")
    if not risk_watchlist:
        risk_watchlist.append("특이 리스크 없음(기계적 체크 기준)")

    next_actions: list[str] = []
    if pause_state or recon_status == "FAIL":
        next_actions.append("ops: pause_log / reconciliation_checks 확인 후 원인 제거")
    next_actions.append("research: 주요 뉴스 헤드라인 추적(과장/루머 필터링)")
    next_actions.append("quant: spread/ATR 급등 시 진입 보수적으로 조정 검토(다음 회의 안건)")

    return ResearchBrief(
        title="일일 리서치 브리프(뉴스+시장)",
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
) -> ResearchBrief:
    """Research Agent: qualitative context + news (LLM optional).

    - Output is stored as an asset (`agent_daily_reports` + `events(RESEARCH_DAILY_BRIEF)`).
    - Must not be required for real-time execution.
    """

    headlines = list(headlines or [])
    fallback = _deterministic_brief(symbol=symbol, snapshot=snapshot, features=features, ops=ops, headlines=headlines)

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
            {"source": h.get("source"), "title": h.get("title"), "url": h.get("url"), "published_at": h.get("published_at")}
            for h in headlines[:20]
            if isinstance(h, Mapping)
        ],
        "fallback": {"summary": fallback.summary, "risk_watchlist": fallback.risk_watchlist},
    }

    system_prompt = (
        "너는 자동투자 시스템의 Research Agent다.\n"
        "역할:\n"
        "- 시장/뉴스를 조사해 사람이 이해 가능한 한국어 브리프를 만든다.\n"
        "- 매수/매도 같은 방향성 직접 제안은 금지(신호는 Quant/Market Agent가 담당).\n"
        "- 불확실하면 '미확인'이라고 쓴다.\n"
        "\n"
        "출력은 반드시 JSON 1개만 출력:\n"
        "{\n"
        "  \"summary\": \"...\",\n"
        "  \"key_findings\": [\"...\"],\n"
        "  \"risk_watchlist\": [\"...\"],\n"
        "  \"next_actions\": [\"...\"]\n"
        "}\n"
        "제약:\n"
        "- 각 리스트는 최대 8개\n"
        "- summary는 1~3문장\n"
    )

    user_prompt = "입력 JSON:\n" + _safe_json(ctx)

    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
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

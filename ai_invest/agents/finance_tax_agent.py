from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.agents.prompt_contract import finance_monthly_system_prompt
from ai_invest.config.llm_router import LLMRoute
from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, OpenAITextResult, openai_generate_text


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = str(value or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def _clip(s: str, n: int) -> str:
    s = str(s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _json_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = str(item or "").strip()
        if not s:
            continue
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class FinanceTaxReview:
    tax_export_status: str
    validation_report: Mapping[str, Any]
    discrepancy_alerts: list[str]
    summary: str
    used_llm: bool
    llm_meta: Mapping[str, Any] | None


def _deterministic_review(
    *,
    year: int,
    month: int,
    tax_export_run: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
) -> FinanceTaxReview:
    run = _as_mapping(tax_export_run)
    man = _as_mapping(manifest)
    status = str(run.get("status") or "UNKNOWN").upper()
    vr = _as_mapping(man.get("validation_report"))
    errs = _json_list(man.get("errors"), max_items=12)

    discrepancy_alerts: list[str] = []
    if status in {"FAILED", "ERROR"}:
        discrepancy_alerts.append("월말 산출 실패: tax export 상태가 FAILED")
    if errs:
        discrepancy_alerts.extend([f"manifest 오류: {e}" for e in errs[:5]])

    delta = vr.get("realized_minus_ledger_net_approx")
    try:
        d = abs(float(delta)) if delta is not None else 0.0
        if d >= 1000.0:
            discrepancy_alerts.append(f"실현손익-원장 근사 차이 경고: {d:.0f} KRW")
    except Exception:
        pass

    if not discrepancy_alerts:
        discrepancy_alerts.append("특이 불일치 경고 없음(결정론 검증 기준)")

    summary = (
        f"{year:04d}-{month:02d} 월말 결산 요약: 상태={status}, "
        f"오류수={len(errs)}, 경고수={len(discrepancy_alerts)}"
    )
    return FinanceTaxReview(
        tax_export_status=status,
        validation_report=dict(vr),
        discrepancy_alerts=discrepancy_alerts[:12],
        summary=summary,
        used_llm=False,
        llm_meta=None,
    )


def finance_tax_monthly_review(
    *,
    year: int,
    month: int,
    tax_export_run: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
    llm_route: LLMRoute | None = None,
) -> FinanceTaxReview:
    fallback = _deterministic_review(year=year, month=month, tax_export_run=tax_export_run, manifest=manifest)

    if llm_route is not None:
        use_llm = bool(llm_route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    else:
        llm_enabled = _parse_bool(os.environ.get("FINANCE_TAX_LLM_ENABLED", ""), default=True)
        use_llm = llm_enabled and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if not use_llm:
        return fallback

    ctx = {
        "period": f"{year:04d}-{month:02d}",
        "tax_export_run": dict(tax_export_run or {}),
        "manifest": dict(manifest or {}),
        "fallback": {
            "tax_export_status": fallback.tax_export_status,
            "validation_report": dict(fallback.validation_report),
            "discrepancy_alerts": list(fallback.discrepancy_alerts),
            "summary": fallback.summary,
        },
    }
    system_prompt = finance_monthly_system_prompt()
    user_prompt = "입력 JSON:\n" + _safe_json(ctx)
    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=(llm_route.model if llm_route else None),
            api_style=(llm_route.api_style if llm_route else None),
            reasoning_effort=(llm_route.reasoning_effort if llm_route else None),
            temperature=(float(llm_route.temperature) if (llm_route and llm_route.temperature is not None) else 0.1),
            timeout_sec=(int(llm_route.timeout_sec) if (llm_route and llm_route.timeout_sec) else 120),
        )
        data = json.loads(res.text.strip())
        if not isinstance(data, Mapping):
            raise ValueError("non-object json")

        status = str(data.get("tax_export_status") or fallback.tax_export_status).strip().upper()
        vr_raw = data.get("validation_report")
        validation_report = dict(vr_raw) if isinstance(vr_raw, Mapping) else dict(fallback.validation_report)
        discrepancy_alerts = _json_list(data.get("discrepancy_alerts"), max_items=12) or list(fallback.discrepancy_alerts)
        summary = _clip(str(data.get("summary") or fallback.summary), 1200)

        return FinanceTaxReview(
            tax_export_status=status,
            validation_report=validation_report,
            discrepancy_alerts=discrepancy_alerts[:12],
            summary=summary,
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

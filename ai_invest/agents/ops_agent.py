from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class OpsOpinion:
    system_state: str  # OK / DEGRADED / FAIL
    veto: bool
    alerts: list[str]
    reconciliation_status: str  # OK / WARN / FAIL
    reason_codes: list[str]
    reason: dict[str, Any]


def ops_agent_opine(payload: Mapping[str, Any]) -> OpsOpinion:
    ops = payload.get("ops") or {}
    recon = str(ops.get("reconciliation_status") or "OK").upper()
    rate_limit = bool(ops.get("rate_limit_alert") or False)
    paused = bool(ops.get("pause_state") or False)

    alerts: list[str] = []
    reason_codes: list[str] = []
    veto = False

    if recon == "FAIL":
        veto = True
        reason_codes.append(ReasonCode.RG_RECON_FAIL.value)
        alerts.append("reconciliation_status=FAIL")
    if rate_limit:
        veto = True
        reason_codes.append(ReasonCode.RG_RATE_LIMIT_STORM.value)
        alerts.append("rate_limit_alert=true")
    if paused:
        veto = True
        reason_codes.append(ReasonCode.OP_PAUSE_TRIGGERED.value)
        alerts.append("pause_state=true")

    if not reason_codes:
        reason_codes.append(ReasonCode.RG_PASS.value)

    system_state = "OK" if not veto else "DEGRADED"
    if recon == "FAIL":
        system_state = "FAIL"

    return OpsOpinion(
        system_state=system_state,
        veto=veto,
        alerts=alerts,
        reconciliation_status=recon,
        reason_codes=reason_codes[:3],
        reason={"ops": ops},
    )


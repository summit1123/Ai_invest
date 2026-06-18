from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from zoneinfo import ZoneInfo

from ai_invest.notifications import telegram_client
from ai_invest.notifications.templates import render
from ai_invest.storage.postgres import PostgresRepo


KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _ts_payload() -> dict[str, str]:
    now = _utcnow()
    return {"ts_utc": now.isoformat(), "ts_kst": now.astimezone(KST).isoformat()}


def _stable_hash(obj: Any) -> str:
    try:
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        raw = str(obj).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


@dataclass(frozen=True)
class NotificationContext:
    send_telegram: bool
    notify_safe_enabled: bool
    notify_safe_hold: bool
    notify_safe_change_only: bool
    dedupe_within_sec: int


def load_notification_context() -> NotificationContext:
    dedupe = os.environ.get("NOTIFICATION_DEDUPE_WITHIN_SEC", "").strip()
    try:
        dedupe_sec = int(dedupe) if dedupe else 60
    except Exception:
        dedupe_sec = 60
    return NotificationContext(
        send_telegram=parse_bool(os.environ.get("SEND_TELEGRAM", "")),
        notify_safe_enabled=parse_bool(os.environ.get("NOTIFY_SAFE_DECISION_ENABLED", "0")),
        notify_safe_hold=parse_bool(os.environ.get("NOTIFY_SAFE_DECISION_HOLD", "")),
        notify_safe_change_only=parse_bool(os.environ.get("NOTIFY_SAFE_DECISION_CHANGE_ONLY", "1")),
        dedupe_within_sec=max(0, dedupe_sec),
    )


class NotificationService:
    def __init__(self, repo: PostgresRepo, ctx: NotificationContext | None = None) -> None:
        self._repo = repo
        self._ctx = ctx or load_notification_context()
        self._safe_decision_state_cache: dict[str, str] = {}

    def _safe_decision_state_signature(
        self,
        *,
        symbol: str,
        action: str,
        reasons: list[str],
        context: Mapping[str, Any],
    ) -> str:
        c = dict(context or {})
        action_u = str(action or "").upper()
        reasons_n = sorted(str(r or "").strip().upper() for r in list(reasons or []) if str(r or "").strip())
        state = {
            "symbol": str(symbol or "").upper(),
            "action": action_u,
            "reasons": reasons_n,
            "market_signal": str(c.get("market_signal") or "").upper(),
            "regime_trade_allowed": bool(c.get("regime_trade_allowed")),
            "risk_veto": bool(c.get("risk_veto")),
            "ops_veto": bool(c.get("ops_veto")),
            "reconciliation_status": str(c.get("reconciliation_status") or "").upper(),
            "pause_state": bool(c.get("pause_state")),
            "trade_plan_slot_key": str(c.get("trade_plan_slot_key") or ""),
            "trade_plan_target_pct": c.get("trade_plan_target_pct"),
            "capital_tier": str(c.get("capital_tier") or ""),
            "capital_target_cap_pct": c.get("capital_target_cap_pct"),
        }
        return _stable_hash(state)

    def _deliver_telegram(
        self,
        *,
        event_id: uuid.UUID,
        template_id: str,
        severity: str,
        chat_id: str,
        payload: Mapping[str, Any],
        dedupe_key: str | None = None,
    ) -> None:
        delivery_id = uuid.uuid4()
        text = render(template_id, payload)

        if not chat_id:
            self._repo.insert_notification_delivery(
                delivery_id=delivery_id,
                event_id=event_id,
                channel="TELEGRAM",
                template_id=template_id,
                severity=severity,
                status="FAILED",
                attempt_count=0,
                last_error="chat_id missing",
                dedupe_key=dedupe_key,
                payload={"event": payload},
                sent_at=None,
            )
            return

        if dedupe_key and self._ctx.dedupe_within_sec > 0:
            if self._repo.was_notification_sent_recently(dedupe_key=dedupe_key, within_sec=self._ctx.dedupe_within_sec):
                self._repo.insert_notification_delivery(
                    delivery_id=delivery_id,
                    event_id=event_id,
                    channel="TELEGRAM",
                    template_id=template_id,
                    severity=severity,
                    status="SKIPPED",
                    attempt_count=0,
                    last_error=f"dedupe skip within {self._ctx.dedupe_within_sec}s",
                    dedupe_key=dedupe_key,
                    payload={"telegram": {"chat_id": chat_id}, "event": payload},
                    sent_at=None,
                )
                return

        if not self._ctx.send_telegram:
            self._repo.insert_notification_delivery(
                delivery_id=delivery_id,
                event_id=event_id,
                channel="TELEGRAM",
                template_id=template_id,
                severity=severity,
                status="PENDING",
                attempt_count=0,
                last_error="SEND_TELEGRAM disabled",
                dedupe_key=dedupe_key,
                payload={"telegram": {"chat_id": chat_id}, "event": payload},
                sent_at=None,
            )
            return

        result = telegram_client.send_message(chat_id=chat_id, text=text)
        status = "SENT" if result.ok else "FAILED"
        self._repo.insert_notification_delivery(
            delivery_id=delivery_id,
            event_id=event_id,
            channel="TELEGRAM",
            template_id=template_id,
            severity=severity,
            status=status,
            attempt_count=1,
            last_error=result.error,
            dedupe_key=dedupe_key,
            payload={"telegram": {"chat_id": chat_id, "message_id": result.message_id}, "event": payload},
            sent_at=_utcnow() if result.ok else None,
        )

    def notify_safe_decision(
        self,
        *,
        event_id: uuid.UUID,
        symbol: str,
        action: str,
        reasons: list[str],
        run_id: uuid.UUID,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._ctx.notify_safe_enabled:
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_safe_decision",
                severity="NORMAL",
                status="SKIPPED",
                attempt_count=0,
                last_error="safe decision notification disabled (NOTIFY_SAFE_DECISION_ENABLED=false)",
                dedupe_key=f"DECISION:SAFE:DISABLED:{symbol}",
                payload={"symbol": symbol, "action": action, "reasons": reasons, "context": dict(context or {})},
                sent_at=None,
            )
            return

        action = action.upper()
        safe_ctx = dict(context or {})
        if self._ctx.notify_safe_change_only:
            symbol_key = str(symbol or "").upper()
            sig = self._safe_decision_state_signature(
                symbol=symbol_key,
                action=action,
                reasons=list(reasons or []),
                context=safe_ctx,
            )
            prev = self._safe_decision_state_cache.get(symbol_key)
            if prev == sig:
                self._repo.insert_notification_delivery(
                    delivery_id=uuid.uuid4(),
                    event_id=event_id,
                    channel="TELEGRAM",
                    template_id="tpl_safe_decision",
                    severity="NORMAL",
                    status="SKIPPED",
                    attempt_count=0,
                    last_error="unchanged safe decision state",
                    dedupe_key=f"DECISION:SAFE:{symbol_key}:{sig}",
                    payload={
                        "symbol": symbol,
                        "action": action,
                        "reasons": reasons,
                        "context": safe_ctx,
                        "skip_mode": "change_only",
                    },
                    sent_at=None,
                )
                return
            self._safe_decision_state_cache[symbol_key] = sig

        if action == "HOLD" and not self._ctx.notify_safe_hold:
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_safe_decision",
                severity="NORMAL",
                status="SKIPPED",
                attempt_count=0,
                last_error="HOLD suppressed (NOTIFY_SAFE_DECISION_HOLD=false)",
                dedupe_key=f"DECISION:SAFE:{symbol}:{action}",
                payload={"symbol": symbol, "action": action, "reasons": reasons, "run_id": str(run_id)},
                sent_at=None,
            )
            return
        try:
            chat_id = telegram_client.chat_id_trading()
        except Exception as exc:  # pragma: no cover
            chat_id = ""
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_safe_decision",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"symbol": symbol, "action": action}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_safe_decision",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"DECISION:SAFE:{symbol}:{action}",
            payload={
                **_ts_payload(),
                "symbol": symbol,
                "action": action,
                "reasons": reasons,
                "context": safe_ctx,
                "run_id": str(run_id),
            },
        )

    def notify_fill(
        self,
        *,
        event_id: uuid.UUID,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        fee_currency: str,
    ) -> None:
        quote_currency = str(symbol or "").split("-", 1)[0] if "-" in str(symbol or "") else str(fee_currency or "")
        total_value = float(qty) * float(price)
        total_fee = float(fee)
        fee_rate = (total_fee / total_value) if float(total_value) > 0.0 else 0.0
        fee_rate_pct = float(fee_rate) * 100.0
        fee_rate_bps = float(fee_rate) * 10000.0
        try:
            chat_id = telegram_client.chat_id_trading()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_fill_notice",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"symbol": symbol, "side": side}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_fill_notice",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"FILL:{symbol}:{side}:{event_id}",
            payload={
                **_ts_payload(),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "fee": fee,
                "fee_currency": fee_currency,
                "total_value": total_value,
                "total_fee": total_fee,
                "quote_currency": quote_currency,
                "fee_rate_pct": fee_rate_pct,
                "fee_rate_bps": fee_rate_bps,
            },
        )

    def notify_recon_fail(
        self,
        *,
        event_id: uuid.UUID,
        symbol: str,
        diff_summary: str | None,
        run_id: uuid.UUID,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_ops()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_recon_fail",
                severity="CRITICAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"symbol": symbol}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_recon_fail",
            severity="CRITICAL",
            chat_id=chat_id,
            dedupe_key=f"OPS:RECON_FAIL:{symbol}",
            payload={
                **_ts_payload(),
                "symbol": symbol,
                "diff_summary": diff_summary,
                "run_id": str(run_id),
            },
        )

    def notify_pause(
        self,
        *,
        event_id: uuid.UUID,
        symbol: str,
        reason_type: str,
        run_id: uuid.UUID,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_ops()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_pause_critical",
                severity="CRITICAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"symbol": symbol, "reason_type": reason_type}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_pause_critical",
            severity="CRITICAL",
            chat_id=chat_id,
            dedupe_key=f"OPS:PAUSE:{reason_type}:{symbol}",
            payload={
                **_ts_payload(),
                "symbol": symbol,
                "reason_type": reason_type,
                "run_id": str(run_id),
            },
        )

    def notify_tax_export_done(self, *, event_id: uuid.UUID, export_id: str, year: int, month: int) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_tax_export_done",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"export_id": export_id}},
                sent_at=None,
            )
            return
        period_label = f"{year:04d}-{month:02d}"
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_tax_export_done",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"FINANCE:TAX_EXPORT_DONE:{period_label}:{export_id}",
            payload={
                **_ts_payload(),
                "period_label": period_label,
                "export_id": export_id,
            },
        )

    def notify_tax_export_fail(
        self, *, event_id: uuid.UUID, export_id: str, year: int, month: int, errors: list[str]
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_tax_export_fail",
                severity="HIGH",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"export_id": export_id, "errors": errors}},
                sent_at=None,
            )
            return
        period_label = f"{year:04d}-{month:02d}"
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_tax_export_fail",
            severity="HIGH",
            chat_id=chat_id,
            dedupe_key=f"FINANCE:TAX_EXPORT_FAIL:{period_label}:{export_id}",
            payload={
                **_ts_payload(),
                "period_label": period_label,
                "export_id": export_id,
                "errors": errors[:5],
            },
        )

    def notify_finance_monthly_review(
        self,
        *,
        event_id: uuid.UUID,
        period_label: str,
        tax_export_status: str,
        discrepancy_alerts: list[str],
        summary: str,
        manifest_ref: str,
        llm_used: bool,
        llm_model: str | None,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_finance_monthly_review",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"period_label": period_label}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_finance_monthly_review",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"FINANCE:MONTHLY_REVIEW:{period_label}:{manifest_ref}",
            payload={
                **_ts_payload(),
                "period_label": period_label,
                "tax_export_status": tax_export_status,
                "discrepancy_alerts": list(discrepancy_alerts or [])[:5],
                "summary": summary,
                "manifest_ref": manifest_ref,
                "llm_used": bool(llm_used),
                "llm_model": llm_model,
            },
        )

    def notify_research_daily_brief(
        self,
        *,
        event_id: uuid.UUID,
        brief_date: str,
        summary: str,
        risk_watchlist: list[str],
        headlines: list[Mapping[str, Any]] | None = None,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_research()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_research_daily_brief",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"brief_date": brief_date}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_research_daily_brief",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"RESEARCH:DAILY_BRIEF:{brief_date}",
            payload={
                **_ts_payload(),
                "brief_date": brief_date,
                "summary": summary,
                "risk_watchlist": risk_watchlist[:5],
                "headlines": list(headlines or [])[:3],
            },
        )

    def notify_daily_review(
        self,
        *,
        event_id: uuid.UUID,
        day: str,
        realized_pnl: float,
        fees_paid: float,
        trades_count: int,
        max_drawdown: float | None,
        improvement_title: str | None = None,
        improvement_reason: str | None = None,
        suggested_changes: list[str] | None = None,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_daily_review",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"day": day}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_daily_review",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"REVIEW:DAILY:{day}",
            payload={
                **_ts_payload(),
                "day": day,
                "realized_pnl": realized_pnl,
                "fees_paid": fees_paid,
                "trades_count": trades_count,
                "max_drawdown": max_drawdown,
                "improvement_title": str(improvement_title or "").strip(),
                "improvement_reason": str(improvement_reason or "").strip(),
                "suggested_changes": [str(x).strip() for x in list(suggested_changes or []) if str(x).strip()][:5],
            },
        )

    def notify_weekly_priority(
        self,
        *,
        event_id: uuid.UUID,
        week_label: str,
        priority_title: str,
        hypothesis: str,
        owner: str,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_weekly_priority",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"week_label": week_label}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_weekly_priority",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"GOV:WEEKLY_PRIORITY:{week_label}:{priority_title}",
            payload={
                **_ts_payload(),
                "week_label": week_label,
                "priority_title": priority_title,
                "hypothesis": hypothesis,
                "owner": owner,
            },
        )

    def notify_weekly_review(
        self,
        *,
        event_id: uuid.UUID,
        week_label: str,
        weekly_pnl: float,
        win_rate: float,
        loss_tags_top3: str,
        rule_patch_status: str,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_review()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_weekly_review",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"week_label": week_label}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_weekly_review",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"REVIEW:WEEKLY:{week_label}",
            payload={
                **_ts_payload(),
                "week_label": week_label,
                "weekly_pnl": weekly_pnl,
                "win_rate": win_rate,
                "loss_tags_top3": loss_tags_top3,
                "rule_patch_status": rule_patch_status,
            },
        )

    def notify_meeting_summary(
        self,
        *,
        event_id: uuid.UUID,
        meeting_id: str,
        summary: str,
        assistant_minutes: str | None = None,
        assistant_meta: Mapping[str, Any] | None = None,
        trade_plan: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_meeting()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_meeting_summary",
                severity="NORMAL",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"meeting_id": meeting_id}},
                sent_at=None,
            )
            return
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_meeting_summary",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"MEETING:SUMMARY:{meeting_id}",
            payload={
                **_ts_payload(),
                "meeting_id": meeting_id,
                "summary": summary,
                "assistant_minutes": assistant_minutes,
                "assistant_meta": dict(assistant_meta or {}),
                "trade_plan": dict(trade_plan or {}),
            },
        )

    def notify_meeting_action_items(self, *, event_id: uuid.UUID, meeting_id: str, items: list[Mapping[str, Any]]) -> None:
        try:
            chat_id = telegram_client.chat_id_meeting()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_meeting_action_items",
                severity="HIGH",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={"event": {"meeting_id": meeting_id}},
                sent_at=None,
            )
            return
        action_hash = _stable_hash(items)
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_meeting_action_items",
            severity="HIGH",
            chat_id=chat_id,
            dedupe_key=f"MEETING:ACTION:{meeting_id}:{action_hash}",
            payload={
                **_ts_payload(),
                "meeting_id": meeting_id,
                "items": items[:10],
            },
        )

    def notify_trade_plan_set(
        self,
        *,
        event_id: uuid.UUID,
        meeting_id: str | None,
        slot_key: str,
        symbol: str,
        target_position_pct: float,
        valid_from_kst: str | None,
        valid_to_kst: str | None,
        allowed_actions: Mapping[str, Any] | None = None,
        rebalance_band_pct: float | None = None,
        cooldown_minutes: int | None = None,
        constraints: Mapping[str, Any] | None = None,
        rationale_summary: str | None = None,
        activation_status: str | None = None,
        activation_gate: Mapping[str, Any] | None = None,
        runtime_entry_policy: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            chat_id = telegram_client.chat_id_meeting()
        except Exception as exc:  # pragma: no cover
            self._repo.insert_notification_delivery(
                delivery_id=uuid.uuid4(),
                event_id=event_id,
                channel="TELEGRAM",
                template_id="tpl_trade_plan_set",
                severity="HIGH",
                status="FAILED",
                attempt_count=0,
                last_error=f"telegram config error: {exc}",
                dedupe_key=None,
                payload={
                    "event": {
                        "slot_key": slot_key,
                        "symbol": symbol,
                        "runtime_entry_policy": dict(runtime_entry_policy or {}),
                    }
                },
                sent_at=None,
            )
            return

        dedupe = _stable_hash(
            {
                "slot_key": slot_key,
                "symbol": symbol,
                "target": target_position_pct,
                "valid_to": valid_to_kst,
                "allowed": dict(allowed_actions or {}),
                "runtime_entry_policy": dict(runtime_entry_policy or {}),
            }
        )
        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_trade_plan_set",
            severity="HIGH",
            chat_id=chat_id,
            dedupe_key=f"GOV:TRADE_PLAN:{slot_key}:{symbol}:{dedupe}",
            payload={
                **_ts_payload(),
                "meeting_id": meeting_id,
                "slot_key": slot_key,
                "symbol": symbol,
                "target_position_pct": target_position_pct,
                "valid_from_kst": valid_from_kst,
                "valid_to_kst": valid_to_kst,
                "allowed_actions": dict(allowed_actions or {}),
                "rebalance_band_pct": rebalance_band_pct,
                "cooldown_minutes": cooldown_minutes,
                "constraints": dict(constraints or {}),
                "rationale_summary": rationale_summary or "",
                "activation_status": activation_status,
                "activation_gate": dict(activation_gate or {}),
                "runtime_entry_policy": dict(runtime_entry_policy or {}),
            },
        )

    def notify_engineering_change_announced(
        self,
        *,
        event_id: uuid.UUID,
        change_id: str,
        summary_lines: list[str],
        activation_mode: str,
        rollback_hint: str,
    ) -> None:
        chat_id = ""
        error_msg = ""
        try:
            chat_id = telegram_client.chat_id_engineering()
        except Exception as exc:  # pragma: no cover
            error_msg = str(exc)
            try:
                chat_id = telegram_client.chat_id_meeting()
            except Exception as exc2:  # pragma: no cover
                error_msg = f"{error_msg}; fallback={exc2}"
                self._repo.insert_notification_delivery(
                    delivery_id=uuid.uuid4(),
                    event_id=event_id,
                    channel="TELEGRAM",
                    template_id="tpl_engineering_change_announced",
                    severity="NORMAL",
                    status="FAILED",
                    attempt_count=0,
                    last_error=f"telegram config error: {error_msg}",
                    dedupe_key=None,
                    payload={"event": {"change_id": change_id}},
                    sent_at=None,
                )
                return

        self._deliver_telegram(
            event_id=event_id,
            template_id="tpl_engineering_change_announced",
            severity="NORMAL",
            chat_id=chat_id,
            dedupe_key=f"ENG:CHANGE:{change_id}",
            payload={
                **_ts_payload(),
                "change_id": str(change_id),
                "summary_lines": [str(x) for x in list(summary_lines or [])[:3]],
                "activation_mode": str(activation_mode or "PAPER/HOLD"),
                "rollback_hint": str(rollback_hint or "git revert <commit_sha> 후 오케스트레이터 재시작"),
            },
        )

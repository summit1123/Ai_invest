from __future__ import annotations

import unittest

from ai_invest.notifications.templates import render


class NotificationTemplateTests(unittest.TestCase):
    def test_safe_decision_human_readable_blocks(self) -> None:
        text = render(
            "tpl_safe_decision",
            {
                "ts_kst": "2026-02-11T08:00:00+09:00",
                "symbol": "KRW-BTC",
                "action": "HOLD",
                "reasons": ["RG_SPREAD_TOO_WIDE", "RG_RISK_VETO"],
                "context": {
                    "spread_bps": 12.34,
                    "rsi_14": 55.2,
                    "atr_pct": 1.21,
                    "vol_zscore": 1.8,
                    "regime_trade_allowed": True,
                    "risk_veto": True,
                    "ops_veto": False,
                    "reconciliation_status": "OK",
                    "market_signal": "BUY",
                    "market_confidence": 0.63,
                    "trade_plan_slot_key": "2026-02-11 08:00",
                    "trade_plan_target_pct": 10,
                },
            },
        )

        self.assertIn("결론", text)
        self.assertIn("스프레드 과다", text)
        self.assertIn("리스크 게이트 차단", text)
        self.assertIn("운영 안내", text)

    def test_trade_plan_set_template_contains_constraints(self) -> None:
        text = render(
            "tpl_trade_plan_set",
            {
                "ts_kst": "2026-02-11T08:00:00+09:00",
                "meeting_id": "m-1",
                "slot_key": "2026-02-11 08:00",
                "symbol": "KRW-BTC",
                "target_position_pct": 12.5,
                "valid_from_kst": "2026-02-11T08:00:00+09:00",
                "valid_to_kst": "2026-02-11T16:00:00+09:00",
                "allowed_actions": {"buy": True, "sell": True},
                "rebalance_band_pct": 2.0,
                "cooldown_minutes": 20,
                "constraints": {
                    "max_spread_bps": 8.0,
                    "max_slippage_bps": 10.0,
                    "max_position_pct": 20.0,
                },
                "rationale_summary": "리스크 상한 유지 + 스프레드 정상화",
            },
        )

        self.assertIn("거버넌스 플랜 보고", text)
        self.assertIn("KRW-BTC", text)
        self.assertIn("스프레드", text)
        self.assertIn("근거 요약", text)

    def test_fill_notice_includes_total_amount_and_total_fee(self) -> None:
        text = render(
            "tpl_fill_notice",
            {
                "ts_kst": "2026-03-03T11:15:00+09:00",
                "symbol": "KRW-NEAR",
                "side": "BUY",
                "qty": 146.99028714,
                "price": 2043,
                "fee": 150.15057831,
                "fee_currency": "KRW",
                "total_value": 300_301.15663082,
                "total_fee": 150.15057831,
                "quote_currency": "KRW",
                "fee_rate_pct": 0.05,
                "fee_rate_bps": 5.0,
            },
        )

        self.assertIn("체결 수량: 146.99028714", text)
        self.assertIn("체결 가격: 2,043 KRW", text)
        self.assertIn("수수료: 150.15057831 KRW (요율 0.0500%, 5.00 bps)", text)
        self.assertIn("총 체결 금액: 300,301 KRW", text)
        self.assertIn("총 수수료: 150.15057831 KRW", text)

    def test_research_daily_brief_includes_links(self) -> None:
        text = render(
            "tpl_research_daily_brief",
            {
                "brief_date": "2026-02-11",
                "summary": "시장 변동성 확대",
                "risk_watchlist": ["매크로 일정", "거래소 장애"],
                "headlines": [
                    {"title": "BTC ETF 유입", "url": "https://example.com/a"},
                    {"title": "거래소 점검", "url": "https://example.com/b"},
                ],
            },
        )

        self.assertIn("참고 기사", text)
        self.assertIn("https://example.com/a", text)

    def test_finance_monthly_review_template(self) -> None:
        text = render(
            "tpl_finance_monthly_review",
            {
                "ts_kst": "2026-02-28T23:10:00+09:00",
                "period_label": "2026-02",
                "tax_export_status": "COMPLETED",
                "manifest_ref": "exp-123",
                "llm_used": True,
                "llm_model": "gpt-5.2-pro",
                "summary": "원장/실현손익 차이 경미, 정합성 통과",
                "discrepancy_alerts": ["실현손익-원장 근사 차이 500 KRW"],
            },
        )
        self.assertIn("정산 리뷰", text)
        self.assertIn("gpt-5.2-pro", text)
        self.assertIn("2026-02", text)

    def test_engineering_change_template(self) -> None:
        text = render(
            "tpl_engineering_change_announced",
            {
                "ts_kst": "2026-02-12T11:00:00+09:00",
                "change_id": "tpv2-20260212-1",
                "activation_mode": "PAPER/HOLD",
                "summary_lines": ["게이트 decision 분리", "execution_plan 추가", "역호환 유지"],
                "rollback_hint": "git revert <sha> 후 재시작",
            },
        )
        self.assertIn("엔지니어링 공지", text)
        self.assertIn("tpv2-20260212-1", text)
        self.assertIn("PAPER/HOLD", text)

    def test_daily_review_template_contains_improvement_advice(self) -> None:
        text = render(
            "tpl_daily_review",
            {
                "day": "2026-02-26",
                "realized_pnl": -3200,
                "fees_paid": 1800,
                "trades_count": 11,
                "max_drawdown": 5400,
                "improvement_title": "수수료 누수 차단이 1순위",
                "improvement_reason": "수수료가 손익을 잠식",
                "suggested_changes": [
                    "`strategy.alpha_score.entry_alpha` +0.03",
                    "`governance.micro_mode.min_alpha` +0.02",
                ],
            },
        )
        self.assertIn("오늘 수정 우선순위", text)
        self.assertIn("수수료 누수 차단", text)
        self.assertIn("권장 수정 항목", text)


if __name__ == "__main__":
    unittest.main()

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

        self.assertIn("한 줄 요약", text)
        self.assertIn("스프레드 과다", text)
        self.assertIn("리스크 차단", text)
        self.assertIn("운영자 확인", text)

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

        self.assertIn("트레이드 플랜 확정", text)
        self.assertIn("KRW-BTC", text)
        self.assertIn("max_spread", text)
        self.assertIn("근거 요약", text)

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

        self.assertIn("주요 링크", text)
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
        self.assertIn("Finance/Tax 리뷰", text)
        self.assertIn("gpt-5.2-pro", text)
        self.assertIn("2026-02", text)


if __name__ == "__main__":
    unittest.main()

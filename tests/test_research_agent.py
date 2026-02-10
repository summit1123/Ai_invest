from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_invest.agents.research_agent import research_agent_daily_brief
from ai_invest.llm.openai_http import OpenAITextResult, OpenAIUsage


class ResearchAgentTests(unittest.TestCase):
    def test_fallback_when_llm_disabled(self) -> None:
        with patch.dict(os.environ, {"RESEARCH_LLM_ENABLED": "false"}, clear=False):
            out = research_agent_daily_brief(
                symbol="KRW-BTC",
                snapshot={"last_price": 1, "spread_bps": 2.0},
                features={"rsi_14": 55.0, "atr_pct": 1.1, "vol_zscore": 1.7},
                ops={"pause_state": False, "reconciliation_status": "OK"},
                headlines=[{"source": "x", "title": "t", "url": "u"}],
            )
        self.assertFalse(out.used_llm)
        self.assertIn("KRW-BTC", out.summary)

    def test_llm_json_is_parsed(self) -> None:
        fake = OpenAITextResult(
            text='{"summary":"요약","key_findings":["a"],"risk_watchlist":["b"],"next_actions":["c"]}',
            model="gpt-5",
            endpoint="responses",
            usage=OpenAIUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            response_id="resp_1",
        )
        with patch.dict(os.environ, {"RESEARCH_LLM_ENABLED": "true", "OPENAI_API_KEY": "test"}, clear=False):
            with patch("ai_invest.agents.research_agent.openai_generate_text", return_value=fake):
                out = research_agent_daily_brief(
                    symbol="KRW-BTC",
                    snapshot={"last_price": 1, "spread_bps": 2.0},
                    features={"rsi_14": 55.0, "atr_pct": 1.1, "vol_zscore": 1.7},
                    ops={"pause_state": False, "reconciliation_status": "OK"},
                    headlines=[],
                )
        self.assertTrue(out.used_llm)
        self.assertEqual(out.summary, "요약")
        self.assertEqual(out.key_findings, ["a"])
        self.assertEqual(out.risk_watchlist, ["b"])
        self.assertEqual(out.next_actions, ["c"])


if __name__ == "__main__":
    unittest.main()


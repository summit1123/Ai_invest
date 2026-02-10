from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_invest.agents.secretary_agent import generate_meeting_minutes
from ai_invest.llm.openai_http import OpenAITextResult, OpenAIUsage


class SecretaryAgentTests(unittest.TestCase):
    def test_deterministic_fallback_when_llm_disabled(self) -> None:
        session = {"meeting_id": "m1", "meeting_type": "DAILY_STRATEGY", "summary": "short"}
        messages = [{"sender_agent": "ops_agent", "message_type": "CLAIM", "content": "ok"}]
        with patch.dict(os.environ, {"SECRETARY_LLM_ENABLED": "false"}, clear=False):
            out = generate_meeting_minutes(session=session, messages=messages)
        self.assertFalse(out.used_llm)
        self.assertIn("회의록", out.text)

    def test_llm_path_is_used_when_available(self) -> None:
        session = {"meeting_id": "m1", "meeting_type": "DAILY_STRATEGY", "summary": "short"}
        messages = [{"sender_agent": "ops_agent", "message_type": "CLAIM", "content": "ok"}]

        fake = OpenAITextResult(
            text="LLM 회의록",
            model="gpt-5",
            endpoint="responses",
            usage=OpenAIUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            response_id="resp_1",
        )
        with patch.dict(os.environ, {"SECRETARY_LLM_ENABLED": "true", "OPENAI_API_KEY": "test"}, clear=False):
            with patch("ai_invest.agents.secretary_agent.openai_generate_text", return_value=fake):
                out = generate_meeting_minutes(session=session, messages=messages)
        self.assertTrue(out.used_llm)
        self.assertEqual(out.text, "LLM 회의록")
        self.assertEqual(out.model, "gpt-5")


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_invest.llm.openai_http import OpenAIConfigError, openai_generate_text


class _FakeResp:
    def __init__(self, *, ok: bool, status_code: int, json_data: object, text: str = "") -> None:
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or ""

    def json(self):  # type: ignore[override]
        return self._json_data


class OpenAIHttpTests(unittest.TestCase):
    def test_missing_api_key_raises(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with self.assertRaises(OpenAIConfigError):
                openai_generate_text(system_prompt="x", user_prompt="y", model="gpt-5")

    def test_responses_api_parses_text(self) -> None:
        fake = _FakeResp(
            ok=True,
            status_code=200,
            json_data={
                "id": "resp_1",
                "model": "gpt-5",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "회의록 요약"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            },
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test", "OPENAI_LLM_MODEL": "gpt-5", "OPENAI_BASE_URL": "https://example.com/v1"},
            clear=False,
        ):
            with patch("ai_invest.llm.openai_http.requests.post", return_value=fake) as post:
                res = openai_generate_text(system_prompt="s", user_prompt="u")
        self.assertEqual(res.endpoint, "responses")
        self.assertEqual(res.text, "회의록 요약")
        self.assertEqual(res.model, "gpt-5")
        self.assertEqual(res.response_id, "resp_1")
        self.assertTrue(post.called)

    def test_auto_falls_back_to_chat_completions(self) -> None:
        def side_effect(url: str, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if "/responses" in url:
                return _FakeResp(ok=False, status_code=404, json_data={"error": "not found"}, text="not found")
            return _FakeResp(
                ok=True,
                status_code=200,
                json_data={
                    "id": "chatcmpl_1",
                    "model": "gpt-5",
                    "choices": [{"message": {"role": "assistant", "content": "chat output"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                },
            )

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test", "OPENAI_LLM_MODEL": "gpt-5", "OPENAI_BASE_URL": "https://example.com/v1"},
            clear=False,
        ):
            with patch("ai_invest.llm.openai_http.requests.post", side_effect=side_effect):
                res = openai_generate_text(system_prompt="s", user_prompt="u")
        self.assertEqual(res.endpoint, "chat.completions")
        self.assertEqual(res.text, "chat output")


if __name__ == "__main__":
    unittest.main()


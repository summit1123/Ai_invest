#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, openai_generate_text  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="LLM smoke test (OpenAI HTTP).")
    p.add_argument("--prompt", type=str, default="회의록 테스트: 아래 내용을 2줄로 요약해줘. 내용=시장 점검/리스크/결정")
    p.add_argument("--model", type=str, default="")
    args = p.parse_args()

    load_dotenv()
    try:
        res = openai_generate_text(
            system_prompt="너는 테스트용 요약 봇이다. 한국어로만 답해라.",
            user_prompt=str(args.prompt),
            model=args.model.strip() or None,
            temperature=0.2,
        )
        print("[OK] LLM call succeeded")
        print(f"- endpoint: {res.endpoint}")
        print(f"- model: {res.model}")
        if res.usage:
            print(f"- usage: {res.usage}")
        print("")
        print(res.text)
        return 0
    except (OpenAIConfigError, OpenAIRequestError) as exc:
        print(f"[FAIL] {exc}")
        if getattr(exc, "status_code", None):
            print(f"- status_code: {getattr(exc, 'status_code')}")
        if getattr(exc, "body", None):
            print(f"- body: {getattr(exc, 'body')}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse

from ai_invest.config.dotenv import load_dotenv
from ai_invest.notifications.telegram_client import chat_id_engineering, send_message


def main() -> int:
    p = argparse.ArgumentParser(description="Send engineering commit report to Telegram.")
    p.add_argument("--commit", type=str, required=True)
    p.add_argument("--message", type=str, default="")
    p.add_argument("--tests", type=str, default="pytest: (unknown)")
    p.add_argument("--extra", type=str, default="")
    args = p.parse_args()

    load_dotenv()

    lines = [
        "[엔지니어링] 커밋 보고",
        f"- commit: {args.commit.strip()}",
    ]
    if args.message.strip():
        lines.append(f"- message: {args.message.strip()}")
    if args.tests.strip():
        lines.append(f"- tests: {args.tests.strip()}")
    if args.extra.strip():
        lines.append(f"- extra: {args.extra.strip()}")

    res = send_message(chat_id=chat_id_engineering(), text="\n".join(lines) + "\n")
    if not res.ok:
        print(f"[FAIL] telegram send failed: {res.error}")
        return 2
    print(f"[OK] telegram sent (message_id={res.message_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


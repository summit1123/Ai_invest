#!/usr/bin/env python3
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.storage.postgres import PostgresRepo  # noqa: E402


def main() -> int:
    load_dotenv()
    repo = PostgresRepo()

    env = dict(**__import__("os").environ)

    def upsert(channel_type: str, room_name: str, team_scope: str, key_env: str) -> None:
        room_key = (env.get(key_env) or "").strip()
        if not room_key:
            return
        repo.upsert_communication_room(
            room_id=uuid.uuid4(),
            channel_type=channel_type,
            room_key=room_key,
            room_name=room_name,
            team_scope=team_scope,
            is_active=True,
            meta={"source": "env", "key_env": key_env},
        )

    # Telegram rooms
    upsert("TELEGRAM", "ops-critical", "OPS", "TELEGRAM_CHAT_ID_OPS")
    upsert("TELEGRAM", "trading-feed", "TRADING", "TELEGRAM_CHAT_ID_TRADING")
    upsert("TELEGRAM", "review-report", "REVIEW", "TELEGRAM_CHAT_ID_REVIEW")
    upsert("TELEGRAM", "research-daily", "RESEARCH", "TELEGRAM_CHAT_ID_RESEARCH")
    upsert("TELEGRAM", "agent-meeting", "MEETING", "TELEGRAM_CHAT_ID_MEETING")
    upsert("TELEGRAM", "engineering-change", "ENGINEERING", "TELEGRAM_CHAT_ID_ENGINEERING")

    # Slack rooms (optional; UI visibility even if sending isn't implemented yet)
    upsert("SLACK", "ops-critical", "OPS", "SLACK_CHANNEL_ID_OPS")
    upsert("SLACK", "trading-feed", "TRADING", "SLACK_CHANNEL_ID_TRADING")
    upsert("SLACK", "review-report", "REVIEW", "SLACK_CHANNEL_ID_REVIEW")
    upsert("SLACK", "research-daily", "RESEARCH", "SLACK_CHANNEL_ID_RESEARCH")
    upsert("SLACK", "agent-meeting", "MEETING", "SLACK_CHANNEL_ID_MEETING")
    upsert("SLACK", "engineering-change", "ENGINEERING", "SLACK_CHANNEL_ID_ENGINEERING")
    upsert("SLACK", "governance", "GOVERNANCE", "SLACK_CHANNEL_ID_GOVERNANCE")

    items = repo.fetch_communication_rooms(limit=50)
    print(f"[완료] communication_rooms upserted. rooms={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


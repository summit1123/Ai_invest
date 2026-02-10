#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


@dataclass
class TestResult:
    name: str
    ok: bool
    detail: str


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def to_psycopg_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_upbit_jwt(access_key: str, secret_key: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    h = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret_key.encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
    s = b64url(signature)
    return f"{h}.{p}.{s}"


def test_db(env: dict[str, str]) -> TestResult:
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        return TestResult("DB", False, "POSTGRES_DSN 누락")

    dsn = to_psycopg_dsn(dsn)
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_database(), current_user")
                db, user = cur.fetchone()
                cur.execute("select extname from pg_extension where extname='vector'")
                vector_exists = bool(cur.fetchone())
        if not vector_exists:
            return TestResult("DB", False, f"연결 성공({db}/{user}), vector 확장 없음")
        return TestResult("DB", True, f"연결 성공({db}/{user}), vector 확장 확인")
    except Exception as exc:
        return TestResult("DB", False, f"연결 실패: {exc}")


def test_telegram(env: dict[str, str]) -> TestResult:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID_OPS", "")
    if not token or not chat_id:
        return TestResult("Telegram", False, "TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID_OPS 누락")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = "[운영] 스모크 테스트: 텔레그램 알림 경로 정상"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        data = resp.json()
        if resp.ok and data.get("ok"):
            message_id = data.get("result", {}).get("message_id")
            return TestResult("Telegram", True, f"전송 성공(message_id={message_id})")
        return TestResult("Telegram", False, f"전송 실패: {data}")
    except Exception as exc:
        return TestResult("Telegram", False, f"호출 실패: {exc}")


def test_upbit_auth(env: dict[str, str]) -> TestResult:
    access_key = env.get("UPBIT_ACCESS_KEY", "")
    secret_key = env.get("UPBIT_SECRET_KEY", "")
    if not access_key or not secret_key:
        return TestResult("Upbit", False, "UPBIT_ACCESS_KEY 또는 UPBIT_SECRET_KEY 누락")

    token = build_upbit_jwt(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get("https://api.upbit.com/v1/accounts", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return TestResult("Upbit", True, f"인증 조회 성공(accounts={len(data)})")
        return TestResult("Upbit", False, f"인증 조회 실패(status={resp.status_code}, body={resp.text[:200]})")
    except Exception as exc:
        return TestResult("Upbit", False, f"호출 실패: {exc}")


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    results = [
        test_db(env),
        test_telegram(env),
        test_upbit_auth(env),
    ]

    failed = 0
    for result in results:
        status = "성공" if result.ok else "실패"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.ok:
            failed += 1

    if failed:
        print(f"[요약] 실패 {failed}건")
        return 2

    print("[요약] 모든 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

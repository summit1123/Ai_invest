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

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "[FAIL] Missing Python dependency: psycopg.\n"
        "Run one of the following first:\n"
        "  1) python -m pip install -e .\n"
        "  2) python -m pip install 'psycopg[binary]'\n"
    ) from exc
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
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def to_psycopg_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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
        return TestResult("DB", False, "POSTGRES_DSN missing")
    require_pgvector = parse_bool(env.get("REQUIRE_PGVECTOR", ""))

    dsn = to_psycopg_dsn(dsn)
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_database(), current_user")
                db, user = cur.fetchone()
                cur.execute("select extname from pg_extension where extname='vector'")
                vector_exists = bool(cur.fetchone())
        if not vector_exists and require_pgvector:
            return TestResult("DB", False, f"Connected ({db}/{user}) but pgvector extension missing")
        if not vector_exists:
            return TestResult("DB", True, f"Connected ({db}/{user}), pgvector not installed")
        return TestResult("DB", True, f"Connected ({db}/{user}), pgvector installed")
    except Exception as exc:
        return TestResult("DB", False, f"Connection failed: {exc}")


def test_telegram(env: dict[str, str]) -> TestResult:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID_OPS", "")
    if not token or not chat_id:
        return TestResult("Telegram", False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID_OPS missing")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = "[ops] smoke test notification path OK"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        data = resp.json()
        if resp.ok and data.get("ok"):
            message_id = data.get("result", {}).get("message_id")
            return TestResult("Telegram", True, f"Sent successfully (message_id={message_id})")
        return TestResult("Telegram", False, f"Send failed: {data}")
    except Exception as exc:
        return TestResult("Telegram", False, f"Request failed: {exc}")


def test_upbit_auth(env: dict[str, str]) -> TestResult:
    access_key = env.get("UPBIT_ACCESS_KEY", "")
    secret_key = env.get("UPBIT_SECRET_KEY", "")
    if not access_key or not secret_key:
        return TestResult("Upbit", False, "UPBIT_ACCESS_KEY or UPBIT_SECRET_KEY missing")

    token = build_upbit_jwt(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get("https://api.upbit.com/v1/accounts", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return TestResult("Upbit", True, f"Authenticated successfully (accounts={len(data)})")
        return TestResult(
            "Upbit",
            False,
            f"Auth failed (status={resp.status_code}, body={resp.text[:200]})",
        )
    except Exception as exc:
        return TestResult("Upbit", False, f"Request failed: {exc}")


def main() -> int:
    if not ENV_PATH.exists():
        print("[FAIL] .env file not found.")
        return 1

    env = load_env(ENV_PATH)
    results = [
        test_db(env),
        test_telegram(env),
        test_upbit_auth(env),
    ]

    failed = 0
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.ok:
            failed += 1

    if failed:
        print(f"[SUMMARY] {failed} checks failed")
        return 2

    print("[SUMMARY] All smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

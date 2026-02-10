#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import psycopg


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def to_psycopg_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        print("[실패] POSTGRES_DSN이 비어 있습니다.")
        return 1

    parsed = urlparse(to_psycopg_dsn(dsn))
    host = parsed.hostname or ""
    port = parsed.port or 5432
    db_name = parsed.path.lstrip("/") or ""

    if host not in {"localhost", "127.0.0.1"}:
        print(f"[경고] 현재 DSN host는 로컬이 아닙니다: {host}")
    else:
        print(f"[정보] 로컬 Postgres 대상 확인: host={host}, port={port}, db={db_name}")

    try:
        with psycopg.connect(to_psycopg_dsn(dsn)) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_user, current_database()")
                user, db = cur.fetchone()
                print(f"[성공] DB 연결 성공: user={user}, db={db}")

                cur.execute("select extname from pg_extension where extname='vector'")
                vector_ok = bool(cur.fetchone())
                if vector_ok:
                    print("[성공] pgvector 확장 활성화 확인")
                else:
                    print("[실패] pgvector 확장이 없습니다. SQL 실행 필요: CREATE EXTENSION IF NOT EXISTS vector;")
                    return 2
    except Exception as exc:
        print(f"[실패] DB 연결 실패: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

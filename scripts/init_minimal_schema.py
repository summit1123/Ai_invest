#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCHEMA_PATH = ROOT / "sql" / "minimal_schema.sql"


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


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1
    if not SCHEMA_PATH.exists():
        print("[실패] sql/minimal_schema.sql 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        print("[실패] POSTGRES_DSN 값이 비어 있습니다.")
        return 1

    dsn = to_psycopg_dsn(dsn)
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        print("[성공] 최소 스키마 적용 완료")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"[실패] 스키마 적용 실패: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "[실패] Python 의존성이 설치되어 있지 않습니다(psycopg 누락).\n"
        "아래 중 하나로 실행하세요:\n"
        "  1) uv sync && .venv/bin/python scripts/check_postgres_local.py\n"
        "  2) uv run python scripts/check_postgres_local.py\n"
    ) from exc


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


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        print("[실패] POSTGRES_DSN이 비어 있습니다.")
        return 1
    require_pgvector = parse_bool(env.get("REQUIRE_PGVECTOR", ""))

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
                    msg = (
                        "[경고] pgvector 확장이 없습니다(선택).\n"
                        " - vector 기능이 필요하면 `sql/optional_pgvector.sql`을 적용하세요.\n"
                        " - pgvector를 필수로 강제하려면 .env에 REQUIRE_PGVECTOR=true 를 설정하세요."
                    )
                    if require_pgvector:
                        print(msg.replace("[경고]", "[실패]"))
                        return 2
                    print(msg)
    except Exception as exc:
        print(f"[실패] DB 연결 실패: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

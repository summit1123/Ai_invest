#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "[실패] Python 의존성이 설치되어 있지 않습니다(psycopg 누락).\n"
        "아래 중 하나로 실행하세요:\n"
        "  1) uv sync && .venv/bin/python scripts/init_schema_v1_1.py\n"
        "  2) uv run python scripts/init_schema_v1_1.py\n"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCHEMA_PATH = ROOT / "sql" / "schema_v1_1.sql"
OPTIONAL_PGVECTOR_PATH = ROOT / "sql" / "optional_pgvector.sql"


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


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1
    if not SCHEMA_PATH.exists():
        print("[실패] sql/schema_v1_1.sql 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        print("[실패] POSTGRES_DSN 값이 비어 있습니다.")
        return 1

    dsn = to_psycopg_dsn(dsn)
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    enable_pgvector = parse_bool(env.get("ENABLE_PGVECTOR", "")) or parse_bool(env.get("REQUIRE_PGVECTOR", ""))
    optional_pgvector_sql = ""
    if enable_pgvector and OPTIONAL_PGVECTOR_PATH.exists():
        optional_pgvector_sql = OPTIONAL_PGVECTOR_PATH.read_text(encoding="utf-8")

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if optional_pgvector_sql:
                    try:
                        cur.execute(optional_pgvector_sql)
                    except Exception as exc:
                        if parse_bool(env.get("REQUIRE_PGVECTOR", "")):
                            raise
                        print(f"[경고] optional pgvector 적용 실패(선택): {exc}")
            conn.commit()
        print("[성공] schema_v1_1 적용 완료")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"[실패] 스키마 적용 실패: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


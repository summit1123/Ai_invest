from __future__ import annotations

import ai_invest.storage.postgres as postgres


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        return None

    def fetchone(self):
        return (1,)


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False
        self.rollback_calls = 0

    def cursor(self):
        return _FakeCursor()

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


def test_repo_reuses_single_connection(monkeypatch) -> None:
    calls: list[_FakeConn] = []

    def _connect(*args, **kwargs):
        conn = _FakeConn()
        calls.append(conn)
        return conn

    monkeypatch.setattr(postgres, "connect_postgres", _connect)

    repo = postgres.PostgresRepo("postgresql://user:pass@localhost:5432/db")
    with repo.connect() as conn1:
        assert conn1 is calls[0]
    with repo.connect() as conn2:
        assert conn2 is calls[0]

    assert len(calls) == 1


def test_repo_context_rolls_back_on_exception(monkeypatch) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(postgres, "connect_postgres", lambda *args, **kwargs: conn)

    repo = postgres.PostgresRepo("postgresql://user:pass@localhost:5432/db")
    try:
        with repo.connect():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert conn.rollback_calls == 1

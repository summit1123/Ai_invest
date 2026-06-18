from __future__ import annotations

import ai_invest.runtime.preflight as preflight


def _rules(
    *,
    mode: str = "paper",
    live_enabled: bool = False,
    symbols: list[str] | None = None,
    allow_live_exploration: bool = False,
    live_profit_floor_bps: float = 1.0,
    live_profit_required_margin_bps: float = 0.5,
    live_learning_enabled: bool = False,
) -> dict:
    return {
        "universe": {
            "mode": mode,
            "symbols": list(symbols or ["KRW-BTC"]),
            "dynamic": {"enabled": False},
        },
        "execution": {"min_order_krw": 10_000},
        "governance": {
            "micro_mode": {
                "allow_live_exploration": bool(allow_live_exploration),
                "live_profit_floor_bps": float(live_profit_floor_bps),
                "live_profit_required_margin_bps": float(live_profit_required_margin_bps),
            },
            "activation_gate": {
                "live_execution_enabled": live_enabled,
                "live_data_collection": {
                    "enabled": bool(live_learning_enabled),
                    "exploration_enabled": bool(live_learning_enabled),
                    "target_position_pct": 70.0 if live_learning_enabled else 0.0,
                    "min_predicted_after_cost_bps": -7.0,
                },
            }
        },
    }


def test_paper_preflight_requires_postgres() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="paper"),
        env={},
        require_trading=True,
        probe_postgres=False,
    )

    assert report.ok is False
    assert any(check.name == "storage.postgres" and check.ok is False for check in report.checks)



def test_live_trading_preflight_requires_live_flags() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=False),
        env={"POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db"},
        require_trading=True,
        probe_postgres=False,
    )

    assert report.ok is False
    assert any(check.name == "live.activation_gate" and check.ok is False for check in report.checks)
    assert any(check.name == "live.env_flag" and check.ok is False for check in report.checks)
    assert any(check.name == "broker.credentials" and check.ok is False for check in report.checks)



def test_live_non_trading_preflight_skips_broker_credentials() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=False),
        env={"POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db"},
        require_trading=False,
        probe_postgres=False,
    )

    assert report.ok is True
    assert not any(check.name == "broker.credentials" for check in report.checks)



def test_live_trading_preflight_passes_with_required_inputs() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=True),
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
        probe_postgres=False,
    )

    assert report.ok is True


def test_live_trading_preflight_rejects_unscoped_live_exploration() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=True, allow_live_exploration=True),
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
        probe_postgres=False,
    )

    assert report.ok is False
    assert any(check.name == "live.runtime_policy" and check.ok is False for check in report.checks)


def test_live_trading_preflight_allows_scoped_live_learning_policy() -> None:
    report = preflight.build_startup_preflight(
        rules_raw=_rules(
            mode="live",
            live_enabled=True,
            allow_live_exploration=True,
            live_profit_floor_bps=0.0,
            live_profit_required_margin_bps=0.0,
            live_learning_enabled=True,
        ),
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
        probe_postgres=False,
    )

    assert report.ok is True
    assert any(check.name == "live.runtime_policy" and check.ok is True for check in report.checks)


def test_live_trading_preflight_can_probe_broker_readiness(monkeypatch) -> None:
    class _FakeClient:
        @classmethod
        def from_env(cls):
            return cls()

        def get_accounts(self):
            return [{"currency": "KRW", "balance": "120000", "locked": "0"}]

        def get_order_chance(self, *, market: str):
            assert market == "KRW-BTC"
            return {
                "market": {
                    "bid": {"min_total": "5000"},
                    "bid_types": ["limit", "price"],
                }
            }

    monkeypatch.setattr(preflight, "UpbitPrivateClient", _FakeClient)

    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=True),
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
        probe_postgres=False,
        probe_broker=True,
    )

    assert report.ok is True
    assert any(check.name == "broker.connectivity" and check.ok is True for check in report.checks)
    assert any(check.name == "broker.order_chance" and check.ok is True for check in report.checks)


def test_live_trading_preflight_fails_when_exchange_min_total_exceeds_rule(monkeypatch) -> None:
    class _FakeClient:
        @classmethod
        def from_env(cls):
            return cls()

        def get_accounts(self):
            return [{"currency": "KRW", "balance": "120000", "locked": "0"}]

        def get_order_chance(self, *, market: str):
            assert market == "KRW-BTC"
            return {
                "market": {
                    "bid": {"min_total": "7000"},
                    "bid_types": ["limit", "price"],
                }
            }

    monkeypatch.setattr(preflight, "UpbitPrivateClient", _FakeClient)

    rules = _rules(mode="live", live_enabled=True)
    rules["execution"]["min_order_krw"] = 5000
    report = preflight.build_startup_preflight(
        rules_raw=rules,
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
        probe_postgres=False,
        probe_broker=True,
    )

    assert report.ok is False
    assert any(check.name == "broker.order_chance" and check.ok is False for check in report.checks)


def test_preflight_reports_postgres_connectivity_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(preflight, "connect_postgres", _boom)

    report = preflight.build_startup_preflight(
        rules_raw=_rules(mode="paper"),
        env={"POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db"},
        require_trading=True,
        probe_postgres=True,
    )

    assert report.ok is False
    assert any(check.name == "storage.postgres_connectivity" and check.ok is False for check in report.checks)

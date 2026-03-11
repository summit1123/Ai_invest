from __future__ import annotations

from ai_invest.runtime.preflight import build_startup_preflight


def _rules(*, mode: str = "paper", live_enabled: bool = False, symbols: list[str] | None = None) -> dict:
    return {
        "universe": {
            "mode": mode,
            "symbols": list(symbols or ["KRW-BTC"]),
            "dynamic": {"enabled": False},
        },
        "execution": {"min_order_krw": 10_000},
        "governance": {
            "activation_gate": {
                "live_execution_enabled": live_enabled,
            }
        },
    }


def test_paper_preflight_requires_postgres() -> None:
    report = build_startup_preflight(
        rules_raw=_rules(mode="paper"),
        env={},
        require_trading=True,
    )

    assert report.ok is False
    assert any(check.name == "storage.postgres" and check.ok is False for check in report.checks)



def test_live_trading_preflight_requires_live_flags() -> None:
    report = build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=False),
        env={"POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db"},
        require_trading=True,
    )

    assert report.ok is False
    assert any(check.name == "live.activation_gate" and check.ok is False for check in report.checks)
    assert any(check.name == "live.env_flag" and check.ok is False for check in report.checks)
    assert any(check.name == "broker.credentials" and check.ok is False for check in report.checks)



def test_live_non_trading_preflight_skips_broker_credentials() -> None:
    report = build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=False),
        env={"POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db"},
        require_trading=False,
    )

    assert report.ok is True
    assert not any(check.name == "broker.credentials" for check in report.checks)



def test_live_trading_preflight_passes_with_required_inputs() -> None:
    report = build_startup_preflight(
        rules_raw=_rules(mode="live", live_enabled=True),
        env={
            "POSTGRES_DSN": "postgresql://user:pass@localhost:5432/db",
            "ENABLE_LIVE_TRADING": "true",
            "UPBIT_ACCESS_KEY": "access",
            "UPBIT_SECRET_KEY": "secret",
        },
        require_trading=True,
    )

    assert report.ok is True

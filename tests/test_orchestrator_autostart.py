from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ai_invest.runtime.orchestrator_autostart import (
    OrchestratorAutostartState,
    _status_workers_alive,
    maybe_start_orchestrator,
    stop_orchestrator,
)


class OrchestratorAutostartTests(unittest.TestCase):
    def test_status_workers_alive_false_when_missing(self) -> None:
        path = Path("/tmp/does-not-exist-orchestrator-status.json")
        self.assertFalse(_status_workers_alive(path))

    def test_status_workers_alive_true_with_alive_pid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "status.json"
            p.write_text(
                json.dumps(
                    {
                        "workers": {
                            "paper_loop": {
                                "pid": os.getpid(),
                                "alive": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_status_workers_alive(p))

    def test_maybe_start_skips_when_disabled(self) -> None:
        with patch.dict(os.environ, {"APP_AUTOSTART_ORCHESTRATOR": "false"}, clear=False):
            st = maybe_start_orchestrator()
        self.assertFalse(st.enabled)
        self.assertFalse(st.started_here)

    def test_maybe_start_skips_when_external_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "status.json"
            p.write_text(
                json.dumps(
                    {
                        "workers": {
                            "paper_loop": {
                                "pid": os.getpid(),
                                "alive": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "APP_AUTOSTART_ORCHESTRATOR": "true",
                    "APP_AUTOSTART_FORCE": "false",
                    "ORCHESTRATOR_STATUS_PATH": str(p),
                },
                clear=False,
            ):
                st = maybe_start_orchestrator()
        self.assertTrue(st.enabled)
        self.assertFalse(st.started_here)
        self.assertIn("external orchestrator", st.reason)

    def test_stop_orchestrator_waits_for_polled_stop_request_on_windows(self) -> None:
        class _FakeProc:
            def __init__(self) -> None:
                self.pid = 4321
                self.wait_calls: list[float | None] = []
                self.terminate_called = False
                self.kill_called = False

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.wait_calls.append(timeout)
                return 0

            def terminate(self) -> None:
                self.terminate_called = True

            def kill(self) -> None:
                self.kill_called = True

        fake_proc = _FakeProc()
        state = OrchestratorAutostartState(
            enabled=True,
            started_here=True,
            reason="started",
            status_path=Path("runtime/status.json"),
            log_path=Path("logs/autostart.log"),
            proc=fake_proc,
            log_fp=StringIO(),
        )

        with patch("ai_invest.runtime.orchestrator_autostart.sys.platform", "win32"):
            with patch("ai_invest.runtime.orchestrator_autostart.write_stop_request") as write_stop_request:
                stop_orchestrator(state)

        write_stop_request.assert_called_once()
        self.assertEqual(fake_proc.wait_calls, [8.0])
        self.assertFalse(fake_proc.terminate_called)
        self.assertFalse(fake_proc.kill_called)


if __name__ == "__main__":
    unittest.main()

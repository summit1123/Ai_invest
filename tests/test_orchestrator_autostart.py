from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_invest.runtime.orchestrator_autostart import _status_workers_alive, maybe_start_orchestrator


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


if __name__ == "__main__":
    unittest.main()

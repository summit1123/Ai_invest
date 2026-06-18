from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_invest.runtime.orchestrator_stop_trace import consume_stop_request, write_stop_request


class OrchestratorStopTraceTests(unittest.TestCase):
    def test_write_and_consume_stop_request_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stop_request.json"
            write_stop_request(
                source="autostart",
                reason="parent_shutdown",
                target_pid=1234,
                extra={"note": "unit-test"},
                path=path,
            )
            consumed = consume_stop_request(path=path, max_age_seconds=60.0)
            self.assertIsNotNone(consumed)
            assert consumed is not None
            self.assertEqual(str(consumed.get("source")), "autostart")
            self.assertEqual(str(consumed.get("reason")), "parent_shutdown")
            self.assertEqual(int(consumed.get("target_pid") or 0), 1234)
            self.assertEqual(str(((consumed.get("extra") or {}) if isinstance(consumed.get("extra"), dict) else {}).get("note")), "unit-test")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

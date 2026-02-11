from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_module():
    path = Path("scripts/run_multi_orchestrator.py")
    spec = importlib.util.spec_from_file_location("run_multi_orchestrator", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MultiOrchestratorTests(unittest.TestCase):
    def test_build_commands_includes_all_workers(self) -> None:
        mod = _load_module()
        cmds = mod._build_commands(
            python_bin="python",
            decision_interval_sec=15,
            work_interval_sec=1800,
            governance_sleep_sec=30,
            enable_paper=True,
            enable_work=True,
            enable_governance=True,
        )
        names = [name for name, _ in cmds]
        self.assertEqual(names, ["paper_loop", "work_loop", "governance_loop"])

    def test_build_commands_respects_disable_flags(self) -> None:
        mod = _load_module()
        cmds = mod._build_commands(
            python_bin="python",
            decision_interval_sec=15,
            work_interval_sec=1800,
            governance_sleep_sec=30,
            enable_paper=False,
            enable_work=True,
            enable_governance=False,
        )
        self.assertEqual([name for name, _ in cmds], ["work_loop"])


if __name__ == "__main__":
    unittest.main()


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
            research_interval_sec=3600,
            quant_interval_sec=1800,
            risk_interval_sec=300,
            ops_interval_sec=60,
            governance_sleep_sec=30,
            review_sleep_sec=60,
            adaptive_sleep_sec=3600,
            enable_paper=True,
            enable_work=True,
            enable_governance=True,
            enable_review=True,
            enable_adaptive=True,
        )
        names = [name for name, _ in cmds]
        self.assertEqual(
            names,
            [
                "paper_loop",
                "research_work_loop",
                "quant_work_loop",
                "risk_work_loop",
                    "ops_work_loop",
                    "governance_loop",
                    "review_loop",
                    "adaptive_tuning_loop",
                ],
            )

    def test_build_commands_respects_disable_flags(self) -> None:
        mod = _load_module()
        cmds = mod._build_commands(
            python_bin="python",
            decision_interval_sec=15,
            research_interval_sec=3600,
            quant_interval_sec=1800,
            risk_interval_sec=300,
            ops_interval_sec=60,
            governance_sleep_sec=30,
            review_sleep_sec=60,
            adaptive_sleep_sec=3600,
            enable_paper=False,
            enable_work=True,
            enable_governance=False,
            enable_review=False,
            enable_adaptive=False,
        )
        self.assertEqual([name for name, _ in cmds], ["research_work_loop", "quant_work_loop", "risk_work_loop", "ops_work_loop"])

    def test_stop_request_target_helper_accepts_matching_pid(self) -> None:
        mod = _load_module()
        self.assertTrue(
            mod._stop_request_targets_current_process(
                {"target_pid": 1234, "source": "autostart"},
                pid=1234,
            )
        )
        self.assertFalse(
            mod._stop_request_targets_current_process(
                {"target_pid": 9999, "source": "autostart"},
                pid=1234,
            )
        )
        self.assertTrue(mod._stop_request_targets_current_process({"source": "signal"}, pid=1234))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_module():
    path = Path("scripts/run_adaptive_tuning_loop.py")
    spec = importlib.util.spec_from_file_location("run_adaptive_tuning_loop", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class AdaptiveTuningLoopTests(unittest.TestCase):
    def test_apply_whitelisted_patch_ignores_outside_keys(self) -> None:
        mod = _load_module()
        rules_raw = {
            "strategy": {"alpha_score": {"entry_alpha": 0.45}},
            "governance": {"micro_mode": {"min_alpha": 0.2}},
        }
        patch = {
            "strategy.alpha_score.entry_alpha": 0.5,
            "governance.micro_mode.min_alpha": 0.25,
            "risk.max_daily_loss_pct": 99.0,
        }
        out = mod._apply_whitelisted_patch(
            rules_raw=rules_raw,
            patch=patch,
            whitelist_paths=[
                "strategy.alpha_score.entry_alpha",
                "governance.micro_mode.min_alpha",
            ],
        )
        self.assertEqual(float(out["strategy"]["alpha_score"]["entry_alpha"]), 0.5)
        self.assertEqual(float(out["governance"]["micro_mode"]["min_alpha"]), 0.25)
        self.assertNotIn("risk", out)

    def test_build_tuning_patch_respects_max_step_10pct(self) -> None:
        mod = _load_module()
        rules_raw = {
            "strategy": {"alpha_score": {"entry_alpha": 0.45, "cooldown_minutes": 30}},
            "governance": {
                "micro_mode": {"min_alpha": 0.20, "max_spread_bps": 4.0},
                "plan_continuity": {"min_hold_minutes": 180},
            },
        }
        cfg = {
            "max_step_pct": 10.0,
            "target_trades_in_execution_window": 3,
            "whitelist_paths": [
                "strategy.alpha_score.entry_alpha",
                "strategy.alpha_score.cooldown_minutes",
                "governance.micro_mode.min_alpha",
                "governance.micro_mode.max_spread_bps",
                "governance.plan_continuity.min_hold_minutes",
            ],
        }
        m = mod.WindowMetrics
        metrics = {
            "execution": m(1, 20.0, -2000.0, -2000.0, 2.0, 1000.0, 0.5, 0.5, 3),
            "short": m(20, 30.0, -800.0, -16000.0, 5.0, 5000.0, 0.2, 0.4, 4),
            "medium": m(100, 48.0, -120.0, -12000.0, 8.0, 8000.0, 0.15, 0.3, 5),
            "anchor": m(300, 50.0, 10.0, 3000.0, 9.0, 9000.0, 0.1, 0.2, 6),
        }
        patch, _diag = mod._build_tuning_patch(
            rules_raw=rules_raw,
            tuning_cfg=cfg,
            metrics=metrics,
            regime="SHOCK",
            composite_score=-1.0,
        )
        self.assertTrue(bool(patch))
        for path, new_value in patch.items():
            old_value = float(mod._path_get(rules_raw, path))
            max_step = abs(old_value) * 0.10
            if max_step == 0:
                max_step = 0.10
            self.assertLessEqual(abs(float(new_value) - old_value), max_step + 1e-6, msg=path)

    def test_should_rollback_when_ev_or_drawdown_degrades(self) -> None:
        mod = _load_module()
        out_ev = mod._should_rollback(
            recent_ev=80.0,
            baseline_ev=120.0,
            recent_dd=1000.0,
            baseline_dd=1000.0,
            ev_ratio=0.20,
            dd_ratio=0.30,
        )
        self.assertTrue(bool(out_ev["should_rollback"]))
        self.assertTrue(bool(out_ev["ev_degraded"]))

        out_dd = mod._should_rollback(
            recent_ev=130.0,
            baseline_ev=120.0,
            recent_dd=1400.0,
            baseline_dd=1000.0,
            ev_ratio=0.20,
            dd_ratio=0.30,
        )
        self.assertTrue(bool(out_dd["should_rollback"]))
        self.assertTrue(bool(out_dd["drawdown_degraded"]))


if __name__ == "__main__":
    unittest.main()

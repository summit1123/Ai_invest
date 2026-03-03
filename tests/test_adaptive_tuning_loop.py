from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
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
            "preserve_exploration_on_low_activity": False,
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

    def test_build_tuning_patch_does_not_tighten_when_activity_low(self) -> None:
        mod = _load_module()
        rules_raw = {
            "strategy": {"alpha_score": {"entry_alpha": 0.62, "cooldown_minutes": 120}},
            "governance": {
                "micro_mode": {"min_alpha": 0.72, "max_spread_bps": 1.5},
                "plan_continuity": {"min_hold_minutes": 120},
            },
        }
        cfg = {
            "max_step_pct": 10.0,
            "target_trades_in_execution_window": 6,
            "preserve_exploration_on_low_activity": True,
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
            "execution": m(0, 10.0, -1200.0, -1200.0, 3.0, 2000.0, 0.35, 0.4, 4),
            "short": m(10, 30.0, -500.0, -5000.0, 6.0, 5000.0, 0.25, 0.3, 4),
            "medium": m(60, 44.0, -120.0, -7200.0, 10.0, 8000.0, 0.2, 0.2, 5),
            "anchor": m(180, 48.0, -30.0, -5400.0, 14.0, 9000.0, 0.2, 0.2, 6),
        }
        patch, _diag = mod._build_tuning_patch(
            rules_raw=rules_raw,
            tuning_cfg=cfg,
            metrics=metrics,
            regime="SHOCK",
            composite_score=-1.0,
        )
        self.assertLessEqual(float(patch.get("strategy.alpha_score.entry_alpha", 0.62)), 0.62)
        self.assertLessEqual(float(patch.get("governance.micro_mode.min_alpha", 0.72)), 0.72)
        self.assertLessEqual(float(patch.get("strategy.alpha_score.cooldown_minutes", 120)), 120)
        self.assertLessEqual(float(patch.get("governance.plan_continuity.min_hold_minutes", 120)), 120)
        self.assertGreaterEqual(float(patch.get("governance.micro_mode.max_spread_bps", 1.5)), 1.5)

    def test_should_skip_patch_for_min_interval(self) -> None:
        mod = _load_module()

        class _Repo:
            def __init__(self, ts: datetime) -> None:
                self._ts = ts

            def fetch_latest_event(self, *, event_type: str):  # type: ignore[no-untyped-def]
                return {
                    "event_id": "evt-1",
                    "event_type": event_type,
                    "ts": self._ts,
                }

        now = datetime.now(timezone.utc)
        repo = _Repo(now - timedelta(hours=2))
        skip, info = mod._should_skip_patch_for_min_interval(
            repo=repo,
            tuning_cfg={"min_apply_interval_hours": 6},
            now=now,
        )
        self.assertTrue(skip)
        self.assertEqual(str(info.get("reason")), "min_apply_interval_not_elapsed")

    def test_apply_patch_change_budget_limits_and_enforces_direction(self) -> None:
        mod = _load_module()
        rules_raw = {
            "strategy": {"alpha_score": {"entry_alpha": 0.65, "cooldown_minutes": 160}},
            "governance": {
                "micro_mode": {"min_alpha": 0.65, "max_spread_bps": 8.0},
                "plan_continuity": {"min_hold_minutes": 120},
            },
        }
        patch = {
            "strategy.alpha_score.entry_alpha": 0.70,  # tighten
            "governance.micro_mode.max_spread_bps": 9.0,  # loosen
            "strategy.alpha_score.cooldown_minutes": 175,  # tighten
        }
        tuned, diag = mod._apply_patch_change_budget(
            rules_raw=rules_raw,
            patch=patch,
            tuning_cfg={
                "max_paths_per_patch": 1,
                "single_direction_per_cycle": True,
                "path_priority": [
                    "strategy.alpha_score.entry_alpha",
                    "strategy.alpha_score.cooldown_minutes",
                    "governance.micro_mode.max_spread_bps",
                ],
            },
            composite_score=-0.4,
        )
        self.assertEqual(len(tuned), 1)
        self.assertIn("strategy.alpha_score.entry_alpha", tuned)
        self.assertEqual(str(diag.get("desired_direction")), "TIGHTEN")


if __name__ == "__main__":
    unittest.main()

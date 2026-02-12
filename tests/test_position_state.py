from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_invest.runtime.position_state import parse_position_state, with_hwm_update


class PositionStateTests(unittest.TestCase):
    def test_parse_and_context(self) -> None:
        state = parse_position_state(
            {
                "entry_price": 100.0,
                "entry_ts": "2026-02-12T00:00:00+00:00",
                "hwm_price": 103.0,
                "strategy_tag": "mom",
                "cooldown_until": "2099-01-01T00:00:00+00:00",
                "last_exit_reason": "stop",
            }
        )
        now = datetime(2026, 2, 12, 1, 0, 0, tzinfo=timezone.utc)
        ctx = state.to_context(now=now)
        self.assertEqual(state.strategy_tag, "MOM")
        self.assertTrue(bool(ctx.get("cooldown_active")))
        self.assertEqual(str(ctx.get("last_exit_reason")), "STOP")

    def test_hwm_update_only_when_price_higher(self) -> None:
        state = parse_position_state({"entry_price": 100.0, "hwm_price": 101.0})
        s1 = with_hwm_update(state=state, last_price=100.5)
        s2 = with_hwm_update(state=state, last_price=102.0)
        self.assertEqual(float(s1.hwm_price or 0.0), 101.0)
        self.assertEqual(float(s2.hwm_price or 0.0), 102.0)


if __name__ == "__main__":
    unittest.main()


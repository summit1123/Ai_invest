from __future__ import annotations

import unittest

from ai_invest.domain.reason_codes import ReasonCode
from ai_invest.execution.order_state_machine import (
    InvalidTransitionError,
    OrderState,
    OrderStateMachine,
    map_upbit_status_to_internal,
)


class OrderStateMachineTests(unittest.TestCase):
    def test_happy_path_transition(self) -> None:
        machine = OrderStateMachine()
        machine.transition(OrderState.ACK, ReasonCode.RG_PASS)
        machine.transition(OrderState.PARTIAL, ReasonCode.RG_PASS)
        machine.transition(OrderState.FILLED, ReasonCode.RG_PASS)
        self.assertEqual(machine.state, OrderState.FILLED)
        self.assertEqual(len(machine.history), 3)

    def test_invalid_transition_is_blocked(self) -> None:
        machine = OrderStateMachine()
        with self.assertRaises(InvalidTransitionError):
            machine.transition(OrderState.FILLED, ReasonCode.EX_INVALID_STATE_TRANSITION)

    def test_terminal_state_cannot_transition(self) -> None:
        machine = OrderStateMachine()
        machine.transition(OrderState.ACK, ReasonCode.RG_PASS)
        machine.transition(OrderState.CANCELED, ReasonCode.EX_PARTIAL_FILL_TIMEOUT)
        with self.assertRaises(InvalidTransitionError):
            machine.transition(OrderState.ACK, ReasonCode.EX_INVALID_STATE_TRANSITION)

    def test_upbit_status_mapping(self) -> None:
        self.assertEqual(
            map_upbit_status_to_internal(status="wait", executed_volume=0.0, remaining_volume=1.0),
            OrderState.ACK,
        )
        self.assertEqual(
            map_upbit_status_to_internal(status="watch", executed_volume=0.3, remaining_volume=0.7),
            OrderState.PARTIAL,
        )
        self.assertEqual(map_upbit_status_to_internal(status="done"), OrderState.FILLED)
        self.assertEqual(map_upbit_status_to_internal(status="cancel"), OrderState.CANCELED)


if __name__ == "__main__":
    unittest.main()


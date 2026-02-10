from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ai_invest.domain.reason_codes import ReasonCode, parse_reason_code


class OrderState(str, Enum):
    NEW = "NEW"
    ACK = "ACK"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


TERMINAL_STATES = {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED}

ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.NEW: {OrderState.ACK, OrderState.REJECTED},
    OrderState.ACK: {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELED},
    OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELED},
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.REJECTED: set(),
}


class InvalidTransitionError(RuntimeError):
    """Raised when order state transition violates the contract."""


def map_upbit_status_to_internal(
    *,
    status: str,
    executed_volume: float = 0.0,
    remaining_volume: float | None = None,
) -> OrderState:
    if status in {"wait", "watch"}:
        if executed_volume > 0 and (remaining_volume is None or remaining_volume > 0):
            return OrderState.PARTIAL
        return OrderState.ACK
    if status == "done":
        return OrderState.FILLED
    if status == "cancel":
        return OrderState.CANCELED
    raise ValueError(f"Unknown upbit status: {status}")


@dataclass(frozen=True)
class TransitionRecord:
    from_state: OrderState
    to_state: OrderState
    reason_code: ReasonCode


@dataclass
class OrderStateMachine:
    state: OrderState = OrderState.NEW
    history: list[TransitionRecord] = field(default_factory=list)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def can_transition(self, to_state: OrderState) -> bool:
        return to_state in ALLOWED_TRANSITIONS[self.state]

    def transition(self, to_state: OrderState, reason_code: ReasonCode | str) -> None:
        code = parse_reason_code(reason_code)

        if self.is_terminal():
            raise InvalidTransitionError(
                f"Terminal state {self.state} cannot transition to {to_state}"
            )
        if not self.can_transition(to_state):
            raise InvalidTransitionError(
                f"Invalid transition: {self.state} -> {to_state} (reason={code})"
            )

        self.history.append(TransitionRecord(self.state, to_state, code))
        self.state = to_state

    def apply_exchange_snapshot(
        self,
        *,
        upbit_status: str,
        executed_volume: float = 0.0,
        remaining_volume: float | None = None,
        reason_code: ReasonCode | str = ReasonCode.RG_PASS,
    ) -> None:
        mapped = map_upbit_status_to_internal(
            status=upbit_status,
            executed_volume=executed_volume,
            remaining_volume=remaining_volume,
        )
        if mapped == self.state:
            return
        self.transition(mapped, reason_code)

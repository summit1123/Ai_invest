# reason_codes.md - Reason Code Dictionary

Purpose:
- Keep decision, execution, outcome, and ops reasons stable and machine-readable.
- Prefer structured reason codes over free-form explanations.
- Reuse the same codes across `decisions`, `events`, and `decision_outcomes`.

## Naming rules

- Format: `DOMAIN_DETAIL`
- Domains:
  - `RG_*`: Safe Judge and runtime gates
  - `EX_*`: execution and order lifecycle
  - `OC_*`: post-trade outcome review
  - `OP_*`: operations lifecycle

## Safe Judge codes (`RG_*`)

| Code | Default action | Meaning |
|---|---|---|
| `RG_RECON_FAIL` | `PAUSE` | Reconciliation failed. |
| `RG_DAILY_LOSS_LIMIT_HIT` | `PAUSE` | Daily loss limit was hit. |
| `RG_DATA_BAD` | `PAUSE` | Input market or state data is invalid. |
| `RG_RATE_LIMIT_STORM` | `PAUSE` | API rate limit pressure is severe. |
| `RG_WS_UNSTABLE` | `PAUSE` | Websocket or stream health is unstable. |
| `RG_RISK_VETO` | `HOLD` | Risk agent vetoed new exposure. |
| `RG_REGIME_BLOCKED` | `HOLD` | Market regime blocks new entries. |
| `RG_SPREAD_TOO_WIDE` | `HOLD` | Spread is too wide for a new entry. |
| `RG_SLIPPAGE_EST_TOO_HIGH` | `HOLD` | Predicted slippage or total cost is too high. |
| `RG_EDGE_TOO_LOW` | `HOLD` | Expected after-cost edge is too low. |
| `RG_NEWS_RISK` | `HOLD` | News shock or event risk elevated runtime risk. |
| `RG_EXPOSURE_LIMIT` | `HOLD` | Exposure or soft loss budget is exhausted. |
| `RG_TRADE_PLAN_FLAT` | `HOLD` | Trade plan says flat / no new exposure. |
| `RG_TRADE_PLAN_TARGET_REACHED` | `HOLD` | Current exposure is already at target. |
| `RG_MIN_ORDER_NOT_MET` | `HOLD` | Order notional is below exchange minimum. |
| `RG_COOLDOWN_ACTIVE` | `HOLD` | Cooldown period is still active. |
| `RG_SIGNAL_CONFLICT` | `HOLD` | Signals conflict and cannot be reconciled. |
| `RG_MICRO_BLOCKED_COOLDOWN` | `HOLD` | Micro mode blocked by cooldown. |
| `RG_MICRO_BLOCKED_EDGE` | `HOLD` | Micro mode blocked by weak edge. |
| `RG_MICRO_BLOCKED_POLICY` | `HOLD` | Micro mode blocked by policy. |
| `RG_CAP_PENDING` | `HOLD` | Runtime cap promotion is pending. |
| `RG_CAP_PROMOTED` | `BUY` | Runtime cap promotion allowed a small entry. |
| `RG_CAP_BLOCKED` | `HOLD` | Runtime cap promotion was blocked. |
| `RG_PASS` | `BUY/SELL` | All relevant gates passed. |

## Execution codes (`EX_*`)

| Code | Default action | Meaning |
|---|---|---|
| `EX_ORDER_SUBMIT_FAIL` | `RETRY/PAUSE` | Order submission failed. |
| `EX_ORDER_REJECTED` | `HOLD` | Exchange rejected the order. |
| `EX_ACK_TIMEOUT` | `CANCEL/RETRY` | No acknowledgment received in time. |
| `EX_PARTIAL_FILL_TIMEOUT` | `CANCEL_REST` | Partial fill did not complete in time. |
| `EX_CANCEL_FAILED` | `PAUSE` | Cancel request failed or order state diverged. |
| `EX_REPRICE_LIMIT_REACHED` | `HOLD` | Reprice limit was reached. |
| `EX_TICK_SIZE_INVALID` | `HOLD` | Price does not match tick-size rules. |
| `EX_INSUFFICIENT_BALANCE` | `HOLD` | Available balance is insufficient. |
| `EX_INVALID_STATE_TRANSITION` | `PAUSE` | Order state machine entered an invalid transition. |

## Outcome codes (`OC_*`)

| Code | Meaning |
|---|---|
| `OC_FALSE_BREAKOUT` | Breakout failed and quickly reverted. |
| `OC_REGIME_MISCLASSIFIED` | Regime classification was wrong. |
| `OC_COST_UNDERESTIMATED` | Costs or slippage were underestimated. |
| `OC_STOP_TOO_TIGHT` | Stop was too tight and cut a valid trade. |
| `OC_STOP_TOO_LOOSE` | Stop was too loose and let losses run. |
| `OC_LATE_ENTRY` | Entry came too late. |
| `OC_EARLY_EXIT` | Exit came too early. |
| `OC_LIQUIDITY_DROPOUT` | Liquidity deteriorated materially. |
| `OC_NEWS_SHOCK` | News or event shock broke the setup. |
| `OC_SIGNAL_OVERFIT` | Signal was overly tuned to a narrow regime. |
| `OC_EXECUTION_LATENCY` | Execution delay degraded outcome. |
| `OC_RULE_DRIFT` | Rule set drifted away from the intended behavior. |

## Ops codes (`OP_*`)

| Code | Meaning |
|---|---|
| `OP_PAUSE_TRIGGERED` | System entered pause state. |
| `OP_RESUME_COMPLETED` | System resumed normal operation. |
| `OP_RESTART_RECOVERY` | Restart/recovery flow completed. |
| `OP_MANUAL_REVIEW_REQUIRED` | Manual review is required before continuing. |

## Usage rules

1. Persist only structured reason codes in core storage.
2. Map human-readable strings from templates, not from inline ad-hoc text.
3. Use `OC_*` for outcome review labels whenever possible.
4. Cap multi-reason payloads to at most 3 codes.
5. New codes must be added to both `reason_codes.py` and this document.

# Live Trading Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live-learning trading system safe to use for autonomous agent investing by preventing order churn, isolating clean experiment seasons, and allowing multi-symbol research with single-symbol execution.

**Architecture:** Add hard execution guards at the lowest order-submission layer, then surface the same constraints in runtime decision context and governance plans. Keep agent autonomy for research and symbol selection, but make pending orders, recent fills, cooldowns, and single-active-symbol constraints non-negotiable.

**Tech Stack:** Python, pytest, PostgreSQL-backed repository, Upbit private/public APIs, existing AI Invest runtime/governance modules.

---

### Task 1: Execution Churn Guards

**Files:**
- Modify: `ai_invest/execution/live_execution.py`
- Modify: `ai_invest/storage/postgres.py`
- Test: `tests/test_live_executor.py`

- [ ] Write failing tests for blocking BUY/SELL when an open order exists for the symbol.
- [ ] Add a repository method to fetch recent open orders by symbol/status.
- [ ] Add a live executor pre-submit guard that returns `None` and records a skipped metric/event when open orders exist.
- [ ] Verify focused live executor tests pass.

### Task 2: Runtime Position And Cooldown Guards

**Files:**
- Modify: `ai_invest/runtime/paper_loop.py`
- Modify: `ai_invest/judge/safe_judge.py`
- Test: `tests/test_paper_loop_realtime_runtime.py`
- Test: `tests/test_safe_judge.py`

- [ ] Write failing tests for minimum hold/cooldown behavior and minimum actionable order difference.
- [ ] Ensure runtime context treats open orders and current positions as execution locks.
- [ ] Ensure Safe Judge reports explicit block reasons instead of continuing BUY churn.
- [ ] Verify focused runtime and judge tests pass.

### Task 3: Multi-Symbol Research With Single Execution

**Files:**
- Modify: `rules.yaml`
- Modify: `ai_invest/meetings/governance_meeting.py`
- Test: `tests/test_governance_meeting_protocol.py`

- [ ] Write failing tests showing governance can consider multiple candidate symbols but emits one executable symbol.
- [ ] Enable dynamic universe scouting while preserving `max_open_positions=1`.
- [ ] Keep position/open-order locks from switching active execution symbols mid-trade.
- [ ] Verify governance tests pass.

### Task 4: Clean Season Reset Tooling

**Files:**
- Create: `scripts/start_new_trading_season.py`
- Test: `tests/test_trading_season.py`

- [ ] Write failing tests for dry-run backup/season metadata behavior.
- [ ] Implement a non-destructive season starter that creates a backup manifest and new season/run namespace.
- [ ] Do not delete production trading evidence by default.
- [ ] Verify tests pass.

### Task 5: Validation And Publish

**Files:**
- No production files unless test fallout requires small fixes.

- [ ] Run focused tests for live executor, runtime, safe judge, governance, preflight, notifications.
- [ ] Run live preflight without starting the orchestrator.
- [ ] Stage only source/config/test/docs files required for this change.
- [ ] Commit, push current branch, and open/publish a draft PR.

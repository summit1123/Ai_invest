---
name: fastapi-react-trading-platform
description: Implement and evolve this repository as a FastAPI backend + React frontend trading platform. Use when adding API endpoints, request/response schemas, database integration, execution/reconciliation services, React pages/components, or backend-frontend contracts tied to the project docs.
---

# Fastapi React Trading Platform

## Overview

Build features with contract-first rules:
- Backend execution authority stays in Safe Judge.
- Frontend focuses on observability, review, and operations UI.
- Data and behavior stay aligned with `database.md`, `rules.yaml`, `reason_codes.md`, and `order_state_machine.md`.

## Workflow

1. Read project contracts first:
   - `architecture.md`
   - `database.md`
   - `agents.md`
   - `guidelines.md`
   - `rules.yaml`
   - `reason_codes.md`
   - `order_state_machine.md`
2. Choose task track:
   - FastAPI backend: load `references/backend-fastapi.md`
   - React frontend: load `references/frontend-react.md`
   - API integration or contract changes: load `references/api-contracts.md`
3. Implement with minimal scope and deterministic behavior.
4. Add tests:
   - Backend: endpoint/service/state-machine tests
   - Frontend: contract/selector/component tests (or at least API typing and loading/error states)
5. Verify docs stay in sync when contracts change.

## Guardrails

- Do not bypass Safe Judge with frontend or agent code.
- Do not introduce new reason strings; use `ReasonCode` enum values.
- Do not allow illegal order state transitions; enforce the state machine.
- Persist all critical events and notification delivery outcomes.
- Prefer adding a small contract/test before adding complexity.

## Output Expectations

- Backend deliverables should include:
  - router/schema/service/repository separation
  - explicit error contracts
  - tests
- Frontend deliverables should include:
  - typed API client interfaces
  - loading/error/empty states
  - clear page-to-endpoint mapping
- Cross-cutting deliverables should include:
  - migration notes for schema changes
  - references to updated docs

## References

- FastAPI implementation guide: `references/backend-fastapi.md`
- React implementation guide: `references/frontend-react.md`
- API and schema contract guide: `references/api-contracts.md`

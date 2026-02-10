# FastAPI + React 개발 기준 (v1)

## 목적
- 백엔드는 FastAPI, 프론트는 React 기준으로 구현 범위를 고정한다.
- 실거래 로직의 안전성(Safe Judge, 상태머신, reason code)과 UI 관측성을 동시에 확보한다.

## 백엔드 원칙
1. FastAPI router -> service -> repository 계층 분리
2. `rules.yaml` 로더/검증 실패 시 부팅 실패
3. `reason_codes` enum 외 임의 문자열 금지
4. 주문 상태 전이는 `order_state_machine` 강제

## 프론트 원칙
1. React에서 실행권 로직 구현 금지(조회/표시/운영제어 UI 중심)
2. API 타입 계약 우선, 페이지별 에러/빈상태 고정
3. HOLD/PAUSE/RECON_FAIL 사유를 최상단 노출

## 구현 참조
- 스킬: `skills/fastapi-react-trading-platform/SKILL.md`
- 백엔드 참조: `skills/fastapi-react-trading-platform/references/backend-fastapi.md`
- 프론트 참조: `skills/fastapi-react-trading-platform/references/frontend-react.md`
- API 계약: `skills/fastapi-react-trading-platform/references/api-contracts.md`

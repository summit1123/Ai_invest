# API Contracts Guide

## 목적
- FastAPI <-> React 연동 시 계약 변경을 통제한다.
- 문서와 구현의 불일치를 줄인다.

## 공통 응답 규칙
```json
{
  "ok": true,
  "data": {},
  "meta": {
    "ts_utc": "2026-02-09T10:00:00Z",
    "request_id": "uuid"
  }
}
```

에러 규칙:
```json
{
  "ok": false,
  "error": {
    "code": "RG_RECON_FAIL",
    "message_ko": "정합성 실패로 거래가 중단되었습니다.",
    "details": {}
  },
  "meta": {
    "ts_utc": "2026-02-09T10:00:00Z",
    "request_id": "uuid"
  }
}
```

## 주요 타입
1. `DecisionView`
   - `decision_id`, `action`, `score`, `confidence`
   - `selected_reasons[]`, `rejected_reasons[]`
2. `ExecutionQualityView`
   - `slippage_bps_vs_decision`, `slippage_bps_vs_submit`
   - `spread_bps_at_submit`, `filled_ratio`
3. `OpsStatusView`
   - `pause_state`, `reconciliation_status`, `latest_reason_codes[]`
4. `OutcomeReviewView`
   - `decision_id`, `outcome_label`, `error_type`, `fix_hypothesis`

## 계약 변경 원칙
1. 응답 필드 삭제 금지(필요 시 deprecate 후 제거).
2. enum 추가 시 프론트 fallback 라벨 제공.
3. `reason_code`는 `reason_codes.md` 사전 외 신규 문자열 금지.
4. 주문 상태는 `order_state_machine.md` 값 외 사용 금지.

## 문서 동기화 체크
- DB 스키마 변경 시: `database.md`
- API 타입 변경 시: `react_design_plan.md`
- reason_code 변경 시: `reason_codes.md`

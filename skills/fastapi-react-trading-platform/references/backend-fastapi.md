# Backend FastAPI Guide

## 목적
- FastAPI 기반으로 거래 시스템 백엔드를 구현할 때 구조를 고정한다.
- 실행권은 Safe Judge에 두고 API는 관측/제어/조회 역할을 수행한다.

## 권장 구조
```text
app/
  main.py
  api/
    v1/
      routers/
        overview.py
        conference.py
        decisions.py
        timeline.py
        execution.py
        ops.py
        review.py
  schemas/
    common.py
    decision.py
    execution.py
    ops.py
    review.py
  services/
    decision_service.py
    execution_service.py
    recon_service.py
    notification_service.py
  repositories/
    events_repo.py
    decisions_repo.py
    orders_repo.py
    fills_repo.py
    outcomes_repo.py
  domain/
    safe_judge.py
    order_state_machine.py
    reason_codes.py
```

## 구현 원칙
1. Router는 입출력 변환만 수행하고 비즈니스 로직은 Service로 이동한다.
2. Service는 Repository를 통해 DB 접근한다.
3. Safe Judge 관련 판단 함수는 순수 함수로 유지한다.
4. 모든 주요 예외는 구조화된 에러 응답으로 반환한다.

## 필수 엔드포인트 (MVP)
- `GET /api/v1/ui/today-overview`
- `GET /api/v1/ui/conference/{decision_id}`
- `GET /api/v1/ui/judge/{decision_id}`
- `GET /api/v1/ui/timeline`
- `GET /api/v1/ui/execution-quality`
- `GET /api/v1/ui/reconciliation-status`
- `GET /api/v1/ui/pause-log`
- `GET /api/v1/ui/notifications-delivery`
- `GET /api/v1/ui/review/weekly`

## 데이터 계약 강제
1. `rules.yaml`은 앱 시작 시 로드 + 검증 실패 시 부팅 실패.
2. `reason_codes`는 enum 기반으로만 저장.
3. 주문 상태 업데이트는 상태머신을 반드시 통과.

## 테스트 최소 세트
1. rules 로딩/검증 실패 케이스
2. Safe Judge 차단(HOLD/PAUSE) 케이스
3. 주문 상태 불법 전이 차단
4. 주요 조회 API 스키마 검증

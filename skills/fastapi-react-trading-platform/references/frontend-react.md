# Frontend React Guide

## 목적
- React UI를 운영/복기 중심으로 구성한다.
- 거래 실행 로직을 프론트에 두지 않고, 백엔드 계약을 명확히 소비한다.

## 권장 구조
```text
src/
  app/
    providers.tsx
    router.tsx
  pages/
    TodayPage.tsx
    ConferencePage.tsx
    JudgePage.tsx
    TimelinePage.tsx
    ExecutionQualityPage.tsx
    ReconciliationPage.tsx
    PauseLogPage.tsx
    NotificationDeliveryPage.tsx
    WeeklyReviewPage.tsx
  entities/
    decision/
    execution/
    ops/
  features/
    filters/
    charts/
    status-badges/
  shared/
    api/client.ts
    api/types.ts
    ui/
    utils/
```

## 상태관리
- 서버 상태: TanStack Query
- 로컬 UI 상태: Zustand 또는 React Context
- 실시간 반영: 초기 polling(5~10초), 이후 SSE/WS 확장

## 화면 우선순위
1. Today / Conference / Judge
2. Timeline / Execution Quality
3. Reconciliation / Pause Log
4. Notification Delivery / Weekly Review

## UX 규칙
1. HOLD/PAUSE 사유를 최상단에 노출.
2. 알림 전달 실패는 운영 우선도 높게 표시.
3. 수치만 보여주지 말고 reason_code -> 한국어 라벨 매핑 제공.
4. 로딩/에러/빈 상태를 모든 화면에서 통일.

## 테스트 최소 세트
1. API 타입 파싱 실패 시 안전하게 에러 렌더링
2. reason_code 라벨 매핑 검증
3. 핵심 페이지 라우팅/필터 동작 검증

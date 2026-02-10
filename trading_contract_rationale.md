# trading_contract_rationale.md - 초기 계약값 심층 설계 근거 (v1.0)

> 목적: `rules.yaml`의 초기값을 왜 이렇게 잡았는지 설명하고, 과적합 없이 조정하는 방법을 고정한다.

---

## 1. 설계 원칙

1. 생존 우선: 기대수익보다 손실 상한 먼저 고정
2. 비용 우선: 신호가 좋아도 비용이 높으면 진입 금지
3. 단순 우선: 1심볼, 1전략, 1개월 검증 후 확장

---

## 2. 핵심 숫자 근거

### 2.1 리스크

- `max_risk_per_trade_pct = 0.35`
  - 연속 손실 4회에도 -1.4% 수준에서 일손실 한도 안쪽 유지
- `max_daily_loss_pct = 1.50`
  - 변동성 급등일에 자동 정지 가능한 보수적 상한
- `max_weekly_loss_pct = 4.00`
  - 한 주 단위 전략 드리프트를 조기 탐지

### 2.2 비용/진입 게이트

- `max_spread_bps_entry = 8.0`
- `max_predicted_slippage_bps = 10.0`
- `max_total_cost_bps = 18.0`
- `min_expected_edge_bps = 28.0`

해석:
- 최소 기대엣지(28bps)에서 비용 상한(18bps)과 버퍼를 제외해도 순엣지 양수를 유지
- 비용이 높은 구간에서 과매매를 막는 목적

### 2.3 손절/청산

- `atr_stop_mult = 2.2`, `atr_trail_mult = 2.8`
  - 노이즈 손절과 손실 확장 사이의 중간값
- `hard_stop_pct = 1.2`
  - 비정상 급락 시 하드 컷
- `min_hold_seconds = 300`
  - 진입 직후 비용/미세변동에 의한 즉시 반전매매 방지
- `include_tax_in_realtime_stop = false`
  - 세금은 정산 레이어에서 처리, 실시간 손절 왜곡 방지

---

## 3. 기대 동작

1. 평시:
   - TREND 구간에서만 진입
   - spread/slippage 양호 시 지정가(post_only) 중심 체결
2. 비용 급등:
   - `RG_SPREAD_TOO_WIDE` 또는 `RG_SLIPPAGE_EST_TOO_HIGH`로 HOLD
3. 운영 이상:
   - recon FAIL, 429 폭증, WS 불안정 시 즉시 PAUSE

---

## 4. 과최적화 방지 조정 규칙

1. 한 주에 1~2개 파라미터만 변경
2. 변경 전/후 동일 데이터 구간 비교
3. 성과 평가는 수익 단독이 아니라:
   - MDD
   - 손실 꼬리
   - 비용 대비 순엣지
4. 검증 실패 시 즉시 롤백

---

## 5. 우선 조정 순서

1. `max_spread_bps_entry`, `max_predicted_slippage_bps`
2. `min_expected_edge_bps`
3. `atr_stop_mult`, `atr_trail_mult`
4. `max_risk_per_trade_pct`

이 순서로 가면, 전략 자체를 크게 바꾸지 않고도 실거래 손익 변동성을 먼저 줄일 수 있다.

---

## 6. Gate 통과 기준(초기)

1. 7일 Paper 운용에서 `recon FAIL 미복구 = 0`
2. 일손실 제한 위반 `0`
3. `notification_deliveries` 누락 `0`
4. `decision_outcomes` 기록 누락 `0`
5. 비용 반영 순엣지 양수 구간에서만 진입하는지 샘플 검증 통과

---

## 7. 참고(Upbit 공식 문서)

- 주문 생성/타입/`time_in_force`: https://global-docs.upbit.com/reference/order
- 주문 가능 정보 조회(수수료/최소 주문 관련 정보 포함): https://docs-e.upbit.com/reference/query-order-chance
- 오픈 주문 상태(`wait`, `watch`): https://docs-e.upbit.com/reference/query-open-orders
- 종료 주문 상태(`done`, `cancel`): https://docs-e.upbit.com/reference/query-closed-orders

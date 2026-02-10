# order_state_machine.md - 주문 상태머신 전이표 (Upbit, v1.0)

> 목적: 주문 라이프사이클을 결정론적으로 고정해 중복/유실/무한루프를 방지한다.
> 기준: `orders.status`는 `NEW/ACK/PARTIAL/FILLED/CANCELED/REJECTED`만 사용.

---

## 1. 상태 정의

| 내부 상태 | 의미 | 종료 상태 |
|---|---|---|
| `NEW` | 로컬 주문 생성, 거래소 제출 전/직후 | 아니오 |
| `ACK` | 거래소 접수 확인(활성 주문) | 아니오 |
| `PARTIAL` | 일부 체결, 잔량 존재 | 아니오 |
| `FILLED` | 전량 체결 완료 | 예 |
| `CANCELED` | 취소 완료 또는 IOC/FOK 잔량 종료 | 예 |
| `REJECTED` | 거래소 거부/제출 실패 | 예 |

---

## 2. Upbit 상태 매핑

| Upbit 응답 상태 | 내부 상태 |
|---|---|
| `wait` / `watch` | `ACK` 또는 `PARTIAL` |
| `done` | `FILLED` |
| `cancel` | `CANCELED` |

판정 규칙:
- `wait/watch` + `executed_volume == 0` -> `ACK`
- `wait/watch` + `executed_volume > 0` + `remaining_volume > 0` -> `PARTIAL`

---

## 3. 허용 전이표

| From | To | 트리거 | reason_code |
|---|---|---|---|
| `NEW` | `ACK` | 주문 접수 성공 | `RG_PASS` |
| `NEW` | `REJECTED` | 제출 실패/거부 | `EX_ORDER_SUBMIT_FAIL` or `EX_ORDER_REJECTED` |
| `ACK` | `PARTIAL` | 첫 부분 체결 감지 | `RG_PASS` |
| `ACK` | `FILLED` | 전량 체결 | `RG_PASS` |
| `ACK` | `CANCELED` | 취소 성공, 혹은 IOC/FOK 잔량 취소 | `RG_PASS` |
| `PARTIAL` | `FILLED` | 잔량 체결 완료 | `RG_PASS` |
| `PARTIAL` | `CANCELED` | 잔량 취소 성공 | `EX_PARTIAL_FILL_TIMEOUT` |

불법 전이:
- 위 표 외 전이 발생 시 전이 거부 + `EX_INVALID_STATE_TRANSITION` 이벤트 기록 + `PAUSE` 후보

---

## 4. 실행 정책 (post_only 우선)

기본 정책:
1. `post_only` 지정가 제출
2. `post_only_timeout_sec` 내 미체결이면 취소
3. `reprice_interval_sec` 주기로 재호가
4. `max_reprice_count` 초과 시 해당 사이클 진입 포기(`HOLD`)

예외 정책:
- `RECON_FAIL`/`DAILY_LOSS`/`MANUAL` 상태에서는 신규 주문 생성 금지
- `fallback_to_market=false`면 시장가 폴백 금지

---

## 5. 타임아웃/재시도 기준

| 항목 | 기준 | 초과 시 |
|---|---|---|
| ACK 대기 | 2초 | `EX_ACK_TIMEOUT`, 재시도 최대 `max_submit_retries` |
| 부분체결 잔량 대기 | `cancel_on_timeout_sec` | 잔량 취소 |
| 주문 전체 생존시간 | 120초 | 강제 종료(`CANCELED`) |

---

## 6. 멱등성/정합성 규칙

1. 같은 `order_id`에 중복 상태 반영 금지
2. 같은 `fill_id`는 1회만 적재
3. 종료 상태(`FILLED/CANCELED/REJECTED`) 이후 상태 변경 금지
4. 재시작 시 거래소 주문 재조회로 상태 복구 후 전이 재개

---

## 7. 테스트 시나리오

1. 정상 접수 -> 부분체결 -> 전량체결
2. 정상 접수 -> 미체결 -> 취소 -> 재호가 -> 접수
3. 제출 실패 -> `REJECTED`
4. 종료 상태 이후 추가 전이 시도 -> 차단 + 경고
5. 재시작 후 open order 동기화로 상태 일치 복원

---

## 8. 참고(Upbit 공식 문서)

- 주문 생성/`time_in_force` (`ioc`, `fok`, `post_only`): https://global-docs.upbit.com/reference/order
- 체결대기 상태(`wait`, `watch`) 조회: https://docs-e.upbit.com/reference/query-open-orders
- 종료 상태(`done`, `cancel`) 조회: https://docs-e.upbit.com/reference/query-closed-orders

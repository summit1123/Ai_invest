# tax_reporting.md - 세금/정산 리포팅 표준 (v1.1)

> 목적: 거래 원장 데이터를 월말/연말 산출물로 일관되게 생성한다.  
> 범위: 데이터 소스, CSV 규격, 검증 규칙, 감사 추적.  
> 비범위: 세법 해석/법률 자문.

관련 문서:
- DB 스키마: `database.md`
- 운영 절차: `ops_runbook.md`
- 알림 표준: `notifications_telegram.md`
- Agent 역할: `agents.md` (Finance/Tax Agent)

---

## 1. 데이터 소스 매핑

| 용도 | 테이블 | 핵심 필드 |
|---|---|---|
| 거래원장 | `ledger_entries` | `entry_type`, `currency`, `amount`, `fee_amount`, `order_id`, `fill_id` |
| 실현손익 | `realized_trades` | `symbol`, `ts_open`, `ts_close`, `realized_pnl`, `fees_total` |
| 체결 검증 | `fills` | `fill_id`, `order_id`, `price`, `quantity`, `fee` |
| 일별 요약 | `pnl_daily` | `day`, `realized_pnl`, `fees_paid`, `trades_count` |

보조 소스:
- `orders` (주문 상태 보강)
- `events` (감사 추적 보강)

---

## 2. 산출물 규격 (CSV)

### 2.1 월말 산출물

파일:
- `tax_trades_YYYY_MM.csv`
- `tax_ledger_YYYY_MM.csv`
- `tax_summary_YYYY_MM.csv`

### `tax_trades_YYYY_MM.csv` 컬럼

1. `trade_id`
2. `symbol`
3. `ts_open_kst`
4. `ts_close_kst`
5. `side`
6. `qty`
7. `avg_entry_price`
8. `avg_exit_price`
9. `realized_pnl_krw`
10. `fees_total_krw`
11. `pnl_bps`
12. `tags_json`

### `tax_ledger_YYYY_MM.csv` 컬럼

1. `entry_id`
2. `ts_kst`
3. `entry_type`
4. `symbol`
5. `currency`
6. `amount`
7. `price`
8. `fee_amount`
9. `fee_currency`
10. `order_id`
11. `fill_id`

### `tax_summary_YYYY_MM.csv` 컬럼

1. `month`
2. `realized_pnl_total_krw`
3. `fees_total_krw`
4. `trade_count`
5. `winning_trade_count`
6. `losing_trade_count`
7. `max_drawdown`

### 2.2 연말 산출물

파일:
- `tax_trades_YYYY.csv`
- `tax_ledger_YYYY.csv`
- `tax_summary_YYYY.csv`

연말 파일은 월별 산출물의 집계 일관성을 유지해야 한다.

---

## 3. 단위/시간대 규칙

- 시간대: KST (`UTC+09:00`)로 변환하여 출력
- 통화 기본 단위: KRW
- 수량 소수점: 원본 정밀도 유지
- 금액 소수점: 소수 2자리 표준(원본 별도 보존 가능)

---

## 4. 검증 규칙

### 4.1 합계 검증

1. `SUM(realized_trades.realized_pnl)`와 월 요약 손익 일치
2. `SUM(realized_trades.fees_total)`와 월 요약 수수료 일치
3. `ledger_entries`의 거래 관련 항목 합계와 `realized_trades` 합계 허용 오차 내 일치

권장 허용 오차:
- KRW 기준 `±1` 이내

### 4.2 정합성 검증

1. `fills.fill_id` 중복 금지
2. `fills` 누락 여부(`orders` 대비) 검증
3. `order_id` 참조 불일치 건수 0
4. 기간 경계(월말 23:59:59) 포함/제외 규칙 고정

### 4.3 재현성 검증

1. 동일 기간 재생성 시 행 수 동일
2. 동일 기간 재생성 시 체크섬 동일

---

## 5. 감사 추적 표준

### 5.1 TaxExportManifestV1 (JSON)

```json
{
  "export_id": "uuid",
  "period_start": "2026-01-01T00:00:00+09:00",
  "period_end": "2026-01-31T23:59:59+09:00",
  "generated_at": "2026-02-01T00:10:00+09:00",
  "source_tables": ["ledger_entries", "realized_trades", "fills", "pnl_daily"],
  "row_counts": {
    "ledger_entries": 1203,
    "realized_trades": 134,
    "fills": 892,
    "pnl_daily": 31
  },
  "checksum_sha256": "hex-string",
  "generated_by": "system-or-user-id"
}
```

### 5.2 체크섬 규칙

- 파일별 SHA-256 생성
- `tax_manifest_*.json`에 파일명-체크섬 매핑 저장
- 재실행 시 체크섬 비교 결과 기록

---

## 6. 월말/연말 실행 절차

1. 대상 기간 확정(KST)
2. 소스 테이블 스냅샷 쿼리
3. CSV 생성
4. 검증 규칙 실행
5. Manifest 생성 및 체크섬 저장
6. 실패 시 알림(`tpl_tax_export_fail`), 성공 시 요약 알림

권장 스케줄:
- 월말: 말일 23:30 KST
- 연말: 12/31 종료 후 1회 + 재생성 가능

---

## 7. 예외 케이스 처리

- 부분 체결: 체결 단위 합산 후 실현손익 계산
- 입출금/보정 거래: `entry_type` 기준 분리 집계
- 늦게 도착한 체결 이벤트: 재생성 시점에 반영하고 manifest 버전 갱신
- 데이터 불일치: 산출 중단 후 `MANUAL_REVIEW`

---

## 8. 테스트 시나리오

1. 월말 산출물 3종 생성 성공
2. 합계 검증(`realized_trades` vs `ledger_entries`) 통과
3. 누락/중복 fill 검증 통과
4. 동일 기간 재생성 시 체크섬 동일
5. 검증 실패 시 manifest에 실패 원인 기록

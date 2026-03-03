# 실거래 전환 실행 가이드 (50,000 KRW 파일럿)

작성일: 2026-03-03 (KST)  
대상 코드베이스: `ai_invest`

## 1) 결론 요약

- 현재 코드 경로는 **paper/live 공용 런타임**으로 동작한다.
- `universe.mode=live` + `live_execution_enabled=true` + `ENABLE_LIVE_TRADING=true` 조건에서 업비트 실주문이 나간다.
- 50,000 KRW로 실거래 파일럿은 가능하다.
- BTC가 1억 KRW여도 **금액 기준 소수점 매수**가 가능하다.

## 2) 현재 상태 진단 (코드 기준)

### 2.1 실행 경로

- `scripts/run_multi_orchestrator.py` -> `scripts/run_paper_loop.py`
- `scripts/run_paper_loop.py` -> `ai_invest/runtime/paper_loop.py`
- `ai_invest/runtime/paper_loop.py`
  - `universe.mode=paper` -> `ai_invest/execution/paper_execution.py` (`PaperExecutor`)
  - `universe.mode=live` -> `ai_invest/execution/live_execution.py` (`LiveExecutor`)

### 2.2 실주문 구현 상태

- 업비트 private 클라이언트 구현:
  - `ai_invest/execution/upbit_private.py`
  - `GET /v1/accounts`, `POST /v1/orders`, `GET /v1/order`, `DELETE /v1/order`
- 라이브 계좌 동기화 구현:
  - `ai_invest/execution/live_sync.py`
- 실주문 실행기 구현:
  - `ai_invest/execution/live_execution.py`

### 2.3 게이트 설정

- 라이브 실행 조건(3중):
1. `universe.mode: live`
2. `governance.activation_gate.live_execution_enabled: true`
3. 환경변수 `ENABLE_LIVE_TRADING=true`

## 3) 50,000 KRW로 BTC 소수점 매수 가능한가

가능하다. 조건은 아래 2개다.

1. 업비트 KRW 마켓 최소 주문 가능 금액 이상일 것  
2. 주문 타입이 금액 기반(시장가 매수 `ord_type=price`) 또는 수량 기반(지정가) 규칙을 충족할 것

업비트 KRW 마켓 공식 최소 주문 가능 금액은 `5,000 KRW`다.

예시 (수수료 제외 단순 계산):

- BTC 가격: 100,000,000 KRW
- 매수 금액: 50,000 KRW
- 예상 수량: `50,000 / 100,000,000 = 0.0005 BTC`

즉 `0.00%`처럼 매우 작은 BTC 단위로도 매수된다.

## 4) 실거래 가능 구조 완료 상태

완료된 항목:

1. Live 주문 클라이언트 구현 완료
2. `LiveExecutor` 구현 완료
3. `universe.mode=live` 라우팅 완료
4. 주문 상태 전이/이벤트 반영 완료
5. `reconciliation_status=FAIL` 시 `PAUSE` 유지
6. Safe Judge 실행권/AI shadow 분리 유지

운영 전 최종 확인 항목:

1. 실계좌 소액 스모크 테스트(BUY->SELL 1회)
2. 체결 지연/부분체결 로그 점검
3. Telegram 알림/PAUSE 알림 동작 점검

## 5) 구현 로드맵 (권장 순서)

### Step A. Exchange Private Client 추가

- 신규 파일 권장:
  - `ai_invest/execution/upbit_private.py`
- 최소 기능:
  - JWT 서명
  - 계좌 조회
  - 주문 생성
  - 주문 조회
  - 주문 취소
- 출력은 내부 표준 스키마로 정규화

### Step B. LiveExecutor 구현

- 신규 파일 권장:
  - `ai_invest/execution/live_execution.py`
- 책임:
  - Safe Judge 결정 수신
  - 주문 요청/체결 반영
  - `orders`, `fills`, `execution_metrics`, `ledger_entries` 저장
  - 실패 시 reason code 및 이벤트 기록

### Step C. Runtime 라우팅 분기

- 수정 대상:
  - `ai_invest/runtime/paper_loop.py` 또는 `runtime/live_loop.py` 신규 분리
- 요구사항:
  - `universe.mode == "paper"` -> `PaperExecutor`
  - `universe.mode == "live"` -> `LiveExecutor`
  - 공통 게이트(`Risk/Ops/Safe Judge`)는 동일 적용

### Step D. 테스트 보강

- 단위 테스트:
  - 주문 파라미터 검증
  - 상태 전이 검증
  - 오류 코드 매핑 검증
- 통합 테스트:
  - 모의 응답 기반 `BUY->FILL->SELL` 시나리오
  - recon FAIL, 429, 타임아웃 시 `PAUSE` 시나리오

### Step E. 운영 런북 확정

- PAUSE/RESUME 수동 절차
- 재시작 절차
- 일손실 초과 시 당일 거래중지 절차

## 6) 50,000 KRW 파일럿 운영안 (실거래 시작 전제)

목표는 “수익 극대화”가 아니라 “실행 신뢰성 검증”이다.

1. 심볼 고정: `KRW-BTC` 단일 심볼
2. 최대 진입 비중: 계좌의 20% 내외 (1회 약 10,000 KRW)
3. 일 최대 진입 횟수: 2~3회
4. 일 손실 한도: -1.0% 도달 시 자동 `PAUSE`
5. 스프레드 급등 시 진입 금지
6. 최소 주문금액은 5,000 KRW보다 충분히 큰 값으로 유지 (`execution.min_order_krw: 10000` 권장)

## 7) `rules.yaml` 변경 초안 (실거래 구현 이후에만 적용)

```yaml
universe:
  mode: live
  symbols: [KRW-BTC]
  max_open_positions: 1

governance:
  activation_gate:
    live_execution_enabled: true
  micro_mode:
    max_position_pct: 20.0
    max_trades_per_day: 3
    max_daily_loss_pct: 1.0

execution:
  min_order_krw: 10000
```

추가로 환경변수도 반드시 켜야 한다.

```bash
export ENABLE_LIVE_TRADING=true
```

## 8) 실행 체크리스트

### 8.1 사전 체크

1. `scripts/smoke_tests.py` 통과 (DB/Telegram/Upbit auth)
2. 업비트 API 키 권한 확인(주문 권한)
3. 2FA/출금보안/접속 IP 설정 점검

### 8.2 배포 전 체크

1. 페이퍼 모드 7일 이상 연속 무장애
2. 주문/체결/원장/알림 이벤트 누락률 0%
3. recon FAIL 시 PAUSE 동작 검증

### 8.3 실거래 시작일 체크

1. 시작 자금 50,000 KRW
2. 자동매매 시작 후 1시간은 수동 모니터링
3. 이상 징후 시 즉시 `PAUSE`

### 8.4 게이트 과차단 점검 (필수)

실거래 전에는 “안전 게이트가 과하게 막고 있지 않은지”를 수치로 확인해야 한다.

```bash
./.venv/bin/python scripts/check_gate_blocking.py --hours 24
```

권장 해석 기준:

1. `HOLD` 비중이 95% 이상이고 `BUY` 비중이 1.5% 이하이면 과차단 가능성 높음
2. 상위 사유가 `RG_SPREAD_TOO_WIDE`면 진입 스프레드 정책/대상 심볼 유동성 점검
3. 상위 사유가 `RG_EDGE_TOO_LOW`면 알파 임계값/비용 모델/체결비용 추정 재조정

## 9) 참고 문서 (공식)

- 업비트 KRW 마켓 최소주문/호가 단위:  
  https://docs.upbit.com/kr/v1.5.9/docs/krw-market-info
- 업비트 주문 API(시장가 매수 `ord_type=price`, 금액 기준):  
  https://global-docs.upbit.com/reference/order

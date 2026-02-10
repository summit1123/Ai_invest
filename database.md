# database.md - 코인 자동매매(PnL-first) DB/스토리지 설계 (v1.1, DB-only)

> 이 문서는 **데이터베이스/스토리지**만 다룬다.  
> 아키텍처/에이전트/운영 정책은 `architecture.md`, `agents.md`, `guidelines.md`를 참고.
> Telegram 알림 규격/운영 Runbook/세금 산출/GitHub 변경관리는 각각 `notifications_telegram.md`, `ops_runbook.md`, `tax_reporting.md`, `engineering_change_management.md`를 참고.

---

## 1) 저장소 구성

1. Time-series/Data Lake: `Parquet + DuckDB`
2. OLTP/Event Store: `Postgres`
3. Vector Store: `FAISS` 또는 `pgvector/Qdrant`

역할 분리:
- 실시간 쓰기/조회 기준 원장: Postgres
- 리서치/백테스트 집계: DuckDB
- 유사사례 검색: Vector Store

---

## 2) 공통 키/컬럼 규칙

- 기본 시간: `TIMESTAMPTZ` (UTC)
- 식별자: `UUID` 우선
- 실행 추적: `run_id`
- 룰 추적: `rule_version_id`
- 확장 필드: `JSONB`
- 이벤트 저장: append-only (`events`)

---

## 3) 핵심 테이블 (v1.0 + v1.1 공통)

### 3.1 events (Event Store)
```sql
CREATE TABLE events (
  event_id         UUID PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL,
  event_type       TEXT NOT NULL,
  entity_type      TEXT NOT NULL,
  entity_id        TEXT NOT NULL,
  run_id           UUID,
  rule_version_id  UUID,
  payload          JSONB NOT NULL
);

CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_type_ts ON events(event_type, ts);
CREATE INDEX idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX idx_events_run ON events(run_id);
```

권장 `event_type` 최소셋:
- System: `SYSTEM_START`, `SYSTEM_STOP`, `CLOCK_IN`, `CLOCK_OUT`
- Data: `MARKET_SNAPSHOT`, `FEATURE_SNAPSHOT`, `ORDERBOOK_SNAPSHOT`
- Decision: `AGENT_OPINION`, `SAFE_DECISION`, `AI_DECISION`
- Trading: `ORDER_SUBMITTED`, `ORDER_ACK`, `ORDER_CANCELED`, `ORDER_REJECTED`, `FILL`
- Ops: `RISK_VETO`, `REGIME_BLOCK`, `PAUSE`, `RESUME`, `RECONCILIATION_FAIL`
- Rules: `RULE_PROPOSAL`, `RULE_APPROVED`, `RULE_REJECTED`, `RULE_ACTIVATED`
- Governance/Finance: `WEEKLY_PRIORITY_SET`, `IMPROVEMENT_ACTION_ASSIGNED`, `TAX_EXPORT_COMPLETED`, `TAX_EXPORT_FAILED`
- Research/Meeting: `AGENT_DAILY_REPORT`, `RESEARCH_DAILY_BRIEF`, `MEETING_STARTED`, `MEETING_MESSAGE`, `MEETING_SUMMARY`, `MEETING_ACTION_ASSIGNED`
- Change/GitOps: `CHANGE_PROPOSAL`, `CHANGE_VALIDATED`, `PR_OPENED`, `CI_PASSED`, `CI_FAILED`, `PR_READY`, `PR_APPROVED`, `PR_MERGED`, `ROLLBACK_EXECUTED`
- Review: `AAR_NOTE`, `MISTAKE_TAG`, `DECISION_OUTCOME_RECORDED`

### 3.2 runs
```sql
CREATE TABLE runs (
  run_id         UUID PRIMARY KEY,
  run_type       TEXT NOT NULL,           -- LIVE / PAPER / BACKTEST
  started_at     TIMESTAMPTZ NOT NULL,
  ended_at       TIMESTAMPTZ,
  description    TEXT,
  config         JSONB NOT NULL,
  git_commit     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.3 rule_versions
```sql
CREATE TABLE rule_versions (
  rule_version_id UUID PRIMARY KEY,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by      TEXT NOT NULL,          -- human / ai / system
  parent_version  UUID,
  status          TEXT NOT NULL,          -- DRAFT / CANDIDATE / ACTIVE / RETIRED
  summary         TEXT NOT NULL,
  rules_dsl       JSONB NOT NULL,
  diff            JSONB,
  backtest_report JSONB
);

CREATE INDEX idx_rule_versions_status ON rule_versions(status, created_at DESC);
```

### 3.4 agent_opinions
```sql
CREATE TABLE agent_opinions (
  opinion_id      UUID PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL,
  symbol          TEXT NOT NULL,
  agent_name      TEXT NOT NULL,
  signal          TEXT NOT NULL,          -- LONG / SHORT / HOLD / NONE
  confidence      DOUBLE PRECISION NOT NULL,
  horizon         TEXT,
  features        JSONB,
  reason          JSONB,
  raw_payload     JSONB NOT NULL,
  run_id          UUID,
  rule_version_id UUID
);

CREATE INDEX idx_opinions_ts_symbol ON agent_opinions(symbol, ts DESC);
CREATE INDEX idx_opinions_agent_ts ON agent_opinions(agent_name, ts DESC);
```

### 3.5 decisions
```sql
CREATE TABLE decisions (
  decision_id      UUID PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL,
  symbol           TEXT NOT NULL,
  judge_type       TEXT NOT NULL,         -- SAFE / AI
  action           TEXT NOT NULL,         -- BUY / SELL / HOLD / PAUSE
  score            DOUBLE PRECISION,
  confidence       DOUBLE PRECISION,
  gates            JSONB NOT NULL,
  selected_reasons JSONB,                -- reason_codes.md 표준 코드 배열
  rejected_reasons JSONB,                -- reason_codes.md 표준 코드 배열
  expected_cost_bps DOUBLE PRECISION,
  expected_rr      DOUBLE PRECISION,
  run_id           UUID,
  rule_version_id  UUID
);

CREATE INDEX idx_decisions_ts_symbol ON decisions(symbol, ts DESC);
CREATE INDEX idx_decisions_judge_ts ON decisions(judge_type, ts DESC);
```

### 3.6 orders / fills
```sql
CREATE TABLE orders (
  order_id        TEXT PRIMARY KEY,
  ts_created      TIMESTAMPTZ NOT NULL,
  symbol          TEXT NOT NULL,
  side            TEXT NOT NULL,          -- BUY / SELL
  order_type      TEXT NOT NULL,
  price           DOUBLE PRECISION,
  quantity        DOUBLE PRECISION NOT NULL,
  time_in_force   TEXT,
  status          TEXT NOT NULL,          -- NEW/ACK/PARTIAL/FILLED/CANCELED/REJECTED
  client_order_id TEXT,
  meta            JSONB,
  run_id          UUID,
  rule_version_id UUID
);

CREATE INDEX idx_orders_ts_symbol ON orders(symbol, ts_created DESC);
CREATE INDEX idx_orders_status ON orders(status, ts_created DESC);

CREATE TABLE fills (
  fill_id        UUID PRIMARY KEY,
  order_id       TEXT NOT NULL REFERENCES orders(order_id),
  ts_filled      TIMESTAMPTZ NOT NULL,
  price          DOUBLE PRECISION NOT NULL,
  quantity       DOUBLE PRECISION NOT NULL,
  fee            DOUBLE PRECISION,
  fee_currency   TEXT,
  liquidity      TEXT,
  meta           JSONB
);

CREATE INDEX idx_fills_order ON fills(order_id);
CREATE INDEX idx_fills_ts ON fills(ts_filled DESC);
```

### 3.7 positions / pnl_daily
```sql
CREATE TABLE positions (
  symbol          TEXT PRIMARY KEY,
  ts_updated      TIMESTAMPTZ NOT NULL,
  qty             DOUBLE PRECISION NOT NULL,
  avg_entry_price DOUBLE PRECISION,
  unrealized_pnl  DOUBLE PRECISION,
  stop_price      DOUBLE PRECISION,
  take_profit     DOUBLE PRECISION,
  meta            JSONB
);

CREATE TABLE pnl_daily (
  day             DATE PRIMARY KEY,
  realized_pnl    DOUBLE PRECISION NOT NULL,
  fees_paid       DOUBLE PRECISION NOT NULL,
  trades_count    INTEGER NOT NULL,
  max_drawdown    DOUBLE PRECISION,
  notes           JSONB
);
```

### 3.8 casebook_docs
```sql
CREATE TABLE casebook_docs (
  doc_id          UUID PRIMARY KEY,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  symbol          TEXT NOT NULL,
  timeframe       TEXT NOT NULL,
  ts_anchor       TIMESTAMPTZ NOT NULL,
  outcome_label   TEXT,
  pnl_bps         DOUBLE PRECISION,
  tags            JSONB,
  text            TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  embedding_dim   INTEGER NOT NULL,
  vector_id       TEXT
);

CREATE INDEX idx_casebook_symbol_ts ON casebook_docs(symbol, ts_anchor DESC);
```

(선택: pgvector)
```sql
-- CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE casebook_docs ADD COLUMN embedding vector(1536);
CREATE INDEX idx_casebook_embedding ON casebook_docs USING ivfflat (embedding vector_cosine_ops);
```

---

## 4) v1.1 하드닝 신규 테이블

### 4.1 market_quotes
```sql
CREATE TABLE market_quotes (
  ts            TIMESTAMPTZ NOT NULL,
  symbol        TEXT NOT NULL,
  best_bid      DOUBLE PRECISION,
  best_ask      DOUBLE PRECISION,
  mid_price     DOUBLE PRECISION,
  spread_abs    DOUBLE PRECISION,
  spread_bps    DOUBLE PRECISION,
  source        TEXT,
  PRIMARY KEY (symbol, ts)
);

CREATE INDEX idx_quotes_ts ON market_quotes(ts DESC);
```

### 4.2 orderbook_l2_snapshots
```sql
CREATE TABLE orderbook_l2_snapshots (
  snapshot_id   UUID PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  symbol        TEXT NOT NULL,
  depth         INTEGER NOT NULL,
  bids          JSONB NOT NULL,
  asks          JSONB NOT NULL,
  meta          JSONB
);

CREATE INDEX idx_ob_ts_symbol ON orderbook_l2_snapshots(symbol, ts DESC);
```

### 4.3 execution_metrics (TCA-lite)
```sql
CREATE TABLE execution_metrics (
  metric_id     UUID PRIMARY KEY,
  order_id      TEXT,
  symbol        TEXT NOT NULL,
  ts_decision   TIMESTAMPTZ,
  ts_submit     TIMESTAMPTZ,
  ts_first_fill TIMESTAMPTZ,
  ts_last_fill  TIMESTAMPTZ,
  decision_mid  DOUBLE PRECISION,
  submit_mid    DOUBLE PRECISION,
  fill_vwap     DOUBLE PRECISION,
  slippage_bps_vs_decision DOUBLE PRECISION,
  slippage_bps_vs_submit   DOUBLE PRECISION,
  spread_bps_at_submit     DOUBLE PRECISION,
  filled_ratio  DOUBLE PRECISION,
  latency_ms_decision_to_submit INTEGER,
  latency_ms_submit_to_fill     INTEGER,
  meta          JSONB
);

CREATE INDEX idx_execm_order ON execution_metrics(order_id);
CREATE INDEX idx_execm_ts ON execution_metrics(ts_submit DESC);
```

슬리피지 벤치마크 고정:
- `decision_mid`: 결정 시점 mid
- `submit_mid`: 주문 제출 시점 mid
- `fill_vwap`: 체결 VWAP

### 4.4 balances_snapshots / reconciliation_checks
```sql
CREATE TABLE balances_snapshots (
  snapshot_id   UUID PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  currency      TEXT NOT NULL,
  free          DOUBLE PRECISION NOT NULL,
  locked        DOUBLE PRECISION NOT NULL,
  source        TEXT NOT NULL,
  meta          JSONB
);

CREATE INDEX idx_bal_ts_curr ON balances_snapshots(currency, ts DESC);

CREATE TABLE reconciliation_checks (
  check_id      UUID PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  scope         TEXT NOT NULL,           -- ORDER / FILL / POSITION / BALANCE
  symbol        TEXT,
  status        TEXT NOT NULL,           -- OK / WARN / FAIL
  diff_summary  TEXT,
  diff_payload  JSONB,
  action_taken  TEXT,                    -- NONE / PAUSE / RESYNC / MANUAL_REVIEW
  run_id        UUID
);

CREATE INDEX idx_recon_ts ON reconciliation_checks(ts DESC);
CREATE INDEX idx_recon_status ON reconciliation_checks(status, ts DESC);
```

### 4.5 pause_log
```sql
CREATE TABLE pause_log (
  pause_id       UUID PRIMARY KEY,
  ts_pause       TIMESTAMPTZ NOT NULL,
  ts_resume      TIMESTAMPTZ,
  reason_type    TEXT NOT NULL,          -- DATA_BAD / RATE_LIMIT / RECON_FAIL / HIGH_VOL / DAILY_LOSS / MANUAL
  severity       TEXT NOT NULL,          -- LOW / MED / HIGH
  auto_resumable BOOLEAN NOT NULL,
  resume_policy  JSONB,
  notes          TEXT,
  run_id         UUID
);

CREATE INDEX idx_pause_ts ON pause_log(ts_pause DESC);
```

### 4.6 ledger_entries / realized_trades
```sql
CREATE TABLE ledger_entries (
  entry_id      UUID PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  entry_type    TEXT NOT NULL,          -- TRADE_FILL / FEE / DEPOSIT / WITHDRAW / ADJUSTMENT
  symbol        TEXT,
  currency      TEXT NOT NULL,
  amount        DOUBLE PRECISION NOT NULL,
  price         DOUBLE PRECISION,
  fee_amount    DOUBLE PRECISION,
  fee_currency  TEXT,
  order_id      TEXT,
  fill_id       UUID,
  meta          JSONB
);

CREATE INDEX idx_ledger_ts ON ledger_entries(ts DESC);
CREATE INDEX idx_ledger_curr ON ledger_entries(currency, ts DESC);

CREATE TABLE realized_trades (
  trade_id        UUID PRIMARY KEY,
  symbol          TEXT NOT NULL,
  ts_open         TIMESTAMPTZ NOT NULL,
  ts_close        TIMESTAMPTZ NOT NULL,
  side            TEXT NOT NULL,
  qty             DOUBLE PRECISION NOT NULL,
  avg_entry_price DOUBLE PRECISION NOT NULL,
  avg_exit_price  DOUBLE PRECISION NOT NULL,
  realized_pnl    DOUBLE PRECISION NOT NULL,
  fees_total      DOUBLE PRECISION NOT NULL,
  pnl_bps         DOUBLE PRECISION,
  tags            JSONB,
  meta            JSONB
);

CREATE INDEX idx_realized_close ON realized_trades(ts_close DESC);
```

### 4.7 shadow_policy / shadow_trades
```sql
CREATE TABLE shadow_policy (
  policy_id      UUID PRIMARY KEY,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  name           TEXT NOT NULL,
  cost_model     JSONB NOT NULL,
  fill_model     JSONB NOT NULL,
  benchmark_rule JSONB NOT NULL,
  notes          TEXT
);

CREATE TABLE shadow_trades (
  trade_id       UUID PRIMARY KEY,
  policy_id      UUID NOT NULL REFERENCES shadow_policy(policy_id),
  judge_type     TEXT NOT NULL,
  ts_open        TIMESTAMPTZ NOT NULL,
  ts_close       TIMESTAMPTZ,
  symbol         TEXT NOT NULL,
  side           TEXT NOT NULL,
  entry_price    DOUBLE PRECISION NOT NULL,
  exit_price     DOUBLE PRECISION,
  pnl_bps        DOUBLE PRECISION,
  meta           JSONB
);

CREATE INDEX idx_shadow_close ON shadow_trades(ts_close DESC);
```

### 4.8 strategy_reviews / tax_export_runs
```sql
CREATE TABLE strategy_reviews (
  review_id        UUID PRIMARY KEY,
  week_start       DATE NOT NULL,
  week_end         DATE NOT NULL,
  priority_title   TEXT NOT NULL,
  hypothesis       TEXT NOT NULL,
  owner            TEXT NOT NULL,
  success_criteria JSONB NOT NULL,
  status           TEXT NOT NULL,          -- OPEN / IN_PROGRESS / DONE / CANCELED
  evidence         JSONB,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_id           UUID
);

CREATE INDEX idx_strategy_reviews_week ON strategy_reviews(week_start DESC);
CREATE INDEX idx_strategy_reviews_status ON strategy_reviews(status, created_at DESC);

CREATE TABLE tax_export_runs (
  export_id        UUID PRIMARY KEY,
  period_start     TIMESTAMPTZ NOT NULL,
  period_end       TIMESTAMPTZ NOT NULL,
  generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  status           TEXT NOT NULL,          -- STARTED / COMPLETED / FAILED
  checksum_sha256  TEXT,
  generated_by     TEXT NOT NULL,
  manifest         JSONB NOT NULL,
  meta             JSONB
);

CREATE INDEX idx_tax_export_runs_period ON tax_export_runs(period_start DESC, period_end DESC);
CREATE INDEX idx_tax_export_runs_status ON tax_export_runs(status, generated_at DESC);
```

### 4.9 notification_deliveries (필수)

```sql
CREATE TABLE notification_deliveries (
  delivery_id      UUID PRIMARY KEY,
  event_id         UUID NOT NULL REFERENCES events(event_id),
  channel          TEXT NOT NULL,                 -- TELEGRAM / SLACK
  template_id      TEXT NOT NULL,
  severity         TEXT NOT NULL,                 -- CRITICAL / HIGH / NORMAL
  status           TEXT NOT NULL,                 -- PENDING / SENT / FAILED
  attempt_count    INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT,
  dedupe_key       TEXT,
  payload          JSONB NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at          TIMESTAMPTZ
);

CREATE INDEX idx_notification_deliveries_status ON notification_deliveries(status, created_at DESC);
CREATE INDEX idx_notification_deliveries_dedupe ON notification_deliveries(dedupe_key, created_at DESC);
CREATE INDEX idx_notification_deliveries_event ON notification_deliveries(event_id);
```

### 4.10 change_proposals / github_pr_runs (필수)

```sql
CREATE TABLE change_proposals (
  proposal_id      UUID PRIMARY KEY,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by       TEXT NOT NULL,         -- service_engineering_agent / human
  title            TEXT NOT NULL,
  proposal_type    TEXT NOT NULL,         -- CODE_CHANGE / DOC_CHANGE / CONFIG_CHANGE
  risk_level       TEXT NOT NULL,         -- LOW / MEDIUM / HIGH
  status           TEXT NOT NULL,         -- DRAFT / VALIDATED / PR_OPENED / MERGED / REJECTED / ROLLED_BACK
  payload          JSONB NOT NULL
);

CREATE INDEX idx_change_proposals_status ON change_proposals(status, created_at DESC);

CREATE TABLE github_pr_runs (
  pr_run_id        UUID PRIMARY KEY,
  proposal_id      UUID NOT NULL REFERENCES change_proposals(proposal_id),
  repo             TEXT NOT NULL,
  base_branch      TEXT NOT NULL,
  head_branch      TEXT NOT NULL,
  pr_number        INTEGER,
  pr_url           TEXT,
  commit_sha       TEXT,
  ci_status        TEXT,                  -- PENDING / PASSED / FAILED
  approval_status  TEXT,                  -- PENDING / APPROVED / CHANGES_REQUESTED
  merged_at        TIMESTAMPTZ,
  payload          JSONB,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_github_pr_runs_proposal ON github_pr_runs(proposal_id, created_at DESC);
CREATE INDEX idx_github_pr_runs_ci ON github_pr_runs(ci_status, created_at DESC);
```

### 4.11 decision_outcomes (필수)

```sql
CREATE TABLE decision_outcomes (
  outcome_id        UUID PRIMARY KEY,
  decision_id       UUID NOT NULL REFERENCES decisions(decision_id),
  trade_id          UUID,
  symbol            TEXT NOT NULL,
  ts_open           TIMESTAMPTZ,
  ts_close          TIMESTAMPTZ,
  outcome_label     TEXT NOT NULL,         -- WIN / LOSS / FLAT / MISS
  error_type        TEXT,                  -- reason_codes.md의 OC_* 코드 사용
  root_cause        TEXT,
  evidence_refs     JSONB,                 -- event_id / order_id / fill_id 참조 목록
  fix_hypothesis    TEXT,
  reviewed_by       TEXT NOT NULL,         -- system / agent / human
  reviewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_id            UUID,
  rule_version_id   UUID,
  meta              JSONB
);

CREATE INDEX idx_decision_outcomes_decision ON decision_outcomes(decision_id);
CREATE INDEX idx_decision_outcomes_close ON decision_outcomes(ts_close DESC);
CREATE INDEX idx_decision_outcomes_error ON decision_outcomes(error_type, reviewed_at DESC);
```

### 4.12 communication_rooms / agent_daily_reports / meeting_sessions / meeting_messages (필수)

```sql
CREATE TABLE communication_rooms (
  room_id           UUID PRIMARY KEY,
  channel_type      TEXT NOT NULL,                -- TELEGRAM / SLACK
  room_key          TEXT NOT NULL,                -- chat_id or channel_id
  room_name         TEXT NOT NULL,                -- ops-critical, research-daily ...
  team_scope        TEXT NOT NULL,                -- OPS / TRADING / RESEARCH / MEETING / ENGINEERING / REVIEW
  is_active         BOOLEAN NOT NULL DEFAULT true,
  meta              JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_communication_rooms ON communication_rooms(channel_type, room_key);
CREATE INDEX idx_communication_rooms_scope ON communication_rooms(team_scope, is_active);

CREATE TABLE agent_daily_reports (
  report_id         UUID PRIMARY KEY,
  report_date       DATE NOT NULL,
  agent_name        TEXT NOT NULL,
  team_scope        TEXT NOT NULL,                -- RESEARCH / RISK / OPS / MARKET / GOVERNANCE ...
  title             TEXT NOT NULL,
  summary           TEXT NOT NULL,
  findings          JSONB,
  risks             JSONB,
  action_items      JSONB,
  run_id            UUID,
  rule_version_id   UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_daily_reports_date ON agent_daily_reports(report_date DESC, agent_name);
CREATE INDEX idx_agent_daily_reports_scope ON agent_daily_reports(team_scope, report_date DESC);

CREATE TABLE meeting_sessions (
  meeting_id        UUID PRIMARY KEY,
  meeting_type      TEXT NOT NULL,                -- DAILY_RESEARCH / WEEKLY_STRATEGY / INCIDENT_REVIEW
  status            TEXT NOT NULL,                -- OPEN / CLOSED
  started_at        TIMESTAMPTZ NOT NULL,
  ended_at          TIMESTAMPTZ,
  facilitator       TEXT NOT NULL,
  participants      JSONB NOT NULL,
  agenda            JSONB,
  summary           TEXT,
  decisions         JSONB,
  action_items      JSONB,
  run_id            UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_meeting_sessions_started ON meeting_sessions(started_at DESC);
CREATE INDEX idx_meeting_sessions_type ON meeting_sessions(meeting_type, started_at DESC);

CREATE TABLE meeting_messages (
  message_id        UUID PRIMARY KEY,
  meeting_id        UUID NOT NULL REFERENCES meeting_sessions(meeting_id),
  ts                TIMESTAMPTZ NOT NULL,
  sender_agent      TEXT NOT NULL,
  message_type      TEXT NOT NULL,                -- CLAIM / EVIDENCE / PROPOSAL / QUESTION / ACTION_ITEM
  content           TEXT NOT NULL,
  payload           JSONB,
  confidence        DOUBLE PRECISION,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_meeting_messages_meeting ON meeting_messages(meeting_id, ts);
CREATE INDEX idx_meeting_messages_agent ON meeting_messages(sender_agent, ts DESC);
```

---

## 5) v1.2 후보 (선택, 뉴스/이벤트 확장)

```sql
CREATE TABLE documents_raw (
  doc_id          UUID PRIMARY KEY,
  source          TEXT NOT NULL,
  published_at    TIMESTAMPTZ,
  collected_at    TIMESTAMPTZ NOT NULL,
  title           TEXT,
  body            TEXT,
  meta            JSONB
);

CREATE TABLE extracted_events (
  event_id        UUID PRIMARY KEY,
  doc_id          UUID REFERENCES documents_raw(doc_id),
  ts              TIMESTAMPTZ NOT NULL,
  severity        TEXT,
  impact          TEXT,
  action_hint     TEXT,
  payload         JSONB
);

CREATE TABLE event_symbol_map (
  map_id          UUID PRIMARY KEY,
  event_id        UUID REFERENCES extracted_events(event_id),
  symbol          TEXT NOT NULL,
  weight          DOUBLE PRECISION
);
```

---

## 6) 마이그레이션 순서(권장)

1. `events`, `runs`, `rule_versions`
2. `agent_opinions`, `decisions`, `orders`, `fills`, `positions`, `pnl_daily`
3. `market_quotes`, `orderbook_l2_snapshots`, `execution_metrics`
4. `balances_snapshots`, `reconciliation_checks`, `pause_log`
5. `ledger_entries`, `realized_trades`
6. `notification_deliveries`, `communication_rooms`, `agent_daily_reports`, `meeting_sessions`, `meeting_messages`
7. `strategy_reviews`, `tax_export_runs`, `decision_outcomes`
8. `change_proposals`, `github_pr_runs`
9. `casebook_docs`, `shadow_policy`, `shadow_trades`
10. (선택) `documents_raw`, `extracted_events`, `event_symbol_map`

---

## 7) 운영용 DB 체크 항목

- 인덱스 누락 점검 (`EXPLAIN ANALYZE` 기준)
- 대용량 테이블 파티셔닝 검토 (`events`, `fills`, `market_quotes`)
- 보관 정책:
  - 원본 이벤트: 장기 보관
  - L2 스냅샷: 단기 보관 + 집계만 장기 보관
- 백업:
  - 일 단위 전체 백업 + PITR(가능 시)

---

## 8) 관련 문서

- 아키텍처: `architecture.md`
- 에이전트 계약: `agents.md`
- 운영 정책: `guidelines.md`
- 룰 기본값: `rules.yaml`
- 원인코드 사전: `reason_codes.md`
- 주문 상태머신: `order_state_machine.md`
- Telegram 알림 표준: `notifications_telegram.md`
- 장애 대응 절차: `ops_runbook.md`
- 세금/정산 리포팅: `tax_reporting.md`
- GitHub 변경관리: `engineering_change_management.md`

-- 최소 MVP 스키마: events / decisions / orders / fills / notification_deliveries
--
-- NOTE:
-- - pgvector는 v1.1 casebook/embedding 용도로만 필요하며 "선택"이다(database.md 참고).
-- - vector 기능이 필요하면 `sql/optional_pgvector.sql`을 별도로 적용한다.

CREATE TABLE IF NOT EXISTS events (
  event_id         UUID PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL,
  event_type       TEXT NOT NULL,
  entity_type      TEXT NOT NULL,
  entity_id        TEXT NOT NULL,
  run_id           UUID,
  rule_version_id  UUID,
  payload          JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id       UUID PRIMARY KEY,
  ts                TIMESTAMPTZ NOT NULL,
  symbol            TEXT NOT NULL,
  judge_type        TEXT NOT NULL,
  action            TEXT NOT NULL,
  score             DOUBLE PRECISION,
  confidence        DOUBLE PRECISION,
  gates             JSONB NOT NULL,
  selected_reasons  JSONB,
  rejected_reasons  JSONB,
  expected_cost_bps DOUBLE PRECISION,
  expected_rr       DOUBLE PRECISION,
  run_id            UUID,
  rule_version_id   UUID
);

CREATE INDEX IF NOT EXISTS idx_decisions_ts_symbol ON decisions(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_judge_ts ON decisions(judge_type, ts DESC);

CREATE TABLE IF NOT EXISTS orders (
  order_id         TEXT PRIMARY KEY,
  ts_created       TIMESTAMPTZ NOT NULL,
  symbol           TEXT NOT NULL,
  side             TEXT NOT NULL,
  order_type       TEXT NOT NULL,
  price            DOUBLE PRECISION,
  quantity         DOUBLE PRECISION NOT NULL,
  time_in_force    TEXT,
  status           TEXT NOT NULL,
  client_order_id  TEXT,
  meta             JSONB,
  run_id           UUID,
  rule_version_id  UUID
);

CREATE INDEX IF NOT EXISTS idx_orders_ts_symbol ON orders(symbol, ts_created DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, ts_created DESC);

CREATE TABLE IF NOT EXISTS fills (
  fill_id         UUID PRIMARY KEY,
  order_id        TEXT NOT NULL REFERENCES orders(order_id),
  ts_filled       TIMESTAMPTZ NOT NULL,
  price           DOUBLE PRECISION NOT NULL,
  quantity        DOUBLE PRECISION NOT NULL,
  fee             DOUBLE PRECISION,
  fee_currency    TEXT,
  liquidity       TEXT,
  meta            JSONB
);

CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts_filled DESC);

CREATE TABLE IF NOT EXISTS notification_deliveries (
  delivery_id      UUID PRIMARY KEY,
  event_id         UUID NOT NULL REFERENCES events(event_id),
  channel          TEXT NOT NULL,
  template_id      TEXT NOT NULL,
  severity         TEXT NOT NULL,
  status           TEXT NOT NULL,
  attempt_count    INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT,
  dedupe_key       TEXT,
  payload          JSONB NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status
  ON notification_deliveries(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_dedupe
  ON notification_deliveries(dedupe_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_event
  ON notification_deliveries(event_id);

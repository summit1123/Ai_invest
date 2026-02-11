export type ApiMeta = {
  ts_utc: string
  request_id: string
}

export type ApiOk<T> = {
  ok: true
  data: T
  meta: ApiMeta
}

export type ApiError = {
  code: string
  message_ko: string
  details?: unknown
}

export type ApiFail = {
  ok: false
  error: ApiError
  meta: ApiMeta
}

export type ApiResponse<T> = ApiOk<T> | ApiFail

export type DecisionView = {
  decision_id: string
  ts: string
  symbol: string
  judge_type: 'SAFE' | 'AI'
  action: 'BUY' | 'SELL' | 'HOLD' | 'PAUSE'
  score: number | null
  confidence: number | null
  selected_reasons: string[] | null
  rejected_reasons: string[] | null
  gates: unknown
  expected_cost_bps?: number | null
  expected_rr?: number | null
}

export type TodayOverview = {
  latest_safe_decision: DecisionView | null
  latest_ai_decision: DecisionView | null
  pause: { paused: boolean; latest: unknown | null }
  latest_reconciliation: unknown | null
}

export type TimelineEvent = {
  event_id: string
  ts: string
  event_type: string
  entity_type: string
  entity_id: string
  run_id: string | null
  rule_version_id: string | null
  payload: unknown
}

export type ExecutionMetricView = {
  metric_id: string
  order_id: string | null
  symbol: string
  ts_submit: string | null
  fill_vwap: number | null
  slippage_bps_vs_submit: number | null
  spread_bps_at_submit: number | null
  filled_ratio: number | null
}

export type TaxExportRunView = {
  export_id: string
  period_start: string
  period_end: string
  generated_at: string
  status: 'STARTED' | 'COMPLETED' | 'FAILED'
  checksum_sha256: string | null
  generated_by: string
}

export type LedgerEntryView = {
  entry_id: string
  ts: string
  entry_type: string
  symbol: string | null
  currency: string
  amount: number
  price: number | null
  fee_amount: number | null
  fee_currency: string | null
  order_id: string | null
  fill_id: string | null
}

export type DecisionOutcomeView = {
  outcome_id: string
  reviewed_at: string
  decision_id: string
  trade_id: string | null
  symbol: string
  ts_open: string | null
  ts_close: string | null
  outcome_label: 'WIN' | 'LOSS' | 'FLAT' | 'MISS'
  error_type: string | null
  root_cause: string | null
}

export type LatestDecisionRow = {
  decision_id: string
  ts: string
  symbol: string
  judge_type: 'SAFE' | 'AI'
  action: 'BUY' | 'SELL' | 'HOLD' | 'PAUSE'
  confidence: number | null
  selected_reasons: string[] | null
}

export type CommunicationRoomView = {
  room_id: string
  channel_type: string
  room_key: string
  room_name: string
  team_scope: string
  is_active: boolean
}

export type AgentDailyReportView = {
  report_id: string
  report_date: string
  agent_name: string
  team_scope: string
  title: string
  summary: string
  findings: unknown
  risks: unknown
  action_items: unknown
  created_at: string
}

export type MeetingSessionView = {
  meeting_id: string
  meeting_type: string
  status: string
  started_at: string
  ended_at: string | null
  facilitator: string
  participants: unknown
  summary: string | null
  decisions?: unknown
  action_items: unknown
  run_id: string | null
}

export type MeetingMessageView = {
  message_id: string
  ts: string
  sender_agent: string
  message_type: string
  content: string
  payload: unknown
  confidence: number | null
}

export type StrategyReviewView = {
  review_id: string
  week_start: string
  week_end: string
  priority_title: string
  hypothesis: string
  owner: string
  success_criteria: unknown
  status: string
  evidence: unknown
  created_at: string
}

export type GovernanceStatusView = {
  policy_active: TimelineEvent | null
  policy_proposed: TimelineEvent | null
  plan_active: TimelineEvent | null
  plan_proposed: TimelineEvent | null
  plan_blocked: TimelineEvent | null
  ready_tasks: Record<string, unknown[]>
  completed_tasks: TimelineEvent[]
}

export type AgentOpinionView = {
  opinion_id: string
  ts: string
  symbol: string
  agent_name: string
  signal: string
  confidence: number
  horizon: string | null
  features: unknown
  reason: unknown
  raw_payload: unknown
  run_id: string | null
  rule_version_id: string | null
  decision_id: string | null
}

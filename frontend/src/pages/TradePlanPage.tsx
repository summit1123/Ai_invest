import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import { fmtTsKst } from '../shared/format'
import type { TradePlanPayloadV2 } from '../shared/api/types'

type TradePlanEvent = {
  event_id: string
  ts: string
  event_type: string
  entity_type: string
  entity_id: string
  payload: unknown
}

type TradePlanData = { event: TradePlanEvent | null }

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

export function TradePlanPage() {
  const q = useQuery({
    queryKey: ['trade-plan-latest'],
    queryFn: () => apiGet<TradePlanData>('/api/v1/ui/trade-plan/latest'),
    refetchInterval: 10_000,
  })

  if (q.isLoading) {
    return (
      <div className="page">
        <div className="card">로딩 중...</div>
      </div>
    )
  }
  if (q.isError) {
    return (
      <div className="page">
        <div className="errorBox">
          <div style={{ fontWeight: 700, marginBottom: 8 }}>불러오기 실패</div>
          <div className="mono">{String(q.error)}</div>
        </div>
      </div>
    )
  }

  const ev = q.data?.event ?? null
  const p = (ev?.payload as TradePlanPayloadV2 | null) ?? null
  const activationGate = (p?.activation_gate as any) ?? {}
  const executionPlan = (p?.execution_plan as any) ?? {}
  const executionNumbers = (executionPlan?.final_numbers as any) ?? {}
  const activationDecision = String(activationGate?.decision_effective ?? activationGate?.decision ?? '-')
  const holdMode = String(activationGate?.hold_mode ?? '-')
  const capCfg = (activationGate?.conditional_activation as any) ?? {}
  const capCond = (capCfg?.conditions as any) ?? {}
  const capPromotion = (capCfg?.promotion as any) ?? {}
  const capRuntime = (activationGate?.cap_runtime as any) ?? {}

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>Trade Plan (최신)</h2>
          <span className="pill">{ev ? 'ACTIVE' : 'N/A'}</span>
        </div>

        {!ev ? (
          <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            아직 Trade Plan 이벤트가 없습니다. (예: <span className="mono">TRADE_PLAN_SET</span>)
          </div>
        ) : (
          <>
            <div className="muted" style={{ fontSize: 12 }}>
              시각(KST): <span className="mono">{fmtTsKst(ev.ts)}</span>
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              event_id: <span className="mono">{ev.event_id}</span>
            </div>

            <div className="grid grid2" style={{ marginTop: 12 }}>
              <div className="card">
                <div className="cardTitle">
                  <h2>요약</h2>
                  <span className="pill">plan</span>
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  symbol: <span className="mono">{String(p?.symbol ?? '')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  target_position_pct: <span className="mono">{String(p?.target_position_pct ?? '')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  activation decision: <span className="mono">{activationDecision}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  hold_mode: <span className="mono">{holdMode}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  valid_to: <span className="mono">{String(p?.valid_to_kst ?? p?.valid_to ?? '')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                  execution target: <span className="mono">{String(executionNumbers?.target_position_pct ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  band: <span className="mono">{String(executionNumbers?.rebalance_band_pct ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  cooldown: <span className="mono">{String(executionNumbers?.cooldown_minutes ?? '-')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                  notes: <span className="mono">{String(p?.notes ?? '')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                  CAP enabled: <span className="mono">{String(Boolean(capCfg?.enabled))}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  promoted: <span className="mono">{String(Boolean(activationGate?.cap_promoted))}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  CAP 조건: <span className="mono">alpha≥{String(capCond?.min_alpha ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  <span className="mono">spread≤{String(capCond?.max_spread_bps ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  <span className="mono">vol_z≥{String(capCond?.min_vol_z ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  <span className="mono">atr≥{String(capCond?.min_atr_pct ?? '-')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  CAP 지속: <span className="mono">{String(capCond?.sustain_seconds ?? '-')}s</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  승격 cap: <span className="mono">{String(capPromotion?.target_position_pct_cap ?? '-')}%</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  TTL: <span className="mono">{String(capPromotion?.promotion_ttl_minutes ?? '-')}m</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  CAP 런타임: <span className="mono">{String(capRuntime?.consecutive_passes ?? 0)}</span>
                  /
                  <span className="mono">{String(capRuntime?.required_passes ?? '-')}</span>
                  <span style={{ opacity: 0.35 }}> · </span>
                  expires: <span className="mono">{String(capRuntime?.promote_expires_at ?? '-')}</span>
                </div>
              </div>

              <div className="card">
                <div className="cardTitle">
                  <h2>원문(JSON)</h2>
                  <span className="pill">event</span>
                </div>
                <pre className="codeBlock">{safeJson(ev)}</pre>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

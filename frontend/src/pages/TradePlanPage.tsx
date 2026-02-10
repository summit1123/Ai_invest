import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import { fmtTsKst } from '../shared/format'

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
  const p = (ev?.payload as any) ?? null

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
                  valid_to: <span className="mono">{String(p?.valid_to_kst ?? p?.valid_to ?? '')}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                  notes: <span className="mono">{String(p?.notes ?? '')}</span>
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


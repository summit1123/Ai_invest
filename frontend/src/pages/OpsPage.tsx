import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import { fmtTsKst } from '../shared/format'

type ReconRow = {
  check_id: string
  ts: string
  scope: string
  symbol: string | null
  status: string
  diff_summary: string | null
  action_taken: string | null
}

type PauseRow = {
  pause_id: string
  ts_pause: string
  ts_resume: string | null
  reason_type: string
  severity: string
  auto_resumable: boolean
  notes: string | null
}

type DeliveryRow = {
  delivery_id: string
  created_at: string
  channel: string
  template_id: string
  severity: string
  status: string
  attempt_count: number
  last_error: string | null
}

type ReconData = { items: ReconRow[] }
type PauseData = { items: PauseRow[] }
type DeliveryData = { items: DeliveryRow[] }

export function OpsPage() {
  const qRecon = useQuery({
    queryKey: ['recon'],
    queryFn: () => apiGet<ReconData>('/api/v1/ui/reconciliation-status?limit=50'),
    refetchInterval: 5000,
  })
  const qPause = useQuery({
    queryKey: ['pause-log'],
    queryFn: () => apiGet<PauseData>('/api/v1/ui/pause-log?limit=50'),
    refetchInterval: 5000,
  })
  const qDeliv = useQuery({
    queryKey: ['notification-delivery'],
    queryFn: () => apiGet<DeliveryData>('/api/v1/ui/notifications-delivery?limit=80'),
    refetchInterval: 5000,
  })

  if (qRecon.isLoading || qPause.isLoading || qDeliv.isLoading) {
    return (
      <div className="page">
        <div className="card">로딩 중...</div>
      </div>
    )
  }
  if (qRecon.isError || qPause.isError || qDeliv.isError) {
    const err = (qRecon.error ?? qPause.error ?? qDeliv.error) as unknown
    return (
      <div className="page">
        <div className="errorBox">
          <div style={{ fontWeight: 700, marginBottom: 8 }}>불러오기 실패</div>
          <div className="mono">{String(err)}</div>
        </div>
      </div>
    )
  }

  const recon = qRecon.data?.items ?? []
  const pause = qPause.data?.items ?? []
  const deliveries = qDeliv.data?.items ?? []

  return (
    <div className="page">
      <div className="grid">
        <div className="card">
          <div className="cardTitle">
            <h2>정합성 체크</h2>
            <span className="pill">{recon.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>시각(KST)</th>
                <th style={{ width: 90 }}>상태</th>
                <th style={{ width: 90 }}>범위</th>
                <th style={{ width: 110 }}>심볼</th>
                <th>요약</th>
              </tr>
            </thead>
            <tbody>
              {recon.map((r) => (
                <tr key={r.check_id}>
                  <td className="mono">{fmtTsKst(r.ts)}</td>
                  <td>{r.status}</td>
                  <td className="mono">{r.scope}</td>
                  <td className="mono">{r.symbol ?? ''}</td>
                  <td className="muted">{r.diff_summary ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>PAUSE 로그</h2>
            <span className="pill">{pause.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>pause(KST)</th>
                <th style={{ width: 160 }}>resume(KST)</th>
                <th style={{ width: 110 }}>사유</th>
                <th style={{ width: 90 }}>심각도</th>
                <th>메모</th>
              </tr>
            </thead>
            <tbody>
              {pause.map((p) => (
                <tr key={p.pause_id}>
                  <td className="mono">{fmtTsKst(p.ts_pause)}</td>
                  <td className="mono">{fmtTsKst(p.ts_resume)}</td>
                  <td className="mono">{p.reason_type}</td>
                  <td>{p.severity}</td>
                  <td className="muted">{p.notes ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>알림 전송 이력</h2>
            <span className="pill">{deliveries.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>created(KST)</th>
                <th style={{ width: 90 }}>채널</th>
                <th style={{ width: 140 }}>template</th>
                <th style={{ width: 90 }}>상태</th>
                <th style={{ width: 90 }}>시도</th>
                <th>에러</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.map((d) => (
                <tr key={d.delivery_id}>
                  <td className="mono">{fmtTsKst(d.created_at)}</td>
                  <td className="mono">{d.channel}</td>
                  <td className="mono">{d.template_id}</td>
                  <td>{d.status}</td>
                  <td className="mono">{d.attempt_count}</td>
                  <td className="muted">{d.last_error ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


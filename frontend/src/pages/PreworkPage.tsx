import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import { fmtTsKst } from '../shared/format'

type WorkReportRow = {
  report_id: string
  created_at: string | null
  title: string | null
  summary: string | null
  age_minutes: number | null
}

type WorkReportsData = {
  reports: Record<string, WorkReportRow>
  missing: string[]
  stale: string[]
  max_age_minutes: number
  checked_at_utc: string
}

const ORDER = ['research_agent', 'quant_strategist', 'risk_manager', 'ops_manager']

function badgeFor(agent: string, data: WorkReportsData): { label: string; cls: string } {
  if ((data.missing ?? []).includes(agent)) return { label: '미제출', cls: 'pillDanger' }
  if ((data.stale ?? []).includes(agent)) return { label: '지연', cls: 'pillWarn' }
  return { label: '정상', cls: 'pillOk' }
}

export function PreworkPage() {
  const q = useQuery({
    queryKey: ['work-reports-latest'],
    queryFn: () => apiGet<WorkReportsData>('/api/v1/ui/work-reports/latest'),
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

  const data = q.data ?? { reports: {}, missing: [], stale: [], max_age_minutes: 360, checked_at_utc: '' }

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>사전업무 상태</h2>
          <span className="pill">max_age={data.max_age_minutes}분</span>
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          체크 시각(KST): <span className="mono">{fmtTsKst(data.checked_at_utc)}</span>
        </div>

        <div className="stack">
          {ORDER.map((agent) => {
            const row = data.reports?.[agent]
            const badge = badgeFor(agent, data)
            return (
              <div key={agent} className="reportCard">
                <div className="reportTop">
                  <div>
                    <div className="reportTitle">{agent}</div>
                    <div className="reportMeta mono">
                      {row?.created_at ? fmtTsKst(row.created_at) : '(보고 없음)'} · age={typeof row?.age_minutes === 'number' ? row.age_minutes.toFixed(1) : '-'}m
                    </div>
                  </div>
                  <span className={`pill ${badge.cls}`}>{badge.label}</span>
                </div>
                <div className="reportSummary">{row?.summary ?? '아직 보고서가 없습니다.'}</div>
                <div className="muted mono" style={{ fontSize: 12, marginTop: 6 }}>
                  report_id: {row?.report_id ?? '-'}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

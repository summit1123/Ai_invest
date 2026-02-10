import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { AgentDailyReportView } from '../shared/api/types'

type ResearchData = { items: AgentDailyReportView[] }

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

export function ResearchDailyPage() {
  const q = useQuery({
    queryKey: ['research-daily'],
    queryFn: () => apiGet<ResearchData>('/api/v1/ui/research/daily?limit=40'),
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

  const items = q.data?.items ?? []

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>리서치 일보</h2>
          <span className="pill">{items.length}건</span>
        </div>
        {items.length === 0 ? (
          <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            아직 보고서가 없습니다. (생성: <span className="mono">uv run python scripts/research_daily_brief.py</span>)
          </div>
        ) : (
          <div className="stack">
            {items.map((r) => (
              <div key={r.report_id} className="reportCard">
                <div className="reportTop">
                  <div>
                    <div className="reportTitle">{r.title}</div>
                    <div className="reportMeta mono">
                      {r.report_date} · {r.agent_name} · {r.team_scope}
                    </div>
                  </div>
                  <span className="pill">보고</span>
                </div>
                <div className="reportSummary">{r.summary}</div>
                <details className="reportDetails">
                  <summary>원문 데이터 보기</summary>
                  <pre className="codeBlock">{safeJson({ findings: r.findings, risks: r.risks, action_items: r.action_items })}</pre>
                </details>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

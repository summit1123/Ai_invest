import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { TimelineEvent } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'
import { eventTypeKo } from '../shared/domain/eventTypesKo'

type TimelineData = { items: TimelineEvent[] }

export function TimelinePage() {
  const q = useQuery({
    queryKey: ['timeline'],
    queryFn: () => apiGet<TimelineData>('/api/v1/ui/timeline?limit=200'),
    refetchInterval: 5000,
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

  const items = q.data!.items ?? []

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>타임라인</h2>
          <span className="pill">{items.length}건</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>시각(KST)</th>
              <th style={{ width: 170 }}>유형</th>
              <th style={{ width: 140 }}>엔티티</th>
              <th>entity_id</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => {
              const isDecision = e.event_type === 'SAFE_DECISION' || e.event_type === 'AI_DECISION'
              let safeId = e.entity_id
              if (e.event_type === 'AI_DECISION') {
                const shadowOf = (e.payload as any)?.shadow_of as string | undefined
                if (shadowOf) safeId = shadowOf
              }
              const to = `/decision/${safeId}`
              return (
                <tr key={e.event_id}>
                  <td className="mono">{fmtTsKst(e.ts)}</td>
                  <td title={e.event_type}>{eventTypeKo(e.event_type)}</td>
                  <td className="muted mono">{e.entity_type}</td>
                  <td className="mono">
                    {isDecision ? (
                      <Link to={to} className="link">
                        {e.entity_id}
                      </Link>
                    ) : (
                      e.entity_id
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

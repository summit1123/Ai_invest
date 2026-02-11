import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { MeetingSessionView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'

type MeetingsData = { items: MeetingSessionView[] }

export function MeetingsPage() {
  const q = useQuery({
    queryKey: ['meetings'],
    queryFn: () => apiGet<MeetingsData>('/api/v1/ui/meetings?limit=50'),
    refetchInterval: 30_000,
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
          <h2>회의</h2>
          <span className="pill">{items.length}건</span>
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          시드(데모): <span className="mono">uv run python scripts/seed_meeting_demo.py</span>
        </div>
        {items.length === 0 ? (
          <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            아직 회의 로그가 없습니다.
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>시작(KST)</th>
                <th style={{ width: 140 }}>종류</th>
                <th style={{ width: 90 }}>상태</th>
                <th style={{ width: 90 }}>활성화</th>
                <th style={{ width: 80 }}>정책v</th>
                <th style={{ width: 140 }}>진행자</th>
                <th>meeting_id</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.meeting_id}>
                  <td className="mono">{fmtTsKst(m.started_at)}</td>
                  <td className="mono">{m.meeting_type}</td>
                  <td>{m.status}</td>
                  <td className="mono">{String((m.decisions as any)?.activation_status ?? '-')}</td>
                  <td className="mono">{String((m.decisions as any)?.policy_version ?? '-')}</td>
                  <td className="mono">{m.facilitator}</td>
                  <td className="mono">
                    <Link to={`/meetings/${m.meeting_id}`} className="link">
                      {m.meeting_id}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

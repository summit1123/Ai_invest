import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { CommunicationRoomView } from '../shared/api/types'

type RoomsData = { items: CommunicationRoomView[] }

export function RoomsPage() {
  const q = useQuery({
    queryKey: ['rooms'],
    queryFn: () => apiGet<RoomsData>('/api/v1/ui/collaboration/rooms?limit=200'),
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
          <h2>협업 채널</h2>
          <span className="pill">{items.length}개</span>
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          시드: <span className="mono">uv run python scripts/seed_communication_rooms.py</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 120 }}>채널 타입</th>
              <th style={{ width: 160 }}>room_name</th>
              <th style={{ width: 140 }}>team_scope</th>
              <th style={{ width: 90 }}>활성</th>
              <th>room_key</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.room_id}>
                <td className="mono">{r.channel_type}</td>
                <td className="mono">{r.room_name}</td>
                <td className="mono">{r.team_scope}</td>
                <td>{r.is_active ? 'Y' : 'N'}</td>
                <td className="mono">{r.room_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


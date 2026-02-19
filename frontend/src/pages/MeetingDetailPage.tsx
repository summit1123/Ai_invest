import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { MeetingMessageView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'

type MeetingDetailData = {
  session: unknown
  messages: MeetingMessageView[]
}

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

export function MeetingDetailPage() {
  const { meetingId } = useParams()
  const id = meetingId ?? ''

  const q = useQuery({
    queryKey: ['meeting', id],
    queryFn: () => apiGet<MeetingDetailData>(`/api/v1/ui/meetings/${id}?limit=500`),
    enabled: Boolean(id),
    refetchInterval: 30_000,
  })

  if (!id) {
    return (
      <div className="page">
        <div className="card">meeting_id가 필요합니다.</div>
      </div>
    )
  }
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

  const data = q.data!
  const messages = data.messages ?? []
  const s = data.session as any
  const actionItems = (s?.action_items as any)?.items
  const decisions = s?.decisions as any
  const assignedTasks = Array.isArray(decisions?.assigned_tasks) ? (decisions.assigned_tasks as any[]) : []
  const participants = Array.isArray(s?.participants) ? (s.participants as string[]) : []

  return (
    <div className="page">
      <div className="grid">
        <div className="card">
          <div className="cardTitle">
            <h2>회의 상세</h2>
            <span className="pill">{messages.length} 메시지</span>
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            meeting_id: <span className="mono">{id}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            시작(KST): <span className="mono">{fmtTsKst(s?.started_at)}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            종료(KST): <span className="mono">{fmtTsKst(s?.ended_at)}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            종류/상태: <span className="mono">{s?.meeting_type ?? ''}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            <span className="mono">{s?.status ?? ''}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            진행자: <span className="mono">{s?.facilitator ?? ''}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            활성화: <span className="mono">{String(decisions?.activation_status ?? '-')}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            정책버전: <span className="mono">{String(decisions?.policy_version ?? '-')}</span>
          </div>
          {participants.length > 0 ? (
            <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              참석자: <span className="mono">{participants.join(', ')}</span>
            </div>
          ) : null}
          {s?.summary ? (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                요약
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{String(s.summary)}</div>
            </div>
          ) : null}
          {Array.isArray(actionItems) && actionItems.length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                액션 아이템
              </div>
              <div className="stack" style={{ gap: 8 }}>
                {actionItems.slice(0, 10).map((it: any, idx: number) => (
                  <div key={idx} className="msgCard">
                    <div className="muted" style={{ fontSize: 12 }}>
                      담당: <span className="mono">{it?.owner ?? ''}</span>
                      <span style={{ opacity: 0.35 }}> · </span>
                      기한: <span className="mono">{it?.due_date ?? ''}</span>
                    </div>
                    <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.55 }}>{String(it?.action ?? '')}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {assignedTasks.length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                회의 배정 업무
              </div>
              <div className="stack" style={{ gap: 8 }}>
                {assignedTasks.slice(0, 12).map((it: any, idx: number) => (
                  <div key={idx} className="msgCard">
                    <div className="muted" style={{ fontSize: 12 }}>
                      담당: <span className="mono">{it?.target_agent ?? ''}</span>
                      <span style={{ opacity: 0.35 }}> · </span>
                      우선순위: <span className="mono">{it?.priority ?? ''}</span>
                      <span style={{ opacity: 0.35 }}> · </span>
                      기한: <span className="mono">{it?.due_ts_kst ?? ''}</span>
                    </div>
                    <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.55 }}>{String(it?.description ?? '')}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <details style={{ marginTop: 10 }}>
            <summary className="muted">세션 원문(JSON)</summary>
            <pre className="codeBlock">{safeJson(data.session)}</pre>
          </details>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>트랜스크립트</h2>
            <span className="pill">오름차순</span>
          </div>
          {messages.length === 0 ? (
            <div className="muted" style={{ fontSize: 13 }}>
              메시지가 없습니다.
            </div>
          ) : (
            <div className="stack">
              {messages.map((m) => (
                <div key={m.message_id} className="msgCard">
                  <div className="msgTop">
                    <span className="pill">{m.message_type}</span>
                    <span className="mono muted">{fmtTsKst(m.ts)}</span>
                    <span className="mono">{m.sender_agent}</span>
                  </div>
                  <div className="msgContent">{m.content}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

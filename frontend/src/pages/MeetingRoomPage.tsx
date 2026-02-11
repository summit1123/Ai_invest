import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MeetingMessageView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'

type LiveMeta = {
  meeting_id: string
  slot_key: string
  started_at: string
  meeting_type: string
  status: string
}

type LiveSummary = {
  meeting_id: string
  slot_key: string
  ended_at: string
  summary_short: string
  assistant_minutes: string
  trade_plan: any
}

type LiveMsg = MeetingMessageView & { meeting_id: string }

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

export function MeetingRoomPage() {
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [meta, setMeta] = useState<LiveMeta | null>(null)
  const [msgs, setMsgs] = useState<LiveMsg[]>([])
  const [summary, setSummary] = useState<LiveSummary | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const esRef = useRef<EventSource | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const meetingId = meta?.meeting_id ?? summary?.meeting_id ?? ''

  const tradePlanLine = useMemo(() => {
    const p = summary?.trade_plan as any
    if (!p) return null
    const sym = String(p.symbol ?? '')
    const tgt = p.target_position_pct
    if (!sym) return null
    const tgtTxt = typeof tgt === 'number' ? `${tgt.toFixed(1)}%` : ''
    return `${sym} target ${tgtTxt}`.trim()
  }, [summary])

  function stopStream() {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }

  function startMeeting() {
    stopStream()
    setErr(null)
    setSummary(null)
    setMeta(null)
    setMsgs([])
    setPhase('running')

    const es = new EventSource('/api/v1/ui/meetings/governance/live')
    esRef.current = es

    es.addEventListener('meta', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(String(ev.data || '{}')) as LiveMeta
        setMeta(data)
      } catch {
        // ignore
      }
    })

    es.addEventListener('message', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(String(ev.data || '{}')) as LiveMsg
        setMsgs((prev) => [...prev, data])
      } catch {
        // ignore
      }
    })

    es.addEventListener('summary', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(String(ev.data || '{}')) as LiveSummary
        setSummary(data)
      } catch {
        // ignore
      }
    })

    es.addEventListener('error', (ev: any) => {
      // SSE network errors also arrive here without payload.
      try {
        const data = JSON.parse(String(ev?.data || '{}')) as any
        const msg = String(data?.error ?? '알 수 없는 에러')
        setErr(msg)
      } catch {
        setErr('연결 오류 또는 서버 에러')
      }
      setPhase('error')
      stopStream()
    })

    es.addEventListener('done', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(String(ev.data || '{}')) as any
        if (data?.ok === false) {
          setPhase('error')
        } else {
          setPhase('done')
        }
      } catch {
        setPhase('done')
      }
      stopStream()
    })
  }

  useEffect(() => {
    return () => stopStream()
  }, [])

  useEffect(() => {
    // Auto-scroll transcript on new messages while running.
    if (phase !== 'running') return
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [msgs.length, phase])

  return (
    <div className="page">
      <div className="grid grid2">
        <div className="card">
          <div className="cardTitle">
            <h2>회의실(라이브)</h2>
            <span className="pill">{phase === 'running' ? '진행 중' : phase === 'done' ? '완료' : phase === 'error' ? '에러' : '대기'}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
            버튼을 누르면 거버넌스 회의가 즉시 실행되고, 에이전트 발언이 실시간으로 흘러갑니다.
          </div>
          <div className="stack" style={{ gap: 8 }}>
            <button className="btn" onClick={startMeeting} disabled={phase === 'running'}>
              새 회의 시작
            </button>
            {phase === 'running' ? (
              <button className="btn btnGhost" onClick={stopStream}>
                중지(스트림만)
              </button>
            ) : null}
          </div>

          {err ? (
            <div className="errorBox" style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>오류</div>
              <div className="mono">{err}</div>
            </div>
          ) : null}

          <div className="stack" style={{ marginTop: 12, gap: 6 }}>
            <div className="muted" style={{ fontSize: 12 }}>
              meeting_id: <span className="mono">{meetingId || '(생성 전)'}</span>
            </div>
            {meta ? (
              <>
                <div className="muted" style={{ fontSize: 12 }}>
                  시작(KST): <span className="mono">{fmtTsKst(meta.started_at)}</span>
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  slot_key: <span className="mono">{meta.slot_key}</span>
                </div>
              </>
            ) : null}
            {summary ? (
              <>
                <div className="muted" style={{ fontSize: 12 }}>
                  종료(KST): <span className="mono">{fmtTsKst(summary.ended_at)}</span>
                </div>
                {tradePlanLine ? (
                  <div className="muted" style={{ fontSize: 12 }}>
                    Trade Plan: <span className="mono">{tradePlanLine}</span>
                  </div>
                ) : null}
              </>
            ) : null}
          </div>

          {meetingId ? (
            <div style={{ marginTop: 12 }}>
              <Link to={`/meetings/${meetingId}`} className="link">
                회의 상세(저장된 로그) 열기
              </Link>
            </div>
          ) : null}
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>실시간 트랜스크립트</h2>
            <span className="pill">{msgs.length}개</span>
          </div>
          <div ref={scrollRef} style={{ maxHeight: 680, overflow: 'auto' }}>
            {msgs.length === 0 ? (
              <div className="muted" style={{ fontSize: 13 }}>
                아직 메시지가 없습니다.
              </div>
            ) : (
              <div className="stack">
                {msgs.map((m) => (
                  <div key={m.message_id} className="msgCard">
                    <div className="msgTop">
                      <span className="pill">{m.message_type}</span>
                      <span className="mono muted">{fmtTsKst(m.ts)}</span>
                      <span className="mono">{m.sender_agent}</span>
                    </div>
                    <div className="msgContent">{m.content}</div>
                    {m.payload ? (
                      <details className="reportDetails" style={{ marginTop: 8 }}>
                        <summary>payload(JSON)</summary>
                        <pre className="codeBlock">{safeJson(m.payload)}</pre>
                      </details>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>

          {summary ? (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                회의록(Secretary)
              </div>
              <pre className="codeBlock" style={{ whiteSpace: 'pre-wrap' }}>
                {summary.assistant_minutes}
              </pre>
              <details className="reportDetails" style={{ marginTop: 10 }}>
                <summary>Trade Plan 원문(JSON)</summary>
                <pre className="codeBlock">{safeJson(summary.trade_plan)}</pre>
              </details>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

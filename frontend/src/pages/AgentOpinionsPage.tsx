import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useMemo, useState } from 'react'
import { apiGet } from '../shared/api/client'
import type { AgentOpinionView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'
import { reasonTitleKo } from '../shared/domain/reasonCodesKo'

type OpinionsData = { items: AgentOpinionView[] }

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

function extractReasonCodes(o: AgentOpinionView): string[] {
  const r1 = (o.reason as any)?.reason_codes
  if (Array.isArray(r1)) return r1 as string[]
  const r2 = (o.raw_payload as any)?.reason_codes
  if (Array.isArray(r2)) return r2 as string[]
  return []
}

function summarize(o: AgentOpinionView): string {
  const raw = o.raw_payload as any
  switch (o.agent_name) {
    case 'market_agent':
      return `signal=${raw?.signal ?? o.signal} conf=${raw?.confidence ?? o.confidence} target%=${raw?.target_position_pct ?? ''}`
    case 'regime_agent':
      return `regime=${raw?.regime ?? ''} trade_allowed=${raw?.trade_allowed ?? ''}`
    case 'risk_agent':
      return `veto=${raw?.veto ?? ''} max_pos%=${raw?.max_position_pct ?? ''} max_loss%=${raw?.max_loss_per_trade_pct ?? ''}`
    case 'ops_agent':
      return `state=${raw?.system_state ?? ''} veto=${raw?.veto ?? ''} recon=${raw?.reconciliation_status ?? ''}`
    default:
      return `signal=${o.signal} conf=${o.confidence}`
  }
}

export function AgentOpinionsPage() {
  const [symbol, setSymbol] = useState<string>('')
  const [agentName, setAgentName] = useState<string>('')

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    p.set('limit', '200')
    if (symbol.trim()) p.set('symbol', symbol.trim())
    if (agentName.trim()) p.set('agent_name', agentName.trim())
    return p.toString()
  }, [symbol, agentName])

  const q = useQuery({
    queryKey: ['agent-opinions', qs],
    queryFn: () => apiGet<OpinionsData>(`/api/v1/ui/agent-opinions?${qs}`),
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

  const items = q.data?.items ?? []

  const agentSet = Array.from(new Set(items.map((x) => x.agent_name))).sort()

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>에이전트 의견</h2>
          <span className="pill">{items.length}건</span>
        </div>

        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          AGENT_OPINION + agent_opinions 동시 추적(표준)
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div className="pill">
            <span className="muted">심볼</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="예: KRW-BTC"
              style={{
                marginLeft: 8,
                background: 'transparent',
                border: 'none',
                outline: 'none',
                color: 'inherit',
                width: 140,
              }}
            />
          </div>
          <div className="pill">
            <span className="muted">에이전트</span>
            <select
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              style={{
                marginLeft: 8,
                background: 'transparent',
                border: 'none',
                outline: 'none',
                color: 'inherit',
              }}
            >
              <option value="">전체</option>
              {agentSet.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
        </div>

        {items.length === 0 ? (
          <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            아직 의견이 없습니다. (생성: <span className="mono">uv run python scripts/run_paper_loop.py</span>)
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>시각(KST)</th>
                <th style={{ width: 130 }}>에이전트</th>
                <th style={{ width: 220 }}>요약</th>
                <th style={{ width: 90 }}>신뢰도</th>
                <th style={{ width: 260 }}>사유</th>
                <th style={{ width: 250 }}>결정</th>
                <th>원문</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => {
                const codes = extractReasonCodes(o)
                return (
                  <tr key={o.opinion_id}>
                    <td className="mono">{fmtTsKst(o.ts)}</td>
                    <td className="mono">{o.agent_name}</td>
                    <td className="mono">{summarize(o)}</td>
                    <td className="mono">{o.confidence.toFixed(2)}</td>
                    <td className="mono" style={{ opacity: 0.95 }}>
                      {codes.slice(0, 3).map((c) => reasonTitleKo(c)).join(', ') || '없음'}
                    </td>
                    <td className="mono">
                      {o.decision_id ? (
                        <>
                          <Link to={`/conference/${o.decision_id}`} className="link">
                            회의
                          </Link>
                          <span style={{ opacity: 0.35 }}> · </span>
                          <Link to={`/decision/${o.decision_id}`} className="link">
                            판정
                          </Link>
                        </>
                      ) : (
                        <span className="muted">N/A</span>
                      )}
                    </td>
                    <td>
                      <details>
                        <summary className="muted">JSON</summary>
                        <pre className="codeBlock">{safeJson(o.raw_payload)}</pre>
                      </details>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

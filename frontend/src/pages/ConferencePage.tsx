import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { DecisionView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'
import { reasonTitleKo } from '../shared/domain/reasonCodesKo'

type ConferenceData = {
  decision: DecisionView | null
  agent_inputs: unknown
  event: unknown
}

function pillClassByTone(tone: 'ok' | 'warn' | 'danger' | 'neutral'): string {
  if (tone === 'ok') return 'pill pillOk'
  if (tone === 'warn') return 'pill pillWarn'
  if (tone === 'danger') return 'pill pillDanger'
  return 'pill'
}

function actionTone(action: string): 'ok' | 'warn' | 'danger' | 'neutral' {
  const a = (action || '').toUpperCase()
  if (a === 'BUY') return 'ok'
  if (a === 'SELL') return 'warn'
  if (a === 'PAUSE') return 'danger'
  return 'neutral'
}

function safeJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

function ReasonCodesLine(props: { codes: unknown }) {
  const codes = Array.isArray(props.codes) ? (props.codes as string[]) : []
  return (
    <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
      reason_codes: <span className="mono">{codes.map((c) => reasonTitleKo(c)).join(', ') || '없음'}</span>
    </div>
  )
}

function AgentCard(props: {
  title: string
  badge: string
  tone: 'ok' | 'warn' | 'danger' | 'neutral'
  summary: Array<{ k: string; v: string }>
  reasonCodes?: unknown
  raw: unknown
}) {
  return (
    <div className="card">
      <div className="cardTitle">
        <h2>{props.title}</h2>
        <span className={pillClassByTone(props.tone)}>{props.badge}</span>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        {props.summary.map((r) => (
          <div key={r.k} className="muted" style={{ fontSize: 12 }}>
            {r.k}: <span className="mono">{r.v}</span>
          </div>
        ))}
      </div>
      <ReasonCodesLine codes={props.reasonCodes} />
      <details className="reportDetails" style={{ marginTop: 10 }}>
        <summary>원문 JSON 보기</summary>
        <pre className="codeBlock">{safeJson(props.raw)}</pre>
      </details>
    </div>
  )
}

export function ConferencePage() {
  const { decisionId } = useParams()
  const id = decisionId ?? ''

  const q = useQuery({
    queryKey: ['conference', id],
    queryFn: () => apiGet<ConferenceData>(`/api/v1/ui/conference/${id}`),
    enabled: Boolean(id),
    refetchInterval: 5000,
  })

  if (!id) {
    return (
      <div className="page">
        <div className="card">decision_id가 필요합니다.</div>
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
  const d = data.decision
  const reasons = Array.isArray(d?.selected_reasons) ? d?.selected_reasons : []

  // agent_inputs in SAFE_DECISION event payload
  const agentInputs = (data as any).agent_inputs as any

  const g = (d?.gates as any) ?? {}
  const safeAction = d?.action ?? 'N/A'

  const market = agentInputs?.market ?? null
  const regime = agentInputs?.regime ?? null
  const risk = agentInputs?.risk ?? null
  const ops = agentInputs?.ops ?? null

  return (
    <div className="page">
      <div className="grid grid2">
        <div className="card">
          <div className="cardTitle">
            <h2>Safe 결정</h2>
            <span className={pillClassByTone(actionTone(safeAction))}>{safeAction}</span>
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            시각(KST): <span className="mono">{fmtTsKst(d?.ts)}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            decision_id: <span className="mono">{id}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            비용(bps): <span className="mono">{(d as any)?.expected_cost_bps ?? ''}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            RR: <span className="mono">{(d as any)?.expected_rr ?? ''}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            게이트: <span className="mono">pause={String(g.pause_state ?? '')}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            <span className="mono">recon={_safeStr(g.reconciliation_status)}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            <span className="mono">regime_allowed={String(g.regime_trade_allowed ?? '')}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            <span className="mono">risk_veto={String(g.risk_veto ?? '')}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            <span className="mono">ops_veto={String(g.ops_veto ?? '')}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            이유: <span className="mono">{reasons.map((c) => reasonTitleKo(c)).join(', ') || '없음'}</span>
          </div>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>게이트(원문)</h2>
            <span className="pill">JSON</span>
          </div>
          <pre className="codeBlock">{JSON.stringify(d?.gates ?? {}, null, 2)}</pre>
        </div>
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <div className="grid grid2">
          <AgentCard
            title="Market Agent"
            badge={market?.signal ?? 'N/A'}
            tone={market?.signal === 'LONG' ? 'ok' : market?.signal === 'SELL' ? 'warn' : 'neutral'}
            summary={[
              { k: 'signal', v: _safeStr(market?.signal) },
              { k: 'confidence', v: _safeStr(market?.confidence) },
              { k: 'target_position_pct', v: _safeStr(market?.target_position_pct) },
            ]}
            reasonCodes={market?.reason_codes}
            raw={market}
          />
          <AgentCard
            title="Regime Agent"
            badge={regime?.trade_allowed ? '거래 허용' : '차단'}
            tone={regime?.trade_allowed ? 'ok' : 'danger'}
            summary={[
              { k: 'regime', v: _safeStr(regime?.regime) },
              { k: 'trade_allowed', v: String(Boolean(regime?.trade_allowed)) },
            ]}
            reasonCodes={regime?.reason_codes}
            raw={regime}
          />
        </div>
        <div className="grid grid2">
          <AgentCard
            title="Risk Agent"
            badge={risk?.veto ? 'VETO' : 'OK'}
            tone={risk?.veto ? 'danger' : 'ok'}
            summary={[
              { k: 'veto', v: String(Boolean(risk?.veto)) },
              { k: 'max_position_pct', v: _safeStr(risk?.max_position_pct) },
              { k: 'max_loss_per_trade_pct', v: _safeStr(risk?.max_loss_per_trade_pct) },
            ]}
            reasonCodes={risk?.reason_codes}
            raw={risk}
          />
          <AgentCard
            title="Ops Agent"
            badge={ops?.system_state ?? 'N/A'}
            tone={ops?.system_state === 'FAIL' ? 'danger' : ops?.veto ? 'warn' : 'ok'}
            summary={[
              { k: 'system_state', v: _safeStr(ops?.system_state) },
              { k: 'veto', v: String(Boolean(ops?.veto)) },
              { k: 'reconciliation_status', v: _safeStr(ops?.reconciliation_status) },
              { k: 'alerts', v: Array.isArray(ops?.alerts) ? ops.alerts.join(', ') : '' },
            ]}
            reasonCodes={ops?.reason_codes}
            raw={ops}
          />
        </div>
      </div>
    </div>
  )
}

function _safeStr(x: unknown): string {
  if (x === null || x === undefined) return ''
  return String(x)
}

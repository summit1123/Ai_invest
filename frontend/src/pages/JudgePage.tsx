import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { DecisionView } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'
import { reasonTitleKo } from '../shared/domain/reasonCodesKo'

type JudgeData = {
  safe: DecisionView | null
  ai_shadow: DecisionView | null
}

function actionPillClass(action: string): string {
  const a = (action || '').toUpperCase()
  if (a === 'BUY') return 'pill pillOk'
  if (a === 'SELL') return 'pill pillWarn'
  if (a === 'PAUSE') return 'pill pillDanger'
  return 'pill'
}

function DecisionCard(props: { title: string; d: DecisionView | null }) {
  const reasons = Array.isArray(props.d?.selected_reasons) ? props.d?.selected_reasons : []
  return (
    <div className="card">
      <div className="cardTitle">
        <h2>{props.title}</h2>
        <span className={actionPillClass(props.d?.action ?? '')}>{props.d?.action ?? 'N/A'}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        시각(KST): <span className="mono">{fmtTsKst(props.d?.ts)}</span>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        decision_id: <span className="mono">{props.d?.decision_id ?? 'none'}</span>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        이유: <span className="mono">{reasons.map((c) => reasonTitleKo(c)).join(', ') || '없음'}</span>
      </div>
      <div style={{ marginTop: 12 }}>
        <pre className="codeBlock">{JSON.stringify(props.d?.gates ?? {}, null, 2)}</pre>
      </div>
    </div>
  )
}

export function JudgePage() {
  const { decisionId } = useParams()
  const id = decisionId ?? ''

  const q = useQuery({
    queryKey: ['judge', id],
    queryFn: () => apiGet<JudgeData>(`/api/v1/ui/judge/${id}`),
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

  return (
    <div className="page">
      <div className="grid grid2">
        <DecisionCard title="Safe Judge" d={data.safe} />
        <DecisionCard title="AI Shadow" d={data.ai_shadow} />
      </div>
    </div>
  )
}

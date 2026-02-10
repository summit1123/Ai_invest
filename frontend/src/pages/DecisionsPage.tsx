import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../shared/api/client'
import type { LatestDecisionRow } from '../shared/api/types'
import { fmtTsKst } from '../shared/format'
import { reasonTitleKo } from '../shared/domain/reasonCodesKo'

type DecisionsData = { items: LatestDecisionRow[] }

function actionPillClass(action: string): string {
  const a = (action || '').toUpperCase()
  if (a === 'BUY') return 'pill pillOk'
  if (a === 'SELL') return 'pill pillWarn'
  if (a === 'PAUSE') return 'pill pillDanger'
  return 'pill'
}

export function DecisionsPage() {
  const q = useQuery({
    queryKey: ['latest-decisions'],
    queryFn: () => apiGet<DecisionsData>('/api/v1/ui/latest-decisions?limit=80'),
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

  return (
    <div className="page">
      <div className="card">
        <div className="cardTitle">
          <h2>의사결정</h2>
          <span className="pill">{items.length}건</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>시각(KST)</th>
              <th style={{ width: 100 }}>심볼</th>
              <th style={{ width: 70 }}>판사</th>
              <th style={{ width: 80 }}>액션</th>
              <th style={{ width: 80 }}>신뢰도</th>
              <th style={{ width: 260 }}>이유</th>
              <th>링크</th>
            </tr>
          </thead>
          <tbody>
            {items.map((d) => {
              const reasons = Array.isArray(d.selected_reasons) ? d.selected_reasons : []
              return (
                <tr key={d.decision_id}>
                  <td className="mono">{fmtTsKst(d.ts)}</td>
                  <td className="mono">{d.symbol}</td>
                  <td>{d.judge_type}</td>
                  <td>
                    <span className={actionPillClass(d.action)}>{d.action}</span>
                  </td>
                  <td className="mono">{d.confidence ?? ''}</td>
                  <td className="mono" style={{ opacity: 0.95 }}>
                    {reasons.slice(0, 3).map((c) => reasonTitleKo(c)).join(', ')}
                  </td>
                  <td className="mono">
                    <Link to={`/conference/${d.decision_id}`} className="link">
                      회의
                    </Link>
                    <span style={{ opacity: 0.35 }}> · </span>
                    <Link to={`/decision/${d.decision_id}`} className="link">
                      판정
                    </Link>
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

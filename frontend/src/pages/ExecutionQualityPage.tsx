import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { ExecutionMetricView } from '../shared/api/types'
import { fmtNumber, fmtTsKst } from '../shared/format'

type ExecutionData = { items: ExecutionMetricView[] }

export function ExecutionQualityPage() {
  const q = useQuery({
    queryKey: ['execution-quality'],
    queryFn: () => apiGet<ExecutionData>('/api/v1/ui/execution-quality?limit=200'),
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
          <h2>실행 품질(TCA-lite)</h2>
          <span className="pill">{items.length}건</span>
        </div>
          <table className="table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>제출(KST)</th>
              <th style={{ width: 100 }}>심볼</th>
              <th style={{ width: 120 }}>VWAP</th>
              <th style={{ width: 140 }}>슬리피지(bps)</th>
              <th style={{ width: 140 }}>스프레드(bps)</th>
              <th style={{ width: 120 }}>체결비율</th>
              <th>order_id</th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.metric_id}>
                <td className="mono">{fmtTsKst(m.ts_submit)}</td>
                <td className="mono">{m.symbol}</td>
                <td className="mono">{m.fill_vwap ?? ''}</td>
                <td className="mono">{fmtNumber(m.slippage_bps_vs_submit ?? null, 2)}</td>
                <td className="mono">{fmtNumber(m.spread_bps_at_submit ?? null, 2)}</td>
                <td className="mono">{fmtNumber(m.filled_ratio ?? null, 2)}</td>
                <td className="mono">{m.order_id ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

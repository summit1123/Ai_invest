import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { DecisionOutcomeView, LedgerEntryView, TaxExportRunView } from '../shared/api/types'
import { fmtNumber, fmtTsKst } from '../shared/format'

type TaxExportData = { items: TaxExportRunView[] }
type LedgerData = { items: LedgerEntryView[] }
type OutcomeData = { items: DecisionOutcomeView[] }

export function FinancePage() {
  const qExports = useQuery({
    queryKey: ['tax-exports'],
    queryFn: () => apiGet<TaxExportData>('/api/v1/ui/tax-exports?limit=50'),
    refetchInterval: 10_000,
  })
  const qLedger = useQuery({
    queryKey: ['ledger'],
    queryFn: () => apiGet<LedgerData>('/api/v1/ui/ledger?limit=200'),
    refetchInterval: 10_000,
  })
  const qOutcomes = useQuery({
    queryKey: ['outcomes'],
    queryFn: () => apiGet<OutcomeData>('/api/v1/ui/outcomes?limit=200'),
    refetchInterval: 10_000,
  })

  if (qExports.isLoading || qLedger.isLoading || qOutcomes.isLoading) {
    return (
      <div className="page">
        <div className="card">로딩 중...</div>
      </div>
    )
  }
  if (qExports.isError || qLedger.isError || qOutcomes.isError) {
    const err = (qExports.error ?? qLedger.error ?? qOutcomes.error) as unknown
    return (
      <div className="page">
        <div className="errorBox">
          <div style={{ fontWeight: 700, marginBottom: 8 }}>불러오기 실패</div>
          <div className="mono">{String(err)}</div>
        </div>
      </div>
    )
  }

  const exports = qExports.data?.items ?? []
  const ledger = qLedger.data?.items ?? []
  const outcomes = qOutcomes.data?.items ?? []

  return (
    <div className="page">
        <div className="card">
          <div className="cardTitle">
            <h2>세금 산출(Tax Export)</h2>
            <span className="pill">{exports.length}건</span>
          </div>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>생성(KST)</th>
              <th style={{ width: 110 }}>상태</th>
              <th style={{ width: 170 }}>시작(KST)</th>
              <th style={{ width: 170 }}>종료(KST)</th>
              <th style={{ width: 140 }}>생성자</th>
              <th>export_id</th>
            </tr>
          </thead>
          <tbody>
            {exports.map((r) => (
              <tr key={r.export_id}>
                <td className="mono">{fmtTsKst(r.generated_at)}</td>
                <td>{r.status}</td>
                <td className="mono">{fmtTsKst(r.period_start)}</td>
                <td className="mono">{fmtTsKst(r.period_end)}</td>
                <td className="mono">{r.generated_by}</td>
                <td className="mono">{r.export_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="cardTitle">
            <h2>원장(ledger_entries)</h2>
            <span className="pill">{ledger.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>시각(KST)</th>
                <th style={{ width: 120 }}>유형</th>
                <th style={{ width: 100 }}>통화</th>
                <th style={{ width: 140 }}>금액</th>
                <th style={{ width: 140 }}>수수료</th>
                <th style={{ width: 110 }}>심볼</th>
                <th>order_id</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((e) => (
                <tr key={e.entry_id}>
                  <td className="mono">{fmtTsKst(e.ts)}</td>
                  <td>{e.entry_type}</td>
                  <td className="mono">{e.currency}</td>
                  <td className="mono">{fmtNumber(e.amount, 2)}</td>
                  <td className="mono">
                    {e.fee_amount ?? ''} {e.fee_currency ?? ''}
                  </td>
                  <td className="mono">{e.symbol ?? ''}</td>
                  <td className="mono">{e.order_id ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="cardTitle">
            <h2>결과/오판(복기)</h2>
            <span className="pill">{outcomes.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>리뷰(KST)</th>
                <th style={{ width: 110 }}>결과</th>
                <th style={{ width: 160 }}>오류유형(OC_*)</th>
                <th style={{ width: 110 }}>심볼</th>
                <th style={{ width: 170 }}>종료(KST)</th>
                <th>decision_id</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map((o) => (
                <tr key={o.outcome_id}>
                  <td className="mono">{fmtTsKst(o.reviewed_at)}</td>
                  <td>{o.outcome_label}</td>
                  <td className="mono">{o.error_type ?? ''}</td>
                  <td className="mono">{o.symbol}</td>
                  <td className="mono">{fmtTsKst(o.ts_close)}</td>
                  <td className="mono">{o.decision_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { StrategyReviewView, TaxExportRunView } from '../shared/api/types'
import { fmtTsKst, fmtNumber } from '../shared/format'

type PnlRow = {
  day: string
  realized_pnl: number
  fees_paid: number
  trades_count: number
  max_drawdown: number | null
}

type TradeRow = {
  trade_id: string
  symbol: string
  ts_open: string
  ts_close: string
  side: string
  qty: number
  avg_entry_price: number
  avg_exit_price: number
  realized_pnl: number
  fees_total: number
  pnl_bps: number | null
}

type WeeklyReviewData = { pnl_daily: PnlRow[]; realized_trades: TradeRow[] }
type TaxExportData = { items: TaxExportRunView[] }
type StrategyReviewData = { items: StrategyReviewView[] }

function sum(nums: number[]): number {
  return nums.reduce((a, b) => a + b, 0)
}

const KST_DAY_FORMATTER = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function addDays(day: string, delta: number): string {
  const base = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(base.getTime())) return day
  base.setUTCDate(base.getUTCDate() + delta)
  return base.toISOString().slice(0, 10)
}

function toKstDay(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  return KST_DAY_FORMATTER.format(d)
}

export function WeeklyReviewPage() {
  const q = useQuery({
    queryKey: ['weekly-review'],
    queryFn: () => apiGet<WeeklyReviewData>('/api/v1/ui/review/weekly'),
    refetchInterval: 10_000,
  })
  const qTax = useQuery({
    queryKey: ['tax-exports-small'],
    queryFn: () => apiGet<TaxExportData>('/api/v1/ui/tax-exports?limit=10'),
    refetchInterval: 30_000,
  })
  const qGov = useQuery({
    queryKey: ['strategy-reviews'],
    queryFn: () => apiGet<StrategyReviewData>('/api/v1/ui/strategy-reviews?limit=5'),
    refetchInterval: 60_000,
  })

  if (q.isLoading || qTax.isLoading || qGov.isLoading) {
    return (
      <div className="page">
        <div className="card">로딩 중...</div>
      </div>
    )
  }
  if (q.isError || qTax.isError || qGov.isError) {
    const err = (q.error ?? qTax.error ?? qGov.error) as unknown
    return (
      <div className="page">
        <div className="errorBox">
          <div style={{ fontWeight: 700, marginBottom: 8 }}>불러오기 실패</div>
          <div className="mono">{String(err)}</div>
        </div>
      </div>
    )
  }

  const pnl = q.data?.pnl_daily ?? []
  const trades = q.data?.realized_trades ?? []
  const taxRuns = qTax.data?.items ?? []
  const priorities = qGov.data?.items ?? []
  const latestPriority = priorities[0] ?? null

  const latestDay = pnl.reduce((acc, row) => (row.day > acc ? row.day : acc), '')
  const weeklyStartDay = latestDay ? addDays(latestDay, -6) : ''
  const weeklyPnlRows = latestDay ? pnl.filter((r) => r.day >= weeklyStartDay && r.day <= latestDay) : []
  const weeklyTrades = latestDay
    ? trades.filter((t) => {
        const closeDay = toKstDay(t.ts_close)
        return !!closeDay && closeDay >= weeklyStartDay && closeDay <= latestDay
      })
    : []

  const weeklyPnl = sum(weeklyPnlRows.map((r) => r.realized_pnl))
  const wins = weeklyTrades.filter((t) => t.realized_pnl > 1.0).length
  const losses = weeklyTrades.filter((t) => t.realized_pnl < -1.0).length
  const winRate = weeklyTrades.length ? (wins / weeklyTrades.length) * 100 : 0

  return (
    <div className="page">
      <div className="grid grid2">
        <div className="card">
          <div className="cardTitle">
            <h2>요약</h2>
            <span className="pill">주간</span>
          </div>
          <div className="kpiGrid">
            <div className="kpi">
              <div className="kpiLabel">손익 합계</div>
              <div className="kpiValue mono">{fmtNumber(weeklyPnl, 0)} KRW</div>
            </div>
            <div className="kpi">
              <div className="kpiLabel">승률</div>
              <div className="kpiValue mono">{fmtNumber(winRate, 1)}%</div>
            </div>
            <div className="kpi">
              <div className="kpiLabel">승/패</div>
              <div className="kpiValue mono">
                {wins}/{losses}
              </div>
            </div>
          </div>
          <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
            집계 구간: <span className="mono">{weeklyStartDay && latestDay ? `${weeklyStartDay} ~ ${latestDay}` : '-'}</span>
          </div>
          {latestPriority ? (
            <div style={{ marginTop: 12 }} className="muted">
              <div style={{ fontSize: 12, marginBottom: 6 }}>이번 주 개선 우선순위</div>
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                <span style={{ fontWeight: 700, color: 'rgba(255,255,255,0.90)' }}>{latestPriority.priority_title}</span>
                <span style={{ opacity: 0.65 }}> · </span>
                <span className="mono">{latestPriority.owner}</span>
              </div>
              <div style={{ fontSize: 12, marginTop: 6, opacity: 0.9 }}>{latestPriority.hypothesis}</div>
            </div>
          ) : (
            <div style={{ marginTop: 12 }} className="muted">
              <div style={{ fontSize: 12 }}>
                주간 우선순위가 없습니다. (등록: <span className="mono">uv run python scripts/set_weekly_priority.py --title ...</span>)
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>월말 산출(Tax Export)</h2>
            <span className="pill">{taxRuns.length}회</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 160 }}>생성(KST)</th>
                <th style={{ width: 110 }}>상태</th>
                <th style={{ width: 140 }}>기간</th>
                <th>export_id</th>
              </tr>
            </thead>
            <tbody>
              {taxRuns.map((r) => (
                <tr key={r.export_id}>
                  <td className="mono">{fmtTsKst(r.generated_at)}</td>
                  <td>{r.status}</td>
                  <td className="mono">
                    {fmtTsKst(r.period_start)} ~ {fmtTsKst(r.period_end)}
                  </td>
                  <td className="mono">{r.export_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="cardTitle">
            <h2>일별 PnL</h2>
            <span className="pill">{pnl.length}일</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 120 }}>일자</th>
                <th style={{ width: 160 }}>실현손익</th>
                <th style={{ width: 160 }}>수수료</th>
                <th style={{ width: 110 }}>거래 수</th>
                <th>최대 낙폭</th>
              </tr>
            </thead>
            <tbody>
              {pnl.map((r) => (
                <tr key={r.day}>
                  <td className="mono">{r.day}</td>
                  <td className="mono">{fmtNumber(r.realized_pnl, 0)}</td>
                  <td className="mono">{fmtNumber(r.fees_paid, 0)}</td>
                  <td className="mono">{r.trades_count}</td>
                  <td className="mono">{r.max_drawdown ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>실현 거래</h2>
            <span className="pill">{weeklyTrades.length}건</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 100 }}>심볼</th>
                <th style={{ width: 160 }}>종료(KST)</th>
                <th style={{ width: 160 }}>손익(KRW)</th>
                <th style={{ width: 120 }}>수수료</th>
                <th>trade_id</th>
              </tr>
            </thead>
            <tbody>
              {weeklyTrades.map((t) => (
                <tr key={t.trade_id}>
                  <td className="mono">{t.symbol}</td>
                  <td className="mono">{fmtTsKst(t.ts_close)}</td>
                  <td className="mono">{fmtNumber(t.realized_pnl, 0)}</td>
                  <td className="mono">{fmtNumber(t.fees_total, 0)}</td>
                  <td className="mono">{t.trade_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

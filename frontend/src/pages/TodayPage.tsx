import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../shared/api/client'
import type { AgentDailyReportView, GovernanceStatusView, MeetingSessionView, StrategyReviewView, TodayOverview } from '../shared/api/types'
import { fmtNumber, fmtTsKst } from '../shared/format'
import { reasonTitleKo } from '../shared/domain/reasonCodesKo'
import { Link } from 'react-router-dom'

function pillClass(paused: boolean): string {
  return paused ? 'pill pillDanger' : 'pill pillOk'
}

function actionPillClass(action: string): string {
  const a = (action || '').toUpperCase()
  if (a === 'BUY') return 'pill pillOk'
  if (a === 'SELL') return 'pill pillWarn'
  if (a === 'PAUSE') return 'pill pillDanger'
  return 'pill'
}

export function TodayPage() {
  const q = useQuery({
    queryKey: ['today-overview'],
    queryFn: () => apiGet<TodayOverview>('/api/v1/ui/today-overview'),
    refetchInterval: 5000,
  })
  const qResearch = useQuery({
    queryKey: ['research-latest'],
    queryFn: () => apiGet<{ items: AgentDailyReportView[] }>('/api/v1/ui/research/daily?limit=1'),
    refetchInterval: 30_000,
  })
  const qGov = useQuery({
    queryKey: ['priority-latest'],
    queryFn: () => apiGet<{ items: StrategyReviewView[] }>('/api/v1/ui/strategy-reviews?limit=1'),
    refetchInterval: 60_000,
  })
  const qMeet = useQuery({
    queryKey: ['meeting-latest'],
    queryFn: () => apiGet<{ items: MeetingSessionView[] }>('/api/v1/ui/meetings?limit=1'),
    refetchInterval: 60_000,
  })
  const qGovStatus = useQuery({
    queryKey: ['governance-status'],
    queryFn: () => apiGet<GovernanceStatusView>('/api/v1/ui/governance/status'),
    refetchInterval: 30_000,
  })

  if (q.isLoading || qResearch.isLoading || qGov.isLoading || qMeet.isLoading || qGovStatus.isLoading) {
    return (
      <div className="page">
        <div className="card">로딩 중...</div>
      </div>
    )
  }
  if (q.isError || qResearch.isError || qGov.isError || qMeet.isError || qGovStatus.isError) {
    const err = (q.error ?? qResearch.error ?? qGov.error ?? qMeet.error ?? qGovStatus.error) as unknown
    return (
      <div className="page">
        <div className="errorBox">
          <div style={{ fontWeight: 700, marginBottom: 8 }}>불러오기 실패</div>
          <div className="mono">{String(err)}</div>
        </div>
      </div>
    )
  }

  const data = q.data!
  const safe = data.latest_safe_decision
  const ai = data.latest_ai_decision
  const paused = data.pause?.paused ?? false
  const reconStatus = (data.latest_reconciliation as any)?.status as string | undefined
  const portfolio = data.portfolio
  const latestReport = qResearch.data?.items?.[0] ?? null
  const latestPriority = qGov.data?.items?.[0] ?? null
  const latestMeeting = qMeet.data?.items?.[0] ?? null
  const governanceStatus = qGovStatus.data ?? null
  const readyTasksMap = (governanceStatus?.ready_tasks ?? {}) as Record<string, any[]>
  const readyTaskCount = Object.values(readyTasksMap).reduce((acc, rows) => acc + (Array.isArray(rows) ? rows.length : 0), 0)
  const planActive = governanceStatus?.plan_active as any
  const planProposed = governanceStatus?.plan_proposed as any
  const watchlist = (latestReport?.risks as any)?.watchlist

  return (
    <div className="page">
      <div className="grid grid2">
        <div className="card">
          <div className="cardTitle">
            <h2>시스템</h2>
            <span className={pillClass(paused)}>{paused ? '중단(PAUSE)' : '정상'}</span>
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            정합성 상태: <span className="mono">{reconStatus ?? 'N/A'}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            현금(KRW): <span className="mono">{fmtNumber(portfolio?.cash_krw, 0)}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            포지션 평가: <span className="mono">{fmtNumber(portfolio?.position_value_krw, 0)}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            보유종목: <span className="mono">{portfolio?.positions_count ?? 0}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            총자산: <span className="mono">{fmtNumber(portfolio?.equity_krw, 0)}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            노출비중: <span className="mono">{fmtNumber(portfolio?.exposure_pct, 1)}%</span>
          </div>
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>최신 Safe 결정</h2>
            <span className={actionPillClass(safe?.action ?? '')}>{safe?.action ?? 'N/A'}</span>
          </div>
          <div className="mono" style={{ fontSize: 12, opacity: 0.9 }}>
            decision_id: {safe?.decision_id ?? 'none'}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            시각(KST): <span className="mono">{fmtTsKst(safe?.ts)}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            비용(bps): <span className="mono">{(safe as any)?.expected_cost_bps ?? ''}</span>
            <span style={{ opacity: 0.35 }}> · </span>
            RR: <span className="mono">{(safe as any)?.expected_rr ?? ''}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            이유:{" "}
            <span className="mono">
              {Array.isArray(safe?.selected_reasons) ? safe?.selected_reasons?.map((c) => reasonTitleKo(c)).join(', ') : '없음'}
            </span>
          </div>
        </div>
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="cardTitle">
            <h2>AI Shadow</h2>
            <span className={actionPillClass(ai?.action ?? '')}>{ai?.action ?? 'N/A'}</span>
          </div>
          <div className="mono" style={{ fontSize: 12, opacity: 0.9 }}>
            decision_id: {ai?.decision_id ?? 'none'}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            시각(KST): <span className="mono">{fmtTsKst(ai?.ts)}</span>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            이유:{' '}
            <span className="mono">
              {Array.isArray(ai?.selected_reasons) ? ai?.selected_reasons?.map((c) => reasonTitleKo(c)).join(', ') : '없음'}
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid2" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="cardTitle">
            <h2>리서치 브리프</h2>
            <span className="pill">{latestReport ? latestReport.report_date : 'N/A'}</span>
          </div>
          {latestReport ? (
            <>
              <div style={{ fontSize: 13, lineHeight: 1.55 }}>{latestReport.summary}</div>
              {Array.isArray(watchlist) && watchlist.length > 0 ? (
                <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                  리스크: <span className="mono">{watchlist.slice(0, 3).join(', ')}</span>
                </div>
              ) : null}
              <div style={{ marginTop: 10 }}>
                <Link to="/research" className="link">
                  리서치 페이지로 이동
                </Link>
              </div>
            </>
          ) : (
            <div className="muted" style={{ fontSize: 13 }}>
              아직 없습니다. (생성: <span className="mono">uv run python scripts/research_daily_brief.py</span>)
            </div>
          )}
        </div>

        <div className="card">
          <div className="cardTitle">
            <h2>거버넌스/회의</h2>
            <span className="pill">주간/일일</span>
          </div>
          {latestPriority ? (
            <div style={{ marginBottom: 12 }}>
              <div className="muted" style={{ fontSize: 12 }}>
                주간 우선순위
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.55 }}>
                <span style={{ fontWeight: 700 }}>{latestPriority.priority_title}</span>
                <span style={{ opacity: 0.35 }}> · </span>
                <span className="mono">{latestPriority.owner}</span>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                {latestPriority.hypothesis}
              </div>
              <div style={{ marginTop: 8 }}>
                <Link to="/review/weekly" className="link">
                  주간리뷰로 이동
                </Link>
              </div>
            </div>
          ) : (
            <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
              주간 우선순위 없음
            </div>
          )}

          {latestMeeting ? (
            <div>
              <div className="muted" style={{ fontSize: 12 }}>
                최근 회의
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                시작(KST): <span className="mono">{fmtTsKst(latestMeeting.started_at)}</span>
                <span style={{ opacity: 0.35 }}> · </span>
                종류: <span className="mono">{latestMeeting.meeting_type}</span>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                활성 플랜: <span className="mono">{planActive?.entity_id ?? '-'}</span>
                <span style={{ opacity: 0.35 }}> · </span>
                제안 플랜: <span className="mono">{planProposed?.entity_id ?? '-'}</span>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                READY 업무: <span className="mono">{readyTaskCount}</span>
              </div>
              <div style={{ marginTop: 8 }}>
                <Link to={`/meetings/${latestMeeting.meeting_id}`} className="link">
                  회의 상세로 이동
                </Link>
              </div>
            </div>
          ) : (
            <div className="muted" style={{ fontSize: 13 }}>
              회의 로그 없음 (시드: <span className="mono">uv run python scripts/seed_meeting_demo.py</span>)
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

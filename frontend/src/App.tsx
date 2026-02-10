import { NavLink, Route, Routes } from 'react-router-dom'
import { TodayPage } from './pages/TodayPage'
import { TimelinePage } from './pages/TimelinePage'
import { ExecutionQualityPage } from './pages/ExecutionQualityPage'
import { FinancePage } from './pages/FinancePage'
import { OpsPage } from './pages/OpsPage'
import { ResearchDailyPage } from './pages/ResearchDailyPage'
import { WeeklyReviewPage } from './pages/WeeklyReviewPage'
import { DecisionsPage } from './pages/DecisionsPage'
import { ConferencePage } from './pages/ConferencePage'
import { JudgePage } from './pages/JudgePage'
import { AgentOpinionsPage } from './pages/AgentOpinionsPage'
import { RoomsPage } from './pages/RoomsPage'
import { MeetingsPage } from './pages/MeetingsPage'
import { MeetingDetailPage } from './pages/MeetingDetailPage'

function NavItem(props: { to: string; label: string; hint: string }) {
  return (
    <NavLink
      to={props.to}
      className={({ isActive }) => (isActive ? 'navItem navItemActive' : 'navItem')}
    >
      <span>{props.label}</span>
      <span className="navHint">{props.hint}</span>
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="shell">
      <aside className="nav">
        <div className="brand">
          <div className="brandTitle">AI 자동투자 콘솔</div>
          <div className="brandSubtitle">PnL-first · 페이퍼 트레이딩 v1.1</div>
        </div>
        <div className="navSectionTitle">모니터링</div>
        <nav className="navList">
          <NavItem to="/" label="대시보드" hint="오늘" />
          <NavItem to="/decisions" label="의사결정" hint="SAFE/AI" />
          <NavItem to="/agents" label="에이전트" hint="의견" />
          <NavItem to="/timeline" label="타임라인" hint="이벤트" />
          <NavItem to="/execution" label="실행품질" hint="슬리피지" />
        </nav>

        <div className="navSectionTitle" style={{ marginTop: 14 }}>
          운영/리뷰
        </div>
        <nav className="navList">
          <NavItem to="/ops" label="운영" hint="정합성/중단" />
          <NavItem to="/research" label="리서치" hint="일보" />
          <NavItem to="/review/weekly" label="주간리뷰" hint="KPI" />
          <NavItem to="/finance" label="정산" hint="원장/세금" />
        </nav>

        <div className="navSectionTitle" style={{ marginTop: 14 }}>
          협업
        </div>
        <nav className="navList">
          <NavItem to="/rooms" label="채널" hint="방/채널" />
          <NavItem to="/meetings" label="회의" hint="로그" />
        </nav>
        <div style={{ marginTop: 14 }} className="muted">
          <div style={{ fontSize: 12, lineHeight: 1.4 }}>
            백엔드: <span className="mono">:8000</span> (vite proxy)
          </div>
        </div>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<TodayPage />} />
          <Route path="/decisions" element={<DecisionsPage />} />
          <Route path="/conference/:decisionId" element={<ConferencePage />} />
          <Route path="/decision/:decisionId" element={<JudgePage />} />
          <Route path="/agents" element={<AgentOpinionsPage />} />
          <Route path="/timeline" element={<TimelinePage />} />
          <Route path="/execution" element={<ExecutionQualityPage />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/research" element={<ResearchDailyPage />} />
          <Route path="/review/weekly" element={<WeeklyReviewPage />} />
          <Route path="/finance" element={<FinancePage />} />
          <Route path="/rooms" element={<RoomsPage />} />
          <Route path="/meetings" element={<MeetingsPage />} />
          <Route path="/meetings/:meetingId" element={<MeetingDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}

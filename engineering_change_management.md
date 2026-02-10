# engineering_change_management.md - GitHub 자동 변경관리 표준 (v1.1)

> 목표: 시스템 개선 제안을 자동으로 GitHub PR까지 올리고, 사용자는 변경점 리뷰/승인에 집중한다.  
> 원칙: 자동화는 강하게, 머지 권한은 가드레일로 제한.

관련 문서:
- 운영 정책: `guidelines.md`
- 에이전트 정의: `agents.md`
- 아키텍처: `architecture.md`
- 알림 표준: `notifications_telegram.md`
- DB 스키마: `database.md`

---

## 1. 운영 모델

- 자동 수행:
  - 변경 제안 생성
  - 브랜치 생성
  - 커밋/푸시
  - PR 생성
  - CI 실행/리포트
  - 리뷰 요약 텔레그램 전송
- 사용자 수행:
  - 변경점 확인
  - 최종 승인/머지 결정

기본 정책:
- 기본 브랜치 직접 푸시 금지
- 모든 변경은 PR 경유

---

## 2. 역할 분리

### 2.1 Service Engineering Agent
- 역할: 코드/설정/문서 개선안 생성 및 PR 자동화
- 권한:
  - feature branch 생성/푸시 가능
  - PR 생성 가능
  - 기본 브랜치 머지 불가(사용자 승인 필요)

### 2.2 Strategy Coordinator Agent
- 역할: 개선 우선순위 제시, 실험 1건 선정
- 권한: 코드 변경 불가, 제안만 가능

### 2.3 CTO Ops Agent
- 역할: 시스템 안정성 기준에서 변경 리스크 평가
- 권한: 배포 차단 권고 가능

---

## 3. 표준 인터페이스

### 3.1 ChangeProposalV1

```json
{
  "proposal_id": "uuid",
  "title": "Execution latency metric 보강",
  "type": "CODE_CHANGE",
  "priority": "HIGH",
  "scope": ["execution", "metrics", "docs"],
  "reason": [
    "submit->fill latency 관측 누락",
    "운영 장애 원인 추적 어려움"
  ],
  "acceptance_criteria": [
    "CI green",
    "테스트 3건 통과",
    "문서 동기화 완료"
  ],
  "risk_level": "MEDIUM",
  "rollback_plan": "feature flag off + revert commit"
}
```

### 3.2 PullRequestPlanV1

```json
{
  "proposal_id": "uuid",
  "repo": "owner/repo",
  "base_branch": "main",
  "head_branch": "auto/change-20260209-001",
  "commit_message": "feat: add execution latency instrumentation",
  "pr_title": "[AUTO] execution latency instrumentation",
  "labels": ["auto-change", "needs-review", "risk:medium"],
  "reviewers": ["human_owner"],
  "checklist": [
    "lint",
    "unit-tests",
    "integration-tests",
    "docs-check"
  ]
}
```

---

## 4. GitHub 자동화 흐름

1. Agent가 `ChangeProposalV1` 생성
2. 정책 게이트(범위/리스크/금지 파일) 검증
3. feature branch 생성
4. 변경 반영 및 커밋
5. 원격 푸시
6. PR 생성 (`needs-review` 라벨)
7. CI 실행
8. 텔레그램으로 PR 요약/CI 상태 전송
9. 사용자 리뷰 후 승인/머지

머지 정책:
- CI 필수 체크 전부 통과
- 사용자 승인 1회 이상
- 충돌 없음
- 보안/비밀스캔 통과

---

## 5. 가드레일

금지:
- `main` 직접 푸시
- 비밀정보(.env/key) 커밋
- 무승인 자동 머지

필수:
- 변경 영향 요약
- 롤백 계획
- 테스트 결과 첨부
- 문서 동기화 여부 체크

권장:
- PR 크기 제한(예: 400 lines 이하)
- 고위험 변경은 분할 PR

---

## 6. 텔레그램 연계

전송 이벤트:
- PR 생성: `tpl_pr_opened`
- CI 실패: `tpl_ci_failed`
- PR 리뷰 준비 완료: `tpl_pr_ready`
- 머지 완료: `tpl_pr_merged`

메시지 핵심:
- 제안 ID, PR 링크, 변경 파일 수, 리스크 레벨, CI 상태, 체크리스트 결과

상세 알림 규칙은 `notifications_telegram.md`를 따른다.

---

## 7. 감사/추적

필수 기록:
- proposal 생성 시각/생성자
- PR 번호/브랜치/커밋 SHA
- CI 결과(체크 이름, 성공/실패)
- 승인자/승인 시각
- 머지/롤백 시각
- 저장 위치: `change_proposals`, `github_pr_runs`, `events`

권장 이벤트 타입:
- `CHANGE_PROPOSAL`
- `CHANGE_VALIDATED`
- `PR_OPENED`
- `CI_PASSED`, `CI_FAILED`
- `PR_READY`
- `PR_APPROVED`
- `PR_MERGED`
- `ROLLBACK_EXECUTED`

---

## 8. 테스트 시나리오

1. 제안 생성 후 2분 내 PR 생성
2. CI 실패 시 머지 차단
3. 사용자 승인 전 자동 머지 불가
4. 비밀정보 포함 커밋 탐지 시 PR 차단
5. 머지 후 텔레그램 요약 전송
6. 롤백 수행 시 이벤트/로그 추적 가능

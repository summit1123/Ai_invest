# FINANCE_OPERATION_ENTRYPOINT_V1.md

## 목적
AI_invest 레포를 finance 조직의 실제 운영 대상 1호로 연결하기 위한 진입 문서.

## 대상 레포
- `summit1123/AI_invest`
- local: `/Users/kdh/.openclaw/workspace/projects/AI_invest`

## 현재 확인된 구조
- `ai_invest/`
- `frontend/`
- `app/`
- `scripts/`
- `tests/`
- `notifications/`
- `ops/`
- `research/`
- `strategy/`
- `meetings/`

이 레포는 단순 자동매매 코드가 아니라,
운영/전략/리뷰/알림 구조가 이미 존재하는 시스템형 레포다.

## finance 조직의 1차 역할
- 이 레포의 운영 구조를 읽는다
- 로그/알림/리뷰 루프를 파악한다
- Discord 채널 구조에 매핑한다
- 이후 개선 포인트와 코드 수정 흐름을 만든다

## 바로 해야 할 것
1. README / architecture / ops 문서 읽기
2. 실제 실행 루프 스크립트 파악
3. 알림 경로 파악
4. 리뷰/개선 루프 문서화
5. Discord 채널별 책임 연결

## 목표
finance 조직이 이 레포를 기준으로
- 운영 상태를 읽고
- 문제를 감지하고
- 개선 포인트를 만들고
- 필요하면 코드 수정까지 이어지게 한다

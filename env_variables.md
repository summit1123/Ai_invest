# env_variables.md - 환경변수 표준 (v1)

## 1) 기본 앱/런타임

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `APP_ENV` | Y | `dev` | 실행 환경 (`dev/staging/prod`) |
| `APP_TIMEZONE` | Y | `Asia/Seoul` | 기본 시간대 |
| `LOG_LEVEL` | Y | `INFO` | 로깅 레벨 |

## 2) OpenAI (LLM + Embeddings)

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `OPENAI_API_KEY` | Y | `sk-...` | OpenAI API 키 |
| `OPENAI_BASE_URL` | N | `https://api.openai.com/v1` | 프록시/대체 베이스 URL |
| `OPENAI_ORG_ID` | N | `org_...` | 조직 ID |
| `OPENAI_LLM_MODEL` | Y | `gpt-5` | 의사결정 보조/요약 LLM |
| `OPENAI_EMBEDDING_MODEL` | Y | `text-embedding-3-small` | 임베딩 모델 |
| `OPENAI_EMBEDDING_DIMENSIONS` | N | `1536` | v3 모델 차원 축소/고정 옵션 |

## 3) 거래소(Upbit)

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `UPBIT_ACCESS_KEY` | Y | `...` | 업비트 Access Key |
| `UPBIT_SECRET_KEY` | Y | `...` | 업비트 Secret Key |
| `UPBIT_API_BASE_URL` | N | `https://api.upbit.com` | REST base URL |
| `UPBIT_WS_PUBLIC_URL` | N | `wss://api.upbit.com/websocket/v1` | 시세/호가 WS URL |
| `UPBIT_WS_PRIVATE_URL` | N | `wss://api.upbit.com/websocket/v1/private` | private WS URL(사용 시) |

## 4) DB/스토리지

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `POSTGRES_DSN` | Y | `postgresql+psycopg://...` | 운영 DB (events/orders/decisions 등) |
| `DUCKDB_PATH` | Y | `./data/lake/market.duckdb` | 분석/백테스트 로컬 파일 |
| `PARQUET_ROOT` | Y | `./data/parquet` | 원천/피처 parquet 루트 |
| `REDIS_URL` | N | `redis://localhost:6379/0` | 큐/락/알림 dedupe |

## 5) Telegram 알림

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Y | `123456:ABC...` | Bot 토큰 |
| `TELEGRAM_CHAT_ID_OPS` | Y | `-100...` | ops-critical 채널 |
| `TELEGRAM_CHAT_ID_TRADING` | Y | `-100...` | trading-feed 채널 |
| `TELEGRAM_CHAT_ID_REVIEW` | Y | `-100...` | review-report 채널 |
| `TELEGRAM_CHAT_ID_RESEARCH` | Y | `-100...` | research-daily 채널 |
| `TELEGRAM_CHAT_ID_MEETING` | Y | `-100...` | agent-meeting 채널 |
| `TELEGRAM_CHAT_ID_ENGINEERING` | Y | `-100...` | engineering-change 채널 |
| `SEND_TELEGRAM` | N | `true` | 실제 Telegram 전송 on/off (off면 delivery는 `PENDING` 저장) |
| `NOTIFY_SAFE_DECISION_HOLD` | N | `false` | Safe 결정이 `HOLD`일 때 알림 전송 여부 |
| `NOTIFY_SAFE_DECISION_CHANGE_ONLY` | N | `true` | Safe 결정 알림을 상태 변화가 있을 때만 전송 |
| `NOTIFICATION_DEDUPE_WITHIN_SEC` | N | `60` | dedupe key 기준 중복 전송 억제 시간(초) |

## 6) Slack 알림 (추가)

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | Y | `xoxb-...` | Slack bot token |
| `SLACK_SIGNING_SECRET` | Y | `...` | 요청 검증용 시크릿 |
| `SLACK_APP_TOKEN` | N | `xapp-...` | Socket mode 사용 시 |
| `SLACK_WORKSPACE` | Y | `ai-invest` | 워크스페이스 이름 |
| `SLACK_CHANNEL_ID_OPS` | Y | `C...` | ai-invest-ops-critical |
| `SLACK_CHANNEL_ID_TRADING` | Y | `C...` | ai-invest-trading-feed |
| `SLACK_CHANNEL_ID_REVIEW` | Y | `C...` | ai-invest-review-report |
| `SLACK_CHANNEL_ID_RESEARCH` | Y | `C...` | ai-invest-research-daily |
| `SLACK_CHANNEL_ID_MEETING` | Y | `C...` | ai-invest-agent-meeting |
| `SLACK_CHANNEL_ID_ENGINEERING` | Y | `C...` | ai-invest-engineering-change |
| `SLACK_CHANNEL_ID_GOVERNANCE` | Y | `C...` | ai-invest-governance |

## 7) 벡터 DB (택1 + 공통)

공통:

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `VECTOR_STORE_PROVIDER` | Y | `qdrant` | `qdrant/pinecone/weaviate/pgvector` |
| `VECTOR_COLLECTION` | Y | `casebook_docs_v1` | 컬렉션/인덱스명 |
| `VECTOR_DISTANCE` | Y | `cosine` | 거리 메트릭 |
| `VECTOR_TOP_K_DEFAULT` | Y | `8` | 기본 검색 개수 |

Qdrant:

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `QDRANT_URL` | 조건부 | `https://...cloud.qdrant.io` | Qdrant endpoint |
| `QDRANT_API_KEY` | 조건부 | `...` | API key |

Pinecone:

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `PINECONE_API_KEY` | 조건부 | `...` | API key |
| `PINECONE_INDEX` | 조건부 | `casebook-v1` | 인덱스명 |
| `PINECONE_CLOUD` | 조건부 | `aws` | cloud |
| `PINECONE_REGION` | 조건부 | `us-east-1` | region |

Weaviate:

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `WEAVIATE_URL` | 조건부 | `https://...weaviate.network` | endpoint |
| `WEAVIATE_API_KEY` | 조건부 | `...` | API key |

pgvector:

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `PGVECTOR_DSN` | 조건부 | `postgresql+psycopg://...` | vector 전용 DB 또는 운영 DB |

## 8) GitHub 변경관리(자동 PR/CI)

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `GITHUB_TOKEN` | Y | `ghp_...` | PR/CI 자동화 토큰 |
| `GITHUB_REPO` | Y | `owner/repo` | 대상 리포 |
| `GITHUB_BASE_BRANCH` | Y | `main` | 기준 브랜치 |

## 9) 운영/리포트

| 변수 | 필수 | 예시 | 설명 |
|---|---|---|---|
| `REPORT_TIMEZONE` | Y | `Asia/Seoul` | 리포트 기준 시간대 |
| `PAPER_TRADING` | Y | `true` | 실거래 전 기본 true |
| `ENABLE_LIVE_TRADING` | Y | `false` | 실거래 스위치 |
| `MAX_DAILY_LOSS_PCT_OVERRIDE` | N | `1.5` | 긴급 오버라이드(권장: 미사용) |
| `APP_AUTOSTART_ORCHESTRATOR` | N | `true` | API 서버 부팅 시 오케스트레이터 자동 기동(기본: true, pytest 환경은 기본 false) |
| `APP_AUTOSTART_FORCE` | N | `false` | 외부 오케스트레이터가 떠 있어도 강제 기동 |
| `APP_AUTOSTART_LOG_PATH` | N | `logs/orchestrator.autostart.log` | 자동기동 오케스트레이터 로그 경로 |
| `ORCHESTRATOR_STATUS_PATH` | N | `runtime/orchestrator_status.json` | 오케스트레이터 상태 파일 경로 |

## 10) 보안/운영 원칙

1. `.env`는 절대 커밋하지 않는다.
2. 로컬은 `.env`, 배포는 Secret Manager를 사용한다.
3. 필수 키 누락 시 부팅 실패하도록 한다(fail fast).
4. `PAPER_TRADING=true`가 기본값이며, `ENABLE_LIVE_TRADING=true`는 운영 승인 후에만 변경한다.

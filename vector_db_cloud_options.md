# vector_db_cloud_options.md - OpenAI 기반 벡터 DB 클라우드 비교 (as of 2026-02-09)

## 결론 요약

1. **MVP/개인 운영 24/7 기준 추천 1순위: Qdrant Cloud**
   - 이유: 단순 API, 운영 난이도 낮음, 비용 시작점이 낮은 편.
2. **팀/엔터프라이즈 확장형: Pinecone**
   - 이유: 인덱스/운영 기능이 성숙하고 region 선택이 명확.
3. **기존 Postgres 중심이면: pgvector (Supabase/Neon 등)**
   - 이유: 인프라 단순화(한 DB), 다만 벡터 전용 엔진 대비 성능 튜닝 부담 존재.

## 전제

- 임베딩/LLM은 OpenAI 사용:
  - Embeddings: `text-embedding-3-small` 또는 `text-embedding-3-large`
  - LLM: OpenAI chat model
- 24/7 운영에서 핵심은:
  - 장애 대응 쉬움
  - 지연/비용 예측 가능
  - 백업/복구 용이성

## 후보 비교

| 항목 | Qdrant Cloud | Pinecone | Weaviate Cloud | pgvector(Managed Postgres) |
|---|---|---|---|---|
| 운영 난이도 | 낮음 | 낮음~중간 | 중간 | 중간 |
| 비용 시작점 | 낮은 편 | 무료/유료 플랜 다양 | 유료 시작점 명확 | DB 플랜 의존 |
| OpenAI 연동 | 단순 | 공식 가이드 풍부 | OpenAI integration 문서 제공 | 앱 레벨에서 구현 |
| 24/7 개인운영 적합도 | 높음 | 높음 | 중간 | 중간 |
| 강점 | 간단/가성비 | 성숙한 관리형 벡터DB | 모듈형/확장성 | 스택 단순화 |
| 주의점 | 고성능 대규모는 플랜 설계 필요 | 비용 구조 이해 필요 | 초기 구성/러닝커브 | 튜닝/성능 책임 증가 |

## 추천 아키텍처 (현재 프로젝트 기준)

### 단계 1 (MVP)
- `VECTOR_STORE_PROVIDER=qdrant`
- 컬렉션: `casebook_docs_v1`
- 거리: `cosine`
- OpenAI 임베딩 생성 -> Qdrant upsert -> Top-K 조회

### 단계 2 (트래픽 증가 시)
- 응답 지연/비용 기준 초과 시 Pinecone 재평가
- 또는 Postgres 통합 필요가 크면 pgvector 전환 검토

## 미니PC vs 클라우드

- 24/7 개인 투자 운영에서는 **클라우드 벡터DB가 유리**:
  - 전원/디스크/백업 리스크 감소
  - 운영 중단 시 복구가 빠름
  - 확장 시 증설이 단순
- 미니PC+FAISS는 초기 실험에는 좋지만:
  - 장애복구/백업/원격 접근/지속운영에서 부담이 커짐

## OpenAI 모델 선택 기준

1. 비용/속도 우선: `text-embedding-3-small`
2. 정밀도 우선: `text-embedding-3-large`
3. 차원/비용 조절 필요 시 `dimensions` 옵션 사용

## 운영 체크포인트

1. 임베딩 생성 실패율
2. upsert 지연
3. 검색 p95 지연
4. hit quality(유사사례 유효도)
5. 월 비용 추이

## 출처 (공식 문서)

- OpenAI Embeddings: https://platform.openai.com/docs/guides/embeddings
- OpenAI Vector DB 파트너 가이드: https://cookbook.openai.com/examples/vector_databases/readme
- Qdrant Cloud: https://qdrant.tech/documentation/cloud/
- Qdrant Pricing: https://qdrant.tech/pricing/
- Qdrant + OpenAI: https://qdrant.tech/documentation/embeddings/openai/
- Pinecone Pricing: https://www.pinecone.io/pricing/
- Pinecone Regions: https://docs.pinecone.io/reference/api/latest/control-plane/create_index
- Pinecone + OpenAI: https://www.pinecone.io/learn/series/langchain/langchain-retrieval-augmentation/
- Weaviate Pricing: https://weaviate.io/pricing
- Weaviate + OpenAI: https://docs.weaviate.io/weaviate/model-providers/openai
- Supabase pgvector: https://supabase.com/docs/guides/database/extensions/pgvector
- pgvector: https://github.com/pgvector/pgvector

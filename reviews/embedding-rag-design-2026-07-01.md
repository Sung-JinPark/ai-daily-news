# 임베딩·RAG 설계 (Z4) — 승인 대기

**Date**: 2026-07-01
**Status**: **DESIGN. Implementation pending user approval.**
**Reason**: 이 방향은 지속적 월비용($10~30 추가 예상)과 새 파이프라인 단계를 추가하므로 설계·비용 산정 후 사용자가 GO/NO-GO 결정.

---

## 왜 (Motivation)

현재 `/search`는 90일 · 1.2MB 인덱스에서 **부분일치 검색**만 함:
- "GPT" 검색 시 title/summary에 "GPT" 문자열 포함된 것만
- 의미상 관련 (예: "언어 모델")이지만 문자열 불일치 → miss
- 전 기간 검색 불가 (SQLite 다운로드 필요)

**임베딩 도입 시 가능해지는 것**:
1. **시맨틱 `/search`** — "언어 모델 안전성" 검색 → 실제로 그 주제를 다룬 기사가 나옴 (문자열 불일치도 OK)
2. **`/ask` RAG 챗봇** — "지난 분기 오픈소스 트렌드는?" → 아카이브 인용하며 답변
3. **관련 기사 자동 추천** — 각 스토리 페이지에 "유사한 스토리" 확장
4. **중복 감지 강화** — SimHash가 놓치는 의미상 유사한 기사 클러스터링

---

## 공급자 비교 (2026-07 기준)

| 공급자 | 모델 | 차원 | 단가 (per 1M tokens) | 강점 | 약점 |
|---|---|---|---|---|---|
| **Voyage AI** | voyage-3 | 1024 | **$0.06** input | 가장 저렴, 뉴스 도메인 fine-tuned voyage-3-large 옵션 | 상대적 신생, 벤치마크 소량 |
| **OpenAI** | text-embedding-3-small | 1536 | $0.02 (Batch $0.01) | 저렴, 광범위 검증됨 | 다른 OpenAI 인프라 필요 |
| **OpenAI** | text-embedding-3-large | 3072 | $0.13 (Batch $0.065) | 최고 품질 (MTEB 상위) | 저장·인덱싱 비용↑ |
| **Cohere** | embed-multilingual-v3 | 1024 | $0.10 | 한국어 강함, 매체 성격에 맞음 | OpenAI 대비 비쌈 |
| **Anthropic** | (자체 임베딩 미제공) | — | — | — | Voyage AI 공식 파트너로 권장 |
| **Local (sentence-transformers)** | multilingual-e5-large | 1024 | $0 (인프라만) | 반복 비용 0, 데이터 통제 | GPU/CPU 인프라 필요, GH Pages와 호환 안 됨 |

### 권장: **Voyage AI (voyage-3 + Anthropic 파트너십)**

이유:
- 이 프로젝트가 이미 Anthropic Claude를 씀 → 공식 권장 임베딩 파트너
- 단가 $0.06/M tokens = OpenAI-small의 1/3에서 3배 저렴 (실측 필요)
- 한국어 요약 + 영어 원제 혼재 = multilingual 필요 → voyage-3은 지원
- GitHub Actions 배치 실행 친화적 (Anthropic 배치와 유사한 패턴)

**Fallback**: OpenAI text-embedding-3-small ($0.01 with Batch API). 가장 널리 지원되고 저렴.

---

## Corpus 볼륨 · 비용 산정

### 임베딩 대상

**Layer 1**: 요약 요약 임베딩
- `articles.json`의 `summary_ko` (평균 250자 ≈ 150 tokens)
- 총 기사: 현재 1,786 → 1년 후 ~29,000
- 총 tokens: 29,000 × 150 = **4.4M tokens/년**
- Voyage-3 비용: 4.4M × $0.06/M = **$0.26/년**

**Layer 2**: 원문 본문 임베딩 (더 강력)
- `data/corpus/YYYY-MM-DD/bodies.jsonl`의 `body_text` (평균 4,000자 ≈ 2,500 tokens)
- 각 본문을 500 tokens chunks로 분할 (RAG 표준)
- 청크당 임베딩: 2,500 / 500 = 5 청크
- 총 청크: 29,000 × 5 = **145,000 청크/년**
- 총 tokens: 145,000 × 500 = **72.5M tokens/년**
- Voyage-3 비용: 72.5M × $0.06/M = **$4.35/년**

### 스토리지

- 벡터 크기: 1024차원 × 4 bytes (float32) = 4 KB/vector
- 요약 임베딩: 29,000 × 4KB = 116 MB
- 본문 청크 임베딩: 145,000 × 4KB = **580 MB**
- **총 ~700 MB/년**

Git 저장은 무리. 대안:
- **npm 옵션 A**: 매일 재빌드 후 GitHub Release로 배포 (X1 후속)
- **npm 옵션 B**: 별 서비스 (Cloudflare R2 무료 티어 10 GB · Backblaze B2 · AWS S3)
- **npm 옵션 C**: HuggingFace Datasets 무료

권장: **A** (GitHub Release + Actions artifact). 이미 M4에서 archive.db.gz 배포 인프라 있음. 벡터도 같은 방식.

### 쿼리 비용

Voyage-3 쿼리 임베딩: 평균 20 tokens × $0.06/M = $0.00000012/쿼리. 무시 가능.

### RAG 시 LLM 비용

`/ask`에서 top-K 청크 + 사용자 질문 → Haiku 답변 생성:
- 컨텍스트: 5 청크 × 500 tokens = 2,500 tokens
- 질문: 100 tokens
- 답변: 500 tokens
- 총 (per 쿼리): $1/M × 2600 + $5/M × 500 = **$0.005/쿼리**

일 100 쿼리 가정: $0.50/일 = **$15/월**. 시맨틱 검색만 하면 이 비용 없음.

### 총 비용 시나리오

| 시나리오 | 임베딩 (년) | RAG LLM (월) | 총 (월) |
|---|---|---|---|
| **요약만 임베딩 + 시맨틱 /search** | $0.26 | $0 | **~$0.02** |
| **본문 임베딩 + 시맨틱 /search** | $4.35 | $0 | **~$0.36** |
| **본문 임베딩 + 시맨틱 /search + /ask** | $4.35 | $0~15 | **$0.36~15.36** |

가장 저렴한 옵션(요약만)도 시맨틱 검색이 매우 개선됨. **RAG /ask는 별도 결정.**

---

## 파이프라인 설계

### 신규 모듈: `pipeline/embed.py`

**로직**:
```python
# 매일 daily.yml에서 실행
# 1. summarize 이후, 신규 요약 embeddings 생성
# 2. bodies.jsonl 신규 항목 embeddings 생성 (옵션)
# 3. Voyage API 배치 호출 (128 items per batch)
# 4. 결과를 data/embeddings/YYYY-MM-DD.jsonl.gz에 저장
#    (article_id, text_source, chunk_idx, vector[1024])
```

**단계**:
1. 어제까지 처리된 마지막 article_id 조회 (`data/embeddings/manifest.json`)
2. 신규 기사 필터
3. Voyage batch API 호출
4. gzip JSONL로 저장
5. manifest 갱신 + sha256

**멱등**: 동일 article_id 재실행 시 스킵.

### 검색 인덱스: 벡터 저장소

**단순 접근** (권장 첫 단계):
- `data/embeddings/index.faiss` (Faiss flat index) 매일 재빌드
- 크기: 145K × 4KB = 580 MB → gzip 후 ~200 MB
- GitHub Release로 배포 (매주 1회)

**시맨틱 `/search`**:
- 사이트 방문 시 Faiss index 미리 로드 (JS wasm 사용 가능한 hnswlib 등)
- 실시간 유사도 검색 (질의 임베딩 → cosine similarity)

**대안**: 
- Turbopuffer / Pinecone (SaaS) — 저렴하지만 종속성
- Cloudflare Vectorize — 서버리스 벡터 DB, 무료 티어 있음

**권장**: 첫 단계는 **build-time에 top-K 미리 계산** — 각 기사에 대해 "유사한 5개" 미리 배치 산출 → `data/similarity/YYYY-MM-DD.json`으로 저장 → 사이트가 fetch. 실시간 임베딩 검색은 v2.

---

## UI 설계

### 시맨틱 `/search` (v1)

기존 `/search` 확장:
- 상단에 토글: `[문자열] [시맨틱]`
- 시맨틱 모드: 
  - 쿼리 → Voyage API 임베딩 → build-time 저장된 벡터와 비교
  - 실제로는 build-time에 미리 계산 안 되므로 v1에서는 "관련 태그로 확장 검색" fallback
- v2에서 진짜 벡터 검색 (Cloudflare Vectorize 등)

### `/ask` RAG (v2)

새 페이지:
- 채팅 UI (단일 턴, 다중 턴 X)
- 질문 입력 → Voyage 임베딩 → top-5 청크 fetch → Haiku 프롬프트
- 답변 + 참조 기사 링크
- localStorage에 채팅 이력 저장 (선택)

**정적 사이트 제약**: Voyage API 호출은 클라이언트 사이드 (API key 노출 위험). 대안:
1. **Cloudflare Worker** 프록시 (무료 티어 100K 요청/일) → API key 서버에 숨김
2. **GitHub Actions로 사전 계산된 FAQ** (일반적 질문 답변 미리 만들어둠)
3. **Turbopuffer / Cloudflare Vectorize API** 직접 (API key still noise)

권장: v1 (시맨틱 `/search`)는 **top-K similarity 사전 계산** 방식으로 정적 유지. v2 (`/ask`)는 Cloudflare Worker 프록시가 필요 → 별도 인프라 결정.

### 관련 스토리 확장 (즉시 가능)

`/story/[cluster]` 하단의 "관련 스토리" 6개가 지금 tag Jaccard 기반. 임베딩 도입 시 시맨틱 유사도로 대체 → 훨씬 정확.

---

## 마일스톤 제안

### Z-Embed v1 (권장 스타트)
1. `pipeline/embed.py` 구현 — Voyage API + Batch, 요약만 임베딩
2. `data/embeddings/*.jsonl.gz` 저장 + manifest
3. `pipeline/similarity.py` — 각 기사의 top-K 사전 계산 → `data/similarity/*.json`
4. 스토리 페이지의 "관련 스토리"를 시맨틱 유사도로 대체
5. `/search`에 시맨틱 토글 (사전 계산된 top-K로 확장 검색)
- **비용**: ~$0.30/월 (모든 것 포함)
- **작업량**: 3-4 세션

### Z-Embed v2 (선택)
1. 본문 임베딩 + 청크 인덱스
2. Cloudflare Worker 프록시 API
3. `/ask` RAG 페이지
- **비용**: ~$5~20/월 (사용량 따라)
- **작업량**: 4-5 세션

---

## 승인 결정 항목

사용자에게 질문:

1. **공급자**: Voyage AI (권장) / OpenAI / Cohere / 로컬 sentence-transformers 중 어느 것?
2. **범위**: v1만 (시맨틱 검색 + 관련 스토리, ~$0.3/월) / v1+v2 전체 (/ask 포함, ~$5-20/월)?
3. **본문 임베딩 여부**: 요약만 (저렴) / 본문 포함 (더 정확, 청크 5배)?
4. **RAG 인프라**: Cloudflare Worker 프록시 도입 vs 사전 계산 FAQ만?
5. **스토리지**: GitHub Release (권장, 무료) vs Cloudflare R2 (5GB 이상 시)?
6. **첫 마일스톤 시점**: 지금 바로 / X 시리즈 검증 후 (2026-07-14 이후) / 8월?

## 다음 세션

승인 시 위 결정에 따라 Z-Embed v1을 3-4개 마일스톤으로 분할 실행.
- ZE1: `pipeline/embed.py` (Voyage API 통합)
- ZE2: `pipeline/similarity.py` (top-K 사전 계산)
- ZE3: 스토리 페이지 "관련 스토리" 시맨틱화
- ZE4 (선택): `/search` 시맨틱 토글

---

**한 줄 요약**: 시맨틱 검색만이면 월 $0.3 (Voyage-3 요약 임베딩 + 사전 계산 유사도)로 시작 가능. RAG /ask까지 원하면 인프라(Cloudflare Worker) + 월 $5~20. 승인 항목 6개 결정 후 세부 마일스톤 설계.

---
description: LLM/외부 API 호출 규약과 파이프라인 단계 간 JSON 데이터 계약
paths:
  - "pipeline/**"
  - "site/src/lib/**"
  - "data/**"
---

# API · 데이터 계약

## Anthropic / Voyage
- 요약은 **Anthropic Batch API** (`client.messages.batches.create/retrieve/results`)로만 보낸다.
  50% 할인 목적이며, 동기 `messages.create`로 바꾸면 비용이 2배가 된다.
- 모델 ID는 모듈 상수 한 곳에만 둔다 (`pipeline/summarize.py`의 `MODEL`). 호출부에 흩뿌리지 않는다.
- 배치는 제출 후 폴링(런당 최대 50분). 부분 실패(스키마 불합격 등)는 건너뛰되 로그를 남기고,
  전체를 실패시키지 않는다.
- **배치 타임아웃은 실패가 아니라 "다음 런으로 이어받기"다 (AUD-030)** — Batch API SLA는
  "보통 수 분, 과부하 시 최대 24시간"이라 런 내 타임아웃만으론 못 버틴다(2026-09-29 관측: 7시간+).
  50분 내 `ended`가 안 되면 `pipeline.summarize`가 `.cache/pending_batch.json`에
  `{day, batch_id, cluster_meta, stats}`를 저장하고 **그 날짜 articles.json을 빈 배열로 써서**
  (rank/digest가 "파일 없음"으로 하드 실패하지 않게) `exit 0`한다. 다음 런 시작 시
  Phase 0에서 그 배치를 조회해 `ended`면 결과를 병합(resubmit 없음), 아니면 그대로 다음 런까지 대기.
  `index_latest.py`는 article 수 0인 날을 `latest_day`로 선택하지 않는다(전날 실데이터 유지).
- 프롬프트는 `pipeline/utils/prompts.py`에 모은다. 인라인 문자열로 흩어놓지 않는다.
- API 키는 `.env`의 `ANTHROPIC_API_KEY`에서만 읽는다. 하드코딩·로그 출력 금지.

## 외부 HTTP (수집)
- `pipeline/utils/http` 경유 (User-Agent 식별 + 호스트별 1초 간격). robots/레이트 리밋을 우회하지 않는다.
- 소스 목록은 `pipeline/sources.yaml`이 단일 출처. 코드에 URL을 박지 않는다.

## 단계 간 데이터 계약 (`data/YYYY-MM-DD/`)
- 산출물: `articles.json`(기사 배열) · `highlights.json` · `trending.json` · `digest.json` ·
  `arxiv_refs.json` · `_stats.json`.
- `articles.json` 항목의 필수 키 — 임의로 제거하지 않는다:
  `id, cluster_id, title_original, url, source_id, source_name, published, fetched_at,
  cluster_size, also_covered_by, summary_ko, insights_ko, category, importance_score, tags`.
- 키를 추가/변경하면 **같은 커밋에서** `site/src/lib/loadData.ts`의 타입과 사용처를 함께 고친다.
- 쓰기는 항상 `write_text_atomic` + UTF-8, `ensure_ascii=False`. 날짜는 ISO 8601 UTC.
- 기사 **본문 전문**은 산출물에 넣지 않는다 (`data/corpus/*/bodies.jsonl`은 로컬 전용 — DBQ-3).
- 파이프라인 단계는 순서 의존적이다: collect → dedupe → summarize → rank → trending → index_latest.
  앞 단계 산출물이 없으면 뒤 단계는 조용히 통과하지 말고 명확히 실패한다.

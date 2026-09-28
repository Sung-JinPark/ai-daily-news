---
description: Python 파이프라인 · Astro 사이트 코드 스타일
paths:
  - "pipeline/**"
  - "site/**"
  - "scripts/**"
---

# 코드 스타일

포매터·린터 설정이 없다(ruff/black/eslint 모두 미사용). **주변 코드의 관례를 그대로 따른다.**

## Python (`pipeline/`, `scripts/`)
- 파일 맨 위: 한 줄 모듈 docstring → `from __future__ import annotations` → 표준 라이브러리 →
  서드파티 → `pipeline.*` 순 import.
- 모듈 로거는 `log = logging.getLogger(__name__)`. `print()`는 CLI 출력에만.
- 모듈 레벨 상수는 UPPER_SNAKE (`TOP_N = 5`, `MODEL = "..."`), 내부 전용은 `_` 접두.
- 타입 힌트는 PEP 604 (`str | None`, `dict`, `list`) — `Optional`/`Dict` 사용하지 않는다.
- CLI 진입점은 `argparse` + `python -m pipeline.<module>`로 실행 가능하게.
- 주석은 **왜**를 남긴다. 결정 근거가 있으면 감사 ID를 붙인다 (예: `AUD-006`, `DBQ-3`, `REPRO-1`).
- 들여쓰기 4칸, 한글 주석/문서 허용, 소스 인코딩은 UTF-8.

### 반드시 재사용할 유틸
- **원자적 쓰기** — 읽고-고쳐-다시쓰는 출력(continuity, aggregate JSONL, latest.json)은
  `pipeline.utils.atomic.write_text_atomic`으로만 쓴다. 직접 `Path.write_text()` 금지
  (중단 시 누적 파일 전체 손실 + `git add`가 torn 파일을 커밋할 수 있음 — AUD-006).
- **HTTP** — 외부 요청은 `pipeline.utils.http`를 통한다 (식별용 User-Agent + 호스트별 1초 레이트 리밋).
  `httpx`/`requests`를 모듈에서 직접 호출하지 않는다.
- **날짜** — UTC aware datetime (`datetime.now(timezone.utc)`). naive datetime을 만들지 않는다.

## Astro / TypeScript (`site/`)
- 링크·자산 경로는 반드시 base path를 붙인다:
  `const base = import.meta.env.BASE_URL.replace(/\/$/, "");` → `` `${base}/path` ``.
  하드코딩된 `/ai-daily-news/`는 금지 (dev에는 base가 없어 깨진다).
- 데이터 로딩은 `site/src/lib/loadData.ts`를 통해서만. 페이지에서 `../data`를 직접 읽지 않는다.
- 데이터 모양이 바뀌면 `loadData.ts`의 `export type`(예: `Article`)을 같은 커밋에서 갱신한다.
- 스타일은 Tailwind 유틸리티. 빌드는 `output: "static"`이므로 서버 런타임 코드를 넣지 않는다.

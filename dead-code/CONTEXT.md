# DEAD CODE CLEANUP — filled slots (ai-daily-news, 2026-08-31)

## 2. PROJECT CONTEXT

- 저장소 루트: `C:\workspace\ai-daily-news`
- 스택: Python 3.12 (pipeline/) + Astro 4 / TypeScript (site/), 패키지 매니저 pip(no venv) / npm
- 배포 형태: **APP** — pyproject.toml이 `pipeline`을 hatchling wheel로 패키징하지만 PyPI 미배포(내부용). site는 GitHub Pages 정적 배포. LIBRARY 취급 불필요.
- 명령어
  - install (py): `pip install -e .[dev]` (실제로는 이미 전역에 pytest만 설치돼 있음; ruff/vulture는 이번 분석용으로 임시 설치)
  - install (site): `npm install --no-audit --no-fund` (site/ 안에서)
  - build (site): `npm run build` (site/ 안에서, = `astro build`)
  - typecheck (site): `npx astro check` (site/ 안에서) — 별도 tsc 설정 없음, astro check가 .astro+.ts 전체를 봄
  - lint: 없음 (ESLint 미설치·미설정 — 이번 작업에서 새로 설치/설정하지 않는다. P6 위반 방지)
  - test (py): `python -m pytest tests/ -q` (repo root에서, pythonpath=".")
  - test (site): 없음 (vitest/jest 미설치)
  - bundle size: 생략 (astro build 로그의 페이지 수만 비교)
- 진입점 (RED-LINE, F 카테고리 "미사용 파일" 판정에서 제외):
  - `python -m pipeline.<module>` 로 CI/배치에서 직접 호출되는 최상위 모듈: audit_cluster_merge, audit_sources, audit_ci_health, collect, dedupe, summarize, rank, digest, aggregate_tags, weekly, glossary, trending, entity_index, embed, similarity, themes, predict_extract, quarterly_report, model_facts, index_latest, build_db, collect_papers (근거: `.github/workflows/*.yml` + `run-pipeline.bat` grep 결과)
  - `site/src/pages/**/*.astro`, `site/src/pages/**/*.ts` 전체 — Astro 파일 기반 라우팅 규약. 어떤 페이지도 다른 파일이 import하지 않는 게 정상이므로 F 카테고리 판정 대상에서 전부 제외.
  - `scripts/verify-populated.mjs`, `scripts/verify-realdata.mjs` — `site/package.json` scripts + `docs/*.md` 런북에 명시된 수동 실행 진입점.
- 동적 로딩 위치: 없음으로 확인됨 — `importlib`/`getattr(obj, name)`/`globals()[...]` 패턴 pipeline 전역(research 제외) grep 0건, Astro `<component :is>`/템플릿 리터럴 dynamic import/`Astro.glob` 0건. (재확인 근거는 findings.raw.md에 grep 커맨드와 함께 기록)
- 외부 계약: 위 진입점 전부 + RSS(`/rss.xml`)·sitemap(`/sitemap.xml`)·검색 인덱스(`/search-index.json`) 공개 엔드포인트(외부 크롤러/구독자가 소비) + `data/*.json` 필드 스키마(파이썬이 쓰고 `site/src/lib/loadData.ts`가 읽는 크로스 언어 계약 — 필드 삭제·rename은 이번 범위 밖, P6 동작보존 원칙으로 처리)
- 보호 경로(glob) — 절대 건드리지 않음:
  - `data/**` (파이프라인 산출물 전체, 코드 아님)
  - `pipeline/research/**` (CLAUDE.md가 명시한 민감 영역 — leak gate, private 데이터, 별도 methodology 스킬(`concept-research-methodology`)로만 다룸. **이번 정리 범위에서 완전히 제외**, 손대지 않고 report에만 "제외" 명시)
  - `docs/**`, `guide/**`, `cost/**`, `raw/**`, `fixtures/**`, `reviews/**`, `review/**`, `research/**`(top-level, `pipeline/research`와 다른 디렉토리 — 문서/방법론 노트), `notebooks/**` — 전부 문서·픽스처·아카이브, 애플리케이션 코드 아님
  - `site/dist/**`, `site/node_modules/**`, `**/__pycache__/**`, `.venv/**`, `.pytest_cache/**`, `.cache/**` — 빌드 산출물/의존성
  - `.github/workflows/**` — CI 계약 자체
  - `site/public/**` — 정적 자산, URL 문자열로만 참조되어 import 그래프로 검증 불가 → 손대지 않음 (H 카테고리 후보에서도 제외, Tier 3조차 만들지 않음)
  - `"내가 볼 자료/"` — 이번 작업과 무관한 사용자 폴더
  - `.env`, `.env.example`, `.gitleaks.toml`, `.pre-commit-config.yaml`, `CLAUDE.md`, `README.md`, 루트 `*.md` — 설정/문서
- 테스트 없는 영역 정책: `tests/`는 `pipeline/research/*`(제외 대상)와 `pipeline.dedupe`, `pipeline.rank`, `pipeline.summarize`, `pipeline.trending`만 커버한다. 그 외 pipeline 최상위 모듈(collect, digest, aggregate_tags, weekly, glossary, entity_index, embed, similarity, themes, predict_extract, quarterly_report, model_facts, index_latest, build_db, audit_*, backfill_*, corpus_writer, state, extract, arxiv_refs)과 site 전체는 무테스트. → **기본값 채택**: 무테스트 영역은 D·F만 Tier 2 허용(빌드+typecheck 통과 조건부), E·G·H·L은 Tier 3(보고만).
- 기준 브랜치: `main` / 작업 브랜치: `chore/dead-code-20260831`
- 탐지 도구
  - Python: `ruff check --select F401,F811,F841` (A), `vulture --min-confidence 80` (D/E/F 후보) — 이번 실행을 위해 임시로 `pip install ruff vulture` (pyproject.toml에는 추가하지 않음, 분석 전용)
  - Python 의존성(G): `pyproject.toml` dependencies 17개를 대상으로 저장소 전체 import grep 수동 대조 (deptry 미설치 시 폴백)
  - TS/Astro: `npx knip` (E/F/G), `npx astro check`(타입/미사용 관련 진단, A에 준함)
  - ESLint/vue-plugin 계열은 **미설치 상태 유지** — 새로 설치하면 P6(동작 보존 외 변경 금지) 위반 소지 있어 이번 실행 도구셋에서 제외
- 최대 반복: 25 (queue 소비 1항목=1반복 기준, Phase0~1은 이미 이 세션에서 완료했으므로 큐 처리+Phase4에 집중)
- 산출물 디렉토리: `dead-code/` (이 파일 CONTEXT.md 포함, BASELINE.md, findings.raw.md, findings.triaged.md, STATE.md, DEAD_CODE_REPORT.md)

## 이번 실행의 스코프 축소 (근거 기록)

원 설계도의 K(DB 객체)는 해당 없음(오라클/전통 DB 없음, SQLite 산출물은 데이터). J(API 엔드포인트)는 site의 `.xml.ts`/`.json.ts`가 전부 공개 계약이라 사실상 Tier 3 이상 후보가 나오기 어려움 — 별도로 취급하지 않고 발견 시에만 Deferred에 기록.
`pipeline/research/**`는 코드량이 크고(30+ 파일) 민감도가 높아 완전 제외. 필요하면 별도 실행에서 `concept-research-methodology` 스킬을 먼저 로드하고 진행할 것을 REPORT에 권고 사항으로 남긴다.

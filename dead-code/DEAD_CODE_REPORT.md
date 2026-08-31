# DEAD CODE CLEANUP REPORT — ai-daily-news

- 브랜치: `chore/dead-code-20260831` (base `main` @ 080a661)
- 실행: 2026-08-31, 단일 세션 내 직접 실행 (§방법론 노트 참고 — 문서가 상정한 `/ralph-loop` 반복 대신, 컨텍스트가 유지되는 이번 세션에서 Phase 0~4를 직접 순서대로 수행)
- 스코프: `pipeline/**` (excl. `pipeline/research/**`) + `site/src/**`. `pipeline/research/**`, `data/**`, docs/fixtures/review 계열 디렉토리는 CONTEXT.md에 근거와 함께 전부 제외.

## Summary

| 지표 | Baseline | Final | Δ |
|---|---|---|---|
| pytest | 115 passed | 115 passed | 0 |
| site build | 1605 pages | 1605 pages | 0 |
| LOC (diff) | — | — | -356 / +6 (net -350) |
| 커밋 수 | — | 3 | — |
| 반복 | — | 3 (직접 실행, 큐 항목 3개) | — |

## Removed (Tier 1·2)

| # | 파일:라인 | 심볼 | 카테고리 | Tier | 증거 요약 | 커밋 |
|---|---|---|---|---|---|---|
| 1 | pipeline/{digest,embed,entity_index,glossary,index_latest,model_facts,predict_extract,rank,themes,trending,weekly}.py | 20개 미사용 import (`Path`, `date`, `Any`, `time`, `defaultdict`, `BATCH_POLL_SEC`, `BATCH_TIMEOUT_MIN` 등) | A | 1 | ruff F401 도구 출력 자체가 증거(로컬 스코프) | dd54e15 |
| 2 | pipeline/collect.py:196 | `error = ""` | A | 1 | ruff F841 + 성공 경로가 리터럴 `""`을 직접 씀 확인 | dd54e15 |
| 3 | pipeline/collect_papers.py:248-249 | `new_first_seen`, `new_last_seen` | A | 1 | ruff F841 + 실제 UPDATE가 SQL `MIN/MAX(COALESCE(...))`로 `day`에서 직접 계산, 이 변수들 미참조 확인 | dd54e15 |
| 4 | pipeline/collect_papers.py:245 | `old_seen_count` → `_old_seen_count` (튜플 언패킹 4번째 자리, arity 유지 위해 `_` 접두) | A | 1 | vulture 60% + 전역 검색 0건 | dd54e15 |
| 5 | pipeline/collect_papers.py:245 | `old_first_seen` → `_old_first_seen` | A | 1 | 항목 3 삭제의 연쇄 효과 — vulture 재실행으로 포착, pytest 재검증 | 1108099 |
| 6 | site/src/lib/loadData.ts | `loadCorpusManifest`, `loadSkippedRows`, `loadSourceHealth` (+ `SkippedRow`, `SourceHealthRow`, `CorpusDayCoverage` 타입) | E | 2 | knip + 전역 grep 0건 + `git log -S`로 커밋 `8c921ab`("remove public /research pages") 확인 — 소비처(/research/completeness)가 명시적으로 삭제됨 | 9e4fd4b |
| 7 | site/src/lib/loadData.ts | `loadEntityCooccurrence`, `cooccurrenceGraph` (+ `EntityCooc`, `CooccurrenceEdge`, `CooccurrenceGraph` 타입) | E | 2 | 상동 — /research/network 소비처 삭제(`8c921ab`) | 9e4fd4b |
| 8 | site/src/lib/loadData.ts | `loadEntityMentions`, `entityTimeSeries` (+ `EntityMention`, `TimeSeriesPoint`, `EntityTimeSeries` 타입) | E | 2 | 상동 — /research/evolution 소비처 삭제(`8c921ab`) | 9e4fd4b |
| 9 | site/src/lib/loadData.ts | `loadWeeklyDigest` | E | 2 | 전역 grep 0건 + 모든 weekly 페이지가 `allWeeklyDigests()`만 사용함을 직접 확인(sitemap.xml.ts, weekly/index·all·[week]·[week]/[view].astro, weekly.xml.ts 전부) | 9e4fd4b |
| 10 | site/src/lib/loadData.ts | `clusterIdFromSlug` | E | 2 | 전역 grep 0건 + `story/[cluster].astro`가 `getStaticPaths`의 `props`로 `clusterId`를 직접 전달해 역조회가 애초에 불필요함을 소스로 확인 | 9e4fd4b |
| 11 | site/src/lib/loadPlayers.ts | `findPlayer` | E | 2 | 전역 grep 0건 + `players/[id].astro`가 `PLAYERS.find(...)`로 인라인 구현함을 확인 | 9e4fd4b |

**Knip 오탐 방지 기록** — `weekToDateRange`, `articlesByCluster`, `loadWeekArticles`는 knip이 같은 배치로 "unused export"라 보고했지만, `loadData.ts` 내부에서 각각 3/6/3회 등장(다른 활성 함수가 내부 호출) 확인 후 **삭제하지 않음**. 삭제했다면 `weeklyCoverageMatrix`, `weeklyOutletCategoryMix`, `loadCluster`, `allClusters`, `relatedClusters`, `relatedSemanticClusters`가 깨졌을 것.

## False Positives (되돌린 항목)

없음 — 이번 실행에서 revert된 삭제는 없음. 단, 삭제 전 단계에서 잡아낸 도구 오탐 2건을 기록:
- `weekToDateRange`/`articlesByCluster`/`loadWeekArticles` (knip) — 위 참고. RED-LINE 추가 제안: knip은 "export 미사용"만 보고 "함수 미사용"을 보장하지 않음 — export 제거 후보는 항상 정의 파일 내부 재참조 카운트를 별도 확인할 것.
- `REFS_COVERAGE_START` (vulture, `pipeline/arxiv_refs.py:46`) — `pipeline/research/weekly_brief.py`에서 import해 사용 중(스코프 제외 디렉토리). RED-LINE 추가 제안: `pipeline/research/**`를 vulture 스캔에서 제외하면 그 경계를 넘는 참조가 오탐으로 뜬다 — Tier 2 증거 수집 시 grep은 항상 저장소 전체(스코프 제외 디렉토리 포함)로 수행할 것. (이번 실행은 실제로 매번 저장소 전체 grep을 사용해 이 함정을 피함.)

## Deferred (Tier 3 — 사람 판단 필요)

| # | 심볼 | 카테고리 | 삭제 시 리스크 | 확인 필요 대상 |
|---|---|---|---|---|
| 1 | `pipeline/aggregates_manifest.py:106 file_schema_version` | E | vulture 60%, 전역 grep 결과 자기 모듈 docstring 외 참조 0건. 도입 커밋(`f21bf79`, schema_version sidecar meta)은 의도적 기능 도입으로 보임 — 향후 소비처가 예정돼 있을 수 있음 | 코드 작성자(사용자) 확인, 또는 test_aggregates_manifest.py 추가 후 재실행 |
| 2 | `pipeline/extract.py:71 extract_body` | E | 전역 grep 0건, 최초 커밋(init scaffold)부터 존재. 같은 파일의 `extract_article`/`_extract_og_image`는 활발히 쓰임 — 리팩토링 잔재로 추정 | 사용자 확인 |
| 3 | `pipeline/quarterly_report.py:68 current_quarter` | E | 전역 grep 0건, 파일 자체 내에서도 미호출(CLI 진입점은 다른 방식으로 분기 중인 것으로 추정) | 사용자 확인 |
| 4 | `site/src/lib/loadData.ts` — `Glossary`, `ModelRow`, `SimilarNeighbor`, `CoverageCluster` 타입 | E (type) | knip이 "미사용 export"로 보고하지만 각각 `loadGlossary()`, `loadModelsIndex()`, `loadSimilarity()`/`relatedSemanticClusters()`, `weeklyCoverageMatrix()`의 리턴 타입으로 구조적으로 살아있음(호출자가 타입 추론만 사용해 이름을 직접 import 안 할 뿐) — 삭제해도 컴파일은 되지만 문서 가치 손실, "죽은 코드"로 보기 어려움 | 낮은 우선순위, 원한다면 export 제거만 검토(삭제 아님) |
| 5 | `site/src/lib/loadData.ts` — `_idsBySlug` (private 모듈 변수) | D (연쇄 후보) | 이번 실행에서 유일한 reader(`clusterIdFromSlug`)를 삭제한 결과 이제 write-only가 됨. `clusterSlug()` 내부에서 계속 `.set()`됨. 다음 라운드에서 D 카테고리로 처리 가능(무테스트 영역에서도 D는 Tier 2 허용) | 다음 실행에서 처리 권장 — 이번엔 "한 커밋 = 한 카테고리" 원칙과 스코프 크립 방지를 위해 보류 |
| 6 | `scripts/check-papers-db.mjs`, `scripts/overview-counts.mjs` | F | 워크플로/`package.json`/문서 어디에도 참조 없음(verify-populated·verify-realdata와 달리). 다만 standalone node 스크립트는 사용자가 수동으로 직접 실행할 가능성을 정적 분석으로 배제 불가 | 사용자에게 "지금도 수동으로 쓰는지" 확인 필요 |
| 7 | `pipeline/research/**` (전체, 30+ 파일) | 전체 미적용 | CLAUDE.md가 명시한 민감 영역(leak gate, private 데이터) — 이번 실행에서 완전히 제외 | 필요 시 `concept-research-methodology` 스킬을 먼저 로드하고 별도 실행 권장 |

## Tool Runs

| 도구 | 버전 | 커맨드 | 잔여 |
|---|---|---|---|
| ruff | 0.16.5 | `ruff check --select F401,F811,F841 pipeline --exclude pipeline/research` | 0 |
| vulture | 2.16 | `vulture pipeline --exclude pipeline/research` (default confidence 60) | 4 (전부 Deferred/오탐, 위 표 참고) |
| knip | 6.33.0 | `npx knip --reporter compact` (site/) | unused exports 3 + types 4 (전부 오탐/Deferred, 위 표 참고) |
| pytest | 9.0.3 | `python -m pytest tests/ -q` | 115 passed (불변) |
| astro build | 4.16.19 | `npm run build` | 1605 pages (불변) |

ruff·vulture는 이번 실행을 위해 `pip install ruff vulture`로 임시 설치(pyproject.toml에는 추가하지 않음). ESLint 및 `@astrojs/check`는 새 devDependency 설치가 필요해 이번 실행 도구셋에서 제외(P6 — 동작 보존 외 변경 금지).

## Reproduce

```
git checkout chore/dead-code-20260831
python -m pytest tests/ -q
cd site && npm run build
```

## 방법론 노트 — 원 설계도 대비 편차

1. **`/ralph-loop` 미사용**: 이 환경의 `/ralph-loop`는 컨텍스트를 리셋하지 않고 같은 세션 안에서 stop-hook으로 프롬프트를 재주입하는 방식이라(설계도가 상정한 "반복마다 컨텍스트 초기화"와 다름), 컨텍스트가 이미 유지되는 이 세션에서 Phase 0~4를 직접 순서대로 수행하는 편이 3개 큐 항목 규모에는 더 적합하다고 판단. STATE.md/BASELINE.md/findings 파일은 그대로 남겨 재현·재개 가능하게 함.
2. **Tier 정책의 근거 있는 예외**: `NO_TEST_POLICY`는 무테스트 영역의 E 카테고리를 기본 Tier 3(보고만)로 두지만, TS의 9개 심볼은 "소비 페이지가 명시적 커밋(`8c921ab`)으로 삭제됨"이라는 3번 증거가 매우 강해 Tier 2로 처리. 반대로 Python의 3개(file_schema_version/extract_body/current_quarter)는 "참조 없음"이라는 근거만 있고 "왜 없어졌는지" 서사가 없어 정책대로 Tier 3 유지. 이 비대칭은 의도적 — "확신 없으면 보수적으로"라는 원칙(P1)에 따른 것.

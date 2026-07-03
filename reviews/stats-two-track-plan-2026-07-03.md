# /stats 투트랙(기사+논문) 통계 아키텍처 — 설계 문서

- 작성: 2026-07-03 (플랜모드 세션 — 코드 무변경, 이 문서가 유일 산출물)
- 배경: 사용자 결정 "통계는 논문만이 아니라 기사(뉴스) 자료도 별도 트랙으로 추출·관리한다".
  구현은 이 설계의 승인 후 다음 세션에서 진행한다.
- 실측 기준: 29일치 · 기사 1,904건 (2026-06-01 ~ 2026-07-02).

---

## §1 현황 커버리지 맵 (실측)

| 영역 | 실물 근거 | 기사 관련 수치 |
|---|---|---|
| `pipeline/research/export_public_stats.py` | `papers_block()`·`concepts_block()`뿐 — 기사 블록 없음 | `refs_pipe_7d`(기사→논문 인용 후보 커버리지)만 간접 |
| `data/research_stats.json` | schema v1: papers/concepts 2블록 | `concepts.mentions.news`, `per_day_mentions[].news` — "개념 렌즈를 통과한 뉴스"뿐 |
| `site/src/pages/stats.astro` | 섹션 2개(논문 수집 현황 / 연구 코퍼스 집계) | 기사 코퍼스 자체 통계 0 |
| `site/src/lib/loadData.ts` (`ResearchStats`) | 타입 동일 구조 | 〃 |

**판정: 기사 자체의 코퍼스 통계 트랙 부재** — 볼륨·소스·카테고리·스토리·중요도·커버리지 전부 없음. 검토자 사전 판정과 일치.

## §2 트랙 정의와 지표 카탈로그 (v1 상한: 트랙당 ≤8)

### [A 기사 트랙] — 소스: `data/<day>/articles.json`(29일·1,904건), `data/aggregates/source_health.jsonl`

| # | 지표 | 정의/집계 | 소스 필드 | 공개 안전성 |
|---|---|---|---|---|
| A1 | 볼륨 | 총 기사 수 · 일별 시계열(Sparkline) | day 파일 길이 | 안전(공개 원천 재집계) |
| A2 | 소스 | 활성 소스 수 · 상위 5 점유율 표 | source_id | 안전 |
| A3 | 카테고리 분포 | 6분류 비율 표 | category | 안전 |
| A4 | 스토리(클러스터) | 총 스토리 수 · 멀티소스 스토리 비율 · 평균 클러스터 크기 | cluster_id, cluster_size, also_covered_by | 안전 |
| A5 | 중요도 분포 | importance 1~5 히스토그램 표 | importance_score | 안전 |
| A6 | 커버리지 | 수집 기간 · 요일별 평균 볼륨(주말 패턴) | day 키 | 안전 |

- **필드 가용성(실측)**: `tags`/`subtitle_en`은 초기 일부 날짜(2026-06-04~06 등)에 부분 결여 → **v1 지표는 이 두 필드를 쓰지 않는다**(안전). 나머지 10개 필드(id, source_id, category, importance_score, cluster_id, cluster_size, also_covered_by, published, fetched_at, insights_ko)는 전 기간 균일.
- v2 후보 (2026-07-03 확정 순위, Q4): ① 다중 시리즈 오버레이 차트 ② TS/py 대조 하니스 상설화 ③ 소스 다양성 지수(HHI) ④ 태그 분포 추이(2026-06-07 이후 구간 한정 — 초기 날짜 tags 결여).

### [P 논문 트랙] — 현행 papers 블록 **변경 최소** (수집/보강/멘션 kind/일별 시계열/분류/refs_pipe 유지)

### [C 교차 트랙] — 현행 concepts 블록을 "두 코퍼스를 잇는 렌즈"로 재규정 (news/paper 멘션 분리 · kind 분포 유지). **refs(기사→논문 인용)의 소속은 C 권고** — 행위 주체는 기사지만 의미는 코퍼스 연결이다 (§10-①).

## §3 ★핵심 결정 — 기사 트랙 계산 위치: **권고 = 옵션 A (빌드 타임)**

| | A 빌드 타임 (loadData 헬퍼) | B 로컬 익스포터 통합 | C CI 별도 스텝 |
|---|---|---|---|
| 신선도 | CI 배포마다(일 2회+) ✅ | 로컬 20:00 의존 ❌ | CI 일 2회 |
| 로컬 머신 의존 | 없음 ✅ | 있음 ❌ | 없음 |
| 중간 파일/push 경합 | 0 ✅ | research_stats 확장 | 파일+생성 주체 2개 ❌ |
| 사니타이즈 | 무관(공개 필드만) ✅ | 가드 범위 확장 필요 | 무관 |
| 스냅샷 이력 | 없음 → **불필요** (근거②) | 가능 | 가능 |
| 로직 위치 | 사이트 코드 | 파이프라인 | 파이프라인 |

**근거**:
1. **데이터 거주지가 계산 위치를 정한다** — 기사 데이터는 git-tracked 공개라 CI 빌드가 직접 접근 가능. 공개 데이터를 사설 야간 배치로 우회시키는 B는 신선도의 구조적 손해에 비대칭까지 유발한다.
2. **이력 보존 분기점의 해소** — 기사 통계의 원천(일별 articles.json)이 git에 영구 보존되므로 어떤 과거 시점의 통계든 언제나 재계산 가능하다. 연구용 이력·기술통계는 §8의 사설 export가 담당한다.
3. **빌드 비용 ~0 (실측)** — `site/src/pages/source/[id].astro`의 getStaticPaths가 이미 `allDays()×loadDay()`로 전 코퍼스를 순회하며 캐시를 공유한다. 추가 순회 비용은 실질 0.

**로직 이중화 관리**: v1은 TS(빌드)와 py(§8 사설 export)의 이중 구현을 허용하되, 이 문서 §2의 지표 정의 표를 단일 진실원으로 삼는다 — 정의 변경 시 문서를 먼저 고치고 양쪽 구현이 따라간다. 두 출력의 수치 대조 하니스는 v2 후보로 등재.

## §4 스키마

- 옵션 A 채택 시 **`research_stats.json`은 v1 그대로** (papers/concepts) — 기사 블록은 파일에 넣지 않는다(빌드가 직접 계산). 스키마 변경 0, 하위호환 자동.
- 신규 TS 타입 `ArticleStats` + `computeArticleStats()` (loadData.ts) — 반환 형태:
  `{ total, per_day[], sources{active, top5[]}, categories[], clusters{total, multi_pct, avg_size}, importance_hist, weekday_avg[], span{first, last, days} }`
- **generated_at 분리 표기**: 기사 트랙 = 빌드 시각(각 배포마다 갱신), 논문·교차 트랙 = `research_stats.generated_at`(로컬 20:00) — 페이지에 트랙별 기준 시각을 병기해 신선도 차이를 정직하게 드러낸다.

## §5 /stats 페이지 IA

섹션 순서: **① 기사 코퍼스 → ② 논문 수집 현황(현행) → ③ 연구 코퍼스 집계(현행, "교차 렌즈" 부제)**.

- ①: 스탯 카드 4(총 기사/일평균/활성 소스/스토리) → 일별 볼륨 Sparkline → 카테고리·소스 상위·중요도 3표 → 요일 패턴 표
- 트랙별 신선도 캡션: "기사: 매 배포 시 재계산 · 논문/연구: 야간 집계 기준 <generated_at>"
- 기존 정직성 장치 유지(보강 진행 캡션 · 개념 사전 비공개 고지) · 디자인 토큰·Sparkline 재사용
- 다중 시리즈 차트는 v2 후보 (기존 구현 검토 문서의 §7 한계 서술 존중)

## §6 경계·사니타이즈 영향

- 기사 트랙은 설계상 렉시콘 무접촉(공개 필드만 사용) → 사니타이즈 하드 가드 대상 아님. 옵션 A라 export payload 변화도 없으므로 가드 범위 변경 불요.
- `research/README.md` 경계 계약 추가 조항 초안:
  > 기사 코퍼스 통계(볼륨·소스·카테고리·스토리·중요도·커버리지)는 git-tracked 공개 원천(articles.json)의 자명한 재집계로서 공개 허용이며, 빌드 타임에 계산된다. 연구 방법론(개념 사전·원장)과 무접촉이므로 사니타이즈 가드의 대상이 아니다.

## §7 하니스·검증 계획 (E4/AUD-019 목록에 병합 — 신규 하니스 신설 금지)

- `scripts/verify-realdata.mjs` 확장: /stats에 ①기사 섹션 렌더(카드·표 마커) ②논문 섹션 유지 ③트랙별 신선도 캡션 존재 assertion
- 수치 대조 스팟체크(구현 세션의 검증 기준): 빌드 HTML의 총 기사 수·일평균을 articles.json 재계산과 대조
- (v2 후보) TS·py 이중 구현 수치 대조 하니스

## §8 논문(연구) 연계 — 사설 corpus_descriptives

- RDB exports(`pipeline/research/export_dataset.py`)에 `corpus_descriptives` 산출물 추가: 기사·논문 코퍼스의 기술 통계(기간·N·소스 분포·분류 분포·클러스터 통계) — 논문 methodology 절 재료. `DATASET.md`에 표 사전 추가.
- 공개 /stats(§2-A 지표)와 사설 export는 **같은 §2 정의 표의 두 소비처**다. 정의 변경 절차: 이 문서의 표 갱신 → 양쪽 구현 반영 → 하니스 대조(v2).

## §9 구현 태스크 분해 (다음 세션)

| T | 내용 | 표면 | 게이트 | 완료 조건 |
|---|---|---|---|---|
| T1 | loadData.ts: `ArticleStats` 타입+`computeArticleStats()` (§2-A 정의 그대로, tags 미사용) | 공개 site | G1(push 시) | 헬퍼 수치가 원천 재계산과 일치 |
| T2 | stats.astro 3트랙 IA 개편(§5) + 신선도 캡션 | 공개 site | G1 | 빌드 렌더 + 스팟체크 3건 |
| T3 | README 경계 1문단(§6) | 문서 | G2 | 반영 |
| T4 | verify-realdata 확장(§7) — E4 목록과 한 커밋 | scripts | G2 | 고의 FAIL 테스트 포함 PASS |
| T5 | 사설 corpus_descriptives export + DATASET.md(§8) | 사설 | G2 | 2회 실행 바이트 동일 |

순서: T1→T2(한 커밋 가능) → T3 → T5 → T4 · push는 G1 일괄.

## §10 미결정 질문 (사용자 판단 필요)

1. **refs(기사→논문 인용)의 트랙 소속** — 권고 C(교차)이나 A(기사 행위)로 볼 수도 있음. 페이지에서 어느 섹션에 둘지.
2. **기사 통계 스냅샷 이력 별도 보존 필요 여부** — 권고 "불요"(원천이 git에 영구 보존, 언제든 재계산 가능). 연구상 "그날 페이지에 표시된 값" 자체가 필요하면 B/C 재고.
3. **소스 상위 점유 표에 소스명 노출** — 공개 데이터라 안전하지만 편집 판단(특정 매체 순위 공개가 부담이면 "Source A…" 익명화).
4. **v2 후보 우선순위** — 다중 시리즈 차트 vs 소스 다양성 지수 vs 태그 추이.

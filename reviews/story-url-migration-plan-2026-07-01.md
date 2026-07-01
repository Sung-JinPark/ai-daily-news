# Story URL 안정 슬러그 마이그레이션 플랜 (설계만)

**Date**: 2026-07-01
**Status**: **DESIGN. No code change yet.**
**Trigger for implementation**: sitemap 인덱스 커버리지 확인 후 별도 세션 (N2-impl).

## 왜 지금 (요약)

- `/story/[cluster]` URL은 현재 `cluster_id` (`k000001…k001113`) 기반
- `cluster_id`는 `data/cluster_continuity.json`의 `next_id` 순차 카운터 (`dedupe.py:226-234`)
- **continuity 파일이 삭제/손상되면 전체 재계산 → 모든 클러스터가 새 ID 배정 → 색인된 URL 대량 404**
- 지금 아카이브에 스토리 페이지 111개 · 6개월 뒤 수천 개 예상. **작을 때 이관 비용이 훨씬 저렴**

## 안정 키 후보 비교

### 후보 A — 최초 기사의 `url_hash` (권장)

**정의**: 클러스터에 처음 편입된 기사의 `url_hash` (SHA-256 앞 16자, 콘텐츠 기반)를 안정 슬러그로 사용. `dedupe.py`가 신규 클러스터 생성 시 continuity 엔트리에 `first_url_hash` 필드로 기록.

**불변성 근거**:
- `pipeline/state.py:12` `url_hash = SHA256(url)[:16]` — URL 문자열이 같으면 항상 같은 해시
- URL 자체는 원본 사이트가 발행한 canonical URL → 재클러스터링해도 불변
- continuity 파일이 삭제되어도, 어제 대표였던 기사가 오늘도 데이터에 남아있다면 (아카이브 파일에는 여전히 존재) 그 기사의 URL은 동일 → url_hash 동일

**리스크**:
- 원본 사이트가 URL을 바꾸면? — 이는 원 기사 자체가 이관된 경우로, RSS로 새 URL을 받으면 새 클러스터로 잡힘 → 기존 안정 슬러그와 관계 끊김. 하지만 이는 URL 변경 이벤트로, 지금 해결 대상이 아님 (기존 링크 유지가 목적)
- 초기 클러스터 대표가 삭제되어 아카이브에서 사라지면? — 아카이브는 append-only라 삭제되지 않음. 이 시나리오 없음

### 후보 B — 정규화된 최초 제목의 해시

**정의**: 최초 기사 title을 SimHash 정규화 규칙(`dedupe.py:43-47` `normalize`)으로 처리한 뒤 그 결과의 SHA-256 앞자리.

**단점**:
- 클러스터 재계산 시 대표가 바뀔 수 있음 (trust map + published 정렬 → 다른 기사가 rep이 될 수 있음)
- title 정규화가 미묘하게 다르면 다른 슬러그 → 불변성 파괴
- **후보 A 대비 우위 없음**

### 후보 C — SimHash 값 자체를 인코딩

**정의**: `rep_sh.value`를 base32 등으로 인코딩.

**단점**:
- SimHash는 대표 기사가 바뀌면 값도 바뀜 (rep의 title에서 계산)
- 결정성 낮음. 기각.

### 결정: **후보 A 채택**

- 이유: URL은 원 사이트 결정이라 우리 파이프라인이 못 바꿔 = 불변성이 pipeline external
- 구현 비용: continuity 엔트리에 `first_url_hash` 1개 필드 추가
- 안정 슬러그 형식: `s-<url_hash>` (`k` prefix와 구별)

## 정적 리다이렉트 전략

### 제약: GitHub Pages는 서버 301 불가

옵션:

**옵션 1 — 각 옛 `/story/[cluster_id]/` 경로에 클라이언트 리다이렉트 스텁 페이지 emit** (권장)

빌드 시 각 옛 cluster_id에 대해:
```html
<!doctype html>
<html>
<head>
  <link rel="canonical" href="{절대 새 URL}"/>
  <meta http-equiv="refresh" content="0; url={상대 새 URL}"/>
  <title>Redirecting…</title>
  <script>location.replace("{상대 새 URL}");</script>
</head>
<body>
  <p>이 페이지가 이동했습니다. <a href="{상대 새 URL}">여기로 이동</a>합니다.</p>
</body>
</html>
```

**작동 원리**:
- 크롤러: `<link rel="canonical">` + meta refresh → 검색엔진이 새 URL을 canonical로 인식
- 브라우저: JS `location.replace` (즉시) + meta refresh (fallback)
- 스크린리더: `<p>` 텍스트 fallback

**cluster_id → 안정 슬러그 매핑 정보 필요**: 빌드 시 `articlesByCluster` + continuity 파일에서 각 cluster_id의 `first_url_hash`를 알 수 있음 → 스텁 페이지 emit 시 사용.

**옵션 2 — Astro의 `getStaticPaths`에서 두 경로 모두 emit 후 옛 경로는 새 경로로 리다이렉트하는 wrapper 렌더**

동일 효과, 코드 위치만 다름. 옵션 1과 실질 동일.

### 결정: **옵션 1**

`/story/[cluster].astro`에 옛 슬러그(`k…`) 판별 로직 추가, 이 경우 리다이렉트 스텁 렌더. 새 슬러그(`s-…`)는 실제 페이지 렌더. `getStaticPaths`가 두 종류 모두 emit.

## sitemap · StoryLink · 하위 호환

### sitemap.xml.ts

**현재**: `/story/{cluster_id}` 형식으로 emit (~111 URL)

**이관 후**:
- 새 URL만 `<url>`로 emit (canonical 명시)
- 옛 URL은 sitemap에서 제거 (검색엔진이 자연 discovery로 canonical 인식)
- 새로 감지되는 크롤러 트래픽은 새 URL로 유입

### StoryLink 컴포넌트

- 새 슬러그로만 링크
- 캐시된 브라우저에서 옛 URL을 방문하면 리다이렉트 스텁이 잡아줌

### 옛 스텁 제거 시점

- **최소 6개월** — 검색엔진이 canonical 학습 및 색인 이관 완료
- 2027-01 시점 재검토

## 구현 체크리스트 (별 세션 N2-impl)

- [ ] `pipeline/dedupe.py`: continuity 신규 엔트리에 `first_url_hash` 추가 (기존 엔트리는 `last_titles`처럼 lazy 마이그레이션 — 다음 매치 시 채움)
- [ ] `pipeline/dedupe.py`: 기존 continuity 엔트리 backfill 스크립트 (일회성 `pipeline/backfill_first_url_hash.py`)
- [ ] `site/src/lib/loadData.ts`: `ClusterSummary`에 `first_url_hash` 필드 추가, `loadCluster`가 continuity 파일 읽어서 매핑
- [ ] `site/src/pages/story/[cluster].astro`: `getStaticPaths`가 안정 슬러그와 옛 cluster_id 두 종류 모두 emit
- [ ] `site/src/pages/story/[cluster].astro`: 옛 슬러그일 때 리다이렉트 스텁 렌더 (canonical + meta refresh + JS + fallback text)
- [ ] `site/src/pages/sitemap.xml.ts`: 새 URL만 emit
- [ ] `site/src/components/StoryLink.astro`: 새 슬러그 사용
- [ ] `site/src/lib/loadData.ts`: 관련 헬퍼들이 안정 슬러그와 legacy id 둘 다 lookup 지원
- [ ] 배포 후 Google Search Console에서 색인 이관 모니터링
- [ ] 6개월 후 스텁 제거 (2027-01)

## 리스크 · 롤백

### 리스크

- **canonical 미인식**: 일부 검색엔진이 meta refresh를 취약한 신호로 볼 수 있음 → JS `location.replace` + text fallback으로 완화
- **일부 옛 URL의 백링크 유실**: 6개월 스텁 유지 기간 동안 카노니컬 시그널 전달로 완화
- **이관 시점의 캐시된 사이트맵**: Search Console에서 old sitemap "삭제" 요청

### 롤백

- 스텁 페이지 emit 조건을 false로 바꾸면 옛 URL이 사라지고 새 URL만 남음
- 새 → 옛 URL로 되돌리기 어려움 (한번 canonical로 넘어간 시그널은 되돌리기 힘듦)
- → 이관 실행 전 스테이징에서 최소 1주 dry-run 후 실행

## 결정 요약

| 항목 | 결정 |
|---|---|
| 안정 키 | 후보 A: 최초 기사 `url_hash` |
| 슬러그 형식 | `s-<16-char-url-hash>` |
| 옛 URL 처리 | 리다이렉트 스텁 페이지 (canonical + meta refresh + JS + text) |
| 스텁 유지 기간 | 최소 6개월 (2027-01 재검토) |
| 구현 트리거 | 별 세션 N2-impl (SEO 리다이렉트 계획과 함께) |
| 지금 세션 산출 | 이 문서만 |

## 다음 재검토

- **2026-07-14** (P1a 재감사) 시점에 continuity 엔트리 수 재확인. 아직 <200개면 이관 최적 시점.
- **2026-08-30** (아카이브 90일 시점) 이후 URL 개수 급증하면 이관 비용 증가. 그 전에 실행 권장.

---

**한 줄**: URL 안정성 위해 `cluster_id` → 최초 기사 `url_hash` 기반 슬러그로 마이그레이션. 구현은 별 세션. GitHub Pages 제약으로 리다이렉트는 클라이언트 스텁으로. 6개월 스텁 유지 후 제거.

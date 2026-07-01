# P1b decision — no additional cross-day guard yet

**Date**: 2026-07-01
**Trigger**: P1a audit (`reviews/cluster-merge-audit-2026-07-01.md`)
**Verdict**: **No pipeline change. R1 guard remains in place.**

## What the audit showed

27일치 아카이브에서:

- `day_span >= 30` 클러스터: **0개** (아카이브가 짧기 때문에 정의상 불가능)
- 최장 클러스터: **span 3일 · 멤버 3개** (`k000305`, `k000309`)
- 크로스데이 클러스터(span > 1) 총합: **34개** (전체 클러스터의 약 1.9%)
- R1 이후 `last_titles`로 갱신된 continuity 엔트리: **165개**

## Why P1b hardening is deferred

프롬프트 세트의 P1b 실행 조건은 "P1a 리포트에서 오병합이 확인된 경우"였음. 오늘 데이터에서는:

1. **관측 자체가 불가능** — day_span >= 30 이라는 목표 범위의 클러스터가 존재하지 않음. 임계값/게이트 강화 후 효과 비교를 할 baseline이 없음.
2. R1의 티어 임계값(near ≤8 · far ≤6 + Jaccard ≥0.4)이 이미 배포되어 있고, R1 이후 165개 엔트리가 `last_titles` 축적을 시작. Far-gap 매칭 자체가 아직 트리거되지 않았을 가능성 큼.
3. 지금 P1b를 실행해서 로직을 더 조이면 나중에 실측 없이 튜닝하는 셈. 리뷰의 원 정신(스케일 커질 때 물릴 지점을 잠가두자)에 맞으려면 **관측 → 조정** 순서를 지켜야 함.

## When to reopen

P1a 감사 스크립트를 다음 시점에 재실행:

| 시점 | 예상 상태 | 판단 |
|---|---|---|
| 2026-07-14 (아카이브 ~45일) | 첫 크로스데이 클러스터(span > 14일) 관측 가능 | 상위 5개 육안 검수. 오병합 없으면 유지, 있으면 P1b (a) 발행 페널티 또는 (b) Jaccard 하한 조정. |
| 2026-08-30 (아카이브 ~90일) | R1 far-gap 게이트가 처음으로 대량 트리거되는 시점 | 30일 초과 span 클러스터 상위 15개 전수 검수. 여기서 오병합률이 5% 이상이면 P1b 필수. |

## cluster_id 안정성 각주 (프롬프트 P1b 후반부)

프롬프트가 언급한 "cluster_id가 재계산 시 재배정되면 /story URL이 깨진다"는 우려에 대한 현재 상태:

- `data/cluster_continuity.json`이 authoritative state — 커밋되어 있음
- `pipeline/dedupe.py` 모듈 코멘트(R1)에 "삭제 금지" 명시
- cluster_id 자체는 순차 카운터(`next_id`)로 발급되므로 continuity 파일 삭제 시 전체 재계산 → 다른 ID로 배정됨 → SEO 자산 유실 가능
- **완화책 (미구현, 후속 후보)**: `/story/[cluster]` URL을 cluster_id 대신 클러스터 최초 기사의 url_hash로 매핑. url_hash는 콘텐츠 기반이라 재계산 시에도 동일. 이번 세션에서는 URL 스키마 미변경 (스키마 마이그레이션은 SEO 리다이렉트 계획 포함해야 하므로 별도).

## 결론 요약 (한 줄)

R1 게이트는 배포됨. 오늘 데이터에 조정 근거 없음. P1a 스크립트를 2026-07-14 · 2026-08-30에 재실행해서 재판단.

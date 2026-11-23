# Cross-day cluster merge audit — 2026-11-23

R1 (커밋 `00cf89a`)로 크로스데이 병합에 티어 임계값 + 제목 Jaccard 게이트가 이미 붙어 있는 상태를 대상으로, 현재 90일 continuity 설정 아래에서 실제 오병합이 얼마나 감지되는지 데이터로 확인합니다.

## 스캔 요약

- 대상 일수: **174일**
- 기사 총계: **12,869건**
- 관측된 cluster_id 수: **12,692개**
- day_span >= 30일 클러스터: **5개**

## day_span 분포

| 구간 | 클러스터 수 |
|---|---|
| 1일 | 12609 |
| 2~3일 | 53 |
| 4~7일 | 12 |
| 8~14일 | 7 |
| 15~30일 | 7 |
| 31~60일 | 1 |
| 61~90일 | 3 |
| 90일+ | 0 |

## 연속성 인덱스 상태

- `data/cluster_continuity.json` 엔트리: **6,571개**
- `last_titles` 필드 있는 엔트리 (R1 이후 갱신됨): **6,571개**
- 각 엔트리의 `last_seen`부터 오늘까지 경과 일수 분포:
    - 61~90일: 2295개
    - 1일: 223개
    - 90일+: 103개
    - 31~60일: 2123개
    - 8~14일: 546개
    - 4~7일: 469개
    - 15~30일: 771개
    - 2~3일: 41개

## day_span >= 30일 상위 15개 (오병합 후보)

### `k005778` — span 70일 · 멤버 2 · 매체 1 · 카테고리 {'product': 2}
- 기간: 2026-09-02 → 2026-11-10
- 소스별 카운트: aws_ml_blog×2
- 대표 제목 샘플:
    - Introducing Claude Fable 5.1 on AWS
    - Introducing Claude Fable 5.2 on AWS

### `k003631` — span 61일 · 멤버 3 · 매체 1 · 카테고리 {'community': 3}
- 기간: 2026-08-04 → 2026-10-03
- 소스별 카운트: arstechnica×3
- 대표 제목 샘플:
    - Research roundup: 6 cool science stories we almost missed
    - Research roundup: 7 cool science stories we almost missed
    - Research roundup: 6 cool science stories we almost missed

### `k006726` — span 61일 · 멤버 3 · 매체 1 · 카테고리 {'business': 2, 'community': 1}
- 기간: 2026-09-16 → 2026-11-15
- 소스별 카운트: techcrunch_ai×3
- 대표 제목 샘플:
    - The AI graveyard: a running list of projects and startups that didn’t make it
    - The AI graveyard: a running list of projects and startups that didn’t make it
    - The AI graveyard: a running list of projects and startups that didn’t make it

### `k008549` — span 44일 · 멤버 3 · 매체 1 · 카테고리 {'product': 3}
- 기간: 2026-10-10 → 2026-11-22
- 소스별 카운트: techcrunch_ai×3
- 대표 제목 샘플:
    - Here are the top AI agents that can live in your text messages
    - Here are the top AI agents that can live in your text messages
    - Here are the top AI agents that can live in your text messages

### `k001215` — span 30일 · 멤버 2 · 매체 1 · 카테고리 {'product': 2}
- 기간: 2026-07-02 → 2026-07-31
- 소스별 카운트: aws_ml_blog×2
- 대표 제목 샘플:
    - Debugging production agents with Amazon Bedrock AgentCore Observability
    - Optimizing production agents with Amazon Bedrock AgentCore Observability

## 멤버 수 상위 15개 클러스터

| cluster_id | 멤버 | day_span | 매체 | 카테고리 | 최신 제목 |
|---|---|---|---|---|---|
| `k006606` | 4 | 29일 | 1 | community×4 | TechCrunch Disrupt 2026: Day 2 |
| `k000305` | 3 | 3일 | 2 | policy×3 | "Dangerous" AI models are coming no matter what |
| `k000309` | 3 | 3일 | 2 | community×2, business×1 | Sixty percent of US consumers say 'AI' in brand messaging is… |
| `k003631` | 3 | 61일 | 1 | community×3 | Research roundup: 6 cool science stories we almost missed |
| `k006726` | 3 | 61일 | 1 | business×2, community×1 | The AI graveyard: a running list of projects and startups th… |
| `k008549` | 3 | 44일 | 1 | product×3 | Here are the top AI agents that can live in your text messag… |
| `k010670` | 3 | 9일 | 1 | business×2, policy×1 | The unfolding Jensen Huang scandal: a timeline |
| `c0000-a8f410cf` | 2 | 1일 | 1 | model_research×2 | Direct Preference Optimization Beyond Chatbots |
| `c0001-d8c08d82` | 2 | 1일 | 1 | product×2 | Adding MCP Tools to Reachy Mini |
| `c0002-6965f8d8` | 2 | 1일 | 1 | product×2 | Holo3.1: Fast & Local Computer Use Agents |
| `k000294` | 2 | 2일 | 1 | product×1, policy×1 | Pentagon boasts of using AI to write reports mandated by Con… |
| `k000322` | 2 | 2일 | 1 | community×1, model_research×1 | Cockroaches scurry around with thousands of pieces of bacter… |
| `k000323` | 2 | 2일 | 1 | business×1, hardware×1 | Among the large new rockets Amazon was counting on, only Eur… |
| `k000324` | 2 | 2일 | 1 | product×2 | Anthropic "pauses" token-based billing for its Claude Agent … |
| `k000325` | 2 | 2일 | 1 | policy×2 | US approval of Paramount/Warner Bros. deal surprised DOJ law… |

## Same-day 클러스터 카테고리 혼재 밴드 (N7 관측)

- 멤버 2건 이상 same-day 클러스터: **86**
- 카테고리 혼재 클러스터: **1** (1%)

### 혼재 샘플
- `k001253` (멤버 2, {'product': 1, 'community': 1})
    - The short leash AI coding method for beating Fable
    - The short leash AI coding method for beating Fable

_이 밴드는 관측용입니다. HAMMING_THRESHOLD (same-day)는 이번 세션에서 변경하지 않았습니다._

## 병합 이벤트 로그 (N3)

- 총 병합 이벤트: **21,419건**
- 종류별: same_day 2280 · cross_near 19134 · cross_far 5
- cross_far Jaccard 평균: **1.0** (5건)

### Hamming 거리 분포 — same_day

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 2227 |
| 3~4 | 0 |
| 5~6 | 11 |
| 7~8 | 6 |
| 9~10 | 8 |
| 11~12 | 28 |
| 13+ | 0 |

### Hamming 거리 분포 — cross_near

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 19004 |
| 3~4 | 50 |
| 5~6 | 62 |
| 7~8 | 18 |
| 9~10 | 0 |
| 11~12 | 0 |
| 13+ | 0 |

### Hamming 거리 분포 — cross_far

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 4 |
| 3~4 | 0 |
| 5~6 | 1 |
| 7~8 | 0 |
| 9~10 | 0 |
| 11~12 | 0 |
| 13+ | 0 |

## 판단

- `day_span >= 30`인 클러스터가 감지됨. 위 샘플의 제목 리스트를 육안 검수해 서로 다른 사건이 묶여있으면 P1b에서 추가 게이트 도입.

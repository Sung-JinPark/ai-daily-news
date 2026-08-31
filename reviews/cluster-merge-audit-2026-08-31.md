# Cross-day cluster merge audit — 2026-08-31

R1 (커밋 `00cf89a`)로 크로스데이 병합에 티어 임계값 + 제목 Jaccard 게이트가 이미 붙어 있는 상태를 대상으로, 현재 90일 continuity 설정 아래에서 실제 오병합이 얼마나 감지되는지 데이터로 확인합니다.

## 스캔 요약

- 대상 일수: **86일**
- 기사 총계: **6,274건**
- 관측된 cluster_id 수: **6,128개**
- day_span >= 30일 클러스터: **1개**

## day_span 분포

| 구간 | 클러스터 수 |
|---|---|
| 1일 | 6069 |
| 2~3일 | 48 |
| 4~7일 | 8 |
| 8~14일 | 1 |
| 15~30일 | 2 |
| 31~60일 | 0 |
| 61~90일 | 0 |
| 90일+ | 0 |

## 연속성 인덱스 상태

- `data/cluster_continuity.json` 엔트리: **5,582개**
- `last_titles` 필드 있는 엔트리 (R1 이후 갱신됨): **4,683개**
- 각 엔트리의 `last_seen`부터 오늘까지 경과 일수 분포:
    - 61~90일: 1056개
    - 1일: 210개
    - 31~60일: 2236개
    - 4~7일: 447개
    - 15~30일: 1116개
    - 8~14일: 496개
    - 2~3일: 21개

## day_span >= 30일 상위 15개 (오병합 후보)

### `k001215` — span 30일 · 멤버 2 · 매체 1 · 카테고리 {'product': 2}
- 기간: 2026-07-02 → 2026-07-31
- 소스별 카운트: aws_ml_blog×2
- 대표 제목 샘플:
    - Debugging production agents with Amazon Bedrock AgentCore Observability
    - Optimizing production agents with Amazon Bedrock AgentCore Observability

## 멤버 수 상위 15개 클러스터

| cluster_id | 멤버 | day_span | 매체 | 카테고리 | 최신 제목 |
|---|---|---|---|---|---|
| `k000305` | 3 | 3일 | 2 | policy×3 | "Dangerous" AI models are coming no matter what |
| `k000309` | 3 | 3일 | 2 | community×2, business×1 | Sixty percent of US consumers say 'AI' in brand messaging is… |
| `c0000-a8f410cf` | 2 | 1일 | 1 | model_research×2 | Direct Preference Optimization Beyond Chatbots |
| `c0001-d8c08d82` | 2 | 1일 | 1 | product×2 | Adding MCP Tools to Reachy Mini |
| `c0002-6965f8d8` | 2 | 1일 | 1 | product×2 | Holo3.1: Fast & Local Computer Use Agents |
| `k000294` | 2 | 2일 | 1 | product×1, policy×1 | Pentagon boasts of using AI to write reports mandated by Con… |
| `k000322` | 2 | 2일 | 1 | community×1, model_research×1 | Cockroaches scurry around with thousands of pieces of bacter… |
| `k000323` | 2 | 2일 | 1 | business×1, hardware×1 | Among the large new rockets Amazon was counting on, only Eur… |
| `k000324` | 2 | 2일 | 1 | product×2 | Anthropic "pauses" token-based billing for its Claude Agent … |
| `k000325` | 2 | 2일 | 1 | policy×2 | US approval of Paramount/Warner Bros. deal surprised DOJ law… |
| `k000378` | 2 | 2일 | 1 | product×2 | Show HN: I built 184 free browser tools – PDF, image, dev, A… |
| `k000336` | 2 | 2일 | 1 | product×2 | The Slate Truck's price may have leaked, starts at $24,950 |
| `k000008` | 2 | 2일 | 1 | business×2 | Microsoft turns to AWS as GitHub faces AI capacity crunch |
| `k000330` | 2 | 2일 | 1 | product×2 | Second carcass-eating fly species cleared by FDA for maggot … |
| `k000320` | 2 | 2일 | 1 | product×2 | Unlocking UK house-building with AI-accelerated planning |

## Same-day 클러스터 카테고리 혼재 밴드 (N7 관측)

- 멤버 2건 이상 same-day 클러스터: **85**
- 카테고리 혼재 클러스터: **1** (1%)

### 혼재 샘플
- `k001253` (멤버 2, {'product': 1, 'community': 1})
    - The short leash AI coding method for beating Fable
    - The short leash AI coding method for beating Fable

_이 밴드는 관측용입니다. HAMMING_THRESHOLD (same-day)는 이번 세션에서 변경하지 않았습니다._

## 병합 이벤트 로그 (N3)

- 총 병합 이벤트: **10,956건**
- 종류별: same_day 1180 · cross_near 9776 · cross_far 0

### Hamming 거리 분포 — same_day

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 1152 |
| 3~4 | 0 |
| 5~6 | 1 |
| 7~8 | 6 |
| 9~10 | 5 |
| 11~12 | 16 |
| 13+ | 0 |

### Hamming 거리 분포 — cross_near

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 9663 |
| 3~4 | 48 |
| 5~6 | 54 |
| 7~8 | 11 |
| 9~10 | 0 |
| 11~12 | 0 |
| 13+ | 0 |

## 판단

- `day_span >= 30`인 클러스터가 감지됨. 위 샘플의 제목 리스트를 육안 검수해 서로 다른 사건이 묶여있으면 P1b에서 추가 게이트 도입.

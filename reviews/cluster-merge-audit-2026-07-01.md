# Cross-day cluster merge audit — 2026-07-01

R1 (커밋 `00cf89a`)로 크로스데이 병합에 티어 임계값 + 제목 Jaccard 게이트가 이미 붙어 있는 상태를 대상으로, 현재 90일 continuity 설정 아래에서 실제 오병합이 얼마나 감지되는지 데이터로 확인합니다.

## 스캔 요약

- 대상 일수: **27일**
- 기사 총계: **1,786건**
- 관측된 cluster_id 수: **1,747개**
- day_span >= 30일 클러스터: **0개**

## day_span 분포

| 구간 | 클러스터 수 |
|---|---|
| 1일 | 1713 |
| 2~3일 | 34 |
| 4~7일 | 0 |
| 8~14일 | 0 |
| 15~30일 | 0 |
| 31~60일 | 0 |
| 61~90일 | 0 |
| 90일+ | 0 |

## 연속성 인덱스 상태

- `data/cluster_continuity.json` 엔트리: **1,113개**
- `last_titles` 필드 있는 엔트리 (R1 이후 갱신됨): **165개**
- 각 엔트리의 `last_seen`부터 오늘까지 경과 일수 분포:
    - 8~14일: 478개
    - 15~30일: 133개
    - 1일: 165개
    - 4~7일: 210개
    - 2~3일: 127개

## day_span >= 30일 상위 15개 (오병합 후보)

_현재 아카이브에는 해당 범위의 클러스터가 없습니다._

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

## 병합 이벤트 로그 (N3)

- 총 병합 이벤트: **179건**
- 종류별: same_day 14 · cross_near 165 · cross_far 0

### Hamming 거리 분포 — same_day

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 12 |
| 3~4 | 0 |
| 5~6 | 1 |
| 7~8 | 0 |
| 9~10 | 1 |
| 11~12 | 0 |
| 13+ | 0 |

### Hamming 거리 분포 — cross_near

| 구간 | 이벤트 수 |
|---|---|
| 0~2 | 164 |
| 3~4 | 0 |
| 5~6 | 1 |
| 7~8 | 0 |
| 9~10 | 0 |
| 11~12 | 0 |
| 13+ | 0 |

## 판단

- `day_span >= 30`인 크로스데이 클러스터가 **아직 없다** — 아카이브가 짧기 때문 (28~29일). **현 단계에서는 P1b 추가 방어가 불필요**하며 R1의 티어 임계값 + 제목 Jaccard 게이트만으로 충분하다고 판단.
- 재감사 시점: 아카이브가 45일을 넘겼을 때 (2026-07-14 이후), 그리고 90일을 넘겼을 때 (2026-08-30 이후) 이 스크립트를 재실행.

# LLM 통합 월비용 재산정 (2026-07-01 세션 이후)

리뷰(#6)에서 "새 LLM 스텝 4종의 통합 월비용 재산정이 없다"는 지적에 대한 답. 각 스텝별 단가와 월 예상, 예산 대비 마진을 하나의 표로.

## 스텝별 실측 · 예상

| 스텝 | 배치? | 빈도 | 입력 (avg) | 출력 (avg) | 월 호출 | 비용 산정 근거 |
|---|---|---|---|---|---|---|
| `summarize` (기존) | Batch 50% | 매일 | ~4K (with cache) | ~500 | ~60 × 30 = 1,800 | 기존 실측, ~$3/월 |
| `digest` (기존) | 비배치 | 매일 | ~3K | ~800 | 30 | ~$0.5/월 |
| `weekly` (기존) | 비배치 | 주 1회 | ~8K | ~1.5K | 4 | ~$0.5/월 |
| `glossary` (기존) | 비배치 | 주 1회 | ~5K | ~1K | 4 | ~$0.3/월 |
| **`themes` (F12 신규)** | 비배치 | 매일 | ~4K | ~1.5K | 30 | ~$0.7/월 |
| **`predict_extract` Pass A (F13 신규)** | Batch 50% | 매일 | ~2K × 30건 = 60K | ~500 × 30 = 15K | 30 (batches) | ~$0.6/월 |
| **`predict_extract` Pass B (F13 신규)** | 비배치 | 매일 | ~4K + (∼24K 컨텍스트) = 28K | ~2K | 30 (평균 pending 15개/일 → 1 batch) | **~$3.4/월** ← 컨텍스트 무거움 |
| **`model_facts` (F14 신규)** | Batch 50% | 매일 | ~2K × 15건 = 30K | ~400 × 15 = 6K | 30 | ~$0.3/월 |
| **`quarterly_report` (M6 신규)** | 비배치 | 분기 1회 | ~10K | ~2K | 4/년 = 0.33/월 | ~$0.05/월 |

## 통합 월비용 예상

**기존 총합**: ~$4.3/월

**신규 총합**: ~$5.05/월
- themes $0.7
- predictions Pass A $0.6
- **predictions Pass B $3.4** ← 가장 큰 항목
- model_facts $0.3
- quarterly $0.05

**세션 후 총합**: ~$9.3/월

## 예산 마진

Anthropic 크레딧 월 $10~12 가정 시:
- 마진 $0.7~2.7 (10~30%). 남는 여유가 크지 않음.
- **주의 지점**: Pass B가 예상보다 컨텍스트 커지면 (예: pending 60개 → 4 batches → $13/월) 초과 위험.

## 완화 조치 (이미 적용)

- `predict_extract`의 `importance >= 3` 필터로 Pass A 후보를 하루 30건 내외로 제한 (`predict_extract.py:52 IMPORTANCE_MIN`)
- Pass B는 `_pending_needs_resolution`로 호출 대상 축소 (horizon 지남 OR 60일 초과만)
- Batch API가 자동으로 50% 할인 (`summarize`, `predict_extract` Pass A, `model_facts`)
- 프롬프트 캐시 활용 (System prompt cache_control ephemeral)

## 추가 완화 (권장, 미적용)

- Pass B 컨텍스트를 후보 예측의 태그/엔티티와 겹치는 기사로 사전 필터 (전체 30일 덤프 대신 top-K 20건 정도). 예상 절감 60%. → **다음 커밋 후보**.
- Pass B를 Batch API로 마이그레이션 (요청을 pending 예측별로 나누고 배치 제출). 50% 추가 할인 가능하나 구현 복잡도 증가.
- 저볼륨 일(articles < 25) `themes` 스킵.

## 재산정 주기

**분기말**에 실측 (Anthropic 콘솔 stats + Batch API dashboard)과 비교하여 표 업데이트. Pass B의 실 컨텍스트 크기가 예상과 30% 이상 차이나면 완화 조치 우선 적용.

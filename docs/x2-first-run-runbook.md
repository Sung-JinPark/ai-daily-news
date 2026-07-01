# X2 첫 CI 런 이후 검증 런북

세션 종료 시점 (2026-07-01 KST 오후)에 daily.yml 첫 자동 런 (KST 18:00)이 아직 실행되지 않았음. 이 문서는 그 이후에 사람이 클릭 몇 번으로 실행하는 검증 체크리스트.

## 예상 발생 시각

- **2026-07-01 18:00 KST** (= 09:00 UTC) — 오늘 첫 일반 파이프라인
- **2026-07-02 00:00 KST** (= 15:00 UTC 2026-07-01) — 오늘 두 번째 런
- **2026-07-06 03:00 UTC** — 첫 audit-weekly 자동 실행
- **2026-07-01~07 (사이 어느 날)** — 조건부 `quarterly_report --auto` 실행 → `data/reports/2026-Q2.json` 생성

## 첫 런 직후 (KST 20:00 안팎) 체크

### 1. `git pull` 후 실데이터 스모크

```bash
git pull
node scripts/verify-realdata.mjs
```

**기대 결과**:
- `summary: N passed, M skipped` 형태 로그
- `M` (skipped)이 `0`이 되지 않아도 됨 — quarterly는 첫 7일 안 임의 시점에만 생성
- 실패 (exit code 1)가 없어야 함
- 실패 발생 시 stderr에 "marker … not found …" 메시지 → 렌더 스키마 drift 위치

### 2. Pass B (N4) 컨텍스트 실측 확인

Actions 탭 → 최신 `Daily AI News Pipeline` 런 → "Predictions" 스텝 로그에서 다음 라인 검색:
```
resolve batch X/Y: N predictions, M articles in context
```

**기대**:
- `M` (articles in context) ≤ 20
- 예측이 아직 없으면 `resolve: 0 predictions need review` (정상)
- `--resolve-top-k=20` 기본값 반영 확인

### 3. sitemap 이관 확인

```bash
curl -s https://sung-jinpark.github.io/ai-daily-news/sitemap.xml | grep -c '/story/s-'
curl -s https://sung-jinpark.github.io/ai-daily-news/sitemap.xml | grep -c '/story/k'
```

**기대**: `s-` 62개 이상 (백필된 클러스터), `k` 49개 이하 (백필 안 된 것)

### 4. 스토리 리다이렉트 스텁 실동작

옛 URL 브라우저 방문:
```
https://sung-jinpark.github.io/ai-daily-news/story/k000305
```

**기대**: 자동으로 `/story/s-<hash>`로 리다이렉트. DevTools Network 탭에서 문서 응답에 `<meta http-equiv="refresh">` + `<link rel="canonical">` 확인.

## audit-weekly 수동 트리거 (선택)

첫 자동 실행이 2026-07-06까지 4-5일 남았으니 지금 수동 트리거해서 워크플로우 자체 검증:

```
GitHub → Actions → "Weekly cluster-merge audit" → Run workflow
  simulate_anomaly: true  # 이슈 자동 생성 경로 강제 검증
```

**기대**:
- 이슈 신규 생성 (`[audit] cross-day merge 이상 …건 감지`)
- 다시 트리거 시 코멘트 append (중복 방지 동작)

verify 후 이슈 수동 close.

## Anthropic 콘솔 실비용 대조 (7월말)

`reviews/llm-cost-estimate-2026-07-01.md`의 산정 대비:
- summarize ~$2.79 (Batch, cached)
- themes ~$0.34 (non-batch)
- Pass A ~$1.85 (Batch)
- **Pass B N4 후: ~$0.4-0.5** (기존 $1.14 대비 ~60% 감소)
- model_facts ~$0.90 (Batch)
- 총 ~$7.34/월 예상, N4 반영 시 ~$6.9/월

Anthropic 콘솔 dashboard에서 7월 실측 조회 후 오차 30% 이상 항목 재산정.

## 발견 시 대응

| 문제 | 대응 |
|---|---|
| `verify-realdata` 실패 (marker missing) | 로더 필드명 vs 파이프라인 출력 필드명 대조. drift 최소 수정. |
| Pass B articles > 20 | `--resolve-top-k` 플래그가 CI에서 전달되는지 확인. |
| 리다이렉트 스텁 없음 | dist에 배포되었는지 (`site/dist/story/k*/index.html`) 확인. Actions 로그의 "Deploy Pages" 단계. |
| audit-weekly 이슈 안 생성 | `contents:read + issues:write` 권한 확인. `gh` CLI가 secrets.GITHUB_TOKEN을 사용하는지. |

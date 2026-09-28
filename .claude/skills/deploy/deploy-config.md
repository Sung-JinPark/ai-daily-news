# deploy.yml 설정 레퍼런스

## 트리거
```yaml
on:
  push:
    branches: [main]
    paths: ["site/**", "data/**", ".github/workflows/deploy.yml"]
  workflow_dispatch:
```
- **paths 필터가 핵심이다.** `pipeline/**`·`.claude/**`·`CLAUDE.md`·`AGENTS.md`만 바뀐 push는
  배포를 트리거하지 않는다 (의도된 동작 — 사이트 출력이 안 바뀌는데 Pages를 재배포할 이유가 없다).
  그런 변경을 굳이 반영해야 하면 `gh workflow run deploy.yml --ref main`.
- `workflow_dispatch`가 있으므로 수동 실행은 항상 가능하다.

## 권한 · 동시성
```yaml
permissions: { pages: write, id-token: write, contents: read }
concurrency: { group: pages, cancel-in-progress: false }
```
- `group: pages`를 `daily.yml`과 **공유**한다. 파이프라인 런과 배포가 동시에 겹치면 큐잉된다.
  `cancel-in-progress: false`라 진행 중 배포가 잘리지 않는다 — 배포가 "안 도는 것처럼" 보이면
  먼저 `gh run list --workflow=daily.yml`로 파이프라인이 점유 중인지 확인한다.

## 빌드
- Node 20, `working-directory: site`, `npm install --no-audit --no-fund` → `npm run build`.
- 캐시 키는 `site/package.json` 기준. 의존성 문제로 빌드가 깨지면 lockfile 드리프트를 의심한다.
- 산출물 `site/dist` → `upload-pages-artifact` → `deploy-pages`.

## base path (링크 깨짐의 주원인)
`site/astro.config.mjs`가 환경변수로 계산한다:
- `base` = `/${GITHUB_REPOSITORY의 repo명}` — CI에서는 `/ai-daily-news`, **로컬 dev에서는 `/`**.
- `site` = `https://${GITHUB_REPOSITORY_OWNER 소문자}.github.io` (RSS·sitemap 절대 URL용).

→ 컴포넌트에서 경로를 만들 때는 반드시:
```ts
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
```
로컬에서 멀쩡한 링크가 배포 후에만 404라면 거의 항상 base path 하드코딩이다.

## Secrets
- `GOOGLE_SITE_VERIFICATION` → 빌드 시 `PUBLIC_GOOGLE_SITE_VERIFICATION`으로 주입.
- 미설정이어도 빌드는 통과한다(메타 태그만 비게 됨).

## 관련 워크플로
| 파일 | 스케줄 (UTC / KST) | 역할 |
|---|---|---|
| `daily.yml` | `0 15,9 * * *` / KST 00:00·18:00 | 수집→요약→랭킹, data 커밋 후 Pages 배포까지 |
| `deploy.yml` | push(paths) · 수동 | 사이트만 재빌드·배포 |
| `research-nightly.yml` | 11:00 / KST 20:00 | 사설 연구 런 (사니타이즈 stats 1파일만 공개 커밋) |
| `gitleaks.yml` | 모든 push/PR | 시크릿 스캔 |
| `audit-weekly.yml` | 주간 | 소스·클러스터 감사 |

`daily.yml`도 자체적으로 배포까지 하므로, 파이프라인 직후에는 `deploy.yml`을 따로 돌릴 필요가 없다.

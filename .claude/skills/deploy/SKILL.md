---
name: deploy
description: ai-daily-news 사이트를 GitHub Pages에 배포한다. 변경분 커밋·push 후 deploy.yml 실행 상태를 확인하고, paths 필터에 걸려 자동 트리거가 안 되면 수동으로 실행한다. 사용자가 "배포", "deploy", "사이트 반영", "빌드 돌려줘"라고 하거나 사이트/데이터 변경을 마무리할 때 사용.
allowed-tools: Bash, Read, Grep, Glob
---

# 배포

배포 = **main에 push** → `.github/workflows/deploy.yml`이 Astro 빌드 → GitHub Pages 게시.
빌드 2~3분. 실사이트: https://sung-jinpark.github.io/ai-daily-news/

워크플로 설정 세부(트리거·권한·base path·secrets)는 [deploy-config.md](deploy-config.md) 참고.

## 1. 누출 게이트 (커밋 전 항상)
```bash
git ls-files data/research_private/ data/papers_private/ 'data/corpus/*/bodies.jsonl'
```
결과가 하나라도 나오면 **중단하고 사용자에게 보고**한다. 직접 unstage 하지 않는다.
public 저장소라 한 번 push하면 되돌릴 수 없다.

## 2. 커밋 · push
```bash
git add -A
git commit -m "<무엇을 왜 바꿨는지>"
git pull --rebase origin main   # 파이프라인 CI가 data 커밋을 밀어넣어 자주 뒤처진다
git push
```
`git pull --rebase`를 건너뛰면 non-fast-forward로 거절된다 — 스케줄 런(KST 00:00/18:00)과
야간 연구 런(KST 20:00)이 하루 3회 원격에 커밋을 추가하기 때문.

## 3. 자동 트리거 여부 판단 ★
`deploy.yml`은 **paths 필터**가 있다 — `site/**`, `data/**`, `.github/workflows/deploy.yml`.
이 셋에 해당하지 않는 변경(예: `CLAUDE.md`, `.claude/**`, `pipeline/**`, `README.md`)만
push했다면 **배포는 시작되지 않는다.** 그 경우 수동 실행:
```bash
gh workflow run deploy.yml --ref main
```

## 4. 상태 확인
```bash
gh run list --workflow=deploy.yml --limit 3
gh run watch $(gh run list --workflow=deploy.yml --limit 1 --json databaseId -q '.[0].databaseId')
```
실패하면 `gh run view <id> --log-failed`로 원인을 보고, 추측하지 말고 로그를 읽은 뒤 고친다.

## 5. 배포 후 확인
- 사이트가 뜨는지, 링크가 깨지지 않는지 (base path 회귀가 가장 흔한 실패).
- 헤더의 "업데이트" 시각이 의도한 런 시각인지.

## 하지 말 것
- `site/dist/`를 커밋하지 않는다 (gitignored, CI가 빌드한다).
- `gh-pages` 브랜치를 만들거나 수동으로 아티팩트를 올리지 않는다 — Pages는 Actions 소스다.
- 실패한 런을 그냥 재실행(rerun)으로 넘기지 않는다. 원인을 먼저 확인한다.
- 데이터 파이프라인을 배포 목적으로 돌리지 않는다. 배포는 이미 커밋된 `data/`를 빌드할 뿐이다.

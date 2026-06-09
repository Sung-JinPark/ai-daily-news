# AI Daily News — Claude Code 가이드

## 자동 수행 원칙
- 코드 수정 후 **반드시 git add → commit → push 까지 자동으로** 수행한다. 사용자에게 확인 묻지 않는다.
- 파이프라인 실행, 빌드, 배포도 사용자 확인 없이 바로 수행한다.
- git push 후 GitHub Actions 빌드가 자동 시작된다 (2~3분 소요).

## 파이프라인 실행 순서
```
python -m pipeline.collect
python -m pipeline.dedupe
python -m pipeline.summarize
python -m pipeline.rank
python -m pipeline.trending
python -m pipeline.index_latest
```
또는 `run-pipeline.bat` 실행.

## 사이트 로컬 실행
```
start-site.bat
```
→ http://localhost:4321/ (dev에서는 base path 없음)

## 실제 사이트
https://sung-jinpark.github.io/ai-daily-news/

## 배포 절차
```
git add -A
git commit -m "..."
git push
```
push하면 GitHub Actions `.github/workflows/deploy.yml`이 자동 빌드·배포.

## 주요 디렉터리
- `pipeline/` — 수집·요약·랭킹 파이프라인 (Python)
- `site/src/` — Astro 정적 사이트 소스
- `data/YYYY-MM-DD/` — 날짜별 기사 JSON (커밋됨)
- `.github/workflows/` — CI/CD

## 환경 변수
`.env` 파일에 `ANTHROPIC_API_KEY` 필요. `.env.example` 참고.

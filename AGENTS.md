# AI Daily News — 에이전트 공통 가이드

모든 코딩 에이전트(Claude Code · Codex · Cursor · Copilot)가 공유하는 프로젝트 계약.
Claude Code는 `CLAUDE.md`가 이 파일을 `@AGENTS.md`로 임포트하며, Claude 전용 운영 규칙은
`CLAUDE.md`에, 파일 경로별 세부 규칙은 `.claude/rules/*.md`에 있다.

## 프로젝트 개요
- 글로벌 AI 뉴스를 수집·요약(한국어)·랭킹해 정적 대시보드로 배포하는 파이프라인.
- 스택: Python 3.11+ (수집/요약/랭킹) + Astro 정적 사이트 + GitHub Actions + GitHub Pages.
- LLM: Anthropic Batch API (요약), Voyage (임베딩).
- 실제 사이트: https://sung-jinpark.github.io/ai-daily-news/

## 디렉터리 맵
- `pipeline/` — 수집·요약·랭킹 파이프라인 (Python 패키지)
- `pipeline/research/` — 연구 분석 코드 (추적됨, 산출 데이터는 비공개)
- `site/src/` — Astro 정적 사이트 소스
- `data/YYYY-MM-DD/` — 날짜별 기사 JSON (커밋됨)
- `data/*_private/` — gitignored 로컬 전용 (절대 커밋 금지)
- `tests/` — pytest
- `.github/workflows/` — CI/CD
- `.claude/` — 에이전트 설정 (rules · agents · hooks · skills)

## 명령
```bash
# 파이프라인 (순서 고정) — 또는 run-pipeline.bat
python -m pipeline.collect
python -m pipeline.dedupe
python -m pipeline.summarize
python -m pipeline.rank
python -m pipeline.trending
python -m pipeline.index_latest

# 테스트
pytest

# 사이트 로컬 실행 (start-site.bat) → http://localhost:4321/  (dev에는 base path 없음)
cd site && npm run dev

# 배포 — push하면 .github/workflows/deploy.yml이 자동 빌드·배포
git add -A && git commit -m "..." && git push
```

## 환경 변수
`.env`에 `ANTHROPIC_API_KEY` 필요. `.env.example` 참고. `.env`는 커밋 금지.

## 절대 규칙
1. **사설 데이터 비커밋** — `data/research_private/`, `data/papers_private/`,
   `data/corpus/*/bodies.jsonl`(기사 본문 전문)은 절대 커밋하지 않는다.
   커밋 전 `git ls-files data/research_private/ data/papers_private/`가 빈 결과인지 확인.
2. **공개 로그에 개념명·본문 금지** — 연구 파이프라인의 개념-레벨 stdout은 사설 로그로 리다이렉트.
3. **UTF-8 고정** — 한글이 포함된 diff·문서·리뷰 zip은 UTF-8(no-BOM)로만 내보낸다.
   PowerShell `Get-Content -Raw` / `git show > file.txt` 금지 (모지바케). 상세는 `CLAUDE.md`.
4. **비밀 스캔** — 모든 push/PR에서 `.github/workflows/gitleaks.yml`이 자동 실행.
   로컬 사전 차단: `pip install pre-commit && pre-commit install`.
5. **데이터 공개 정책 변경 금지** — `data/embeddings/` 공개 커밋은 AUD-015 결정 사항.
   사용자 결정 없이 바꾸지 않는다.

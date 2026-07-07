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

## 파이프라인 실행 스케줄
- 매일 2회 자동 실행 — 파이프라인 시작: KST 00:00 / 18:00 (= UTC 15:00 / 09:00).
- 첫 번째 런은 자정 직후에 시작 → ~01:00 KST 완료. 헤더의 "업데이트" 시각이 자정을 넘긴 "오늘 새벽"으로 표시되어, 아침 방문자(KST 08:00)가 "어제 저녁" 시각을 보는 일이 없다.
- 두 번째 런은 KST 18:00에 시작 → ~19:00 KST 완료.
- LLM 요약은 Anthropic Batch API (50% 할인). 제출 후 보통 수 분~수십 분 내 완료, 최대 50분까지 폴링.
- 일요일 UTC 09:00 (= KST 18:00 일요일) 실행 시 weekly digest + glossary 갱신도 함께 진행.

## 연구 야간 런 CI (research-nightly.yml)
- 사설 연구 파이프라인(run-research 상당)을 GitHub Actions로 자동화(cron UTC 11:00 = KST 20:00).
  **repo는 PUBLIC 유지** → 안전선 엄격.
- **상태 지속 = GCS**(사설 트리·papers.db). Secrets: `GCS_SA_KEY`(서비스계정 JSON)·`GCS_BUCKET`.
  **GCS 미설정이면 워크플로 no-op**(안전). 상세·셋업 = `data/research_private/notes/CI_MIGRATION.md`.
- **안전선**: 사설 데이터 **비커밋**(GCS에만) · 공개 커밋은 **사니타이즈 `research_stats.json` 1파일만** ·
  개념-레벨 stdout은 **사설 로그로 리다이렉트**(PUBLIC Actions 로그에 개념명·본문 금지) · 커밋 직전
  **누출 게이트**(tracked 사설 파일 있으면 실패). 로컬 야간 런(run-research-scheduled.bat)이 폴백.

## 주요 디렉터리
- `pipeline/` — 수집·요약·랭킹 파이프라인 (Python)
- `site/src/` — Astro 정적 사이트 소스
- `data/YYYY-MM-DD/` — 날짜별 기사 JSON (커밋됨)
- `.github/workflows/` — CI/CD

## 데이터 공개 정책 메모
- `data/embeddings/`는 **의도적으로 공개 커밋**된다 (AUD-015 결정 기록,
  2026-07-03): 사이트의 시맨틱 유사도(ZE1/ZE2) 빌드 입력이라 CI가 읽어야
  하고, 내용물은 요약문의 Voyage 벡터라 원문 복원 불가 — 공개 위험 없음.
  공개를 중단하려면 CI에 별도 벡터 저장소가 필요한 아키텍처 변경이므로
  사용자 결정 없이 바꾸지 말 것.
- `data/*_private/`는 gitignored 로컬 전용 — 커밋 전 매번
  `git ls-files data/research_private/ data/papers_private/` 빈 결과 확인.

## 환경 변수
`.env` 파일에 `ANTHROPIC_API_KEY` 필요. `.env.example` 참고.

## 비밀 스캔
- 모든 push/PR에서 `.github/workflows/gitleaks.yml`이 자동으로 secrets 스캔.
- 로컬 사전 차단(선택): `pip install pre-commit && pre-commit install` → 이후 모든 커밋 직전에 gitleaks 검사.
- 규칙은 `.gitleaks.toml` (기본 룰셋 + `.env.example`·secret 참조 식별자 화이트리스트).

## 아티팩트 인코딩 (UTF-8 표준)
- 한글이 포함된 diff·문서·리뷰 zip은 **반드시 UTF-8**로 내보낸다 (UTF-16·콘솔 코드페이지 모지바케 금지).
- **★UTF-8 한글 파일을 PowerShell `Get-Content -Raw`로 읽지 말 것** (PS5.1 기본 인코딩=ANSI
  코드페이지 → 한글 모지바케). 반드시 `-Encoding UTF8` 또는 **Python/.NET** 사용:
  - `[System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8)` · 또는 `python`으로 슬라이스.
  - (근거: REPRO-1(d), 2026-07-06 — 리뷰 zip decision-record가 `Get-Content -Raw` 오독으로
    **4연속 모지바케**. 소스 decisions.md는 무손상(Edit 툴 UTF-8). 추출부만 버그였음.)
- `git show <hash> > file.txt` (PowerShell 리다이렉트, 콘솔 코드페이지 의존) **금지**. 대신:
  - PS7: `git show <hash> | Out-File -Encoding utf8NoBOM file.txt`
  - PS5.1: `[System.IO.File]::WriteAllText($p, (git show $h | Out-String), (New-Object System.Text.UTF8Encoding $false))`
    (git show 출력 캡처는 UTF-8 유지 확인됨 — 문제는 파일 read였음.)
- 리뷰 zip 제작 시 **(a) UTF-8 no-BOM 확인 + (b) 모지바케 마커 스캔**(예: `꾩·뙆·占쏙`) — BOM/디코드
  검사만으론 부족(더블디코드 모지바케도 valid UTF-8) — + 개념명·기사 본문 무포함 스캔.

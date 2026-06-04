# AI Daily News

매일 자동으로 전 세계 AI 뉴스를 수집하여 한국어 요약·인사이트로 보여주는 정적 대시보드.

## 구성

- **pipeline/** — Python 수집·중복제거·요약 파이프라인 (Claude Haiku 4.5)
- **site/** — Astro 정적 사이트
- **data/** — 날짜별 처리 결과 JSON (커밋됨, 아카이브 역할)
- **.github/workflows/daily.yml** — GitHub Actions cron (KST 07:00 / 15:00 / 23:00)

## 로컬 실행

```bash
# 1) Python 환경
uv sync   # 또는 pip install -e .[dev]

# 2) 환경 변수
cp .env.example .env   # ANTHROPIC_API_KEY 채우기

# 3) 파이프라인 실행
python -m pipeline.collect
python -m pipeline.dedupe
python -m pipeline.summarize
python -m pipeline.rank
python -m pipeline.trending

# 4) 사이트 빌드
cd site
pnpm install
pnpm dev    # 로컬 미리보기
pnpm build  # site/dist 생성
```

## 원칙

- 본문 전문은 메모리에서만 사용, 저장·노출 금지
- 출력은 LLM이 새로 쓴 한국어 요약·인사이트 + 원문 링크
- 신규 기사만 LLM 호출 (URL 해시 캐시로 멱등 보장)

## 공유 / 검색 노출

- **사이트 URL**: https://sung-jinpark.github.io/ai-daily-news/
- **RSS 피드**: https://sung-jinpark.github.io/ai-daily-news/rss.xml (Feedly 등 구독)
- **Sitemap**: https://sung-jinpark.github.io/ai-daily-news/sitemap.xml
- **robots.txt**: 모든 크롤러 허용 + 사이트맵 위치 명시

### Google Search Console 등록 절차

1. https://search.google.com/search-console 접속 → "URL 접두어" 속성 추가
2. 도메인에 `https://sung-jinpark.github.io/ai-daily-news/` 입력
3. 인증 방법 선택: **HTML 태그**
4. 화면에 표시된 `content="..."` 값(예: `abcd1234`)을 복사
5. 레포 Settings → Secrets and variables → Actions → New repository secret
   - Name: `GOOGLE_SITE_VERIFICATION`
   - Value: 위에서 복사한 인증 코드
6. Actions → Deploy site → Run workflow (수동 1회 트리거)
7. 배포 완료 후 Search Console에서 "확인" 클릭
8. 인증 성공 시 sitemap.xml 제출 (좌측 메뉴 → Sitemaps → `sitemap.xml` 입력 → 제출)

며칠 내로 검색 결과에 노출되기 시작합니다.

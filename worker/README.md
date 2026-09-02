# 방문자 통계 — 자체 수집기 (Cloudflare Worker + D1)

GitHub Pages는 정적 호스팅이라 서버 로그가 없다. 이 워커가 대신 페이지뷰
비콘을 받아 Cloudflare D1(무료 티어)에 저장하고, 관리자 조회는
`admin-local/analytics.html`(레포에는 있지만 **어디에도 배포되지 않는** 순수
로컬 파일)에서 `/stats`를 직접 호출해서 본다.

## 데이터로 하지 않는 것
- 원본 IP를 저장하지 않는다 — `sha256(ip|UA|day|salt)` 해시만 D1에 남는다
  (`src/index.js`의 `hashVisitor`). 쿠키도 쓰지 않는다.
- 리퍼러는 전체 URL이 아니라 호스트명만 보낸다(예: `google.com`).

## 최초 셋업 (1회)

```bash
cd worker
npm install
npx wrangler login          # 브라우저가 열리면 Cloudflare 계정으로 로그인/가입
npx wrangler d1 create ai-daily-news-analytics
```

`d1 create`가 출력하는 `database_id`를 `wrangler.toml`의
`REPLACE_AFTER_D1_CREATE` 자리에 붙여넣는다.

```bash
npm run db:init             # 원격 D1에 schema.sql 적용
```

관리자 조회용 비밀 키를 하나 만들어 워커 시크릿으로 등록한다 (이 키는
**절대 git에 커밋하지 않는다** — 아래 "로컬 뷰어 연결"에서 브라우저
localStorage에만 저장한다):

```bash
# 예시로 랜덤 키 생성 (원하는 임의의 문자열이어도 됨)
node -e "console.log(require('crypto').randomBytes(24).toString('hex'))"
npx wrangler secret put ADMIN_KEY     # 위에서 만든 값을 붙여넣기
npx wrangler secret put HASH_SALT     # 방문자 해시용 임의 문자열 (다른 값으로 새로 생성)
```

배포:

```bash
npm run deploy
```

배포 완료 후 출력되는 URL(예: `https://ai-daily-news-analytics.<subdomain>.workers.dev`)을
기록해 둔다.

## 사이트에 수집 스니펫 연결

GitHub 저장소 Settings → Secrets and variables → Actions에 시크릿 추가:

- `ANALYTICS_COLLECT_URL` = `https://ai-daily-news-analytics.<subdomain>.workers.dev/collect`

(비밀값 아님 — 모든 방문자 브라우저가 호출하는 공개 URL이다. Secrets에 넣는
건 단지 기존 `GOOGLE_SITE_VERIFICATION`과 같은 배포 관행을 따른 것.)

다음 push부터 `site/src/layouts/Base.astro`가 모든 페이지에 픽셀 비콘을
심는다 (`PUBLIC_ANALYTICS_COLLECT_URL`이 비어 있으면 스니펫 자체가
렌더링되지 않아 안전하게 no-op).

## 로컬 뷰어 연결

1. `admin-local/analytics.html`을 브라우저로 직접 연다 (더블클릭 또는
   `file://` 경로로 열기 — 별도 서버 불필요).
2. Worker URL(`https://ai-daily-news-analytics.<subdomain>.workers.dev`)과
   위에서 만든 `ADMIN_KEY` 값을 입력하고 저장.
3. 이 브라우저의 localStorage에만 저장되며 그 뒤로는 자동으로 데이터를
   불러온다. 다른 사람은 이 파일을 열어도 키를 모르면 아무 데이터도 볼 수 없다.

## 로컬 개발/테스트

```bash
cd worker
npm run dev                 # http://localhost:8787 에서 워커 실행
npm run db:init:local       # 로컬 D1(sqlite)에 스키마 적용
```

로컬 워커로 비콘을 테스트하려면 브라우저에서
`http://localhost:8787/collect?p=/test&r=test.com` 을 열어보면 된다.

# 방문자 통계 — 자체 구축 (Supabase, 컴퓨트 레이어 없음)

Cloudflare 같은 별도 서버/워커 없이, 방문자 브라우저가 Supabase의 REST API
(PostgREST)에 직접 페이지뷰를 기록한다. 관리자만 아는 키로만 집계 데이터를
조회할 수 있다 (`get_stats` RPC, 서버 측에서 키 해시 비교).

## 데이터로 하지 않는 것
- 원본 IP를 아예 받지 않는다 — 방문자 브라우저가 만든 임의 id
  (localStorage, `crypto.randomUUID()`)만 `visitor_hash`로 저장한다.
- 리퍼러는 전체 URL이 아니라 호스트명만(예: `google.com`).
- 쿠키를 쓰지 않는다.

## 왜 anon 키를 공개해도 되는가
Supabase의 anon/public API 키는 **원래 클라이언트에 노출하도록 설계된
값**이다. 실제 방벽은 Row Level Security(RLS)다 — `hits` 테이블은 anon에게
`insert`만 허용하고 `select`는 아무 정책도 만들지 않아 전면 차단했다.
관리자 키(`ADMIN_KEY`)는 별개로, `admin_config` 테이블(RLS로 완전 잠금)에
**해시로만** 저장되고 `get_stats()` 함수 안에서 `crypt()` 비교로 검증한다.

## 최초 셋업 (1회)

1. https://supabase.com 에서 무료 계정 생성 (GitHub 로그인 가능, 카드 불필요).
2. 새 프로젝트 생성 (리전은 아무 곳이나, DB 비밀번호는 아무거나 — 이후 안 씀).
   프로비저닝에 1~2분 걸림.
3. 왼쪽 메뉴 **SQL Editor** → `analytics/schema.sql` 전체 내용을 붙여넣고 실행.
4. 이어서 관리자 키를 등록하는 아래 문장을 실행 (실제 키 값은 이 저장소에
   커밋하지 않는다 — Claude가 대화로 별도 전달함):
   ```sql
   insert into admin_config (id, key_hash)
   values (1, crypt('REPLACE_WITH_YOUR_KEY', gen_salt('bf')))
   on conflict (id) do update set key_hash = excluded.key_hash;
   ```
5. **Settings → API**에서 **Project URL**과 **anon public key**를 복사
   (둘 다 비밀값 아님 — 공개해도 되는 값).

## 사이트 연결

GitHub 저장소 Settings → Secrets and variables → Actions에 추가:
- `SUPABASE_URL` = Project URL
- `SUPABASE_ANON_KEY` = anon public key

`site/src/layouts/Base.astro`가 `PUBLIC_SUPABASE_URL` /
`PUBLIC_SUPABASE_ANON_KEY`로 이 값을 받아 모든 페이지에 픽셀 대신
`fetch()` 비콘을 심는다 (둘 중 하나라도 비어 있으면 no-op).

## 로컬 관리자 뷰어 연결

`admin-local/config.js`에 Project URL / anon key를 채워 넣는다 (공개
값이라 커밋해도 안전). `admin-local/analytics.html`을 브라우저로 직접 열고
`admin_config`에 등록한 키를 입력하면 끝 — 그 브라우저의 localStorage에만
저장된다.

## 참고 — 무료 티어 유의사항
Supabase 무료 프로젝트는 **7일간 API 호출이 전혀 없으면 자동 일시정지**될
수 있다. 이 사이트는 매일 갱신되고 방문이 있으면 문제 없지만, 혹시
`admin-local/analytics.html`에서 갑자기 응답이 없으면 Supabase 대시보드에서
프로젝트를 깨워야 할 수 있다(클릭 한 번).

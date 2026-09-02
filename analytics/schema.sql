-- 방문자 통계 — 자체 구축 스키마 (Supabase Postgres).
-- 컴퓨트 레이어 없음: 방문자 브라우저가 Supabase REST API(PostgREST)에
-- 직접 hits를 insert한다. anon 키는 Supabase 설계상 공개해도 되는 값이고
-- (RLS가 실제 방벽), 실 데이터 조회는 admin_key를 아는 사람만 get_stats()로
-- 가능하다 — 원본 IP는 아예 받지 않는다(클라이언트가 만든 임의 visitor_hash만).
--
-- 적용 방법: Supabase 대시보드 → SQL Editor → 이 파일 전체 붙여넣고 실행.
-- 그 다음 관리자 키를 등록하는 별도 INSERT 문(비밀이라 이 파일엔 없음)을
-- 한 번 더 실행해야 한다 — analytics/README.md 참고.

create extension if not exists pgcrypto;

create table if not exists hits (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  day date not null default (now() at time zone 'utc')::date,
  path text not null,
  ref text not null default '',       -- 리퍼러 호스트명만 (예: google.com), 빈 값=직접 방문
  browser text not null default '',
  os text not null default '',
  visitor_hash text not null          -- 클라이언트가 생성한 임의 id (localStorage), IP 아님
);

create index if not exists idx_hits_day on hits (day);
create index if not exists idx_hits_path on hits (day, path);
create index if not exists idx_hits_visitor on hits (day, visitor_hash);

alter table hits enable row level security;

drop policy if exists "anon can insert hits" on hits;
create policy "anon can insert hits" on hits
  for insert to anon
  with check (true);
-- select/update/delete 정책을 하나도 만들지 않았으므로 anon은 절대 읽을 수 없다.

-- 관리자 키 해시만 저장 (평문 저장 안 함). RLS로 완전 잠금 — REST로는
-- anon/authenticated 아무도 접근 불가, get_stats()의 SECURITY DEFINER만 우회.
create table if not exists admin_config (
  id int primary key default 1,
  key_hash text not null,
  check (id = 1)
);
alter table admin_config enable row level security;
-- (의도적으로 정책 없음 — 전면 차단)

-- 관리자 통계 조회 RPC. admin_key가 저장된 해시와 일치해야만 데이터 반환.
create or replace function get_stats(admin_key text, days int default 30)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  ok boolean;
  start_day date := (now() at time zone 'utc')::date - days;
  end_day date := (now() at time zone 'utc')::date;
  result json;
begin
  select (key_hash = crypt(admin_key, key_hash)) into ok from admin_config where id = 1;
  if not coalesce(ok, false) then
    raise exception 'unauthorized';
  end if;

  select json_build_object(
    'range', json_build_object('start', start_day, 'end', end_day, 'days', days),
    'total_pageviews', (select count(*) from hits where day between start_day and end_day),
    'total_visitors', (select count(distinct visitor_hash) from hits where day between start_day and end_day),
    'top_paths', (select coalesce(json_agg(t), '[]'::json) from (
      select path as name, count(*) as count from hits
      where day between start_day and end_day
      group by path order by count desc limit 15
    ) t),
    'top_referrers', (select coalesce(json_agg(t), '[]'::json) from (
      select case when ref = '' then '(직접 방문)' else ref end as name, count(*) as count
      from hits where day between start_day and end_day
      group by ref order by count desc limit 15
    ) t),
    'top_browsers', (select coalesce(json_agg(t), '[]'::json) from (
      select browser as name, count(*) as count from hits
      where day between start_day and end_day
      group by browser order by count desc limit 8
    ) t),
    'top_os', (select coalesce(json_agg(t), '[]'::json) from (
      select os as name, count(*) as count from hits
      where day between start_day and end_day
      group by os order by count desc limit 8
    ) t),
    'daily_series', (select coalesce(json_agg(t), '[]'::json) from (
      select day, count(*) as count from hits
      where day between start_day and end_day
      group by day order by day asc
    ) t)
  ) into result;

  return result;
end;
$$;

-- anon이 함수 자체는 호출할 수 있어야 함 (내부에서 admin_key를 검증하므로 안전).
grant execute on function get_stats(text, int) to anon;

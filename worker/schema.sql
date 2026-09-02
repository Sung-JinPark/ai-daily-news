-- 방문자 통계 — 자체 수집기 스키마 (Cloudflare D1)
-- 원본 IP는 절대 저장하지 않는다: visitor_hash = sha256(ip|ua|day|salt),
-- 워커에서 해시로만 저장한다 (worker/src/index.js의 hashVisitor 참고).
CREATE TABLE IF NOT EXISTS hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,               -- unix seconds (UTC)
  day TEXT NOT NULL,                 -- YYYY-MM-DD (UTC), 집계용
  path TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',      -- 리퍼러 호스트명만 (예: google.com), 빈 값=직접 방문
  country TEXT NOT NULL DEFAULT '',  -- Cloudflare가 판별한 국가 코드
  browser TEXT NOT NULL DEFAULT '',
  os TEXT NOT NULL DEFAULT '',
  visitor_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hits_day ON hits(day);
CREATE INDEX IF NOT EXISTS idx_hits_path ON hits(day, path);
CREATE INDEX IF NOT EXISTS idx_hits_visitor ON hits(day, visitor_hash);

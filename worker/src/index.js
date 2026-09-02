// 방문자 통계 자체 수집기 — Cloudflare Worker + D1.
// GET /collect : 페이지뷰 픽셀 비콘 (사이트 어디서나 호출, 비밀 없음)
// GET /stats   : 관리자 조회 (Authorization: Bearer <ADMIN_KEY> 필요)

const PIXEL_GIF = Uint8Array.from(atob("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="), (c) => c.charCodeAt(0));

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization",
};

function parseUA(ua) {
  ua = ua || "";
  let os = "기타";
  if (/Windows/i.test(ua)) os = "Windows";
  else if (/Mac OS X|Macintosh/i.test(ua)) os = "macOS";
  else if (/Android/i.test(ua)) os = "Android";
  else if (/iPhone|iPad|iPod/i.test(ua)) os = "iOS";
  else if (/Linux/i.test(ua)) os = "Linux";

  let browser = "기타";
  if (/Edg\//i.test(ua)) browser = "Edge";
  else if (/Chrome\//i.test(ua) && !/Chromium/i.test(ua)) browser = "Chrome";
  else if (/Firefox\//i.test(ua)) browser = "Firefox";
  else if (/Safari\//i.test(ua) && !/Chrome/i.test(ua)) browser = "Safari";
  return { os, browser };
}

async function hashVisitor(ip, ua, day, salt) {
  const data = new TextEncoder().encode(`${ip}|${ua}|${day}|${salt}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

function utcDay(d) {
  return d.toISOString().slice(0, 10);
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function handleCollect(request, env) {
  const url = new URL(request.url);
  const path = (url.searchParams.get("p") || "/").slice(0, 512);
  const ref = (url.searchParams.get("r") || "").slice(0, 256);
  const ua = request.headers.get("User-Agent") || "";
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const country = request.cf?.country || "";
  const now = new Date();
  const day = utcDay(now);
  const { os, browser } = parseUA(ua);
  const visitorHash = await hashVisitor(ip, ua, day, env.HASH_SALT || "default-salt");

  try {
    await env.DB.prepare(
      "INSERT INTO hits (ts, day, path, ref, country, browser, os, visitor_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
      .bind(Math.floor(now.getTime() / 1000), day, path, ref, country, browser, os, visitorHash)
      .run();
  } catch (e) {
    // 수집 실패가 방문자에게 보이는 응답을 막지 않는다 — 그냥 픽셀은 정상 반환.
  }

  return new Response(PIXEL_GIF, {
    headers: { "Content-Type": "image/gif", "Cache-Control": "no-store" },
  });
}

async function handleStats(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.ADMIN_KEY || ""}`;
  if (!env.ADMIN_KEY || !safeEqual(auth, expected)) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json", ...CORS_HEADERS },
    });
  }

  const url = new URL(request.url);
  const days = Math.min(365, Math.max(1, parseInt(url.searchParams.get("days") || "30", 10) || 30));
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  const startDay = utcDay(start);
  const endDay = utcDay(end);

  const range = "day >= ?1 AND day <= ?2";
  const binds = [startDay, endDay];

  const [totalRow, uniqueRow, paths, refs, countries, browsers, oses, series] = await Promise.all([
    env.DB.prepare(`SELECT COUNT(*) AS n FROM hits WHERE ${range}`).bind(...binds).first(),
    env.DB.prepare(`SELECT COUNT(DISTINCT visitor_hash) AS n FROM hits WHERE ${range}`).bind(...binds).first(),
    env.DB.prepare(`SELECT path AS name, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY path ORDER BY count DESC LIMIT 15`).bind(...binds).all(),
    env.DB.prepare(`SELECT CASE WHEN ref = '' THEN '(직접 방문)' ELSE ref END AS name, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY ref ORDER BY count DESC LIMIT 15`).bind(...binds).all(),
    env.DB.prepare(`SELECT CASE WHEN country = '' THEN '(알 수 없음)' ELSE country END AS name, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY country ORDER BY count DESC LIMIT 10`).bind(...binds).all(),
    env.DB.prepare(`SELECT browser AS name, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY browser ORDER BY count DESC LIMIT 8`).bind(...binds).all(),
    env.DB.prepare(`SELECT os AS name, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY os ORDER BY count DESC LIMIT 8`).bind(...binds).all(),
    env.DB.prepare(`SELECT day, COUNT(*) AS count FROM hits WHERE ${range} GROUP BY day ORDER BY day ASC`).bind(...binds).all(),
  ]);

  const body = {
    range: { start: startDay, end: endDay, days },
    total_pageviews: totalRow?.n || 0,
    total_visitors: uniqueRow?.n || 0,
    top_paths: paths.results || [],
    top_referrers: refs.results || [],
    top_countries: countries.results || [],
    top_browsers: browsers.results || [],
    top_os: oses.results || [],
    daily_series: series.results || [],
  };

  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    if (url.pathname === "/collect" && request.method === "GET") {
      return handleCollect(request, env);
    }

    if (url.pathname === "/stats" && request.method === "GET") {
      return handleStats(request, env);
    }

    return new Response("not found", { status: 404 });
  },
};

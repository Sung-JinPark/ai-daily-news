import type { APIContext } from "astro";
import { allDays, loadDay } from "~/lib/loadData";

// The client-side /search page fetches this file in full on every visit.
// Keeping the payload proportional to the whole archive would push mobile
// users into 20MB+ downloads within a year, so we cap the search index at
// the recent WINDOW_DAYS. Longer-range research is served by the SQLite
// archive advertised on /research.
//
// P2 slimming: 27일 3.14MB 실측 → 90일 cap 포화 시 ~10MB 예상 (5MB 목표 초과).
// insights 필드 제거 + summary 160자 truncate로 페이로드 축소.
// - insights는 검색 스니펫에 거의 안 쓰이는 데다 라인당 500B+ 무거움
// - summary 원문은 매칭 시 관련 day의 articles.json에서 지연 로드 가능하지만
//   현재 /search UI가 스니펫만 쓰므로 160자 컷으로 충분
// - title/tags/source/category/published/importance_score 등 스코어링용 필드는 유지
const WINDOW_DAYS = 90;
const SUMMARY_SNIPPET_CHARS = 160;

function snippet(text: string, max: number): string {
  const t = text ?? "";
  if (t.length <= max) return t;
  return t.slice(0, max - 1) + "\u2026";
}

export async function GET(_context: APIContext) {
  const days = allDays().slice(0, WINDOW_DAYS);
  const docs: Array<{
    id: string;
    day: string;
    url: string;
    title: string;
    summary: string;
    source: string;
    category: string;
    tags: string[];
    published: string | null;
    importance_score: number;
  }> = [];

  const seen = new Set<string>();
  for (const day of days) {
    for (const a of loadDay(day).articles) {
      if (seen.has(a.id)) continue;
      seen.add(a.id);
      docs.push({
        id: a.id,
        day,
        url: a.url,
        title: a.title_original ?? "",
        summary: snippet(a.summary_ko ?? "", SUMMARY_SNIPPET_CHARS),
        source: a.source_name ?? "",
        category: a.category ?? "",
        tags: a.tags ?? [],
        published: a.published ?? null,
        importance_score: a.importance_score ?? 0,
      });
    }
  }

  return new Response(
    JSON.stringify({
      window_days: WINDOW_DAYS,
      snippet_chars: SUMMARY_SNIPPET_CHARS,
      count: docs.length,
      docs,
    }),
    { headers: { "Content-Type": "application/json; charset=utf-8" } },
  );
}

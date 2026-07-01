import type { APIContext } from "astro";
import { allDays, loadDay } from "~/lib/loadData";

// The client-side /search page fetches this file in full on every visit.
// Keeping the payload proportional to the whole archive would push mobile
// users into 20MB+ downloads within a year, so we cap the search index at
// the recent WINDOW_DAYS. Longer-range research is served by the SQLite
// archive advertised on /research.
const WINDOW_DAYS = 90;

export async function GET(_context: APIContext) {
  const days = allDays().slice(0, WINDOW_DAYS);
  const docs: Array<{
    id: string;
    day: string;
    url: string;
    title: string;
    summary: string;
    insights: string;
    source: string;
    category: string;
    tags: string[];
    published: string | null;
    image_url: string | null;
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
        summary: a.summary_ko ?? "",
        insights: (a.insights_ko ?? []).join(" \u2022 "),
        source: a.source_name ?? "",
        category: a.category ?? "",
        tags: a.tags ?? [],
        published: a.published ?? null,
        image_url: a.image_url ?? null,
        importance_score: a.importance_score ?? 0,
      });
    }
  }

  return new Response(
    JSON.stringify({ window_days: WINDOW_DAYS, count: docs.length, docs }),
    { headers: { "Content-Type": "application/json; charset=utf-8" } },
  );
}

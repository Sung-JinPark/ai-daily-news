import type { APIContext } from "astro";
import { allDays, loadDay } from "~/lib/loadData";

export async function GET(_context: APIContext) {
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
  for (const day of allDays()) {
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

  return new Response(JSON.stringify(docs), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

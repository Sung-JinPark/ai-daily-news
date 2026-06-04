import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { CATEGORIES, allDays, loadDay, type Article } from "~/lib/loadData";

const MAX_ITEMS = 50;

function collectRecent(): Article[] {
  const items: Article[] = [];
  for (const day of allDays()) {
    for (const a of loadDay(day).articles) {
      items.push(a);
      if (items.length >= MAX_ITEMS) return items;
    }
  }
  return items;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function buildDescription(a: Article): string {
  const insights = (a.insights_ko ?? [])
    .map((i) => `<li>${escapeHtml(i)}</li>`)
    .join("");
  const category = CATEGORIES[a.category] ?? a.category;
  return `
<p><strong>[${escapeHtml(category)} · ${escapeHtml(a.source_name)}]</strong></p>
<p>${escapeHtml(a.summary_ko)}</p>
${insights ? `<ul>${insights}</ul>` : ""}
<p><a href="${a.url}">원문 보기 →</a></p>
`.trim();
}

export async function GET(context: APIContext) {
  const articles = collectRecent();
  return rss({
    title: "AI Daily News — 한국어 인사이트",
    description: "매일 자동 업데이트되는 글로벌 AI 뉴스 한국어 요약·인사이트",
    site: context.site!,
    items: articles.map((a) => ({
      title: a.title_original,
      link: a.url,
      pubDate: a.published ? new Date(a.published) : new Date(a.fetched_at),
      description: buildDescription(a),
      categories: [CATEGORIES[a.category] ?? a.category, a.source_name],
      author: a.source_name,
    })),
    customData: `<language>ko-KR</language>`,
  });
}

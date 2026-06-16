import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { allWeeklyDigests, articleById } from "~/lib/loadData";

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function GET(context: APIContext) {
  const digests = allWeeklyDigests();
  const lookup = articleById();
  const baseUrl = context.site!.toString().replace(/\/$/, "");
  const basePath = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");

  return rss({
    title: "AI Daily News — 주간 다이제스트",
    description: "매주 일요일 발행되는 글로벌 AI 주간 흐름 분석",
    site: context.site!,
    items: digests.map((d) => {
      const themes = d.themes
        .map((t) => `<li><strong>#${escapeHtml(t.name)}</strong> — ${escapeHtml(t.summary_ko)}</li>`)
        .join("");
      const topList = d.top_story_ids
        .map((id) => lookup.get(id))
        .filter((a) => !!a)
        .map((a) => `<li><a href="${a!.url}">${escapeHtml(a!.title_original)}</a> — ${escapeHtml(a!.source_name)}</li>`)
        .join("");
      return {
        title: `주간 다이제스트 ${d.week}`,
        link: `${baseUrl}${basePath}/weekly/${d.week}`,
        pubDate: new Date(d.generated_at),
        description: `
<p>${escapeHtml(d.theme_recap_ko)}</p>
${themes ? `<h3>테마</h3><ul>${themes}</ul>` : ""}
${topList ? `<h3>이번 주 TOP</h3><ul>${topList}</ul>` : ""}
        `.trim(),
      };
    }),
    customData: `<language>ko-KR</language>`,
  });
}

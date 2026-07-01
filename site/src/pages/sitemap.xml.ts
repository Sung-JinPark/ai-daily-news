import type { APIContext } from "astro";
import { CATEGORIES, allClusters, allDays, allTrendingKeywords, allWeeklyDigests, loadDay, loadTagsIndex } from "~/lib/loadData";
import { PLAYERS } from "~/lib/loadPlayers";

function url(site: URL, base: string, path: string): string {
  const b = base.replace(/\/$/, "");
  return new URL(`${b}${path}`, site).toString();
}

export async function GET(context: APIContext) {
  const site = context.site!;
  const base = (import.meta.env.BASE_URL ?? "/") as string;
  const days = allDays();

  const sources = new Map<string, string>();
  for (const day of days) {
    for (const a of loadDay(day).articles) sources.set(a.source_id, a.source_name);
  }

  const urls: { loc: string; lastmod?: string }[] = [];
  urls.push({ loc: url(site, base, "/"), lastmod: new Date().toISOString() });
  urls.push({ loc: url(site, base, "/today") });
  urls.push({ loc: url(site, base, "/archive") });
  urls.push({ loc: url(site, base, "/topic") });
  urls.push({ loc: url(site, base, "/players") });
  urls.push({ loc: url(site, base, "/weekly") });
  for (const day of days) {
    urls.push({ loc: url(site, base, `/archive/${day}`), lastmod: `${day}T00:00:00Z` });
  }
  for (const slug of Object.keys(CATEGORIES)) {
    urls.push({ loc: url(site, base, `/category/${slug}`) });
  }
  for (const sid of sources.keys()) {
    urls.push({ loc: url(site, base, `/source/${sid}`) });
  }
  const tags = loadTagsIndex();
  if (tags) {
    for (const tag of Object.keys(tags.tags)) {
      urls.push({ loc: url(site, base, `/topic/${encodeURIComponent(tag)}`) });
    }
  }
  for (const p of PLAYERS) {
    urls.push({ loc: url(site, base, `/players/${p.id}`) });
  }
  for (const d of allWeeklyDigests()) {
    urls.push({ loc: url(site, base, `/weekly/${d.week}`), lastmod: d.generated_at });
  }
  for (const t of allTrendingKeywords(30)) {
    urls.push({ loc: url(site, base, `/trending/${encodeURIComponent(t.keyword)}`) });
  }
  for (const c of allClusters(30, 2)) {
    urls.push({ loc: url(site, base, `/story/${c.cluster_id}`), lastmod: `${c.last_seen}T00:00:00Z` });
  }

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls
  .map(
    (u) =>
      `  <url><loc>${u.loc}</loc>${u.lastmod ? `<lastmod>${u.lastmod}</lastmod>` : ""}</url>`,
  )
  .join("\n")}
</urlset>
`;

  return new Response(body, { headers: { "Content-Type": "application/xml" } });
}

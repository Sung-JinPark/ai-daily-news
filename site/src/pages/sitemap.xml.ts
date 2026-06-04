import type { APIContext } from "astro";
import { CATEGORIES, allDays, loadDay, type Article } from "~/lib/loadData";

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
  urls.push({ loc: url(site, base, "/hot") });
  urls.push({ loc: url(site, base, "/archive") });
  for (const day of days) {
    urls.push({ loc: url(site, base, `/archive/${day}`), lastmod: `${day}T00:00:00Z` });
  }
  for (const slug of Object.keys(CATEGORIES)) {
    urls.push({ loc: url(site, base, `/category/${slug}`) });
  }
  for (const sid of sources.keys()) {
    urls.push({ loc: url(site, base, `/source/${sid}`) });
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

import type { APIContext } from "astro";
import fs from "node:fs";
import path from "node:path";

// ZE4: sidecar for /search that carries only the article_id -> neighbor
// article_ids map, without scores. Fetched lazily by the search page
// when the user toggles "시맨틱 확장" on. Kept separate from the main
// search-index.json so the default page load stays lean.

const DATA_ROOT = path.resolve(process.cwd(), "../data");

export async function GET(_context: APIContext) {
  const file = path.join(DATA_ROOT, "similarity", "similar.json");
  if (!fs.existsSync(file)) {
    return new Response(
      JSON.stringify({ schema_version: 1, available: false, similar: {} }),
      { headers: { "Content-Type": "application/json; charset=utf-8" } },
    );
  }
  let similar: Record<string, string[]> = {};
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf-8"));
    for (const [aid, neighbors] of Object.entries<any>(raw.similar ?? {})) {
      if (!Array.isArray(neighbors)) continue;
      similar[aid] = neighbors
        .map((n: any) => n?.article_id)
        .filter((x: unknown): x is string => typeof x === "string");
    }
  } catch {}
  return new Response(
    JSON.stringify({
      schema_version: 1,
      available: Object.keys(similar).length > 0,
      count: Object.keys(similar).length,
      similar,
    }),
    { headers: { "Content-Type": "application/json; charset=utf-8" } },
  );
}

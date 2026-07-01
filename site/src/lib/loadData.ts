import fs from "node:fs";
import path from "node:path";

const DATA_ROOT = path.resolve(process.cwd(), "../data");

export type Article = {
  id: string;
  cluster_id: string;
  title_original: string;
  url: string;
  image_url?: string | null;
  source_id: string;
  source_name: string;
  published: string | null;
  fetched_at: string;
  cluster_size: number;
  also_covered_by: string[];
  summary_ko: string;
  insights_ko: string[];
  category: string;
  importance_score: number;
  tags?: string[];
  subtitle_en?: string;
  institution?: string;
  authors?: string;
};

export type TagsIndex = {
  updated_at: string;
  window_days: number;
  tags: Record<string, { count: number; article_ids: string[]; categories: string[] }>;
};

export type WeeklyDigest = {
  week: string;              // "YYYY-Www"
  n_input: number;
  generated_at: string;
  top_story_ids: string[];
  theme_recap_ko: string;
  themes: Array<{ name: string; summary_ko: string; article_ids: string[] }>;
};

export type GlossaryTerm = {
  term: string;
  full: string;
  desc: string;
  seed?: boolean;
  added_at?: string;
};

export type Glossary = {
  version: number;
  updated_at: string;
  terms: GlossaryTerm[];
};

export type LatestIndex = {
  latest_day: string;
  latest_count?: number;
  low_volume?: boolean;
  low_volume_floor?: number;
  all_days: string[];
  updated_at: string;
};

export type TrendingItem = { keyword: string; count: number };

// English-only trending tokens: ASCII letter start, allow letters/digits/-+./.
const ASCII_KEYWORD_RE = /^[a-z][a-z0-9\-+.]+$/;
function isEnglishKeyword(k: string): boolean {
  return ASCII_KEYWORD_RE.test(k.toLowerCase()) && k.length >= 3;
}
function filterEnglish(items: TrendingItem[]): TrendingItem[] {
  return items.filter((t) => isEnglishKeyword(t.keyword));
}

export type Digest = {
  day: string;
  tldr_ko: string;
  bullets_ko: string[];
  theme_of_day: string;
  n_input?: number;
};

function readJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8")) as T;
  } catch {
    return fallback;
  }
}

export function loadLatest(): LatestIndex | null {
  const file = path.join(DATA_ROOT, "latest.json");
  if (!fs.existsSync(file)) return null;
  return readJson<LatestIndex>(file, { latest_day: "", all_days: [], updated_at: "" });
}

export function loadTagsIndex(): TagsIndex | null {
  const file = path.join(DATA_ROOT, "tags_index.json");
  if (!fs.existsSync(file)) return null;
  return readJson<TagsIndex | null>(file, null);
}

export function allWeeklyDigests(): WeeklyDigest[] {
  const dir = path.join(DATA_ROOT, "weekly");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => readJson<WeeklyDigest | null>(path.join(dir, f), null))
    .filter((d): d is WeeklyDigest => d !== null)
    .sort((a, b) => b.week.localeCompare(a.week));
}

export function loadWeeklyDigest(week: string): WeeklyDigest | null {
  const file = path.join(DATA_ROOT, "weekly", `${week}.json`);
  if (!fs.existsSync(file)) return null;
  return readJson<WeeklyDigest | null>(file, null);
}

// True iff at least one article surfaced in the digest carries tags. Older
// digests (pre-tagging-pipeline) return false so we can hide viz buttons.
export function digestHasTags(digest: WeeklyDigest): boolean {
  const lookup = articleById();
  const ids = [
    ...digest.top_story_ids,
    ...digest.themes.flatMap((t) => t.article_ids),
  ];
  for (const id of ids) {
    const a = lookup.get(id);
    if (a && (a.tags?.length ?? 0) > 0) return true;
  }
  return false;
}

export function loadGlossary(): GlossaryTerm[] {
  const file = path.join(DATA_ROOT, "glossary.json");
  if (!fs.existsSync(file)) return [];
  const data = readJson<Glossary | null>(file, null);
  return data?.terms ?? [];
}

/**
 * Collect unique trending keywords across the last `windowDays` days,
 * summing counts. Useful for generating /trending/[keyword] static pages.
 */
export function allTrendingKeywords(windowDays: number = 30): Array<{ keyword: string; count: number }> {
  const days = allDays().slice(0, windowDays);
  const sums = new Map<string, number>();
  for (const day of days) {
    const items = readJson<TrendingItem[]>(path.join(DATA_ROOT, day, "trending.json"), []);
    for (const t of items) {
      if (!isEnglishKeyword(t.keyword)) continue;
      sums.set(t.keyword, (sums.get(t.keyword) ?? 0) + t.count);
    }
  }
  return [...sums.entries()]
    .map(([keyword, count]) => ({ keyword, count }))
    .sort((a, b) => b.count - a.count);
}

/**
 * Find articles whose title/summary/insights mention the keyword (case-insensitive).
 */
export function articlesMentioningKeyword(keyword: string, windowDays: number = 14): Article[] {
  const needle = keyword.toLowerCase();
  return loadRecentDays(windowDays).filter((a) => {
    const hay = `${a.title_original}\n${a.summary_ko}\n${(a.insights_ko ?? []).join("\n")}`.toLowerCase();
    return hay.includes(needle);
  });
}

export function articleById(): Map<string, Article> {
  const map = new Map<string, Article>();
  for (const a of loadRecentDays(30)) {
    if (!map.has(a.id)) map.set(a.id, a);
  }
  return map;
}

export function loadDay(day: string): {
  articles: Article[];
  highlights: string[];
  trending: TrendingItem[];
  digest: Digest | null;
} {
  const dir = path.join(DATA_ROOT, day);
  const digestFile = path.join(dir, "digest.json");
  const digest = fs.existsSync(digestFile) ? readJson<Digest | null>(digestFile, null) : null;
  return {
    articles: readJson<Article[]>(path.join(dir, "articles.json"), []),
    highlights: readJson<string[]>(path.join(dir, "highlights.json"), []),
    trending: filterEnglish(readJson<TrendingItem[]>(path.join(dir, "trending.json"), [])),
    digest,
  };
}

export function allDays(): string[] {
  if (!fs.existsSync(DATA_ROOT)) return [];
  return fs
    .readdirSync(DATA_ROOT, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^\d{4}-\d{2}-\d{2}$/.test(d.name))
    .map((d) => d.name)
    .sort()
    .reverse();
}

export const CATEGORIES: Record<string, string> = {
  model_research: "모델/연구",
  business: "비즈니스/투자",
  policy: "정책/규제",
  product: "제품/툴",
  hardware: "하드웨어/인프라",
  community: "커뮤니티",
};

/**
 * Combined "hotness" score across multiple days.
 * importance (1-5) is the strongest signal; cluster_size (number of sources
 * covering the story) is a proxy for how widely it's being talked about;
 * recency gives a gentle decay so 5-day-old items don't hold #1 forever.
 */
export function hotnessScore(a: Article, now: Date = new Date()): number {
  const importance = a.importance_score / 5; // 0.2 - 1
  const cluster = Math.log2(Math.max(a.cluster_size, 1) + 1) / 4; // 0 - ~1
  const refDate = a.published ? new Date(a.published) : new Date(a.fetched_at);
  const daysOld = Math.max((now.getTime() - refDate.getTime()) / 86400000, 0);
  const recency = Math.max(1 - daysOld / 7, 0);
  return importance * 0.6 + cluster * 0.15 + recency * 0.25;
}

export type ModelRow = {
  model: string;
  latest_version: string | null;
  latest_seen: string;
  article_count: number;
  articles: Array<{ id: string; day: string; title: string; url: string }>;
  top_benchmarks: Array<{ name: string; score: string; article_id: string }>;
  pricing: Array<{ unit: string; value: string; article_id: string }>;
  strengths_ko: string[];
  weaknesses_ko: string[];
};

export type ModelsIndex = {
  generated_at: string;
  lookback_days: number;
  models: ModelRow[];
};

export function loadModelsIndex(): ModelsIndex | null {
  const file = path.join(DATA_ROOT, "models", "index.json");
  if (!fs.existsSync(file)) return null;
  return readJson<ModelsIndex | null>(file, null);
}

export type Prediction = {
  id: string;
  article_id: string;
  article_url: string;
  article_title: string;
  source_name: string;
  day_made: string;
  claim_ko: string;
  who: string;
  horizon: string;
  confidence: "low" | "medium" | "high";
  status: "pending" | "confirmed" | "contradicted" | "still_pending";
  resolution_article_id: string | null;
  resolution_day: string | null;
  resolution_note_ko: string | null;
  last_reviewed?: string;
};

export function loadPredictions(): Prediction[] {
  const file = path.join(DATA_ROOT, "predictions", "registry.json");
  if (!fs.existsSync(file)) return [];
  const data = readJson<{ predictions?: Prediction[] } | null>(file, null);
  return data?.predictions ?? [];
}

export type Theme = {
  slug: string;
  name: string;
  thesis_ko: string;
  cluster_ids: string[];
  daily_counts: { day: string; count: number }[];
};

export type ThemesPayload = {
  generated_at: string;
  window_start: string;
  window_end: string;
  themes: Theme[];
  week?: string;
};

export function loadRollingThemes(): ThemesPayload | null {
  const file = path.join(DATA_ROOT, "themes", "rolling.json");
  if (!fs.existsSync(file)) return null;
  return readJson<ThemesPayload | null>(file, null);
}

export function allArchivedThemes(): ThemesPayload[] {
  const dir = path.join(DATA_ROOT, "themes");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => /^\d{4}-W\d{2}\.json$/.test(f))
    .map((f) => readJson<ThemesPayload | null>(path.join(dir, f), null))
    .filter((d): d is ThemesPayload => d !== null)
    .sort((a, b) => (b.week ?? "").localeCompare(a.week ?? ""));
}

export function loadRecentDays(maxDays: number = 7): Article[] {
  const days = allDays().slice(0, maxDays);
  const out: Article[] = [];
  for (const day of days) {
    out.push(...loadDay(day).articles);
  }
  return out;
}

/**
 * Compute the ISO week's (Monday, Sunday) range for a given YYYY-Www key.
 * Mirrors pipeline/weekly.py:44-52.
 */
export function weekToDateRange(weekStr: string): { monday: string; sunday: string } {
  const m = weekStr.match(/^(\d{4})-W(\d{1,2})$/);
  if (!m) throw new Error(`invalid week: ${weekStr}`);
  const year = parseInt(m[1], 10);
  const week = parseInt(m[2], 10);
  // ISO week 1 = week containing Jan 4th. Compute Monday of that week, then add (week-1)*7.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const jan4Dow = jan4.getUTCDay() || 7; // 1..7 with Sunday=7
  const weekOneMon = new Date(jan4);
  weekOneMon.setUTCDate(jan4.getUTCDate() - (jan4Dow - 1));
  const mon = new Date(weekOneMon);
  mon.setUTCDate(weekOneMon.getUTCDate() + (week - 1) * 7);
  const sun = new Date(mon);
  sun.setUTCDate(mon.getUTCDate() + 6);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { monday: iso(mon), sunday: iso(sun) };
}

export type ClusterSummary = {
  cluster_id: string;
  articles: Article[];
  first_seen: string;
  last_seen: string;
  category: string;
  tags: string[];
  outlets: string[];
  member_count: number;
  day_span: number;
};

let _clusterCache: Map<string, Article[]> | null = null;
let _clusterCacheDays = -1;

/**
 * Group all articles from the recent window by cluster_id, sorted oldest→newest per cluster.
 * Cached across calls within the same build.
 */
export function articlesByCluster(maxDays: number = 30): Map<string, Article[]> {
  if (_clusterCache && _clusterCacheDays === maxDays) return _clusterCache;
  const map = new Map<string, Article[]>();
  const seenIds = new Set<string>();
  for (const a of loadRecentDays(maxDays)) {
    if (!a.cluster_id) continue;
    if (seenIds.has(a.id)) continue;
    seenIds.add(a.id);
    const arr = map.get(a.cluster_id) ?? [];
    arr.push(a);
    map.set(a.cluster_id, arr);
  }
  for (const arr of map.values()) {
    arr.sort((a, b) => (a.published ?? a.fetched_at).localeCompare(b.published ?? b.fetched_at));
  }
  _clusterCache = map;
  _clusterCacheDays = maxDays;
  return map;
}

function summarizeCluster(clusterId: string, articles: Article[]): ClusterSummary {
  const outlets = Array.from(new Set(articles.map((a) => a.source_name))).sort();
  const catCount = new Map<string, number>();
  const tagCount = new Map<string, number>();
  for (const a of articles) {
    catCount.set(a.category, (catCount.get(a.category) ?? 0) + 1);
    for (const t of a.tags ?? []) tagCount.set(t, (tagCount.get(t) ?? 0) + 1);
  }
  let dominantCategory = articles[0]?.category ?? "";
  let bestCat = 0;
  for (const [c, n] of catCount) if (n > bestCat) { bestCat = n; dominantCategory = c; }
  const tags = Array.from(tagCount.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([t]) => t);
  const first = articles[0];
  const last = articles[articles.length - 1];
  const firstSeen = (first.published ?? first.fetched_at).slice(0, 10);
  const lastSeen = (last.published ?? last.fetched_at).slice(0, 10);
  const daySpan = Math.max(
    1,
    Math.round(
      (Date.parse(lastSeen) - Date.parse(firstSeen)) / 86400000,
    ) + 1,
  );
  return {
    cluster_id: clusterId,
    articles,
    first_seen: firstSeen,
    last_seen: lastSeen,
    category: dominantCategory,
    tags,
    outlets,
    member_count: articles.length,
    day_span: daySpan,
  };
}

/**
 * Enumerate meaningful clusters — those with ≥minMembers articles across days
 * OR spanning ≥2 days OR any single article covered by multiple outlets.
 * Used for /story/[cluster] getStaticPaths and for Related-Story lookups.
 */
export function allClusters(maxDays: number = 30, minMembers: number = 2): ClusterSummary[] {
  const grouped = articlesByCluster(maxDays);
  const out: ClusterSummary[] = [];
  for (const [id, articles] of grouped) {
    const summary = summarizeCluster(id, articles);
    const multiOutlet = articles.some((a) => (a.cluster_size ?? 1) > 1);
    if (summary.member_count >= minMembers || summary.day_span >= 2 || multiOutlet) {
      out.push(summary);
    }
  }
  return out.sort((a, b) => b.last_seen.localeCompare(a.last_seen));
}

/**
 * Load all articles published within the calendar week `weekStr` (YYYY-Www).
 * Uses `weekToDateRange` to compute Mon/Sun and pulls each matching day file.
 */
export function loadWeekArticles(weekStr: string): Article[] {
  const { monday, sunday } = weekToDateRange(weekStr);
  const out: Article[] = [];
  for (const day of allDays()) {
    if (day >= monday && day <= sunday) {
      out.push(...loadDay(day).articles);
    }
  }
  return out;
}

export type CoverageCluster = {
  cluster_id: string;
  title: string;
  category: string;
  outlets: string[];
  cluster_size: number;
  importance_score: number;
};

export type CoverageMatrix = {
  outlets: string[];
  clusters: CoverageCluster[];
  cells: Array<{ outlet: string; cluster_id: string; importance: number }>;
};

/**
 * Build a matrix of outlets × top clusters for the given week.
 * Cluster score = cluster_size × max importance. Outlets sorted by article count.
 */
export function weeklyCoverageMatrix(weekStr: string, maxClusters = 12, maxOutlets = 15): CoverageMatrix {
  const arts = loadWeekArticles(weekStr);
  const byCluster = new Map<string, Article[]>();
  for (const a of arts) {
    if (!a.cluster_id) continue;
    const arr = byCluster.get(a.cluster_id) ?? [];
    arr.push(a);
    byCluster.set(a.cluster_id, arr);
  }
  const clusters: CoverageCluster[] = [];
  for (const [cid, group] of byCluster) {
    const rep = group.slice().sort((a, b) => b.importance_score - a.importance_score)[0];
    const outletsSet = new Set<string>();
    let clusterSize = 1;
    for (const a of group) {
      outletsSet.add(a.source_name);
      for (const co of a.also_covered_by ?? []) outletsSet.add(co);
      clusterSize = Math.max(clusterSize, a.cluster_size ?? 1);
    }
    clusters.push({
      cluster_id: cid,
      title: rep.title_original,
      category: rep.category,
      outlets: Array.from(outletsSet),
      cluster_size: clusterSize,
      importance_score: rep.importance_score,
    });
  }
  clusters.sort((a, b) => {
    const sa = a.cluster_size * a.importance_score;
    const sb = b.cluster_size * b.importance_score;
    if (sb !== sa) return sb - sa;
    return b.importance_score - a.importance_score;
  });
  const topClusters = clusters.slice(0, maxClusters);

  const outletCounts = new Map<string, number>();
  for (const a of arts) outletCounts.set(a.source_name, (outletCounts.get(a.source_name) ?? 0) + 1);
  const outletList = Array.from(outletCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, maxOutlets)
    .map(([o]) => o);
  const outletSet = new Set(outletList);

  const cells: Array<{ outlet: string; cluster_id: string; importance: number }> = [];
  for (const c of topClusters) {
    for (const o of c.outlets) {
      if (outletSet.has(o)) {
        cells.push({ outlet: o, cluster_id: c.cluster_id, importance: c.importance_score });
      }
    }
  }
  return { outlets: outletList, clusters: topClusters, cells };
}

/**
 * For each outlet in the week, the fraction of its articles falling in each category.
 * Only outlets with ≥3 weekly articles are returned.
 */
export function weeklyOutletCategoryMix(weekStr: string, minArticles = 3): Array<{
  outlet: string;
  total: number;
  counts: Record<string, number>;
}> {
  const arts = loadWeekArticles(weekStr);
  const byOutlet = new Map<string, Record<string, number>>();
  const totals = new Map<string, number>();
  for (const a of arts) {
    const row = byOutlet.get(a.source_name) ?? {};
    row[a.category] = (row[a.category] ?? 0) + 1;
    byOutlet.set(a.source_name, row);
    totals.set(a.source_name, (totals.get(a.source_name) ?? 0) + 1);
  }
  const out: Array<{ outlet: string; total: number; counts: Record<string, number> }> = [];
  for (const [outlet, counts] of byOutlet) {
    const total = totals.get(outlet) ?? 0;
    if (total < minArticles) continue;
    out.push({ outlet, total, counts });
  }
  return out.sort((a, b) => b.total - a.total);
}

export function loadCluster(clusterId: string, maxDays: number = 30): ClusterSummary | null {
  const grouped = articlesByCluster(maxDays);
  const arr = grouped.get(clusterId);
  if (!arr || arr.length === 0) return null;
  return summarizeCluster(clusterId, arr);
}

/**
 * Related clusters ranked by tag Jaccard × recency boost.
 * Returns clusters that share ≥1 tag with the given cluster.
 */
export function relatedClusters(clusterId: string, limit: number = 6, maxDays: number = 30): Array<ClusterSummary & { overlap: number }> {
  const target = loadCluster(clusterId, maxDays);
  if (!target || target.tags.length === 0) return [];
  const targetTags = new Set(target.tags);
  const now = Date.now();
  const scored: Array<ClusterSummary & { overlap: number; score: number }> = [];
  for (const c of allClusters(maxDays, 1)) {
    if (c.cluster_id === clusterId) continue;
    const shared = c.tags.filter((t) => targetTags.has(t));
    if (shared.length === 0) continue;
    const union = new Set([...targetTags, ...c.tags]).size;
    const jaccard = shared.length / union;
    const daysOld = Math.max(0, (now - Date.parse(c.last_seen)) / 86400000);
    const recency = Math.max(0, 1 - daysOld / 14);
    const score = jaccard * (0.6 + 0.4 * recency);
    scored.push({ ...c, overlap: shared.length, score });
  }
  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ score: _score, ...rest }) => rest);
}

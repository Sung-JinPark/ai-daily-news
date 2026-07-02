import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

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

// X3 schema-version awareness. The loader keeps its own KNOWN_VERSIONS
// table so that a pipeline producing schema_version=2 (or higher)
// prints a build-time warning without failing the build — the operator
// gets an early heads-up that a migration is due, but a partial deploy
// where the pipeline advances first still renders.
const KNOWN_SCHEMA_VERSIONS: Record<string, number> = {
  "themes/rolling.json": 1,
  "themes/weekly": 1,
  "predictions/registry.json": 1,
  "models/index.json": 1,
  "reports/quarterly": 1,
  "corpus/manifest.json": 1,
  "cluster_continuity.json": 1,
};
const _warnedFor = new Set<string>();
function _checkSchemaVersion(label: string, payload: unknown): void {
  if (!payload || typeof payload !== "object") return;
  const v = (payload as { schema_version?: unknown }).schema_version;
  if (typeof v !== "number") return; // missing = legacy file, treat as v1 silently
  const known = KNOWN_SCHEMA_VERSIONS[label];
  if (known == null) return;
  if (v !== known && !_warnedFor.has(label)) {
    _warnedFor.add(label);
    // eslint-disable-next-line no-console
    console.warn(`[loadData] schema drift on ${label}: file schema_version=${v}, loader expects ${known}. Update the site loader/renderer.`);
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

export type QuarterlyReport = {
  quarter: string;
  generated_at: string;
  start: string;
  end: string;
  n_days: number;
  n_articles: number;
  // Coverage disclosure — populated for reports generated by N1 and
  // later; older reports leave these undefined and the site falls
  // back to the (start ~ end, n_days) header alone.
  quarter_total_days?: number;
  coverage_days?: number;
  coverage_ratio?: number;
  title_ko: string;
  exec_summary_ko: string;
  top_narratives_ko: Array<{ name: string; summary_ko: string }>;
  top_movers_ko: Array<{ entity: string; movement_ko: string }>;
  open_questions_ko: string[];
  closing_ko: string;
};

export function allQuarterlyReports(): QuarterlyReport[] {
  const dir = path.join(DATA_ROOT, "reports");
  if (!fs.existsSync(dir)) return [];
  const items = fs
    .readdirSync(dir)
    .filter((f) => /^\d{4}-Q[1-4]\.json$/.test(f))
    .map((f) => readJson<QuarterlyReport | null>(path.join(dir, f), null))
    .filter((r): r is QuarterlyReport => r !== null);
  for (const r of items) _checkSchemaVersion("reports/quarterly", r);
  return items.sort((a, b) => b.quarter.localeCompare(a.quarter));
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
  const data = readJson<ModelsIndex | null>(file, null);
  _checkSchemaVersion("models/index.json", data);
  return data;
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
  _checkSchemaVersion("predictions/registry.json", data);
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
  const data = readJson<ThemesPayload | null>(file, null);
  _checkSchemaVersion("themes/rolling.json", data);
  return data;
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

// ---------- ZE3 semantic similarity loader ----------

export type SimilarNeighbor = { article_id: string; score: number };

let _similarityCache: Record<string, SimilarNeighbor[]> | null = null;
function loadSimilarity(): Record<string, SimilarNeighbor[]> {
  if (_similarityCache) return _similarityCache;
  const file = path.join(DATA_ROOT, "similarity", "similar.json");
  if (!fs.existsSync(file)) {
    _similarityCache = {};
    return _similarityCache;
  }
  const data = readJson<{ similar?: Record<string, SimilarNeighbor[]> } | null>(file, null);
  _similarityCache = data?.similar ?? {};
  return _similarityCache;
}

/** Whether the similarity index has any entries. Consumers use this to
 * decide between semantic and tag-based fallback. */
export function similarityAvailable(): boolean {
  return Object.keys(loadSimilarity()).length > 0;
}

/**
 * Return clusters semantically closest to the given cluster by
 * aggregating the top-K neighbors of every article inside it.
 * Returns an empty list when the similarity file has no data yet;
 * callers should fall back to tag-based relatedClusters().
 */
export function relatedSemanticClusters(
  clusterId: string,
  limit: number = 6,
  maxDays: number = CLUSTER_WINDOW_DAYS,
): Array<ClusterSummary & { score: number }> {
  const target = loadCluster(clusterId, maxDays);
  if (!target) return [];
  const sim = loadSimilarity();
  if (!Object.keys(sim).length) return [];
  const grouped = articlesByCluster(maxDays);
  const clusterByArticle = new Map<string, string>();
  for (const [cid, arts] of grouped) {
    for (const a of arts) clusterByArticle.set(a.id, cid);
  }
  const bestByCluster = new Map<string, number>();
  for (const a of target.articles) {
    const neighbors = sim[a.id] ?? [];
    for (const n of neighbors) {
      const otherCluster = clusterByArticle.get(n.article_id);
      if (!otherCluster || otherCluster === clusterId) continue;
      const prev = bestByCluster.get(otherCluster) ?? 0;
      if (n.score > prev) bestByCluster.set(otherCluster, n.score);
    }
  }
  const scored: Array<ClusterSummary & { score: number }> = [];
  for (const [otherCluster, score] of bestByCluster) {
    const summary = loadCluster(otherCluster, maxDays);
    if (!summary) continue;
    scored.push({ ...summary, score });
  }
  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

// ---------- Z3 corpus completeness ----------

export type SkippedRow = {
  logged_at?: string;
  day: string;
  url_hash?: string;
  url?: string;
  source_id?: string;
  title?: string;
  phase: string;
  reason?: string;
};

export type SourceHealthRow = {
  logged_at?: string;
  day: string;
  source_id: string;
  items: number;
  capped?: number;
  error?: string;
};

export type CorpusDayCoverage = {
  day: string;
  bodies?: { lines: number; bytes: number };
  members?: { lines: number; bytes: number };
  skipped?: { lines: number; bytes: number };
};

export function loadCorpusManifest(): {
  days: Record<string, CorpusDayCoverage>;
} {
  const file = path.join(DATA_ROOT, "corpus", "manifest.json");
  if (!fs.existsSync(file)) return { days: {} };
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf-8"));
    const daysIn = raw?.days ?? {};
    const days: Record<string, CorpusDayCoverage> = {};
    for (const [day, entry] of Object.entries<any>(daysIn)) {
      const files = entry?.files ?? {};
      days[day] = {
        day,
        bodies: files["bodies.jsonl"] ? { lines: files["bodies.jsonl"].lines ?? 0, bytes: files["bodies.jsonl"].bytes ?? 0 } : undefined,
        members: files["members.jsonl"] ? { lines: files["members.jsonl"].lines ?? 0, bytes: files["members.jsonl"].bytes ?? 0 } : undefined,
        skipped: files["skipped.jsonl"] ? { lines: files["skipped.jsonl"].lines ?? 0, bytes: files["skipped.jsonl"].bytes ?? 0 } : undefined,
      };
    }
    return { days };
  } catch {
    return { days: {} };
  }
}

export function loadSkippedRows(): SkippedRow[] {
  const corpusRoot = path.join(DATA_ROOT, "corpus");
  if (!fs.existsSync(corpusRoot)) return [];
  const rows: SkippedRow[] = [];
  for (const day of fs.readdirSync(corpusRoot)) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
    const p = path.join(corpusRoot, day, "skipped.jsonl");
    if (!fs.existsSync(p)) continue;
    for (const line of fs.readFileSync(p, "utf-8").split("\n")) {
      const t = line.trim();
      if (!t) continue;
      try {
        rows.push({ day, ...(JSON.parse(t) as SkippedRow) });
      } catch {
        continue;
      }
    }
  }
  return rows;
}

export function loadSourceHealth(): SourceHealthRow[] {
  const file = path.join(DATA_ROOT, "aggregates", "source_health.jsonl");
  if (!fs.existsSync(file)) return [];
  const rows: SourceHealthRow[] = [];
  for (const line of fs.readFileSync(file, "utf-8").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      rows.push(JSON.parse(t) as SourceHealthRow);
    } catch {
      continue;
    }
  }
  return rows;
}

// ---------- Z2 co-occurrence graph loader ----------

export type EntityCooc = {
  day: string;
  entity_a: string;
  entity_a_type: "model" | "lab" | "tag";
  entity_b: string;
  entity_b_type: "model" | "lab" | "tag";
  cluster_id: string;
  article_id: string;
  category: string;
};

let _entityCoocCache: EntityCooc[] | null = null;
export function loadEntityCooccurrence(): EntityCooc[] {
  if (_entityCoocCache) return _entityCoocCache;
  const file = path.join(DATA_ROOT, "aggregates", "entity_cooccurrence.jsonl");
  if (!fs.existsSync(file)) {
    _entityCoocCache = [];
    return _entityCoocCache;
  }
  const rows: EntityCooc[] = [];
  for (const line of fs.readFileSync(file, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      rows.push(JSON.parse(trimmed) as EntityCooc);
    } catch {
      continue;
    }
  }
  _entityCoocCache = rows;
  return _entityCoocCache;
}

export type CooccurrenceEdge = {
  a: string;
  a_type: "model" | "lab" | "tag";
  b: string;
  b_type: "model" | "lab" | "tag";
  weight: number;
  cluster_ids: string[];
};

export type CooccurrenceGraph = {
  nodes: Array<{ id: string; type: "model" | "lab" | "tag"; degree: number; total_weight: number }>;
  edges: CooccurrenceEdge[];
};

/**
 * Aggregate entity_cooccurrence.jsonl into a node/edge graph. Filter
 * options let the caller focus on typed slices (model×lab is the
 * canonical view; model×tag / lab×tag exist too).
 */
export function cooccurrenceGraph(opts: {
  minWeight?: number;
  topNodes?: number;
  includeTypes?: Array<"model" | "lab" | "tag">;
} = {}): CooccurrenceGraph {
  const minWeight = opts.minWeight ?? 1;
  const topNodes = opts.topNodes ?? 25;
  const includeTypes = new Set(opts.includeTypes ?? ["model", "lab", "tag"]);
  const rows = loadEntityCooccurrence();
  if (rows.length === 0) return { nodes: [], edges: [] };

  const edgeMap = new Map<string, CooccurrenceEdge>();
  const typeOf = new Map<string, "model" | "lab" | "tag">();
  for (const r of rows) {
    if (!includeTypes.has(r.entity_a_type) || !includeTypes.has(r.entity_b_type)) continue;
    typeOf.set(r.entity_a, r.entity_a_type);
    typeOf.set(r.entity_b, r.entity_b_type);
    const key = `${r.entity_a}\u0001${r.entity_b}`;
    const existing = edgeMap.get(key);
    if (existing) {
      existing.weight += 1;
      if (!existing.cluster_ids.includes(r.cluster_id)) existing.cluster_ids.push(r.cluster_id);
    } else {
      edgeMap.set(key, {
        a: r.entity_a,
        a_type: r.entity_a_type,
        b: r.entity_b,
        b_type: r.entity_b_type,
        weight: 1,
        cluster_ids: [r.cluster_id],
      });
    }
  }

  // Filter edges by min weight.
  const edges = Array.from(edgeMap.values())
    .filter((e) => e.weight >= minWeight)
    .sort((a, b) => b.weight - a.weight);

  // Score nodes by total edge weight to pick the visible top-N.
  const nodeScores = new Map<string, { degree: number; total: number }>();
  for (const e of edges) {
    for (const id of [e.a, e.b]) {
      const s = nodeScores.get(id) ?? { degree: 0, total: 0 };
      s.degree += 1;
      s.total += e.weight;
      nodeScores.set(id, s);
    }
  }
  const nodes = Array.from(nodeScores.entries())
    .map(([id, s]) => ({ id, type: typeOf.get(id)!, degree: s.degree, total_weight: s.total }))
    .sort((a, b) => b.total_weight - a.total_weight)
    .slice(0, topNodes);
  const kept = new Set(nodes.map((n) => n.id));
  const visibleEdges = edges.filter((e) => kept.has(e.a) && kept.has(e.b));
  return { nodes, edges: visibleEdges };
}

export type EntityMention = {
  day: string;
  entity_type: "model" | "lab" | "tag";
  entity: string;
  article_id: string;
  cluster_id: string;
  source_id: string;
  importance_score: number;
  category: string;
};

let _entityMentionsCache: EntityMention[] | null = null;
export function loadEntityMentions(): EntityMention[] {
  if (_entityMentionsCache) return _entityMentionsCache;
  const file = path.join(DATA_ROOT, "aggregates", "entity_mentions.jsonl");
  if (!fs.existsSync(file)) {
    _entityMentionsCache = [];
    return _entityMentionsCache;
  }
  const rows: EntityMention[] = [];
  for (const line of fs.readFileSync(file, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      rows.push(JSON.parse(trimmed) as EntityMention);
    } catch {
      continue;
    }
  }
  _entityMentionsCache = rows;
  return _entityMentionsCache;
}

export type TimeSeriesPoint = { bucket: string; count: number };
export type EntityTimeSeries = {
  entity: string;
  entity_type: "model" | "lab" | "tag";
  total: number;
  series: TimeSeriesPoint[];
  peak_bucket: string;
  peak_count: number;
};

/**
 * Bucket entity mentions by day/week/month across the whole archive and
 * return the top-N entities as sparklines. Fills empty buckets with
 * count=0 so every series has the same x-axis length.
 */
export function entityTimeSeries(
  granularity: "day" | "week" | "month" = "day",
  topN: number = 15,
  typeFilter?: "model" | "lab" | "tag",
): { buckets: string[]; series: EntityTimeSeries[] } {
  const rows = loadEntityMentions();
  if (rows.length === 0) return { buckets: [], series: [] };

  function bucketOf(day: string): string {
    if (granularity === "day") return day;
    const d = new Date(day + "T00:00:00Z");
    if (granularity === "month") return day.slice(0, 7);
    // ISO week — YYYY-Www
    const target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const dayNum = target.getUTCDay() || 7;
    target.setUTCDate(target.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
    const weekNo = Math.ceil((((target.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
    return `${target.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
  }

  const allBuckets = new Set<string>();
  const byEntity = new Map<string, Map<string, number>>();
  const typeOf = new Map<string, "model" | "lab" | "tag">();
  for (const r of rows) {
    if (typeFilter && r.entity_type !== typeFilter) continue;
    const b = bucketOf(r.day);
    allBuckets.add(b);
    const key = `${r.entity_type}\u0001${r.entity}`;
    typeOf.set(key, r.entity_type);
    const inner = byEntity.get(key) ?? new Map<string, number>();
    inner.set(b, (inner.get(b) ?? 0) + 1);
    byEntity.set(key, inner);
  }
  const buckets = Array.from(allBuckets).sort();

  const scored: EntityTimeSeries[] = [];
  for (const [key, inner] of byEntity) {
    let total = 0;
    let peakBucket = buckets[0] ?? "";
    let peakCount = 0;
    const series: TimeSeriesPoint[] = buckets.map((b) => {
      const count = inner.get(b) ?? 0;
      total += count;
      if (count > peakCount) {
        peakCount = count;
        peakBucket = b;
      }
      return { bucket: b, count };
    });
    scored.push({
      entity: key.split("\u0001")[1],
      entity_type: typeOf.get(key)!,
      total,
      series,
      peak_bucket: peakBucket,
      peak_count: peakCount,
    });
  }
  scored.sort((a, b) => b.total - a.total);
  return { buckets, series: scored.slice(0, topN) };
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
  // X1 stable slug — deterministic hash of the earliest (published,
  // url_hash) tuple observed for the cluster. Undefined if the
  // continuity entry has not been backfilled yet.
  first_url_hash?: string;
};

// ---------- X1 stable-slug URL layer ----------
// The site advertises `/story/s-<first_url_hash>` as the canonical URL
// for every cluster once its continuity entry carries a stable hash.
// The legacy `k000…` route stays alive as a client-side redirect stub
// so already-indexed links keep working during the six-month migration
// window (see reviews/story-url-migration-plan-2026-07-01.md).

type ContinuityEntry = {
  cluster_id: string;
  simhash?: string;
  last_seen?: string;
  first_url_hash?: string;
  first_published?: string;
};

let _continuityCache: ContinuityEntry[] | null = null;
function loadContinuity(): ContinuityEntry[] {
  if (_continuityCache) return _continuityCache;
  const file = path.join(DATA_ROOT, "cluster_continuity.json");
  if (!fs.existsSync(file)) {
    _continuityCache = [];
    return _continuityCache;
  }
  const data = readJson<{ entries?: ContinuityEntry[] } | null>(file, null);
  _continuityCache = data?.entries ?? [];
  return _continuityCache;
}

let _slugById: Map<string, string> | null = null;
let _idsBySlug: Map<string, string> | null = null;
function _ensureSlugMaps(): void {
  if (_slugById && _idsBySlug) return;
  _slugById = new Map();
  _idsBySlug = new Map();
  for (const e of loadContinuity()) {
    if (e.first_url_hash) {
      const slug = `s-${e.first_url_hash}`;
      _slugById.set(e.cluster_id, slug);
      _idsBySlug.set(slug, e.cluster_id);
    }
  }
}

// Y1 F-1: same 16-char url_hash the pipeline uses (pipeline/state.py).
function urlHash16(url: string): string {
  return crypto.createHash("sha256").update(url, "utf-8").digest("hex").slice(0, 16);
}

// Y1 F-1: computes the same deterministic (published, url_hash) minimum
// the pipeline computes on the server side. Used to synthesize a stable
// slug for clusters that have no continuity entry — either because they
// come from the legacy c-prefix scheme that predates the current
// dedupe rollout, or because they were created after the last backfill
// run.
function computeSlugFromArticles(articles: Article[]): string | undefined {
  if (!articles || articles.length === 0) return undefined;
  const keys: Array<[string, string]> = [];
  for (const a of articles) {
    if (!a.url) continue;
    keys.push([a.published ?? "", urlHash16(a.url)]);
  }
  if (keys.length === 0) return undefined;
  keys.sort((a, b) => (a[0] === b[0] ? a[1].localeCompare(b[1]) : a[0].localeCompare(b[0])));
  return `s-${keys[0][1]}`;
}

// Caches the per-cluster fallback slug so we don't recompute for every
// StoryLink / getStaticPaths / sitemap emission.
const _computedSlugById = new Map<string, string>();

/** Return the canonical URL slug for a given cluster_id.
 *
 * Order of precedence (Y1):
 *   1. Continuity entry with a frozen first_url_hash.
 *   2. Deterministic minimum computed from the cluster's articles
 *      (covers c-prefix legacy clusters + new clusters born after the
 *      last backfill). Cached per build.
 *   3. Legacy cluster_id when neither path yields a value.
 */
export function clusterSlug(clusterId: string): string {
  _ensureSlugMaps();
  const fromContinuity = _slugById!.get(clusterId);
  if (fromContinuity) return fromContinuity;
  const cached = _computedSlugById.get(clusterId);
  if (cached) return cached;
  // Fallback: recompute from articles. Uses articlesByCluster's cache.
  const grouped = articlesByCluster();
  const arts = grouped.get(clusterId);
  const slug = arts ? computeSlugFromArticles(arts) : undefined;
  if (slug) {
    _computedSlugById.set(clusterId, slug);
    _idsBySlug!.set(slug, clusterId);
    return slug;
  }
  return clusterId;
}

/** Reverse lookup: takes an `s-…` slug and returns the underlying
 * cluster_id, or returns the input unchanged when it is not a stable
 * slug (i.e. the caller passed a legacy `k000…` id).
 */
export function clusterIdFromSlug(slug: string): string {
  _ensureSlugMaps();
  return _idsBySlug!.get(slug) ?? slug;
}

let _clusterCache: Map<string, Article[]> | null = null;
let _clusterCacheDays = -1;

/**
 * Group all articles from the recent window by cluster_id, sorted oldest→newest per cluster.
 * Cached across calls within the same build.
 */
// Cluster window matches pipeline/dedupe.py CONTINUITY_DAYS (90). Keeping
// these in lockstep prevents story timelines from silently truncating the
// oldest members of a persistent cluster.
export const CLUSTER_WINDOW_DAYS = 90;

export function articlesByCluster(maxDays: number = CLUSTER_WINDOW_DAYS): Map<string, Article[]> {
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
  // Pull the stable slug hash. Prefer the frozen continuity value
  // (Y1 F-2); when missing, synthesize deterministically from these
  // articles (Y1 F-1 fallback for c-prefix / late-arrival clusters).
  _ensureSlugMaps();
  let slugCandidate = _slugById!.get(clusterId);
  if (!slugCandidate) {
    slugCandidate = computeSlugFromArticles(articles);
  }
  const firstUrlHash = slugCandidate?.startsWith("s-") ? slugCandidate.slice(2) : undefined;
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
    first_url_hash: firstUrlHash,
  };
}

/**
 * Enumerate meaningful clusters — those with ≥minMembers articles across days
 * OR spanning ≥2 days OR any single article covered by multiple outlets.
 * Used for /story/[cluster] getStaticPaths and for Related-Story lookups.
 */
// AUDIT-1 AUD-004: link emitters must use the SAME eligibility rule as
// /story/[cluster].astro's getStaticPaths, or they produce dangling
// links to slugs whose page was never built (observed live on archive
// and players pages). One cached set, one source of truth.
let _storyPageIds: Set<string> | null = null;
export function storyPageExists(clusterId: string | null | undefined): boolean {
  if (!clusterId) return false;
  if (!_storyPageIds) {
    _storyPageIds = new Set(allClusters(CLUSTER_WINDOW_DAYS, 2).map((c) => c.cluster_id));
  }
  return _storyPageIds.has(clusterId);
}


export function allClusters(maxDays: number = CLUSTER_WINDOW_DAYS, minMembers: number = 2): ClusterSummary[] {
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

export function loadCluster(clusterId: string, maxDays: number = CLUSTER_WINDOW_DAYS): ClusterSummary | null {
  const grouped = articlesByCluster(maxDays);
  const arr = grouped.get(clusterId);
  if (!arr || arr.length === 0) return null;
  return summarizeCluster(clusterId, arr);
}

/**
 * Related clusters ranked by tag Jaccard × recency boost.
 * Returns clusters that share ≥1 tag with the given cluster.
 */
export function relatedClusters(clusterId: string, limit: number = 6, maxDays: number = CLUSTER_WINDOW_DAYS): Array<ClusterSummary & { overlap: number }> {
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

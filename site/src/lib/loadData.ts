import fs from "node:fs";
import path from "node:path";

const DATA_ROOT = path.resolve(process.cwd(), "../data");

export type Article = {
  id: string;
  cluster_id: string;
  title_original: string;
  url: string;
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
};

export type LatestIndex = {
  latest_day: string;
  all_days: string[];
  updated_at: string;
};

export type TrendingItem = { keyword: string; count: number };

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

export function loadDay(day: string): {
  articles: Article[];
  highlights: string[];
  trending: TrendingItem[];
} {
  const dir = path.join(DATA_ROOT, day);
  return {
    articles: readJson<Article[]>(path.join(dir, "articles.json"), []),
    highlights: readJson<string[]>(path.join(dir, "highlights.json"), []),
    trending: readJson<TrendingItem[]>(path.join(dir, "trending.json"), []),
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

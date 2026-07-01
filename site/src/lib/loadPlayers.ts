import players from "~/data/players.json";
import { loadRecentDays, type Article } from "./loadData";

export type Player = {
  id: string;
  name: string;
  color: string;
  blurb: string;
  homepage: string;
  source_ids: string[];
  tags: string[];
  institution_aliases: string[];
};

export const PLAYERS: Player[] = players as Player[];

export function findPlayer(id: string): Player | undefined {
  return PLAYERS.find((p) => p.id === id);
}

function matchesPlayer(article: Article, p: Player): boolean {
  if (p.source_ids.includes(article.source_id)) return true;
  const tags = article.tags ?? [];
  if (tags.some((t) => p.tags.includes(t))) return true;
  if (article.institution) {
    const inst = article.institution.trim().toLowerCase();
    if (p.institution_aliases.some((a) => a.toLowerCase() === inst)) return true;
  }
  return false;
}

export function articlesForPlayer(p: Player, maxDays = 30): Article[] {
  const recent = loadRecentDays(maxDays);
  return recent
    .filter((a) => matchesPlayer(a, p))
    .sort((a, b) => (b.published ?? "").localeCompare(a.published ?? ""));
}

export function countsForPlayers(maxDays = 30): Record<string, number> {
  const recent = loadRecentDays(maxDays);
  const counts: Record<string, number> = {};
  for (const p of PLAYERS) counts[p.id] = 0;
  for (const a of recent) {
    for (const p of PLAYERS) {
      if (matchesPlayer(a, p)) counts[p.id] = (counts[p.id] ?? 0) + 1;
    }
  }
  return counts;
}

/**
 * Daily mention counts for the last N days (chronological, oldest→newest).
 * Days with zero mentions are included as zero-count entries so the sparkline
 * has a continuous horizontal axis.
 */
export function playerDailyCounts(p: Player, days = 30): { day: string; count: number }[] {
  const arts = articlesForPlayer(p, days);
  const counts = new Map<string, number>();
  for (const a of arts) {
    const day = (a.published ?? a.fetched_at).slice(0, 10);
    counts.set(day, (counts.get(day) ?? 0) + 1);
  }
  const out: { day: string; count: number }[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(today.getUTCDate() - i);
    const day = d.toISOString().slice(0, 10);
    out.push({ day, count: counts.get(day) ?? 0 });
  }
  return out;
}

/**
 * Other players whose articles share cluster_ids or tags with this player.
 * Overlap counts unique shared clusters + unique shared tag co-occurrences.
 */
export function coMentionedPlayers(p: Player, days = 30, limit = 5): Array<{ player: Player; overlap: number }> {
  const myArts = articlesForPlayer(p, days);
  const myClusters = new Set(myArts.map((a) => a.cluster_id).filter(Boolean));
  const myTags = new Set(myArts.flatMap((a) => a.tags ?? []));
  const results: Array<{ player: Player; overlap: number }> = [];
  for (const other of PLAYERS) {
    if (other.id === p.id) continue;
    const otherArts = articlesForPlayer(other, days);
    let overlap = 0;
    const seenClusters = new Set<string>();
    for (const a of otherArts) {
      if (a.cluster_id && myClusters.has(a.cluster_id) && !seenClusters.has(a.cluster_id)) {
        overlap += 2; // same-story co-appearance is stronger signal
        seenClusters.add(a.cluster_id);
      }
      for (const t of a.tags ?? []) {
        if (myTags.has(t)) overlap += 1;
      }
    }
    if (overlap > 0) results.push({ player: other, overlap });
  }
  return results.sort((a, b) => b.overlap - a.overlap).slice(0, limit);
}

/**
 * Top tags across this player's recent articles.
 */
export function playerTopTags(p: Player, days = 30, limit = 8): Array<{ tag: string; count: number }> {
  const arts = articlesForPlayer(p, days);
  const counts = new Map<string, number>();
  for (const a of arts) {
    for (const t of a.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([tag, count]) => ({ tag, count }));
}

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

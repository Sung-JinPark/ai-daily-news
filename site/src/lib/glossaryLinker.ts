import type { Article, GlossaryTerm } from "./loadData";

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const ASCII_RE_LOCAL = /^[\x20-\x7E]+$/;

/** Return the subset of glossary terms that appear in at least one of the given
 *  articles (title, summary, insights). Uses word boundaries for ASCII terms
 *  and literal contains for non-ASCII. */
export function termsAppearingIn(
  terms: GlossaryTerm[],
  articles: Article[],
): GlossaryTerm[] {
  if (!terms.length || !articles.length) return [];
  const haystack = articles
    .map((a) => `${a.title_original} ${a.summary_ko} ${(a.insights_ko ?? []).join(" ")}`)
    .join("\n");
  const hayLower = haystack.toLowerCase();
  return terms.filter((t) => {
    const term = t.term;
    if (ASCII_RE_LOCAL.test(term)) {
      const re = new RegExp(`\\b${term.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}\\b`, "i");
      return re.test(haystack);
    }
    return hayLower.includes(term.toLowerCase());
  });
}

function escapeAttr(s: string): string {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const ASCII_RE = /^[\x20-\x7E]+$/;

/**
 * Wrap first occurrence of each glossary term in `text` with a `<span class="gl">`.
 * The returned string is safe to pass to Astro's set:html.
 *
 * - Longer terms are matched first so "Context Window" wins over "Window".
 * - Each term is matched only on its first occurrence (case-insensitive).
 * - ASCII terms use word boundaries; Korean terms use literal match.
 */
export function linkifyGlossary(text: string, terms: GlossaryTerm[]): string {
  if (!text) return "";
  const escaped = escapeHtml(text);
  if (!terms.length) return escaped;

  const sorted = [...terms].sort((a, b) => b.term.length - a.term.length);
  const patterns = sorted.map((t) =>
    ASCII_RE.test(t.term)
      ? `\\b${escapeRegex(t.term)}\\b`
      : escapeRegex(t.term),
  );
  const re = new RegExp(`(${patterns.join("|")})`, "gi");

  const byLower = new Map<string, GlossaryTerm>();
  for (const t of sorted) {
    const key = t.term.toLowerCase();
    if (!byLower.has(key)) byLower.set(key, t);
  }

  const used = new Set<string>();
  return escaped.replace(re, (match) => {
    const key = match.toLowerCase();
    if (used.has(key)) return match;
    const term = byLower.get(key);
    if (!term) return match;
    used.add(key);
    return `<span class="gl" data-full="${escapeAttr(term.full)}" data-desc="${escapeAttr(term.desc)}">${match}</span>`;
  });
}

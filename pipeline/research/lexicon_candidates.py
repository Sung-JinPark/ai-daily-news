"""Deterministic emergent-term candidates (lexicon growth loop, LLM 0).

Extracts 1-3grams from corpus titles/abstracts (English tokens),
excludes stopwords, entity vocab (TAG_VOCAB names), and anything the
current lexicon already matches, then scores novelty:

    score = freq(last 30 days of data) / (freq(before that) + 1)

(exact formula fixed here; anchored at the LATEST DATA DAY, never
wall clock). Top N candidates land in a private review sheet
``notes/lexicon-candidates-YYYY-MM.md`` with checkboxes; the
researcher marks ``[x]`` and runs lexicon_apply to adopt.

Monthly gate lives here (first day of month, KST, --force bypass) so
run-research.bat calls unconditionally.

Usage: python -m pipeline.research.lexicon_candidates [--force] [--top 30]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.research.research_db import (
    _atomic_write,
    compile_alias,
    iter_news_texts,
    iter_paper_texts,
    open_db,
)

NOTES_DIR = Path("data") / "research_private" / "notes"
KST = timezone(timedelta(hours=9))
RECENT_DAYS = 30
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
STOP = set("""the and for with from that this are was has have will can our your their its new using use used based
model models language large data ai llm llms system systems approach method methods paper propose proposed present
results show shows study learning neural network networks deep machine performance task tasks framework via
towards toward improving improved analysis evaluation experiments arxiv abstract""".split())


def _entity_names() -> set[str]:
    """Lowercased entity/tag vocab names to exclude (they are entities,
    not method concepts)."""
    names: set[str] = set()
    try:
        from pipeline.entity_index import LABS, MODELS  # type: ignore
        for v in (MODELS, LABS):
            for k in v:
                names.add(str(k).lower())
    except Exception:
        pass
    return names


def _lexicon_patterns(conn) -> list:
    return [compile_alias(p) for (p,) in conn.execute("SELECT pattern FROM aliases").fetchall()]


def build_candidates(top_n: int = 30) -> tuple[str, list[dict]]:
    texts = [(d, t) for d, _, f, t in iter_news_texts() if f == "title"]
    texts += [(d, t) for d, _, f, t in iter_paper_texts(enriched_only=True)]
    if not texts:
        return "", []
    anchor = max(d for d, _ in texts)
    recent_start = (datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=RECENT_DAYS - 1)).strftime("%Y-%m-%d")

    conn = open_db()
    lex = _lexicon_patterns(conn)
    conn.close()
    entities = _entity_names()

    recent, prior = Counter(), Counter()
    examples: dict[str, str] = {}
    for day, text in texts:
        toks = [t.lower() for t in TOKEN_RE.findall(text)]
        grams = set()
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i:i + n])
                grams.add(g)
        for g in grams:
            parts = g.split()
            if any(p in STOP for p in parts) or any(p in entities for p in parts):
                continue
            if any(rx.search(g) for rx in lex):
                continue
            if day >= recent_start:
                recent[g] += 1
                examples.setdefault(g, text[:110])
            else:
                prior[g] += 1

    scored = []
    for g, r in recent.items():
        if r < 3:  # noise floor: appears in >=3 recent docs
            continue
        p = prior.get(g, 0)
        scored.append({"term": g, "recent": r, "prior": p,
                       "score": round(r / (p + 1), 2), "example": examples[g]})
    scored.sort(key=lambda x: (-x["score"], -x["recent"], x["term"]))
    return anchor, scored[:top_n]


def write_sheet(anchor: str, cands: list[dict]) -> Path:
    month = anchor[:7]
    out = NOTES_DIR / f"lexicon-candidates-{month}.md"
    lines = [f"# lexicon candidates — {month} (anchor {anchor}, 결정적 n-gram, LLM 0)", "",
             "채택: `[ ]`→`[x]` + kind 기입 후 `python -m pipeline.research.lexicon_apply <이 파일>`", "",
             "| 채택 | term | recent(30d) | prior | score | kind(기입) | 예문 |",
             "|---|---|---:|---:|---:|---|---|"]
    for c in cands:
        ex = c["example"].replace("|", "/")
        lines.append(f"| [ ] | {c['term']} | {c['recent']} | {c['prior']} | {c['score']} |  | {ex} |")
    lines.append("")
    _atomic_write(out, "\n".join(lines) + "\n")
    print(f"[candidates] {len(cands)} -> {out}")
    return out


def run(force: bool = False, top_n: int = 30):
    today = datetime.now(KST)
    if today.day != 1 and not force:
        print(f"[candidates] not the 1st (KST day={today.day}) - skipping. --force to override.")
        return None
    anchor, cands = build_candidates(top_n)
    if not cands:
        print("[candidates] corpus empty or no candidates above noise floor")
        return None
    return write_sheet(anchor, cands)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--force", action="store_true")
    p.add_argument("--top", type=int, default=30)
    a = p.parse_args()
    run(force=a.force, top_n=a.top)

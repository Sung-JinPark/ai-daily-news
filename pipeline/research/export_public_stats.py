"""Sanitized PUBLIC stats export for the site's /stats page.

Writes ``data/research_stats.json`` (git-tracked, public). The local
DBs stay private; this file carries **aggregates only** per the
boundary decision (2026-07-03): counts, trends, and generic kind
distributions — NEVER concept names, alias patterns, or any lexicon
content (the paper's methodology stays unpublished).

A hard sanitization guard enforces that: after building the payload,
the serialized JSON is scanned for every concept_id and canonical_name
from research.db (all states) — any hit aborts the export. Matching is
word-boundary aware for ASCII terms (SG-1, 2026-07-04) so a short
concept token no longer false-positives inside an unrelated word.

Updated nightly by run-research.bat; the scheduled wrapper commits
and pushes it, which triggers the Pages deploy (site auto-refresh).

Usage: python -m pipeline.research.export_public_stats
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.research.research_db import DB_FILE as RESEARCH_DB
from pipeline.research.research_db import _atomic_write

PAPERS_DB = Path("data") / "papers_private" / "papers.db"
OUT = Path("data") / "research_stats.json"
SCHEMA_VERSION = 1


def papers_block() -> dict | None:
    if not PAPERS_DB.exists():
        return None
    c = sqlite3.connect(PAPERS_DB)
    try:
        total = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        enriched = c.execute("SELECT COUNT(*) FROM papers WHERE enriched=1").fetchone()[0]
        kinds = dict(c.execute(
            "SELECT mention_kind, COUNT(*) FROM paper_mentions GROUP BY mention_kind"))
        per_day = [
            {"day": d, "n": n} for d, n in c.execute(
                "SELECT day, COUNT(*) FROM paper_mentions GROUP BY day ORDER BY day")
        ]
        cats = [
            {"category": k, "n": n} for k, n in c.execute(
                "SELECT primary_category, COUNT(*) n FROM papers "
                "WHERE enriched=1 AND primary_category IS NOT NULL "
                "GROUP BY primary_category ORDER BY n DESC, primary_category LIMIT 10")
        ]
    finally:
        c.close()
    refs_days = refs_rows = 0
    for d in sorted(Path("data").glob("2???-??-??"))[-7:]:
        f = d / "arxiv_refs.json"
        if f.exists():
            refs_days += 1
            try:
                refs_rows += len(json.loads(f.read_text(encoding="utf-8"))["refs"])
            except Exception:
                pass
    return {
        "total": total,
        "enriched": enriched,
        "enriched_pct": round(enriched / total * 100, 1) if total else 0.0,
        "mentions": kinds,
        "per_day_mentions": per_day,
        "top_categories": cats,
        "refs_pipe_7d": {"days_covered": refs_days, "rows": refs_rows},
    }


def concepts_block() -> dict | None:
    if not RESEARCH_DB.exists():
        return None
    c = sqlite3.connect(RESEARCH_DB)
    try:
        ver = c.execute("SELECT COALESCE(MAX(version),0) FROM lexicon_versions").fetchone()[0]
        n_concepts = c.execute("SELECT COUNT(*) FROM concepts WHERE status='active'").fetchone()[0]
        n_alias = c.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        by_src = dict(c.execute(
            "SELECT source_type, COUNT(*) FROM latest_mentions GROUP BY source_type"))
        # kind names are the generic taxonomy (method/architecture/task/
        # paradigm) — safe; concept names are NOT.
        kind_dist = dict(c.execute(
            "SELECT kind, COUNT(*) FROM concepts WHERE status='active' GROUP BY kind"))
        per_day = [
            {"day": d, "news": nn or 0, "paper": np or 0}
            for d, nn, np in c.execute(
                "SELECT day, SUM(CASE WHEN source_type='news' THEN 1 END), "
                "SUM(CASE WHEN source_type='paper' THEN 1 END) "
                "FROM latest_mentions GROUP BY day ORDER BY day")
        ]
        pairs = c.execute("SELECT COUNT(*) FROM concept_pairs").fetchone()[0]
        revivals = c.execute("SELECT COUNT(*) FROM revival_events").fetchone()[0]
        both = c.execute(
            "SELECT COUNT(*) FROM media_lag WHERE news_minus_paper_days IS NOT NULL"
        ).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        c.close()
    return {
        "lexicon_version": int(ver),
        "active_concepts": n_concepts,
        "alias_count": n_alias,
        "mentions": {"news": by_src.get("news", 0), "paper": by_src.get("paper", 0)},
        "kind_distribution": kind_dist,
        "per_day_mentions": per_day,
        "cooccurrence_pairs": pairs,
        "revival_events": revivals,
        "media_lag_observed": both,
    }


def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def _make_probe(term: str):
    r"""Return callable(payload_lower) -> bool detecting ``term``.

    SG-1 (2026-07-04): ASCII terms match on **word boundaries**
    (phrase-aware — internal whitespace tolerates any run) so a short
    concept token no longer false-positives as a substring of an
    unrelated English word (e.g. a token inside "education"). Non-ASCII
    terms (e.g. Korean) fall back to literal containment because ``\b``
    is ill-defined there — keeping detection so a real leak is never
    under-matched. Mirrors site/src/lib/glossaryLinker.ts.
    """
    t = term.strip().lower()
    if not t:
        return lambda _p: False
    if _is_ascii(t):
        pat = r"\b" + r"\s+".join(re.escape(w) for w in t.split()) + r"\b"
        rx = re.compile(pat)
        return lambda p: rx.search(p) is not None
    return lambda p: t in p


def _sanitize_check(serialized: str) -> None:
    """Abort if any lexicon content leaked into the public payload.

    Scans the serialized payload for every concept_id / canonical_name in
    research.db (ALL states, not just active). Any hit raises SystemExit
    *before* the atomic write, so nothing is written. The error message
    deliberately omits the offending term — logs / stack traces may be a
    public surface.
    """
    if not RESEARCH_DB.exists():
        return
    c = sqlite3.connect(RESEARCH_DB)
    try:
        names = [
            (cid, cname) for cid, cname in c.execute(
                "SELECT concept_id, canonical_name FROM concepts")
        ]
    finally:
        c.close()
    low = serialized.lower()
    for cid, cname in names:
        for token in (cid, cname):
            if token and len(str(token)) >= 3 and _make_probe(str(token))(low):
                raise SystemExit(
                    "sanitize guard: a concept term leaked into "
                    "research_stats.json payload; export aborted"
                )


def run() -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "papers": papers_block(),
        "concepts": concepts_block(),
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    _sanitize_check(serialized)
    _atomic_write(OUT, serialized + "\n")
    p = payload["papers"] or {}
    k = payload["concepts"] or {}
    print(f"[public-stats] papers={p.get('total')} enriched={p.get('enriched')} "
          f"concepts={k.get('active_concepts')} -> {OUT}")
    return OUT


if __name__ == "__main__":
    run()

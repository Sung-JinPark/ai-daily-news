"""Deterministic concept matcher — fills the concept_mentions ledger.

Matches the private lexicon (research.db aliases) against the dual
corpus (D3): news = data/<day>/articles.json title_original+summary_ko;
paper = papers.db title (+ abstract when enriched). enriched=0 papers
contribute title-only rows and their abstract coverage arrives
incrementally as the nightly enrich drains — reruns are idempotent
upserts, so re-running after enrichment simply adds the new abstract
mentions (logged as skipped_abstracts until then).

Re-backfill contract (RDB-2): a lexicon version bump ADDS rows under
the new version — old-version rows are preserved (version-to-version
comparison is itself research data). ``latest_mentions`` view serves
analysis.

Determinism: patterns compiled once, corpora iterated in sorted order,
PK dedupe; match_text keeps the first-observed surface form.

Usage:
    python -m pipeline.research.concept_extract --backfill [--source all]
    python -m pipeline.research.concept_extract --day 2026-07-01 --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3

from pipeline.research.research_db import (
    DB_FILE,
    compile_alias,
    iter_news_texts,
    iter_paper_texts,
    open_db,
)


def load_patterns(conn: sqlite3.Connection, version: int | None) -> tuple[int, list]:
    if version is None:
        row = conn.execute("SELECT MAX(version) FROM lexicon_versions").fetchone()
        if not row or row[0] is None:
            raise SystemExit("no lexicon version — run research_db seed first")
        version = int(row[0])
    pats = [
        (cid, compile_alias(pattern))
        for cid, pattern in conn.execute(
            "SELECT concept_id, pattern FROM aliases WHERE added_version <= ?", (version,)
        ).fetchall()
    ]
    return version, pats


def scan(texts, source_type: str, patterns, version: int, conn, dry_run: bool,
         day_filter: str | None) -> dict:
    stats = {"rows_scanned": 0, "mentions_new": 0, "mentions_dup": 0}
    cur = conn.cursor()
    current_day = None
    for day, sid, field, text in texts:
        if day_filter and day != day_filter:
            continue
        stats["rows_scanned"] += 1
        # transaction per news day / whole paper batch (atomicity unit)
        if source_type == "news" and day != current_day:
            if not dry_run and current_day is not None:
                conn.commit()
            current_day = day
        for cid, rx in patterns:
            m = rx.search(text)
            if not m:
                continue
            if dry_run:
                stats["mentions_new"] += 1
                continue
            cur.execute(
                """INSERT OR IGNORE INTO concept_mentions
                   (concept_id, source_type, source_id, day, field, match_text, lexicon_version)
                   VALUES (?,?,?,?,?,?,?)""",
                (cid, source_type, sid, day, field, m.group(0), version),
            )
            if cur.rowcount > 0:
                stats["mentions_new"] += 1
            else:
                stats["mentions_dup"] += 1
    if not dry_run:
        conn.commit()
    return stats


def run(source: str, day: str | None, version: int | None, dry_run: bool) -> dict:
    conn = open_db()
    try:
        version, patterns = load_patterns(conn, version)
        out = {"lexicon_version": version}
        if source in ("news", "all"):
            out["news"] = scan(iter_news_texts(), "news", patterns, version, conn, dry_run, day)
            # F2 (instrument v3): English body corpus (private ledger built
            # by en_corpus.py from the committed bodies.jsonl) — field=body_en.
            from pipeline.research.en_corpus import iter_en_texts
            out["news_body_en"] = scan(iter_en_texts(), "news", patterns, version, conn, dry_run, day)
        if source in ("paper", "all"):
            out["paper"] = scan(iter_paper_texts(enriched_only=True), "paper", patterns,
                                version, conn, dry_run, day)
            # honesty count: papers whose abstract is still pending
            import sqlite3 as s
            from pipeline.research.research_db import PAPERS_DB
            if PAPERS_DB.exists():
                c2 = s.connect(PAPERS_DB)
                out["papers_without_abstract"] = c2.execute(
                    "SELECT COUNT(*) FROM papers WHERE enriched=0").fetchone()[0]
                c2.close()
        for k, v in out.items():
            print(f"[extract] {k}: {v}")
        if not dry_run:
            top = conn.execute(
                """SELECT concept_id, COUNT(*) n FROM latest_mentions
                   GROUP BY concept_id ORDER BY n DESC, concept_id LIMIT 10"""
            ).fetchall()
            print("[extract] top concepts:", top)
    finally:
        conn.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--backfill", action="store_true")
    p.add_argument("--day", default=None)
    p.add_argument("--source", choices=["news", "paper", "all"], default="all")
    p.add_argument("--lexicon-version", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if not a.backfill and not a.day:
        p.error("--backfill or --day required")
    run(a.source, a.day, a.lexicon_version, a.dry_run)


if __name__ == "__main__":
    main()

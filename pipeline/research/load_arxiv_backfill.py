"""BACK-1 Phase 1 loader — promote staged all-arXiv harvest into papers.db.

Population redefinition (㉠): papers.db becomes the all-arXiv-cs record for the
harvested window. The pre-existing news-mentioned papers are preserved as a FLAGGED
SUBSET — a paper is "news-amplified" iff it has a paper_mentions row (that table is
untouched here). New all-arXiv rows carry published=created and first_seen_day=
created (publication day); existing rows keep their news-derived first_seen_day.

Idempotent upsert. Reads staging JSONL(s) produced by harvest_arxiv.py.
Does NOT fetch anything. Run AFTER a papers.db rollback snapshot exists.

Usage: python -m pipeline.research.load_arxiv_backfill data/research_private/back1_staging/arxiv_harvest.jsonl [more.jsonl ...]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path("data") / "papers_private" / "papers.db"


def load(paths: list[str]) -> dict:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # population annotation (queryable): origin column, default 'news' for the
    # pre-existing rows; new all-arXiv rows are 'oai_backfill'. Idempotent.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    if "origin" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN origin TEXT DEFAULT 'news'")
        conn.commit()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st = {"seen": 0, "inserted": 0, "updated": 0, "bad": 0}
    for path in paths:
        for line in Path(path).open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            aid = r.get("arxiv_id")
            created = r.get("created") or ""
            if not aid or not created:
                st["bad"] += 1
                continue
            st["seen"] += 1
            day = created[:10]
            cats_json = json.dumps(r.get("categories") or [], ensure_ascii=False)
            authors_json = json.dumps([a for a in (r.get("authors") or []) if a], ensure_ascii=False)
            row = cur.execute("SELECT first_seen_day FROM papers WHERE arxiv_id=?", (aid,)).fetchone()
            if row is None:
                cur.execute(
                    """INSERT INTO papers (arxiv_id, title, abstract, primary_category,
                         categories_json, published, updated, first_seen_day, last_seen_day,
                         seen_count, tags_json, importance_max, enriched, enriched_at,
                         schema_version, authors_json, origin)
                       VALUES (?,?,?,?,?,?,?,?,?,0,'[]',0,1,?,2,?,'oai_backfill')""",
                    (aid, r.get("title"), r.get("abstract"), r.get("primary_category"),
                     cats_json, created, r.get("updated"), day, day, now, authors_json))
                st["inserted"] += 1
            else:
                # keep news-derived first_seen_day; fill/refresh metadata + published
                cur.execute(
                    """UPDATE papers SET
                         title=COALESCE(title, ?), abstract=COALESCE(abstract, ?),
                         primary_category=?, categories_json=?, published=?, updated=?,
                         authors_json=CASE WHEN authors_json IN ('','[]') THEN ? ELSE authors_json END,
                         enriched=1, enriched_at=COALESCE(enriched_at, ?)
                       WHERE arxiv_id=?""",
                    (r.get("title"), r.get("abstract"), r.get("primary_category"), cats_json,
                     created, r.get("updated"), authors_json, now, aid))
                st["updated"] += 1
        conn.commit()
    # summary counts
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    news_amp = conn.execute(
        "SELECT COUNT(DISTINCT p.arxiv_id) FROM papers p JOIN paper_mentions m USING(arxiv_id)").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('back1_population', ?)",
                 (json.dumps({"redefinition": "all-arXiv-cs", "loaded_at": now,
                              "news_amplified_subset": news_amp, "total_papers": total}, ensure_ascii=False),))
    conn.commit()
    conn.close()
    st["total_papers"] = total
    st["news_amplified_subset"] = news_amp
    print(f"[load] seen={st['seen']} inserted={st['inserted']} updated={st['updated']} bad={st['bad']} "
          f"-> papers.db total={total} (news-amplified subset={news_amp})")
    return st


if __name__ == "__main__":
    paths = sys.argv[1:] or [str(Path("data") / "research_private" / "back1_staging" / "arxiv_harvest.jsonl")]
    load(paths)

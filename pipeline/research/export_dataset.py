"""Standard long-format exports — the paper's analysis interface.

Dumps the ledger + derived features to
``data/research_private/exports/`` so any analysis tool (pandas, R,
igraph, survival packages) can consume them without touching SQLite.
Deterministic (sorted, no timestamps inside files). --format csv adds
CSV twins for stats packages.

Also exports a cold research.db checkpoint into
``data/research_private/db_exports/research-YYYY-MM-DD.db`` (same
sqlite3-backup-API + retention pattern as export_papers_db) — the
tree gcs_sync already mirrors, so backups ride the nightly sync.

Usage: python -m pipeline.research.export_dataset [--format csv]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from pipeline.research.research_db import DB_FILE
from pipeline.research.export_papers_db import NAME_RE  # reuse retention regex shape

EXPORT_DIR = Path("data") / "research_private" / "exports"
DB_EXPORT_DIR = Path("data") / "research_private" / "db_exports"
KST = timezone(timedelta(hours=9))

QUERIES = {
    "mentions": "SELECT * FROM latest_mentions ORDER BY concept_id, source_type, source_id, field",
    "daily_counts": "SELECT * FROM daily_concept_counts ORDER BY concept_id, source_type, day",
    "concept_pairs": "SELECT * FROM concept_pairs ORDER BY concept_a, concept_b, source_type, source_id",
    "concept_spans": "SELECT * FROM concept_spans ORDER BY concept_id",
    "revival_events": "SELECT * FROM revival_events ORDER BY revived_day, concept_id",
    "novelty_series": "SELECT * FROM novelty_series ORDER BY month",
    "breadth_depth": "SELECT * FROM breadth_depth ORDER BY concept_id, month",
    "media_lag": "SELECT * FROM media_lag ORDER BY concept_id",
}


def export_tables(fmt_csv: bool = False) -> dict:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    stats = {}
    try:
        for name, q in QUERIES.items():
            df = pd.read_sql_query(q, conn)
            df.to_parquet(EXPORT_DIR / f"{name}.parquet", index=False)
            if fmt_csv:
                df.to_csv(EXPORT_DIR / f"{name}.csv", index=False, encoding="utf-8")
            stats[name] = len(df)
    finally:
        conn.close()
    print(f"[export] {stats} -> {EXPORT_DIR}")
    return stats


def export_db_checkpoint() -> Path | None:
    """Cold copy of research.db (pattern: export_papers_db)."""
    if not DB_FILE.exists():
        return None
    day = datetime.now(KST).strftime("%Y-%m-%d")
    DB_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dst_path = DB_EXPORT_DIR / f"research-{day}.db"
    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    # verify
    c = sqlite3.connect(dst_path)
    ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    # retention: last 7 days + Mondays (same policy as papers export)
    import re
    rx = re.compile(r"^research-(\d{4}-\d{2}-\d{2})\.db$")
    today_dt = datetime.strptime(day, "%Y-%m-%d")
    for p in sorted(DB_EXPORT_DIR.glob("research-*.db")):
        m = rx.match(p.name)
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%Y-%m-%d")
        if (today_dt - d).days > 7 and d.weekday() != 0:
            p.unlink()
    print(f"[export] research.db checkpoint -> {dst_path} (integrity={'ok' if ok else 'FAIL'})")
    return dst_path if ok else None


def export_corpus_descriptives() -> Path:
    """T5 (two-track plan §8): descriptive statistics of BOTH corpora
    for the paper's methodology section. Article definitions are pinned
    D1~D7 (same as the site's build-time computeArticleStats — the plan
    §2 table is the single source of truth for both implementations).
    Deterministic: sorted keys/lists, no timestamps inside the payload.
    """
    import json

    # --- article corpus (public day files; pinned D1~D6, D7: no tags) ---
    data_root = Path("data")
    days = sorted(p.name for p in data_root.glob("2???-??-??")
                  if (p / "articles.json").exists())
    ids: set[str] = set()
    row_sum = 0
    per_day = []
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    # D2: regroup by cluster_id — never aggregate the per-article
    # cluster_size/also_covered_by snapshots.
    cluster_n: dict[str, int] = {}
    cluster_src: dict[str, set] = {}
    hist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "missing": 0}
    for d in days:
        arts = json.loads((data_root / d / "articles.json").read_text(encoding="utf-8"))
        row_sum += len(arts)
        per_day.append({"day": d, "n": len(arts)})  # D3
        for a in arts:
            ids.add(a["id"])
            by_source[a["source_id"]] = by_source.get(a["source_id"], 0) + 1
            by_category[a.get("category", "")] = by_category.get(a.get("category", ""), 0) + 1
            imp = a.get("importance_score")
            if isinstance(imp, (int, float)) and 1 <= imp <= 5:
                hist[str(int(round(imp)))] += 1
            else:
                hist["missing"] += 1
            cid = a.get("cluster_id")
            if cid:
                cluster_n[cid] = cluster_n.get(cid, 0) + 1
                cluster_src.setdefault(cid, set()).add(a["source_id"])
    multi = sum(1 for s in cluster_src.values() if len(s) >= 2)
    articles_block = {
        "total_distinct_ids": len(ids),          # D1
        "row_sum": row_sum,
        "per_day": per_day,
        "sources": {
            "active": len(by_source),
            # D5: count desc, source_id asc
            "counts": [{"source_id": s, "n": n} for s, n in
                       sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))],
        },
        "categories": [{"category": c, "n": n} for c, n in
                       sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))],
        "clusters": {
            "total": len(cluster_n),
            "multi_source": multi,
            "multi_pct": round(multi / len(cluster_n) * 100, 1) if cluster_n else 0.0,
            "avg_size": round(sum(cluster_n.values()) / len(cluster_n), 1) if cluster_n else 0.0,
        },
        "importance_hist": hist,                  # D6
        "span": {"first": days[0], "last": days[-1], "days": len(days)} if days else None,
    }

    # --- paper corpus (papers.db) ---
    papers_block = None
    papers_db = Path("data") / "papers_private" / "papers.db"
    if papers_db.exists():
        c = sqlite3.connect(papers_db)
        try:
            total = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            enriched = c.execute("SELECT COUNT(*) FROM papers WHERE enriched=1").fetchone()[0]
            span = c.execute("SELECT MIN(day), MAX(day) FROM paper_mentions").fetchone()
            cats = [{"category": k, "n": n} for k, n in c.execute(
                "SELECT primary_category, COUNT(*) FROM papers "
                "WHERE enriched=1 AND primary_category IS NOT NULL "
                "GROUP BY primary_category ORDER BY COUNT(*) DESC, primary_category")]
        finally:
            c.close()
        papers_block = {"total": total, "enriched": enriched,
                        "span": {"first": span[0], "last": span[1]},
                        "categories_enriched": cats}

    payload = {"schema_version": 1, "definitions": "two-track plan §2 + pins D1~D7",
               "articles": articles_block, "papers": papers_block}
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / "corpus_descriptives.json"
    from pipeline.research.research_db import _atomic_write
    _atomic_write(out, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"[export] corpus_descriptives: articles={articles_block['total_distinct_ids']} "
          f"papers={papers_block['total'] if papers_block else None} -> {out}")
    return out


def run(fmt_csv: bool = False) -> bool:
    export_tables(fmt_csv)
    export_corpus_descriptives()
    return export_db_checkpoint() is not None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                   help="csv additionally writes CSV twins")
    a = p.parse_args()
    raise SystemExit(0 if run(fmt_csv=(a.format == "csv")) else 1)


if __name__ == "__main__":
    main()

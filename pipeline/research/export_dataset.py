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


def run(fmt_csv: bool = False) -> bool:
    export_tables(fmt_csv)
    return export_db_checkpoint() is not None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                   help="csv additionally writes CSV twins")
    a = p.parse_args()
    raise SystemExit(0 if run(fmt_csv=(a.format == "csv")) else 1)


if __name__ == "__main__":
    main()

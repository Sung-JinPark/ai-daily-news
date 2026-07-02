"""Cold checkpoint export of the private papers.db.

D-4 deferred backing up papers.db because gcs_sync's naive file walk
could capture a torn snapshot of a hot SQLite file. This module closes
that gap: it produces a **consistent cold copy** via the sqlite3
backup API (transaction-safe even if a writer is mid-flight) into

    data/research_private/db_exports/papers-YYYY-MM-DD.db

which sits inside the tree gcs_sync already mirrors — so the nightly
GCS backup picks the exports up with zero further wiring.

Retention (local disk protection): keep every export from the last 7
days plus every Monday export; delete the rest. Idempotent — the same
day re-exports over its own file.

Usage:
    python -m pipeline.research.export_papers_db [--keep-days 7]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_DB = Path("data") / "papers_private" / "papers.db"
EXPORT_DIR = Path("data") / "research_private" / "db_exports"
KEEP_DAYS = 7

KST = timezone(timedelta(hours=9))
NAME_RE = re.compile(r"^papers-(\d{4}-\d{2}-\d{2})\.db$")


def export(day: str | None = None) -> Path | None:
    """Write the cold copy for ``day`` (default: today KST). Returns
    the export path, or None when the source DB doesn't exist."""
    if not SRC_DB.exists():
        print(f"[export] {SRC_DB} does not exist - nothing to export")
        return None
    day = day or datetime.now(KST).strftime("%Y-%m-%d")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dst_path = EXPORT_DIR / f"papers-{day}.db"
    src = sqlite3.connect(SRC_DB)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"[export] {SRC_DB} -> {dst_path} ({dst_path.stat().st_size:,} bytes)")
    return dst_path


def verify(path: Path) -> bool:
    """integrity_check == ok AND row counts match the source."""
    conn = sqlite3.connect(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        n_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        n_mentions = conn.execute("SELECT COUNT(*) FROM paper_mentions").fetchone()[0]
    finally:
        conn.close()
    src = sqlite3.connect(SRC_DB)
    try:
        s_papers = src.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        s_mentions = src.execute("SELECT COUNT(*) FROM paper_mentions").fetchone()[0]
    finally:
        src.close()
    ok = integrity == "ok" and n_papers == s_papers and n_mentions == s_mentions
    print(
        f"[verify] integrity={integrity} papers={n_papers}/{s_papers} "
        f"mentions={n_mentions}/{s_mentions} -> {'OK' if ok else 'MISMATCH'}"
    )
    return ok


def prune(keep_days: int = KEEP_DAYS, today: str | None = None) -> list[Path]:
    """Delete exports older than ``keep_days`` unless they fall on a
    Monday. Returns the deleted paths."""
    if not EXPORT_DIR.exists():
        return []
    today_dt = datetime.strptime(
        today or datetime.now(KST).strftime("%Y-%m-%d"), "%Y-%m-%d"
    )
    deleted: list[Path] = []
    for p in sorted(EXPORT_DIR.glob("papers-*.db")):
        m = NAME_RE.match(p.name)
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%Y-%m-%d")
        age = (today_dt - d).days
        if age <= keep_days:
            continue
        if d.weekday() == 0:  # Monday exports are kept indefinitely
            continue
        p.unlink()
        deleted.append(p)
        print(f"[prune] deleted {p.name} (age {age}d)")
    return deleted


def run(keep_days: int = KEEP_DAYS) -> bool:
    path = export()
    if path is None:
        return True  # nothing to do is not a failure
    ok = verify(path)
    prune(keep_days)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    args = parser.parse_args()
    raise SystemExit(0 if run(keep_days=args.keep_days) else 1)


if __name__ == "__main__":
    main()

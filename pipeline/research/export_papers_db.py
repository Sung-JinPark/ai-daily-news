"""Cold checkpoint export of the private papers.db.

D-4 deferred backing up papers.db because gcs_sync's naive file walk
could capture a torn snapshot of a hot SQLite file. This module closes
that gap: it produces a **consistent cold copy** via the sqlite3
backup API (transaction-safe even if a writer is mid-flight) into

    data/research_private/db_exports/papers-YYYY-MM-DD.db

which sits inside the tree gcs_sync mirrors. NOTE: gcs_sync is not
wired into any workflow today (see notes/decisions.md "gcs_sync는
미사용 잔존"), so these exports are currently the only copies - treat
the retention floor below as the actual backup depth, not a cache.

Retention (local disk protection): keep every export from the last
``keep_days`` days, the most recent ``keep_mondays`` Monday exports, and
the most recent ``keep_monthly`` month-anchor exports (the first Monday
present in a calendar month). Delete the rest. Idempotent - the same
day re-exports over its own file.

Before 2026-09-28 every Monday was kept indefinitely, which grew the
directory by ~283MB/week without bound (19 files / 4.5GB when capped).

Usage:
    python -m pipeline.research.export_papers_db         [--keep-days 7] [--keep-mondays 4] [--keep-monthly 3]
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
KEEP_MONDAYS = 4
KEEP_MONTHLY = 3

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


def prune(
    keep_days: int = KEEP_DAYS,
    keep_mondays: int = KEEP_MONDAYS,
    keep_monthly: int = KEEP_MONTHLY,
    today: str | None = None,
) -> list[Path]:
    """Delete exports that fall outside every retention window.

    Kept: exports from the last ``keep_days`` days, the most recent
    ``keep_mondays`` Monday exports, and the most recent ``keep_monthly``
    month anchors (the first Monday present in a calendar month). The
    Monday windows are capped so the directory cannot grow without bound.
    Returns the deleted paths.
    """
    if not EXPORT_DIR.exists():
        return []
    today_dt = datetime.strptime(
        today or datetime.now(KST).strftime("%Y-%m-%d"), "%Y-%m-%d"
    )
    dated: list[tuple[datetime, Path]] = []
    for p in sorted(EXPORT_DIR.glob("papers-*.db")):
        m = NAME_RE.match(p.name)
        if m:
            dated.append((datetime.strptime(m.group(1), "%Y-%m-%d"), p))

    mondays = sorted({d for d, _ in dated if d.weekday() == 0}, reverse=True)
    keep_weekly = set(mondays[:keep_mondays]) if keep_mondays > 0 else set()
    anchors: dict[tuple[int, int], datetime] = {}
    for d in reversed(mondays):  # oldest first -> first Monday of each month wins
        anchors.setdefault((d.year, d.month), d)
    keep_anchors = (
        set(sorted(anchors.values(), reverse=True)[:keep_monthly])
        if keep_monthly > 0
        else set()
    )

    deleted: list[Path] = []
    for d, path in dated:
        age = (today_dt - d).days
        if age <= keep_days or d in keep_weekly or d in keep_anchors:
            continue
        path.unlink()
        deleted.append(path)
        print(f"[prune] deleted {path.name} (age {age}d)")
    return deleted


def run(
    keep_days: int = KEEP_DAYS,
    keep_mondays: int = KEEP_MONDAYS,
    keep_monthly: int = KEEP_MONTHLY,
) -> bool:
    path = export()
    if path is None:
        return True  # nothing to do is not a failure
    ok = verify(path)
    prune(keep_days, keep_mondays, keep_monthly)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    parser.add_argument(
        "--keep-mondays", type=int, default=KEEP_MONDAYS,
        help="how many recent Monday exports to keep (0 = none)",
    )
    parser.add_argument(
        "--keep-monthly", type=int, default=KEEP_MONTHLY,
        help="how many month anchors (first Monday of a month) to keep",
    )
    parser.add_argument(
        "--prune-only", action="store_true",
        help="apply retention without writing a new export",
    )
    args = parser.parse_args()
    if args.prune_only:
        prune(args.keep_days, args.keep_mondays, args.keep_monthly)
        raise SystemExit(0)
    raise SystemExit(
        0 if run(
            keep_days=args.keep_days,
            keep_mondays=args.keep_mondays,
            keep_monthly=args.keep_monthly,
        ) else 1
    )


if __name__ == "__main__":
    main()

"""One-shot backfill for X1: populate ``first_url_hash`` /
``first_published`` on every ``data/cluster_continuity.json`` entry.

Reads all ``data/YYYY-MM-DD/articles.json`` files, groups article
(published, url_hash) tuples by cluster_id, computes the deterministic
minimum per cluster, and writes the values back to the continuity
entry — no matter how it was originally created. Idempotent: re-running
never changes a value that is already correct.

Run once before enabling stable-slug URLs, and again any time the
continuity file is regenerated from scratch.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

from pipeline.dedupe import (
    CONTINUITY_FILE,
    deterministic_first_key,
    load_continuity,
    save_continuity,
)

log = logging.getLogger(__name__)
DATA_DIR = Path("data")
DATE_RE = "^\\d{4}-\\d{2}-\\d{2}$"


def _iter_day_dirs():
    import re
    if not DATA_DIR.exists():
        return
    for p in sorted(DATA_DIR.iterdir()):
        if p.is_dir() and re.match(DATE_RE, p.name):
            yield p


def _articles_by_cluster() -> dict[str, list[dict]]:
    """Return {cluster_id: [article, ...]} across every archived day.

    Each article contributes its (published, url) so the deterministic
    minimum is computable exactly the same way ``dedupe.py`` computes it
    online, regardless of order.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for day_dir in _iter_day_dirs():
        p = day_dir / "articles.json"
        if not p.exists():
            continue
        try:
            arts = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for a in arts:
            cid = a.get("cluster_id") or ""
            if not cid:
                continue
            grouped[cid].append({"url": a.get("url", ""), "published": a.get("published")})
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    continuity = load_continuity()
    entries = continuity.get("entries", []) or []
    grouped = _articles_by_cluster()
    log.info("backfill: %d continuity entries, %d clusters observed in archive",
             len(entries), len(grouped))

    updated = 0
    added = 0
    unchanged = 0
    for e in entries:
        cid = e.get("cluster_id", "")
        members = grouped.get(cid, [])
        if not members:
            continue
        published, uh = deterministic_first_key(members)
        if not uh:
            continue
        old_pub = e.get("first_published") or ""
        old_uh = e.get("first_url_hash") or ""
        if not old_uh:
            added += 1
        elif (old_pub, old_uh) != (published, uh):
            updated += 1
        else:
            unchanged += 1
            continue
        if not args.dry_run:
            e["first_published"] = published
            e["first_url_hash"] = uh

    log.info(
        "backfill result: %d added, %d updated (moved earlier), %d unchanged, %d skipped (no articles)",
        added, updated, unchanged, len(entries) - added - updated - unchanged,
    )
    if not args.dry_run:
        save_continuity(continuity)
        log.info("wrote %s", CONTINUITY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backfill ``first_url_hash`` / ``first_published`` on every
``data/cluster_continuity.json`` entry that does not have one yet.

Y1 (F-1 + F-2) semantics:

* **Freeze**: once an entry carries ``first_url_hash`` it is never
  overwritten. The script only *adds* values, never *updates* them.
* **Broader source of truth**: the deterministic minimum is computed
  from BOTH ``data/YYYY-MM-DD/articles.json`` (representative per day)
  AND ``data/corpus/YYYY-MM-DD/members.jsonl`` (every cluster member
  from every day's dedupe output). Members were previously only
  reachable via the gitignored ``raw/`` tree; M1 committed them under
  ``data/corpus/`` so extended backfill can now see every article a
  cluster ever contained.

Clusters that surface only in older ``articles.json`` under a legacy
``c…`` id (pre-k-prefix scheme) are not in continuity at all — the
site-side ``clusterSlug`` helper computes their stable slug on the
fly using the same deterministic key rule, so they still get a
canonical ``s-<hash>`` URL without needing a continuity entry.
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


def _iter_day_dirs(root: Path = DATA_DIR):
    import re
    if not root.exists():
        return
    for p in sorted(root.iterdir()):
        if p.is_dir() and re.match(DATE_RE, p.name):
            yield p


def _members_by_cluster() -> dict[str, list[dict]]:
    """Return {cluster_id: [{url, published}, ...]} across every archived
    day, unioning ``articles.json`` (representatives) with
    ``corpus/members.jsonl`` (every dedupe member).
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    # Source 1: representative articles.
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
    # Source 2 (Y1 extension): every dedupe member from corpus/members.
    corpus_root = DATA_DIR / "corpus"
    if corpus_root.exists():
        for day_dir in _iter_day_dirs(corpus_root):
            p = day_dir / "members.jsonl"
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                cid = m.get("cluster_id") or ""
                if not cid:
                    continue
                if not m.get("url"):
                    continue
                grouped[cid].append({"url": m["url"], "published": m.get("published")})
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    continuity = load_continuity()
    entries = continuity.get("entries", []) or []
    grouped = _members_by_cluster()
    log.info(
        "backfill: %d continuity entries, %d clusters seen in archive (articles.json ∪ corpus/members.jsonl)",
        len(entries), len(grouped),
    )

    added = 0
    already_frozen = 0
    no_members = 0
    for e in entries:
        cid = e.get("cluster_id", "")
        if e.get("first_url_hash"):
            already_frozen += 1
            continue
        members = grouped.get(cid, [])
        if not members:
            no_members += 1
            continue
        published, uh = deterministic_first_key(members)
        if not uh:
            no_members += 1
            continue
        if not args.dry_run:
            e["first_published"] = published
            e["first_url_hash"] = uh
        added += 1

    log.info(
        "backfill result: %d added, %d already frozen, %d skipped (no members)",
        added, already_frozen, no_members,
    )
    if not args.dry_run:
        save_continuity(continuity)
        log.info("wrote %s", CONTINUITY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

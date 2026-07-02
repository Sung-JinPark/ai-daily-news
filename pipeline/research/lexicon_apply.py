"""Apply researcher-approved lexicon candidates (growth loop, RDB-4).

Reads a candidates sheet, takes rows marked ``[x]`` (kind column
required), adds each as a concept with a word-boundary plain alias,
bumps lexicon_versions, and re-runs the full backfill under the new
version (old-version rows preserved per the RDB-2 contract).

Usage: python -m pipeline.research.lexicon_apply notes/lexicon-candidates-YYYY-MM.md
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from pipeline.research.concept_extract import run as extract_run
from pipeline.research.research_db import open_db

ROW_RE = re.compile(r"^\|\s*\[x\]\s*\|\s*([^|]+?)\s*\|[^|]*\|[^|]*\|[^|]*\|\s*([^|]*?)\s*\|", re.IGNORECASE)
VALID_KINDS = {"method", "architecture", "task", "paradigm"}


def parse_approved(sheet: Path) -> list[dict]:
    approved = []
    for line in sheet.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        term, kind = m.group(1).strip(), m.group(2).strip().lower()
        if kind not in VALID_KINDS:
            raise SystemExit(f"approved term '{term}' has invalid/missing kind '{kind}' "
                             f"(required: {sorted(VALID_KINDS)})")
        approved.append({"term": term, "kind": kind})
    return approved


def apply(sheet: Path) -> dict:
    approved = parse_approved(sheet)
    if not approved:
        print("[apply] no [x] rows - nothing to do")
        return {"applied": 0}
    conn = open_db()
    try:
        new_ver = int(conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM lexicon_versions").fetchone()[0])
        for a in approved:
            cid = re.sub(r"[^a-z0-9]+", "-", a["term"].lower()).strip("-")
            pattern = r"\b" + re.escape(a["term"]) + r"\b"
            conn.execute(
                "INSERT OR IGNORE INTO concepts(concept_id, canonical_name, kind, first_lexicon_version) VALUES (?,?,?,?)",
                (cid, a["term"], a["kind"], new_ver))
            conn.execute(
                "INSERT OR IGNORE INTO aliases(concept_id, pattern, pattern_kind, added_version) VALUES (?,?,?,?)",
                (cid, pattern, "plain", new_ver))
        n_concepts = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO lexicon_versions(version, created_at, note, concept_count) VALUES (?,?,?,?)",
            (new_ver, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             f"applied {len(approved)} from {sheet.name}", n_concepts))
        conn.commit()
    finally:
        conn.close()
    print(f"[apply] version={new_ver} added={len(approved)} -> full re-backfill under v{new_ver}")
    extract_run(source="all", day=None, version=new_ver, dry_run=False)
    return {"applied": len(approved), "version": new_ver}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sheet", type=Path)
    a = p.parse_args()
    apply(a.sheet)

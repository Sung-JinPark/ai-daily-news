"""Lexicon bootstrap for CI (GitHub-only, no external store).

The Actions-cache holds the working private state across nightly runs, but a cache
miss (first run, or eviction) would lose the **hand-curated lexicon** — the one
piece that is NOT re-derivable from the committed public data. This module ferries
just the lexicon (concepts + aliases + versions — small, ~KB) through a GitHub
Secret (`LEXICON_SEED_B64`, itself encrypted at rest). On a cold start CI imports it,
then `concept_extract --backfill` rebuilds the mentions from the committed public
day files. Nothing sensitive touches the public repo or public logs.

Usage:
    python -m pipeline.research.lexicon_seed --export-b64   # -> paste into the Secret (run locally)
    python -m pipeline.research.lexicon_seed --import-b64   # CI: seed only if lexicon absent (reads $LEXICON_SEED_B64)
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys

from pipeline.research.research_db import DB_FILE, open_db

TABLES = ["concepts", "aliases", "lexicon_versions"]


def export_seed() -> str:
    conn = sqlite3.connect(DB_FILE)
    data = {}
    for t in TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        data[t] = [dict(zip(cols, row)) for row in conn.execute(f"SELECT * FROM {t}")]
    conn.close()
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def import_seed(payload: str) -> dict:
    data = json.loads(payload)
    conn = open_db()  # ensures the schema exists
    st = {}
    for t in TABLES:
        rows = data.get(t) or []
        if not rows:
            continue
        cols = list(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        conn.executemany(
            f"INSERT OR IGNORE INTO {t} ({','.join(cols)}) VALUES ({placeholders})",
            [tuple(r.get(c) for c in cols) for r in rows],
        )
        st[t] = len(rows)
    conn.commit()
    conn.close()
    return st


def lexicon_present() -> bool:
    if not os.path.exists(DB_FILE):
        return False
    conn = sqlite3.connect(DB_FILE)
    try:
        n = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    conn.close()
    return n > 0


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--export-b64", action="store_true", help="print base64 lexicon seed (run locally)")
    ap.add_argument("--import-b64", action="store_true", help="CI: import from $LEXICON_SEED_B64 if lexicon absent")
    a = ap.parse_args()
    if a.export_b64:
        sys.stdout.write(base64.b64encode(export_seed().encode("utf-8")).decode("ascii"))
    elif a.import_b64:
        if lexicon_present():
            print("[lexicon-seed] lexicon already present (cache hit) — skip import")
            return
        b64 = os.environ.get("LEXICON_SEED_B64", "").strip()
        if not b64:
            print("[lexicon-seed] no local lexicon and LEXICON_SEED_B64 unset — cannot bootstrap")
            sys.exit(1)
        st = import_seed(base64.b64decode(b64).decode("utf-8"))
        print(f"[lexicon-seed] imported {st}")
    else:
        ap.error("choose --export-b64 or --import-b64")


if __name__ == "__main__":
    main()

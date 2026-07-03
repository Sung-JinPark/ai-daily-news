"""research.db — method/concept mention ledger for the trend paper.

MECHANISM ONLY. The lexicon *content* (concepts, alias patterns,
candidate lists) is the paper's methodology and lives privately under
``data/research_private/lexicon/`` (gitignored, per the boundary
contract in research/README.md). This module provides:

  * the schema (mention ledger, D1: row-level, method-agnostic)
  * ``screen`` — count corpus hits for a private candidate file and
    write an adoption rationale (evidence-based v1 selection, D2)
  * ``seed``  — load an adopted seed file into concepts/aliases as a
    new lexicon version

Dual corpus (D3): news = data/<day>/articles.json (title_original +
summary_ko); paper = papers.db (title + abstract, enriched rows only —
screening also counts title-only hits for evidence, flagged as such).

Day-key convention: news days are the repo's existing UTC day keys
(data/<day>/ dirs, see pipeline/collect.py:26); paper days use
papers.first_seen_day (same UTC convention). No naive date.today()
anywhere (AUDIT-1 AUD-012 discipline).

Usage:
    python -m pipeline.research.research_db init
    python -m pipeline.research.research_db screen  # candidates -> rationale+seed
    python -m pipeline.research.research_db seed    # seed -> DB (version 1)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
PRIVATE_ROOT = DATA_DIR / "research_private"
DB_FILE = PRIVATE_ROOT / "research.db"
LEXICON_DIR = PRIVATE_ROOT / "lexicon"
CANDIDATES_FILE = LEXICON_DIR / "candidates_v1.json"
SEED_FILE = LEXICON_DIR / "seed_v1.json"
RATIONALE_FILE = PRIVATE_ROOT / "notes" / "lexicon-v1-rationale.md"
PAPERS_DB = DATA_DIR / "papers_private" / "papers.db"

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lexicon_versions (
  version       INTEGER PRIMARY KEY,
  created_at    TEXT,
  note          TEXT,
  concept_count INTEGER
);
CREATE TABLE IF NOT EXISTS concepts (
  concept_id            TEXT PRIMARY KEY,
  canonical_name        TEXT,
  kind                  TEXT,      -- method | architecture | task | paradigm
  first_lexicon_version INTEGER,
  status                TEXT DEFAULT 'active',
  note                  TEXT
);
CREATE TABLE IF NOT EXISTS aliases (
  concept_id    TEXT,
  pattern       TEXT,              -- regex, word boundaries required
  pattern_kind  TEXT,              -- plain | regex | context_required
  added_version INTEGER,
  PRIMARY KEY (concept_id, pattern)
);
CREATE TABLE IF NOT EXISTS concept_mentions (   -- the ledger (D1)
  concept_id      TEXT,
  source_type     TEXT,            -- news | paper
  source_id       TEXT,            -- article_id (url_hash) or arxiv_id
  day             TEXT,
  field           TEXT,            -- title | summary | abstract
  match_text      TEXT,            -- first-observed surface form
  lexicon_version INTEGER,
  PRIMARY KEY (concept_id, source_type, source_id, field, lexicon_version)
);
CREATE INDEX IF NOT EXISTS idx_cm_day ON concept_mentions(day);
-- F3 (schema v2): event_day — when the mentioned thing HAPPENED.
-- news: = day (collection day). paper: arXiv published date (fallback
-- day for unenriched papers, refined as enrich drains). The original
-- `day` column keeps its observed_day meaning (no rename — ledger
-- immutability).
CREATE INDEX IF NOT EXISTS idx_cm_concept ON concept_mentions(concept_id, source_type);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE VIEW IF NOT EXISTS latest_mentions AS
  SELECT * FROM concept_mentions
  WHERE lexicon_version = (SELECT MAX(version) FROM lexicon_versions);

CREATE VIEW IF NOT EXISTS daily_concept_counts AS
  SELECT concept_id, source_type, day, COUNT(*) AS n
  FROM latest_mentions
  GROUP BY concept_id, source_type, day;

-- F2 (instrument v3): the RESEARCH view — English source fields only.
-- summary (KO, product-derived) rows stay in the ledger as auxiliary
-- instrument history but are excluded from research measurement.
CREATE VIEW IF NOT EXISTS latest_mentions_en AS
  SELECT * FROM latest_mentions
  WHERE field IN ('title', 'body_en', 'abstract');
"""


def open_db() -> sqlite3.Connection:
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA_SQL)
    # F3 schema v2 migration (idempotent): event_day column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(concept_mentions)")}
    if "event_day" not in cols:
        conn.execute("ALTER TABLE concept_mentions ADD COLUMN event_day TEXT")
        conn.commit()
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def _atomic_write(path: Path, text: str) -> None:
    """AUDIT-1 AUD-006 discipline: tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------- corpus iterators (shared with concept_extract) ----------


def iter_news_texts():
    """Yield (day, article_id, field, text) for every archived article."""
    for day_dir in sorted(DATA_DIR.glob("2???-??-??")):
        f = day_dir / "articles.json"
        if not f.exists():
            continue
        try:
            arts = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for a in arts:
            aid = a.get("id")
            if not aid:
                continue
            if a.get("title_original"):
                yield day_dir.name, aid, "title", a["title_original"]
            if a.get("summary_ko"):
                yield day_dir.name, aid, "summary", a["summary_ko"]


def iter_paper_texts(enriched_only: bool = True):
    """Yield (day, arxiv_id, field, text). Title rows for every paper
    that has one; abstract rows only when enriched (D3). Returns a
    skipped-count via StopIteration value? No — caller counts."""
    if not PAPERS_DB.exists():
        return
    conn = sqlite3.connect(PAPERS_DB)
    try:
        rows = conn.execute(
            "SELECT arxiv_id, first_seen_day, title, abstract, enriched FROM papers"
        ).fetchall()
    finally:
        conn.close()
    for aid, day, title, abstract, enriched in rows:
        if title:
            yield day, aid, "title", title
        if abstract and (enriched or not enriched_only):
            yield day, aid, "abstract", abstract


def compile_alias(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# ---------- screen: candidates -> evidence -> seed ----------


def screen(candidates_path: Path = CANDIDATES_FILE) -> dict:
    cands = json.loads(candidates_path.read_text(encoding="utf-8"))["candidates"]
    news = list(iter_news_texts())
    papers = list(iter_paper_texts())
    results = []
    for c in cands:
        per_alias = []
        total = {"news": 0, "paper": 0}
        for al in c["aliases"]:
            rx = compile_alias(al["pattern"])
            n_news = sum(1 for _, _, _, t in news if rx.search(t))
            n_paper = sum(1 for _, _, _, t in papers if rx.search(t))
            per_alias.append({**al, "hits_news": n_news, "hits_paper": n_paper})
            total["news"] += n_news
            total["paper"] += n_paper
        adopted_aliases = [a for a in per_alias if a["hits_news"] + a["hits_paper"] >= 1]
        results.append({
            "concept_id": c["concept_id"], "canonical_name": c["canonical_name"],
            "kind": c["kind"], "total": total,
            "adopted": bool(adopted_aliases),
            "aliases": per_alias, "adopted_aliases": adopted_aliases,
        })

    adopted = [r for r in results if r["adopted"]]
    rejected = [r for r in results if not r["adopted"]]

    seed = {"lexicon_version": 1, "concepts": [
        {"concept_id": r["concept_id"], "canonical_name": r["canonical_name"],
         "kind": r["kind"],
         "aliases": [{"pattern": a["pattern"], "pattern_kind": a["pattern_kind"]}
                     for a in r["adopted_aliases"]]}
        for r in sorted(adopted, key=lambda r: r["concept_id"])
    ]}
    _atomic_write(SEED_FILE, json.dumps(seed, ensure_ascii=False, indent=2) + "\n")

    lines = ["# lexicon v1 rationale — evidence-based adoption", "",
             f"corpus: news texts={len(news):,} field-rows · paper texts={len(papers):,} field-rows",
             f"rule: adopt concept iff >=1 corpus hit on any alias; adopt only hit-bearing aliases", "",
             f"**adopted {len(adopted)} / rejected {len(rejected)} (of {len(results)} candidates)**", "",
             "| concept | kind | news hits | paper hits | adopted aliases |", "|---|---|---:|---:|---|"]
    for r in sorted(results, key=lambda r: (-int(r["adopted"]), r["concept_id"])):
        mark = "" if r["adopted"] else " ~~(rejected)~~"
        pats = "; ".join(f"`{a['pattern']}`({a['hits_news']}/{a['hits_paper']})" for a in r["aliases"])
        lines.append(f"| {r['concept_id']}{mark} | {r['kind']} | {r['total']['news']} | {r['total']['paper']} | {pats} |")
    _atomic_write(RATIONALE_FILE, "\n".join(lines) + "\n")

    print(f"[screen] adopted={len(adopted)} rejected={len(rejected)} -> {SEED_FILE.name}, {RATIONALE_FILE.name}")
    return {"adopted": len(adopted), "rejected": len(rejected)}


# ---------- seed: seed file -> DB ----------


def seed(seed_path: Path = SEED_FILE, note: str = "v1 evidence-based seed") -> dict:
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    version = int(payload["lexicon_version"])
    conn = open_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO lexicon_versions(version, created_at, note, concept_count) VALUES (?,?,?,?)",
            (version, datetime.now(timezone.utc).isoformat(timespec="seconds"), note,
             len(payload["concepts"])),
        )
        n_alias = 0
        for c in payload["concepts"]:
            cur.execute(
                "INSERT OR IGNORE INTO concepts(concept_id, canonical_name, kind, first_lexicon_version) VALUES (?,?,?,?)",
                (c["concept_id"], c["canonical_name"], c["kind"], version),
            )
            for al in c["aliases"]:
                cur.execute(
                    "INSERT OR IGNORE INTO aliases(concept_id, pattern, pattern_kind, added_version) VALUES (?,?,?,?)",
                    (c["concept_id"], al["pattern"], al["pattern_kind"], version),
                )
                n_alias += 1
        conn.commit()
        n_c = cur.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        n_a = cur.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
    finally:
        conn.close()
    print(f"[seed] version={version} concepts={n_c} aliases={n_a}")
    return {"version": version, "concepts": n_c, "aliases": n_a}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("cmd", choices=["init", "screen", "seed"])
    p.add_argument("--note", default="v1 evidence-based seed")
    a = p.parse_args()
    if a.cmd == "init":
        open_db().close()
        print(f"[init] {DB_FILE}")
    elif a.cmd == "screen":
        screen()
    else:
        seed(note=a.note)


if __name__ == "__main__":
    main()

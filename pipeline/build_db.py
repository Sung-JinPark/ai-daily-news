"""Build a SQLite `data/archive.db` from every JSON/JSONL file the
pipeline maintains.

Runs at the tail of the daily pipeline. Reads:

  * `data/YYYY-MM-DD/articles.json`
  * `data/corpus/YYYY-MM-DD/{bodies, members, skipped}.jsonl`
  * `data/aggregates/{entity_mentions, tag_cooccurrence,
                       entity_cooccurrence, source_health}.jsonl`
  * `data/tags_index.json`, `data/glossary.json`,
    `data/weekly/*.json`, `data/themes/*.json`,
    `data/predictions/registry.json`, `data/models/index.json`,
    `data/models/facts.jsonl`

Writes `data/archive.db` (SQLite) + `data/exports/archive.db.gz`
(compressed copy staged for GitHub Release / artifact upload).

Both artifacts are gitignored. The build is deterministic and
recomputable from the git-tracked JSON files at any time.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "archive.db"
EXPORT_DIR = DATA_DIR / "exports"
DATE_LEN = 10  # YYYY-MM-DD


# ---------- schema ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  cluster_id TEXT,
  title_original TEXT,
  url TEXT,
  image_url TEXT,
  source_id TEXT,
  source_name TEXT,
  published TEXT,
  fetched_at TEXT,
  cluster_size INTEGER,
  summary_ko TEXT,
  category TEXT,
  importance_score INTEGER,
  subtitle_en TEXT,
  institution TEXT,
  authors TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_day ON articles(day);
CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id);
CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);

CREATE TABLE IF NOT EXISTS article_tags (
  article_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (article_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_article_tags_tag ON article_tags(tag);

CREATE TABLE IF NOT EXISTS insights (
  article_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  insight_ko TEXT,
  PRIMARY KEY (article_id, position)
);

CREATE TABLE IF NOT EXISTS also_covered_by (
  article_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  PRIMARY KEY (article_id, source_name)
);

CREATE TABLE IF NOT EXISTS cluster_members (
  cluster_id TEXT NOT NULL,
  day TEXT NOT NULL,
  url_hash TEXT NOT NULL,
  is_representative INTEGER,
  source_id TEXT,
  source_name TEXT,
  title TEXT,
  url TEXT,
  published TEXT,
  PRIMARY KEY (day, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_cluster_members_cluster ON cluster_members(cluster_id);

CREATE TABLE IF NOT EXISTS bodies (
  url_hash TEXT NOT NULL,
  day TEXT NOT NULL,
  url TEXT,
  title TEXT,
  source_id TEXT,
  source_name TEXT,
  published TEXT,
  fetched_at TEXT,
  body_chars INTEGER,
  body_text TEXT,
  extract_status TEXT,
  PRIMARY KEY (day, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_bodies_urlhash ON bodies(url_hash);

CREATE TABLE IF NOT EXISTS skipped (
  logged_at TEXT,
  day TEXT NOT NULL,
  url_hash TEXT,
  url TEXT,
  source_id TEXT,
  title TEXT,
  phase TEXT,
  reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_skipped_day ON skipped(day);
CREATE INDEX IF NOT EXISTS idx_skipped_phase ON skipped(phase);

CREATE TABLE IF NOT EXISTS entity_mentions (
  day TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity TEXT NOT NULL,
  article_id TEXT NOT NULL,
  cluster_id TEXT,
  source_id TEXT,
  importance_score INTEGER,
  category TEXT
);
CREATE INDEX IF NOT EXISTS idx_ment_day ON entity_mentions(day);
CREATE INDEX IF NOT EXISTS idx_ment_entity ON entity_mentions(entity);
CREATE INDEX IF NOT EXISTS idx_ment_type ON entity_mentions(entity_type);

CREATE TABLE IF NOT EXISTS tag_cooccurrence (
  day TEXT NOT NULL,
  tag_a TEXT NOT NULL,
  tag_b TEXT NOT NULL,
  cluster_id TEXT,
  article_id TEXT,
  category TEXT
);
CREATE INDEX IF NOT EXISTS idx_tag_cooc_pair ON tag_cooccurrence(tag_a, tag_b);

CREATE TABLE IF NOT EXISTS entity_cooccurrence (
  day TEXT NOT NULL,
  entity_a TEXT NOT NULL,
  entity_a_type TEXT NOT NULL,
  entity_b TEXT NOT NULL,
  entity_b_type TEXT NOT NULL,
  cluster_id TEXT,
  article_id TEXT,
  category TEXT
);
CREATE INDEX IF NOT EXISTS idx_ent_cooc_pair ON entity_cooccurrence(entity_a, entity_b);

CREATE TABLE IF NOT EXISTS source_health (
  logged_at TEXT,
  day TEXT NOT NULL,
  source_id TEXT NOT NULL,
  items INTEGER,
  capped INTEGER,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_health_day ON source_health(day);

CREATE TABLE IF NOT EXISTS weekly_digests (
  week TEXT PRIMARY KEY,
  n_input INTEGER,
  generated_at TEXT,
  theme_recap_ko TEXT
);

CREATE TABLE IF NOT EXISTS weekly_themes (
  week TEXT NOT NULL,
  position INTEGER NOT NULL,
  name TEXT,
  summary_ko TEXT,
  PRIMARY KEY (week, position)
);

CREATE TABLE IF NOT EXISTS weekly_theme_articles (
  week TEXT NOT NULL,
  theme_position INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  PRIMARY KEY (week, theme_position, article_id)
);

CREATE TABLE IF NOT EXISTS weekly_top_stories (
  week TEXT NOT NULL,
  position INTEGER NOT NULL,
  article_id TEXT NOT NULL,
  PRIMARY KEY (week, position)
);

CREATE TABLE IF NOT EXISTS themes (
  slug TEXT PRIMARY KEY,
  name TEXT,
  thesis_ko TEXT,
  window_start TEXT,
  window_end TEXT,
  week TEXT
);

CREATE TABLE IF NOT EXISTS theme_clusters (
  slug TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  PRIMARY KEY (slug, cluster_id)
);

CREATE TABLE IF NOT EXISTS predictions (
  id TEXT PRIMARY KEY,
  article_id TEXT,
  article_url TEXT,
  article_title TEXT,
  source_name TEXT,
  day_made TEXT,
  claim_ko TEXT,
  who TEXT,
  horizon TEXT,
  confidence TEXT,
  status TEXT,
  resolution_article_id TEXT,
  resolution_day TEXT,
  resolution_note_ko TEXT
);

CREATE TABLE IF NOT EXISTS model_facts (
  article_id TEXT NOT NULL,
  day TEXT NOT NULL,
  model TEXT NOT NULL,
  version TEXT,
  strengths_ko TEXT,
  weaknesses_ko TEXT,
  PRIMARY KEY (article_id, model)
);

CREATE TABLE IF NOT EXISTS model_facts_benchmarks (
  article_id TEXT NOT NULL,
  model TEXT NOT NULL,
  name TEXT NOT NULL,
  score TEXT
);
CREATE INDEX IF NOT EXISTS idx_mfb_model ON model_facts_benchmarks(model);

CREATE TABLE IF NOT EXISTS model_facts_pricing (
  article_id TEXT NOT NULL,
  model TEXT NOT NULL,
  unit TEXT,
  value TEXT
);

CREATE TABLE IF NOT EXISTS models_index (
  model TEXT PRIMARY KEY,
  latest_version TEXT,
  latest_seen TEXT,
  article_count INTEGER,
  strengths_ko TEXT,
  weaknesses_ko TEXT
);

CREATE TABLE IF NOT EXISTS glossary (
  term TEXT PRIMARY KEY,
  full TEXT,
  desc TEXT,
  seed INTEGER,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS tags_index (
  tag TEXT PRIMARY KEY,
  count INTEGER,
  categories TEXT
);
"""


# ---------- loaders ----------

def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _load_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _list_days() -> list[str]:
    return sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir() and len(p.name) == DATE_LEN and p.name[4] == "-" and p.name[7] == "-"
    )


def _load_articles(cur: sqlite3.Cursor) -> int:
    n = 0
    for day in _list_days():
        articles = _load_json(DATA_DIR / day / "articles.json", [])
        for a in articles:
            aid = a.get("id")
            if not aid:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO articles(
                    id, day, cluster_id, title_original, url, image_url,
                    source_id, source_name, published, fetched_at, cluster_size,
                    summary_ko, category, importance_score, subtitle_en,
                    institution, authors
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    aid, day, a.get("cluster_id"), a.get("title_original"),
                    a.get("url"), a.get("image_url"),
                    a.get("source_id"), a.get("source_name"),
                    a.get("published"), a.get("fetched_at"),
                    a.get("cluster_size"),
                    a.get("summary_ko"), a.get("category"),
                    a.get("importance_score"), a.get("subtitle_en"),
                    a.get("institution"), a.get("authors"),
                ),
            )
            for tag in a.get("tags", []) or []:
                cur.execute("INSERT OR IGNORE INTO article_tags(article_id, tag) VALUES (?,?)", (aid, tag))
            for i, ins in enumerate(a.get("insights_ko", []) or []):
                cur.execute("INSERT OR REPLACE INTO insights(article_id, position, insight_ko) VALUES (?,?,?)", (aid, i, ins))
            for src in a.get("also_covered_by", []) or []:
                cur.execute("INSERT OR IGNORE INTO also_covered_by(article_id, source_name) VALUES (?,?)", (aid, src))
            n += 1
    return n


def _load_corpus(cur: sqlite3.Cursor) -> tuple[int, int, int]:
    n_bodies = n_members = n_skipped = 0
    corpus_root = DATA_DIR / "corpus"
    if not corpus_root.exists():
        return 0, 0, 0
    for day_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        day = day_dir.name
        for row in _iter_jsonl(day_dir / "bodies.jsonl"):
            cur.execute(
                """INSERT OR REPLACE INTO bodies(
                    url_hash, day, url, title, source_id, source_name,
                    published, fetched_at, body_chars, body_text, extract_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.get("url_hash"), day, row.get("url"), row.get("title"),
                    row.get("source_id"), row.get("source_name"),
                    row.get("published"), row.get("fetched_at"),
                    row.get("body_chars"), row.get("body_text"),
                    row.get("extract_status"),
                ),
            )
            n_bodies += 1
        for row in _iter_jsonl(day_dir / "members.jsonl"):
            cur.execute(
                """INSERT OR REPLACE INTO cluster_members(
                    cluster_id, day, url_hash, is_representative,
                    source_id, source_name, title, url, published
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    row.get("cluster_id"), day, row.get("url_hash"),
                    1 if row.get("is_representative") else 0,
                    row.get("source_id"), row.get("source_name"),
                    row.get("title"), row.get("url"), row.get("published"),
                ),
            )
            n_members += 1
        for row in _iter_jsonl(day_dir / "skipped.jsonl"):
            cur.execute(
                """INSERT INTO skipped(logged_at, day, url_hash, url,
                    source_id, title, phase, reason
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    row.get("logged_at"), day, row.get("url_hash"),
                    row.get("url"), row.get("source_id"), row.get("title"),
                    row.get("phase"), row.get("reason"),
                ),
            )
            n_skipped += 1
    return n_bodies, n_members, n_skipped


def _load_aggregates(cur: sqlite3.Cursor) -> tuple[int, int, int, int]:
    n_m = n_tc = n_ec = n_sh = 0
    agg = DATA_DIR / "aggregates"
    if agg.exists():
        for row in _iter_jsonl(agg / "entity_mentions.jsonl"):
            cur.execute(
                """INSERT INTO entity_mentions(day, entity_type, entity,
                    article_id, cluster_id, source_id, importance_score, category
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    row.get("day"), row.get("entity_type"), row.get("entity"),
                    row.get("article_id"), row.get("cluster_id"),
                    row.get("source_id"), row.get("importance_score"),
                    row.get("category"),
                ),
            )
            n_m += 1
        for row in _iter_jsonl(agg / "tag_cooccurrence.jsonl"):
            cur.execute(
                """INSERT INTO tag_cooccurrence(day, tag_a, tag_b,
                    cluster_id, article_id, category
                ) VALUES (?,?,?,?,?,?)""",
                (
                    row.get("day"), row.get("tag_a"), row.get("tag_b"),
                    row.get("cluster_id"), row.get("article_id"),
                    row.get("category"),
                ),
            )
            n_tc += 1
        for row in _iter_jsonl(agg / "entity_cooccurrence.jsonl"):
            cur.execute(
                """INSERT INTO entity_cooccurrence(day, entity_a, entity_a_type,
                    entity_b, entity_b_type, cluster_id, article_id, category
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    row.get("day"), row.get("entity_a"), row.get("entity_a_type"),
                    row.get("entity_b"), row.get("entity_b_type"),
                    row.get("cluster_id"), row.get("article_id"),
                    row.get("category"),
                ),
            )
            n_ec += 1
        for row in _iter_jsonl(agg / "source_health.jsonl"):
            cur.execute(
                """INSERT INTO source_health(logged_at, day, source_id,
                    items, capped, error
                ) VALUES (?,?,?,?,?,?)""",
                (
                    row.get("logged_at"), row.get("day"), row.get("source_id"),
                    row.get("items"), row.get("capped"), row.get("error"),
                ),
            )
            n_sh += 1
    return n_m, n_tc, n_ec, n_sh


def _load_weekly(cur: sqlite3.Cursor) -> int:
    n = 0
    weekly_dir = DATA_DIR / "weekly"
    if not weekly_dir.exists():
        return 0
    for f in sorted(weekly_dir.glob("*.json")):
        digest = _load_json(f, None)
        if not digest:
            continue
        week = digest.get("week", "")
        cur.execute(
            "INSERT OR REPLACE INTO weekly_digests(week, n_input, generated_at, theme_recap_ko) VALUES (?,?,?,?)",
            (week, digest.get("n_input"), digest.get("generated_at"), digest.get("theme_recap_ko")),
        )
        for i, aid in enumerate(digest.get("top_story_ids", []) or []):
            cur.execute("INSERT OR REPLACE INTO weekly_top_stories(week, position, article_id) VALUES (?,?,?)", (week, i, aid))
        for i, theme in enumerate(digest.get("themes", []) or []):
            cur.execute(
                "INSERT OR REPLACE INTO weekly_themes(week, position, name, summary_ko) VALUES (?,?,?,?)",
                (week, i, theme.get("name"), theme.get("summary_ko")),
            )
            for aid in theme.get("article_ids", []) or []:
                cur.execute(
                    "INSERT OR IGNORE INTO weekly_theme_articles(week, theme_position, article_id) VALUES (?,?,?)",
                    (week, i, aid),
                )
        n += 1
    return n


def _load_themes(cur: sqlite3.Cursor) -> int:
    n = 0
    themes_dir = DATA_DIR / "themes"
    if not themes_dir.exists():
        return 0
    for f in sorted(themes_dir.glob("*.json")):
        payload = _load_json(f, None)
        if not payload:
            continue
        week = payload.get("week")
        for t in payload.get("themes", []) or []:
            slug = t.get("slug")
            if not slug:
                continue
            cur.execute(
                "INSERT OR REPLACE INTO themes(slug, name, thesis_ko, window_start, window_end, week) VALUES (?,?,?,?,?,?)",
                (slug, t.get("name"), t.get("thesis_ko"),
                 payload.get("window_start"), payload.get("window_end"), week),
            )
            for cid in t.get("cluster_ids", []) or []:
                cur.execute("INSERT OR IGNORE INTO theme_clusters(slug, cluster_id) VALUES (?,?)", (slug, cid))
            n += 1
    return n


def _load_predictions(cur: sqlite3.Cursor) -> int:
    reg = _load_json(DATA_DIR / "predictions" / "registry.json", None)
    if not reg:
        return 0
    n = 0
    for p in reg.get("predictions", []) or []:
        cur.execute(
            """INSERT OR REPLACE INTO predictions(id, article_id, article_url,
                article_title, source_name, day_made, claim_ko, who, horizon,
                confidence, status, resolution_article_id, resolution_day,
                resolution_note_ko
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                p.get("id"), p.get("article_id"), p.get("article_url"),
                p.get("article_title"), p.get("source_name"), p.get("day_made"),
                p.get("claim_ko"), p.get("who"), p.get("horizon"),
                p.get("confidence"), p.get("status"),
                p.get("resolution_article_id"), p.get("resolution_day"),
                p.get("resolution_note_ko"),
            ),
        )
        n += 1
    return n


def _load_models(cur: sqlite3.Cursor) -> tuple[int, int]:
    n_facts = 0
    facts_path = DATA_DIR / "models" / "facts.jsonl"
    for row in _iter_jsonl(facts_path):
        aid = row.get("article_id")
        day = row.get("day")
        for fact in row.get("facts", []) or []:
            model = fact.get("model")
            if not model:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO model_facts(article_id, day, model,
                    version, strengths_ko, weaknesses_ko
                ) VALUES (?,?,?,?,?,?)""",
                (
                    aid, day, model, fact.get("version"),
                    json.dumps(fact.get("strengths_ko", []) or [], ensure_ascii=False),
                    json.dumps(fact.get("weaknesses_ko", []) or [], ensure_ascii=False),
                ),
            )
            for b in fact.get("benchmarks", []) or []:
                cur.execute(
                    "INSERT INTO model_facts_benchmarks(article_id, model, name, score) VALUES (?,?,?,?)",
                    (aid, model, b.get("name"), b.get("score")),
                )
            for p in fact.get("pricing", []) or []:
                cur.execute(
                    "INSERT INTO model_facts_pricing(article_id, model, unit, value) VALUES (?,?,?,?)",
                    (aid, model, p.get("unit"), p.get("value")),
                )
            n_facts += 1
    idx = _load_json(DATA_DIR / "models" / "index.json", None)
    n_idx = 0
    if idx:
        for m in idx.get("models", []) or []:
            cur.execute(
                """INSERT OR REPLACE INTO models_index(model, latest_version,
                    latest_seen, article_count, strengths_ko, weaknesses_ko
                ) VALUES (?,?,?,?,?,?)""",
                (
                    m.get("model"), m.get("latest_version"), m.get("latest_seen"),
                    m.get("article_count"),
                    json.dumps(m.get("strengths_ko", []) or [], ensure_ascii=False),
                    json.dumps(m.get("weaknesses_ko", []) or [], ensure_ascii=False),
                ),
            )
            n_idx += 1
    return n_facts, n_idx


def _load_glossary(cur: sqlite3.Cursor) -> int:
    g = _load_json(DATA_DIR / "glossary.json", None)
    if not g:
        return 0
    n = 0
    for term in g.get("terms", []) or []:
        cur.execute(
            "INSERT OR REPLACE INTO glossary(term, full, desc, seed, added_at) VALUES (?,?,?,?,?)",
            (term.get("term"), term.get("full"), term.get("desc"),
             1 if term.get("seed") else 0, term.get("added_at")),
        )
        n += 1
    return n


def _load_tags_index(cur: sqlite3.Cursor) -> int:
    idx = _load_json(DATA_DIR / "tags_index.json", None)
    if not idx:
        return 0
    n = 0
    for tag, info in (idx.get("tags", {}) or {}).items():
        cur.execute(
            "INSERT OR REPLACE INTO tags_index(tag, count, categories) VALUES (?,?,?)",
            (tag, info.get("count"), json.dumps(info.get("categories", []) or [], ensure_ascii=False)),
        )
        n += 1
    return n


# ---------- main ----------

def build() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_FILE.exists():
        DB_FILE.unlink()
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.executescript(SCHEMA)
        cur = conn.cursor()
        stats = {}
        stats["articles"] = _load_articles(cur)
        b, m, s = _load_corpus(cur)
        stats["bodies"] = b
        stats["cluster_members"] = m
        stats["skipped"] = s
        em, tc, ec, sh = _load_aggregates(cur)
        stats["entity_mentions"] = em
        stats["tag_cooccurrence"] = tc
        stats["entity_cooccurrence"] = ec
        stats["source_health"] = sh
        stats["weekly_digests"] = _load_weekly(cur)
        stats["themes"] = _load_themes(cur)
        stats["predictions"] = _load_predictions(cur)
        nf, ni = _load_models(cur)
        stats["model_facts"] = nf
        stats["models_index"] = ni
        stats["glossary_terms"] = _load_glossary(cur)
        stats["tags_index"] = _load_tags_index(cur)
        cur.execute(
            "INSERT OR REPLACE INTO build_meta(key, value) VALUES ('generated_at', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        cur.execute(
            "INSERT OR REPLACE INTO build_meta(key, value) VALUES ('stats', ?)",
            (json.dumps(stats, ensure_ascii=False),),
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return stats


def export() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = EXPORT_DIR / "archive.db.gz"
    with DB_FILE.open("rb") as src, gzip.open(dest, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(src, gz)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-export", action="store_true", help="skip archive.db.gz build")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stats = build()
    log.info("build_db: %s (%d bytes)", stats, DB_FILE.stat().st_size)
    if not args.no_export:
        gz = export()
        log.info("export: %s (%d bytes)", gz, gz.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

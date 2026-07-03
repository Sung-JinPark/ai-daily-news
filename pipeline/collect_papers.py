"""Auto-collect arXiv papers mentioned in the daily news corpus into a
private SQLite DB at ``data/papers_private/papers.db``.

Data flow:

    data/YYYY-MM-DD/articles.json   (already fetched by pipeline.collect)
        │
        │  filter arxiv sources + arxiv.org/abs/ urls
        ▼
    papers.db  (private, gitignored)
        ├── papers          — one row per arxiv_id (base, no version)
        ├── paper_mentions  — (arxiv_id, article_id) pairs across days
        └── meta            — schema + last_run bookkeeping

The module is deliberately self-contained: it reads the git-tracked
articles.json that the public pipeline already produces, so we never
re-fetch news. arXiv API calls happen only during optional
``enrich`` — bounded by a 3s per-request sleep per the arXiv Terms
of Service.

CLI (see ``python -m pipeline.collect_papers --help``):
    --day YYYY-MM-DD       collect one day (default: newest data/ day)
    --backfill             collect every day/articles.json under data/
    --dry-run              parse + report, don't write to DB
    --with-pdf             also download pdfs into data/papers_private/pdf/
    --no-enrich            skip arXiv API metadata enrichment
    --sleep 3.0            seconds between arXiv API requests
    --limit-enrich N       cap enrichment to N papers per run

Everything under ``data/papers_private/`` is gitignored — this
module never touches public repo state.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import feedparser

from pipeline.arxiv_refs import (
    ARXIV_URL_RE,
    ARXIV_VERSION_RE,
    extract_arxiv_refs,
    load_refs_file,
    parse_arxiv_id,
)
from pipeline.state import url_hash
from pipeline.utils.http import get_client, fetch

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
RAW_DIR = Path("raw")
PRIVATE_ROOT = DATA_DIR / "papers_private"
DB_FILE = PRIVATE_ROOT / "papers.db"
PDF_DIR = PRIVATE_ROOT / "pdf"

# v2: paper_mentions.mention_kind distinguishes 'primary' (the article
# IS the paper — arXiv feed one-shots) from 'reference' (a non-arXiv
# article whose text links/cites the paper). v1 DBs migrate in-place
# in open_db(); all pre-existing rows are primary by construction.
SCHEMA_VERSION = 2

# arXiv source ids from pipeline/sources.yaml — used to short-circuit
# non-arxiv rows before falling back to a URL regex. Keep in sync when
# new arxiv:* sources are added.
ARXIV_SOURCE_IDS = {
    "arxiv_cs_ai", "arxiv_cs_lg", "arxiv_cs_cl", "arxiv_cs_cv",
    "arxiv_cs_ro", "arxiv_stat_ml",
}

# Extraction patterns live in pipeline.arxiv_refs (C4-1) so CI's
# collect step and this local consumer can never drift. Re-exported
# names above keep existing call sites working.

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_BATCH_SIZE = 25  # id_list per request — under the 100 policy cap

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
  arxiv_id          TEXT PRIMARY KEY,
  title             TEXT,
  authors_json      TEXT,
  abstract          TEXT,
  primary_category  TEXT,
  categories_json   TEXT,
  published         TEXT,
  updated           TEXT,
  abs_url           TEXT,
  pdf_url           TEXT,
  first_seen_day    TEXT,
  last_seen_day     TEXT,
  seen_count        INTEGER DEFAULT 0,
  tags_json         TEXT,
  importance_max    INTEGER DEFAULT 0,
  pdf_path          TEXT,
  enriched          INTEGER DEFAULT 0,
  schema_version    INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_papers_last_seen ON papers(last_seen_day);
CREATE INDEX IF NOT EXISTS idx_papers_enriched  ON papers(enriched);

CREATE TABLE IF NOT EXISTS paper_mentions (
  arxiv_id     TEXT NOT NULL,
  day          TEXT NOT NULL,
  article_id   TEXT NOT NULL,
  cluster_id   TEXT,
  source_id    TEXT,
  importance   INTEGER,
  mention_kind TEXT NOT NULL DEFAULT 'primary',
  PRIMARY KEY (arxiv_id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_mentions_day ON paper_mentions(day);
CREATE INDEX IF NOT EXISTS idx_paper_mentions_arxiv ON paper_mentions(arxiv_id);

CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  TEXT
);
"""


# ---------- id parsing ----------
# parse_arxiv_id / extract_arxiv_refs are imported from
# pipeline.arxiv_refs — single source of truth shared with CI.


def is_arxiv_row(row: dict) -> bool:
    """Cheap pre-filter — used to skip non-paper rows without regex."""
    if row.get("source_id") in ARXIV_SOURCE_IDS:
        return True
    url = row.get("url") or ""
    return "arxiv.org/abs/" in url or "arxiv.org/pdf/" in url


# ---------- DB ----------


def open_db() -> sqlite3.Connection:
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA_SQL)
    # v1 -> v2 in-place migration: add mention_kind to pre-existing DBs.
    # Every v1 row was produced by the url-based primary path, so the
    # column default 'primary' is the correct backfill. Idempotent —
    # the column check makes re-runs no-ops.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_mentions)")}
    if "mention_kind" not in cols:
        conn.execute(
            "ALTER TABLE paper_mentions ADD COLUMN mention_kind TEXT NOT NULL DEFAULT 'primary'"
        )
        log.info("[migrate] paper_mentions.mention_kind added (schema v1 -> v2)")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def _merge_json_list(existing_json: str | None, incoming: list) -> str:
    """Union a JSON-list column with a new list, preserving order."""
    if not incoming:
        return existing_json or "[]"
    try:
        current = json.loads(existing_json) if existing_json else []
        if not isinstance(current, list):
            current = []
    except json.JSONDecodeError:
        current = []
    seen = set(current)
    for item in incoming:
        if item and item not in seen:
            current.append(item)
            seen.add(item)
    return json.dumps(current, ensure_ascii=False)


def upsert_paper(conn: sqlite3.Connection, arxiv_id: str, article: dict, day: str,
                 kind: str = "primary") -> str:
    """Insert or update the papers row for one article mention.

    ``kind`` only affects the INSERT title seed: a primary mention's
    article title IS the paper title; a reference mention's article
    title is the *referring* article, so the placeholder row keeps
    title NULL and lets the nightly enrich fill it from arXiv.
    Update rules (tags union, importance_max, first/last day) are
    identical for both kinds.

    seen_count is NOT touched here (AUD-005): it used to be a
    per-run counter that inflated on every rerun/backfill. It is now
    DERIVED — recomputed in bulk at the end of each collect as
    COUNT(DISTINCT day) of the paper's mentions (idempotent).

    Returns 'insert' or 'update' so the caller can log real deltas.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT tags_json, importance_max, first_seen_day, seen_count FROM papers WHERE arxiv_id = ?",
        (arxiv_id,),
    )
    existing = cur.fetchone()
    tags = article.get("tags") or []
    importance = int(article.get("importance_score") or 0)
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    if existing is None:
        cur.execute(
            """
            INSERT INTO papers (
                arxiv_id, title, abs_url, pdf_url,
                first_seen_day, last_seen_day, seen_count,
                tags_json, importance_max, enriched, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?)
            """,
            (
                arxiv_id,
                (article.get("title_original") or None) if kind == "primary" else None,
                abs_url,
                pdf_url,
                day,
                day,
                json.dumps(tags, ensure_ascii=False),
                importance,
                SCHEMA_VERSION,
            ),
        )
        return "insert"

    old_tags_json, old_importance_max, old_first_seen, old_seen_count = existing
    merged_tags = _merge_json_list(old_tags_json, tags)
    new_importance_max = max(int(old_importance_max or 0), importance)
    new_first_seen = min(old_first_seen, day) if old_first_seen else day
    new_last_seen = max(day, old_first_seen or day)
    cur.execute(
        """
        UPDATE papers SET
            last_seen_day  = MAX(COALESCE(last_seen_day, ?), ?),
            first_seen_day = MIN(COALESCE(first_seen_day, ?), ?),
            tags_json      = ?,
            importance_max = ?
        WHERE arxiv_id = ?
        """,
        (day, day, day, day, merged_tags, new_importance_max, arxiv_id),
    )
    return "update"


def recompute_seen_counts(conn: sqlite3.Connection) -> int:
    """AUD-005: seen_count = COUNT(DISTINCT mention day) — derived,
    idempotent, immune to rerun/backfill inflation. Runs at the end of
    every collect; the first run doubles as the one-time migration
    that corrects historically inflated values. Returns how many rows
    changed."""
    cur = conn.execute(
        """
        UPDATE papers SET seen_count = (
            SELECT COUNT(DISTINCT day) FROM paper_mentions
            WHERE paper_mentions.arxiv_id = papers.arxiv_id
        )
        WHERE seen_count != (
            SELECT COUNT(DISTINCT day) FROM paper_mentions
            WHERE paper_mentions.arxiv_id = papers.arxiv_id
        )
        """
    )
    conn.commit()
    return cur.rowcount


def upsert_mention(conn: sqlite3.Connection, arxiv_id: str, article: dict, day: str,
                   kind: str = "primary") -> bool:
    """Insert (arxiv_id, article_id) mention row. Returns True on
    new insert, False on already-present. The (arxiv_id, article_id)
    PK dedupes across kinds — a pair that already exists as primary
    is not double-counted as reference."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO paper_mentions (
            arxiv_id, day, article_id, cluster_id, source_id, importance, mention_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            arxiv_id,
            day,
            article.get("id"),
            article.get("cluster_id"),
            article.get("source_id"),
            int(article.get("importance_score") or 0),
            kind,
        ),
    )
    return cur.rowcount > 0


# ---------- day traversal ----------


def iter_days(explicit_day: str | None, backfill: bool) -> list[str]:
    """Pick which data/<day>/articles.json files to process."""
    day_dirs = sorted(
        p for p in DATA_DIR.iterdir()
        if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)
        and (p / "articles.json").exists()
    )
    if backfill:
        return [p.name for p in day_dirs]
    if explicit_day:
        return [explicit_day] if (DATA_DIR / explicit_day / "articles.json").exists() else []
    return [day_dirs[-1].name] if day_dirs else []


def load_articles(day: str) -> list[dict]:
    path = DATA_DIR / day / "articles.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- cross-source reference extraction (C1) ----------
#
# Text-source reality (investigated 2026-07-02):
#   * data/corpus/*/bodies.jsonl does NOT exist — corpus persists only
#     members/skipped (titles + urls). Article bodies live in the
#     gitignored, transient raw/ tree.
#   * articles.json LLM fields (summary_ko / insights_ko) carry ZERO
#     arXiv references across 1,317 non-arXiv articles — the Korean
#     summaries do not preserve source links.
#   * raw/<day>/<source>.json RSS `summary` DOES carry references
#     (~0.5% of items) and is therefore the extraction source.
# C4-1 closed the coverage gap: pipeline.collect now persists the
# candidates to data/<day>/arxiv_refs.json on every CI run (full
# coverage from 2026-07-02). Source priority here is refs-file first,
# live raw/ scan as the fallback for pre-persistence local days.


def load_raw_items(day: str) -> list[dict]:
    """Return raw feed items for ``day`` — every ``raw/<day>/*.json``
    that parses to a list of source-item dicts. Files like
    clusters.json (dedupe state, not a source dump) are skipped by the
    shape check."""
    day_dir = RAW_DIR / day
    if not day_dir.exists():
        return []
    items: list[dict] = []
    for f in sorted(day_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            if isinstance(it, dict) and it.get("url") and it.get("source_id"):
                items.append(it)
    return items


def _referring_dict(article_id: str, source_id: str | None,
                    by_id: dict, title: str | None = None) -> dict:
    """Build the pseudo-article dict the upsert path expects for a
    reference mention. Attaches cluster/importance/tags when the
    referring article survived dedupe into articles.json."""
    known = by_id.get(article_id)
    return {
        "id": article_id,
        "cluster_id": (known or {}).get("cluster_id"),
        "source_id": source_id,
        "importance_score": (known or {}).get("importance_score") or 0,
        "tags": (known or {}).get("tags") or [],
        "title_original": title or (known or {}).get("title_original"),
    }


def extract_referenced_papers(day: str, articles: list[dict]) -> list[tuple[str, dict]]:
    """Return [(arxiv_id, referring_article_dict), ...] for ``day``.

    Source priority:
      1. ``data/<day>/arxiv_refs.json`` — persisted by pipeline.collect
         on every CI/local run since 2026-07-02. Authoritative when
         present (even when its refs list is empty — that means
         extraction ran and found nothing).
      2. Live ``raw/<day>`` scan — fallback for pre-persistence days
         that were collected on this machine.

    Both paths share the extraction patterns in pipeline.arxiv_refs,
    and both key the referring article as ``url_hash(url)`` (verified
    identical to the articles.json ``id`` scheme).
    """
    by_id = {a.get("id"): a for a in articles}
    out: list[tuple[str, dict]] = []

    payload = load_refs_file(day)
    if payload is not None:
        for ref in payload["refs"]:
            aid = ref.get("arxiv_id")
            article_id = ref.get("article_id")
            if not aid or not article_id:
                continue
            out.append((aid, _referring_dict(article_id, ref.get("source_id"), by_id)))
        return out

    for item in load_raw_items(day):
        item_url = item.get("url") or ""
        if "arxiv.org" in item_url:
            continue
        blob = f"{item.get('title') or ''} {item.get('summary') or ''}"
        refs = extract_arxiv_refs(blob)
        if not refs:
            continue
        article_id = url_hash(item_url)
        referring = _referring_dict(article_id, item.get("source_id"), by_id, title=item.get("title"))
        for aid in sorted(refs):
            out.append((aid, referring))
    return out


# ---------- enrichment ----------

# arXiv API resilience (C2). Observed 2026-07-02: blanket 429s, then
# read timeouts, with the generic project UA — arXiv's guidance asks
# automated clients to identify themselves with contact info, and
# anonymous UAs are first in line for throttling.
ARXIV_UA = (
    "ai-daily-news-research/1.0 "
    "(paper metadata enrichment; contact: 91ssjj@gmail.com)"
)
BACKOFF_CAP_SEC = 60.0
MAX_CONSECUTIVE_FAILURES = 2


def _arxiv_client() -> "httpx.Client":
    """Dedicated client for the arXiv API with an identifying UA.
    Still routed through ``fetch`` so the per-host throttle applies."""
    import httpx

    from pipeline.utils.http import DEFAULT_TIMEOUT

    return httpx.Client(
        headers={"User-Agent": ARXIV_UA, "Accept": "*/*"},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )


def _text(entry, key: str) -> str:
    val = entry.get(key)
    if isinstance(val, dict):
        return val.get("value", "") or ""
    return (val or "") if isinstance(val, str) else ""


def enrich_batch(client, ids: list[str]) -> dict[str, dict]:
    """Call arXiv API for a batch of ids and parse Atom into a dict
    keyed by base arxiv_id. No sleeping here — pacing and backoff are
    the caller's job so failures can back off exponentially."""
    if not ids:
        return {}
    url = f"{ARXIV_API}?id_list={','.join(ids)}&max_results={len(ids)}"
    log.info("[enrich] fetching %d ids", len(ids))
    resp = fetch(url, client=client)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    out: dict[str, dict] = {}
    for entry in parsed.entries:
        eid = entry.get("id", "")
        m = ARXIV_URL_RE.search(eid)
        if not m:
            continue
        arxiv_id = ARXIV_VERSION_RE.sub("", m.group("id"))
        authors = [a.get("name") for a in (entry.get("authors") or []) if a.get("name")]
        cats = [t.get("term") for t in (entry.get("tags") or []) if t.get("term")]
        primary = cats[0] if cats else None
        pdf_url = None
        for link in entry.get("links") or []:
            if link.get("type") == "application/pdf":
                pdf_url = link.get("href")
                break
        out[arxiv_id] = {
            "title": re.sub(r"\s+", " ", _text(entry, "title")).strip() or None,
            "authors_json": json.dumps(authors, ensure_ascii=False) if authors else "[]",
            "abstract": _text(entry, "summary").strip() or None,
            "primary_category": primary,
            "categories_json": json.dumps(cats, ensure_ascii=False),
            "published": entry.get("published"),
            "updated": entry.get("updated"),
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        }
    return out


def enrich_pending(conn: sqlite3.Connection, sleep_sec: float, limit: int | None) -> dict:
    """Fill metadata for every paper with enriched=0. Returns stats.

    Resilience (C2): on batch failure the inter-request sleep doubles
    (capped at ``BACKOFF_CAP_SEC``); after ``MAX_CONSECUTIVE_FAILURES``
    consecutive failures this run's enrichment aborts and the rest
    defers to the next invocation. Aborting is always safe — the
    collection upserts committed before enrichment started.
    """
    cur = conn.cursor()
    cur.execute("SELECT arxiv_id FROM papers WHERE enriched = 0 ORDER BY last_seen_day DESC")
    pending = [row[0] for row in cur.fetchall()]
    if limit is not None:
        pending = pending[:limit]
    stats = {"attempted": len(pending), "enriched": 0, "failed": 0, "aborted": False}
    if not pending:
        return stats

    consecutive_failures = 0
    backoff = sleep_sec
    client = _arxiv_client()
    try:
        for i in range(0, len(pending), ARXIV_BATCH_SIZE):
            batch = pending[i:i + ARXIV_BATCH_SIZE]
            try:
                fetched = enrich_batch(client, batch)
            except Exception as exc:
                consecutive_failures += 1
                stats["failed"] += len(batch)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    deferred = len(pending) - i - len(batch)
                    stats["aborted"] = True
                    log.warning(
                        "[enrich] batch failed (%s) - %d consecutive failures, "
                        "aborting this run; %d ids defer to next run",
                        exc, consecutive_failures, max(0, deferred),
                    )
                    break
                log.warning(
                    "[enrich] batch failed (%s) - backing off %.0fs before retrying",
                    exc, backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP_SEC)
                continue
            consecutive_failures = 0
            backoff = sleep_sec
            _apply_enrich_batch(cur, batch, fetched, stats)
            conn.commit()
            # arXiv ToS: cap request rate between successful batches.
            time.sleep(sleep_sec)
    finally:
        client.close()
    return stats


def _apply_enrich_batch(cur, batch: list[str], fetched: dict[str, dict], stats: dict) -> None:
    for aid in batch:
        meta = fetched.get(aid)
        if not meta:
            stats["failed"] += 1
            continue
        cur.execute(
            """
            UPDATE papers SET
                title            = COALESCE(?, title),
                authors_json     = ?,
                abstract         = ?,
                primary_category = ?,
                categories_json  = ?,
                published        = ?,
                updated          = ?,
                abs_url          = ?,
                pdf_url          = ?,
                enriched         = 1
            WHERE arxiv_id = ?
            """,
            (
                meta["title"],
                meta["authors_json"],
                meta["abstract"],
                meta["primary_category"],
                meta["categories_json"],
                meta["published"],
                meta["updated"],
                meta["abs_url"],
                meta["pdf_url"],
                aid,
            ),
        )
        stats["enriched"] += 1


# ---------- optional PDF ----------


def download_pdfs(conn: sqlite3.Connection, sleep_sec: float) -> dict:
    """Fetch pdf for every enriched paper that doesn't yet have one."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT arxiv_id, pdf_url FROM papers WHERE (pdf_path IS NULL OR pdf_path = '')"
    )
    rows = cur.fetchall()
    stats = {"attempted": len(rows), "downloaded": 0, "skipped": 0, "failed": 0}
    if not rows:
        return stats
    with get_client() as client:
        for aid, pdf_url in rows:
            safe_id = aid.replace("/", "_")
            local = PDF_DIR / f"{safe_id}.pdf"
            if local.exists():
                cur.execute("UPDATE papers SET pdf_path = ? WHERE arxiv_id = ?", (str(local), aid))
                stats["skipped"] += 1
                continue
            try:
                resp = fetch(pdf_url or f"https://arxiv.org/pdf/{aid}", client=client)
                resp.raise_for_status()
                local.write_bytes(resp.content)
                cur.execute("UPDATE papers SET pdf_path = ? WHERE arxiv_id = ?", (str(local), aid))
                stats["downloaded"] += 1
            except Exception as exc:
                log.warning("[pdf] %s failed: %s", aid, exc)
                stats["failed"] += 1
            time.sleep(sleep_sec)
    conn.commit()
    return stats


# ---------- orchestrator ----------


def collect(days: Iterable[str], conn: sqlite3.Connection, dry_run: bool,
            with_references: bool = True) -> dict:
    stats = {"scanned_articles": 0, "arxiv_rows": 0, "unmatched_urls": 0,
             "papers_inserted": 0, "papers_updated": 0,
             "mentions_inserted": 0, "mentions_skipped": 0,
             "refs_found": 0, "ref_papers_inserted": 0,
             "ref_mentions_inserted": 0, "ref_mentions_skipped": 0,
             "days": []}
    for day in days:
        articles = load_articles(day)
        day_scanned = 0
        day_arxiv = 0
        day_ins = day_upd = 0
        day_ment_new = day_ment_skip = 0
        for art in articles:
            day_scanned += 1
            if not is_arxiv_row(art):
                continue
            aid = parse_arxiv_id(art.get("url") or "")
            if not aid:
                stats["unmatched_urls"] += 1
                continue
            day_arxiv += 1
            if dry_run:
                continue
            action = upsert_paper(conn, aid, art, day, kind="primary")
            if action == "insert":
                day_ins += 1
            else:
                day_upd += 1
            if upsert_mention(conn, aid, art, day, kind="primary"):
                day_ment_new += 1
            else:
                day_ment_skip += 1

        # C1: cross-source references from raw/ RSS summaries.
        day_refs = 0
        day_ref_ins = day_ref_ment_new = day_ref_ment_skip = 0
        if with_references:
            for aid, referring in extract_referenced_papers(day, articles):
                day_refs += 1
                if dry_run:
                    continue
                if upsert_paper(conn, aid, referring, day, kind="reference") == "insert":
                    day_ref_ins += 1
                if upsert_mention(conn, aid, referring, day, kind="reference"):
                    day_ref_ment_new += 1
                else:
                    day_ref_ment_skip += 1

        if not dry_run:
            conn.commit()
        stats["scanned_articles"] += day_scanned
        stats["arxiv_rows"] += day_arxiv
        stats["papers_inserted"] += day_ins
        stats["papers_updated"] += day_upd
        stats["mentions_inserted"] += day_ment_new
        stats["mentions_skipped"] += day_ment_skip
        stats["refs_found"] += day_refs
        stats["ref_papers_inserted"] += day_ref_ins
        stats["ref_mentions_inserted"] += day_ref_ment_new
        stats["ref_mentions_skipped"] += day_ref_ment_skip
        stats["days"].append({
            "day": day, "articles": day_scanned, "arxiv": day_arxiv,
            "papers_inserted": day_ins, "papers_updated": day_upd,
            "mentions_new": day_ment_new, "mentions_skipped": day_ment_skip,
            "refs_found": day_refs, "ref_mentions_new": day_ref_ment_new,
        })
    return stats


def _record_last_run(conn: sqlite3.Connection, stats: dict) -> None:
    payload = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": stats,
    }
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_run', ?)",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--day", default=None, help="YYYY-MM-DD")
    parser.add_argument("--backfill", action="store_true", help="all data/<day>/ dirs")
    parser.add_argument("--dry-run", action="store_true", help="parse only")
    parser.add_argument("--with-pdf", action="store_true", help="also download pdfs")
    parser.add_argument("--no-enrich", action="store_true", help="skip arXiv API")
    parser.add_argument("--no-references", action="store_true",
                        help="skip cross-source reference extraction (raw/ scan)")
    parser.add_argument("--sleep", type=float, default=3.0, help="arXiv request spacing (sec)")
    parser.add_argument("--limit-enrich", type=int, default=None, help="cap enrichment per run")
    args = parser.parse_args()

    days = iter_days(args.day, args.backfill)
    log.info("[collect] days=%s dry_run=%s enrich=%s with_pdf=%s references=%s",
             days, args.dry_run, not args.no_enrich, args.with_pdf, not args.no_references)
    if not days:
        log.info("[collect] no eligible data/<day>/articles.json - nothing to do")
        return

    conn = open_db()
    try:
        collect_stats = collect(days, conn, dry_run=args.dry_run,
                                with_references=not args.no_references)
        log.info(
            "[collect] scanned=%d arxiv_rows=%d inserted=%d updated=%d "
            "mentions_new=%d mentions_skipped=%d unmatched=%d",
            collect_stats["scanned_articles"], collect_stats["arxiv_rows"],
            collect_stats["papers_inserted"], collect_stats["papers_updated"],
            collect_stats["mentions_inserted"], collect_stats["mentions_skipped"],
            collect_stats["unmatched_urls"],
        )
        log.info(
            "[collect] references: found=%d ref_papers_new=%d ref_mentions_new=%d ref_mentions_skipped=%d",
            collect_stats["refs_found"], collect_stats["ref_papers_inserted"],
            collect_stats["ref_mentions_inserted"], collect_stats["ref_mentions_skipped"],
        )

        if not args.dry_run:
            changed = recompute_seen_counts(conn)
            log.info("[collect] seen_count recomputed (distinct days): %d rows corrected", changed)

        enrich_stats = {"attempted": 0, "enriched": 0, "failed": 0, "skipped": True}
        if not args.dry_run and not args.no_enrich:
            enrich_stats = enrich_pending(conn, args.sleep, args.limit_enrich)
            enrich_stats["skipped"] = False
            log.info("[enrich] attempted=%d enriched=%d failed=%d",
                     enrich_stats["attempted"], enrich_stats["enriched"], enrich_stats["failed"])
        elif args.no_enrich:
            log.info("[enrich] skipped (--no-enrich)")

        pdf_stats = {"attempted": 0, "downloaded": 0, "skipped": True}
        if not args.dry_run and args.with_pdf:
            pdf_stats = download_pdfs(conn, args.sleep)
            pdf_stats["skipped"] = False
            log.info("[pdf] downloaded=%d skipped=%d failed=%d",
                     pdf_stats["downloaded"], pdf_stats["skipped"], pdf_stats.get("failed", 0))

        if not args.dry_run:
            _record_last_run(conn, {
                "collect": collect_stats, "enrich": enrich_stats, "pdf": pdf_stats,
            })
    finally:
        conn.close()


if __name__ == "__main__":
    main()

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

from pipeline.utils.http import get_client, fetch

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
PRIVATE_ROOT = DATA_DIR / "papers_private"
DB_FILE = PRIVATE_ROOT / "papers.db"
PDF_DIR = PRIVATE_ROOT / "pdf"

SCHEMA_VERSION = 1

# arXiv source ids from pipeline/sources.yaml — used to short-circuit
# non-arxiv rows before falling back to a URL regex. Keep in sync when
# new arxiv:* sources are added.
ARXIV_SOURCE_IDS = {
    "arxiv_cs_ai", "arxiv_cs_lg", "arxiv_cs_cl", "arxiv_cs_cv",
    "arxiv_cs_ro", "arxiv_stat_ml",
}

# Modern arXiv ids: YYMM.NNNNN(vN). Old ids: archive/YYMMNNN
ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>[a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)

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


def parse_arxiv_id(url: str) -> str | None:
    """Return the version-stripped base arxiv id, or None.

    Handles both modern ('2606.30626v1' -> '2606.30626') and legacy
    ('cs/0501001v2' -> 'cs/0501001') ids.
    """
    if not url:
        return None
    m = ARXIV_URL_RE.search(url)
    if not m:
        return None
    raw = m.group("id")
    return ARXIV_VERSION_RE.sub("", raw)


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


def upsert_paper(conn: sqlite3.Connection, arxiv_id: str, article: dict, day: str) -> str:
    """Insert or update the papers row for one article mention.

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
                article.get("title_original") or None,
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
            seen_count     = seen_count + 1,
            tags_json      = ?,
            importance_max = ?
        WHERE arxiv_id = ?
        """,
        (day, day, day, day, merged_tags, new_importance_max, arxiv_id),
    )
    return "update"


def upsert_mention(conn: sqlite3.Connection, arxiv_id: str, article: dict, day: str) -> bool:
    """Insert (arxiv_id, article_id) mention row. Returns True on
    new insert, False on already-present."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO paper_mentions (
            arxiv_id, day, article_id, cluster_id, source_id, importance
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            arxiv_id,
            day,
            article.get("id"),
            article.get("cluster_id"),
            article.get("source_id"),
            int(article.get("importance_score") or 0),
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


# ---------- enrichment ----------


def _text(entry, key: str) -> str:
    val = entry.get(key)
    if isinstance(val, dict):
        return val.get("value", "") or ""
    return (val or "") if isinstance(val, str) else ""


def enrich_batch(ids: list[str], sleep_sec: float) -> dict[str, dict]:
    """Call arXiv API for a batch of ids and parse Atom into a dict
    keyed by base arxiv_id."""
    if not ids:
        return {}
    url = f"{ARXIV_API}?id_list={','.join(ids)}&max_results={len(ids)}"
    log.info("[enrich] fetching %d ids", len(ids))
    with get_client() as client:
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
    # arXiv ToS: cap request rate.
    time.sleep(sleep_sec)
    return out


def enrich_pending(conn: sqlite3.Connection, sleep_sec: float, limit: int | None) -> dict:
    """Fill metadata for every paper with enriched=0. Returns stats."""
    cur = conn.cursor()
    cur.execute("SELECT arxiv_id FROM papers WHERE enriched = 0 ORDER BY last_seen_day DESC")
    pending = [row[0] for row in cur.fetchall()]
    if limit is not None:
        pending = pending[:limit]
    stats = {"attempted": len(pending), "enriched": 0, "failed": 0}
    if not pending:
        return stats

    for i in range(0, len(pending), ARXIV_BATCH_SIZE):
        batch = pending[i:i + ARXIV_BATCH_SIZE]
        try:
            fetched = enrich_batch(batch, sleep_sec)
        except Exception as exc:
            log.warning("[enrich] batch failed (%s) — will retry on next run", exc)
            stats["failed"] += len(batch)
            continue
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
        conn.commit()
    return stats


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


def collect(days: Iterable[str], conn: sqlite3.Connection, dry_run: bool) -> dict:
    stats = {"scanned_articles": 0, "arxiv_rows": 0, "unmatched_urls": 0,
             "papers_inserted": 0, "papers_updated": 0,
             "mentions_inserted": 0, "mentions_skipped": 0, "days": []}
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
            action = upsert_paper(conn, aid, art, day)
            if action == "insert":
                day_ins += 1
            else:
                day_upd += 1
            if upsert_mention(conn, aid, art, day):
                day_ment_new += 1
            else:
                day_ment_skip += 1
        if not dry_run:
            conn.commit()
        stats["scanned_articles"] += day_scanned
        stats["arxiv_rows"] += day_arxiv
        stats["papers_inserted"] += day_ins
        stats["papers_updated"] += day_upd
        stats["mentions_inserted"] += day_ment_new
        stats["mentions_skipped"] += day_ment_skip
        stats["days"].append({
            "day": day, "articles": day_scanned, "arxiv": day_arxiv,
            "papers_inserted": day_ins, "papers_updated": day_upd,
            "mentions_new": day_ment_new, "mentions_skipped": day_ment_skip,
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
    parser.add_argument("--sleep", type=float, default=3.0, help="arXiv request spacing (sec)")
    parser.add_argument("--limit-enrich", type=int, default=None, help="cap enrichment per run")
    args = parser.parse_args()

    days = iter_days(args.day, args.backfill)
    log.info("[collect] days=%s dry_run=%s enrich=%s with_pdf=%s",
             days, args.dry_run, not args.no_enrich, args.with_pdf)
    if not days:
        log.info("[collect] no eligible data/<day>/articles.json - nothing to do")
        return

    conn = open_db()
    try:
        collect_stats = collect(days, conn, dry_run=args.dry_run)
        log.info(
            "[collect] scanned=%d arxiv_rows=%d inserted=%d updated=%d "
            "mentions_new=%d mentions_skipped=%d unmatched=%d",
            collect_stats["scanned_articles"], collect_stats["arxiv_rows"],
            collect_stats["papers_inserted"], collect_stats["papers_updated"],
            collect_stats["mentions_inserted"], collect_stats["mentions_skipped"],
            collect_stats["unmatched_urls"],
        )

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

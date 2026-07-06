"""ENR-1 (2026-07-06): local body re-extraction for EN-instrument coverage.

The EN research instrument (``latest_mentions_en``) counts news concepts
from ``title`` + ``body_en``. An ENR-1 marginal-recall pilot (CODEBOOK
'ENR-1') found body_en adds **+181% recall over title alone** for the EN
instrument — so title-only days severely under-count. Every CI-run day IS
title-only, because ``data/corpus/<day>/bodies.jsonl`` is gitignored
(DBQ-3) and is written only where the summarizer ran locally. That leaves
the EN news time series discontinuous (a few body-covered days ~3x higher).

This module homogenises coverage (decision **(d)**): for each day's PUBLIC
``articles.json`` it re-extracts each article's body **locally** via
trafilatura (``pipeline.extract`` — NO LLM, NO translation; the sources
are English) and writes ``data/corpus/<day>/bodies.jsonl``. Those bodies
stay **local and gitignored** — never committed or republished, so DBQ-3
holds. ``en_corpus`` then converts them to body_en; ``concept_extract``
re-backfills the mentions.

Idempotent: skips articles whose body is already present (no re-fetch).
Offline-friendly: extraction failures are logged and skipped (no stall).
Fetches go through the shared throttled http client.

Usage:
  python -m pipeline.research.backfill_bodies [--day D | --backfill] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline import corpus_writer
from pipeline.extract import extract_article

log = logging.getLogger(__name__)

DATA = Path("data")
BODY_CAP = 6000  # matches summarize MAX_BODY_CHARS / en_corpus TEXT_CAP


def _load_articles(day: str) -> list[dict]:
    f = DATA / day / "articles.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def existing_body_ids(day: str) -> set[str]:
    """Article ids that already have a non-empty body in bodies.jsonl."""
    path = DATA / "corpus" / day / "bodies.jsonl"
    if not path.exists():
        return set()
    have: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("url_hash") and (r.get("body_text") or "").strip():
            have.add(r["url_hash"])
    return have


def backfill_day(day: str, limit: int | None = None) -> dict:
    articles = _load_articles(day)
    stats = {"day": day, "articles": len(articles), "already": 0,
             "fetched": 0, "failed": 0}
    if not articles:
        return stats
    have = existing_body_ids(day)
    stats["already"] = len(have)
    todo = [a for a in articles if a.get("id") and a["id"] not in have]
    if limit is not None:
        todo = todo[:limit]
    for a in todo:
        url = a.get("url", "")
        if not url:
            stats["failed"] += 1
            continue
        try:
            fetched = extract_article(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("[backfill-bodies] extract failed %s: %s", url, exc)
            stats["failed"] += 1
            continue
        body = (fetched.get("body") or "")[:BODY_CAP]
        if not body.strip():
            stats["failed"] += 1
            continue
        # url_hash MUST be the article id so en_corpus keeps the row
        # (it matches bodies.url_hash against articles.json id).
        corpus_writer.append_body(
            day,
            url_hash=a["id"],
            url=url,
            title=a.get("title_original", ""),
            source_id=a.get("source_id", ""),
            source_name=a.get("source_name", ""),
            published=a.get("published"),
            body_text=body,
            body_chars=len(body),
            extract_status="refetch",
        )
        stats["fetched"] += 1
    # Deliberately do NOT update data/corpus/manifest.json: the re-extracted
    # bodies are local-only (gitignored, DBQ-3), so their sha256/line metadata
    # must not churn the PUBLIC manifest. bodies.jsonl is consumed directly by
    # en_corpus, which does not read the manifest.
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--day", default=None)
    p.add_argument("--backfill", action="store_true",
                   help="every data/<day>/ (oldest first)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap articles fetched per day (validation / gentle drain)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    all_days = sorted(d.name for d in DATA.glob("2???-??-??") if d.is_dir())
    if args.backfill:
        days = all_days
    elif args.day:
        days = [args.day]
    else:
        days = all_days[-1:] if all_days else []

    grand = {"fetched": 0, "failed": 0, "already": 0}
    for d in days:
        s = backfill_day(d, limit=args.limit)
        if s["articles"]:
            log.info("[backfill-bodies] %s: fetched=%d failed=%d already=%d (of %d)",
                     d, s["fetched"], s["failed"], s["already"], s["articles"])
        for k in grand:
            grand[k] += s[k]
    log.info("[backfill-bodies] TOTAL fetched=%d failed=%d already=%d",
             grand["fetched"], grand["failed"], grand["already"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
